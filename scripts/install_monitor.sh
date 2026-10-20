#!/bin/bash

#######################################
# Webshell监控系统一键部署脚本（多站点）
# 适用于 Ubuntu 18.04/20.04/22.04
#
# 本脚本委托 manage_sites.sh 完成引擎安装，
# 并交互式添加第一个站点（可选）。
#######################################

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_msg() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

check_root() {
    if [ "${EUID:-$(id -u)}" -ne 0 ]; then
        print_error "请使用 root 权限运行此脚本"
        exit 1
    fi
}

check_os() {
    if [ ! -f /etc/os-release ]; then
        print_error "无法识别操作系统"
        exit 1
    fi
    # shellcheck source=/dev/null
    . /etc/os-release
    if [ "${ID:-}" != "ubuntu" ]; then
        print_warning "此脚本主要针对 Ubuntu，当前系统: ${ID:-unknown}"
        read -r -p "是否继续？(y/n) " -n 1 reply
        echo
        if [[ ! "$reply" =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

install_optional_deps() {
    if ! command -v apt-get &>/dev/null; then
        return
    fi
    apt-get update -qq

    if ! command -v auditctl &>/dev/null; then
        read -r -p "是否安装 auditd 进行系统审计？(y/n) " -n 1 reply
        echo
        if [[ "$reply" =~ ^[Yy]$ ]]; then
            apt-get install -y auditd audispd-plugins
            systemctl enable auditd
            systemctl start auditd
            print_msg "✓ auditd 已安装并启动"
        fi
    fi

    if ! command -v aide &>/dev/null; then
        read -r -p "是否安装 AIDE 进行文件完整性检查？(y/n) " -n 1 reply
        echo
        if [[ "$reply" =~ ^[Yy]$ ]]; then
            apt-get install -y aide
            print_msg "✓ AIDE 已安装"
        fi
    fi

    if ! command -v mail &>/dev/null; then
        read -r -p "是否安装 mailutils 用于邮件告警？(y/n) " -n 1 reply
        echo
        if [[ "$reply" =~ ^[Yy]$ ]]; then
            apt-get install -y mailutils
            print_msg "✓ mailutils 已安装"
        fi
    fi
}

main() {
    clear
    cat <<'EOF'
╔═══════════════════════════════════════╗
║  Webshell监控系统 - 多站点部署        ║
║  Version 2.0                         ║
╚═══════════════════════════════════════╝
EOF
    echo

    check_root
    check_os

    print_msg "开始安装引擎..."
    bash "$CURRENT_DIR/manage_sites.sh" install
    install_optional_deps

    echo
    read -r -p "是否现在添加第一个站点？(y/n) " -n 1 reply
    echo
    if [[ "$reply" =~ ^[Yy]$ ]]; then
        read -r -p "站点 ID [例如 blog]: " site_id
        read -r -p "项目目录绝对路径: " project_dir
        read -r -p "框架 profile [默认 thinkphp]: " profile
        profile="${profile:-thinkphp}"
        read -r -p "告警邮箱（可留空）: " alert_email

        args=("$site_id" "$project_dir" "$profile")
        if [ -n "$alert_email" ]; then
            args+=(--alert-email "$alert_email")
        fi
        bash "$CURRENT_DIR/manage_sites.sh" add-site "${args[@]}"
    fi

    cat <<EOF

========================================
安装完成！
========================================

引擎目录: /opt/webshell_monitor
配置目录: /etc/webshell-monitor/sites
日志目录: /var/log/webshell/<site_id>.log

常用命令:
  # 添加站点
  sudo /opt/webshell_monitor/manage_sites.sh add-site <id> <dir> [profile]

  # 列出站点
  sudo /opt/webshell_monitor/manage_sites.sh list-sites

  # 启停
  sudo systemctl start webshell-monitor@<id>
  sudo systemctl stop webshell-monitor@<id>
  sudo systemctl status webshell-monitor@<id>

  # 报告 / 备份
  sudo /opt/webshell_monitor/webshell_monitor.sh --site <id> --report
  sudo /opt/webshell_monitor/webshell_monitor.sh --site <id> --backup

从旧版单站迁移:
  sudo /opt/webshell_monitor/manage_sites.sh migrate-legacy <id> <project_dir> thinkphp

图形界面请部署 backend/ + frontend/（见仓库 README）

========================================
EOF
}

main "$@"
