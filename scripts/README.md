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
```

## Profile

| Profile | 说明 |
|---------|------|
| `thinkphp` | 默认：public/index.php 等 + runtime 白名单 |
| `laravel` | public/index.php、.env、config/* + storage 白名单 |
| `wordpress` | wp-config.php 等 + wp-content/cache |
| `plain_php` | 最小 index.php，可再自定义 |

## 从旧版迁移

见 [MIGRATION.md](./MIGRATION.md)。

## 图形界面 / 生产部署

仓库 `backend/`（Flask）+ `frontend/`（Vue3）提供登录、站点管理、配置与启停。

生产完整步骤见 [docs/DEPLOY.md](../docs/DEPLOY.md)。

## 测试建议

1. 在非白名单目录创建含 `eval(` 的 `.php` → 应被隔离
2. 修改关键文件 → 应自动恢复（`AUTO_RESTORE=true`）
3. 两个站点同时运行，互不影响隔离目录
