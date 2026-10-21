"""Sync DB site records to on-disk config + systemd via Executor."""

from __future__ import annotations

from ..executor import SiteRuntimeConfig, get_executor_for_node
from ..models import Site, db
from .profiles import profile_defaults  # re-export for existing imports


def build_runtime_config(site: Site) -> SiteRuntimeConfig:
    crit = [c.file_path for c in site.critical_files if c.enabled]
    white = [w.dir_path for w in site.whitelist_dirs if w.enabled]
    return SiteRuntimeConfig(
        site_id=site.site_id,
        project_dir=site.project_dir,
        profile=site.profile,
        auto_restore=bool(site.auto_restore),
        lock_critical_files=bool(getattr(site, "lock_critical_files", True)),
        alert_email=site.alert_email or "",
        wechat_webhook=site.wechat_webhook or "",
        dingtalk_webhook=site.dingtalk_webhook or "",
        critical_files=crit,
        whitelist_dirs=white,
    )


def sync_site(site: Site, restart: bool = True) -> tuple[bool, str]:
    executor = get_executor_for_node(site.node_id or "local")
    result = executor.write_config(build_runtime_config(site))
    if not result.ok:
        site.sync_status = "error"
        site.sync_message = result.message or result.stderr
        db.session.commit()
        return False, site.sync_message

    lock_on = bool(getattr(site, "lock_critical_files", True))
    lock_fn = getattr(executor, "apply_file_lock", None)
    if callable(lock_fn):
        lock_fn(site.site_id, lock=lock_on)

    if restart and site.enabled:
        r2 = executor.restart_site(site.site_id)
        if not r2.ok:
            # config ok but restart failed — still mark sync warn
            site.sync_status = "error"
            site.sync_message = f"config ok; restart failed: {r2.message} {r2.stderr}"
            db.session.commit()
            return False, site.sync_message

    site.sync_status = "synced"
    site.sync_message = "ok"
    db.session.commit()
    return True, "ok"
