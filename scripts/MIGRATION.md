# 从单站旧版迁移到多站

## 变更摘要

| 旧版 | 新版 |
|------|------|
| 硬编码 `PROJECT_DIR` 于脚本内 | `/etc/webshell-monitor/sites/<id>.conf` |
| `webshell-monitor.service` | `webshell-monitor@<id>.service` |
| `/var/quarantine/` 混用 | `/var/quarantine/<id>/` |
| `/var/webshell_backup/<basename>.backup` | `/var/webshell_backup/<id>/<relpath扁平化>.backup` |

## 一键迁移

```bash
cd /path/to/anti-webshell/scripts
sudo ./manage_sites.sh migrate-legacy mysite /www/wwwroot/mysite thinkphp
```

可选告警：

```bash
sudo ./manage_sites.sh add-site mysite /www/wwwroot/mysite thinkphp \
  --alert-email admin@example.com
```

## 手动迁移步骤

1. `sudo ./manage_sites.sh install`
2. 停止旧服务：`sudo systemctl stop webshell-monitor`
3. `sudo ./manage_sites.sh add-site <id> <dir> thinkphp`
4. （可选）将旧备份按相对路径重命名到新目录，例如：
   - 旧：`/var/webshell_backup/index.php.backup`
   - 新：`/var/webshell_backup/<id>/public__index.php.backup`
5. 确认：`sudo systemctl status webshell-monitor@<id>`

## 添加第二个站点

```bash
sudo ./manage_sites.sh add-site shop /www/wwwroot/shop laravel
sudo ./manage_sites.sh list-sites
```
