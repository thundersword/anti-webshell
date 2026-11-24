# 隔离后的入侵时间线 — 设计

Date: 2026-11-24  
Status: approved for implementation planning

## 1. 问题

可疑文件被隔离时，引擎只知道站点、路径、命中原因、隔离位置和时间。告警里没有来源 IP、URL、写文件的进程和用户，也没有从更早请求到事后访问的时间线。控制面事件列表由站点日志事后解析得到，没有详情。

## 2. 目标

1. 隔离成功后，用审计日志、Web 访问日志和 `auth.log` 串一条入侵时间线。
2. 第一条告警保持现有「已隔离」文案，立即发出。分析结束后再发第二条短结论（来源 IP、URL、进程、用户、置信度）。
3. 完整时间线在站点事件详情中按时间展示。
4. 安装、建站、删站和控制面保存站点时自动维护 audit 规则。手工改法写在部署文档和站点概览里。
5. 已有站点通过 `manage_sites.sh install` 升级，不重建站点配置。

非目标：反混淆或执行木马；在告警里附带木马正文；为普通「新增文件」告警做日志关联；补查规则生效之前已经落盘的文件；单独的补跑队列；新的告警渠道。

## 3. 方案

**先隔离，后台出时间线，再发第二条告警。**

分析在被监控的机器上运行。控制面只读结果。隔离和第一条告警不等待分析。

## 4. 架构

只在 `quarantine_file` 成功时启动分析。白名单内的新增、未隔离的可疑文件、关键文件恢复，保持现有行为。

四块职责：

| 组件 | 职责 |
|------|------|
| `manage_sites.sh` | 安装分析器；`sync-audit` 重写审计规则并加载；升级时重启已有 `webshell-monitor@*` |
| `webshell_monitor.sh` | 隔离、写锚点、记带 `incident=` 的日志、发第一条告警，并用 `systemd-run --scope` 启动分析 |
| `webshell_investigate.py` | 读日志、写时间线 JSON、向标准输出打印第二条告警正文 |
| 控制面 | 解析 `incident_id`；`GET /api/events/<id>/timeline` 经 Executor 读 JSON；事件抽屉与站点表单、概览上的审计状态 |

分析器由 `webshell_investigate.sh` 包一层：运行 Python，再调用 `webshell_monitor.sh --site <id> --send-alert`。`--send-alert` 只做两件事：经现有 `send_alert` 发告警，并把正文第一行写入该站点日志（`入侵结论 incident=<id>` 或 `未能生成入侵时间线 incident=<id>`）。不进入监控循环。

`systemd-run --scope` 使分析器脱离 `webshell-monitor@<站点>` 的 cgroup，避免 `systemctl restart` 时被一起杀掉。超时 20 秒。监控重启不补跑尚未完成的分析。

目录：

```
/opt/webshell_monitor/webshell_investigate.py
/opt/webshell_monitor/webshell_investigate.sh
/etc/webshell-monitor/investigate.conf
/etc/webshell-monitor/audit.local.rules
/etc/audit/rules.d/webshell.rules          # 工具生成，会被覆盖
/var/lib/webshell/incidents/<site_id>/<incident_id>.anchor.json
/var/lib/webshell/incidents/<site_id>/<incident_id>.json
```

`incident_id` 格式：`YYYYmmddTHHMMSSZ_<sha256(原路径)前 8 位十六进制>`。只含字母数字。隔离日志写入时就生成并写上 `incident=<id>`。分析进程写出 `.json` 后删除对应的 `.anchor.json`。进程在写出 JSON 之前被杀死时，锚点文件留在磁盘上，不自动补跑。结论日志行由 `--send-alert` 写入，不由监控主进程追加。

## 5. 已有站点升级

`manage_sites.sh install` 对已存在的站点做这些事：

1. 安装 `webshell_investigate.py` 与 `webshell_investigate.sh`。
2. 若系统有 `apt-get` 且没有 `auditd`，执行 `apt-get install -y auditd`。安装失败只告警，不中断安装。
3. 不重写已有 `<站点>.conf`、关键文件列表和白名单。缺少 `ACCESS_LOG` / `SITE_HOSTS` 时视为空字符串。
4. `/etc/webshell-monitor/investigate.conf` 仅在不存在时创建。若 `/var/log/nginx/access.log` 或 `/var/log/apache2/access.log` 存在，写入 `DEFAULT_ACCESS_LOG`。不把 `/www/wwwlogs/*.log` 猜进全局配置。
5. 读取全部 `sites/*.conf`（含 `controlplane`）执行 `sync-audit`。
6. `systemctl restart 'webshell-monitor@*'`。重启期间该站没有 inotify 监控。

规则生效之前的隔离事件不补分析。

## 6. 配置

`/etc/webshell-monitor/investigate.conf`：

```bash
DEFAULT_ACCESS_LOG=""
AUTH_LOG="/var/log/auth.log"
AUDIT_LOG="/var/log/audit/audit.log"
```

站点 conf 增加两项，均可为空：

```bash
ACCESS_LOG=""
SITE_HOSTS=""
```

`SITE_HOSTS` 是空格分隔的域名。解析规则：

1. `ACCESS_LOG` 非空：只读这个文件，不再按 Host 过滤。
2. `ACCESS_LOG` 为空且 `DEFAULT_ACCESS_LOG` 非空：读全局文件。`SITE_HOSTS` 非空时只保留 Host 命中的行。
3. 两者皆空，或全局日志且 `SITE_HOSTS` 为空：HTTP 段记缺口「无法区分站点」。审计和登录记录照常写入。

数据库 `sites` 增加可空列 `access_log`、`site_hosts`。控制面保存站点时写入 conf，重启该站，并 `sync-audit`。全局 `investigate.conf` 只在服务器上改，控制面不提供编辑页。

访问日志路径必须是绝对路径，且打开后是普通文件（允许指向普通文件的符号链接）。分析器用该路径直接打开，不经过 shell。

## 7. 审计规则

`sync-audit` 在 `install`、建站、删站、控制面保存站点时执行，可重复运行：

1. 对每个站点收集监控根：存在 `<站点>.watch` 时用其中的绝对路径，否则用 `PROJECT_DIR`。
2. 排除目录来自该 Profile 的 `INOTIFY_EXCLUDE` 以及引擎 `is_noisy_path` 已跳过的路径段（`.git`、`vendor`、`runtime`、`Runtime`、`storage/framework`、`storage/logs`、`bootstrap/cache`）。**不使用白名单目录**，上传目录仍要审计。
3. 每个排除目录写在前面：`-a never,exit -F dir=<path> -k webshell_exclude`。
4. 每个监控根随后写两条：`arch=b64` 与 `arch=b32` 的 `-a always,exit -F dir=<path> -F perm=wa -k webshell_write`。
5. 把 `/etc/webshell-monitor/audit.local.rules` 原样接在末尾。该文件不存在则跳过。升级、建站、删站都不改它。
6. 写入 `/etc/audit/rules.d/webshell.rules`（文件头注释写明会被覆盖），执行 `augenrules --load`。

生成结果不得包含 `-e 2`。手工排除、临时停用某个目录，只编辑 `audit.local.rules`，然后：

```bash
sudo /opt/webshell_monitor/manage_sites.sh sync-audit
sudo auditctl -l | grep webshell
```

`auditd` 未运行或 `augenrules` 失败时，`sync-audit` 返回非零并打印原因，不回滚已经写好的规则文件。

## 8. 时间线

写文件时刻 **T**：取键为 `webshell_write`、路径等于被隔离文件、落在隔离时间前 120 秒内的最后一条成功写入。没有这样的记录时，T = 隔离时间减 1 秒，并标 `time_estimated=true`。

| 窗口 | 写入时间线的内容 |
|------|------------------|
| T 前 15 秒到 T 后 2 秒 | 落盘请求。方法为 POST 或 PUT 的优先；多条时优先 URL 路径含该文件所在目录（相对 `PROJECT_DIR`）的请求，否则取最晚一条 |
| 同一客户端 IP，T 往前 30 分钟 | URL 命中内置入口特征的请求 |
| T 到隔离时间后 2 分钟 | 请求 URL 路径含该文件名的访问 |
| T 前 30 分钟到 T 后 5 分钟 | `auth.log` 中的 SSH `Accepted`、`sudo`、`cron` |

入口特征列表（大小写不敏感的子串）：`invokefunction`、`call_user_func`、`think\app`、`/vendor/phpunit`、`eval-stdin.php`、`auto_prepend_file`。只用于分类，不外推 CVE 编号。

入口按下面顺序只标一个：

1. **cron**：审计里的 `exe` 或 `comm` 是 `cron` / `crond`
2. **ssh**：`exe` 是 `sshd`，或 `exe` 是 shell 且同一窗口内有与 `auid` 对应的 `Accepted`
3. **web_rce**：`exe` 是 `php`、`php-fpm`、`nginx`、`httpd` 或 `apache2`，且落盘请求或同一 IP 的事前请求命中入口特征
4. **web_upload**：上述 Web 进程，且 15 秒窗口内有落盘请求
5. **existing_shell**：`exe` 是 `bash`、`sh`、`dash`、`python`、`perl`，且不是 ssh 或 cron
6. **unknown**：其余

置信度：

- **high**：有审计命中，且 Web 类入口在 15 秒窗口内恰好一条 POST/PUT；或入口为 ssh 且窗口内恰好一条匹配的 `Accepted`；或入口为 cron 且审计恰好一条 cron/crond 写入
- **medium**：有审计命中但 15 秒窗口内的 HTTP 候选多于一条；或没有审计命中但恰好一条 POST/PUT
- **low**：其余情况，包括 `unknown`、只有登录日志、以及只有一条 GET 落盘请求这类够不上 high/medium 的结果

每个日志文件只从末尾向前读，最多 32MB。已压缩的轮转文件不读。因此缺了窗口内记录时，`gaps` 增加「日志窗口超出当前文件末尾 32MB」或「未读取已轮转日志」。

访问日志只解析 nginx/apache combined 格式（含 `$host` 或不含均可；不含 Host 字段时无法按 `SITE_HOSTS` 过滤，记缺口「访问日志无 Host 字段」）。解析失败则 HTTP 段记「访问日志格式无法解析」，审计和登录继续。

时间线 JSON 保存判断、时间、来源、IP、方法、URL、状态码、User-Agent、进程、uid、auid、文件大小、sha256、命中原因。sha256 与大小取隔离副本。不写入文件正文，不反混淆，不执行。

```json
{
  "incident_id": "20261124T034211Z_ab12cd34",
  "site_id": "blog",
  "status": "ok",
  "file_path": "/www/wwwroot/blog/public/x.php",
  "quarantine_path": "/var/quarantine/blog/...",
  "reason": "包含Webshell特征代码",
  "sha256": "...",
  "size": 120,
  "time_estimated": false,
  "entry": "web_upload",
  "confidence": "high",
  "summary": "php-fpm 写入，来源 1.2.3.4 POST /upload.php",
  "actor": {
    "ip": "1.2.3.4",
    "method": "POST",
    "url": "/upload.php",
    "status": 200,
    "ua": "...",
    "exe": "/usr/sbin/php-fpm",
    "uid": "www-data",
    "auid": "unset",
    "comm": "php-fpm"
  },
  "gaps": [],
  "events": [
    {"ts": "2026-11-24T03:40:01Z", "source": "access", "kind": "prior", "summary": "..."},
    {"ts": "2026-11-24T03:42:10Z", "source": "access", "kind": "write", "summary": "..."},
    {"ts": "2026-11-24T03:42:11Z", "source": "audit", "kind": "write", "summary": "..."},
    {"ts": "2026-11-24T03:42:12Z", "source": "monitor", "kind": "quarantine", "summary": "..."},
    {"ts": "2026-11-24T03:42:20Z", "source": "access", "kind": "after", "summary": "..."}
  ]
}
```

`status` 为 `ok` 或 `failed`。失败时 `entry` 为 `unknown`，`confidence` 为 `low`，`summary` 是失败原因。

## 9. 告警

第一条：保持今天的隔离告警，包括现有的 60 秒去重。本设计不改这条告警的发送条件。

第二条由 `--send-alert` 发送，不走那 60 秒去重。成功时正文为：

```text
入侵路径 [blog] 置信度: 高
入口: web_upload
来源 IP: 1.2.3.4
URL: POST /index.php?s=...
进程: /usr/sbin/php-fpm (www-data)
文件: /www/wwwroot/blog/public/x.php
缺口: 无审计记录
```

没有的字段写「未知」。没有缺口时不输出「缺口」行。URL 的查询串截到 200 字符；JSON 内保留完整 URL。

分析失败且 JSON 已写好，或 JSON 写失败：改发

```text
未能生成入侵时间线 [blog]
文件: /www/wwwroot/blog/public/x.php
原因: 分析超时
```

邮件或 Webhook 发送失败时，JSON 与站点日志中的「入侵结论」或失败行仍然保留。

## 10. 失败与降级

| 情况 | 行为 |
|------|------|
| Python 缺失、超时、异常 | 站点日志写原因。能写 JSON 则 `status=failed`。发「未能生成入侵时间线」。不编造 IP、URL、进程 |
| 某一类日志缺失或不可读 | 时间线仍写，`gaps` 记录缺失项，置信度按第 8 节下降 |
| 访问日志非 combined | HTTP 段缺口，审计与登录继续 |
| 单文件超过末尾 32MB | 停止继续向前读，记缺口 |
| 时间线目录不可写 | 只记日志并发失败告警 |
| 监控服务在分析途中重启 | 独立 scope 继续跑完，不补跑 |
| 路径不是绝对路径或不是普通文件 | 分析器拒绝打开该日志，按缺失处理 |

隔离本身不因分析失败而回滚。

## 11. 控制面

`monitor_events` 增加可空、可索引的 `incident_id`。日志解析见到 `incident=<id>` 时写入该列。隔离行和「入侵结论」行都会带上同一个 id。

`GET /api/events/<event_pk>/timeline` 需要登录。站点确定方式与现有事件列表相同（`X-Site-Id` 或 `?site_id=`）。事件的 `site_id` 必须与该站点一致，否则 404。响应：

| 条件 | 响应 |
|------|------|
| `<incident_id>.json` 存在 | 200，正文为该 JSON |
| 文件不存在，且该事件 `created_at` 距现在不足 30 秒 | 202，`{"status":"pending"}` |
| 文件不存在，且已超过 30 秒 | 404，`{"status":"missing","message":"日志中的失败原因；没有失败行时为未能生成"}` |
| 事件没有 `incident_id` | 404，`{"status":"missing","message":"该事件没有入侵分析"}` |

Executor 新增 `read_incident(site_id, incident_id)`。`site_id` 与 `incident_id` 都做白名单校验后再拼路径。`WEBSHELL_DRY_RUN=1` 时仍允许读取测试用的 incidents 目录。

站点事件表列不变。隔离行和入侵结论行点击打开抽屉：200 且 `status=ok` 时按 `events` 时间顺序展示，并显示入口、置信度、actor、缺口；200 且 `status=failed` 时只展示 `summary` 和 `gaps`；202 时显示「分析中」和刷新；404 时显示「未能生成」和 `message`。

站点表单增加「访问日志」「Host」。站点概览增加审计状态：`auditd` 是否 active、`auditctl -l` 是否包含本站监控根与 `webshell_write`，以及第 7 节的两条命令。不新增独立页面。

## 12. 测试

Python 夹具（不启动 auditd、不启动 Web 服务器）：

- 审计命中且 15 秒内恰好一条 POST → `web_upload` / high
- 同一 IP 事前请求含 `invokefunction`，再由 php-fpm 写文件 → `web_rce`
- `exe` 为 shell 且 auth 有对应 `Accepted` → `ssh`
- 无审计记录 → `gaps` 含审计缺失，置信度按第 8 节下降，仍输出对得上的访问记录
- 访问日志非 combined → HTTP 缺口
- 告警摘要中查询串最长 200 字符，JSON 中 URL 完整
- 非绝对路径的日志被拒绝

Shell：

- 两站点生成的规则中，`never` 在对应 `always` 之前；`audit.local.rules` 被原样拼接；结果不含 `-e 2`
- 白名单目录不会变成 `never` 规则
- 已有 conf 在 `install` 逻辑中不被重写（对渲染/同步函数做隔离测试，不要求在开发机上真的重启 systemd）

API：

- 日志行中的 `incident=<id>` 写入 `monitor_events.incident_id`
- JSON 存在时 timeline 返回 200
- 无文件且事件不足 30 秒返回 202
- 超过 30 秒返回 404

界面不做自动化测试。部署文档中的手工验收：

1. 对已有站点执行升级后，`auditctl -l` 能看到 `webshell_write`。
2. 在非白名单目录放入含 `eval(` 的 `.php`：先收到「已隔离」，再收到带 IP 与进程的第二条告警（日志可用时）。
3. 事件页点开该行能看到时间线；分析失败时看到原因而不是空抽屉。

## 13. 实现顺序

同一份设计，按这个顺序落地：

1. `sync-audit`、配置字段、升级路径与规则测试
2. 锚点、分析器、第二条告警与夹具测试
3. `incident_id`、timeline API、事件抽屉、概览上的审计状态
