# Anti-Webshell

单机多站点 Webshell 实时防护：Bash + inotify 引擎，Flask 控制面，Vue3 图形界面。

## 架构

```
frontend (Vue3)  →  backend (Flask/JWT/MySQL|SQLite)  →  Executor
                                                         ↓
                              /etc/webshell-monitor/sites/<id>.conf
                                                         ↓
                              systemd webshell-monitor@<id> → webshell_monitor.sh
```

## 目录

| 路径 | 说明 |
|------|------|
| [`scripts/`](scripts/) | 监控引擎、Profile、规则、安装与站点管理 |
| [`backend/`](backend/) | Flask API / 配置同步 / Executor |
| [`frontend/`](frontend/) | Vue3 + Element Plus 管理界面 |

## 快速体验（开发）

```bash
# 1) 引擎（需 Linux + root；本机 macOS 可跳过）
cd scripts && sudo ./manage_sites.sh install
sudo ./manage_sites.sh add-site blog /www/wwwroot/blog thinkphp

# 2) API
cd ../backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
WEBSHELL_DRY_RUN=1 python run.py

# 3) 前端
cd ../frontend && pnpm install && pnpm dev
```

默认登录：`admin` / `admin123`。

## 生产部署

完整步骤（引擎 + MySQL + Gunicorn + Nginx）：见 **[docs/DEPLOY.md](docs/DEPLOY.md)**。

## 文档

| 文档 | 说明 |
|------|------|
| [docs/DEPLOY.md](docs/DEPLOY.md) | **生产服务器部署** |
| [scripts/README.md](scripts/README.md) | 引擎安装、多站运维 |
| [scripts/MIGRATION.md](scripts/MIGRATION.md) | 旧版单站 → 多站迁移 |
| [backend/README.md](backend/README.md) | API 与开发运行 |

框架 Profile：`thinkphp` / `laravel` / `wordpress` / `plain_php`。多机扩展点见 backend README（`Executor` + `node_id`）。
