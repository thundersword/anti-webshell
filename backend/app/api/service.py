import re
from pathlib import Path

from flask import jsonify, request
from flask_jwt_extended import jwt_required

from ..executor import get_executor_for_node
from ..models import Site
from . import api_bp
from .sites import CONTROLPLANE_ID, _current_site

_PROJECT_DIR_RE = re.compile(r'^PROJECT_DIR="([^"]*)"')


def _read_project_dir_from_conf(conf_path: Path) -> str:
    if not conf_path.is_file():
        return ""
    for line in conf_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _PROJECT_DIR_RE.match(line.strip())
        if m:
            return m.group(1)
    return ""


def _read_watch_dirs(watch_path: Path) -> list:
    if not watch_path.is_file():
        return []
    dirs = []
    for line in watch_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            dirs.append(line)
    return dirs


@api_bp.get("/service/audit-status")
@jwt_required()
def service_audit_status():
    site = _current_site()
    if not site:
        return jsonify(error="请先创建站点"), 400
    result = get_executor_for_node(site.node_id).audit_status(site.site_id)
    if not result.ok:
        return jsonify(error=result.message or "审计状态获取失败"), 500
    return jsonify(result.data or {})


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


def _self_guard_payload():
    site_id = CONTROLPLANE_ID
    site = Site.query.filter_by(site_id=site_id).first()
    executor = get_executor_for_node(site.node_id if site else "local")
    conf_path = executor.config_root / f"{site_id}.conf"
    watch_path = executor.config_root / f"{site_id}.watch"

    project_dir = _read_project_dir_from_conf(conf_path)
    if not project_dir and site:
        project_dir = site.project_dir or ""

    if executor.dry_run:
        unit_active = "unknown"
    else:
        st = executor.site_status(site_id)
        raw = (st.data or {}).get("raw") or st.message or "unknown"
        unit_active = raw.strip() or "unknown"

    lock_fn = getattr(executor, "lock_status", None)
    lock_status = ""
    if callable(lock_fn):
        lock_result = lock_fn(site_id)
        lock_status = (lock_result.data or {}).get("status") or lock_result.stdout or ""

    return {
        "site_id": site_id,
        "unit_active": unit_active,
        "lock_status": lock_status,
        "project_dir": project_dir,
        "watch_dirs": _read_watch_dirs(watch_path),
    }


@api_bp.get("/self-guard")
@api_bp.get("/service/self-guard")
@jwt_required()
def self_guard_status():
    """Control-plane status; ignore any site_id / X-Site-Id on the request."""
    return jsonify(_self_guard_payload())


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
