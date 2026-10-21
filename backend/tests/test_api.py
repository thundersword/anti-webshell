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
