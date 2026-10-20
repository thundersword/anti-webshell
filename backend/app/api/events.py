import re
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


def _ingest_from_log(site: Site, limit: int = 200):
    """Best-effort parse engine log into monitor_events."""
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
        elif "可疑文件" in msg:
            event_type = "suspicious_create"
        if ":" in msg:
            maybe = msg.rsplit(":", 1)[-1].strip()
            if maybe.startswith("/"):
                file_path = maybe
        db.session.add(
            MonitorEvent(
                site_id=site.site_id,
                event_type=event_type,
                file_path=file_path,
                severity=level,
                message=msg,
                action_taken=action,
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
    if site:
        try:
            _ingest_from_log(site)
        except Exception:  # noqa: BLE001
            pass

    q = MonitorEvent.query
    if site:
        q = q.filter_by(site_id=site.site_id)
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    return jsonify(
        critical_count=q.filter_by(severity="CRITICAL").count(),
        warning_count=q.filter_by(severity="WARNING").count(),
        today_count=q.filter(MonitorEvent.created_at >= today).count(),
        week_count=q.filter(MonitorEvent.created_at >= week).count(),
    )
