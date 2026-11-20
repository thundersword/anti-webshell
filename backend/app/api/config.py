from flask import jsonify, request
from flask_jwt_extended import jwt_required

from ..models import AlertConfig, CriticalFile, WhitelistDir, db
from ..services.alerts import apply_global_alerts, ensure_global_alert_rows
from ..services.site_sync import sync_site
from . import api_bp
from .sites import _current_site


@api_bp.get("/config/critical-files")
@jwt_required()
def list_critical_files():
    site = _current_site()
    if not site:
        return jsonify([])
    return jsonify([c.to_dict() for c in site.critical_files.order_by(CriticalFile.id)])


@api_bp.post("/config/critical-files")
@jwt_required()
def add_critical_file():
    site = _current_site()
    if not site:
        return jsonify(error="请先创建站点"), 400
    data = request.get_json(silent=True) or {}
    path = (data.get("file_path") or "").strip()
    if not path:
        return jsonify(error="file_path 必填"), 400
    if not path.startswith("/"):
        path = f"{site.project_dir.rstrip('/')}/{path}"
    row = CriticalFile(site_pk=site.id, file_path=path, enabled=True)
    db.session.add(row)
    db.session.commit()
    sync_site(site, restart=True)
    return jsonify(row.to_dict()), 201


@api_bp.delete("/config/critical-files/<int:item_id>")
@jwt_required()
def delete_critical_file(item_id):
    row = CriticalFile.query.get(item_id)
    if not row:
        return jsonify(error="不存在"), 404
    site = row.site
    db.session.delete(row)
    db.session.commit()
    sync_site(site, restart=True)
    return jsonify(message="deleted")


@api_bp.get("/config/whitelist-dirs")
@jwt_required()
def list_whitelist_dirs():
    site = _current_site()
    if not site:
        return jsonify([])
    return jsonify([w.to_dict() for w in site.whitelist_dirs.order_by(WhitelistDir.id)])


@api_bp.post("/config/whitelist-dirs")
@jwt_required()
def add_whitelist_dir():
    site = _current_site()
    if not site:
        return jsonify(error="请先创建站点"), 400
    data = request.get_json(silent=True) or {}
    path = (data.get("dir_path") or "").strip()
    if not path:
        return jsonify(error="dir_path 必填"), 400
    if not path.startswith("/"):
        path = f"{site.project_dir.rstrip('/')}/{path}"
    row = WhitelistDir(site_pk=site.id, dir_path=path, enabled=True)
    db.session.add(row)
    db.session.commit()
    sync_site(site, restart=True)
    return jsonify(row.to_dict()), 201


@api_bp.delete("/config/whitelist-dirs/<int:item_id>")
@jwt_required()
def delete_whitelist_dir(item_id):
    row = WhitelistDir.query.get(item_id)
    if not row:
        return jsonify(error="不存在"), 404
    site = row.site
    db.session.delete(row)
    db.session.commit()
    sync_site(site, restart=True)
    return jsonify(message="deleted")


@api_bp.get("/config/alert-config")
@jwt_required()
def list_alert_config():
    rows = ensure_global_alert_rows()
    return jsonify([r.to_dict() for r in rows])


def _apply_alert_row_fields(row: AlertConfig, data: dict) -> None:
    if "enabled" in data:
        row.enabled = bool(data["enabled"])
    if "webhook_url" in data:
        row.webhook_url = data["webhook_url"]
    if "alert_email" in data:
        row.alert_email = data["alert_email"]


@api_bp.put("/config/alert-config")
@jwt_required()
def update_alert_config_batch():
    """Update multiple global channels then fan-out once."""
    data = request.get_json(silent=True) or {}
    channels = data.get("channels") or []
    ensure_global_alert_rows()
    by_id = {
        r.id: r
        for r in AlertConfig.query.filter(AlertConfig.site_pk.is_(None)).all()
    }
    updated = []
    for item in channels:
        if not isinstance(item, dict):
            continue
        row = by_id.get(item.get("id"))
        if not row:
            continue
        _apply_alert_row_fields(row, item)
        updated.append(row)
    db.session.commit()
    apply_result = apply_global_alerts()
    return jsonify(
        channels=[r.to_dict() for r in ensure_global_alert_rows()],
        apply=apply_result,
        updated=len(updated),
    )


@api_bp.put("/config/alert-config/<int:item_id>")
@jwt_required()
def update_alert_config(item_id):
    row = AlertConfig.query.get(item_id)
    if not row or row.site_pk is not None:
        return jsonify(error="不存在"), 404
    data = request.get_json(silent=True) or {}
    _apply_alert_row_fields(row, data)
    db.session.commit()
    apply_global_alerts()
    return jsonify(row.to_dict())
