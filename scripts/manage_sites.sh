#!/bin/bash
#######################################
# Multi-site management for webshell monitor
#
# Usage:
#   manage_sites.sh install
#   manage_sites.sh sync-audit
#   manage_sites.sh add-site <site_id> <project_dir> [profile]
#   manage_sites.sh remove-site <site_id>
#   manage_sites.sh list-sites
#   manage_sites.sh start|stop|restart|status <site_id>
#   manage_sites.sh migrate-legacy <site_id> <project_dir> [profile]
#   manage_sites.sh ensure-self [--no-lock]
#   manage_sites.sh lock-self | unlock-self | disable-self [--purge]
#######################################

set -euo pipefail

SCRIPT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/webshell_monitor"
CONFIG_ROOT="/etc/webshell-monitor/sites"
LOG_ROOT="/var/log/webshell"
SERVICE_TEMPLATE="webshell-monitor@.service"
# Copied for later add-site. cmd_install does not execute this script.
RENDER_SITE_CONFIG="$SCRIPT_HOME/render_site_config.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_msg() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }

require_root() {
    if [ "${EUID:-$(id -u)}" -ne 0 ]; then
        print_error "请使用 root 权限运行"
        exit 1
    fi
}

prepare_investigate_conf() {
    local dest="$1"
    local probe_root="${2:-/}"
    if [ -f "$dest" ]; then
        return 0
    fi
    local access=""
    local nginx="${probe_root%/}/var/log/nginx/access.log"
    local apache="${probe_root%/}/var/log/apache2/access.log"
    if [ -f "$nginx" ]; then
        access="$nginx"
    elif [ -f "$apache" ]; then
        access="$apache"
    fi
    mkdir -p "$(dirname "$dest")"
    cat > "$dest" << EOF
DEFAULT_ACCESS_LOG="$access"
AUTH_LOG="/var/log/auth.log"
AUDIT_LOG="/var/log/audit/audit.log"
EOF
}

cmd_install() {
    require_root
    print_msg "安装 webshell monitor 引擎与 systemd 模板..."

    mkdir -p "$INSTALL_DIR" "$CONFIG_ROOT" "$LOG_ROOT" \
        /var/quarantine /var/webshell_backup

    cp "$SCRIPT_HOME/webshell_monitor.sh" "$INSTALL_DIR/"
    cp "$RENDER_SITE_CONFIG" "$INSTALL_DIR/"
    cp "$SCRIPT_HOME/render_controlplane.sh" "$INSTALL_DIR/"
    cp "$SCRIPT_HOME/manage_sites.sh" "$INSTALL_DIR/"
    cp "$SCRIPT_HOME/sync_audit.sh" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR"/*.sh
    chmod +x "$INSTALL_DIR/sync_audit.sh"

    mkdir -p "$INSTALL_DIR/profiles" "$INSTALL_DIR/rules"
    cp -a "$SCRIPT_HOME/profiles/." "$INSTALL_DIR/profiles/"
    cp -a "$SCRIPT_HOME/rules/." "$INSTALL_DIR/rules/"

    if [ -f "$SCRIPT_HOME/systemd/$SERVICE_TEMPLATE" ]; then
        cp "$SCRIPT_HOME/systemd/$SERVICE_TEMPLATE" "/etc/systemd/system/$SERVICE_TEMPLATE"
    else
        cat > "/etc/systemd/system/$SERVICE_TEMPLATE" << EOF
[Unit]
Description=Webshell Real-time Monitor for site %i
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/webshell_monitor.sh --site %i
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    fi

    # logrotate for per-site logs
    cat > /etc/logrotate.d/webshell-monitor << EOF
$LOG_ROOT/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 0644 root root
    sharedscripts
    postrotate
        systemctl reload 'webshell-monitor@*' > /dev/null 2>&1 || true
    endscript
}
EOF

    if ! grep -q "fs.inotify.max_user_watches" /etc/sysctl.conf 2>/dev/null; then
        echo "fs.inotify.max_user_watches=524288" >> /etc/sysctl.conf
        sysctl -p >/dev/null 2>&1 || true
    fi

    if ! command -v inotifywait &>/dev/null; then
        if command -v apt-get &>/dev/null; then
            apt-get update -qq
            apt-get install -y inotify-tools
        else
            print_warning "请手动安装 inotify-tools"
        fi
    fi

    # Disable legacy single-unit if present
    if systemctl list-unit-files | grep -q '^webshell-monitor.service'; then
        print_warning "检测到旧版 webshell-monitor.service，将禁用（请用 add-site 迁移）"
        systemctl stop webshell-monitor.service 2>/dev/null || true
        systemctl disable webshell-monitor.service 2>/dev/null || true
    fi

    systemctl daemon-reload

    prepare_investigate_conf /etc/webshell-monitor/investigate.conf /
    if command -v apt-get >/dev/null 2>&1 && ! command -v auditctl >/dev/null 2>&1; then
        apt-get update -qq || true
        apt-get install -y auditd || print_warning "auditd 未装上，时间线将缺少进程记录"
    fi
    CONFIG_ROOT="$CONFIG_ROOT" "$INSTALL_DIR/sync_audit.sh" || print_warning "审计规则未加载"
    systemctl restart 'webshell-monitor@*' || true

    print_msg "安装完成。下一步: $0 add-site <site_id> <project_dir> [profile]"
    echo ""
    echo "建议: $0 ensure-self"
    echo "发布流程: unlock-self → 更新 → lock-self"
}

cmd_add_site() {
    require_root
    local site_id="${1:-}"
    shift || true
    local project_dir="${1:-}"
    shift || true
    local profile="thinkphp"
    if [ $# -gt 0 ] && [[ "${1:-}" != --* ]]; then
        profile="$1"
        shift
    fi
    local extra_args=("$@")

    if [ -z "$site_id" ] || [ -z "$project_dir" ]; then
        print_error "用法: $0 add-site <site_id> <project_dir> [profile] [--alert-email ...]"
        exit 1
    fi

    if [ ! -x "$INSTALL_DIR/render_site_config.sh" ]; then
        print_error "未安装引擎，请先: $0 install"
        exit 1
    fi

    PROFILES_DIR="$INSTALL_DIR/profiles" \
        "$INSTALL_DIR/render_site_config.sh" "$site_id" "$project_dir" "$profile" \
        --output-dir "$CONFIG_ROOT" "${extra_args[@]+"${extra_args[@]}"}"

    systemctl enable "webshell-monitor@${site_id}.service"
    systemctl restart "webshell-monitor@${site_id}.service"
    print_msg "站点已添加并启动: $site_id (profile=$profile)"
    systemctl --no-pager status "webshell-monitor@${site_id}.service" || true
    CONFIG_ROOT="$CONFIG_ROOT" "$INSTALL_DIR/sync_audit.sh" || print_warning "审计规则未加载"
}

cmd_remove_site() {
    require_root
    local site_id="${1:-}"
    if [ -z "$site_id" ]; then
        print_error "用法: $0 remove-site <site_id>"
        exit 1
    fi

    if [ "$site_id" = "controlplane" ]; then
        print_error "controlplane 只能用: $0 disable-self"
        exit 1
    fi

    systemctl stop "webshell-monitor@${site_id}.service" 2>/dev/null || true
    systemctl disable "webshell-monitor@${site_id}.service" 2>/dev/null || true

    rm -f "$CONFIG_ROOT/${site_id}.conf" \
          "$CONFIG_ROOT/${site_id}.critical" \
          "$CONFIG_ROOT/${site_id}.whitelist" \
          "$CONFIG_ROOT/${site_id}.watch"

    print_msg "已移除站点服务与配置: $site_id"
    print_warning "隔离区/备份未删除: /var/quarantine/$site_id /var/webshell_backup/$site_id"
    CONFIG_ROOT="$CONFIG_ROOT" "$INSTALL_DIR/sync_audit.sh" || print_warning "审计规则未加载"
}

cmd_sync_audit() {
    require_root
    CONFIG_ROOT="$CONFIG_ROOT" "$INSTALL_DIR/sync_audit.sh" || {
        print_warning "审计规则未加载"
        exit 1
    }
}

cmd_ensure_self() {
    require_root
    local no_lock=0
    [[ "${1:-}" == "--no-lock" ]] && no_lock=1

    if [ ! -x "$INSTALL_DIR/webshell_monitor.sh" ]; then
        print_error "未安装引擎，请先: $0 install"
        exit 1
    fi

    if [ -x "$SCRIPT_HOME/render_controlplane.sh" ]; then
        cp "$SCRIPT_HOME/render_controlplane.sh" "$INSTALL_DIR/" 2>/dev/null || true
        chmod +x "$INSTALL_DIR/render_controlplane.sh"
    fi

    if [ ! -x "$INSTALL_DIR/render_controlplane.sh" ]; then
        print_error "缺少 render_controlplane.sh，请先: $0 install"
        exit 1
    fi

    "$INSTALL_DIR/render_controlplane.sh"
    systemctl enable "webshell-monitor@controlplane.service"
    systemctl restart "webshell-monitor@controlplane.service"
    if [ "$no_lock" -eq 0 ]; then
        cmd_lock_self
    fi
    print_warning "请确认 Nginx 对 UI 根目录未启用 PHP（见 docs/DEPLOY.md）"
    print_msg "控制面自保已启用: controlplane"
}

cmd_lock_self() {
    require_root
    if [ ! -x "$INSTALL_DIR/webshell_monitor.sh" ]; then
        print_error "未安装引擎，请先: $0 install"
        exit 1
    fi
    "$INSTALL_DIR/webshell_monitor.sh" --site controlplane --lock
    print_msg "已锁定 controlplane 自保路径"
}

cmd_unlock_self() {
    require_root
    if [ ! -x "$INSTALL_DIR/webshell_monitor.sh" ]; then
        print_error "未安装引擎，请先: $0 install"
        exit 1
    fi
    "$INSTALL_DIR/webshell_monitor.sh" --site controlplane --unlock
    print_msg "已解锁 controlplane 自保路径"
}

cmd_disable_self() {
    require_root
    local purge=0
    [[ "${1:-}" == "--purge" ]] && purge=1

    read -r -p "确认禁用控制面自保 controlplane? 输入 YES: " ans
    [[ "$ans" == "YES" ]] || { print_error "已取消"; exit 1; }

    cmd_unlock_self || true
    systemctl stop "webshell-monitor@controlplane.service" 2>/dev/null || true
    systemctl disable "webshell-monitor@controlplane.service" 2>/dev/null || true

    if [ "$purge" -eq 1 ]; then
        rm -f "$CONFIG_ROOT/controlplane.conf" \
              "$CONFIG_ROOT/controlplane.critical" \
              "$CONFIG_ROOT/controlplane.whitelist" \
              "$CONFIG_ROOT/controlplane.watch"
        print_msg "已停用并清除 controlplane 配置"
    else
        print_msg "已停用 controlplane 服务（配置保留，可用 ensure-self 恢复）"
    fi
}

cmd_list_sites() {
    echo "已配置站点 ($CONFIG_ROOT):"
    echo "----------------------------------------"
    if [ ! -d "$CONFIG_ROOT" ] || [ -z "$(ls -A "$CONFIG_ROOT"/*.conf 2>/dev/null)" ]; then
        echo "(无)"
        return
    fi
    local conf site status
    for conf in "$CONFIG_ROOT"/*.conf; do
        site="$(basename "$conf" .conf)"
        status="$(systemctl is-active "webshell-monitor@${site}.service" 2>/dev/null || echo unknown)"
        # shellcheck source=/dev/null
        source "$conf"
        echo "  $site  status=$status  profile=${PROFILE:-?}  dir=${PROJECT_DIR:-?}"
    done
}

cmd_svc() {
    require_root
    local action="$1"
    local site_id="${2:-}"
    if [ -z "$site_id" ]; then
        print_error "用法: $0 $action <site_id>"
        exit 1
    fi
    systemctl "$action" "webshell-monitor@${site_id}.service"
}

cmd_migrate_legacy() {
    require_root
    local site_id="${1:-}"
    local project_dir="${2:-}"
    local profile="${3:-thinkphp}"

    if [ -z "$site_id" ] || [ -z "$project_dir" ]; then
        print_error "用法: $0 migrate-legacy <site_id> <project_dir> [profile]"
        exit 1
    fi

    print_msg "迁移旧版单站部署 -> 多站 site=$site_id ..."
    cmd_install
    cmd_add_site "$site_id" "$project_dir" "$profile"
    print_msg "迁移完成。旧备份若在 /var/webshell_backup/*.backup，请按相对路径重命名迁移到 /var/webshell_backup/$site_id/"
}

usage() {
    cat <<EOF
用法:
  $0 install
  $0 sync-audit
  $0 add-site <site_id> <project_dir> [profile] [render options...]
  $0 remove-site <site_id>
  $0 list-sites
  $0 start|stop|restart|status <site_id>
  $0 migrate-legacy <site_id> <project_dir> [profile]
  $0 ensure-self [--no-lock]
  $0 lock-self
  $0 unlock-self
  $0 disable-self [--purge]

  add-site 的 --alert-email / --wechat-webhook / --dingtalk-webhook 仅用于 render；
  全局告警请在 GUI「配置 → 告警配置」维护。

  sync-audit 按站点配置重写审计规则并加载。手工规则只写
  /etc/webshell-monitor/audit.local.rules，不要手改
  /etc/audit/rules.d/webshell.rules，规则中不要写 -e 2。

示例:
  $0 install
  $0 sync-audit
  $0 add-site blog /www/wwwroot/blog thinkphp --alert-email admin@example.com
  $0 add-site shop /www/wwwroot/shop laravel
  $0 list-sites
  $0 ensure-self
  $0 unlock-self
  $0 disable-self
EOF
}

main() {
    local cmd="${1:-}"
    shift || true
    case "$cmd" in
        install) cmd_install ;;
        sync-audit) cmd_sync_audit ;;
        add-site) cmd_add_site "$@" ;;
        remove-site) cmd_remove_site "$@" ;;
        list-sites) cmd_list_sites ;;
        start|stop|restart|status) cmd_svc "$cmd" "$@" ;;
        migrate-legacy) cmd_migrate_legacy "$@" ;;
        ensure-self) cmd_ensure_self "$@" ;;
        lock-self) cmd_lock_self ;;
        unlock-self) cmd_unlock_self ;;
        disable-self) cmd_disable_self "$@" ;;
        -h|--help|help|"") usage ;;
        *) print_error "未知命令: $cmd"; usage; exit 1 ;;
    esac
}

main "$@"
