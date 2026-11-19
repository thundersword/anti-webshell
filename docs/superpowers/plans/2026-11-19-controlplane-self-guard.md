# Control-plane Self-Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Protect the monitor control plane (UI, engine, configs, backend) via a builtin `controlplane` site with multi-root inotify watch, immutable locks, external alerts, and static-only Nginx docs.

**Architecture:** Extend `webshell_monitor.sh` to watch multiple roots from `WATCH_DIRS_FILE`. Add `controlplane` profile + `manage_sites.sh ensure-self|lock-self|unlock-self|disable-self`. Harden API so `controlplane` cannot be deleted from GUI; show read-only self-guard status. Document Nginx PHP denial for `WEBSHELL_UI_ROOT`.

**Tech Stack:** Bash + inotifywait, systemd template units, Flask/JWT API, Vue3 GUI, Nginx (docs only).

**Spec:** `docs/superpowers/specs/2026-11-19-controlplane-self-guard-design.md`

## Global Constraints

- Builtin `site_id` is exactly `controlplane` (not deletable via API/GUI; disable via CLI only).
- Default UI root: `/www/wwwroot/anti-webshell` (`WEBSHELL_UI_ROOT`).
- Default engine: `/opt/webshell_monitor`; configs parent: `/etc/webshell-monitor`; backend: `/opt/anti-webshell/backend` (`WEBSHELL_BACKEND_DIR`).
- Business sites keep single `PROJECT_DIR` when `WATCH_DIRS_FILE` is unset.
- `.env` under backend: permission harden only (`600` root); never auto-restore content via monitor.
- Exclude from backend watch: `.venv/`, `__pycache__/`, `*.db`, `*.sqlite*`.
- Phase 1: no auditd attribution, no separate selfguard daemon.
- `docs/tmp/` stays gitignored (already done).
- Respond / commit messages in project style; user-facing copy may be Chinese.

## File map

| File | Responsibility |
|------|----------------|
| `scripts/webshell_monitor.sh` | Load `WATCH_DIRS_FILE`; multi-root inotify; optional `STRICT_QUARANTINE` |
| `scripts/profiles/controlplane.conf` | Extensions, excludes, flags for self-guard |
| `scripts/render_controlplane.sh` | Render conf + `.watch` + `.critical` for controlplane |
| `scripts/manage_sites.sh` | `ensure-self`, `lock-self`, `unlock-self`, `disable-self`; call ensure after `install` |
| `backend/app/api/sites.py` | Reject delete/create clash for `controlplane` |
| `backend/app/api/service.py` (or new) | Optional `GET /api/service/self-guard` status |
| `frontend/src/views/Sites.vue` (or Home) | Read-only 自保 status; hide delete for controlplane |
| `docs/DEPLOY.md`, `scripts/README.md` | Paths, Nginx static-only, publish unlock/lock flow |
| `backend/tests/test_api.py` | API protection tests |
| `scripts/tests/test_watch_dirs.sh` | Shell smoke test for multi-root parsing (no root required) |

---

### Task 1: Multi-root watch in the engine

**Files:**
- Modify: `scripts/webshell_monitor.sh`
- Create: `scripts/tests/test_watch_dirs.sh`
- Test: `scripts/tests/test_watch_dirs.sh`

**Interfaces:**
- Consumes: site conf may set `WATCH_DIRS_FILE="/etc/webshell-monitor/sites/<id>.watch"`
- Produces: `load_watch_dirs` → global bash array `WATCH_DIRS`; `main` starts one `inotifywait` per existing dir, piping into the same `while read` handler; if `WATCH_DIRS_FILE` empty/missing, `WATCH_DIRS=("$PROJECT_DIR")`

- [ ] **Step 1: Write the failing shell smoke test**

Create `scripts/tests/test_watch_dirs.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/a" "$TMP/b"
printf '%s\n' "$TMP/a" "$TMP/b" "$TMP/missing" > "$TMP/watch.list"

# Extract/load helper by sourcing a tiny stub if needed; preferred: invoke
# a --print-watch-dirs debug flag once implemented.
# Until flag exists, this test should fail.
OUT="$("$ROOT/scripts/webshell_monitor.sh" --print-watch-dirs --watch-file "$TMP/watch.list" --project-dir "$TMP/a" 2>&1)" || true
echo "$OUT" | grep -q "$TMP/a"
echo "$OUT" | grep -q "$TMP/b"
echo "$OUT" | grep -vq "$TMP/missing" || echo "$OUT" | grep -q "skip.*missing"
echo "OK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/tests/test_watch_dirs.sh`  
Expected: FAIL (unknown flag / no `--print-watch-dirs`)

- [ ] **Step 3: Implement watch-dir loading + debug flag + multi-root loop**

In `scripts/webshell_monitor.sh`:

1. Add defaults near top:

```bash
WATCH_DIRS_FILE=""
WATCH_DIRS=()
STRICT_QUARANTINE="false"
```

2. After `load_site_config`, call:

```bash
load_watch_dirs() {
    WATCH_DIRS=()
    if [ -n "${WATCH_DIRS_FILE:-}" ] && [ -f "$WATCH_DIRS_FILE" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in ''|\#*) continue ;; esac
            if [ -d "$line" ]; then
                WATCH_DIRS+=("$line")
            else
                log_message "WARNING" "watch dir missing, skip: $line"
            fi
        done < "$WATCH_DIRS_FILE"
    fi
    if [ "${#WATCH_DIRS[@]}" -eq 0 ]; then
        WATCH_DIRS=("$PROJECT_DIR")
    fi
}
```

Call `load_watch_dirs` from `init` after config load (or end of `load_site_config`).

3. Extend CLI for tests / ops:

```bash
# in argument parse:
--print-watch-dirs) PRINT_WATCH_DIRS=1; shift ;;
--watch-file) WATCH_DIRS_FILE="$2"; shift 2 ;;
--project-dir) PROJECT_DIR="$2"; shift 2 ;;
```

When `PRINT_WATCH_DIRS=1`, after resolving dirs, print each path one per line and exit 0 (allow running without full site conf if `--watch-file` + `--project-dir` given).

4. Replace single `inotifywait` in `main` with:

```bash
combined_exclude="(${INOTIFY_EXCLUDE}|\\.git|\\.svn|\\.hg|node_modules|\\.venv|__pycache__)"
fifo="$QUARANTINE_DIR/.inotify.fifo"
rm -f "$fifo"
mkfifo "$fifo"
pids=()
for root in "${WATCH_DIRS[@]}"; do
    inotifywait -m -r \
        --exclude "$combined_exclude" \
        -e create,close_write,moved_to \
        --format '%e %w%f' \
        "$root" >>"$fifo" 2>/dev/null &
    pids+=($!)
done
while read -r event file; do
    case "$event" in
        *ISDIR*) ;;
        CREATE|MOVED_TO|CREATE,*) handle_create "$file" ;;
        CLOSE_WRITE*|MODIFY*) handle_modify "$file" ;;
    esac
done <"$fifo"
```

On cleanup/trap: kill `"${pids[@]}"` and remove fifo.

5. In `handle_create`, after dangerous-extension match and not whitelisted:

```bash
if [ "${STRICT_QUARANTINE:-false}" = "true" ] || check_file_content "$file"; then
    quarantine_file "$file" "..."
else
    # existing alert-only path
fi
```

- [ ] **Step 4: Re-run smoke test**

Run: `bash scripts/tests/test_watch_dirs.sh`  
Expected: prints `OK`

- [ ] **Step 5: Commit**

```bash
git add scripts/webshell_monitor.sh scripts/tests/test_watch_dirs.sh
git commit -m "feat: watch multiple roots via WATCH_DIRS_FILE"
```

---

### Task 2: controlplane profile + render script

**Files:**
- Create: `scripts/profiles/controlplane.conf`
- Create: `scripts/render_controlplane.sh`
- Modify: `scripts/render_site_config.sh` — allow emitting `WATCH_DIRS_FILE=` when `--watch-file` or env set (optional; prefer dedicated render_controlplane.sh writing conf itself)
- Test: dry-run render into a temp output dir

**Interfaces:**
- Consumes: env `WEBSHELL_UI_ROOT`, `WEBSHELL_INSTALL_DIR`, `WEBSHELL_CONFIG_ROOT` (parent of `sites`), `WEBSHELL_BACKEND_DIR`; optional `/etc/webshell-monitor/controlplane.env`
- Produces:  
  - `/etc/webshell-monitor/sites/controlplane.conf`  
  - `controlplane.watch`  
  - `controlplane.critical`  
  - empty `controlplane.whitelist`  
  - `WATCH_DIRS_FILE`, `STRICT_QUARANTINE=true`, `LOCK_CRITICAL_FILES=true`, `AUTO_RESTORE=true`, `PROJECT_DIR=$WEBSHELL_UI_ROOT`

- [ ] **Step 1: Write profile**

`scripts/profiles/controlplane.conf`:

```bash
DISPLAY_NAME="控制面自保"
PROFILE_NAME="controlplane"
PROFILE_VERSION="1"

# Relative lists unused; render_controlplane builds absolute paths.
CRITICAL_REL_PATHS=()
WHITELIST_REL_DIRS=()

INOTIFY_EXCLUDE='(\.git|\.svn|\.hg|node_modules|/\.venv/|__pycache__|\.db$|\.sqlite)'
DANGEROUS_EXTENSIONS="php|php3|php4|php5|phtml|pht|phps|phar|sh|py|pl|cgi|asp|aspx|jsp|html|htm|shtml|js|css"
STRICT_QUARANTINE="true"
```

- [ ] **Step 2: Implement `render_controlplane.sh`**

Responsibilities:

1. `source /etc/webshell-monitor/controlplane.env` if present (ignore missing).
2. Defaults:

```bash
WEBSHELL_UI_ROOT="${WEBSHELL_UI_ROOT:-/www/wwwroot/anti-webshell}"
WEBSHELL_INSTALL_DIR="${WEBSHELL_INSTALL_DIR:-/opt/webshell_monitor}"
WEBSHELL_CONFIG_PARENT="${WEBSHELL_CONFIG_PARENT:-/etc/webshell-monitor}"
WEBSHELL_BACKEND_DIR="${WEBSHELL_BACKEND_DIR:-/opt/anti-webshell/backend}"
OUTPUT_DIR="${WEBSHELL_CONFIG_PARENT}/sites"
SITE_ID="controlplane"
```

3. Write `.watch` — one line per existing dir among the four roots.
4. Build `.critical`:
   - All regular files under UI root (if exists), max depth reasonable; skip nothing except optional `.map` if desired.
   - All `*.sh` under install dir + `profiles/*.conf` + `rules/*`.
   - `sites/*.conf`, `sites/*.critical`, `sites/*.watch`, `controlplane.env` under config parent (not the controlplane files being rewritten mid-write — write to temp then mv).
   - All `*.py` under backend excluding `.venv` and `__pycache__`.
5. **Do not** add `.env` to critical list; if `$WEBSHELL_BACKEND_DIR/.env` exists: `chown root:root` + `chmod 600` when running as root.
6. Write `.conf` with `WATCH_DIRS_FILE`, `STRICT_QUARANTINE=true`, alert vars from env (`CONTROLPLANE_ALERT_EMAIL`, etc.), and `source` profile DANGEROUS/INOTIFY.
7. Support `--output-dir DIR` for tests.

- [ ] **Step 3: Dry-run test**

```bash
TMP=$(mktemp -d)
mkdir -p "$TMP/ui" "$TMP/engine" "$TMP/cfg" "$TMP/backend/app"
echo 'ok' > "$TMP/ui/index.html"
echo 'x' > "$TMP/engine/webshell_monitor.sh"
echo 'print(1)' > "$TMP/backend/app/x.py"
WEBSHELL_UI_ROOT="$TMP/ui" WEBSHELL_INSTALL_DIR="$TMP/engine" \
  WEBSHELL_CONFIG_PARENT="$TMP/cfg" WEBSHELL_BACKEND_DIR="$TMP/backend" \
  bash scripts/render_controlplane.sh --output-dir "$TMP/cfg/sites"
grep -q WATCH_DIRS_FILE "$TMP/cfg/sites/controlplane.conf"
grep -q "$TMP/ui" "$TMP/cfg/sites/controlplane.watch"
grep -q index.html "$TMP/cfg/sites/controlplane.critical"
grep -q STRICT_QUARANTINE "$TMP/cfg/sites/controlplane.conf"
```

Expected: all greps succeed.

- [ ] **Step 4: Ensure site conf includes STRICT_QUARANTINE for engine**

In `webshell_monitor.sh` `load_site_config`, after `source "$conf"`, STRICT_QUARANTINE is already in environment if set in conf. Confirm `handle_create` uses it (Task 1).

- [ ] **Step 5: Commit**

```bash
git add scripts/profiles/controlplane.conf scripts/render_controlplane.sh
git commit -m "feat: add controlplane profile and renderer"
```

---

### Task 3: manage_sites self-guard commands

**Files:**
- Modify: `scripts/manage_sites.sh`
- Copy `render_controlplane.sh` in `cmd_install` like other scripts

**Interfaces:**
- Produces CLI:
  - `ensure-self` — render, enable+start `webshell-monitor@controlplane`, optional `--no-lock`
  - `lock-self` — run monitor `--site controlplane --lock` **or** call same chattr path as engine / `webshell_monitor.sh --lock`
  - `unlock-self` — `--unlock`
  - `disable-self` — interactive `yes` confirm → stop/disable unit; leave configs unless `--purge`
- `cmd_install` ends with `cmd_ensure_self --no-lock` reminder OR calls ensure-self when UI root exists
- `cmd_remove_site` refuses `controlplane` (print error, exit 1)

- [ ] **Step 1: Refuse remove-site for controlplane**

```bash
if [ "$site_id" = "controlplane" ]; then
    print_error "controlplane 只能用: $0 disable-self"
    exit 1
fi
```

Also remove `.watch` in remove-site for normal sites: `rm -f ... "${site_id}.watch"`

- [ ] **Step 2: Implement ensure/lock/unlock/disable**

Sketch:

```bash
cmd_ensure_self() {
    require_root
    local no_lock=0
    [[ "${1:-}" == "--no-lock" ]] && no_lock=1
    cp "$SCRIPT_HOME/render_controlplane.sh" "$INSTALL_DIR/" 2>/dev/null || true
    chmod +x "$INSTALL_DIR/render_controlplane.sh"
    # prefer installed copy
    "$INSTALL_DIR/render_controlplane.sh"
    systemctl enable webshell-monitor@controlplane.service
    systemctl restart webshell-monitor@controlplane.service
    if [ "$no_lock" -eq 0 ]; then
        cmd_lock_self
    fi
    print_warning "请确认 Nginx 对 UI 根目录未启用 PHP（见 docs/DEPLOY.md）"
}

cmd_lock_self() {
    require_root
    "$INSTALL_DIR/webshell_monitor.sh" --site controlplane --lock
}

cmd_unlock_self() {
    require_root
    "$INSTALL_DIR/webshell_monitor.sh" --site controlplane --unlock
}

cmd_disable_self() {
    require_root
    read -r -p "确认禁用控制面自保 controlplane? 输入 YES: " ans
    [[ "$ans" == "YES" ]] || exit 1
    cmd_unlock_self || true
    systemctl stop webshell-monitor@controlplane.service || true
    systemctl disable webshell-monitor@controlplane.service || true
}
```

Wire into `usage` + `main` case.

- [ ] **Step 3: install copies render_controlplane.sh**

In `cmd_install`, add:

```bash
cp "$SCRIPT_HOME/render_controlplane.sh" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR"/*.sh
```

After install success message, print:

```text
建议: $0 ensure-self
发布流程: unlock-self → 更新 → lock-self
```

- [ ] **Step 4: Manual syntax check**

Run: `bash -n scripts/manage_sites.sh && bash -n scripts/render_controlplane.sh`  
Expected: no output, exit 0

- [ ] **Step 5: Commit**

```bash
git add scripts/manage_sites.sh
git commit -m "feat: add ensure-self/lock-self/unlock-self/disable-self"
```

---

### Task 4: API — protect controlplane + self-guard status

**Files:**
- Modify: `backend/app/api/sites.py`
- Modify: `backend/app/api/service.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- `DELETE /api/sites/controlplane` → `403` `{"error":"controlplane 只能通过 CLI disable-self 禁用"}`
- `POST /api/sites` with `site_id=controlplane` → `403` (creation only via CLI ensure-self; optional allow sync-only later — phase 1 forbid create via API)
- `GET /api/service/self-guard` → `{ site_id, unit_active, lock_status, project_dir, watch_dirs: [] }` from executor reading conf/watch file + systemctl (dry-run safe)

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_api.py`:

```python
def test_cannot_delete_controlplane(client):
    headers = _login(client)
    # Insert a fake controlplane row if create forbidden:
    from app.models import Site, db
    from app import create_app
    # Use client app context:
    with client.application.app_context():
        if not Site.query.filter_by(site_id="controlplane").first():
            db.session.add(Site(
                site_id="controlplane",
                name="自保",
                project_dir="/tmp/ui",
                profile="controlplane",
                node_id="local",
            ))
            db.session.commit()
    r = client.delete("/api/sites/controlplane", headers=headers)
    assert r.status_code == 403

def test_cannot_create_controlplane_via_api(client):
    headers = _login(client)
    r = client.post("/api/sites", headers=headers, json={
        "site_id": "controlplane",
        "project_dir": "/tmp/x",
        "profile": "controlplane",
        "start": False,
    })
    assert r.status_code == 403

def test_self_guard_status(client):
    headers = _login(client)
    r = client.get("/api/service/self-guard", headers=headers)
    assert r.status_code == 200
    body = r.get_json()
    assert "site_id" in body
```

- [ ] **Step 2: Run tests — expect fail**

Run: `cd backend && WEBSHELL_DRY_RUN=1 .venv/bin/pytest -q tests/test_api.py::test_cannot_delete_controlplane tests/test_api.py::test_cannot_create_controlplane_via_api tests/test_api.py::test_self_guard_status`  
Expected: FAIL (404/201/ missing route)

- [ ] **Step 3: Implement API guards + status**

In `delete_site` / `create_site`:

```python
CONTROLPLANE_ID = "controlplane"

if site_id == CONTROLPLANE_ID:
    return jsonify(error="controlplane 只能通过 CLI disable-self 禁用"), 403
# create:
if site_id == CONTROLPLANE_ID:
    return jsonify(error="请使用 manage_sites.sh ensure-self 启用自保"), 403
```

Add `GET /service/self-guard` that:
- Reads conf from executor config_root if present
- Calls `systemctl is-active` via executor helper or returns `unknown` in dry-run
- Returns lock_status via existing `lock_status("controlplane")`

- [ ] **Step 4: Run tests — expect pass**

Run: same pytest command  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/sites.py backend/app/api/service.py backend/tests/test_api.py
git commit -m "feat: protect controlplane in API and expose self-guard status"
```

---

### Task 5: GUI read-only 自保 status

**Files:**
- Modify: `frontend/src/views/Sites.vue` (and/or `Home.vue`)
- Modify: `frontend/src/views/Home.vue` if a compact card fits better

**Interfaces:**
- Consumes: `GET /api/service/self-guard`
- Produces: panel showing unit/lock summary; if a `controlplane` row appears in site list, hide Delete button

- [ ] **Step 1: Hide delete for controlplane in Sites.vue**

Where delete is offered:

```js
const canDelete = (row) => row.site_id !== 'controlplane'
```

- [ ] **Step 2: Add 自保 card**

On Home or Sites: fetch `/service/self-guard`, show:

- 状态: active / inactive / unknown  
- 监控目录摘要  
- 锁定摘要（截断文本）  
- 文案: 「关闭自保请在服务器执行 manage_sites.sh disable-self」

No enable/disable buttons in phase 1.

- [ ] **Step 3: Build check**

Run: `cd frontend && pnpm build`  
Expected: exit 0

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/Sites.vue frontend/src/views/Home.vue
git commit -m "feat: show read-only controlplane self-guard status in GUI"
```

---

### Task 6: Documentation — DEPLOY + scripts README + Nginx static-only

**Files:**
- Modify: `docs/DEPLOY.md`
- Modify: `scripts/README.md`
- Optional create: `docs/nginx-webshell-ui.example.conf`

**Interfaces:** none (docs only)

- [ ] **Step 1: Update DEPLOY.md**

Replace UI path examples with `/www/wwwroot/anti-webshell` / `WEBSHELL_UI_ROOT`.

Nginx section must include:

```nginx
location ~* \.(php|phtml|phar|php\d+|cgi|pl|py|jsp|asp|aspx)$ {
    return 403;
}
```

And explicit: do not include panel PHP snippets; dedicated server block; `root` ownership `root:root` not writable by www.

Add publish flow:

```bash
sudo /opt/webshell_monitor/manage_sites.sh unlock-self
# git pull, pnpm build, rsync to WEBSHELL_UI_ROOT, backend restart
sudo /opt/webshell_monitor/manage_sites.sh lock-self
sudo systemctl restart webshell-api webshell-monitor@controlplane
```

Add first-time: `sudo ./manage_sites.sh ensure-self` after install.

- [ ] **Step 2: Update scripts/README.md**

Document `ensure-self`, `lock-self`, `unlock-self`, `disable-self`, and `.watch` file.

- [ ] **Step 3: Commit**

```bash
git add docs/DEPLOY.md scripts/README.md docs/nginx-webshell-ui.example.conf
git commit -m "docs: controlplane self-guard deploy and static-only Nginx"
```

---

### Task 7: End-to-end checklist (manual on server)

**Files:** none (verification only)

- [ ] **Step 1: Deploy artifacts**

```bash
cd /opt/anti-webshell && git pull
cd scripts && sudo ./manage_sites.sh install
sudo ./manage_sites.sh ensure-self
sudo systemctl is-active webshell-monitor@controlplane
```

Expected: `active`

- [ ] **Step 2: Drop webshell-like file into UI root**

```bash
echo '<?php echo 1;' | sudo tee /www/wwwroot/anti-webshell/evil.php
sleep 2
sudo ls /var/quarantine/controlplane/ | head
sudo tail -20 /var/log/webshell/controlplane.log
```

Expected: file quarantined (or removed); CRITICAL log line; alert if webhook/email configured.

- [ ] **Step 3: Confirm PHP not executed**

```bash
curl -sI "http://<monitor-host>/evil.php" | head -5
```

Expected: `403` (or 404 if already quarantined). If `200` with PHP body, fix Nginx before go-live.

- [ ] **Step 4: Lock cycle**

```bash
sudo ./manage_sites.sh unlock-self
echo test | sudo tee -a /www/wwwroot/anti-webshell/index.html
sudo ./manage_sites.sh lock-self
echo fail | sudo tee -a /www/wwwroot/anti-webshell/index.html
```

Expected: last write fails (Permission denied / Operation not permitted).

- [ ] **Step 5: Final commit only if docs/scripts tweaks discovered**

Fix any gaps; commit with message describing the fix.

---

## Spec coverage self-review

| Spec requirement | Task |
|------------------|------|
| Builtin `controlplane` site | 2, 3 |
| Multi-root `WATCH_DIRS_FILE` | 1, 2 |
| Paths tier 3 + env overrides | 2, 6 |
| Lock/unlock publish flow | 3, 6, 7 |
| STRICT quarantine + dangerous ext incl. html/js/php | 1, 2 |
| External alerts via site conf | 2 (alert env), 6 |
| Static-only Nginx | 6, 7 |
| GUI read-only; no API delete | 4, 5 |
| CLI `disable-self` only | 3, 4 |
| `.env` permission only | 2 |
| Backend excludes | 1 exclude + 2 critical builder |
| `docs/tmp/` gitignore | already done |
| No auditd / no scheme B | respected |

## Placeholder scan

No TBD/TODO left in task steps. Manual Task 7 is explicitly verification.

## Type / name consistency

- `site_id`: `controlplane`
- Conf: `WATCH_DIRS_FILE`, `STRICT_QUARANTINE`
- CLI: `ensure-self`, `lock-self`, `unlock-self`, `disable-self`
- API: `GET /api/service/self-guard`; delete/create 403 for `controlplane`
