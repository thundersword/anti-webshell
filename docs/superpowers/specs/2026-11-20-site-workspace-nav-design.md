# Site workspace navigation (remove global site switcher) — Design

Date: 2026-11-20  
Status: approved for implementation planning

## 1. Problem

The header site `<el-select>` forces a global “current site” for Config / Monitor / Events. Operators find this easy to mis-switch. Site context should come from explicitly entering a site from the site list.

## 2. Goals

1. Remove the global top-right site switcher.
2. Use **站点管理** as the entry: open a **站点工作台** for one site.
3. Keep API `site_id` / `X-Site-Id` behavior via store synced from the route `:siteId`.
4. Keep **全局告警** reachable without selecting a site.
5. Home keeps overview + control-plane self-guard (not tied to a business site).

Non-goals: redesign visual theme; change backend site APIs; multi-site compare views.

## 3. Routes (hash mode)

| Path | Purpose |
|------|---------|
| `/#/` | Home — stats, service for last/default site optional, self-guard, link to sites/alerts |
| `/#/sites` | Site list — create / delete / **进入** |
| `/#/sites/:siteId` | Site overview — status, start/stop/restart, lock actions |
| `/#/sites/:siteId/config` | Critical files, whitelist, protect strategy |
| `/#/sites/:siteId/monitor` | Live logs |
| `/#/sites/:siteId/events` | Events for that site |
| `/#/alerts` | Global alert channels (moved from Config alert tab) |

Legacy top-level `/config`, `/monitor`, `/events` redirect to `/sites` with a toast, or 404→sites.

## 4. UI structure

- **Header:** title + user menu only (no site select).
- **Sidebar top-level:** 首页、站点管理、全局告警.
- **站点工作台:** after entering a site, page shows breadcrumb `站点 / {name}` and **tabs** (概览 | 配置 | 监控 | 事件) navigating the child routes above. Prefer in-page tabs over expanding sidebar.
- **Sites table:** replace「切换」with「进入」→ `router.push(/sites/${siteId})`. On enter, `siteStore.setCurrentSiteId(siteId)`.

## 5. State sync

- On `sites/:siteId/*` route enter: validate site exists; set `currentSiteId`; if missing, redirect to `/sites`.
- API interceptor continues attaching `site_id` for non-global paths when `currentSiteId` is set.
- Leaving site workspace does not require clearing `currentSiteId` (harmless); clearing on navigate to `/` or `/alerts` is optional.

## 6. Page moves

- `Config.vue`: split — site-scoped tabs stay under workspace; alert tab content → `Alerts.vue` at `/alerts`.
- `Monitor.vue` / `Events.vue` / site service controls from `Home.vue`: usable under workspace; Home may keep a short “最近站点” or drop site service controls and only show self-guard + links.
- **Home phase-1:** keep self-guard + event stats (all or require no site); remove or demote “服务状态(当前站)” to workspace overview only.

## 7. Testing

- Manual: no header select; enter site; tabs work; refresh `/#/sites/blog/config` restores context; alerts page saves without site; global APIs still skip site_id.

## 8. Approval record

- Entry model: from site list (user choice 1).
- In-page tabs for workspace; sidebar: 首页 / 站点管理 / 全局告警.
- User confirmed 2026-11-20.
