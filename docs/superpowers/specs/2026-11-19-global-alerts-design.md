# Global alert settings + Web UI — Design

Date: 2026-11-19  
Status: approved for implementation planning

## 1. Problem

Alert channels (email, WeCom webhook, DingTalk webhook) are stored per site (`Site.alert_email` / `wechat_webhook` / `dingtalk_webhook` and `AlertConfig.site_pk`). The Config UI only toggles `enabled` and cannot edit webhook URLs. Operators want one global set of channels for all sites (including `controlplane`), editable in the Web UI.

## 2. Goals

1. Single global configuration for email / WeCom / DingTalk shared by every monitored site and controlplane.
2. Web UI: view and edit enable flags + webhook URL / alert email, then save.
3. On save (and on site sync / `ensure-self`), propagate the same values into each site’s engine conf so `webshell_monitor.sh` `send_alert` keeps working without engine changes.
4. Migrate existing per-site alert rows/fields into global once.

Non-goals: per-site overrides; changing alert message format; new channels (SMS, etc.).

## 3. Approach

**A — Global `AlertConfig` (`site_pk IS NULL`) + fan-out to all site confs.**

Engine continues to read `ALERT_EMAIL` / `WECHAT_WEBHOOK` / `DINGTALK_WEBHOOK` from each site conf. Control plane is the source of truth in DB; sync writes mirrors into conf files.

## 4. Data model

- Prefer three global rows in `alert_config`: `type ∈ {email, wechat, dingtalk}`, `site_pk=NULL`.
- Uniqueness: at most one global row per `type` (enforce in application code; optional unique partial index later).
- `Site.alert_email` / `wechat_webhook` / `dingtalk_webhook`: kept as **mirrors** updated whenever global alerts are applied (so existing `sync_site` / serializers keep working). Do not treat site fields as independently authoritative after this change.
- On first `GET` of global alerts: if no global rows exist, seed from the first non-empty site field values found (or empty disabled rows). Delete or ignore legacy `AlertConfig` rows with non-null `site_pk` after migration (or stop reading them).

## 5. API

- `GET /api/config/alert-config` — returns only global rows (seed if needed). Does **not** require a current site.
- `PUT /api/config/alert-config/<id>` — update `enabled`, `webhook_url`, `alert_email` for that global row; then call `apply_global_alerts()`.
- Optional convenience: `PUT /api/config/alert-config` with body `{ channels: [...] }` batch update — nice-to-have; per-id PUT is enough for phase 1 if UI saves row-by-row or a single “保存” that loops.

`apply_global_alerts()`:

1. Read global email/wechat/dingtalk (empty string if disabled).
2. For each `Site`: set mirror fields; `sync_site(site, restart=True)`.
3. If controlplane conf exists: rewrite alert vars (re-run `render_controlplane.sh` or patch conf) and `systemctl restart webshell-monitor@controlplane` when present.
4. Also update `/etc/webshell-monitor/controlplane.env` keys `CONTROLPLANE_ALERT_EMAIL` / `CONTROLPLANE_WECHAT_WEBHOOK` / `CONTROLPLANE_DINGTALK_WEBHOOK` when that file is used, so future `ensure-self` keeps the same values.

## 6. Web UI

- Config page tab「告警配置」:
  - Independent of selected site (still show a note: 「全局生效，作用于所有站点与控制面」).
  - Editable: switch + input for webhook URL (wechat/dingtalk) or email (email type).
  - Primary **保存** button (or save-on-change with debounce); call API then toast success/failure.
- Sites create/edit: remove or hide alert email field; optional static hint linking to 告警配置.
- Do not show per-site alert columns as editable.

## 7. CLI / docs

- `manage_sites.sh add-site --wechat-webhook` / `--alert-email` / `--dingtalk-webhook`: either write into global AlertConfig via a small helper, or print deprecation and ignore in favor of UI/API. Prefer: if flags passed, update global then fan-out (backward compatible).
- Update `docs/DEPLOY.md` and `scripts/README.md`: configure alerts in GUI; controlplane inherits global.

## 8. Testing

- API: seed global; PUT wechat webhook; assert site mirror fields and dry-run sync ok.
- UI: manual — set WeCom URL, enable, save, trigger create alert on a site, confirm robot receives message.
- Migration: site with existing webhook → first GET creates global with that URL.

## 9. Approval record

- Scope: pure global (user choice 1).
- Approach: A (global AlertConfig + conf fan-out).
- User confirmed 2026-11-19.
