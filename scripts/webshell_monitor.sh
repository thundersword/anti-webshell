#!/bin/bash

#######################################
# Webshell 实时监控脚本
# 基于 inotify 的文件系统监控（多站点）
#
# 配置通过 --site <id> 加载:
#   /etc/webshell-monitor/sites/<id>.conf
#######################################

set -euo pipefail

CONFIG_ROOT="${WEBSHELL_CONFIG_ROOT:-/etc/webshell-monitor/sites}"
SITE_ID=""
SCRIPT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults (overridden by site conf)
PROJECT_DIR=""
LOG_FILE="/var/log/webshell/monitor.log"
ALERT_EMAIL=""
QUARANTINE_DIR="/var/quarantine"
BACKUP_DIR="/var/webshell_backup"
WECHAT_WEBHOOK=""
DINGTALK_WEBHOOK=""
AUTO_RESTORE="true"
DANGEROUS_EXTENSIONS="php|php3|php4|php5|phtml|pht|phps|sh|py|pl|cgi|asp|aspx|jsp|html|htm|shtml"
INOTIFY_EXCLUDE='(\.git|\.svn)'
CRITICAL_FILES_FILE=""
WHITELIST_DIRS_FILE=""
PHP_RULES_FILE="$SCRIPT_HOME/rules/php_webshell.rules"
HTML_RULES_FILE="$SCRIPT_HOME/rules/html_malware.rules"

CRITICAL_FILES=()
WHITELIST_DIRS=()

#######################################
# Config loading
#######################################
# Populate a global array named by $2 from lines in file $1
load_list_file_into() {
    local file=$1
    local arr_name=$2
    eval "$arr_name=()"
    if [ -n "$file" ] && [ -f "$file" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in
                ''|\#*) continue ;;
            esac
            eval "$arr_name+=(\"\$line\")"
        done < "$file"
    fi
}

load_site_config() {
    local site=$1
    local conf="$CONFIG_ROOT/${site}.conf"

    if [ ! -f "$conf" ]; then
        echo "错误: 站点配置不存在: $conf" >&2
        echo "请先运行: render_site_config.sh $site <project_dir> [profile]" >&2
        exit 1
    fi

    # shellcheck source=/dev/null
    source "$conf"
    SITE_ID="$site"

    load_list_file_into "$CRITICAL_FILES_FILE" CRITICAL_FILES
    load_list_file_into "$WHITELIST_DIRS_FILE" WHITELIST_DIRS

    if [ -z "$PROJECT_DIR" ] || [ ! -d "$PROJECT_DIR" ]; then
        echo "错误: PROJECT_DIR 无效: ${PROJECT_DIR:-<empty>}" >&2
        exit 1
    fi
}

# Flatten relative path for backup filename: public/index.php -> public__index.php
backup_key_for_file() {
    local file=$1
    local rel="${file#"$PROJECT_DIR"/}"
    if [ "$rel" = "$file" ]; then
        rel="$(basename "$file")"
    fi
    echo "${rel//\//__}"
}

load_rules_from_file_into() {
    local file=$1
    local arr_name=$2
    eval "$arr_name=()"
    if [ ! -f "$file" ]; then
        return
    fi
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|\#*) continue ;;
        esac
        eval "$arr_name+=(\"\$line\")"
    done < "$file"
}

#######################################
# Init
#######################################
init() {
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
    mkdir -p "$QUARANTINE_DIR" 2>/dev/null || true
    mkdir -p "$BACKUP_DIR" 2>/dev/null || true
    chmod 750 "$BACKUP_DIR" 2>/dev/null || true

    if ! command -v inotifywait &> /dev/null; then
        echo "错误: 未安装 inotify-tools"
        echo "请运行: sudo apt-get install inotify-tools"
        exit 1
    fi

    if [ ! -d "$PROJECT_DIR" ]; then
        echo "错误: 项目目录不存在: $PROJECT_DIR"
        exit 1
    fi

    create_initial_backup
}

#######################################
# Logging / alerts
#######################################
log_message() {
    local level=$1
    local message=$2
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local prefix="[$timestamp] [$level]"
    if [ -n "${SITE_ID:-}" ]; then
        prefix="[$timestamp] [$level] [site=$SITE_ID]"
    fi
    echo "$prefix $message" >> "$LOG_FILE"
    echo "$prefix $message"
}

send_email_alert() {
    local subject=$1
    local body=$2
    if [ -n "$ALERT_EMAIL" ] && command -v mail &> /dev/null; then
        echo "$body" | mail -s "$subject" "$ALERT_EMAIL" 2>/dev/null || true
    fi
}

send_wechat_alert() {
    local message=$1
    if [ -n "$WECHAT_WEBHOOK" ]; then
        curl -s "$WECHAT_WEBHOOK" \
            -H 'Content-Type: application/json' \
            -d "{
                \"msgtype\": \"text\",
                \"text\": {
                    \"content\": \"🚨 Webshell告警 [site=${SITE_ID}]\\n$message\"
                }
            }" > /dev/null 2>&1 || true
    fi
}

send_dingtalk_alert() {
    local message=$1
    if [ -n "$DINGTALK_WEBHOOK" ]; then
        curl -s "$DINGTALK_WEBHOOK" \
            -H 'Content-Type: application/json' \
            -d "{
                \"msgtype\": \"text\",
                \"text\": {
                    \"content\": \"🚨 Webshell告警 [site=${SITE_ID}]\\n$message\"
                }
            }" > /dev/null 2>&1 || true
    fi
}

send_alert() {
    local subject=$1
    local message=$2
    send_email_alert "$subject" "$message"
    send_wechat_alert "$message"
    send_dingtalk_alert "$message"
}

#######################################
# Detection helpers
#######################################
is_whitelisted() {
    local file=$1
    local whitelist
    for whitelist in "${WHITELIST_DIRS[@]}"; do
        if [[ "$file" == "$whitelist"* ]]; then
            return 0
        fi
    done
    return 1
}

check_file_content() {
    local file=$1
    if [ ! -r "$file" ]; then
        return 1
    fi

    local php_patterns=()
    local html_patterns=()
    load_rules_from_file_into "$PHP_RULES_FILE" php_patterns
    load_rules_from_file_into "$HTML_RULES_FILE" html_patterns

    # Fallback if rule files missing
    if [ ${#php_patterns[@]} -eq 0 ]; then
        php_patterns=(
            "eval\s*\("
            "base64_decode.*eval"
            "system\s*\("
            "exec\s*\("
            "shell_exec\s*\("
            "passthru\s*\("
        )
    fi

    local pattern
    for pattern in "${php_patterns[@]}"; do
        if grep -qiE "$pattern" "$file" 2>/dev/null; then
            log_message "WARNING" "文件包含PHP可疑模式: $pattern in $file"
            return 0
        fi
    done

    if [[ "$file" =~ \.(html|htm|shtml)$ ]]; then
        for pattern in "${html_patterns[@]}"; do
            if grep -qiE "$pattern" "$file" 2>/dev/null; then
                log_message "WARNING" "文件包含HTML可疑模式: $pattern in $file"
                return 0
            fi
        done
    fi

    return 1
}

quarantine_file() {
    local file=$1
    local reason=$2

    if [ ! -f "$file" ]; then
        return 1
    fi

    local timestamp basename_q quarantine_path
    timestamp=$(date +%Y%m%d_%H%M%S)
    basename_q=$(basename "$file")
    local key
    key=$(backup_key_for_file "$file")
    quarantine_path="$QUARANTINE_DIR/${timestamp}_${key}"

    if cp -p "$file" "$quarantine_path" 2>/dev/null; then
        rm -f "$file"
        log_message "CRITICAL" "文件已隔离: $file -> $quarantine_path"
        log_message "CRITICAL" "隔离原因: $reason"

        local alert_msg
        alert_msg="检测到可疑文件并已隔离
站点: $SITE_ID
文件: $file
原因: $reason
隔离位置: $quarantine_path
时间: $(date '+%Y-%m-%d %H:%M:%S')
服务器: $(hostname)
请立即检查！"
        send_alert "🚨 Webshell检测告警 - 文件已隔离 [$SITE_ID]" "$alert_msg"
        return 0
    else
        log_message "ERROR" "无法隔离文件: $file"
        return 1
    fi
}

handle_create() {
    local file=$1

    if [[ "$file" =~ \.($DANGEROUS_EXTENSIONS)$ ]]; then
        if ! is_whitelisted "$file"; then
            log_message "WARNING" "检测到新增可疑文件: $file"
            sleep 1

            if check_file_content "$file"; then
                quarantine_file "$file" "包含Webshell特征代码"
            else
                log_message "INFO" "文件未包含明显恶意代码，但仍需人工审查: $file"
                local alert_msg
                alert_msg="检测到新增可疑文件（需人工审查）
站点: $SITE_ID
文件: $file
时间: $(date '+%Y-%m-%d %H:%M:%S')
服务器: $(hostname)"
                send_alert "⚠️ 可疑文件创建 [$SITE_ID]" "$alert_msg"
            fi
        fi
    fi
}

create_initial_backup() {
    log_message "INFO" "检查关键文件备份..."
    local critical_file key backup_file
    for critical_file in "${CRITICAL_FILES[@]}"; do
        if [ -f "$critical_file" ]; then
            key=$(backup_key_for_file "$critical_file")
            backup_file="$BACKUP_DIR/${key}.backup"
            if [ ! -f "$backup_file" ]; then
                mkdir -p "$(dirname "$backup_file")"
                cp -p "$critical_file" "$backup_file"
                log_message "INFO" "已创建初始备份: $key"
            fi
        fi
    done
}

# Force refresh backups (overwrite)
refresh_backups() {
    local critical_file key backup_file
    for critical_file in "${CRITICAL_FILES[@]}"; do
        if [ -f "$critical_file" ]; then
            key=$(backup_key_for_file "$critical_file")
            backup_file="$BACKUP_DIR/${key}.backup"
            mkdir -p "$(dirname "$backup_file")"
            cp -p "$critical_file" "$backup_file"
            log_message "INFO" "已更新备份: $key"
        fi
    done
}

restore_from_backup() {
    local file=$1
    local key backup_file
    key=$(backup_key_for_file "$file")
    backup_file="$BACKUP_DIR/${key}.backup"

    if [ -f "$backup_file" ]; then
        local timestamp tampered_backup
        timestamp=$(date +%Y%m%d_%H%M%S)
        tampered_backup="$QUARANTINE_DIR/tampered_${timestamp}_${key}"
        cp -p "$file" "$tampered_backup" 2>/dev/null || true
        cp -p "$backup_file" "$file"
        log_message "CRITICAL" "已从备份自动恢复文件: $file"
        log_message "INFO" "篡改版本已保存: $tampered_backup"
        return 0
    else
        log_message "ERROR" "备份文件不存在，无法恢复: $backup_file"
        return 1
    fi
}

handle_modify() {
    local file=$1
    local critical_file

    for critical_file in "${CRITICAL_FILES[@]}"; do
        if [ "$file" == "$critical_file" ]; then
            log_message "CRITICAL" "检测到关键文件被修改: $file"

            local timestamp key backup_path alert_msg
            timestamp=$(date +%Y%m%d_%H%M%S)
            key=$(backup_key_for_file "$file")
            backup_path="$QUARANTINE_DIR/modified_${timestamp}_${key}"
            cp -p "$file" "$backup_path" 2>/dev/null || true

            alert_msg="检测到关键文件被修改！
站点: $SITE_ID
文件: $file
时间: $(date '+%Y-%m-%d %H:%M:%S')
服务器: $(hostname)
修改版本已备份: $backup_path"

            if [ "$AUTO_RESTORE" == "true" ]; then
                if restore_from_backup "$file"; then
                    alert_msg="${alert_msg}
状态: ✅ 已自动从备份恢复
原始备份位置: $BACKUP_DIR/${key}.backup

文件已恢复到安全版本，请检查篡改版本以分析攻击。"
                    send_alert "🚨 关键文件被篡改并已自动恢复 [$SITE_ID]" "$alert_msg"
                else
                    alert_msg="${alert_msg}
状态: ❌ 自动恢复失败（备份不存在）

请手动检查和恢复文件！"
                    send_alert "🚨🚨 关键文件被篡改且无法恢复 [$SITE_ID]" "$alert_msg"
                fi
            else
                alert_msg="${alert_msg}
状态: ⚠️ 仅告警，未自动恢复

请立即检查文件内容并手动恢复！
可用备份: $BACKUP_DIR/${key}.backup"
                send_alert "🚨 关键文件修改告警 [$SITE_ID]" "$alert_msg"
            fi
            return
        fi
    done
}

generate_report() {
    log_message "INFO" "生成状态报告"

    local report_file="/tmp/webshell_monitor_report_${SITE_ID}_$(date +%Y%m%d).txt"

    cat > "$report_file" << EOF
Webshell监控系统状态报告
生成时间: $(date '+%Y-%m-%d %H:%M:%S')
服务器: $(hostname)
站点: $SITE_ID
=====================================

监控目录: $PROJECT_DIR
日志文件: $LOG_FILE
隔离目录: $QUARANTINE_DIR
备份目录: $BACKUP_DIR

关键文件数量: ${#CRITICAL_FILES[@]}
白名单目录数量: ${#WHITELIST_DIRS[@]}

隔离文件数量: $(ls -1 "$QUARANTINE_DIR" 2>/dev/null | wc -l | tr -d ' ')

最近告警统计:
- CRITICAL: $( (grep -c "CRITICAL" "$LOG_FILE" 2>/dev/null) || true )
- WARNING: $( (grep -c "WARNING" "$LOG_FILE" 2>/dev/null) || true )
- INFO: $( (grep -c "INFO" "$LOG_FILE" 2>/dev/null) || true )

隔离的文件列表:
$(ls -lh "$QUARANTINE_DIR" 2>/dev/null || echo "无")

=====================================
EOF

    cat "$report_file"
}

cleanup() {
    log_message "INFO" "收到退出信号，停止监控"
    generate_report
    exit 0
}

trap cleanup SIGTERM SIGINT

main() {
    init

    log_message "INFO" "========================================="
    log_message "INFO" "Webshell监控服务启动"
    log_message "INFO" "站点: $SITE_ID"
    log_message "INFO" "监控目录: $PROJECT_DIR"
    log_message "INFO" "日志文件: $LOG_FILE"
    log_message "INFO" "隔离目录: $QUARANTINE_DIR"
    log_message "INFO" "========================================="

    send_alert "✅ Webshell监控服务启动 [$SITE_ID]" "监控服务已在 $(hostname) 上启动\n站点: $SITE_ID\n时间: $(date)"

    # Disable set -e for the monitoring loop pipeline
    set +e
    inotifywait -m -r \
        --exclude "$INOTIFY_EXCLUDE" \
        -e create,modify,moved_to \
        --format '%e %w%f' \
        "$PROJECT_DIR" 2>/dev/null | while read -r event file; do

        case "$event" in
            CREATE|MOVED_TO)
                handle_create "$file"
                ;;
            MODIFY)
                handle_modify "$file"
                ;;
        esac
    done
}

require_site_arg() {
    if [ -z "${SITE_ID:-}" ]; then
        echo "错误: 请指定 --site <site_id>" >&2
        exit 1
    fi
}

print_help() {
    cat << EOF
Webshell监控脚本（多站点）

用法:
  $0 --site <id>                 启动监控服务
  $0 --site <id> --report        生成状态报告
  $0 --site <id> --backup        手动创建/更新关键文件备份
  $0 --site <id> --restore <路径> 手动恢复指定文件
  $0 --site <id> --list-backups  列出所有备份文件
  $0 --help                      显示帮助信息

配置目录: $CONFIG_ROOT/<site_id>.conf
systemd:  systemctl start webshell-monitor@<site_id>
EOF
}

# Argument parsing
ACTION="run"
RESTORE_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --site)
            SITE_ID="$2"
            shift 2
            ;;
        --report)
            ACTION="report"
            shift
            ;;
        --backup)
            ACTION="backup"
            shift
            ;;
        --restore)
            ACTION="restore"
            RESTORE_PATH="${2:-}"
            shift 2
            ;;
        --list-backups)
            ACTION="list-backups"
            shift
            ;;
        --help|-h)
            print_help
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            print_help
            exit 1
            ;;
    esac
done

# Allow SITE_ID from environment (systemd Environment)
if [ -z "$SITE_ID" ] && [ -n "${WEBSHELL_SITE_ID:-}" ]; then
    SITE_ID="$WEBSHELL_SITE_ID"
fi

require_site_arg
load_site_config "$SITE_ID"

case "$ACTION" in
    report)
        generate_report
        ;;
    backup)
        echo "正在创建关键文件备份..."
        refresh_backups
        echo "备份完成！备份位置: $BACKUP_DIR"
        ls -lh "$BACKUP_DIR" 2>/dev/null || true
        ;;
    restore)
        if [ -z "$RESTORE_PATH" ]; then
            echo "错误: 请指定要恢复的文件"
            echo "用法: $0 --site <id> --restore <文件路径>"
            exit 1
        fi
        echo "正在恢复文件: $RESTORE_PATH"
        restore_from_backup "$RESTORE_PATH"
        ;;
    list-backups)
        echo "关键文件备份列表 [site=$SITE_ID]"
        echo "备份目录: $BACKUP_DIR"
        echo "----------------------------------------"
        ls -lh "$BACKUP_DIR" 2>/dev/null || echo "无备份文件"
        ;;
    run)
        main
        ;;
esac
