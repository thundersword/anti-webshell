# Webshell 监控系统（多站点）

基于文件系统变化的 Webshell 实时检测，使用 inotify 实现毫秒级响应。支持单机多站点，每站独立 systemd 服务与配置。

## 核心特性

- 实时监控（inotify）
- Webshell / HTML 恶意特征匹配（规则文件外置）
- 自动隔离与关键文件自动恢复
- 多渠道告警（邮件、企业微信、钉钉）
- 多站点：`webshell-monitor@<site_id>`
- 框架 Profile：ThinkPHP / Laravel / WordPress / plain_php

## 快速开始

```bash
cd /path/to/anti-webshell/scripts
chmod +x *.sh
sudo ./install_monitor.sh
# 或非交互：
sudo ./manage_sites.sh install
sudo ./manage_sites.sh add-site blog /www/wwwroot/blog thinkphp
```

告警请在控制面 GUI **全局告警**（`/alerts`）中设置（对所有站点与 `controlplane` 生效）。`add-site` / `render_site_config.sh` 的 `--alert-email`、`--wechat-webhook`、`--dingtalk-webhook` 仍会传给渲染脚本，便于无 GUI 时首建站点 conf；**以 GUI 保存的全局告警为准**，保存后会 fan-out 并重渲染各站。

## 配置结构

```
/etc/webshell-monitor/sites/
  <site_id>.conf          # 站点主配置（可 source）
  <site_id>.critical      # 关键文件绝对路径列表
  <site_id>.whitelist     # 白名单目录列表
  <site_id>.watch         # 可选：多根目录监控列表（每行一个绝对路径）

/opt/webshell_monitor/
  webshell_monitor.sh
  render_site_config.sh
  manage_sites.sh
  sync_audit.sh
  profiles/*.conf
  rules/*.rules

/etc/webshell-monitor/investigate.conf
/etc/webshell-monitor/audit.local.rules

/var/log/webshell/<site_id>.log
/var/quarantine/<site_id>/
/var/webshell_backup/<site_id>/
```

手动渲染配置（告警字段可选；生产维护请用 GUI 全局告警）：

```bash
sudo ./render_site_config.sh blog /www/wwwroot/blog thinkphp
```

## 控制面自保（controlplane）

`webshell-monitor@controlplane` 监控 UI 静态根、引擎目录、站点配置目录与后端源码。配置由 `render_controlplane.sh` 生成，含 `controlplane.watch`（多根监控路径，一行一个目录）。

```bash
# 首次或升级后启用（默认会 lock-self）
sudo ./manage_sites.sh ensure-self
sudo ./manage_sites.sh ensure-self --no-lock   # 首次部署尚未 rsync 前端时可先不锁

# 发版：解锁 → 更新文件 → 再锁
sudo ./manage_sites.sh unlock-self
# ... git pull / build / rsync ...
sudo ./manage_sites.sh lock-self

# 关闭自保（须交互输入 YES；配置默认保留，可用 ensure-self 恢复）
sudo ./manage_sites.sh disable-self
sudo ./manage_sites.sh disable-self --purge   # 同时删除 controlplane.* 配置
```

GUI 不能删除 `controlplane` 站点；关闭自保只能用 `disable-self`。路径可通过 `WEBSHELL_UI_ROOT` 等环境变量或 `/etc/webshell-monitor/controlplane.env` 覆盖，见 [docs/DEPLOY.md](../docs/DEPLOY.md)。

## 常用命令

```bash
# 站点管理
sudo ./manage_sites.sh list-sites
sudo ./manage_sites.sh add-site shop /www/wwwroot/shop laravel
sudo ./manage_sites.sh remove-site shop
sudo ./manage_sites.sh sync-audit
sudo ./manage_sites.sh start blog
sudo ./manage_sites.sh stop blog

# systemd
sudo systemctl status webshell-monitor@blog
sudo journalctl -u webshell-monitor@blog -f

# 运维
sudo /opt/webshell_monitor/webshell_monitor.sh --site blog --report
sudo /opt/webshell_monitor/webshell_monitor.sh --site blog --backup
sudo /opt/webshell_monitor/webshell_monitor.sh --site blog --list-backups
sudo /opt/webshell_monitor/webshell_monitor.sh --site blog --restore /path/to/file

# 锁定关键文件（chattr +i，阻止 PHP/Web 改写）
sudo /opt/webshell_monitor/webshell_monitor.sh --site blog --lock
sudo /opt/webshell_monitor/webshell_monitor.sh --site blog --lock-status
# 发版前必须先解锁，发完再锁
sudo /opt/webshell_monitor/webshell_monitor.sh --site blog --unlock
```

## 关键文件锁定

默认对新站点启用 `LOCK_CRITICAL_FILES=true`：对 Profile 中的入口/配置等执行 `chattr +i`。Web 用户无法覆盖这些文件；即便拿到写权限也不会改成功。

注意：

- 需要 ext4/xfs 等支持 immutable 的本地文件系统，以及 root
- 正常发版会失败，直到 `--unlock`
- 删除站点时会自动解锁，避免文件永久只读

## Profile

| Profile | 说明 | 默认排除（降 I/O） |
|---------|------|-------------------|
| `thinkphp3` | ThinkPHP 3.x：`index.php`、`Application/`、`ThinkPHP/` | `.git`、`vendor`、`Application/Runtime`、`ThinkPHP/Library` |
| `thinkphp5` | ThinkPHP 5.0/5.1：`public/` + `application/` 或 `config/` | `.git`、`vendor`、`runtime`、`thinkphp/library` |
| `thinkphp6` | ThinkPHP 6.x/8.x：`app/` + `config/`，核心在 vendor | `.git`、`vendor`、`runtime` |
| `thinkphp` | 兼容名，等同 `thinkphp5` | 同上 |
| `laravel` | `artisan`、`.env`、`config/` | `.git`、`vendor`、`storage/framework` |
| `wordpress` | `wp-config.php` 等（仍监控 `uploads` 以防马） | `.git`、`vendor`、cache/upgrade |
| `plain_php` | 最小 `index.php` | `.git`、`vendor`、cache/tmp |

引擎对 `create` / `moved_to` / `close_write` 监听（不用每次 `write()` 的 `modify`），并跳过 `.git` 等热路径。

**新增文件告警**：非白名单目录下新建文件会记 WARNING 日志并走告警渠道；白名单内新增完全静默（不记日志、不告警）。同一文件 60 秒内外发告警去重。

GUI 建站时可点「探测框架」，或 CLI 使用对应 profile 名。

## 从旧版迁移

见 [MIGRATION.md](./MIGRATION.md)。

## 图形界面 / 生产部署

仓库 `backend/`（Flask）+ `frontend/`（Vue3）提供登录、站点管理、配置与启停。

生产完整步骤见 [docs/DEPLOY.md](../docs/DEPLOY.md)。

## 审计规则与升级

`install` 会重启已有的 `webshell-monitor@*`，并且不重写已有站点 conf、关键文件列表和白名单。`/etc/webshell-monitor/investigate.conf` 只在文件不存在时创建。若 `/var/log/nginx/access.log` 或 `/var/log/apache2/access.log` 存在，则写入 `DEFAULT_ACCESS_LOG`。不会自动选用 `/www/wwwlogs/*.log`。全局访问日志为空时，在控制面为该站点填写访问日志。

手工排除或临时停用某个目录时，只编辑 `/etc/webshell-monitor/audit.local.rules`。不要手改 `/etc/audit/rules.d/webshell.rules`。不要在规则里写 `-e 2`。本地文件里的 `never` 规则会排到生成的 `always` 规则之前，其余行仍接在末尾，这样目录排除才会生效。改完后执行：

```bash
sudo /opt/webshell_monitor/manage_sites.sh sync-audit
sudo auditctl -l | grep webshell
```

`add-site` 与 `remove-site` 结束时也会同步审计规则。

## 测试建议

1. 在非白名单目录创建含 `eval(` 的 `.php` → 应被隔离
2. 修改关键文件 → 应自动恢复（`AUTO_RESTORE=true`）
3. 两个站点同时运行，互不影响隔离目录
