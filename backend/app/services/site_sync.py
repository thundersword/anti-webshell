"""Sync DB site records to on-disk config + systemd via Executor."""

from __future__ import annotations

from ..executor import SiteRuntimeConfig, get_executor_for_node
from ..models import Site, db


def profile_defaults(profile: str, project_dir: str) -> tuple[list[str], list[str]]:
    """Relative defaults mirrored from scripts/profiles (for DB seeding)."""
    defaults = {
        "thinkphp": (
            [
                "public/index.php",
                "public/admin.php",
                "application/config.php",
                "application/database.php",
                "thinkphp/base.php",
            ],
            ["runtime/cache", "runtime/temp"],
        ),
        "laravel": (
            [
                "public/index.php",
                "artisan",
                ".env",
                "bootstrap/app.php",
                "config/app.php",
                "config/database.php",
            ],
            [
                "storage/framework/cache",
                "storage/framework/sessions",
                "storage/framework/views",
                "storage/logs",
                "bootstrap/cache",
            ],
        ),
        "wordpress": (
            [
                "index.php",
                "wp-config.php",
                "wp-login.php",
                "wp-settings.php",
                "wp-load.php",
            ],
            ["wp-content/cache", "wp-content/uploads", "wp-content/upgrade"],
        ),
        "plain_php": (
            ["index.php"],
            ["cache", "tmp", "temp"],
        ),
    }
    crit, white = defaults.get(profile, defaults["plain_php"])
    root = project_dir.rstrip("/")

    def abs_paths(rels):
        return [f"{root}/{r}" if not r.startswith("/") else r for r in rels]

    return abs_paths(crit), abs_paths(white)


def build_runtime_config(site: Site) -> SiteRuntimeConfig:
    crit = [c.file_path for c in site.critical_files if c.enabled]
    white = [w.dir_path for w in site.whitelist_dirs if w.enabled]
    return SiteRuntimeConfig(
        site_id=site.site_id,
        project_dir=site.project_dir,
        profile=site.profile,
        auto_restore=bool(site.auto_restore),
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
