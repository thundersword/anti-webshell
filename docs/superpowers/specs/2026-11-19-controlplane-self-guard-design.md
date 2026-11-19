# Control-plane self-guard (方案 A) — Design

Date: 2026-11-19  
Status: approved for implementation planning  
Related incident: monitor UI under `/www/wwwroot/anti-webshell` overwritten with SEO spam HTML and a PHP file-manager (`docs/tmp/` samples; do not commit).

## 1. Problem

The monitor only watches each business site’s `PROJECT_DIR`. Control-plane paths are outside that perimeter:

- Management UI static files
- Engine scripts under `/opt/webshell_monitor`
- Site configs under `/etc/webshell-monitor`
- Backend under `/opt/anti-webshell/backend`

Attackers with write access on the same host can deface the UI (hide alerts), drop PHP tools into the UI tree, and continue writing business sites. A compromised UI must not be the only alert channel.

## 2. Goals (phase 1)

1. Detect and respond to unauthorized writes under control-plane paths.
2. Immutable-lock critical control-plane files by default; unlock only for intentional deploy.
3. Emit CRITICAL alerts via email/Webhook even if the GUI is tampered.
4. Serve the management UI as **static-only** (no PHP execution) at a configurable root.
5. Keep using the existing multi-site engine (方案 A), not a separate daemon (方案 B deferred).

Non-goals (phase 1): auditd process attribution, PHP auto_prepend request binding, remote multi-host agents.

## 3. Approach

**内建站点** `site_id=controlplane`：

- Installed/ensured by `manage_sites.sh install` (and idempotent `ensure-self`).
- Uses one unit `webshell-monitor@controlplane`.
- Profile / render path builds critical-file lists and a **multi-root watch list** (see §5.0): the four path roots are not under a single parent, so the engine must watch multiple directories in one site.
- Not deletable from the GUI; disable only via CLI (`disable-self`).

## 4. Paths (configurable)

| Role | Default | Notes |
|------|---------|--------|
| UI root | `/www/wwwroot/anti-webshell` | Env/`WEBSHELL_UI_ROOT`; replaces docs’ old `/var/www/webshell-ui` |
| Engine | `/opt/webshell_monitor` | `WEBSHELL_INSTALL_DIR` |
| Site configs | `/etc/webshell-monitor` | `WEBSHELL_CONFIG_ROOT` parent |
| Backend | `/opt/anti-webshell/backend` | Source only |

Exclusions under backend watch/lock:

- `.venv/`
- `__pycache__/`
- `*.db`, `*.sqlite*`
- `.env` — **permission hardening only** (`600`, owner root); do not auto-restore or chattr content in a way that blocks legitimate key rotation without unlock-self

## 5. Behaviors

### 5.0 Multi-root watch (engine change)

Today `webshell_monitor.sh` watches a single `PROJECT_DIR`. For `controlplane`, add support for multiple roots without splitting into four systemd units:

- New list file, e.g. `/etc/webshell-monitor/sites/controlplane.watch`, one absolute directory per line (the four roots that exist on disk).
- Conf key: `WATCH_DIRS_FILE=...` (if unset, fall back to `PROJECT_DIR` only for normal business sites).
- Monitor loop: one `inotifywait -m -r` per existing root, merged into the same handler pipeline (background jobs + `wait`, or equivalent).
- `PROJECT_DIR` for controlplane may be set to the UI root for backup-key relativity; quarantine/backup dirs stay `/var/quarantine/controlplane` and `/var/webshell_backup/controlplane`.

Business sites remain single-`PROJECT_DIR`; no behavior change required if `WATCH_DIRS_FILE` is absent.

### 5.1 Monitoring

For each watch root:

- Events: `create`, `close_write`, `moved_to` (same as engine today).
- Dangerous extensions include at least: `php|phtml|phar|html|htm|js|css|sh|py` plus existing engine set.
- New or overwritten dangerous files → quarantine (preferred) + CRITICAL alert.
- Modification of locked critical files → existing restore/lock semantics where applicable; always CRITICAL alert.
- Backend root: skip excluded paths (`.venv`, `__pycache__`, db files) via `INOTIFY_EXCLUDE` / noisy-path checks.

### 5.2 Locking

- After install/ensure: `chattr +i` on critical file lists for UI built assets, engine scripts, selected configs, backend `.py` (and similar) source files.
- UI: lock existing static files after deploy; new drops are still caught by inotify + quarantine.
- Unlock/lock via CLI only for routine ops:

```text
manage_sites.sh unlock-self
# update code, rsync UI, edit conf
manage_sites.sh lock-self
systemctl restart webshell-api webshell-monitor@controlplane
```

### 5.3 Alerts

- controlplane CRITICAL must use configured email and/or Webhook (site or global).
- GUI event list is informational only; operators must not rely on it alone after a UI compromise.

### 5.4 Static-only UI (Nginx)

Management vhost must not pass to php-fpm. Required shape:

- `root` = `WEBSHELL_UI_ROOT` (default `/www/wwwroot/anti-webshell`)
- `try_files` SPA fallback to `/index.html`
- `/api/` reverse-proxied to Gunicorn
- Explicit deny/403 for `php|phtml|phar|phpN|cgi|pl|py|jsp|asp|aspx`
- No `fastcgi_pass` / no panel `enable-php*.conf` includes
- Prefer dedicated server block; do not nest under a PHP-enabled business site
- Directory ownership: prefer `root:root`, not writable by `www` / php-fpm user

Document this in `docs/DEPLOY.md` and keep an example snippet under `docs/` or deploy section.

## 6. Control plane / GUI

- Read-only “自保 / controlplane” status: unit active, lock status summary, recent events.
- No delete site for `controlplane` in the API/GUI.
- Disable/remove self-guard = CLI only (`manage_sites.sh disable-self` with interactive confirmation).

## 7. Install / migrate

On `manage_sites.sh install` / `ensure-self`:

1. Resolve path overrides from env or `/etc/webshell-monitor/controlplane.env`.
2. Render `controlplane` site conf + critical/whitelist lists.
3. Enable and start `webshell-monitor@controlplane`.
4. Apply `lock-self` (or leave unlocked only if explicitly requested for first deploy).
5. Print reminder: verify Nginx has no PHP for UI root.

Existing installs: one-shot `ensure-self` after `git pull`.

## 8. Config surface

| Variable / file | Purpose |
|-----------------|--------|
| `WEBSHELL_UI_ROOT` | UI static root |
| `WEBSHELL_INSTALL_DIR` | Engine dir (existing) |
| `WEBSHELL_CONFIG_ROOT` | Sites conf dir (existing) |
| `WEBSHELL_BACKEND_DIR` | Backend source dir (new, default `/opt/anti-webshell/backend`) |
| `/etc/webshell-monitor/controlplane.env` | Optional persistent overrides for the above + alert hooks |

## 9. Testing

- Dry-run / unit: executor ensure paths; lock list parsing.
- Manual/integration on a test host:
  1. Drop `evil.php` / overwrite `index.html` under UI root → quarantine + alert; file not executable via HTTP (403).
  2. `unlock-self` → edit allowed → `lock-self` → edit fails with immutable.
  3. Stop GUI trust: confirm Webhook/email still received.
  4. `systemctl is-active webshell-monitor@controlplane`.

## 10. Docs & hygiene

- Update `docs/DEPLOY.md`: real UI path, static-only Nginx, unlock/lock-self publish flow, `ensure-self`.
- Update `scripts/README.md` briefly.
- Add `docs/tmp/` to `.gitignore` (incident samples must not be committed).

## 11. Out of scope / later

- auditd attribution on write attempts (including failed writes under +i).
- Independent `webshell-selfguard` process (方案 B).
- Automatic baseline hash drift UI beyond existing events.
- Monitoring `/opt/anti-webshell/frontend` source tree (only deployed UI root).

## 12. Approval record

- Approach: A (builtin `controlplane` site).
- Path tier: 3 (UI + engine + configs + backend).
- UI root: `/www/wwwroot/anti-webshell` (configurable).
- Static-only Nginx: required.
- User confirmed design sections; this document is the freeze for planning.
