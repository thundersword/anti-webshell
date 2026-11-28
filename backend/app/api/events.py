import re
import time
from datetime import datetime, timedelta

from flask import jsonify, request
from flask_jwt_extended import jwt_required

from ..executor import get_executor_for_node
from ..models import MonitorEvent, Site, db
from . import api_bp
from .sites import _current_site

LOG_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<level>[^\]]+)\](?:\s+\[site=(?P<site>[^\]]+)\])?\s+(?P<msg>.*)$"
)
# Underscore is part of YYYYmmddTHHMMSSZ_<hex> incident ids.
INCIDENT_RE = re.compile(r"incident=([A-Za-z0-9_]+)")
_INGEST_AT = {}
_INGEST_MIN_INTERVAL = 30
_STATS_CACHE = {}
_STATS_TTL = 5.0


def _ingest_from_log(site: Site, limit: int = 200, force: bool = False):
    """Best-effort parse engine log into monitor_events (rate-limited)."""
    now = time.monotonic()
    last = _INGEST_AT.get(site.site_id)
    # Missing means this process has not ingested the site yet. Do not use 0:
    # monotonic() can be below the interval early in a process lifetime.
    if last is not None and not force and now - last < _INGEST_MIN_INTERVAL:
        return
    _INGEST_AT[site.site_id] = now
    result = get_executor_for_node(site.node_id).tail_logs(site.site_id, lines=limit)
    lines = (result.data or {}).get("logs") or []
    existing = {
        (e.created_at.isoformat() if e.created_at else "", e.message)
        for e in MonitorEvent.query.filter_by(site_id=site.site_id)
        .order_by(MonitorEvent.id.desc())
        .limit(500)
    }
    for line in lines:
        m = LOG_RE.match(line.strip())
        if not m:
            continue
        level = m.group("level")
        msg = m.group("msg")
        ts_raw = m.group("ts")
        try:
            ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        key = (ts.isoformat(), msg)
        if key in existing:
            continue
        action = None
        event_type = "log"
        file_path = None
        if "隔离" in msg:
            event_type = "quarantine"
            action = "quarantine"
        elif "恢复" in msg:
            event_type = "restore"
            action = "restore"
        elif "关键文件" in msg:
            event_type = "critical_modify"
        # Order matters: 「监控目录新增可疑文件」 contains both substrings.
        elif "可疑文件" in msg:
            event_type = "suspicious_create"
        elif "白名单新增文件" in msg:
            event_type = "file_create"
        elif "监控目录新增文件" in msg:
            event_type = "file_create"
        if ":" in msg:
            maybe = msg.rsplit(":", 1)[-1].strip()
            if " -> " in maybe:
                maybe = maybe.split(" -> ", 1)[0]
            if " incident=" in maybe:
                maybe = maybe.split(" incident=", 1)[0]
            maybe = maybe.strip()
            if maybe.startswith("/"):
                file_path = maybe
        incident_match = INCIDENT_RE.search(msg)
        incident_id = incident_match.group(1) if incident_match else None
        db.session.add(
            MonitorEvent(
                site_id=site.site_id,
                event_type=event_type,
                file_path=file_path,
                severity=level,
                message=msg,
                action_taken=action,
                incident_id=incident_id,
                created_at=ts,
            )
        )
        existing.add(key)
    db.session.commit()


@api_bp.get("/events/")
@jwt_required()
def list_events():
    site = _current_site()
    if site:
        try:
            _ingest_from_log(site)
        except Exception:  # noqa: BLE001
            pass

    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))
    severity = request.args.get("severity")
    q = MonitorEvent.query
    if site:
        q = q.filter_by(site_id=site.site_id)
    if severity:
        q = q.filter_by(severity=severity)
    q = q.order_by(MonitorEvent.created_at.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify(
        events=[e.to_dict() for e in pagination.items],
        total=pagination.total,
        page=page,
        per_page=per_page,
    )


@api_bp.get("/events/stats")
@jwt_required()
def event_stats():
    site = _current_site()
    cache_key = site.site_id if site else "_all"
    now_mono = time.monotonic()
    cached = _STATS_CACHE.get(cache_key)
    if cached and now_mono - cached[0] < _STATS_TTL:
        return jsonify(cached[1])

    q = MonitorEvent.query
    if site:
        q = q.filter_by(site_id=site.site_id)
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    payload = {
        "critical_count": q.filter_by(severity="CRITICAL").count(),
        "warning_count": q.filter_by(severity="WARNING").count(),
        "today_count": q.filter(MonitorEvent.created_at >= today).count(),
        "week_count": q.filter(MonitorEvent.created_at >= week).count(),
    }
    _STATS_CACHE[cache_key] = (now_mono, payload)
    return jsonify(payload)


def _later_failure_message(site: Site, event: MonitorEvent) -> str:
    """Prefer a later log line for this incident that says 未能生成."""
    incident_id = event.incident_id or ""
    if not incident_id:
        return "未能生成"
    candidates = []
    try:
        result = get_executor_for_node(site.node_id).tail_logs(site.site_id, lines=500)
        lines = (result.data or {}).get("logs") or []
    except Exception:  # noqa: BLE001
        lines = []
    for line in lines:
        matched = LOG_RE.match(str(line).strip())
        if not matched:
            continue
        msg = matched.group("msg")
        if incident_id not in msg or "未能生成" not in msg:
            continue
        try:
            ts = datetime.strptime(matched.group("ts"), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if event.created_at is not None and ts <= event.created_at:
            continue
        candidates.append((ts, msg))
    rows = (
        MonitorEvent.query.filter_by(site_id=site.site_id, incident_id=incident_id)
        .order_by(MonitorEvent.created_at.asc(), MonitorEvent.id.asc())
        .limit(100)
        .all()
    )
    for row in rows:
        if row.id == event.id or not row.message or "未能生成" not in row.message:
            continue
        if (
            event.created_at is not None
            and row.created_at is not None
            and row.created_at <= event.created_at
        ):
            continue
        candidates.append((row.created_at or datetime.min, row.message))
    if not candidates:
        return "未能生成"
    candidates.sort(key=lambda item: item[0] or datetime.min)
    return candidates[-1][1]


@api_bp.get("/events/<int:event_pk>/timeline")
@jwt_required()
def event_timeline(event_pk):
    site = _current_site()
    if not site:
        return jsonify(error="站点不存在"), 404
    event = MonitorEvent.query.filter_by(id=event_pk, site_id=site.site_id).first()
    if not event:
        return jsonify(error="事件不存在"), 404
    if not event.incident_id:
        return jsonify(status="missing", message="该事件没有入侵分析"), 404
    result = get_executor_for_node(site.node_id).read_incident(site.site_id, event.incident_id)
    if result.ok:
        timeline = (result.data or {}).get("timeline") or {}
        return jsonify(timeline), 200
    created = event.created_at or datetime.now()
    if datetime.now() - created < timedelta(seconds=30):
        return jsonify(status="pending"), 202
    return jsonify(status="missing", message=_later_failure_message(site, event)), 404


@api_bp.post("/events/<int:event_pk>/investigate")
@jwt_required()
def investigate_event(event_pk):
    site = _current_site()
    if not site:
        return jsonify(error="站点不存在"), 404
    event = MonitorEvent.query.filter_by(id=event_pk, site_id=site.site_id).first()
    if not event:
        return jsonify(error="事件不存在"), 404
    if event.event_type != "file_create":
        return jsonify(error="只能追踪新增文件"), 400
    if event.incident_id:
        return jsonify(error="该事件已有追踪"), 409
    path = event.file_path or ""
    if not path.startswith("/"):
        return jsonify(error="事件没有可追踪的文件路径"), 400
    body = request.get_json(silent=True) or {}
    quarantine = bool(body.get("quarantine"))
    result = get_executor_for_node(site.node_id).investigate_path(site.site_id, path, quarantine)
    if not result.ok:
        return jsonify(error=result.message or "追踪失败"), 400
    incident_id = (result.data or {}).get("incident_id") or ""
    if not incident_id:
        return jsonify(error="未获得 incident_id"), 500
    event.incident_id = incident_id
    db.session.commit()
    return jsonify(incident_id=incident_id, quarantine=quarantine)
