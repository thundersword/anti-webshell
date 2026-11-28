import os
from pathlib import Path

import pytest

os.environ["WEBSHELL_DRY_RUN"] = "1"
os.environ["DEFAULT_ADMIN_USER"] = "admin"
os.environ["DEFAULT_ADMIN_PASSWORD"] = "admin123"


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test.db"
    config_root = tmp_path / "sites"
    config_root.mkdir()
    scripts = Path(__file__).resolve().parents[2] / "scripts"

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["WEBSHELL_CONFIG_ROOT"] = str(config_root)
    os.environ["WEBSHELL_INSTALL_DIR"] = str(scripts)
    os.environ["WEBSHELL_DRY_RUN"] = "1"

    from app import create_app
    from app.config import Config

    class TestConfig(Config):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
        WEBSHELL_DRY_RUN = True
        WEBSHELL_CONFIG_ROOT = str(config_root)
        WEBSHELL_INSTALL_DIR = str(scripts)
        TESTING = True

    app = create_app(TestConfig)
    with app.test_client() as c:
        yield c


def _login(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200
    token = resp.get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_login_and_health(client):
    assert client.get("/api/health").status_code == 200
    headers = _login(client)
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.get_json()["username"] == "admin"


def test_create_site_and_config(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "webroot"
    project.mkdir()
    (project / "public").mkdir()
    (project / "public" / "index.php").write_text("<?php", encoding="utf-8")

    resp = client.post(
        "/api/sites",
        headers=headers,
        json={
            "site_id": "blog",
            "name": "Blog",
            "project_dir": str(project),
            "profile": "thinkphp",
            "start": False,
        },
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["site_id"] == "blog"
    assert body["node_id"] == "local"
    assert body.get("lock_critical_files") is True
    assert len(body["critical_files"]) >= 1

    files = client.get("/api/config/critical-files?site_id=blog", headers=headers)
    assert files.status_code == 200
    assert isinstance(files.get_json(), list)

    profiles = client.get("/api/profiles", headers=headers)
    assert profiles.status_code == 200
    names = [p["name"] for p in profiles.get_json()["profiles"]]
    assert "thinkphp" in names
    assert "thinkphp3" in names
    assert "thinkphp5" in names
    assert "thinkphp6" in names
    assert "laravel" in names


def test_service_status_dry_run(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "site2"
    project.mkdir()
    client.post(
        "/api/sites",
        headers=headers,
        json={
            "site_id": "s2",
            "project_dir": str(project),
            "profile": "plain_php",
            "start": False,
        },
    )
    st = client.get("/api/service/status?site_id=s2", headers=headers)
    assert st.status_code == 200
    assert st.get_json()["status"] in ("stopped", "running", "unknown")


def test_tail_n_lines(tmp_path):
    from app.executor.local import tail_n_lines

    log = tmp_path / "big.log"
    log.write_text("".join(f"line-{i}\n" for i in range(5000)), encoding="utf-8")
    lines = tail_n_lines(log, 20)
    assert len(lines) == 20
    assert lines[-1] == "line-4999"
    assert lines[0] == "line-4980"


def test_events_stats_without_error(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "site3"
    project.mkdir()
    client.post(
        "/api/sites",
        headers=headers,
        json={
            "site_id": "s3",
            "project_dir": str(project),
            "profile": "plain_php",
            "start": False,
        },
    )
    st = client.get("/api/events/stats?site_id=s3", headers=headers)
    assert st.status_code == 200
    body = st.get_json()
    assert "critical_count" in body


def test_detect_thinkphp_versions(client, tmp_path):
    headers = _login(client)

    tp5 = tmp_path / "tp5"
    (tp5 / "application").mkdir(parents=True)
    (tp5 / "thinkphp").mkdir()
    (tp5 / "thinkphp" / "base.php").write_text("<?php", encoding="utf-8")
    r = client.get("/api/profiles/detect", headers=headers, query_string={"project_dir": str(tp5)})
    assert r.status_code == 200
    assert r.get_json()["profile"] == "thinkphp5"

    tp6 = tmp_path / "tp6"
    (tp6 / "app").mkdir(parents=True)
    (tp6 / "config").mkdir()
    (tp6 / "config" / "app.php").write_text("<?php", encoding="utf-8")
    r = client.get("/api/profiles/detect", headers=headers, query_string={"project_dir": str(tp6)})
    assert r.get_json()["profile"] == "thinkphp6"

    tp3 = tmp_path / "tp3"
    (tp3 / "ThinkPHP").mkdir(parents=True)
    (tp3 / "ThinkPHP" / "ThinkPHP.php").write_text("<?php", encoding="utf-8")
    r = client.get("/api/profiles/detect", headers=headers, query_string={"project_dir": str(tp3)})
    assert r.get_json()["profile"] == "thinkphp3"


def test_lock_dry_run(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "locksite"
    project.mkdir()
    (project / "index.php").write_text("<?php", encoding="utf-8")
    client.post(
        "/api/sites",
        headers=headers,
        json={
            "site_id": "lock1",
            "project_dir": str(project),
            "profile": "plain_php",
            "start": False,
            "lock_critical_files": True,
        },
    )
    resp = client.post("/api/service/lock?site_id=lock1", headers=headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["message"] == "locked"


def _create_plain_site(client, headers, project, site_id):
    project.mkdir()
    created = client.post("/api/sites", headers=headers, json={
        "site_id": site_id,
        "name": site_id,
        "project_dir": str(project),
        "profile": "plain_php",
        "start": False,
    })
    assert created.status_code == 201, created.get_data(as_text=True)
    return created


def test_delete_site_reloads_audit_rules(client, tmp_path, monkeypatch):
    headers = _login(client)
    _create_plain_site(client, headers, tmp_path / "web", "blog")
    conf = Path(os.environ["WEBSHELL_CONFIG_ROOT"]) / "blog.conf"
    assert conf.is_file()

    from app.executor.base import ExecResult
    from app.executor.local import LocalSystemdExecutor

    seen = {"n": 0, "conf_exists": None}

    def ok_sync(self):
        seen["n"] += 1
        seen["conf_exists"] = conf.exists()
        return ExecResult(ok=True, message="ok")

    monkeypatch.setattr(LocalSystemdExecutor, "sync_audit", ok_sync)
    resp = client.delete("/api/sites/blog", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["message"] == "deleted"
    assert seen["n"] == 1
    assert seen["conf_exists"] is False

    from app.models import Site

    with client.application.app_context():
        assert Site.query.filter_by(site_id="blog").first() is None


def test_delete_site_survives_sync_audit_failure(client, tmp_path, monkeypatch):
    headers = _login(client)
    _create_plain_site(client, headers, tmp_path / "web", "blog")

    from app.executor.base import ExecResult
    from app.executor.local import LocalSystemdExecutor
    from app.models import Site

    def failed_sync(self):
        return ExecResult(ok=False, message="exit 1")

    monkeypatch.setattr(LocalSystemdExecutor, "sync_audit", failed_sync)
    resp = client.delete("/api/sites/blog", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["message"] == "deleted"
    with client.application.app_context():
        assert Site.query.filter_by(site_id="blog").first() is None

    _create_plain_site(client, headers, tmp_path / "web2", "shop")

    def raising_sync(self):
        raise RuntimeError("audit down")

    monkeypatch.setattr(LocalSystemdExecutor, "sync_audit", raising_sync)
    resp = client.delete("/api/sites/shop", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["message"] == "deleted"
    with client.application.app_context():
        assert Site.query.filter_by(site_id="shop").first() is None


def test_cannot_delete_controlplane(client):
    headers = _login(client)
    from app.models import Site, db

    with client.application.app_context():
        if not Site.query.filter_by(site_id="controlplane").first():
            db.session.add(
                Site(
                    site_id="controlplane",
                    name="自保",
                    project_dir="/tmp/ui",
                    profile="controlplane",
                    node_id="local",
                )
            )
            db.session.commit()
    r = client.delete("/api/sites/controlplane", headers=headers)
    assert r.status_code == 403
    assert "disable-self" in r.get_json().get("error", "")


def test_cannot_create_controlplane_via_api(client):
    headers = _login(client)
    r = client.post(
        "/api/sites",
        headers=headers,
        json={
            "site_id": "controlplane",
            "project_dir": "/tmp/x",
            "profile": "controlplane",
            "start": False,
        },
    )
    assert r.status_code == 403
    assert "ensure-self" in r.get_json().get("error", "")


def test_self_guard_status(client):
    headers = _login(client)
    for path in ("/api/self-guard", "/api/service/self-guard"):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, path
        body = r.get_json()
        assert body["site_id"] == "controlplane"
        assert "unit_active" in body
        assert "lock_status" in body
        assert "project_dir" in body
        assert isinstance(body["watch_dirs"], list)
    # Query/header site context must not change or 404 this global endpoint
    r2 = client.get(
        "/api/self-guard?site_id=blog",
        headers={**headers, "X-Site-Id": "blog"},
    )
    assert r2.status_code == 200
    assert r2.get_json()["site_id"] == "controlplane"


def test_global_alert_config_seed_and_update(client, tmp_path):
    headers = _login(client)
    # create a site with a wechat webhook (legacy per-site field)
    project = tmp_path / "s"
    project.mkdir()
    (project / "index.php").write_text("<?php", encoding="utf-8")
    client.post(
        "/api/sites",
        headers=headers,
        json={
            "site_id": "blog1",
            "project_dir": str(project),
            "profile": "plain_php",
            "start": False,
            "wechat_webhook": "https://qyapi.example/hook-old",
        },
    )
    r = client.get("/api/config/alert-config", headers=headers)
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 3
    assert all(row.get("site_pk") is None for row in rows)
    wechat = next(x for x in rows if x["type"] == "wechat")
    # seeded from site
    assert wechat["webhook_url"] == "https://qyapi.example/hook-old"
    assert wechat["enabled"] is True

    r2 = client.put(
        f"/api/config/alert-config/{wechat['id']}",
        headers=headers,
        json={"enabled": True, "webhook_url": "https://qyapi.example/hook-new"},
    )
    assert r2.status_code == 200
    site = client.get("/api/sites/blog1", headers=headers).get_json()
    assert site["wechat_webhook"] == "https://qyapi.example/hook-new"


def test_global_alert_get_without_sites(client):
    headers = _login(client)
    r = client.get("/api/config/alert-config", headers=headers)
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 3
    assert all(row.get("site_pk") is None for row in rows)


def test_site_access_log_roundtrip(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "web"
    project.mkdir()
    created = client.post("/api/sites", headers=headers, json={
        "site_id": "blog",
        "name": "Blog",
        "project_dir": str(project),
        "profile": "plain_php",
        "start": False,
        "access_log": "/var/log/nginx/blog.log",
        "site_hosts": "blog.example",
    })
    assert created.status_code == 201
    assert created.get_json()["access_log"] == "/var/log/nginx/blog.log"
    assert created.get_json()["site_hosts"] == "blog.example"
    conf = Path(os.environ["WEBSHELL_CONFIG_ROOT"]) / "blog.conf"
    text = conf.read_text(encoding="utf-8")
    assert 'ACCESS_LOG="/var/log/nginx/blog.log"' in text
    assert 'SITE_HOSTS="blog.example"' in text


def test_access_log_rejects_relative_path(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "web"
    project.mkdir()
    created = client.post("/api/sites", headers=headers, json={
        "site_id": "blog",
        "name": "Blog",
        "project_dir": str(project),
        "profile": "plain_php",
        "start": False,
        "access_log": "var/log/nginx/blog.log",
    })
    assert created.status_code == 400
    assert created.get_json()["error"] == "访问日志必须是绝对路径"


def test_timeline_pending_then_ready_then_missing(client, tmp_path, monkeypatch):
    headers = _login(client)
    project = tmp_path / "web"
    project.mkdir()
    client.post("/api/sites", headers=headers, json={
        "site_id": "blog", "name": "Blog", "project_dir": str(project),
        "profile": "plain_php", "start": False,
    })
    log = tmp_path / "blog.log"
    from datetime import datetime, timedelta
    recent = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now() - timedelta(seconds=120)).strftime("%Y-%m-%d %H:%M:%S")
    log.write_text(
        f"[{recent}] [CRITICAL] [site=blog] 文件已隔离: /tmp/a.php -> /tmp/q incident=20261124T034211Z_ab12cd34\n"
        f"[{old}] [CRITICAL] [site=blog] 文件已隔离: /tmp/b.php -> /tmp/q2 incident=20261124T000000Z_bbbbbbbb\n",
        encoding="utf-8",
    )
    conf = Path(os.environ["WEBSHELL_CONFIG_ROOT"]) / "blog.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + f'LOG_FILE="{log}"\n', encoding="utf-8")
    listed = client.get("/api/events/?site_id=blog", headers=headers)
    events = listed.get_json()["events"]
    recent_row = next(
        e for e in events
        if e.get("incident_id") == "20261124T034211Z_ab12cd34"
        or "ab12cd34" in (e.get("message") or "")
    )
    old_row = next(
        e for e in events
        if e.get("incident_id") == "20261124T000000Z_bbbbbbbb"
        or "bbbbbbbb" in (e.get("message") or "")
    )
    assert recent_row["incident_id"] == "20261124T034211Z_ab12cd34"
    assert recent_row["file_path"] == "/tmp/a.php"
    assert old_row["file_path"] == "/tmp/b.php"
    pending = client.get(f"/api/events/{recent_row['id']}/timeline?site_id=blog", headers=headers)
    assert pending.status_code == 202
    assert pending.get_json()["status"] == "pending"
    incidents = tmp_path / "incidents" / "blog"
    incidents.mkdir(parents=True)
    (incidents / "20261124T034211Z_ab12cd34.json").write_text(
        '{"incident_id":"20261124T034211Z_ab12cd34","status":"ok","entry":"web_upload","events":[]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("WEBSHELL_INCIDENTS_DIR", str(tmp_path / "incidents"))
    ready = client.get(f"/api/events/{recent_row['id']}/timeline?site_id=blog", headers=headers)
    assert ready.status_code == 200
    assert ready.get_json()["entry"] == "web_upload"
    missing = client.get(f"/api/events/{old_row['id']}/timeline?site_id=blog", headers=headers)
    assert missing.status_code == 404
    assert missing.get_json()["status"] == "missing"


def test_audit_status_dry_run_commands(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "web"
    project.mkdir()
    client.post("/api/sites", headers=headers, json={
        "site_id": "blog",
        "name": "Blog",
        "project_dir": str(project),
        "profile": "plain_php",
        "start": False,
    })
    resp = client.get("/api/service/audit-status?site_id=blog", headers=headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["sync_command"] == "sudo /opt/webshell_monitor/manage_sites.sh sync-audit"
    assert body["check_command"] == "sudo auditctl -l | grep webshell"
    assert body["auditd"] == "unknown"
    assert body["rules_loaded"] is False


def test_write_config_refreshes_controlplane_backup(tmp_path, monkeypatch):
    project = tmp_path / "web"
    project.mkdir()
    config_root = tmp_path / "sites"
    config_root.mkdir()
    backup = tmp_path / "backup"
    watch = tmp_path / "watch"
    watch.write_text(str(config_root) + "\n", encoding="utf-8")
    (config_root / "controlplane.conf").write_text(
        "\n".join([
            'SITE_ID="controlplane"',
            f'PROJECT_DIR="{project}"',
            f'BACKUP_DIR="{backup}"',
            f'WATCH_DIRS_FILE="{watch}"',
            'LOCK_CRITICAL_FILES="true"',
            "",
        ]),
        encoding="utf-8",
    )
    stamps = tmp_path / "stamps"
    monkeypatch.setenv("WEBSHELL_CONFIG_WRITE_DIR", str(stamps))
    from app.executor.base import SiteRuntimeConfig
    from app.executor.local import LocalSystemdExecutor

    scripts = Path(__file__).resolve().parents[2] / "scripts"
    executor = LocalSystemdExecutor(
        install_dir=str(scripts),
        config_root=str(config_root),
        dry_run=False,
    )
    result = executor.write_config(SiteRuntimeConfig(
        site_id="blog",
        project_dir=str(project),
        profile="plain_php",
        access_log="/var/log/nginx/blog.log",
        site_hosts="blog.example",
        critical_files=[],
        whitelist_dirs=[],
    ))
    assert result.ok, result.stderr or result.message
    conf = (config_root / "blog.conf").read_text(encoding="utf-8")
    assert 'ACCESS_LOG="/var/log/nginx/blog.log"' in conf
    assert 'SITE_HOSTS="blog.example"' in conf
    backed = (backup / "blog.conf.backup").read_text(encoding="utf-8")
    assert 'ACCESS_LOG="/var/log/nginx/blog.log"' in backed
    assert any(stamps.iterdir())


def test_manual_investigate_from_create_event(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "web"
    project.mkdir()
    client.post("/api/sites", headers=headers, json={
        "site_id": "track", "name": "Track", "project_dir": str(project),
        "profile": "plain_php", "start": False,
    })
    log = tmp_path / "track.log"
    from datetime import datetime, timedelta
    created_at = (datetime.now() - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")
    quarantined_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.write_text(
        f"[{created_at}] [WARNING] [site=track] 监控目录新增文件: /www/wwwroot/blog/public/uploads/a.jpg\n"
        f"[{quarantined_at}] [CRITICAL] [site=track] 文件已隔离: /tmp/a.php -> /tmp/q incident=20261124T034211Z_ab12cd34\n",
        encoding="utf-8",
    )
    conf = Path(os.environ["WEBSHELL_CONFIG_ROOT"]) / "track.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + f'LOG_FILE="{log}"\n', encoding="utf-8")
    listed = client.get("/api/events/?site_id=track", headers=headers)
    events = listed.get_json()["events"]
    created = next(
        (e for e in events if e["event_type"] == "file_create"),
        None,
    )
    assert created is not None, [(e.get("event_type"), e.get("message")) for e in events]
    quarantined = next(e for e in events if e["event_type"] == "quarantine")
    assert created["file_path"] == "/www/wwwroot/blog/public/uploads/a.jpg"
    assert not created["incident_id"]

    started = client.post(
        f"/api/events/{created['id']}/investigate?site_id=track",
        headers=headers,
        json={"quarantine": False},
    )
    assert started.status_code == 200
    body = started.get_json()
    assert body["incident_id"] == "dryrun0001"
    assert body["quarantine"] is False
    again = client.post(
        f"/api/events/{created['id']}/investigate?site_id=track",
        headers=headers,
        json={"quarantine": True},
    )
    assert again.status_code == 409
    refused = client.post(
        f"/api/events/{quarantined['id']}/investigate?site_id=track",
        headers=headers,
        json={"quarantine": False},
    )
    assert refused.status_code == 400


def test_track_whitelist_roundtrip(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "web"
    project.mkdir()
    created = client.post("/api/sites", headers=headers, json={
        "site_id": "blog",
        "name": "Blog",
        "project_dir": str(project),
        "profile": "plain_php",
        "start": False,
    })
    assert created.status_code == 201
    assert created.get_json()["track_whitelist"] is False
    conf = Path(os.environ["WEBSHELL_CONFIG_ROOT"]) / "blog.conf"
    assert 'TRACK_WHITELIST="false"' in conf.read_text(encoding="utf-8")

    updated = client.put("/api/sites/blog", headers=headers, json={
        "track_whitelist": True,
    })
    assert updated.status_code == 200
    assert updated.get_json()["track_whitelist"] is True
    assert 'TRACK_WHITELIST="true"' in conf.read_text(encoding="utf-8")

    renamed = client.put("/api/sites/blog", headers=headers, json={
        "name": "Blog 2",
    })
    assert renamed.status_code == 200
    assert renamed.get_json()["track_whitelist"] is True
    assert 'TRACK_WHITELIST="true"' in conf.read_text(encoding="utf-8")


def test_whitelist_track_log_is_file_create_with_incident(client, tmp_path):
    headers = _login(client)
    project = tmp_path / "web"
    project.mkdir()
    client.post("/api/sites", headers=headers, json={
        "site_id": "wlist",
        "name": "Wlist",
        "project_dir": str(project),
        "profile": "plain_php",
        "start": False,
    })
    log = tmp_path / "wlist.log"
    from datetime import datetime
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.write_text(
        f"[{created_at}] [WARNING] [site=wlist] "
        "白名单新增文件: /www/wwwroot/blog/public/uploads/a.jpg "
        "incident=20261128T031800Z_aabbccdd\n",
        encoding="utf-8",
    )
    conf = Path(os.environ["WEBSHELL_CONFIG_ROOT"]) / "wlist.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + f'LOG_FILE="{log}"\n', encoding="utf-8")
    listed = client.get("/api/events/?site_id=wlist", headers=headers)
    events = listed.get_json()["events"]
    created = next((e for e in events if "白名单新增文件" in (e.get("message") or "")), None)
    assert created is not None, [(e.get("event_type"), e.get("message")) for e in events]
    assert created["event_type"] == "file_create"
    assert created["file_path"] == "/www/wwwroot/blog/public/uploads/a.jpg"
    assert created["incident_id"] == "20261128T031800Z_aabbccdd"
