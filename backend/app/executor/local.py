from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from flask import current_app

from .base import ExecResult, Executor, SiteRuntimeConfig

SITE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


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
        r = self._run(["systemctl", "enable", f"webshell-monitor@{site_id}.service"])
        if not r.ok and not self.dry_run:
            return r
        return self._run(["systemctl", "start", f"webshell-monitor@{site_id}.service"])

    def stop_site(self, site_id: str) -> ExecResult:
        return self._run(["systemctl", "stop", f"webshell-monitor@{site_id}.service"])

    def restart_site(self, site_id: str) -> ExecResult:
        return self._run(["systemctl", "restart", f"webshell-monitor@{site_id}.service"])

    def site_status(self, site_id: str) -> ExecResult:
        if self.dry_run:
            return ExecResult(
                ok=True,
                message="dry-run",
                data={"status": "stopped", "site_id": site_id},
            )
        proc = subprocess.run(
            ["systemctl", "is-active", f"webshell-monitor@{site_id}.service"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        state = (proc.stdout or "").strip() or "unknown"
        mapped = {
            "active": "running",
            "inactive": "stopped",
            "failed": "error",
        }.get(state, state)
        return ExecResult(
            ok=True,
            message=state,
            data={"status": mapped, "raw": state, "site_id": site_id},
        )

    def remove_site_config(self, site_id: str) -> ExecResult:
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
        content = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        return ExecResult(ok=True, data={"logs": content[-lines:]})

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
