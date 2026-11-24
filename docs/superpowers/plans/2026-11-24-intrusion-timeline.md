# Intrusion Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a file is quarantined, build an attack timeline from audit, web access, and auth logs, send a second short alert, and show the full timeline in the site event drawer.

**Architecture:** `sync_audit.sh` owns generated audit rules. The monitor quarantines immediately, writes an anchor, and starts `webshell_investigate.sh` in a separate systemd scope. That wrapper runs `webshell_investigate.py` and sends the second alert through `webshell_monitor.sh --send-alert-file`. The control plane only stores log paths and reads the finished JSON.

**Tech Stack:** Bash, Python 3 standard library, Flask/SQLAlchemy, Vue 3 + Element Plus.

**Spec:** `docs/superpowers/specs/2026-11-24-intrusion-timeline-design.md`

## Global Constraints

- Investigate only after `quarantine_file` succeeds. Do not investigate ordinary creates, un-quarantined suspicious files, or critical-file restores.
- First quarantine alert text and its existing 60-second dedupe stay as they are. The second alert does not use that dedupe.
- `incident_id` format: `YYYYmmddTHHMMSSZ_<first 8 hex chars of sha256(original path)>`.
- Audit rules file `/etc/audit/rules.d/webshell.rules` is generated and overwritten. Hand edits go only in `/etc/webshell-monitor/audit.local.rules`.
- `never,exit` rules come before `always,exit` rules. Key `webshell_write` uses `perm=wa` for both `arch=b64` and `arch=b32`. Exclude key is `webshell_exclude`.
- Do not emit `-e 2`.
- Do not turn whitelist directories into `never` rules. Noisy excludes are `.git`, `vendor`, `runtime`, `Runtime`, `storage/framework`, `storage/logs`, `bootstrap/cache`, plus directory tokens parsed from that site's `INOTIFY_EXCLUDE`.
- Watch roots: non-empty `<site>.watch` absolute paths, otherwise `PROJECT_DIR`. Include `controlplane`.
- Access log selection: per-site `ACCESS_LOG` if set; else `DEFAULT_ACCESS_LOG` filtered by `SITE_HOSTS` when those hosts are set; else HTTP gap `无法区分站点`.
- Log path must be absolute and a regular file (symlink to a regular file is allowed). Open it in Python, not via a shell.
- Read at most the last 32MB of each log. Do not open rotated/compressed logs.
- `T` is the latest successful `webshell_write` PATH equal to the file in the 120 seconds before quarantine. Otherwise `T = quarantine - 1 second` and `time_estimated=true`.
- Windows: write requests `T-15s` to `T+2s`; same-IP prior hits `T-30min`; filename hits until quarantine `+2min`; auth `T-30min` to `T+5min`.
- Entry order: `cron`, `ssh`, `web_rce`, `web_upload`, `existing_shell`, `unknown`.
- RCE substrings, case-insensitive: `invokefunction`, `call_user_func`, `think\app`, `/vendor/phpunit`, `eval-stdin.php`, `auto_prepend_file`. Do not invent CVE ids.
- Confidence high: audit hit AND (exactly one POST/PUT in the 15s window for a web entry, OR exactly one matching `Accepted` for ssh, OR exactly one cron/crond audit write for cron). Medium: audit hit with more than one HTTP candidate, OR no audit hit and exactly one POST/PUT. Everything else, including `unknown`, is low.
- Timeline JSON stores judgment, time, source, IP, method, URL, status, UA, exe, uid, auid, size, sha256, reason. No file body, no deobfuscation, no execution.
- Alert query string is truncated to 200 characters. JSON keeps the full URL.
- Investigator timeout is 20 seconds via `systemd-run --scope` outside the monitor cgroup. No retry queue. Delete the anchor only after the JSON is written.
- Missing log: keep the timeline, add a gap, lower confidence by the rules above. Do not invent IP, URL, or process.
- `install` does not rewrite existing site confs. Create `/etc/webshell-monitor/investigate.conf` only when absent. Probe only `/var/log/nginx/access.log` and `/var/log/apache2/access.log`. Do not guess `/www/wwwlogs/*.log`.
- Quarantines from before the audit rules were loaded are not backfilled.

---

### Task 1: Generated audit rules

**Files:**
- Create: `scripts/sync_audit.sh`
- Test: `scripts/tests/test_sync_audit.sh`

**Interfaces:**
- Consumes: site confs that contain `SITE_ID`, `PROJECT_DIR`, `INOTIFY_EXCLUDE`, optional `<site>.watch`, optional `audit.local.rules`.
- Produces: `sync_audit.sh` reads `CONFIG_ROOT` (default `/etc/webshell-monitor/sites`), writes `RULES_OUT` (default `/etc/audit/rules.d/webshell.rules`), appends `LOCAL_RULES` (default `/etc/webshell-monitor/audit.local.rules`). When `WEBSHELL_AUDIT_DRY_RUN=1`, it does not run `augenrules`. Exit 0 on a written file. Exit 1 only when `CONFIG_ROOT` is missing.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_sync_audit.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/sites" "$TMP/www/blog" "$TMP/www/shop/uploads" "$TMP/www/shop/runtime"
cat > "$TMP/sites/blog.conf" << EOF
SITE_ID="blog"
PROJECT_DIR="$TMP/www/blog"
INOTIFY_EXCLUDE='(\\.git|/vendor/|/runtime/)'
EOF
cat > "$TMP/sites/shop.conf" << EOF
SITE_ID="shop"
PROJECT_DIR="$TMP/www/shop"
INOTIFY_EXCLUDE='(\\.git|/vendor/|/runtime/)'
EOF
printf '%s\n' "$TMP/www/shop/uploads" > "$TMP/sites/shop.whitelist"
printf '%s\n' "$TMP/www/blog" "$TMP/extra" > "$TMP/sites/blog.watch"
cat > "$TMP/local.rules" << 'EOF'
# operator exclude
-a never,exit -F dir=/tmp/skip-me -k webshell_exclude
EOF

WEBSHELL_AUDIT_DRY_RUN=1 \
  CONFIG_ROOT="$TMP/sites" \
  RULES_OUT="$TMP/webshell.rules" \
  LOCAL_RULES="$TMP/local.rules" \
  "$ROOT/scripts/sync_audit.sh"

RULES="$(cat "$TMP/webshell.rules")"
printf '%s\n' "$RULES" | grep -q 'overwritten'
printf '%s\n' "$RULES" | grep -q -- '-e 2' && { echo "FAIL: locked audit"; exit 1; }
printf '%s\n' "$RULES" | grep -q "$TMP/www/shop/uploads" && { echo "FAIL: whitelist excluded"; exit 1; }
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/www/shop/runtime"
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/www/blog"
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/extra"
printf '%s\n' "$RULES" | grep -q 'arch=b64'
printf '%s\n' "$RULES" | grep -q 'arch=b32'
printf '%s\n' "$RULES" | grep -q 'webshell_write'
printf '%s\n' "$RULES" | grep -q '/tmp/skip-me'
python3 - << PY
from pathlib import Path
text = Path("$TMP/webshell.rules").read_text()
never = text.find("-F dir=$TMP/www/shop/runtime")
always = text.find("-F dir=$TMP/www/shop ")
# shop project dir always-rule; runtime never must appear earlier
shop_always = text.find("dir=$TMP/www/shop -F perm=wa")
shop_never = text.find("dir=$TMP/www/shop/runtime")
assert shop_never != -1 and shop_always != -1 and shop_never < shop_always, (shop_never, shop_always)
print("order ok")
PY
echo OK
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `bash scripts/tests/test_sync_audit.sh`

Expected: FAIL because `scripts/sync_audit.sh` does not exist.

- [ ] **Step 3: Implement `scripts/sync_audit.sh`**

```bash
#!/usr/bin/env bash
# Rewrite /etc/audit/rules.d/webshell.rules from site confs.
# Custom rules belong in audit.local.rules and are appended verbatim.
set -euo pipefail

CONFIG_ROOT="${CONFIG_ROOT:-/etc/webshell-monitor/sites}"
RULES_OUT="${RULES_OUT:-/etc/audit/rules.d/webshell.rules}"
LOCAL_RULES="${LOCAL_RULES:-/etc/webshell-monitor/audit.local.rules}"

if [ ! -d "$CONFIG_ROOT" ]; then
  echo "sync-audit: config root missing: $CONFIG_ROOT" >&2
  exit 1
fi

NOISY_RELS=(.git vendor runtime Runtime storage/framework storage/logs bootstrap/cache)

exclude_rels() {
  local exclude="${1:-}"
  local token rel seen=" "
  for rel in "${NOISY_RELS[@]}"; do
    printf '%s\n' "$rel"
    seen="$seen $rel "
  done
  printf '%s' "$exclude" | tr '|' '\n' | sed -E 's/\\//g; s/^\(?//; s/\)?$//; s/^[*?.]*//; s/^\/+//; s/\/+$//' | while IFS= read -r token; do
    [ -n "$token" ] || continue
    case "$token" in
      *.*|*'*'*|*'^'*|*'('*|*')'*) continue ;;
    esac
    case "$seen" in
      *" $token "*) continue ;;
    esac
    printf '%s\n' "$token"
  done
}

{
  echo "# Generated by sync_audit.sh — overwritten on install, add-site, remove-site, and GUI save."
  echo "# Put custom rules in /etc/webshell-monitor/audit.local.rules"
  shopt -s nullglob
  for conf in "$CONFIG_ROOT"/*.conf; do
    unset SITE_ID PROJECT_DIR INOTIFY_EXCLUDE || true
    # shellcheck disable=SC1090
    source "$conf"
    [ -n "${SITE_ID:-}" ] || continue
    roots=()
    watch="$CONFIG_ROOT/${SITE_ID}.watch"
    if [ -f "$watch" ]; then
      while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"
        [ -z "$line" ] && continue
        [[ "$line" == \#* ]] && continue
        [[ "$line" == /* ]] && roots+=("$line")
      done < "$watch"
    fi
    if [ "${#roots[@]}" -eq 0 ] && [ -n "${PROJECT_DIR:-}" ]; then
      roots+=("$PROJECT_DIR")
    fi
    rels=()
    while IFS= read -r rel; do
      [ -n "$rel" ] && rels+=("$rel")
    done < <(exclude_rels "${INOTIFY_EXCLUDE:-}")
    for root in "${roots[@]}"; do
      root="${root%/}"
      nevers=()
      for rel in "${rels[@]}"; do
        [ -n "$rel" ] || continue
        nevers+=("-a never,exit -F dir=${root}/${rel} -k webshell_exclude")
      done
      if [ "${#nevers[@]}" -gt 0 ]; then
        printf '%s\n' "${nevers[@]}" | awk '!seen[$0]++'
      fi
      echo "-a always,exit -F arch=b64 -F dir=${root} -F perm=wa -k webshell_write"
      echo "-a always,exit -F arch=b32 -F dir=${root} -F perm=wa -k webshell_write"
    done
  done
  if [ -f "$LOCAL_RULES" ]; then
    cat "$LOCAL_RULES"
  fi
} > "$RULES_OUT.tmp"
mv "$RULES_OUT.tmp" "$RULES_OUT"

if [ "${WEBSHELL_AUDIT_DRY_RUN:-0}" = "1" ]; then
  echo "sync-audit: wrote $RULES_OUT (dry-run, not loaded)"
  exit 0
fi
if ! command -v augenrules >/dev/null 2>&1; then
  echo "sync-audit: wrote $RULES_OUT but augenrules is missing" >&2
  exit 1
fi
augenrules --load
echo "sync-audit: loaded $RULES_OUT"
```

`chmod +x scripts/sync_audit.sh`. Duplicate relative paths are removed with `awk '!seen[$0]++'` so the script stays usable on bash 3.2.

- [ ] **Step 4: Run the test**

Run: `bash scripts/tests/test_sync_audit.sh`

Expected: prints `order ok` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_audit.sh scripts/tests/test_sync_audit.sh
git commit -m "$(cat <<'EOF'
Add generated audit rules for monitored site directories.

EOF
)"
```

---

### Task 2: Per-site access log settings

**Files:**
- Modify: `scripts/render_site_config.sh`
- Modify: `backend/app/executor/base.py` (`SiteRuntimeConfig`)
- Modify: `backend/app/executor/local.py` (`write_config`, `_write_config_direct`)
- Test: `scripts/tests/test_render_access_log.sh`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: site conf contains `ACCESS_LOG="..."` and `SITE_HOSTS="..."` (empty string allowed). `SiteRuntimeConfig` gains `access_log: str = ""` and `site_hosts: str = ""`. `render_site_config.sh` accepts `--access-log PATH` and `--site-hosts "host1 host2"`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_render_access_log.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/proj" "$TMP/out" "$TMP/log"
: > "$TMP/log/access.log"
PROFILES_DIR="$ROOT/scripts/profiles" \
  "$ROOT/scripts/render_site_config.sh" blog "$TMP/proj" plain_php \
  --output-dir "$TMP/out" \
  --access-log "$TMP/log/access.log" \
  --site-hosts "blog.example www.blog.example"
grep -q 'ACCESS_LOG="'"$TMP/log/access.log"'"' "$TMP/out/blog.conf"
grep -q 'SITE_HOSTS="blog.example www.blog.example"' "$TMP/out/blog.conf"
# rendering again without the flags yields empty values, it does not invent a path
PROFILES_DIR="$ROOT/scripts/profiles" \
  "$ROOT/scripts/render_site_config.sh" blog "$TMP/proj" plain_php \
  --output-dir "$TMP/out"
grep -q 'ACCESS_LOG=""' "$TMP/out/blog.conf"
grep -q 'SITE_HOSTS=""' "$TMP/out/blog.conf"
echo OK
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `bash scripts/tests/test_render_access_log.sh`

Expected: FAIL because `--access-log` is an unknown option.

- [ ] **Step 3: Implement the renderer and the executor fields**

In `scripts/render_site_config.sh`, add variables `ACCESS_LOG=""` and `SITE_HOSTS=""`, options `--access-log` and `--site-hosts`, and these two lines in the generated conf next to `DINGTALK_WEBHOOK`:

```bash
ACCESS_LOG="$ACCESS_LOG"
SITE_HOSTS="$SITE_HOSTS"
```

Reject `--access-log` unless the value is empty or an absolute path:

```bash
--access-log)
  ACCESS_LOG="$2"
  if [ -n "$ACCESS_LOG" ] && [[ "$ACCESS_LOG" != /* ]]; then
    echo "错误: ACCESS_LOG 必须是绝对路径" >&2
    exit 1
  fi
  shift 2
  ;;
```

In `SiteRuntimeConfig`, add `access_log: str = ""` and `site_hosts: str = ""`.

In `LocalSystemdExecutor.write_config`, append the flags when the strings are non-empty:

```python
if cfg.access_log:
    args += ["--access-log", cfg.access_log]
if cfg.site_hosts:
    args += ["--site-hosts", cfg.site_hosts]
```

In `_write_config_direct`, add the same two quoted assignments to the conf text. Empty strings stay empty. Do not call `sync_audit.sh` from this task.

- [ ] **Step 4: Run the test**

Run: `bash scripts/tests/test_render_access_log.sh`

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add scripts/render_site_config.sh scripts/tests/test_render_access_log.sh backend/app/executor/base.py backend/app/executor/local.py
git commit -m "$(cat <<'EOF'
Record optional per-site access log path and host names.

EOF
)"
```

---

### Task 3: Install, add, and remove keep audit rules in sync

**Files:**
- Modify: `scripts/manage_sites.sh`
- Modify: `scripts/README.md`
- Modify: `docs/DEPLOY.md`
- Test: `scripts/tests/test_install_preserves_conf.sh`

**Interfaces:**
- Consumes: `scripts/sync_audit.sh` from Task 1. Site conf shape from Task 2.
- Produces: `manage_sites.sh sync-audit` exports `CONFIG_ROOT` and runs `sync_audit.sh`. `cmd_add_site` and `cmd_remove_site` call it after the site change. `cmd_install` copies `sync_audit.sh`, creates `investigate.conf` only if missing, runs `sync-audit`, and restarts `webshell-monitor@*`. `cmd_install` does not call `render_site_config.sh`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_install_preserves_conf.sh`. It does not call `cmd_install` (that needs root and restarts systemd). It checks the install function source and the extracted conf-prep helper:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

awk '/^cmd_install\(\)/,/^cmd_add_site\(\)/' "$ROOT/scripts/manage_sites.sh" \
  | grep -q render_site_config && { echo "FAIL: install rewrites site conf"; exit 1; }

# shellcheck disable=SC1091
source /dev/null
eval "$(sed -n '/^prepare_investigate_conf/,/^}/p' "$ROOT/scripts/manage_sites.sh")"
prepare_investigate_conf "$TMP/investigate.conf" "$TMP"
test -f "$TMP/investigate.conf"
# second call must not overwrite a custom path
printf '%s\n' 'DEFAULT_ACCESS_LOG="/keep/me.log"' 'AUTH_LOG="/var/log/auth.log"' 'AUDIT_LOG="/var/log/audit/audit.log"' > "$TMP/investigate.conf"
prepare_investigate_conf "$TMP/investigate.conf" "$TMP"
grep -q '/keep/me.log' "$TMP/investigate.conf"
echo OK
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `bash scripts/tests/test_install_preserves_conf.sh`

Expected: FAIL because `prepare_investigate_conf` is missing. The `render_site_config` grep passes only after `cmd_install` is updated; if the function is still missing, `sed` prints nothing and `eval` fails first. That is the expected red state.

- [ ] **Step 3: Implement install integration**

Add this function to `scripts/manage_sites.sh` above `cmd_install`:

```bash
prepare_investigate_conf() {
  local dest="$1"
  local probe_root="${2:-/}"
  if [ -f "$dest" ]; then
    return 0
  fi
  local access=""
  if [ -f "${probe_root%/}/var/log/nginx/access.log" ]; then
    access="${probe_root%/}/var/log/nginx/access.log"
  elif [ -f "${probe_root%/}/var/log/apache2/access.log" ]; then
    access="${probe_root%/}/var/log/apache2/access.log"
  fi
  # When probe_root is /, paths are /var/log/... ; when probe_root is a temp dir, paths are $tmp/var/log/...
  if [ "$probe_root" = "/" ]; then
    :
  fi
  mkdir -p "$(dirname "$dest")"
  cat > "$dest" << EOF
DEFAULT_ACCESS_LOG="$access"
AUTH_LOG="/var/log/auth.log"
AUDIT_LOG="/var/log/audit/audit.log"
EOF
}
```

Fix the path logic so a probe root of `/` yields `/var/log/nginx/access.log` and a temp root yields `$TMP/var/log/nginx/access.log`. Implement it as:

```bash
local nginx="${probe_root%/}/var/log/nginx/access.log"
local apache="${probe_root%/}/var/log/apache2/access.log"
```

Do not look at `/www/wwwlogs`.

In `cmd_install`, after copying the other scripts:

```bash
cp "$SCRIPT_HOME/sync_audit.sh" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/sync_audit.sh"
prepare_investigate_conf /etc/webshell-monitor/investigate.conf /
if command -v apt-get >/dev/null 2>&1 && ! command -v auditctl >/dev/null 2>&1; then
  apt-get update -qq || true
  apt-get install -y auditd || print_warning "auditd 未装上，时间线将缺少进程记录"
fi
CONFIG_ROOT="$CONFIG_ROOT" "$INSTALL_DIR/sync_audit.sh" || print_warning "审计规则未加载"
systemctl restart 'webshell-monitor@*' || true
```

`cmd_install` must not call `render_site_config.sh`.

At the end of `cmd_add_site` and `cmd_remove_site`:

```bash
CONFIG_ROOT="$CONFIG_ROOT" "$INSTALL_DIR/sync_audit.sh" || print_warning "审计规则未加载"
```

Add command `sync-audit` that runs the same line and document it in the usage text.

In `scripts/README.md` and `docs/DEPLOY.md`, add the upgrade note and the manual edit procedure from the spec:

- `install` restarts existing `webshell-monitor@*` and does not rewrite site confs.
- Edit `/etc/webshell-monitor/audit.local.rules`, then `sudo /opt/webshell_monitor/manage_sites.sh sync-audit` and `sudo auditctl -l | grep webshell`.
- Do not put `-e 2` in the rules.
- Do not hand-edit `/etc/audit/rules.d/webshell.rules`.
- Fill a per-site access log in the control plane when the global file is empty. `/www/wwwlogs/*.log` is not auto-selected.

Add the three manual acceptance checks from spec section 12 to `docs/DEPLOY.md` section 9, after the existing `eval(` quarantine check.

- [ ] **Step 4: Run the tests**

Run: `bash scripts/tests/test_install_preserves_conf.sh && bash scripts/tests/test_sync_audit.sh && bash scripts/tests/test_render_access_log.sh`

Expected: each prints `OK`.

- [ ] **Step 5: Commit**

```bash
git add scripts/manage_sites.sh scripts/tests/test_install_preserves_conf.sh scripts/README.md docs/DEPLOY.md
git commit -m "$(cat <<'EOF'
Load audit rules on install without rewriting existing site configs.

EOF
)"
```

---

### Task 4: Timeline builder

**Files:**
- Create: `scripts/webshell_investigate.py`
- Create: `scripts/tests/test_investigate.py`

**Interfaces:**
- Consumes: an anchor dict, not a live auditd.
- Produces:
  - `investigate(anchor: dict, logs: dict) -> dict` where `logs` has string keys `access`, `audit`, `auth`. Missing key or `None` means that log is absent.
  - `alert_text(timeline: dict) -> str` returns the second-alert body.
  - `read_log_tail(path: str, limit: int = 32 * 1024 * 1024) -> str` rejects non-absolute paths and non-regular files.
  - CLI: `python3 scripts/webshell_investigate.py --anchor PATH --output PATH` writes JSON, prints `alert_text` to stdout, exits 0 when `status=ok`, 2 when JSON was written with `status=failed`, 3 when JSON could not be written. On exit 3, stdout is still the failure alert body.

Anchor keys used by `investigate`: `incident_id`, `site_id`, `file_path`, `quarantine_path`, `reason`, `event_time` (ISO-8601), `project_dir`, `access_log` (already chosen path or `""`), `site_hosts` (list of strings), `access_is_global` (bool).

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_investigate.py`. Load the module with `importlib` from `scripts/webshell_investigate.py`. Cover:

```python
def test_web_upload_high():
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": combined(T, "POST", "/upload.php?x=1", "1.2.3.4", 200, "blog.example"),
        "auth": "",
    })
    assert timeline["entry"] == "web_upload"
    assert timeline["confidence"] == "high"
    assert timeline["actor"]["ip"] == "1.2.3.4"
    assert timeline["status"] == "ok"

def test_web_rce_prior_invokefunction():
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": "\n".join([
            combined(T - timedelta(minutes=10), "GET", "/index.php?s=invokefunction", "1.2.3.4", 200, "blog.example"),
            combined(T, "POST", "/public/x.php", "1.2.3.4", 200, "blog.example"),
        ]),
        "auth": "",
    })
    assert timeline["entry"] == "web_rce"

def test_ssh_when_shell_and_accepted():
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/bin/bash", comm="bash", uid="root", auid="root", path=FILE),
        "access": "",
        "auth": auth_accepted(T - timedelta(minutes=5), user="root", ip="9.9.9.9"),
    })
    assert timeline["entry"] == "ssh"
    assert timeline["confidence"] == "high"

def test_missing_audit_unique_post_is_medium_with_gap():
    timeline = investigate(anchor(), {
        "audit": None,
        "access": combined(T, "POST", "/up.php", "1.2.3.4", 200, "blog.example"),
        "auth": "",
    })
    assert any("审计" in g for g in timeline["gaps"])
    assert timeline["confidence"] == "medium"
    assert timeline["actor"]["ip"] == "1.2.3.4"

def test_bad_access_format_keeps_audit():
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": "this is not a combined log\n",
        "auth": "",
    })
    assert any("无法解析" in g for g in timeline["gaps"])
    assert timeline["actor"]["exe"] == "/usr/sbin/php-fpm"

def test_alert_truncates_query_json_keeps_full_url():
    long_q = "a" * 400
    timeline = investigate(anchor(), {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": combined(T, "POST", f"/up.php?{long_q}", "1.2.3.4", 200, "blog.example"),
        "auth": "",
    })
    assert long_q in timeline["actor"]["url"]
    body = alert_text(timeline)
    assert "a" * 201 not in body
    assert "置信度: 高" in body

def test_relative_log_path_rejected(tmp_path):
    with pytest.raises(ValueError):
        read_log_tail("relative.log")

def test_global_log_without_hosts_marks_http_gap():
    anc = anchor()
    anc["access_is_global"] = True
    anc["site_hosts"] = []
    timeline = investigate(anc, {
        "audit": audit_write(ts=T, exe="/usr/sbin/php-fpm", comm="php-fpm", uid="www-data", path=FILE),
        "access": combined(T, "POST", "/up.php", "1.2.3.4", 200, "blog.example"),
        "auth": "",
    })
    assert any("无法区分站点" in g for g in timeline["gaps"])
    assert timeline["entry"] == "unknown"
    assert timeline["confidence"] == "low"
    assert timeline["actor"]["ip"] == ""
```

Helpers `anchor()`, `combined()`, `audit_write()`, `auth_accepted()` build the fixture strings. `T` is a fixed timezone-aware datetime. `FILE` is `/www/wwwroot/blog/public/x.php`. `audit_write` emits one `type=SYSCALL` line and one `type=PATH` line sharing `msg=audit(<epoch>.0:1)` with `key="webshell_write"` and `success=yes`.

`test_global_log_without_hosts_marks_http_gap` does not use the access line. php-fpm with no usable HTTP request is `unknown` / `low`, and `actor["ip"]` is `""`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m pytest scripts/tests/test_investigate.py -v`

Expected: FAIL with import error for `webshell_investigate`.

- [ ] **Step 3: Implement `scripts/webshell_investigate.py`**

Implement the pure functions to satisfy the tests and the Global Constraints. Parsing rules:

- Access lines match nginx/apache combined, optional trailing host token. Timestamps use `%d/%b/%Y:%H:%M:%S %z`.
- A line that does not match is ignored. If the access string is non-empty and zero lines match, append gap `访问日志格式无法解析`.
- If `access` is `None`, append gap `无访问日志`. If `audit` is `None`, append gap `无审计记录`. If `auth` is `None`, append gap `无登录日志`. Empty string means the file existed but had no rows; do not add the missing-file gap.
- Audit records group by the `msg=audit(seconds.millis:serial)` id. Keep a record only when `key="webshell_write"`, `success=yes`, and a PATH `name=` equals `file_path`.
- Auth `Accepted` lines set the login user. Match ssh when `auid` equals that user.
- `sha256` and `size` are read from `quarantine_path` when that file exists; otherwise `""` and `0`. Tests omit the file.
- `alert_text` for `status=ok` uses the spec body, with Chinese labels `置信度: 高|中|低`, `入口:`, `来源 IP:`, `URL:`, `进程:`, `文件:`. Missing actor fields render as `未知`. Omit the `缺口:` line when `gaps` is empty; otherwise one line, gaps joined by `；`.
- `alert_text` for `status=failed` is the failure body from the spec (`未能生成入侵时间线`).
- `read_log_tail` seeks from the end, reads at most 32MB, decodes with `errors="replace"`.
- CLI writes JSON with `indent=2` and a trailing newline. After the output file is written (exit 0 or 2), delete the anchor when its path ends with `.anchor.json`. On an unreadable anchor, print the failure body and exit 3 without creating the output file and without deleting anything.

Do not shell out. Do not include file contents in the JSON.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest scripts/tests/test_investigate.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/webshell_investigate.py scripts/tests/test_investigate.py
git commit -m "$(cat <<'EOF'
Build an intrusion timeline from audit, access, and auth logs.

EOF
)"
```

---

### Task 5: Launch investigation after quarantine

**Files:**
- Modify: `scripts/webshell_monitor.sh`
- Create: `scripts/webshell_investigate.sh`
- Test: `scripts/tests/test_investigate_launch.sh`

**Interfaces:**
- Consumes: `investigate` CLI from Task 4. Site conf keys `ACCESS_LOG`, `SITE_HOSTS`, plus `/etc/webshell-monitor/investigate.conf`.
- Produces: `quarantine_file` logs `incident=<id>` on the quarantine line and calls `launch_investigation`. `launch_investigation` writes `/var/lib/webshell/incidents/<site>/<id>.anchor.json` unless `WEBSHELL_INCIDENTS_DIR` overrides the root. When `WEBSHELL_INVESTIGATE_DRY_RUN=1`, it does not call `systemd-run`. Otherwise it runs:

```bash
systemd-run --scope --collect \
  --unit="webshell-investigate-${SITE_ID}-${incident_id}" \
  --property=RuntimeMaxSec=20 \
  "$SCRIPT_HOME/webshell_investigate.sh" --site "$SITE_ID" --anchor "$anchor_path"
```

`webshell_monitor.sh --site ID --send-alert-file PATH` sources the site conf, calls `send_alert` with subject `入侵路径 [ID]` or `未能生成入侵时间线 [ID]` based on the first line, and `log_message CRITICAL` the first line. It does not enter `main`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_investigate_launch.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$("$ROOT/scripts/webshell_monitor.sh" --self-test-investigate 2>&1)" || {
  echo "$OUT" >&2
  exit 1
}
echo "$OUT" | grep -q "OK"
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `bash scripts/tests/test_investigate_launch.sh`

Expected: FAIL with `未知参数: --self-test-investigate`.

- [ ] **Step 3: Implement the hook**

Add `--self-test-investigate` next to `--self-test-create-alert`. The self-test builds a temp site dir, temp conf with `ACCESS_LOG` pointing at a temp file, `WEBSHELL_INVESTIGATE_DRY_RUN=1`, and `WEBSHELL_INCIDENTS_DIR` under the temp dir. It calls `launch_investigation` with a fake quarantined path. Assert the anchor JSON contains `incident_id`, `file_path`, `access_log`, and `site_id`, and that the command log does not contain `systemd-run`. Print `OK`.

`incident_id_for` uses `date -u +%Y%m%dT%H%M%SZ` and `sha256sum` (fallback `shasum -a 256`) and takes 8 hex chars.

`launch_investigation` resolves logs:

- `access_log` = site `ACCESS_LOG` when non-empty.
- Else `DEFAULT_ACCESS_LOG` from `investigate.conf`, with `access_is_global=true`.
- `site_hosts` splits `SITE_HOSTS` on spaces.
- `auth_log` and `audit_log` come from `investigate.conf`, defaulting to `/var/log/auth.log` and `/var/log/audit/audit.log`.

Anchor `event_time` is UTC ISO-8601 of the quarantine moment.

In `quarantine_file`, after the successful copy and the existing quarantine log, change that log line to include ` incident=$incident_id` and call `launch_investigation` for both the alerting and `no_alert` branches. Do not wait for it.

`webshell_investigate.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_HOME="$(cd "$(dirname "$0")" && pwd)"
SITE=""
ANCHOR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site) SITE="$2"; shift 2 ;;
    --anchor) ANCHOR="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 1 ;;
  esac
done
OUT="${ANCHOR%.anchor.json}.json"
BODY="$(mktemp)"
set +e
python3 "$SCRIPT_HOME/webshell_investigate.py" --anchor "$ANCHOR" --output "$OUT" > "$BODY"
set -e
"$SCRIPT_HOME/webshell_monitor.sh" --site "$SITE" --send-alert-file "$BODY" || true
rm -f "$BODY"
```

`--send-alert-file` is parsed before `require_site_arg` returns into the monitor loop, after `load_site_config`. Subject is `入侵路径 [$SITE_ID]` when the first line starts with `入侵路径`, otherwise `未能生成入侵时间线 [$SITE_ID]`.

Copy `webshell_investigate.py` and `webshell_investigate.sh` in `cmd_install` the same way as `sync_audit.sh`, and `chmod +x` the shell script.

- [ ] **Step 4: Run the tests**

Run: `bash scripts/tests/test_investigate_launch.sh && bash scripts/tests/test_alert_on_create.sh && python3 -m pytest scripts/tests/test_investigate.py -q`

Expected: shell tests print `OK`; pytest passes. The existing create-alert self-test must still pass, which shows the first alert path was not retimed.

- [ ] **Step 5: Commit**

```bash
git add scripts/webshell_monitor.sh scripts/webshell_investigate.sh scripts/manage_sites.sh scripts/tests/test_investigate_launch.sh
git commit -m "$(cat <<'EOF'
Start intrusion analysis after quarantine without delaying isolation.

EOF
)"
```

---

### Task 6: Control-plane data and timeline API

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/__init__.py` (`_ensure_schema`)
- Modify: `backend/app/services/site_sync.py`
- Modify: `backend/app/api/sites.py`
- Modify: `backend/app/api/events.py`
- Modify: `backend/app/api/service.py`
- Modify: `backend/app/executor/base.py`
- Modify: `backend/app/executor/local.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: incident JSON files and conf fields from Tasks 2 and 4.
- Produces:
  - `Site.access_log`, `Site.site_hosts` nullable strings, included in `to_dict`.
  - `MonitorEvent.incident_id` nullable indexed string, included in `to_dict`.
  - `Executor.read_incident(self, site_id: str, incident_id: str) -> ExecResult` with `data={"timeline": dict}` or `ok=False`.
  - `Executor.audit_status(self, site_id: str) -> ExecResult` with `data={"auditd": str, "rules_loaded": bool, "sync_command": str, "check_command": str}`.
  - `GET /api/events/<event_pk>/timeline`
  - `GET /api/service/audit-status`
  - `build_runtime_config` copies `access_log` and `site_hosts` into `SiteRuntimeConfig`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api.py`:

```python
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
    recent = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.utcnow() - timedelta(seconds=120)).strftime("%Y-%m-%d %H:%M:%S")
    log.write_text(
        f"[{recent}] [CRITICAL] [site=blog] 文件已隔离: /tmp/a.php -> /tmp/q incident=20261124T034211Z_ab12cd34\n"
        f"[{old}] [CRITICAL] [site=blog] 文件已隔离: /tmp/b.php -> /tmp/q2 incident=20261124T000000Z_bbbbbbbb\n",
        encoding="utf-8",
    )
    conf = Path(os.environ["WEBSHELL_CONFIG_ROOT"]) / "blog.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + f'LOG_FILE="{log}"\n', encoding="utf-8")
    listed = client.get("/api/events/?site_id=blog", headers=headers)
    events = listed.get_json()["events"]
    by_path = {e["file_path"]: e for e in events}
    recent_row = next(e for e in events if "ab12cd34" in (e.get("incident_id") or "") or "ab12cd34" in e["message"])
    old_row = next(e for e in events if "bbbbbbbb" in (e.get("incident_id") or "") or "bbbbbbbb" in e["message"])
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
```

`tail_logs` today returns a dry-run placeholder before it can see a real file. Change it so a `LOG_FILE=` line in the site conf is honored whenever that path exists, including when `dry_run` is true. Return the placeholder only when the resolved file is missing. The test writes the log, points `LOG_FILE` at it, then GETs `/api/events/`. `_ingest_from_log` rate-limits inside one process; this client process has not ingested yet, so the first GET reads the file.

Also assert `incident_id` on the row equals `20261124T034211Z_ab12cd34`, not only that the message contains it.

Add a test that `GET /api/service/audit-status?site_id=blog` returns the two command strings:

```python
assert body["sync_command"] == "sudo /opt/webshell_monitor/manage_sites.sh sync-audit"
assert body["check_command"] == "sudo auditctl -l | grep webshell"
```

In dry-run, `auditd` is `unknown` and `rules_loaded` is false.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd backend && WEBSHELL_DRY_RUN=1 pytest -q tests/test_api.py::test_site_access_log_roundtrip tests/test_api.py::test_timeline_pending_then_ready_then_missing`

Expected: FAIL with unexpected keys or 404 on the timeline route.

- [ ] **Step 3: Implement schema, ingest, and routes**

`_ensure_schema` adds, when missing:

- `sites.access_log VARCHAR(500)`
- `sites.site_hosts VARCHAR(500)`
- `monitor_events.incident_id VARCHAR(64)`

SQLite `ALTER TABLE ... ADD COLUMN` is enough. Create the index in application code only when the dialect supports `CREATE INDEX IF NOT EXISTS`; otherwise skip the index on failure. New databases get the column from the model, so `create_all` covers tests that start empty. Keep the ALTER for existing databases.

`create_site` and `update_site` accept `access_log` and `site_hosts`. Reject `access_log` that is non-empty and does not start with `/`, with HTTP 400 and `{"error":"访问日志必须是绝对路径"}`.

`_ingest_from_log` extracts `incident=([A-Za-z0-9]+)` into `incident_id`.

`read_incident` joins `WEBSHELL_INCIDENTS_DIR` or `/var/lib/webshell/incidents` with `site_id` and `incident_id` only after both match `^[A-Za-z0-9][A-Za-z0-9_-]*$` and `^[A-Za-z0-9]+$` respectively. Return the parsed JSON.

Timeline route, after loading the event for the current site:

| Condition | Status |
|-----------|--------|
| no `incident_id` | 404 `{"status":"missing","message":"该事件没有入侵分析"}` |
| JSON exists | 200 and the JSON object |
| no JSON and event age `< 30` seconds | 202 `{"status":"pending"}` |
| no JSON and age `>= 30` seconds | 404 `{"status":"missing","message":"..."}` |

Age uses `created_at` compared to `utcnow()`. The missing message prefers a later log line for the same `incident_id` that contains `未能生成`; otherwise `未能生成`.

`audit_status` in dry-run returns the fixed command strings, `auditd="unknown"`, `rules_loaded=False`. Outside dry-run, `systemctl is-active auditd` and `auditctl -l`, with `rules_loaded=True` only when the output contains both `webshell_write` and the site `PROJECT_DIR`.

`RemoteAgentExecutor` implements the two new methods by returning `_nyi()`.

`sync_site` after a successful `write_config` calls `executor.sync_audit()` when that method exists. `LocalSystemdExecutor.sync_audit` runs `sync_audit.sh` with `CONFIG_ROOT` set. In dry-run, `_run` already no-ops, so API tests do not touch `/etc/audit`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && WEBSHELL_DRY_RUN=1 pytest -q`

Expected: all existing tests and the new ones PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/tests/test_api.py
git commit -m "$(cat <<'EOF'
Expose intrusion timelines and per-site log settings in the API.

EOF
)"
```

---

### Task 7: Event drawer, log fields, and audit status

**Files:**
- Modify: `frontend/src/views/Sites.vue`
- Modify: `frontend/src/views/Config.vue`
- Modify: `frontend/src/views/Events.vue`
- Modify: `frontend/src/views/SiteOverview.vue`

**Interfaces:**
- Consumes: `POST/PUT /api/sites` fields `access_log` and `site_hosts`; `GET /api/events/:id/timeline`; `GET /api/service/audit-status`; event objects with `incident_id`.
- Produces: the create dialog and the site 防护策略 form save those fields. Event rows with an `incident_id` open a drawer. Site overview shows auditd state and the two commands.

- [ ] **Step 1: Add the form fields**

In `Sites.vue` create dialog, add inputs bound to `form.access_log` and `form.site_hosts` with labels `访问日志` and `Host`. Empty is allowed. Include them in the reset inside `openCreate`. The existing `createSite` posts `form`, so the new keys travel with it.

In `Config.vue` 防护策略, add the same two inputs to `protect`. `loadProtect` copies `data.access_log` and `data.site_hosts`. `saveProtect` sends them on `PUT /sites/${currentSiteId}` together with the existing lock flags. Labels: `访问日志`, `Host`. Placeholder for Host: `blog.example www.blog.example`.

- [ ] **Step 2: Add the event drawer**

In `Events.vue`, on row click, if `row.incident_id` is empty, do nothing. Otherwise open `el-drawer` and `api.get('/events/' + row.id + '/timeline', { skipErrorToast: true })`.

- HTTP 200 and `status === 'ok'`: show `entry`, `confidence`, actor IP, URL, exe, user, the `gaps` list, and `events` in the order returned.
- HTTP 200 and `status === 'failed'`: show `summary` and `gaps` only.
- HTTP 202: show `分析中` and a button that repeats the GET.
- HTTP 404: show `未能生成` and `message`.

Map `confidence` `high|medium|low` to `高|中|低` for display. Leave the table columns unchanged.

The axios interceptor rejects on non-2xx. Read `error.response.status` and `error.response.data` in the catch path. 202 is success and is returned as `response.data` by the interceptor, so the caller sees the JSON body directly, not the status. Have the timeline route use HTTP 200 for pending as well is forbidden by the spec. Adjust the caller: use a raw axios request for this one GET, or teach the interceptor to return 202 bodies. Smallest change: in `frontend/src/api/index.js`, when `error.response.status === 202`, `return error.response.data`. 202 does not go through the error interceptor if axios treats it as success. Axios default `validateStatus` accepts only 2xx, so 202 is success. 404 is the error path. Handle 404 in `catch`. Handle 200 payloads by `status` field `ok`, `failed`, or, if a future body says `pending`, the 分析中 state. For a real 202, the success path receives `{status:'pending'}`.

- [ ] **Step 3: Add audit status to the overview**

`SiteOverview.vue` loads `GET /api/service/audit-status` into a second card titled `审计`. Show `auditd` active/inactive/unknown as `运行中` / `未运行` / `未知`, and `rules_loaded` as `本站规则已加载` or `本站规则未加载`. Show the two commands in a `<pre>` so an operator can copy them. This card is the place that tells operators to edit `audit.local.rules` and rerun `sync-audit`. Do not add a new route.

- [ ] **Step 4: Verify the API still passes and the UI builds**

Run: `cd backend && WEBSHELL_DRY_RUN=1 pytest -q`

Run: `cd frontend && pnpm build`

Expected: pytest passes and the Vite build exits 0.

There is no frontend test runner. After `pnpm dev` is available, check three flows in the browser: create a site with an access log and Host and see them on 防护策略; open an event whose fixture timeline is `status=ok` and see the ordered events; open audit status and see both commands. If the dev servers are not running in this session, say so and stop after the build.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Sites.vue frontend/src/views/Config.vue frontend/src/views/Events.vue frontend/src/views/SiteOverview.vue frontend/src/api/index.js
git commit -m "$(cat <<'EOF'
Show intrusion timelines and audit-rule status in the site UI.

EOF
)"
```
