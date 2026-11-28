from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from flask import current_app

from .base import ExecResult, Executor, SiteRuntimeConfig

SITE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
# Incident ids are YYYYmmddTHHMMSSZ_<8 hex>, so '_' is required. The path
# check still rejects separators other than that single underscore class.
INCIDENT_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
SYNC_AUDIT_COMMAND = "sudo /opt/webshell_monitor/manage_sites.sh sync-audit"
CHECK_AUDIT_COMMAND = "sudo auditctl -l | grep webshell"
_STATUS_CACHE = {}
_STATUS_TTL = 5.0


def _conf_value(text: str, key: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return ""


def _backup_key(file_path: str, watch_dirs: List[str], project_dir: str) -> str:
    """Match webshell_monitor.sh backup_key_for_file."""
    best_len = 0
    best_root = ""
    for root in watch_dirs:
        root = root.rstrip("/")
        if file_path == root or file_path.startswith(root + "/"):
            if len(root) > best_len:
                best_len = len(root)
                best_root = root
    project = (project_dir or "").rstrip("/")
    if best_root:
        prefix = best_root + "/"
        rel = file_path[len(prefix):] if file_path.startswith(prefix) else file_path
    elif project and file_path.startswith(project + "/"):
        rel = file_path[len(project) + 1:]
    else:
        rel = os.path.basename(file_path)
    return rel.replace("/", "__")


def tail_n_lines(path, n=100):
    """Read last n lines without loading the whole file (avoids iowait on large logs)."""
    n = max(1, min(int(n), 2000))
    path = Path(path)
    if not path.exists():
        return []
    block = 8192
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            step = min(block, size)
            size -= step
            f.seek(size)
            data = f.read(step) + data
            if size == 0:
                break
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-n:]


class LocalSystemdExecutor(Executor):
    """Render conf files and drive systemd template units on this host."""

    def __init__(
        self,
        install_dir=None,  # type: Optional[str]
        config_root=None,  # type: Optional[str]
        dry_run=None,  # type: Optional[bool]
    ):
        self.install_dir = Path(
            install_dir
            or os.getenv("WEBSHELL_INSTALL_DIR")
            or current_app.config["WEBSHELL_INSTALL_DIR"]
        )
        self.config_root = Path(
            config_root
            or os.getenv("WEBSHELL_CONFIG_ROOT")
            or current_app.config["WEBSHELL_CONFIG_ROOT"]
        )
        if dry_run is None:
            dry_run = bool(current_app.config.get("WEBSHELL_DRY_RUN"))
        self.dry_run = dry_run

    def _run(self, args: List[str], timeout: int = 60) -> ExecResult:
        if self.dry_run:
            return ExecResult(ok=True, message="dry-run", stdout=" ".join(args))
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            ok = proc.returncode == 0
            return ExecResult(
                ok=ok,
                message="ok" if ok else f"exit {proc.returncode}",
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
            )
        except Exception as exc:  # noqa: BLE001
            return ExecResult(ok=False, message=str(exc))

    def _config_write_targets(self, site_id: str) -> List[Path]:
        return [
            self.config_root / f"{site_id}.conf",
            self.config_root / f"{site_id}.critical",
            self.config_root / f"{site_id}.whitelist",
        ]

    def _allow_config_write(self, path: Path) -> None:
        """Same stamp the running monitor checks before it treats a write as tampering."""
        digest = hashlib.sha256(str(path).encode()).hexdigest()
        directory = Path(os.environ.get(
            "WEBSHELL_CONFIG_WRITE_DIR",
            "/run/webshell-monitor/config-writes",
        ))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / digest).write_text(str(path) + "\n", encoding="utf-8")

    def _file_is_immutable(self, path: Path) -> bool:
        if not path.is_file():
            return False
        lsattr = shutil.which("lsattr")
        if not lsattr:
            return False
        try:
            proc = subprocess.run(
                [lsattr, "-d", str(path)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:  # noqa: BLE001
            return False
        flags = (proc.stdout or "").split()
        return bool(flags) and "i" in flags[0]

    def _chattr(self, path: Path, flag: str) -> None:
        if not path.is_file():
            return
        chattr = shutil.which("chattr")
        if not chattr:
            return
        try:
            subprocess.run(
                [chattr, flag, str(path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:  # noqa: BLE001
            return

    def _controlplane_backup_for(self, path: Path) -> Optional[Path]:
        conf = self.config_root / "controlplane.conf"
        if not conf.is_file():
            return None
        text = conf.read_text(encoding="utf-8", errors="replace")
        backup_dir = _conf_value(text, "BACKUP_DIR")
        project_dir = _conf_value(text, "PROJECT_DIR")
        watch_file = _conf_value(text, "WATCH_DIRS_FILE")
        if not backup_dir:
            return None
        watch_dirs = []
        watch_path = Path(watch_file) if watch_file else None
        if watch_path and watch_path.is_file():
            for line in watch_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and Path(line).is_dir():
                    watch_dirs.append(line)
        key = _backup_key(str(path), watch_dirs, project_dir)
        if not key:
            return None
        return Path(backup_dir) / f"{key}.backup"

    def _refresh_controlplane_backup(self, path: Path) -> None:
        if not path.is_file():
            return
        dest = self._controlplane_backup_for(path)
        if dest is None:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    def write_config(self, cfg: SiteRuntimeConfig) -> ExecResult:
        if not SITE_ID_RE.match(cfg.site_id):
            return ExecResult(ok=False, message="invalid site_id")

        self.config_root.mkdir(parents=True, exist_ok=True)
        targets = self._config_write_targets(cfg.site_id)
        locked = []
        if not self.dry_run and (self.config_root / "controlplane.conf").is_file():
            for path in targets:
                try:
                    was_locked = self._file_is_immutable(path)
                    self._allow_config_write(path)
                    if was_locked:
                        self._chattr(path, "-i")
                    locked.append(was_locked)
                except Exception:  # noqa: BLE001
                    locked.append(False)
        try:
            return self._write_config_unlocked(cfg)
        finally:
            if locked:
                for path, was_locked in zip(targets, locked):
                    try:
                        self._refresh_controlplane_backup(path)
                        if was_locked:
                            self._chattr(path, "+i")
                    except Exception:  # noqa: BLE001
                        continue

    def _write_config_unlocked(self, cfg: SiteRuntimeConfig) -> ExecResult:
        render = self.install_dir / "render_site_config.sh"
        if not render.exists():
            # Fallback: write conf directly without shell helper
            return self._write_config_direct(cfg)

        args = [
            str(render),
            cfg.site_id,
            cfg.project_dir,
            cfg.profile,
            "--output-dir",
            str(self.config_root),
            "--auto-restore",
            "true" if cfg.auto_restore else "false",
            "--lock-critical",
            "true" if cfg.lock_critical_files else "false",
            "--track-whitelist",
            "true" if cfg.track_whitelist else "false",
            "--no-profile-defaults",
        ]
        if cfg.alert_email:
            args += ["--alert-email", cfg.alert_email]
        if cfg.wechat_webhook:
            args += ["--wechat-webhook", cfg.wechat_webhook]
        if cfg.dingtalk_webhook:
            args += ["--dingtalk-webhook", cfg.dingtalk_webhook]
        if cfg.access_log:
            args += ["--access-log", cfg.access_log]
        if cfg.site_hosts:
            args += ["--site-hosts", cfg.site_hosts]
        for path in cfg.critical_files:
            args += ["--critical-file", path]
        for path in cfg.whitelist_dirs:
            args += ["--whitelist-dir", path]

        # When overriding lists entirely, still need profile for extensions/exclude
        # Re-run without --no-profile-defaults if lists empty to seed defaults
        if not cfg.critical_files and not cfg.whitelist_dirs:
            args = [a for a in args if a != "--no-profile-defaults"]

        env = os.environ.copy()
        env["PROFILES_DIR"] = str(self.install_dir / "profiles")
        if self.dry_run:
            return self._write_config_direct(cfg)

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=env,
            )
            if proc.returncode != 0:
                return ExecResult(
                    ok=False,
                    message="render failed",
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                )
            return ExecResult(ok=True, message="config written", stdout=proc.stdout)
        except Exception as exc:  # noqa: BLE001
            return ExecResult(ok=False, message=str(exc))

    def _write_config_direct(self, cfg: SiteRuntimeConfig) -> ExecResult:
        """Used in dry-run / when render script missing."""
        self.config_root.mkdir(parents=True, exist_ok=True)
        critical = self.config_root / f"{cfg.site_id}.critical"
        whitelist = self.config_root / f"{cfg.site_id}.whitelist"
        conf = self.config_root / f"{cfg.site_id}.conf"

        def abs_path(p: str) -> str:
            return p if p.startswith("/") else f"{cfg.project_dir.rstrip('/')}/{p}"

        crit_paths = [abs_path(p) for p in cfg.critical_files]
        white_paths = [abs_path(p) for p in cfg.whitelist_dirs]
        critical.write_text("\n".join(crit_paths) + ("\n" if crit_paths else ""), encoding="utf-8")
        whitelist.write_text("\n".join(white_paths) + ("\n" if white_paths else ""), encoding="utf-8")
        conf.write_text(
            "\n".join(
                [
                    f'SITE_ID="{cfg.site_id}"',
                    f'PROJECT_DIR="{cfg.project_dir}"',
                    f'PROFILE="{cfg.profile}"',
                    f'LOG_FILE="/var/log/webshell/{cfg.site_id}.log"',
                    f'QUARANTINE_DIR="/var/quarantine/{cfg.site_id}"',
                    f'BACKUP_DIR="/var/webshell_backup/{cfg.site_id}"',
                    f'AUTO_RESTORE="{"true" if cfg.auto_restore else "false"}"',
                    f'LOCK_CRITICAL_FILES="{"true" if cfg.lock_critical_files else "false"}"',
                    f'TRACK_WHITELIST="{"true" if cfg.track_whitelist else "false"}"',
                    f'ALERT_EMAIL="{cfg.alert_email or ""}"',
                    f'WECHAT_WEBHOOK="{cfg.wechat_webhook or ""}"',
                    f'DINGTALK_WEBHOOK="{cfg.dingtalk_webhook or ""}"',
                    f'ACCESS_LOG="{cfg.access_log or ""}"',
                    f'SITE_HOSTS="{cfg.site_hosts or ""}"',
                    f'CRITICAL_FILES_FILE="{critical}"',
                    f'WHITELIST_DIRS_FILE="{whitelist}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return ExecResult(ok=True, message="config written (direct)")

    def start_site(self, site_id: str) -> ExecResult:
        _STATUS_CACHE.pop(site_id, None)
        r = self._run(["systemctl", "enable", f"webshell-monitor@{site_id}.service"])
        if not r.ok and not self.dry_run:
            return r
        return self._run(["systemctl", "start", f"webshell-monitor@{site_id}.service"])

    def stop_site(self, site_id: str) -> ExecResult:
        _STATUS_CACHE.pop(site_id, None)
        return self._run(["systemctl", "stop", f"webshell-monitor@{site_id}.service"])

    def restart_site(self, site_id: str) -> ExecResult:
        _STATUS_CACHE.pop(site_id, None)
        return self._run(["systemctl", "restart", f"webshell-monitor@{site_id}.service"])

    def site_status(self, site_id: str) -> ExecResult:
        now = time.monotonic()
        cached = _STATUS_CACHE.get(site_id)
        if cached and now - cached[0] < _STATUS_TTL:
            return cached[1]
        if self.dry_run:
            result = ExecResult(
                ok=True,
                message="dry-run",
                data={"status": "stopped", "site_id": site_id},
            )
            _STATUS_CACHE[site_id] = (now, result)
            return result
        proc = subprocess.run(
            ["systemctl", "is-active", f"webshell-monitor@{site_id}.service"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        state = (proc.stdout or "").strip() or "unknown"
        mapped = {
            "active": "running",
            "inactive": "stopped",
            "failed": "error",
        }.get(state, state)
        result = ExecResult(
            ok=True,
            message=state,
            data={"status": mapped, "raw": state, "site_id": site_id},
        )
        _STATUS_CACHE[site_id] = (now, result)
        return result

    def _critical_file_list(self, site_id: str) -> List[str]:
        list_file = self.config_root / f"{site_id}.critical"
        if not list_file.exists():
            return []
        paths = []
        for line in list_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                paths.append(line)
        return paths

    def apply_file_lock(self, site_id: str, lock: bool = True) -> ExecResult:
        """Lock/unlock critical files with chattr, without relying on monitor CLI."""
        paths = self._critical_file_list(site_id)
        if not paths:
            return ExecResult(
                ok=False,
                message="未找到关键文件列表，请先保存/同步站点配置",
            )
        if self.dry_run:
            lines = [f"[dry-run] {'锁定' if lock else '解锁'} {p}" for p in paths]
            text = "\n".join(lines) + "\n"
            return ExecResult(
                ok=True,
                message="dry-run",
                stdout=text,
                data={"locked": lock, "site_id": site_id, "files": paths},
            )
        chattr = shutil.which("chattr")
        if not chattr:
            return ExecResult(
                ok=False,
                message="未找到 chattr，请安装 e2fsprogs，并以 root 运行 API",
            )
        flag = "+i" if lock else "-i"
        ok_n = 0
        fail_n = 0
        lines = []
        for path in paths:
            p = Path(path)
            if not p.is_file():
                lines.append(f"跳过（不存在） {path}")
                continue
            try:
                proc = subprocess.run(
                    [chattr, flag, str(p)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except Exception as exc:  # noqa: BLE001
                fail_n += 1
                lines.append(f"失败  {path}  {exc}")
                continue
            if proc.returncode == 0:
                ok_n += 1
                lines.append(f"{'已锁定' if lock else '已解锁'}  {path}")
            else:
                fail_n += 1
                err = (proc.stderr or proc.stdout or "").strip()
                lines.append(f"失败  {path}  {err}")
        text = "\n".join(lines) + "\n"
        if ok_n == 0:
            return ExecResult(
                ok=False,
                message="没有文件锁定成功（需 root，且磁盘支持 immutable）",
                stdout=text,
                stderr=text,
            )
        return ExecResult(
            ok=True,
            message=f"成功 {ok_n}，失败 {fail_n}",
            stdout=text,
            data={"locked": lock, "ok": ok_n, "fail": fail_n, "site_id": site_id},
        )

    def lock_status(self, site_id: str) -> ExecResult:
        paths = self._critical_file_list(site_id)
        lines = [f"关键文件锁定状态 [site={site_id}]"]
        lines.append("----------------------------------------")
        if self.dry_run:
            for path in paths:
                lines.append(f"  (dry-run)  {path}")
            text = "\n".join(lines) + "\n"
            return ExecResult(ok=True, stdout=text, data={"status": text, "site_id": site_id})
        lsattr = shutil.which("lsattr")
        for path in paths:
            p = Path(path)
            if not p.is_file():
                lines.append(f"  缺失    {path}")
                continue
            locked = False
            if lsattr:
                proc = subprocess.run(
                    [lsattr, "-d", str(p)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                flags = (proc.stdout or "").split(" ", 1)[0]
                locked = "i" in flags
            lines.append(f"  {'已锁定' if locked else '未锁定'}  {path}")
        text = "\n".join(lines) + "\n"
        return ExecResult(
            ok=True,
            stdout=text,
            data={"status": text, "site_id": site_id},
        )

    def remove_site_config(self, site_id: str) -> ExecResult:
        self.apply_file_lock(site_id, lock=False)
        self.stop_site(site_id)
        if not self.dry_run:
            self._run(["systemctl", "disable", f"webshell-monitor@{site_id}.service"])
        for suffix in (".conf", ".critical", ".whitelist"):
            path = self.config_root / f"{site_id}{suffix}"
            if path.exists():
                path.unlink()
        return ExecResult(ok=True, message="removed")

    def _configured_log_file(self, site_id: str) -> Path:
        """Use a LOG_FILE= path that exists; otherwise the last declared path."""
        log_file = Path(f"/var/log/webshell/{site_id}.log")
        conf = self.config_root / f"{site_id}.conf"
        if not conf.exists():
            return log_file
        fallback = None
        existing = None
        for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped.startswith("LOG_FILE="):
                continue
            raw = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            if not raw:
                continue
            candidate = Path(raw)
            fallback = candidate
            if candidate.exists():
                existing = candidate
        return existing or fallback or log_file

    def tail_logs(self, site_id: str, lines: int = 100) -> ExecResult:
        log_file = self._configured_log_file(site_id)
        if log_file.exists():
            return ExecResult(ok=True, data={"logs": tail_n_lines(log_file, lines)})
        if self.dry_run:
            return ExecResult(
                ok=True,
                data={"logs": [f"[dry-run] no log yet for {site_id}"]},
            )
        return ExecResult(ok=True, data={"logs": []})

    def _project_dir_from_conf(self, site_id: str) -> str:
        conf = self.config_root / f"{site_id}.conf"
        if not conf.is_file():
            return ""
        for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("PROJECT_DIR="):
                return stripped.split("=", 1)[1].strip().strip('"')
        return ""

    def read_incident(self, site_id: str, incident_id: str) -> ExecResult:
        if not site_id or not SITE_ID_RE.match(site_id):
            return ExecResult(ok=False, message="invalid site_id")
        if not incident_id or not INCIDENT_ID_RE.match(incident_id):
            return ExecResult(ok=False, message="invalid incident_id")
        root = os.environ.get("WEBSHELL_INCIDENTS_DIR") or ""
        if not root:
            try:
                root = current_app.config.get("WEBSHELL_INCIDENTS_DIR") or ""
            except Exception:  # noqa: BLE001
                root = ""
        root_path = Path(root or "/var/lib/webshell/incidents")
        path = root_path / site_id / f"{incident_id}.json"
        if not path.is_file():
            return ExecResult(ok=False, message="missing")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return ExecResult(ok=False, message=str(exc))
        if not isinstance(payload, dict):
            return ExecResult(ok=False, message="invalid timeline")
        return ExecResult(ok=True, data={"timeline": payload})

    def audit_status(self, site_id: str) -> ExecResult:
        data = {
            "auditd": "unknown",
            "rules_loaded": False,
            "sync_command": SYNC_AUDIT_COMMAND,
            "check_command": CHECK_AUDIT_COMMAND,
        }
        if self.dry_run:
            return ExecResult(ok=True, message="dry-run", data=data)
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", "auditd"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            data["auditd"] = (proc.stdout or "").strip() or "unknown"
        except Exception:  # noqa: BLE001
            data["auditd"] = "unknown"
        output = ""
        try:
            rules = subprocess.run(
                ["auditctl", "-l"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            output = rules.stdout or ""
        except Exception:  # noqa: BLE001
            output = ""
        project_dir = self._project_dir_from_conf(site_id)
        data["rules_loaded"] = bool(
            project_dir and "webshell_write" in output and project_dir in output
        )
        return ExecResult(ok=True, data=data)

    def investigate_path(self, site_id: str, file_path: str, quarantine: bool = False) -> ExecResult:
        if not SITE_ID_RE.match(site_id):
            return ExecResult(ok=False, message="invalid site_id")
        script = self.install_dir / "webshell_monitor.sh"
        args = [str(script), "--site", site_id, "--investigate", file_path]
        if quarantine:
            args.append("--quarantine")
        if self.dry_run:
            return ExecResult(
                ok=True,
                message="dry-run",
                stdout="incident=dryrun0001\n" + " ".join(args),
                data={"incident_id": "dryrun0001", "command": args},
            )
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return ExecResult(ok=False, message=str(exc))
        blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
        match = re.search(r"incident=([A-Za-z0-9_]+)", blob)
        incident_id = match.group(1) if match else ""
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "追踪失败").strip()
            message = detail.splitlines()[-1] if detail else "追踪失败"
            return ExecResult(
                ok=False,
                message=message,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
            )
        if not incident_id:
            return ExecResult(ok=False, message="未获得 incident_id", stdout=proc.stdout or "")
        return ExecResult(
            ok=True,
            message="started",
            stdout=proc.stdout or "",
            data={"incident_id": incident_id, "command": args},
        )

    def sync_audit(self) -> ExecResult:
        script = self.install_dir / "sync_audit.sh"
        if self.dry_run:
            return self._run([str(script)])
        env = os.environ.copy()
        env["CONFIG_ROOT"] = str(self.config_root)
        try:
            proc = subprocess.run(
                [str(script)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=env,
            )
            ok = proc.returncode == 0
            return ExecResult(
                ok=ok,
                message="ok" if ok else f"exit {proc.returncode}",
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
            )
        except Exception as exc:  # noqa: BLE001
            return ExecResult(ok=False, message=str(exc))

    def list_quarantine(self, site_id: str) -> ExecResult:
        qdir = Path(f"/var/quarantine/{site_id}")
        if not qdir.exists():
            return ExecResult(ok=True, data={"files": []})
        files = sorted([p.name for p in qdir.iterdir() if p.is_file()])
        return ExecResult(ok=True, data={"files": files})

    def list_profiles(self) -> ExecResult:
        profiles_dir = self.install_dir / "profiles"
        if not profiles_dir.exists():
            return ExecResult(ok=True, data={"profiles": ["thinkphp"]})
        names = sorted(p.stem for p in profiles_dir.glob("*.conf"))
        return ExecResult(ok=True, data={"profiles": names})


class RemoteAgentExecutor(Executor):
    """Placeholder for future multi-host agents. Not implemented."""

    def __init__(self, endpoint: str, token: str):
        self.endpoint = endpoint
        self.token = token

    def _nyi(self) -> ExecResult:
        return ExecResult(ok=False, message="RemoteAgentExecutor not implemented yet")

    def write_config(self, cfg: SiteRuntimeConfig) -> ExecResult:
        return self._nyi()

    def start_site(self, site_id: str) -> ExecResult:
        return self._nyi()

    def stop_site(self, site_id: str) -> ExecResult:
        return self._nyi()

    def restart_site(self, site_id: str) -> ExecResult:
        return self._nyi()

    def site_status(self, site_id: str) -> ExecResult:
        return self._nyi()

    def remove_site_config(self, site_id: str) -> ExecResult:
        return self._nyi()

    def tail_logs(self, site_id: str, lines: int = 100) -> ExecResult:
        return self._nyi()

    def list_quarantine(self, site_id: str) -> ExecResult:
        return self._nyi()

    def list_profiles(self) -> ExecResult:
        return self._nyi()

    def read_incident(self, site_id: str, incident_id: str) -> ExecResult:
        return self._nyi()

    def audit_status(self, site_id: str) -> ExecResult:
        return self._nyi()

    def investigate_path(self, site_id: str, file_path: str, quarantine: bool = False) -> ExecResult:
        return self._nyi()


def get_executor_for_node(node_id: str = "local") -> Executor:
    """Factory: local node -> LocalSystemdExecutor; others -> RemoteAgent (future)."""
    if node_id in (None, "", "local"):
        return LocalSystemdExecutor()
    from ..models import Node

    node = Node.query.get(node_id)
    if not node or node.kind == "local":
        return LocalSystemdExecutor()
    if node.kind == "agent":
        return RemoteAgentExecutor(node.endpoint or "", token="")
    return LocalSystemdExecutor()
