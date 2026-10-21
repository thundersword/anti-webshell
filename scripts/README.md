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
sudo ./manage_sites.sh add-site blog /www/wwwroot/blog thinkphp \
  --alert-email admin@example.com
```

## 配置结构

```
/etc/webshell-monitor/sites/
  <site_id>.conf          # 站点主配置（可 source）
  <site_id>.critical      # 关键文件绝对路径列表
  <site_id>.whitelist     # 白名单目录列表

/opt/webshell_monitor/
  webshell_monitor.sh
  render_site_config.sh
  manage_sites.sh
  profiles/*.conf
  rules/*.rules

/var/log/webshell/<site_id>.log
/var/quarantine/<site_id>/
/var/webshell_backup/<site_id>/
```

手动渲染配置：

```bash
sudo ./render_site_config.sh blog /www/wwwroot/blog thinkphp \
  --alert-email admin@example.com
```

## 常用命令

```bash
# 站点管理
sudo ./manage_sites.sh list-sites
sudo ./manage_sites.sh add-site shop /www/wwwroot/shop laravel
sudo ./manage_sites.sh remove-site shop
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

GUI 建站时可点「探测框架」，或 CLI 使用对应 profile 名。

## 从旧版迁移

见 [MIGRATION.md](./MIGRATION.md)。

## 图形界面 / 生产部署

仓库 `backend/`（Flask）+ `frontend/`（Vue3）提供登录、站点管理、配置与启停。

生产完整步骤见 [docs/DEPLOY.md](../docs/DEPLOY.md)。

## 测试建议

1. 在非白名单目录创建含 `eval(` 的 `.php` → 应被隔离
2. 修改关键文件 → 应自动恢复（`AUTO_RESTORE=true`）
3. 两个站点同时运行，互不影响隔离目录
