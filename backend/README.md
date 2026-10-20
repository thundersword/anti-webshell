# Webshell 防护控制面（Flask）

图形配置与多站点管控 API。检测引擎仍由 `scripts/webshell_monitor.sh` + systemd 模板单元执行。

## 快速启动（开发）

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 开发默认可使用 SQLite；生产建议 MySQL：
# DATABASE_URL=mysql+pymysql://user:pass@127.0.0.1:3306/webshell?charset=utf8mb4
export WEBSHELL_DRY_RUN=1   # 无 root/systemd 时使用
python run.py
```

默认账号：`admin` / `admin123`

## 生产部署

完整步骤（Gunicorn / Nginx / MySQL / 权限）：见 [docs/DEPLOY.md](../docs/DEPLOY.md)。

要点：

- `WEBSHELL_DRY_RUN=0`
- `WEBSHELL_INSTALL_DIR=/opt/webshell_monitor`
- API 需能写 `/etc/webshell-monitor/sites` 并调用 `systemctl`

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录 |
| GET/POST/PUT/DELETE | `/api/sites` | 站点 CRUD |
| GET | `/api/profiles` | 框架 Profile 列表 |
| GET/POST/DELETE | `/api/config/critical-files` | 关键文件（`X-Site-Id` / `?site_id=`） |
| GET/POST/DELETE | `/api/config/whitelist-dirs` | 白名单 |
| GET/PUT | `/api/config/alert-config` | 告警 |
| GET/POST | `/api/service/*` | 状态 / 启停 / 日志 |
| GET | `/api/events/` | 事件列表 |

## Executor / 多机预留

- 本机：`LocalSystemdExecutor`（写 conf + systemctl）
- 远程：`RemoteAgentExecutor`（占位，未实现）
- 工厂：`get_executor_for_node(node_id)`；站点字段 `node_id` 默认 `local`
- 约定：系统副作用只走 Executor，API 路由不直接 `subprocess`

后续若做 Agent：每台机器部署同一套引擎脚本，控制面经 HTTPS + 节点 Token 调用同等接口。

## 测试

```bash
WEBSHELL_DRY_RUN=1 pytest -q
```
