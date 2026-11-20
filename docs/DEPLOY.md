# 生产服务器部署指南

适用环境：Ubuntu 20.04 / 22.04，单机多站点。代码默认安装到 `/opt/anti-webshell`。

## 架构

| 组件 | 作用 | 生产形态 |
|------|------|----------|
| `scripts/` | inotify 监控引擎 | 装到 `/opt/webshell_monitor`，每站一个 `webshell-monitor@<id>` |
| `backend/` | Flask 控制面 | Gunicorn + systemd，监听 `127.0.0.1:5000` |
| `frontend/` | Vue 管理界面 | `pnpm build` 后由 Nginx 托管，`/api` 反代到后端 |

```
浏览器 → Nginx(静态 dist + /api) → Gunicorn(Flask)
                                      ↓
                         写 /etc/webshell-monitor/sites/<id>.conf
                                      ↓
                         systemctl webshell-monitor@<id> → webshell_monitor.sh
```

## 1. 准备系统依赖

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx \
  inotify-tools mysql-server

# Node 20+（仅构建前端需要；可用已有 Node）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm i -g pnpm
```

MySQL 若已有实例可跳过 `mysql-server`，改用外部库连接串。

## 2. 上传代码

```bash
sudo mkdir -p /opt/anti-webshell
sudo chown "$USER:$USER" /opt/anti-webshell
cd /opt
git clone <仓库地址> anti-webshell
# 或: scp -r ./anti-webshell user@server:/opt/
cd /opt/anti-webshell
```

## 3. 安装监控引擎（必须先做）

```bash
cd /opt/anti-webshell/scripts
chmod +x *.sh
sudo ./manage_sites.sh install
```

会完成：

- 脚本 / Profile / 规则 → `/opt/webshell_monitor`
- systemd 模板 → `webshell-monitor@.service`
- 配置目录 → `/etc/webshell-monitor/sites`
- 调高 `fs.inotify.max_user_watches` 等

控制面自保 `ensure-self` **不要在这里立刻执行**：引擎要求 UI 根目录（`WEBSHELL_UI_ROOT`，默认 `/www/wwwroot/anti-webshell`）已存在。请先完成第 6 节前端部署，再按「控制面自保」小节启用。

### 添加要防护的站点

```bash
sudo ./manage_sites.sh add-site blog /www/wwwroot/blog thinkphp

# 多站
sudo ./manage_sites.sh add-site shop /www/wwwroot/shop laravel

sudo ./manage_sites.sh list-sites
sudo systemctl status webshell-monitor@blog
```

Profile 可选：`thinkphp` / `laravel` / `wordpress` / `plain_php`。

### 从旧版单站迁移

```bash
sudo ./manage_sites.sh migrate-legacy <site_id> <项目目录> thinkphp
```

详见 [scripts/MIGRATION.md](../scripts/MIGRATION.md)。

### 控制面自保（controlplane）

前置：UI 根目录必须已存在（见第 6 节 `mkdir` / `rsync`）。否则 `PROJECT_DIR` 校验失败，`webshell-monitor@controlplane` 无法启动。

`ensure-self` 渲染并启动 `webshell-monitor@controlplane`，监控 UI 根目录、引擎目录、`/etc/webshell-monitor` 与后端源码。默认对关键路径 `chattr +i`（可用 `ensure-self --no-lock` 跳过首次锁定）。

```bash
# 若尚未部署前端，至少先建空目录：
sudo mkdir -p /www/wwwroot/anti-webshell
sudo chown root:root /www/wwwroot/anti-webshell

sudo /opt/webshell_monitor/manage_sites.sh ensure-self
sudo systemctl status webshell-monitor@controlplane
```

关闭自保仅能通过 CLI：`disable-self`（需输入 `YES` 确认）。GUI 不可删除 `controlplane` 站点。

## 4. 准备数据库

### MySQL（推荐）

```bash
sudo mysql -e "
CREATE DATABASE webshell DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'webshell'@'localhost' IDENTIFIED BY '请换成强密码';
GRANT ALL ON webshell.* TO 'webshell'@'localhost';
FLUSH PRIVILEGES;
"
```

### SQLite（仅小规模/临时）

```bash
DATABASE_URL=sqlite:////opt/anti-webshell/backend/webshell.db
```

## 5. 部署后端（Gunicorn + systemd）

```bash
cd /opt/anti-webshell/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env
```

### `.env` 生产示例

```bash
FLASK_ENV=production
SECRET_KEY=请用长随机串
JWT_SECRET_KEY=请用另一段长随机串
DATABASE_URL=mysql+pymysql://webshell:强密码@127.0.0.1:3306/webshell?charset=utf8mb4
WEBSHELL_INSTALL_DIR=/opt/webshell_monitor
WEBSHELL_CONFIG_ROOT=/etc/webshell-monitor/sites
WEBSHELL_DRY_RUN=0
DEFAULT_ADMIN_USER=admin
DEFAULT_ADMIN_PASSWORD=首次登录后立刻改掉
```

`WEBSHELL_DRY_RUN` 必须为 `0`，否则不会真正操作 systemd。

### 权限说明

控制面需要：

1. 写 `/etc/webshell-monitor/sites`
2. 执行 `systemctl` 启停 `webshell-monitor@*`
3. 调用 `/opt/webshell_monitor/render_site_config.sh`

当前 `LocalSystemdExecutor` **直接**调用上述命令（不自动加 `sudo`）。生产推荐二选一：

- **简单**：API 以 `root` 运行（下文单元示例）
- **收窄**：专用用户 + 自行改造 Executor 走 `sudo`（需改代码）

### systemd 单元

```bash
sudo tee /etc/systemd/system/webshell-api.service <<'EOF'
[Unit]
Description=Anti-Webshell Control API
After=network.target mysql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/anti-webshell/backend
EnvironmentFile=/opt/anti-webshell/backend/.env
ExecStart=/opt/anti-webshell/backend/.venv/bin/gunicorn \
  -b 127.0.0.1:5000 -w 2 --timeout 120 run:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now webshell-api
sudo systemctl status webshell-api
curl -s http://127.0.0.1:5000/api/health
# 期望: {"status":"ok"}
```

## 6. 构建并部署前端

默认 UI 静态根目录为 `WEBSHELL_UI_ROOT`（`/www/wwwroot/anti-webshell`），可通过环境变量或 `/etc/webshell-monitor/controlplane.env` 覆盖。

```bash
cd /opt/anti-webshell/frontend
pnpm install
pnpm build

sudo mkdir -p /www/wwwroot/anti-webshell
sudo chown root:root /www/wwwroot/anti-webshell
sudo rsync -a --delete dist/ /www/wwwroot/anti-webshell/

# UI 根已就绪后再启用控制面自保（或 ensure-self --no-lock）
sudo /opt/webshell_monitor/manage_sites.sh ensure-self
```

生产**不要**使用 `pnpm dev`。前端请求相对路径 `/api`，由 Nginx 反代。目录应归 `root:root` 所有，**不要**赋予 `www` / php-fpm 用户写权限。

前端使用 **Hash 路由**（例如站点工作区 `/#/sites/<siteId>/config`、全局告警 `/#/alerts`），刷新页面不依赖 Nginx 回退；若改用 History 模式，必须保证 `location /` 含 `try_files $uri $uri/ /index.html;`（宝塔勿写成回退 `index.php`）。

## 7. 配置 Nginx（仅静态，禁止 PHP）

管理界面须使用**独立** `server { }` 块，不要嵌在已启用 PHP 的业务站点下，也**不要** `include` 面板自带的 `enable-php-*.conf` 或任何 `fastcgi_pass` 片段。

完整示例见 [nginx-webshell-ui.example.conf](./nginx-webshell-ui.example.conf)。

```bash
sudo tee /etc/nginx/sites-available/webshell-ui <<'EOF'
server {
    listen 80;
    server_name your-monitor.example.com;   # 改成域名或服务器 IP

    root /www/wwwroot/anti-webshell;
    index index.html;

    # 禁止在此 vhost 执行脚本（即使误上传 .php 也不应被解析）
    location ~* \.(php|phtml|phar|php\d+|cgi|pl|py|jsp|asp|aspx)$ {
        return 403;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:5000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/webshell-ui /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

建议再配 HTTPS（certbot），并将管理站限制为内网 / VPN / IP 白名单。

## 8. 全局告警（GUI）

告警渠道在管理界面统一配置，不按站点单独维护：

1. 登录 GUI → 侧栏 **全局告警**（`/#/alerts`）
2. 按需启用邮件、企业微信、钉钉并填写渠道参数
3. 点击「保存告警配置」

保存后会对**所有业务站点**与 `controlplane` 生效：写入数据库全局 `AlertConfig`，并重新渲染各站 `/etc/webshell-monitor/sites/<id>.conf`。

仅 CLI 建站、尚未部署控制面时，可用 `add-site` / `render_site_config.sh` 的 `--alert-email` 等参数写入首版 conf；**上线后以 GUI 全局告警为准**，下次在 GUI 保存会覆盖各站中的告警字段。

## 9. 上线验收

1. **引擎**
   ```bash
   sudo systemctl status webshell-monitor@blog
   sudo journalctl -u webshell-monitor@blog -n 50
   sudo /opt/webshell_monitor/webshell_monitor.sh --site blog --report
   ```
2. **API**：`curl http://127.0.0.1:5000/api/health`  
   自保：`curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:5000/api/self-guard`  
   本机 200、经域名却 404 → 检查 Nginx `location /api/` 是否反代到 `127.0.0.1:5000`（宝塔勿让静态站点抢走 `/api`）。  
   自保接口（需登录 Token）：`curl -H "Authorization: Bearer <token>" http://127.0.0.1:5000/api/self-guard`  
   若本机 200、经域名却 404，检查 Nginx `location /api/` 是否反代到 `127.0.0.1:5000`（宝塔勿让静态站点抢走 `/api`）。
3. **GUI**：打开 `http://your-monitor.example.com`，用 `.env` 账号登录
4. **全局告警**：在 **全局告警** 页保存后，抽查某站 `.conf` 中邮件 / Webhook 与 GUI 一致
5. **站点同步**：GUI「站点管理」操作后，`sync_status` 应为 `synced`，对应 `webshell-monitor@*` 为 active
6. **功能抽测**
   - 非白名单目录创建含 `eval(` 的 `.php` → 进入 `/var/quarantine/<site_id>/`
   - 非白名单目录创建普通 `.txt`（无恶意特征）→ 引擎日志含「监控目录新增文件」，并触发告警
   - 修改关键文件 → 自动恢复（`AUTO_RESTORE=true`）

## 10. 日常运维

```bash
# 站点
sudo /opt/webshell_monitor/manage_sites.sh list-sites
sudo /opt/webshell_monitor/manage_sites.sh add-site <id> <dir> <profile>
sudo systemctl restart webshell-monitor@<id>

# 控制面
sudo systemctl restart webshell-api
sudo journalctl -u webshell-api -f

# 日志 / 隔离区
tail -f /var/log/webshell/<id>.log
ls -lh /var/quarantine/<id>/
ls -lh /var/webshell_backup/<id>/
```

更新代码后（控制面自保已启用时须先解锁，避免 `chattr +i` 阻塞写入）：

```bash
sudo /opt/webshell_monitor/manage_sites.sh unlock-self

cd /opt/anti-webshell && git pull

# 引擎
cd scripts && sudo ./manage_sites.sh install
sudo systemctl restart 'webshell-monitor@*'

# 后端
cd ../backend && source .venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart webshell-api

# 前端
cd ../frontend && pnpm install && pnpm build
sudo rsync -a --delete dist/ /www/wwwroot/anti-webshell/

sudo /opt/webshell_monitor/manage_sites.sh lock-self
sudo systemctl restart webshell-api webshell-monitor@controlplane
```

## 11. 安全清单

- [ ] 修改默认管理员密码；轮换 `SECRET_KEY` / `JWT_SECRET_KEY`
- [ ] 管理界面不裸奔公网（防火墙 / VPN / IP 限制）
- [ ] `WEBSHELL_DRY_RUN=0`
- [ ] 在 GUI **全局告警**（`/#/alerts`）中配置邮件 / 企业微信 / 钉钉
- [ ] 定期备份 MySQL（或 sqlite）与 `/var/webshell_backup/`
- [ ] 控制面已 `ensure-self`；Nginx 对 UI 根目录无 PHP / fastcgi
- [ ] 关键文件锁定（`chattr +i`）开启后，发版须先 `unlock-self` 再 `lock-self`

## 部署顺序小结

```text
上传代码
  → manage_sites.sh install + add-site   # 真正防护
  → MySQL + backend .env + gunicorn systemd
  → ensure-self（控制面自保）
  → frontend build → Nginx 仅静态 + /api 反代（禁止 PHP）
  → 登录 GUI → 全局告警（`/#/alerts`）→ 验收
```
