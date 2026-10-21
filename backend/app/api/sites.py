from flask import jsonify, request
from flask_jwt_extended import jwt_required

from ..executor import get_executor_for_node
from ..models import CriticalFile, Site, WhitelistDir, db
from ..services.profiles import detect_profile, profile_defaults
from ..services.site_sync import sync_site
from . import api_bp


def _current_site():
    """Resolve site from ?site_id= or X-Site-Id, else first enabled site."""
    site_id = request.args.get("site_id") or request.headers.get("X-Site-Id")
    if site_id:
        return Site.query.filter_by(site_id=site_id).first()
    return Site.query.order_by(Site.id.asc()).first()


@api_bp.get("/sites")
@jwt_required()
def list_sites():
    sites = Site.query.order_by(Site.id.asc()).all()
    return jsonify([s.to_dict() for s in sites])


@api_bp.get("/sites/<site_id>")
@jwt_required()
def get_site(site_id):
    site = Site.query.filter_by(site_id=site_id).first()
    if not site:
        return jsonify(error="站点不存在"), 404
    return jsonify(site.to_dict(include_lists=True))


@api_bp.post("/sites")
@jwt_required()
def create_site():
    data = request.get_json(silent=True) or {}
    site_id = (data.get("site_id") or "").strip()
    project_dir = (data.get("project_dir") or "").strip()
    name = (data.get("name") or site_id).strip()
    profile = (data.get("profile") or "thinkphp5").strip()
    if profile in ("auto", "detect", ""):
        profile = detect_profile(project_dir).get("profile") or "plain_php"
    node_id = (data.get("node_id") or "local").strip()

    if not site_id or not project_dir:
        return jsonify(error="site_id 与 project_dir 必填"), 400
    if Site.query.filter_by(site_id=site_id).first():
        return jsonify(error="site_id 已存在"), 409

    site = Site(
        site_id=site_id,
        name=name,
        project_dir=project_dir,
        profile=profile,
        node_id=node_id,
        auto_restore=bool(data.get("auto_restore", True)),
        lock_critical_files=bool(data.get("lock_critical_files", True)),
        alert_email=data.get("alert_email"),
        wechat_webhook=data.get("wechat_webhook"),
        dingtalk_webhook=data.get("dingtalk_webhook"),
        enabled=bool(data.get("enabled", True)),
    )
    db.session.add(site)
    db.session.flush()

    crit_paths = data.get("critical_files")
    white_paths = data.get("whitelist_dirs")
    if not crit_paths:
        crit_paths, white_default = profile_defaults(profile, project_dir)
        if not white_paths:
            white_paths = white_default
    if not white_paths:
        _, white_paths = profile_defaults(profile, project_dir)

    for p in crit_paths:
        db.session.add(CriticalFile(site_pk=site.id, file_path=p, enabled=True))
    for p in white_paths:
        db.session.add(WhitelistDir(site_pk=site.id, dir_path=p, enabled=True))

    db.session.commit()

    ok, msg = sync_site(site, restart=bool(data.get("start", True)))
    if data.get("start", True) and ok:
        get_executor_for_node(site.node_id).start_site(site.site_id)

    payload = site.to_dict(include_lists=True)
    payload.update({"sync_ok": ok, "sync_message": msg})
    return jsonify(payload), 201


@api_bp.put("/sites/<site_id>")
@jwt_required()
def update_site(site_id):
    site = Site.query.filter_by(site_id=site_id).first()
    if not site:
        return jsonify(error="站点不存在"), 404
    data = request.get_json(silent=True) or {}
    for field in (
        "name",
        "project_dir",
        "profile",
        "node_id",
        "alert_email",
        "wechat_webhook",
        "dingtalk_webhook",
    ):
        if field in data:
            setattr(site, field, data[field])
    if "auto_restore" in data:
        site.auto_restore = bool(data["auto_restore"])
    if "lock_critical_files" in data:
        site.lock_critical_files = bool(data["lock_critical_files"])
    if "enabled" in data:
        site.enabled = bool(data["enabled"])
    db.session.commit()
    ok, msg = sync_site(site, restart=True)
    payload = site.to_dict(include_lists=True)
    payload.update({"sync_ok": ok, "sync_message": msg})
    return jsonify(payload)


@api_bp.delete("/sites/<site_id>")
@jwt_required()
def delete_site(site_id):
    site = Site.query.filter_by(site_id=site_id).first()
    if not site:
        return jsonify(error="站点不存在"), 404
    executor = get_executor_for_node(site.node_id)
    executor.remove_site_config(site.site_id)
    db.session.delete(site)
    db.session.commit()
    return jsonify(message="deleted")
