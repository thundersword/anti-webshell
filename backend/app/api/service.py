from flask import jsonify, request
from flask_jwt_extended import jwt_required

from ..executor import get_executor_for_node
from ..models import Site
from . import api_bp
from .sites import _current_site


@api_bp.get("/service/status")
@jwt_required()
def service_status():
    site = _current_site()
    if not site:
        return jsonify(status="stopped", site_id=None)
    result = get_executor_for_node(site.node_id).site_status(site.site_id)
    data = result.data or {"status": "unknown"}
    return jsonify(data)


@api_bp.post("/service/start")
@jwt_required()
def service_start():
    site = _current_site()
    if not site:
        return jsonify(error="请先创建站点"), 400
    site.enabled = True
    from ..models import db

    db.session.commit()
    result = get_executor_for_node(site.node_id).start_site(site.site_id)
    if not result.ok:
        return jsonify(error=result.message or result.stderr or "启动失败"), 500
    return jsonify(message="started", site_id=site.site_id)


@api_bp.post("/service/stop")
@jwt_required()
def service_stop():
    site = _current_site()
    if not site:
        return jsonify(error="请先创建站点"), 400
    site.enabled = False
    from ..models import db

    db.session.commit()
    result = get_executor_for_node(site.node_id).stop_site(site.site_id)
    if not result.ok:
        return jsonify(error=result.message or result.stderr or "停止失败"), 500
    return jsonify(message="stopped", site_id=site.site_id)


@api_bp.post("/service/restart")
@jwt_required()
def service_restart():
    site = _current_site()
    if not site:
        return jsonify(error="请先创建站点"), 400
    result = get_executor_for_node(site.node_id).restart_site(site.site_id)
    if not result.ok:
        return jsonify(error=result.message or result.stderr or "重启失败"), 500
    return jsonify(message="restarted", site_id=site.site_id)


@api_bp.get("/service/logs")
@jwt_required()
def service_logs():
    site = _current_site()
    lines = int(request.args.get("lines", 100))
    if not site:
        return jsonify(logs=[])
    result = get_executor_for_node(site.node_id).tail_logs(site.site_id, lines=lines)
    return jsonify(logs=(result.data or {}).get("logs", []))
