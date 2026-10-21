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
    try:
        lines = min(max(int(request.args.get("lines", 100)), 1), 500)
    except (TypeError, ValueError):
        lines = 100
    if not site:
        return jsonify(logs=[])
    result = get_executor_for_node(site.node_id).tail_logs(site.site_id, lines=lines)
    return jsonify(logs=(result.data or {}).get("logs", []))


def _lock_executor(site, lock):
    executor = get_executor_for_node(site.node_id)
    fn = getattr(executor, "apply_file_lock", None)
    if not callable(fn):
        return None, "executor does not support file lock"
    return fn(site.site_id, lock=lock), None


@api_bp.post("/service/lock")
@jwt_required()
def service_lock():
    site = _current_site()
    if not site:
        return jsonify(error="请先创建站点"), 400
    result, err = _lock_executor(site, True)
    if err:
        return jsonify(error=err), 500
    if not result.ok:
        return jsonify(error=result.message or result.stderr or "锁定失败", detail=result.stdout), 500
    return jsonify(message="locked", site_id=site.site_id, detail=result.stdout)


@api_bp.post("/service/unlock")
@jwt_required()
def service_unlock():
    site = _current_site()
    if not site:
        return jsonify(error="请先创建站点"), 400
    result, err = _lock_executor(site, False)
    if err:
        return jsonify(error=err), 500
    if not result.ok:
        return jsonify(error=result.message or result.stderr or "解锁失败", detail=result.stdout), 500
    return jsonify(message="unlocked", site_id=site.site_id, detail=result.stdout)


@api_bp.get("/service/lock-status")
@jwt_required()
def service_lock_status():
    site = _current_site()
    if not site:
        return jsonify(status="", site_id=None)
    executor = get_executor_for_node(site.node_id)
    fn = getattr(executor, "lock_status", None)
    if not callable(fn):
        return jsonify(status="", site_id=site.site_id)
    result = fn(site.site_id)
    return jsonify(
        status=(result.data or {}).get("status") or result.stdout or "",
        site_id=site.site_id,
        lock_critical_files=bool(getattr(site, "lock_critical_files", False)),
    )
