# Alert on Create Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alert on every new file under monitored directories (whitelist silent), with 60s per-path external-alert dedupe and a single outbound message when dangerous-extension logic also fires.

**Architecture:** Extend `handle_create` in `webshell_monitor.sh`; stamp-file dedupe under quarantine dir; classify new log lines in events ingest; document behavior.

**Tech Stack:** Bash, existing `send_alert`, Flask events ingest, Markdown docs.

**Spec:** `docs/superpowers/specs/2026-11-19-alert-on-create-design.md`

## Global Constraints

- Any new file under watch roots alerts unless noisy or whitelisted.
- Whitelist creates: silent (no log, no alert) in phase 1.
- Dangerous-extension quarantine / STRICT / review path unchanged in effect; one external alert per event.
- External alert dedupe: same absolute path, 60 seconds.
- No GUI toggle in this plan.

---

### Task 1: Engine `handle_create` + dedupe helpers

**Files:**
- Modify: `scripts/webshell_monitor.sh`
- Create: `scripts/tests/test_alert_on_create.sh`
- Test: `scripts/tests/test_alert_on_create.sh`

**Interfaces:**
- Produces: `should_send_create_alert(path) -> 0/1` using stamp files; `mark_create_alert_sent(path)`; rewritten `handle_create`

- [ ] **Step 1: Write failing smoke test**

`scripts/tests/test_alert_on_create.sh` — source or extract helpers by running a minimal harness. Practical approach: add `--self-test-create-alert` flag that:

1. Sets `QUARANTINE_DIR` to a temp dir, `SITE_ID=test`, empty whitelist, fake `send_alert` that appends to `$TMP/alerts.log`
2. Calls `handle_create` on a `.txt` path (file must exist) → expect alerts.log non-empty and log contains `监控目录新增文件`
3. Calls again immediately → alerts.log still one line (dedupe)
4. Sets whitelist to that file’s parent → `handle_create` → no new alert

Until implemented, running the script fails.

- [ ] **Step 2: Run test — expect fail**

```bash
bash scripts/tests/test_alert_on_create.sh
```

Expected: FAIL (missing flag or missing helpers)

- [ ] **Step 3: Implement**

In `webshell_monitor.sh`:

```bash
CREATE_ALERT_DEDUPE_SECS="${CREATE_ALERT_DEDUPE_SECS:-60}"

create_alert_stamp_path() {
    local file=$1
    local key
    key=$(printf '%s' "$file" | sha256sum 2>/dev/null | awk '{print $1}')
    if [ -z "$key" ]; then
        key=$(printf '%s' "$file" | md5sum 2>/dev/null | awk '{print $1}')
    fi
    [ -n "$key" ] || key=$(basename "$file")
    echo "${QUARANTINE_DIR}/.alert_dedupe/${key}"
}

should_send_create_alert() {
    local file=$1
    local stamp age
    stamp=$(create_alert_stamp_path "$file")
    mkdir -p "$(dirname "$stamp")" 2>/dev/null || true
    if [ -f "$stamp" ]; then
        age=$(( $(date +%s) - $(stat -c %Y "$stamp" 2>/dev/null || stat -f %m "$stamp") ))
        if [ "$age" -lt "$CREATE_ALERT_DEDUPE_SECS" ]; then
            return 1
        fi
    fi
    return 0
}

mark_create_alert_sent() {
    local stamp
    stamp=$(create_alert_stamp_path "$1")
    mkdir -p "$(dirname "$stamp")" 2>/dev/null || true
    date +%s >"$stamp" 2>/dev/null || true
}

handle_create() {
    local file=$1
    if is_noisy_path "$file"; then
        return 0
    fi
    if is_whitelisted "$file"; then
        return 0
    fi

    local is_dangerous=0
    if [[ "$file" =~ \.($DANGEROUS_EXTENSIONS)$ ]]; then
        is_dangerous=1
    fi

    log_message "WARNING" "监控目录新增文件: $file"

    if [ "$is_dangerous" -eq 1 ]; then
        sleep 1
        if [ "${STRICT_QUARANTINE:-false}" = "true" ] || check_file_content "$file"; then
            local reason="包含Webshell特征代码"
            if [ "${STRICT_QUARANTINE:-false}" = "true" ] && ! check_file_content "$file"; then
                reason="STRICT_QUARANTINE: 危险扩展名文件"
            fi
            # quarantine_file already send_alert — mark dedupe to suppress generic create alert
            if should_send_create_alert "$file"; then
                quarantine_file "$file" "$reason"
                mark_create_alert_sent "$file"
            else
                quarantine_file "$file" "$reason"  # still isolate; accept second alert OR refactor quarantine to optional alert
            fi
            return 0
        fi
        if should_send_create_alert "$file"; then
            local alert_msg
            alert_msg="监控目录新增可疑文件（需人工审查）
站点: $SITE_ID
文件: $file
时间: $(date '+%Y-%m-%d %H:%M:%S')
服务器: $(hostname)"
            send_alert "⚠️ 可疑文件创建 [$SITE_ID]" "$alert_msg"
            mark_create_alert_sent "$file"
        fi
        return 0
    fi

    if should_send_create_alert "$file"; then
        local alert_msg
        alert_msg="监控目录新增文件
站点: $SITE_ID
文件: $file
时间: $(date '+%Y-%m-%d %H:%M:%S')
服务器: $(hostname)"
        send_alert "⚠️ 监控目录新增文件 [$SITE_ID]" "$alert_msg"
        mark_create_alert_sent "$file"
    fi
}
```

**Important implementation detail:** Prefer refactoring `quarantine_file` to always quarantine, and only call `send_alert` when `should_send_create_alert` is true — so dedupe does not skip isolation. Exact shape: move `send_alert` out of `quarantine_file` into callers, OR add optional second arg `skip_alert`. Prefer `quarantine_file "$file" "$reason"` keeps alert, and for dedupe path still quarantine but skip alert via `quarantine_file "$file" "$reason" "no_alert"`.

Add `--self-test-create-alert` that mocks `send_alert` / uses temp dirs and exits 0/1 for the test script.

- [ ] **Step 4: Run test — expect pass**

```bash
bash scripts/tests/test_alert_on_create.sh
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add scripts/webshell_monitor.sh scripts/tests/test_alert_on_create.sh
git commit -m "feat: alert on any new file under monitored dirs"
```

---

### Task 2: Events ingest + docs

**Files:**
- Modify: `backend/app/api/events.py`
- Modify: `scripts/README.md`
- Modify: `docs/DEPLOY.md` (acceptance bullet)
- Test: `backend/tests/test_api.py` (optional small unit if ingest is hard to hit; else rely on string branch coverage via a tiny pure helper — keep YAGNI: only change elif chain)

- [ ] **Step 1: Classify create logs**

In `events.py` ingest:

```python
elif "监控目录新增文件" in msg:
    event_type = "file_create"
elif "可疑文件" in msg:
    event_type = "suspicious_create"
```

Order matters: check `监控目录新增文件` before or with 可疑 — messages like `监控目录新增可疑文件` should map to `suspicious_create` if both match; prefer:

```python
elif "可疑文件" in msg:
    event_type = "suspicious_create"
elif "监控目录新增文件" in msg:
    event_type = "file_create"
```

- [ ] **Step 2: Docs**

`scripts/README.md`: note non-whitelist creates alert; whitelist silent; 60s dedupe.

`docs/DEPLOY.md` acceptance: create a `.txt` outside whitelist → alert/log.

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/events.py scripts/README.md docs/DEPLOY.md
git commit -m "feat: classify file_create events and document create alerts"
```

---

## Spec coverage

| Spec item | Task |
|-----------|------|
| Alert any non-whitelist create | 1 |
| Whitelist silent | 1 |
| Dangerous path preserved, one alert | 1 |
| 60s dedupe | 1 |
| Events `file_create` | 2 |
| Docs | 2 |
| No GUI toggle | respected |
