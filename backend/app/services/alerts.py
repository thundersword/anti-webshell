"""Global alert channels (email / wechat / dingtalk)."""

from __future__ import annotations

import os
from pathlib import Path

from ..models import AlertConfig, Site, db
from .site_sync import sync_site

CHANNEL_TYPES = ("email", "wechat", "dingtalk")


def ensure_global_alert_rows() -> list[AlertConfig]:
    rows = {
        r.type: r
        for r in AlertConfig.query.filter(AlertConfig.site_pk.is_(None)).all()
        if r.type in CHANNEL_TYPES
    }
    if len(rows) == 3:
        return [rows[t] for t in CHANNEL_TYPES]

    # Seed from first site that has values
    email = wechat = dingtalk = ""
    for site in Site.query.order_by(Site.id.asc()).all():
        if not email and site.alert_email:
            email = site.alert_email or ""
        if not wechat and site.wechat_webhook:
            wechat = site.wechat_webhook or ""
        if not dingtalk and site.dingtalk_webhook:
            dingtalk = site.dingtalk_webhook or ""
        if email or wechat or dingtalk:
            break

    seeds_data = {
        "email": ("alert_email", email),
        "wechat": ("webhook_url", wechat),
        "dingtalk": ("webhook_url", dingtalk),
    }
    out = []
    for typ in CHANNEL_TYPES:
        if typ in rows:
            out.append(rows[typ])
            continue
        field, value = seeds_data[typ]
        kwargs = {"site_pk": None, "type": typ, "enabled": bool(value)}
        kwargs[field] = value or None
        row = AlertConfig(**kwargs)
        db.session.add(row)
        out.append(row)
    # Drop legacy per-site alert_config rows from consideration (delete)
    AlertConfig.query.filter(AlertConfig.site_pk.isnot(None)).delete(
        synchronize_session=False
    )
    db.session.commit()
    return out


def get_global_alert_values() -> dict[str, str]:
    ensure_global_alert_rows()
    values = {"alert_email": "", "wechat_webhook": "", "dingtalk_webhook": ""}
    for row in AlertConfig.query.filter(AlertConfig.site_pk.is_(None)).all():
        if row.type == "email":
            values["alert_email"] = (row.alert_email or "") if row.enabled else ""
        elif row.type == "wechat":
            values["wechat_webhook"] = (row.webhook_url or "") if row.enabled else ""
        elif row.type == "dingtalk":
            values["dingtalk_webhook"] = (row.webhook_url or "") if row.enabled else ""
    return values


def _patch_controlplane_env(values: dict[str, str]) -> bool:
    env_path = Path(
        os.environ.get("WEBSHELL_CONTROLPLANE_ENV", "/etc/webshell-monitor/controlplane.env")
    )
    # In dry-run / missing parent, skip
    if not env_path.parent.exists() and not os.environ.get("WEBSHELL_DRY_RUN"):
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
    if os.environ.get("WEBSHELL_DRY_RUN") and not env_path.parent.exists():
        return False
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    mapping = {
        "CONTROLPLANE_ALERT_EMAIL": values["alert_email"],
        "CONTROLPLANE_WECHAT_WEBHOOK": values["wechat_webhook"],
        "CONTROLPLANE_DINGTALK_WEBHOOK": values["dingtalk_webhook"],
    }
    keys = set(mapping)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else ""
        if key in keys:
            out.append(f'{key}="{mapping[key]}"')
            keys.discard(key)
        else:
            out.append(line)
    for key in keys:
        out.append(f'{key}="{mapping[key]}"')
    try:
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def apply_global_alerts() -> dict:
    values = get_global_alert_values()
    sites_ok = 0
    for site in Site.query.order_by(Site.id.asc()).all():
        site.alert_email = values["alert_email"]
        site.wechat_webhook = values["wechat_webhook"]
        site.dingtalk_webhook = values["dingtalk_webhook"]
        db.session.commit()
        ok, _ = sync_site(site, restart=True)
        if ok:
            sites_ok += 1
    cp = _patch_controlplane_env(values)
    # Re-render controlplane if renderer exists (best-effort)
    from ..executor import get_executor_for_node
    import shutil
    import subprocess

    render = Path(
        os.environ.get("WEBSHELL_INSTALL_DIR", "/opt/webshell_monitor")
    ) / "render_controlplane.sh"
    if cp and render.exists() and not os.environ.get("WEBSHELL_DRY_RUN"):
        subprocess.run([str(render)], check=False, capture_output=True, text=True)
        get_executor_for_node("local").restart_site("controlplane")
    return {"sites_updated": sites_ok, "controlplane_env": cp}