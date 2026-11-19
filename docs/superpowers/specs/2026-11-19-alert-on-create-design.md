# Alert on any new file under monitored directories — Design

Date: 2026-11-19  
Status: approved for implementation planning

## 1. Problem

Today `handle_create` only acts when the new path matches `DANGEROUS_EXTENSIONS` and is outside whitelist. Ordinary new files (e.g. `.txt`, renamed drops, unexpected assets) produce no external alert. Operators want visibility for **any** create/`moved_to` under watched trees, while keeping whitelist directories quiet for legitimate uploads.

## 2. Goals

1. Alert (log + email/Webhook via existing `send_alert`) for every new **file** under monitor roots that is not noisy and not under whitelist.
2. Preserve existing dangerous-extension quarantine / content-scan / `STRICT_QUARANTINE` behavior.
3. Avoid double-alerting the same create event (one outbound message when both “any create” and “suspicious review/quarantine” would fire).
4. Light dedupe: same absolute path should not re-send external alerts within 60 seconds (log lines may still record).

Non-goals: GUI toggle (phase later as `ALERT_ON_CREATE` if needed); alerting on directory creates; changing whitelist semantics beyond “silent on create”.

## 3. Approach

**A + light throttle** — extend `handle_create` in `scripts/webshell_monitor.sh` only (plus docs + small test). No new daemon.

## 4. Behavior

```
create / moved_to (file)
  → noisy path?          skip
  → whitelisted path?    skip (silent)
  → else:
       log WARNING "监控目录新增文件: <path>"
       if dangerous extension:
           existing: sleep, content check / STRICT → quarantine CRITICAL alert
                      OR review WARNING alert
           (single outbound alert: prefer quarantine/review text; include that it was a create)
       else:
           send_alert WARNING "监控目录新增文件 [site]"
       apply 60s per-path external-alert dedupe
```

- Directory events (`*ISDIR*`) remain ignored as today.
- `INOTIFY_EXCLUDE` / `is_noisy_path` unchanged.
- Whitelist: **silent** (user choice 2) — no log requirement beyond optional DEBUG; phase 1 = no log, no alert.
- Business sites and `controlplane` share the same rule.

## 5. Implementation notes

- File: `scripts/webshell_monitor.sh` — `handle_create`.
- Dedupe: associative array or temp files under `$QUARANTINE_DIR/.alert_dedupe/` keyed by path hash + epoch bucket; keep implementation bash-3.2-safe if macOS is a concern for local smoke (production is Linux; `declare -A` OK on bash 4+ used with systemd). Prefer simple mtime file under `/tmp` or quarantine dir for portability: `touch` stamp path, skip external send if stamp age < 60s.
- Log ingest: events API already classifies 「可疑文件」; add recognition for 「监控目录新增文件」 → `event_type=file_create` in `backend/app/api/events.py` (small, optional but useful for GUI).
- Docs: one paragraph in `scripts/README.md` and a bullet in `docs/DEPLOY.md` acceptance section.

## 6. Testing

- Shell smoke: invoke `handle_create` logic via a temp site conf or unit-style function test if extractable; otherwise document manual:
  1. Create `.txt` outside whitelist → WARNING alert + log.
  2. Create under whitelist → no alert.
  3. Create `.php` with eval → quarantine path unchanged.
  4. Double-create same path within 60s → one external alert (second may only log).

## 7. Approval record

- Scope: any new file alert (choice 2).
- Whitelist: silent (choice 2).
- Approach: A + 60s per-path external dedupe; merge dangerous alerts into one outbound message.
- User confirmed design 2026-11-19.
