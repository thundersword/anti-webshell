from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="operator", nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    def set_password(self, password: str) -> None:
        # pbkdf2 for broader OpenSSL / older macOS Python compatibility
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
        }


class Node(db.Model):
    """Execution node. Default local; reserved for multi-host agents."""

    __tablename__ = "nodes"

    id = db.Column(db.String(64), primary_key=True)  # e.g. local
    name = db.Column(db.String(100), nullable=False)
    kind = db.Column(db.String(20), default="local")  # local | agent
    endpoint = db.Column(db.String(500))
    token_hint = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "endpoint": self.endpoint,
        }


class Site(db.Model):
    __tablename__ = "sites"

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    project_dir = db.Column(db.String(500), nullable=False)
    profile = db.Column(db.String(50), default="thinkphp", nullable=False)
    node_id = db.Column(db.String(64), db.ForeignKey("nodes.id"), default="local")
    auto_restore = db.Column(db.Boolean, default=True)
    lock_critical_files = db.Column(db.Boolean, default=True)
    alert_email = db.Column(db.String(200))
    wechat_webhook = db.Column(db.String(500))
    dingtalk_webhook = db.Column(db.String(500))
    enabled = db.Column(db.Boolean, default=True)
    sync_status = db.Column(db.String(20), default="pending")  # pending|synced|error
    sync_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    critical_files = db.relationship(
        "CriticalFile", backref="site", cascade="all, delete-orphan", lazy="dynamic"
    )
    whitelist_dirs = db.relationship(
        "WhitelistDir", backref="site", cascade="all, delete-orphan", lazy="dynamic"
    )

    def to_dict(self, include_lists=False):
        data = {
            "id": self.id,
            "site_id": self.site_id,
            "name": self.name,
            "project_dir": self.project_dir,
            "profile": self.profile,
            "node_id": self.node_id,
            "auto_restore": self.auto_restore,
            "lock_critical_files": bool(self.lock_critical_files),
            "alert_email": self.alert_email,
            "wechat_webhook": self.wechat_webhook,
            "dingtalk_webhook": self.dingtalk_webhook,
            "enabled": self.enabled,
            "sync_status": self.sync_status,
            "sync_message": self.sync_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_lists:
            data["critical_files"] = [c.to_dict() for c in self.critical_files]
            data["whitelist_dirs"] = [w.to_dict() for w in self.whitelist_dirs]
        return data


class CriticalFile(db.Model):
    __tablename__ = "critical_files"

    id = db.Column(db.Integer, primary_key=True)
    site_pk = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "site_id": self.site.site_id if self.site else None,
            "file_path": self.file_path,
            "enabled": self.enabled,
        }


class WhitelistDir(db.Model):
    __tablename__ = "whitelist_dirs"

    id = db.Column(db.Integer, primary_key=True)
    site_pk = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=False)
    dir_path = db.Column(db.String(500), nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "site_id": self.site.site_id if self.site else None,
            "dir_path": self.dir_path,
            "enabled": self.enabled,
        }


class AlertConfig(db.Model):
    __tablename__ = "alert_config"

    id = db.Column(db.Integer, primary_key=True)
    site_pk = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=True)
    type = db.Column(db.String(20), nullable=False)  # email|wechat|dingtalk
    enabled = db.Column(db.Boolean, default=False)
    webhook_url = db.Column(db.String(500))
    alert_email = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "site_pk": self.site_pk,
            "type": self.type,
            "enabled": self.enabled,
            "webhook_url": self.webhook_url,
            "alert_email": self.alert_email,
        }


class MonitorEvent(db.Model):
    __tablename__ = "monitor_events"

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.String(64), index=True)
    event_type = db.Column(db.String(50))
    file_path = db.Column(db.String(500))
    severity = db.Column(db.String(20), index=True)
    message = db.Column(db.Text)
    action_taken = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "site_id": self.site_id,
            "event_type": self.event_type,
            "file_path": self.file_path,
            "severity": self.severity,
            "message": self.message,
            "action_taken": self.action_taken,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50))
    action = db.Column(db.String(100))
    detail = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
