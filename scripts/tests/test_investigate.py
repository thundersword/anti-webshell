import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "webshell_investigate", ROOT / "webshell_investigate.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
investigate = mod.investigate
alert_text = mod.alert_text
read_log_tail = mod.read_log_tail

FILE = "/www/wwwroot/blog/public/x.php"
T = datetime(2026, 11, 24, 3, 42, 11, tzinfo=timezone.utc)

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _access_stamp(ts):
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    offset = ts.utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, minutes = divmod(total // 60, 60)
    return (
        f"{ts.day:02d}/{_MONTHS[ts.month - 1]}/{ts.year}:"
        f"{ts.strftime('%H:%M:%S')} {sign}{hours:02d}{minutes:02d}"
    )


def anchor():
    quarantine = T + timedelta(seconds=1)
    return {
        "incident_id": "20261124T034211Z_ab12cd34",
        "site_id": "blog",
        "file_path": FILE,
        "quarantine_path": "/var/quarantine/blog/x.php",
        "reason": "包含Webshell特征代码",
        "event_time": quarantine.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project_dir": "/www/wwwroot/blog",
        "access_log": "/var/log/nginx/blog.access.log",
        "site_hosts": ["blog.example"],
        "access_is_global": False,
    }


def combined(ts, method, url, ip, status, host):
    stamp = _access_stamp(ts)
    return (
        f'{ip} - - [{stamp}] "{method} {url} HTTP/1.1" '
        f'{status} 100 "-" "ua" {host}'
    )


def audit_write(ts, exe, comm, uid, path, auid=None):
    msg = f"msg=audit({int(ts.timestamp())}.0:1)"
    auid_token = auid if auid is not None else "unset"
    syscall = (
        f'type=SYSCALL {msg}: success=yes exit=0 '
        f'auid={auid_token} uid={uid} comm="{comm}" exe="{exe}" '
        f'key="webshell_write"'
    )
    path_line = f'type=PATH {msg}: item=1 name="{path}" nametype=CREATE'
    return syscall + "\n" + path_line + "\n"


def auth_accepted(ts, user, ip):
    stamp = ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{stamp} host sshd[1]: Accepted password for {user} "
        f"from {ip} port 22 ssh2"
    )


def auth_syslog(ts, user, ip):
    """Traditional syslog stamp: host-local wall clock, no timezone."""
    local = ts.astimezone()
    stamp = f"{_MONTHS[local.month - 1]} {local.day:2d} {local.strftime('%H:%M:%S')}"
    return (
        f"{stamp} host sshd[1]: Accepted password for {user} "
        f"from {ip} port 22 ssh2"
    )


def test_web_upload_high():
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": combined(T, "POST", "/upload.php?x=1", "1.2.3.4", 200, "blog.example"),
        "auth": "",
    })
    assert timeline["entry"] == "web_upload"
    assert timeline["confidence"] == "high"
    assert timeline["actor"]["ip"] == "1.2.3.4"
    assert timeline["status"] == "ok"

def test_web_rce_prior_invokefunction():
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": "\n".join([
            combined(T - timedelta(minutes=10), "GET", "/index.php?s=invokefunction", "1.2.3.4", 200, "blog.example"),
            combined(T, "POST", "/public/x.php", "1.2.3.4", 200, "blog.example"),
        ]),
    })
    assert timeline["entry"] == "web_rce"

def test_ssh_when_shell_and_accepted():
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/bin/bash", comm="bash", uid="root", auid="root", path=FILE),
        "access": "",
        "auth": auth_accepted(T - timedelta(minutes=5), user="root", ip="9.9.9.9"),
    })
    assert timeline["entry"] == "ssh"
    assert timeline["confidence"] == "high"

def test_missing_audit_unique_post_is_medium_with_gap():
    timeline = investigate(anchor(), {
        "audit": None,
        "access": combined(T, "POST", "/up.php", "1.2.3.4", 200, "blog.example"),
        "auth": "",
    })
    assert any("审计" in g for g in timeline["gaps"])
    assert timeline["confidence"] == "medium"
    assert timeline["actor"]["ip"] == "1.2.3.4"

def test_bad_access_format_keeps_audit():
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": "this is not a combined log\n",
        "auth": "",
    })
    assert any("无法解析" in g for g in timeline["gaps"])
    assert timeline["actor"]["exe"] == "/usr/sbin/php-fpm"

def test_alert_truncates_query_json_keeps_full_url():
    long_q = "a" * 400
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": combined(T, "POST", f"/up.php?{long_q}", "1.2.3.4", 200, "blog.example"),
        "auth": "",
    })
    assert long_q in timeline["actor"]["url"]
    body = alert_text(timeline)
    assert "a" * 201 not in body
    assert "置信度: 高" in body

def test_relative_log_path_rejected(tmp_path):
    with pytest.raises(ValueError):
        read_log_tail("relative.log")

def test_global_log_without_hosts_marks_http_gap():
    anc = anchor()
    anc["access_is_global"] = True
    anc["site_hosts"] = []
    timeline = investigate(anc, {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": combined(T, "POST", "/up.php", "1.2.3.4", 200, "blog.example"),
        "auth": "",
    })
    assert any("无法区分站点" in g for g in timeline["gaps"])
    assert timeline["entry"] == "unknown"
    assert timeline["confidence"] == "low"
    assert timeline["actor"]["ip"] == ""


def test_syslog_local_accepted_is_ssh():
    when = T - timedelta(minutes=5)
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/bin/bash", comm="bash", uid="root", auid="root", path=FILE),
        "access": "",
        "auth": auth_syslog(when, user="root", ip="9.9.9.9"),
    })
    assert timeline["entry"] == "ssh"
    assert timeline["actor"]["ip"] == "9.9.9.9"


def _timeline_for_access_file(directory: Path, earliest):
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "access.log"
    log_path.write_text(
        combined(earliest, "GET", "/a.php", "1.2.3.4", 200, "blog.example") + "\n",
        encoding="utf-8",
    )
    (directory / "access.log.1").write_text("rotated\n", encoding="utf-8")
    anc = anchor()
    anc["access_log"] = str(log_path)
    anchor_path = directory / "incident.anchor.json"
    output_path = directory / "timeline.json"
    anchor_path.write_text(json.dumps(anc), encoding="utf-8")
    code = mod.main(["--anchor", str(anchor_path), "--output", str(output_path)])
    body = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    assert code == 0, body
    return json.loads(body)


def test_rotated_gap_only_when_earliest_line_misses_lookback(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBSHELL_INVESTIGATE_CONF", str(tmp_path / "investigate.conf"))
    (tmp_path / "investigate.conf").write_text("", encoding="utf-8")
    # Earliest line sits on the lookback start, so the retained file covers it.
    covered = _timeline_for_access_file(tmp_path / "covered", T - timedelta(minutes=30))
    assert "未读取已轮转日志" not in covered["gaps"]
    # Earliest line is after T-30min, so the retained file does not cover the window.
    missed = _timeline_for_access_file(tmp_path / "missed", T - timedelta(minutes=10))
    assert "未读取已轮转日志" in missed["gaps"]
