# Global Alerts + Editable Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make email / WeCom / DingTalk alerts a single global setting, editable in the Web UI, and fan them out into every site (and controlplane) conf for the existing monitor engine.

**Architecture:** Global `AlertConfig` rows (`site_pk IS NULL`) are the source of truth. `apply_global_alerts()` mirrors values onto each `Site` and re-syncs confs; controlplane env/conf updated the same way. Config UI edits global channels without requiring a selected site.

**Tech Stack:** Flask/SQLAlchemy, Vue3 + Element Plus, existing `sync_site` / `render_controlplane.sh`.

**Spec:** `docs/superpowers/specs/2026-11-19-global-alerts-design.md`

## Global Constraints

- Pure global only — no per-site alert overrides.
- Channels: `email`, `wechat`, `dingtalk` — one global row each.
- Engine still reads per-site conf `ALERT_EMAIL` / `WECHAT_WEBHOOK` / `DINGTALK_WEBHOOK` (unchanged).
- Disabled channel → empty string written to confs.
- `GET /api/config/alert-config` must work without a current site.
- User-facing UI copy in Chinese where appropriate.

## File map

| File | Responsibility |
|------|----------------|
| `backend/app/services/alerts.py` | ensure/seed global rows; `get_global_alert_values()`; `apply_global_alerts()` |
| `backend/app/api/config.py` | Global-only GET/PUT alert-config |
| `backend/app/api/sites.py` | Stop treating create-time alert fields as authoritative (optional: still accept then write global) |
| `backend/tests/test_api.py` | Tests for seed, PUT fan-out mirrors |
| `frontend/src/views/Config.vue` | Editable global alert form + save |
| `frontend/src/views/Sites.vue` | Remove alert email from create dialog; hint |
| `docs/DEPLOY.md`, `scripts/README.md` | Document global alerts |

---

### Task 1: Backend service + API for global alerts

**Files:**
- Create: `backend/app/services/alerts.py`
- Modify: `backend/app/api/config.py`
- Modify: `backend/tests/test_api.py`
- Modify: `backend/app/services/site_sync.py` (optional: `build_runtime_config` can keep reading site mirrors after apply)

**Interfaces:**
- `ensure_global_alert_rows() -> list[AlertConfig]`
- `get_global_alert_values() -> dict` with keys `alert_email`, `wechat_webhook`, `dingtalk_webhook` (empty if disabled)
- `apply_global_alerts() -> dict` with `sites_updated`, `controlplane_updated`
- `GET /api/config/alert-config` → only `site_pk is None` rows
- `PUT /api/config/alert-config/<id>` → update global row then `apply_global_alerts()`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_api.py`:

```python
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
```

- [ ] **Step 2: Run tests — expect fail**

```bash
cd backend && WEBSHELL_DRY_RUN=1 .venv/bin/pytest -q tests/test_api.py::test_global_alert_config_seed_and_update tests/test_api.py::test_global_alert_get_without_sites
```

Expected: FAIL (old per-site seed / missing fan-out)

- [ ] **Step 3: Implement `backend/app/services/alerts.py`**

```python
"""Global alert channels (email / wechat / dingtalk)."""

from __future__ import annotations

import os
from pathlib import Path

from ..models import AlertConfig, Site, db
from .site_sync import sync_site

CHANNEL_TYPES = ("email", "wechat", "dingtalk")


def ensure_global_alert_rows() -> list[AlertConfig]:
    rows = {
        r.type: r
        for r in AlertConfig.query.filter(AlertConfig.site_pk.is_(None)).all()
        if r.type in CHANNEL_TYPES
    }
    if len(rows) == 3:
        return [rows[t] for t in CHANNEL_TYPES]

    # Seed from first site that has values
    email = wechat = dingtalk = ""
    for site in Site.query.order_by(Site.id.asc()).all():
        if not email and site.alert_email:
            email = site.alert_email or ""
        if not wechat and site.wechat_webhook:
            wechat = site.wechat_webhook or ""
        if not dingtalk and site.dingtalk_webhook:
            dingtalk = site.dingtalk_webhook or ""
        if email or wechat or dingtalk:
            break

    seeds_data = {
        "email": ("alert_email", email),
        "wechat": ("webhook_url", wechat),
        "dingtalk": ("webhook_url", dingtalk),
    }
    out = []
    for typ in CHANNEL_TYPES:
        if typ in rows:
            out.append(rows[typ])
            continue
        field, value = seeds_data[typ]
        kwargs = {"site_pk": None, "type": typ, "enabled": bool(value)}
        kwargs[field] = value or None
        row = AlertConfig(**kwargs)
        db.session.add(row)
        out.append(row)
    # Drop legacy per-site alert_config rows from consideration (delete)
    AlertConfig.query.filter(AlertConfig.site_pk.isnot(None)).delete(
        synchronize_session=False
    )
    db.session.commit()
    return out


def get_global_alert_values() -> dict[str, str]:
    ensure_global_alert_rows()
    values = {"alert_email": "", "wechat_webhook": "", "dingtalk_webhook": ""}
    for row in AlertConfig.query.filter(AlertConfig.site_pk.is_(None)).all():
        if row.type == "email":
            values["alert_email"] = (row.alert_email or "") if row.enabled else ""
        elif row.type == "wechat":
            values["wechat_webhook"] = (row.webhook_url or "") if row.enabled else ""
        elif row.type == "dingtalk":
            values["dingtalk_webhook"] = (row.webhook_url or "") if row.enabled else ""
    return values


def _patch_controlplane_env(values: dict[str, str]) -> bool:
    env_path = Path(
        os.environ.get("WEBSHELL_CONTROLPLANE_ENV", "/etc/webshell-monitor/controlplane.env")
    )
    # In dry-run / missing parent, skip
    if not env_path.parent.exists() and not os.environ.get("WEBSHELL_DRY_RUN"):
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
    if os.environ.get("WEBSHELL_DRY_RUN") and not env_path.parent.exists():
        return False
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    mapping = {
        "CONTROLPLANE_ALERT_EMAIL": values["alert_email"],
        "CONTROLPLANE_WECHAT_WEBHOOK": values["wechat_webhook"],
        "CONTROLPLANE_DINGTALK_WEBHOOK": values["dingtalk_webhook"],
    }
    keys = set(mapping)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else ""
        if key in keys:
            out.append(f'{key}="{mapping[key]}"')
            keys.discard(key)
        else:
            out.append(line)
    for key in keys:
        out.append(f'{key}="{mapping[key]}"')
    try:
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def apply_global_alerts() -> dict:
    values = get_global_alert_values()
    sites_ok = 0
    for site in Site.query.order_by(Site.id.asc()).all():
        site.alert_email = values["alert_email"]
        site.wechat_webhook = values["wechat_webhook"]
        site.dingtalk_webhook = values["dingtalk_webhook"]
        db.session.commit()
        ok, _ = sync_site(site, restart=True)
        if ok:
            sites_ok += 1
    cp = _patch_controlplane_env(values)
    # Re-render controlplane if renderer exists (best-effort)
    from ..executor import get_executor_for_node
    import shutil
    import subprocess

    render = Path(
        os.environ.get("WEBSHELL_INSTALL_DIR", "/opt/webshell_monitor")
    ) / "render_controlplane.sh"
    if cp and render.exists() and not os.environ.get("WEBSHELL_DRY_RUN"):
        subprocess.run([str(render)], check=False, capture_output=True, text=True)
        get_executor_for_node("local").restart_site("controlplane")
    return {"sites_updated": sites_ok, "controlplane_env": cp}
```

Rewrite `list_alert_config` / `update_alert_config` in `config.py` to use this module only (global rows; PUT calls `apply_global_alerts()`).

When creating sites in `sites.py`, after create set alert fields from `get_global_alert_values()` instead of request body (ignore body alert fields or override).

- [ ] **Step 4: Run tests — expect pass**

```bash
cd backend && WEBSHELL_DRY_RUN=1 .venv/bin/pytest -q tests/test_api.py
```

Expected: all pass including new ones

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/alerts.py backend/app/api/config.py backend/app/api/sites.py backend/tests/test_api.py
git commit -m "feat: global alert config with fan-out to all sites"
```

---

### Task 2: Web UI editable global alerts

**Files:**
- Modify: `frontend/src/views/Config.vue`
- Modify: `frontend/src/views/Sites.vue`

**Interfaces:**
- Consumes: `GET/PUT /api/config/alert-config` (and `PUT .../<id>`)
- Produces: editable table + 保存; loads alerts on mount even without site

- [ ] **Step 1: Config.vue alert tab**

Replace read-only webhook column with inputs:

```vue
<el-alert
  type="info"
  :closable="false"
  title="全局告警：对所有站点与控制面生效"
  style="margin-bottom: 12px"
/>
<el-table :data="alertConfigs">
  <!-- type tag, enabled switch (local only until save) -->
  <el-table-column label="Webhook / 邮箱">
    <template #default="{ row }">
      <el-input
        v-if="row.type === 'email'"
        v-model="row.alert_email"
        placeholder="alert@example.com"
      />
      <el-input
        v-else
        v-model="row.webhook_url"
        placeholder="https://..."
      />
    </template>
  </el-table-column>
</el-table>
<el-button type="primary" :loading="alertSaving" @click="saveAlerts">保存告警配置</el-button>
```

`saveAlerts`: for each row `PUT /config/alert-config/${id}` with `{ enabled, webhook_url, alert_email }`, then reload.

`loadAlertConfigs`: call API without depending on `currentSiteId` (still call on mount always).

Remove `@change="handleToggleAlert"` auto-save; toggles apply on 保存 only (clearer).

- [ ] **Step 2: Sites.vue**

Remove「告警邮箱」form item. Add short hint:

```vue
<el-alert type="info" :closable="false" title="告警渠道请在「配置 → 告警配置」中全局设置" />
```

Stop sending `alert_email` in create payload (or leave empty).

- [ ] **Step 3: Build**

```bash
cd frontend && pnpm build
```

Expected: exit 0

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/Config.vue frontend/src/views/Sites.vue
git commit -m "feat: editable global alert settings in Config UI"
```

---

### Task 3: Docs + CLI note

**Files:**
- Modify: `docs/DEPLOY.md`
- Modify: `scripts/README.md`
- Modify: `scripts/manage_sites.sh` usage text (optional one-line: alert flags deprecated; use GUI)

- [ ] **Step 1: Document**

DEPLOY: replace per-site alert checklist with「GUI 配置 → 告警配置（全局）」.

README: note global alerts; `add-site --wechat-webhook` still accepted by render for one-off but GUI is source of truth — if easy, in `cmd_add_site` after render print warning that GUI global alerts will overwrite on next save. Prefer documenting only in phase 1 unless flags can call a python one-liner (skip — docs only).

- [ ] **Step 2: Commit**

```bash
git add docs/DEPLOY.md scripts/README.md
git commit -m "docs: global alert configuration in GUI"
```

---

## Spec coverage

| Spec | Task |
|------|------|
| Global AlertConfig site_pk null | 1 |
| Seed from existing site | 1 |
| apply fan-out + controlplane.env | 1 |
| GET without site | 1 |
| Editable UI + save | 2 |
| Sites form cleanup | 2 |
| Docs | 3 |
| No per-site overrides | respected |
