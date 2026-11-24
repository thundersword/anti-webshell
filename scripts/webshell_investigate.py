#!/usr/bin/env python3
"""Build an intrusion timeline from audit, access, and auth logs.

Pure functions only: this module does not start auditd, send alerts, or
execute a quarantined file. The CLI reads an anchor, writes timeline JSON,
and prints the second-alert body.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAIL_LIMIT = 32 * 1024 * 1024
QUERY_LIMIT = 200

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
RCE_MARKERS = (
    "invokefunction",
    "call_user_func",
    "think\\app",
    "/vendor/phpunit",
    "eval-stdin.php",
    "auto_prepend_file",
)
WEB_NAMES = {"php", "php-fpm", "nginx", "httpd", "apache2"}
SHELL_NAMES = {"bash", "sh", "dash", "zsh", "ash", "ksh", "fish", "csh", "tcsh"}
EXISTING_NAMES = {"bash", "sh", "dash", "python", "perl"}
CRON_NAMES = {"cron", "crond"}
CONFIDENCE_LABEL = {"high": "高", "medium": "中", "low": "低"}

ACCESS_RE = re.compile(
    r"^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<ts>[^\]]+)\]\s+"
    r'"(?P<method>[A-Z]+)\s+(?P<url>\S+)\s+\S+"\s+'
    r"(?P<status>\d+)\s+\S+\s+"
    r'"(?P<referer>[^"]*)"\s+"(?P<ua>[^"]*)"'
    r'(?:\s+"?(?P<host>[^\s"]+)"?)?(?:\s+\S+)*\s*$'
)
AUDIT_MSG_RE = re.compile(r"msg=audit\((\d+\.\d+):(\d+)\)")
ACCEPTED_RE = re.compile(
    r"Accepted\s+\S+\s+for\s+(\S+)\s+from\s+(\S+)",
    re.IGNORECASE,
)
ISO_PREFIX_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
SYSLOG_RE = re.compile(
    r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})"
)


def read_log_tail(path: str, limit: int = TAIL_LIMIT) -> str:
    """Read at most the last `limit` bytes of an absolute regular file."""
    if not os.path.isabs(path):
        raise ValueError("log path must be absolute")
    if not os.path.isfile(path):
        raise ValueError("log path must be a regular file")
    mode = os.stat(path).st_mode
    if not stat.S_ISREG(mode):
        raise ValueError("log path must be a regular file")
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - limit)
        handle.seek(start)
        data = handle.read(limit)
    if start > 0:
        newline = data.find(b"\n")
        if newline != -1:
            data = data[newline + 1:]
    return data.decode("utf-8", errors="replace")


def investigate(anchor: dict, logs: dict) -> dict:
    """Correlate one quarantine anchor with access, audit, and auth text."""
    anchor = anchor or {}
    logs = logs or {}
    gaps: list[str] = []
    quarantine = _parse_time(anchor.get("event_time"))
    file_path = anchor.get("file_path") or ""
    sha256, size = _fingerprint(anchor.get("quarantine_path") or "")

    audit_text = _log_text(logs, "audit")
    access_text = _log_text(logs, "access")
    auth_text = _log_text(logs, "auth")
    if audit_text is None:
        gaps.append("无审计记录")
    if access_text is None:
        gaps.append("无访问日志")

    records = _parse_audit(audit_text or "", file_path)
    record, write_at, time_estimated = _select_write(records, quarantine)
    access_rows = _access_rows(anchor, access_text, gaps)
    auth_rows = _parse_auth(auth_text, gaps, quarantine)

    writes = [row for row in access_rows if _in_window(row["ts"], write_at, 15, 2)]
    chosen = _choose_write(writes, file_path, anchor.get("project_dir") or "")
    posts = [row for row in writes if row["method"] in {"POST", "PUT"}]
    actor_ip = chosen["ip"] if chosen else ""
    rce = _web_rce(writes, access_rows, write_at, actor_ip)
    accepts = _matching_accepts(auth_rows, record, write_at)
    entry = _entry(record, accepts, posts, writes, rce, bool(record) and not time_estimated)
    audit_hit = record is not None and not time_estimated
    confidence = _confidence(
        entry, audit_hit, len(posts), len(accepts), records, quarantine, len(writes)
    )

    actor = _actor(record, chosen, accepts, entry)
    events = _events(
        anchor, access_rows, record, auth_rows, chosen, write_at, quarantine, file_path
    )
    return {
        "incident_id": anchor.get("incident_id") or "",
        "site_id": anchor.get("site_id") or "",
        "status": "ok",
        "file_path": file_path,
        "quarantine_path": anchor.get("quarantine_path") or "",
        "reason": anchor.get("reason") or "",
        "sha256": sha256,
        "size": size,
        "time_estimated": time_estimated,
        "entry": entry,
        "confidence": confidence,
        "summary": _summary(entry, actor),
        "actor": actor,
        "gaps": gaps,
        "events": events,
    }


def alert_text(timeline: dict) -> str:
    """Render the second-alert body. Query strings stop at 200 characters."""
    site = timeline.get("site_id") or "未知"
    path = timeline.get("file_path") or "未知"
    if timeline.get("status") != "ok":
        reason = timeline.get("summary") or "未知"
        return f"未能生成入侵时间线 [{site}]\n文件: {path}\n原因: {reason}\n"

    actor = timeline.get("actor") or {}
    label = CONFIDENCE_LABEL.get(timeline.get("confidence") or "", "低")
    entry = timeline.get("entry") or "unknown"
    ip = _known(actor.get("ip"))
    url_field = _alert_url(actor.get("method") or "", actor.get("url") or "")
    process = _alert_process(actor.get("exe") or "", actor.get("uid") or "")
    lines = [
        f"入侵路径 [{site}] 置信度: {label}",
        f"入口: {entry}",
        f"来源 IP: {ip}",
        f"URL: {url_field}",
        f"进程: {process}",
        f"文件: {path}",
    ]
    gaps = [gap for gap in (timeline.get("gaps") or []) if gap]
    if gaps:
        lines.append("缺口: " + "；".join(gaps))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an intrusion timeline")
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    try:
        anchor = json.loads(Path(args.anchor).read_text(encoding="utf-8"))
        if not isinstance(anchor, dict):
            raise ValueError("anchor is not an object")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        sys.stdout.write(_failure_body("", "", "无法读取锚点"))
        return 3

    try:
        logs, read_gaps = _load_logs(anchor)
        timeline = investigate(anchor, logs)
        for gap in read_gaps:
            if gap not in timeline["gaps"]:
                timeline["gaps"].append(gap)
    except Exception as exc:
        timeline = _failed_timeline(anchor, str(exc) or "分析失败")

    try:
        _write_json(args.output, timeline)
    except OSError:
        sys.stdout.write(_failure_body(
            anchor.get("site_id") or "",
            anchor.get("file_path") or "",
            "无法写入时间线",
        ))
        return 3

    if str(args.anchor).endswith(".anchor.json"):
        try:
            os.remove(args.anchor)
        except OSError:
            pass
    sys.stdout.write(alert_text(timeline))
    return 0 if timeline.get("status") == "ok" else 2


def _log_text(logs: dict, key: str) -> str | None:
    if key not in logs or logs[key] is None:
        return None
    return logs[key]


def _known(value: str) -> str:
    text = (value or "").strip()
    return text or "未知"


def _failure_body(site: str, path: str, reason: str) -> str:
    return (
        f"未能生成入侵时间线 [{site or '未知'}]\n"
        f"文件: {path or '未知'}\n"
        f"原因: {reason or '未知'}\n"
    )


def _failed_timeline(anchor: dict, reason: str) -> dict:
    sha256, size = _fingerprint(anchor.get("quarantine_path") or "")
    return {
        "incident_id": anchor.get("incident_id") or "",
        "site_id": anchor.get("site_id") or "",
        "status": "failed",
        "file_path": anchor.get("file_path") or "",
        "quarantine_path": anchor.get("quarantine_path") or "",
        "reason": anchor.get("reason") or "",
        "sha256": sha256,
        "size": size,
        "time_estimated": True,
        "entry": "unknown",
        "confidence": "low",
        "summary": reason or "分析失败",
        "actor": _empty_actor(),
        "gaps": [],
        "events": [],
    }


def _empty_actor() -> dict:
    return {
        "ip": "",
        "method": "",
        "url": "",
        "status": None,
        "ua": "",
        "exe": "",
        "uid": "",
        "auid": "",
        "comm": "",
    }


def _fingerprint(path: str) -> tuple[str, int]:
    if not path or not os.path.isfile(path):
        return "", 0
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _parse_time(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _access_rows(anchor: dict, text: str | None, gaps: list[str]) -> list[dict]:
    if anchor.get("access_is_global") and not _site_hosts(anchor):
        gaps.append("无法区分站点")
        return []
    if text is None:
        return []
    if text.strip() == "":
        return []
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        matched = ACCESS_RE.match(line.strip())
        if not matched:
            continue
        stamp = _parse_access_time(matched.group("ts"))
        if stamp is None:
            continue
        rows.append({
            "ts": stamp,
            "ip": matched.group("ip"),
            "method": matched.group("method"),
            "url": matched.group("url"),
            "status": int(matched.group("status")),
            "ua": matched.group("ua"),
            "host": matched.group("host") or "",
        })
    if not rows:
        gaps.append("访问日志格式无法解析")
        return []
    if not anchor.get("access_is_global"):
        return rows
    if all(not row["host"] for row in rows):
        gaps.append("访问日志无 Host 字段")
        return []
    allowed = {_host_key(host) for host in _site_hosts(anchor)}
    return [row for row in rows if _host_key(row["host"]) in allowed]


def _site_hosts(anchor: dict) -> list[str]:
    hosts = anchor.get("site_hosts") or []
    if isinstance(hosts, str):
        return [part for part in hosts.split() if part]
    return [str(host) for host in hosts if str(host).strip()]


def _host_key(host: str) -> str:
    text = (host or "").strip().strip("[]").lower()
    if not text or text == "-":
        return ""
    if text.count(":") == 1:
        text = text.split(":", 1)[0]
    return text


def _parse_access_time(text: str) -> datetime | None:
    matched = re.match(
        r"(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})",
        text.strip(),
    )
    if not matched:
        return None
    day, month_name, year, hour, minute, second, sign, off_h, off_m = matched.groups()
    month = MONTHS.get(month_name.title())
    if month is None:
        return None
    direction = 1 if sign == "+" else -1
    offset = timezone(timedelta(hours=direction * int(off_h), minutes=direction * int(off_m)))
    return datetime(int(year), month, int(day), int(hour), int(minute), int(second), tzinfo=offset)


def _parse_audit(text: str, file_path: str) -> list[dict]:
    groups: dict[str, dict] = {}
    order: list[str] = []
    for line in text.splitlines():
        matched = AUDIT_MSG_RE.search(line)
        if not matched:
            continue
        key = f"{matched.group(1)}:{matched.group(2)}"
        if key not in groups:
            groups[key] = {"ts": _audit_time(matched.group(1)), "lines": []}
            order.append(key)
        groups[key]["lines"].append(line)
    records = []
    for key in order:
        blob = "\n".join(groups[key]["lines"])
        if not _audit_kept(blob, file_path):
            continue
        records.append({
            "ts": groups[key]["ts"],
            "exe": _field(blob, "exe"),
            "comm": _field(blob, "comm"),
            "uid": _normalize_id(_field(blob, "uid")),
            "auid": _normalize_id(_field(blob, "auid")) or "unset",
        })
    return records


def _audit_time(seconds: str) -> datetime:
    return datetime.fromtimestamp(float(seconds), tz=timezone.utc)


def _audit_kept(blob: str, file_path: str) -> bool:
    if not re.search(r"\bsuccess=yes\b", blob):
        return False
    if not re.search(r'\bkey="?webshell_write"?\b', blob):
        return False
    for matched in re.finditer(r'\bname=(?:"([^"]*)"|(\S+))', blob):
        name = matched.group(1) if matched.group(1) is not None else matched.group(2)
        if name == file_path:
            return True
    return False


def _field(text: str, name: str) -> str:
    matched = re.search(rf'\b{name}=(?:"([^"]*)"|(\S+))', text)
    if not matched:
        return ""
    return matched.group(1) if matched.group(1) is not None else matched.group(2)


def _normalize_id(value: str) -> str:
    if not value or value in {"unset", "4294967295", "-1", "(none)"}:
        return "unset" if value in {"unset", "4294967295", "-1", "(none)"} else ""
    if value.isdigit():
        try:
            import pwd
            return pwd.getpwuid(int(value)).pw_name
        except (KeyError, OverflowError, ValueError, OSError):
            return value
    return value


def _select_write(records: list[dict], quarantine: datetime):
    start = quarantine - timedelta(seconds=120)
    hits = [row for row in records if start <= row["ts"] <= quarantine]
    if not hits:
        return None, quarantine - timedelta(seconds=1), True
    chosen = _latest(hits)
    return chosen, chosen["ts"], False


def _parse_auth(text: str | None, gaps: list[str], year_hint: datetime) -> list[dict]:
    if text is None:
        gaps.append("无登录日志")
        return []
    if text.strip() == "":
        return []
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        stamp = _parse_auth_time(line, year_hint)
        if stamp is None:
            continue
        accepted = ACCEPTED_RE.search(line)
        if accepted:
            rows.append({
                "ts": stamp,
                "user": accepted.group(1),
                "ip": accepted.group(2).rstrip(":"),
                "kind": "accepted",
                "summary": f"Accepted {accepted.group(1)} from {accepted.group(2).rstrip(':')}",
            })
            continue
        lowered = line.lower()
        if "sudo:" in lowered or " cron" in lowered or lowered.strip().startswith("cron") or "CRON" in line:
            rows.append({
                "ts": stamp,
                "user": "",
                "ip": "",
                "kind": "auth",
                "summary": line.strip()[:300],
            })
    return rows


def _parse_auth_time(line: str, year_hint: datetime, zone=None) -> datetime | None:
    iso = ISO_PREFIX_RE.match(line.strip())
    if iso:
        try:
            return _parse_time(iso.group(1).replace(" ", "T"))
        except ValueError:
            return None
    syslog = SYSLOG_RE.match(line.strip())
    if not syslog:
        return None
    month = MONTHS.get(syslog.group(1))
    if month is None:
        return None
    # Zoneless syslog is host-local. Take the year and month from the hint
    # in that zone, not from the UTC calendar date.
    if zone is None:
        local_hint = year_hint.astimezone()
        zone = local_hint.tzinfo
    else:
        local_hint = year_hint.astimezone(zone)
    year = local_hint.year
    if month > local_hint.month + 1:
        year -= 1
    try:
        parsed = datetime(
            year, month, int(syslog.group(2)),
            int(syslog.group(3)), int(syslog.group(4)), int(syslog.group(5)),
            tzinfo=zone,
        )
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _in_window(stamp: datetime, center: datetime, before_s: int, after_s: int) -> bool:
    return center - timedelta(seconds=before_s) <= stamp <= center + timedelta(seconds=after_s)


def _choose_write(requests: list[dict], file_path: str, project_dir: str) -> dict | None:
    if not requests:
        return None
    posts = [row for row in requests if row["method"] in {"POST", "PUT"}]
    pool = posts or list(requests)
    rel = _relative_dir(file_path, project_dir)
    if rel:
        matched = [row for row in pool if _dir_in_url(row["url"], rel)]
        if matched:
            pool = matched
    return _latest(pool)


def _relative_dir(file_path: str, project_dir: str) -> str:
    if not file_path or not project_dir:
        return ""
    directory = os.path.abspath(os.path.dirname(file_path))
    root = os.path.abspath(project_dir)
    if directory != root and not directory.startswith(root + os.sep):
        return ""
    rel = os.path.relpath(directory, root).replace(os.sep, "/")
    if rel in {"", "."}:
        return ""
    return rel


def _dir_in_url(url: str, rel_dir: str) -> bool:
    parts = [part for part in url.split("?", 1)[0].split("/") if part]
    wanted = [part for part in rel_dir.split("/") if part and part != "."]
    if not wanted:
        return False
    span = len(wanted)
    return any(parts[index:index + span] == wanted for index in range(len(parts) - span + 1))


def _web_rce(writes: list[dict], rows: list[dict], write_at: datetime, ip: str) -> bool:
    if any(_is_rce(row["url"]) for row in writes):
        return True
    if not ip:
        return False
    start = write_at - timedelta(minutes=30)
    return any(
        start <= row["ts"] < write_at and row["ip"] == ip and _is_rce(row["url"])
        for row in rows
    )


def _is_rce(url: str) -> bool:
    lowered = url.lower()
    return any(marker.lower() in lowered for marker in RCE_MARKERS)


def _names(record: dict | None) -> set[str]:
    if not record:
        return set()
    found = set()
    for value in (record.get("exe") or "", record.get("comm") or ""):
        base = os.path.basename(value)
        if base:
            found.add(base)
    return found


def _matching_accepts(rows: list[dict], record: dict | None, write_at: datetime) -> list[dict]:
    if not record:
        return []
    start = write_at - timedelta(minutes=30)
    end = write_at + timedelta(minutes=5)
    auid = record.get("auid") or ""
    return [
        row for row in rows
        if row["kind"] == "accepted"
        and start <= row["ts"] <= end
        and _users_match(auid, row["user"])
    ]


def _users_match(auid: str, user: str) -> bool:
    if not auid or not user or auid == "unset":
        return False
    if auid == user:
        return True
    try:
        import pwd
        if auid.isdigit() and pwd.getpwuid(int(auid)).pw_name == user:
            return True
        if str(pwd.getpwnam(user).pw_uid) == auid:
            return True
    except (KeyError, OverflowError, ValueError, OSError):
        return False
    return False


def _latest(rows: list[dict]) -> dict:
    return max(enumerate(rows), key=lambda item: (item[1]["ts"], item[0]))[1]


def _entry(record, accepts, posts, writes, rce, audit_hit) -> str:
    names = _names(record)
    web = bool(names & WEB_NAMES) or (not audit_hit and bool(posts))
    if names & CRON_NAMES:
        return "cron"
    if "sshd" in names or ((names & SHELL_NAMES) and accepts):
        return "ssh"
    if web and rce:
        return "web_rce"
    if web and (posts or writes):
        return "web_upload"
    if names & EXISTING_NAMES:
        return "existing_shell"
    return "unknown"


def _confidence(
    entry, audit_hit, post_count, accept_count, records, quarantine, request_count,
) -> str:
    if entry == "unknown":
        return "low"
    cron_count = 0
    if audit_hit:
        start = quarantine - timedelta(seconds=120)
        cron_count = sum(
            1 for row in records
            if start <= row["ts"] <= quarantine and (_names(row) & CRON_NAMES)
        )
    if audit_hit and entry in {"web_upload", "web_rce"} and post_count == 1:
        return "high"
    if audit_hit and entry == "ssh" and accept_count == 1:
        return "high"
    if audit_hit and entry == "cron" and cron_count == 1:
        return "high"
    if (audit_hit and request_count > 1) or (not audit_hit and post_count == 1):
        return "medium"
    return "low"


def _actor(record, chosen, accepts, entry) -> dict:
    actor = _empty_actor()
    if record:
        actor["exe"] = record.get("exe") or ""
        actor["comm"] = record.get("comm") or ""
        actor["uid"] = record.get("uid") or ""
        actor["auid"] = record.get("auid") or "unset"
    if entry in {"web_upload", "web_rce"} and chosen:
        actor["ip"] = chosen["ip"]
        actor["method"] = chosen["method"]
        actor["url"] = chosen["url"]
        actor["status"] = chosen["status"]
        actor["ua"] = chosen["ua"]
    elif entry == "ssh" and accepts:
        actor["ip"] = _latest(accepts)["ip"]
    return actor


def _summary(entry: str, actor: dict) -> str:
    comm = actor.get("comm") or os.path.basename(actor.get("exe") or "") or "未知进程"
    ip = actor.get("ip") or ""
    method = actor.get("method") or ""
    path = (actor.get("url") or "").split("?", 1)[0]
    if entry in {"web_upload", "web_rce"} and ip:
        return f"{comm} 写入，来源 {ip} {method} {path}".strip()
    if entry == "ssh" and ip:
        who = actor.get("auid") or actor.get("uid") or ""
        return f"{comm} 写入，SSH {who} 来自 {ip}".strip()
    if actor.get("exe") or actor.get("comm"):
        return f"{comm} 写入"
    if ip and method:
        return f"来源 {ip} {method} {path}".strip()
    return "未能关联写入来源"


def _events(anchor, rows, record, auth_rows, chosen, write_at, quarantine, file_path) -> list[dict]:
    events = []
    ip = chosen["ip"] if chosen else ""
    prior_start = write_at - timedelta(minutes=30)
    for row in rows:
        kind = _access_kind(row, write_at, quarantine, file_path, ip)
        if kind is None:
            continue
        events.append({
            "ts": _iso(row["ts"]),
            "_sort": row["ts"],
            "source": "access",
            "kind": kind,
            "summary": f"{row['ip']} {row['method']} {row['url']} {row['status']}",
        })
    if record is not None:
        events.append({
            "ts": _iso(record["ts"]),
            "_sort": record["ts"],
            "source": "audit",
            "kind": "write",
            "summary": (
                f"{record.get('comm') or os.path.basename(record.get('exe') or '')} "
                f"uid={record.get('uid') or '未知'} exe={record.get('exe') or '未知'}"
            ).strip(),
        })
    auth_end = write_at + timedelta(minutes=5)
    for row in auth_rows:
        if not (prior_start <= row["ts"] <= auth_end):
            continue
        events.append({
            "ts": _iso(row["ts"]),
            "_sort": row["ts"],
            "source": "auth",
            "kind": "login" if row["kind"] == "accepted" else "auth",
            "summary": row["summary"],
        })
    reason = anchor.get("reason") or "隔离"
    events.append({
        "ts": _iso(quarantine),
        "_sort": quarantine,
        "source": "monitor",
        "kind": "quarantine",
        "summary": f"隔离 {file_path} {reason}".strip(),
    })
    events.sort(key=lambda item: (item["_sort"], item["source"]))
    for item in events:
        item.pop("_sort", None)
    return events


def _access_kind(row, write_at, quarantine, file_path, ip) -> str | None:
    filename = os.path.basename(file_path)
    path = row["url"].split("?", 1)[0]
    named = filename and filename in [part for part in path.split("/") if part]
    if _in_window(row["ts"], write_at, 15, 2):
        return "write"
    if ip and row["ip"] == ip and write_at - timedelta(minutes=30) <= row["ts"] < write_at and _is_rce(row["url"]):
        return "prior"
    after_end = quarantine + timedelta(minutes=2)
    if named and write_at < row["ts"] <= after_end:
        return "after"
    return None


def _alert_url(method: str, url: str) -> str:
    if not url:
        return "未知"
    shown = _truncate_query(url)
    if method:
        return f"{method} {shown}"
    return shown


def _truncate_query(url: str) -> str:
    if "?" not in url:
        return url
    path, query = url.split("?", 1)
    if len(query) <= QUERY_LIMIT:
        return url
    return path + "?" + query[:QUERY_LIMIT]


def _alert_process(exe: str, uid: str) -> str:
    if not exe and not uid:
        return "未知"
    if not exe:
        return "未知"
    return f"{exe} ({uid or '未知'})"


def _load_logs(anchor: dict) -> tuple[dict, list[str]]:
    conf = _read_conf()
    paths = {
        "access": anchor.get("access_log") or "",
        "audit": anchor.get("audit_log") or conf.get("AUDIT_LOG") or "",
        "auth": anchor.get("auth_log") or conf.get("AUTH_LOG") or "",
    }
    logs: dict[str, str | None] = {}
    gaps: list[str] = []
    infos: dict[str, dict] = {}
    for kind, path in paths.items():
        text, file_gaps, info = _read_optional(path)
        logs[kind] = text
        gaps.extend(file_gaps)
        infos[kind] = info
    gaps.extend(_rotation_gaps(anchor, logs, infos))
    return logs, gaps


def _read_optional(path: str) -> tuple[str | None, list[str], dict]:
    info = {"truncated": False, "rotated": False}
    if not path:
        return None, [], info
    try:
        text = read_log_tail(path)
    except (OSError, ValueError):
        return None, [], info
    gaps = []
    try:
        if os.path.getsize(path) > TAIL_LIMIT:
            gaps.append("日志窗口超出当前文件末尾 32MB")
            info["truncated"] = True
    except OSError:
        pass
    info["rotated"] = any(os.path.exists(path + suffix) for suffix in (".1", ".1.gz"))
    return text, gaps, info


def _rotation_gaps(anchor: dict, logs: dict, infos: dict) -> list[str]:
    """Flag unread rotations only when this file does not cover T-30min.

    A sibling ``.1`` / ``.1.gz`` is not enough. The retained file misses the
    lookback when its earliest parsed timestamp is later than the window
    start. A read that already stopped at 32MB keeps that gap alone.
    """
    event_time = anchor.get("event_time")
    if not event_time:
        return []
    try:
        quarantine = _parse_time(event_time)
    except (TypeError, ValueError):
        return []
    records = _parse_audit(logs.get("audit") or "", anchor.get("file_path") or "")
    _, write_at, _ = _select_write(records, quarantine)
    window_start = write_at - timedelta(minutes=30)
    gaps = []
    for kind, info in infos.items():
        if info.get("truncated") or not info.get("rotated"):
            continue
        earliest = _earliest_parsed(kind, logs.get(kind), quarantine)
        if earliest is not None and earliest > window_start:
            gaps.append("未读取已轮转日志")
    return gaps


def _earliest_parsed(kind: str, text: str | None, year_hint: datetime) -> datetime | None:
    if not text or not str(text).strip():
        return None
    stamps: list[datetime] = []
    if kind == "access":
        for line in str(text).splitlines():
            if not line.strip():
                continue
            matched = ACCESS_RE.match(line.strip())
            if not matched:
                continue
            stamp = _parse_access_time(matched.group("ts"))
            if stamp is not None:
                stamps.append(stamp)
    elif kind == "audit":
        for matched in AUDIT_MSG_RE.finditer(str(text)):
            stamps.append(_audit_time(matched.group(1)))
    elif kind == "auth":
        for line in str(text).splitlines():
            if not line.strip():
                continue
            stamp = _parse_auth_time(line, year_hint)
            if stamp is not None:
                stamps.append(stamp)
    if not stamps:
        return None
    return min(stamps)


def _read_conf() -> dict:
    candidates = []
    env_path = os.environ.get("WEBSHELL_INVESTIGATE_CONF")
    if env_path:
        candidates.append(env_path)
    candidates.append("/etc/webshell-monitor/investigate.conf")
    for path in candidates:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        return _parse_conf(text)
    return {}


def _parse_conf(text: str) -> dict:
    data = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip().strip("'\"")
    return data


def _write_json(path: str, timeline: dict) -> None:
    payload = json.dumps(timeline, ensure_ascii=False, indent=2) + "\n"
    Path(path).write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
