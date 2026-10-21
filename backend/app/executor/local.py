from __future__ import annotations

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
_STATUS_CACHE = {}
_STATUS_TTL = 5.0


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

    def write_config(self, cfg: SiteRuntimeConfig) -> ExecResult:
        if not SITE_ID_RE.match(cfg.site_id):
            return ExecResult(ok=False, message="invalid site_id")

        self.config_root.mkdir(parents=True, exist_ok=True)
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
            "--no-profile-defaults",
        ]
        if cfg.alert_email:
            args += ["--alert-email", cfg.alert_email]
        if cfg.wechat_webhook:
            args += ["--wechat-webhook", cfg.wechat_webhook]
        if cfg.dingtalk_webhook:
            args += ["--dingtalk-webhook", cfg.dingtalk_webhook]
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
                    f'ALERT_EMAIL="{cfg.alert_email or ""}"',
                    f'WECHAT_WEBHOOK="{cfg.wechat_webhook or ""}"',
                    f'DINGTALK_WEBHOOK="{cfg.dingtalk_webhook or ""}"',
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
            proc = subprocess.run(
                [chattr, flag, str(p)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
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

    def tail_logs(self, site_id: str, lines: int = 100) -> ExecResult:
        log_file = Path(f"/var/log/webshell/{site_id}.log")
        # Allow override via conf
        conf = self.config_root / f"{site_id}.conf"
        if conf.exists():
            for line in conf.read_text(encoding="utf-8").splitlines():
                if line.startswith("LOG_FILE="):
                    log_file = Path(line.split("=", 1)[1].strip().strip('"'))
                    break
        if self.dry_run and not log_file.exists():
            return ExecResult(
                ok=True,
                data={"logs": [f"[dry-run] no log yet for {site_id}"]},
            )
        if not log_file.exists():
            return ExecResult(ok=True, data={"logs": []})
        return ExecResult(ok=True, data={"logs": tail_n_lines(log_file, lines)})

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
