# Site Workspace Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the global header site switcher; enter a per-site workspace from the site list with tabs for overview/config/monitor/events; expose global alerts at `/alerts`.

**Architecture:** Hash routes under `/sites/:siteId/*` with a `SiteWorkspace.vue` shell (breadcrumb + tabs). Sync `siteStore.currentSiteId` from route params. Extract alert UI to `Alerts.vue`. Slim Home to stats + self-guard.

**Tech Stack:** Vue 3, Vue Router 4 (hash), Pinia site store, Element Plus.

**Spec:** `docs/superpowers/specs/2026-11-20-site-workspace-nav-design.md`

## Global Constraints

- No header site `<el-select>`.
- Sidebar: 首页、站点管理、全局告警 only.
- Workspace entry: 站点列表「进入」→ `/sites/:siteId`.
- Global APIs remain without forced site_id (`/self-guard`, `/config/alert-config`, etc.).
- Keep hash history.

## File map

| File | Role |
|------|------|
| `frontend/src/router/index.js` | New routes + legacy redirects |
| `frontend/src/views/Dashboard.vue` | Remove select; new menu |
| `frontend/src/views/SiteWorkspace.vue` | Shell: validate site, tabs, `<router-view>` |
| `frontend/src/views/SiteOverview.vue` | Service status/start/stop (from Home) |
| `frontend/src/views/Alerts.vue` | Global alerts (from Config alert tab) |
| `frontend/src/views/Config.vue` | Site-only tabs; drop alert tab + “select site” alerts |
| `frontend/src/views/Sites.vue` | 「进入」instead of「切换」 |
| `frontend/src/views/Home.vue` | Remove site service card; keep stats + self-guard |
| `frontend/src/views/Monitor.vue` / `Events.vue` | Rely on route-synced store (minimal change) |

---

### Task 1: Router + Dashboard shell

**Files:**
- Modify: `frontend/src/router/index.js`
- Modify: `frontend/src/views/Dashboard.vue`
- Create: `frontend/src/views/SiteWorkspace.vue` (stub with `<router-view />` + tabs)

- [ ] **Step 1: Update routes**

```js
children: [
  { path: '', name: 'Home', component: () => import('../views/Home.vue') },
  { path: 'sites', name: 'Sites', component: () => import('../views/Sites.vue') },
  {
    path: 'sites/:siteId',
    component: () => import('../views/SiteWorkspace.vue'),
    props: true,
    children: [
      { path: '', name: 'SiteOverview', component: () => import('../views/SiteOverview.vue') },
      { path: 'config', name: 'SiteConfig', component: () => import('../views/Config.vue') },
      { path: 'monitor', name: 'SiteMonitor', component: () => import('../views/Monitor.vue') },
      { path: 'events', name: 'SiteEvents', component: () => import('../views/Events.vue') },
    ]
  },
  { path: 'alerts', name: 'Alerts', component: () => import('../views/Alerts.vue') },
  { path: 'config', redirect: '/sites' },
  { path: 'monitor', redirect: '/sites' },
  { path: 'events', redirect: '/sites' },
]
```

In `beforeEach`, if `to.params.siteId`, call `useSiteStore().setCurrentSiteId(to.params.siteId)` (import store outside setup carefully — use pinia after app mounted, or sync inside SiteWorkspace `onMounted`/`watch`).

Prefer syncing inside `SiteWorkspace.vue` watch on `route.params.siteId` to avoid pinia-before-app issues in router module.

- [ ] **Step 2: Dashboard**

Remove `el-select` and site-change handlers. Menu items:

- `/` 首页  
- `/sites` 站点管理  
- `/alerts` 全局告警  

Fix `activeMenu` for nested paths (e.g. active `/sites` when path starts with `/sites`).

- [ ] **Step 3: SiteWorkspace stub**

```vue
<template>
  <div>
    <el-page-header @back="$router.push('/sites')" :content="`站点 ${siteId}`" />
    <el-tabs :model-value="activeTab" @tab-change="onTab">
      <el-tab-pane label="概览" name="overview" />
      <el-tab-pane label="配置" name="config" />
      <el-tab-pane label="监控" name="monitor" />
      <el-tab-pane label="事件" name="events" />
    </el-tabs>
    <router-view />
  </div>
</template>
```

Map tab → route; on mount verify site via `api.get('/sites')` or store; unknown → redirect `/sites`.

- [ ] **Step 4: `pnpm build` + commit**

```bash
cd frontend && pnpm build
git add frontend/src/router/index.js frontend/src/views/Dashboard.vue frontend/src/views/SiteWorkspace.vue
git commit -m "feat: add site workspace routes and remove header site switcher"
```

---

### Task 2: Sites enter + SiteOverview + Home slim

**Files:**
- Modify: `frontend/src/views/Sites.vue`
- Create: `frontend/src/views/SiteOverview.vue`
- Modify: `frontend/src/views/Home.vue`

- [ ] **Step 1: Sites.vue**

Replace 切换 with 进入:

```js
const enterSite = (row) => {
  siteStore.setCurrentSiteId(row.site_id)
  router.push(`/sites/${row.site_id}`)
}
```

- [ ] **Step 2: Move service controls from Home → SiteOverview**

Copy start/stop/status UI from `Home.vue` service card into `SiteOverview.vue` using `route.params.siteId` / store.

Home: delete service card and related methods; keep stats + self-guard; optionally add buttons「站点管理」「全局告警」.

- [ ] **Step 3: Build + commit**

```bash
cd frontend && pnpm build
git commit -m "feat: enter site workspace from list; move service controls to overview"
```

---

### Task 3: Config under workspace + Alerts page

**Files:**
- Create: `frontend/src/views/Alerts.vue`
- Modify: `frontend/src/views/Config.vue`

- [ ] **Step 1: Extract Alerts.vue**

Move alert tab template + `loadAlertConfigs` / `saveAlerts` / `getAlertTypeName` from Config into Alerts.vue (standalone page, no site dependency).

- [ ] **Step 2: Config.vue**

Remove alert tab and global alert alert-box. Remove “请先选择站点” if workspace always has siteId; keep loading by `currentSiteId` from store (set by workspace). Drop standalone “当前站点” alert or show breadcrumb only in workspace.

- [ ] **Step 3: Build + commit**

```bash
cd frontend && pnpm build
git commit -m "feat: global alerts page; site config only inside workspace"
```

---

### Task 4: Smoke + docs note (optional)

- [ ] Manual: header has no select; enter site; tabs; refresh `/#/sites/x/config`; alerts save; self-guard still works.
- [ ] One line in `README.md` or `docs/DEPLOY.md` if GUI paths mentioned — skip if none.

```bash
git status  # commit docs only if changed
```

---

## Spec coverage

| Spec | Task |
|------|------|
| Remove header switcher | 1 |
| Sidebar 首页/站点/告警 | 1 |
| `/sites/:siteId` + tabs | 1–2 |
| 进入 from list | 2 |
| Overview service controls | 2 |
| Home slim | 2 |
| `/alerts` | 3 |
| Config site-only | 3 |
| Route sync store | 1 (SiteWorkspace) |
