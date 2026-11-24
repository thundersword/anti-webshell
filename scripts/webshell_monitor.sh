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
LOCK_CRITICAL_FILES="false"
ACCESS_LOG=""
SITE_HOSTS=""
DANGEROUS_EXTENSIONS="php|php3|php4|php5|phtml|pht|phps|sh|py|pl|cgi|asp|aspx|jsp|html|htm|shtml"
INOTIFY_EXCLUDE='(\.git|\.svn|\.hg|node_modules|/vendor/|/runtime/)'
CRITICAL_FILES_FILE=""
WHITELIST_DIRS_FILE=""
PHP_RULES_FILE="$SCRIPT_HOME/rules/php_webshell.rules"
HTML_RULES_FILE="$SCRIPT_HOME/rules/html_malware.rules"

CRITICAL_FILES=()
WHITELIST_DIRS=()
WATCH_DIRS_FILE=""
WATCH_DIRS=()
STRICT_QUARANTINE="false"
INOTIFY_PIDS=()
INOTIFY_FIFO=""
PRINT_WATCH_DIRS=0
SELF_TEST_CREATE_ALERT=0
SELF_TEST_INVESTIGATE=0
SEND_ALERT_FILE=""
# Relaxed logging: skip hard failures when appending to LOG_FILE (debug CLI only).
LOG_RELAXED=0

CREATE_ALERT_DEDUPE_SECS="${CREATE_ALERT_DEDUPE_SECS:-60}"

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

    load_watch_dirs
}

load_watch_dirs() {
    WATCH_DIRS=()
    if [ -n "${WATCH_DIRS_FILE:-}" ] && [ -f "$WATCH_DIRS_FILE" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in
                ''|\#*) continue ;;
            esac
            if [ -d "$line" ]; then
                WATCH_DIRS+=("$line")
            else
                log_message "WARNING" "watch dir missing, skip: $line"
            fi
        done < "$WATCH_DIRS_FILE"
    fi
    if [ "${#WATCH_DIRS[@]}" -eq 0 ]; then
        WATCH_DIRS=("$PROJECT_DIR")
    fi
}

# Flatten relative path for backup filename: public/index.php -> public__index.php
# Uses longest matching WATCH_DIRS prefix (fallback PROJECT_DIR) for multi-root sites.
backup_key_for_file() {
    local file=$1
    local root rel best_len=0 best_root="" pd len
    pd="${PROJECT_DIR%/}"
    for root in "${WATCH_DIRS[@]}"; do
        root="${root%/}"
        if [[ "$file" == "$root"/* ]] || [ "$file" = "$root" ]; then
            len=${#root}
            if [ "$len" -gt "$best_len" ]; then
                best_len=$len
                best_root="$root"
            fi
        fi
    done
    if [ -n "$best_root" ]; then
        rel="${file#$best_root/}"
    else
        rel="${file#"$pd"/}"
        if [ "$rel" = "$file" ] && [[ "$file" == "$pd"/* ]]; then
            rel="${file#$pd/}"
        elif [ "$rel" = "$file" ]; then
            rel="$(basename "$file")"
        fi
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
    if [ "${LOCK_CRITICAL_FILES:-false}" = "true" ]; then
        lock_all_critical_files
    fi
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
    echo "$prefix $message"
    if [ "${LOG_RELAXED:-0}" = "1" ]; then
        mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
        echo "$prefix $message" >> "$LOG_FILE" 2>/dev/null || true
    else
        if mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null; then
            if ! echo "$prefix $message" >> "$LOG_FILE" 2>/dev/null; then
                echo "警告: 无法写入日志文件: $LOG_FILE" >&2
            fi
        else
            echo "警告: 无法创建日志目录: $(dirname "$LOG_FILE")" >&2
        fi
    fi
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

# Second alert from an investigation body. Does not enter the monitor loop.
send_alert_from_file() {
    local path=$1
    local first="" subject body
    if [ ! -f "$path" ]; then
        log_message "CRITICAL" "入侵分析告警文件不存在: $path"
        return 1
    fi
    IFS= read -r first < "$path" || true
    body=$(cat "$path" || true)
    log_message "CRITICAL" "$first"
    case "$first" in
        入侵路径*)
            subject="入侵路径 [$SITE_ID]"
            ;;
        *)
            subject="未能生成入侵时间线 [$SITE_ID]"
            ;;
    esac
    send_alert "$subject" "$body"
}

#######################################
# Detection helpers
#######################################
is_whitelisted() {
    local file=$1
    local whitelist
    for whitelist in ${WHITELIST_DIRS[@]+"${WHITELIST_DIRS[@]}"}; do
        if [[ "$file" == "$whitelist"* ]]; then
            return 0
        fi
    done
    return 1
}

# Cheap path filter if inotify --exclude misses nested files (filename-only match on some versions).
is_noisy_path() {
    local file=$1
    local base="${file##*/}"
    case "$file" in
        */.git/*|*/.svn/*|*/.hg/*|*/.idea/*|*/node_modules/*|*/.venv/*|*/__pycache__/*|*/vendor/*|*/runtime/*|*/Runtime/*|*/storage/framework/*|*/storage/logs/*|*/bootstrap/cache/*)
            return 0
            ;;
    esac
    case "$base" in
        *.db|*.sqlite|*.sqlite3|*.sqlite-journal|*.sqlite-wal|*.sqlite-shm)
            return 0
            ;;
    esac
    if [[ "$base" =~ \.sqlite ]]; then
        return 0
    fi
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

create_alert_stamp_path() {
    local file=$1
    local key
    key=$(printf '%s' "$file" | sha256sum 2>/dev/null | awk '{print $1}')
    if [ -z "$key" ]; then
        key=$(printf '%s' "$file" | md5sum 2>/dev/null | awk '{print $1}')
    fi
    [ -n "$key" ] || key=$(basename "$file")
    echo "${QUARANTINE_DIR}/.alert_dedupe/${key}"
}

should_send_create_alert() {
    local file=$1
    local stamp age
    stamp=$(create_alert_stamp_path "$file")
    mkdir -p "$(dirname "$stamp")" 2>/dev/null || true
    if [ -f "$stamp" ]; then
        age=$(( $(date +%s) - $(stat -c %Y "$stamp" 2>/dev/null || stat -f %m "$stamp") ))
        if [ "$age" -lt "$CREATE_ALERT_DEDUPE_SECS" ]; then
            return 1
        fi
    fi
    return 0
}

mark_create_alert_sent() {
    local stamp
    stamp=$(create_alert_stamp_path "$1")
    mkdir -p "$(dirname "$stamp")" 2>/dev/null || true
    date +%s >"$stamp" 2>/dev/null || true
}

# UTC stamp plus 8 hex chars of the original path. sha256sum, else shasum -a 256.
incident_id_for() {
    local file=$1
    local stamp digest
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    digest=$(printf '%s' "$file" | sha256sum 2>/dev/null | awk '{print $1}' || true)
    if [ -z "$digest" ]; then
        digest=$(printf '%s' "$file" | shasum -a 256 2>/dev/null | awk '{print $1}' || true)
    fi
    printf '%s_%s\n' "$stamp" "${digest:0:8}"
}

# Mirror webshell_investigate.py _parse_conf: trim, then strip matching quote chars.
unquote_conf_value() {
    local value=$1
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    while [ -n "$value" ]; do
        case "$value" in
            \'*) value=${value#\'} ;;
            \"*) value=${value#\"} ;;
            *) break ;;
        esac
    done
    while [ -n "$value" ]; do
        case "$value" in
            *\') value=${value%\'} ;;
            *\") value=${value%\"} ;;
            *) break ;;
        esac
    done
    printf '%s' "$value"
}

load_investigate_settings() {
    local conf="${WEBSHELL_INVESTIGATE_CONF:-/etc/webshell-monitor/investigate.conf}"
    local line key value
    DEFAULT_ACCESS_LOG_RESOLVED=""
    AUTH_LOG_RESOLVED="/var/log/auth.log"
    AUDIT_LOG_RESOLVED="/var/log/audit/audit.log"
    if [ ! -f "$conf" ]; then
        return 0
    fi
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"
        case "$line" in
            ''|\#*) continue ;;
        esac
        case "$line" in
            *=*) ;;
            *) continue ;;
        esac
        key="${line%%=*}"
        key="${key%"${key##*[![:space:]]}"}"
        value=$(unquote_conf_value "${line#*=}")
        case "$key" in
            DEFAULT_ACCESS_LOG) DEFAULT_ACCESS_LOG_RESOLVED=$value ;;
            AUTH_LOG) AUTH_LOG_RESOLVED=$value ;;
            AUDIT_LOG) AUDIT_LOG_RESOLVED=$value ;;
        esac
    done < "$conf"
    AUTH_LOG_RESOLVED="${AUTH_LOG_RESOLVED:-/var/log/auth.log}"
    AUDIT_LOG_RESOLVED="${AUDIT_LOG_RESOLVED:-/var/log/audit/audit.log}"
}

record_investigate_command() {
    local line=$1
    local dest="${WEBSHELL_INVESTIGATE_COMMAND_LOG:-}"
    if [ -z "$dest" ]; then
        return 0
    fi
    mkdir -p "$(dirname "$dest")" 2>/dev/null || true
    printf '%s\n' "$line" >> "$dest" 2>/dev/null || true
}

# Write the anchor, then start analysis without waiting.
# Args: file quarantine_path reason [incident_id] [event_time]
launch_investigation() {
    local file=$1
    local quarantine_path=$2
    local reason=${3:-}
    local incident_id=${4:-}
    local event_time=${5:-}
    local root dir anchor_path resolved_access access_is_global
    local DEFAULT_ACCESS_LOG_RESOLVED="" AUTH_LOG_RESOLVED="" AUDIT_LOG_RESOLVED=""

    if [ -z "$incident_id" ]; then
        incident_id=$(incident_id_for "$file")
    fi
    if [ -z "$event_time" ]; then
        event_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    fi

    load_investigate_settings
    if [ -n "${ACCESS_LOG:-}" ]; then
        resolved_access="$ACCESS_LOG"
        access_is_global=false
    else
        resolved_access="$DEFAULT_ACCESS_LOG_RESOLVED"
        access_is_global=true
    fi

    root="${WEBSHELL_INCIDENTS_DIR:-/var/lib/webshell/incidents}"
    dir="$root/${SITE_ID}"
    anchor_path="$dir/${incident_id}.anchor.json"
    if ! mkdir -p "$dir" 2>/dev/null; then
        log_message "ERROR" "无法创建事件目录: $dir"
        return 0
    fi

    if ! INCIDENT_ID="$incident_id" \
        ANCHOR_SITE_ID="$SITE_ID" \
        FILE_PATH="$file" \
        QUARANTINE_PATH="$quarantine_path" \
        REASON="$reason" \
        EVENT_TIME="$event_time" \
        ANCHOR_PROJECT_DIR="${PROJECT_DIR:-}" \
        ACCESS_LOG_RESOLVED="$resolved_access" \
        ANCHOR_SITE_HOSTS="${SITE_HOSTS:-}" \
        ACCESS_IS_GLOBAL="$access_is_global" \
        AUTH_LOG_RESOLVED="$AUTH_LOG_RESOLVED" \
        AUDIT_LOG_RESOLVED="$AUDIT_LOG_RESOLVED" \
        ANCHOR_PATH="$anchor_path" \
        python3 - << 'PY'
import json
import os
from pathlib import Path

raw_hosts = os.environ.get("ANCHOR_SITE_HOSTS", "")
hosts = raw_hosts.split() if raw_hosts else []
payload = {
    "incident_id": os.environ.get("INCIDENT_ID", ""),
    "site_id": os.environ.get("ANCHOR_SITE_ID", ""),
    "file_path": os.environ.get("FILE_PATH", ""),
    "quarantine_path": os.environ.get("QUARANTINE_PATH", ""),
    "reason": os.environ.get("REASON", ""),
    "event_time": os.environ.get("EVENT_TIME", ""),
    "project_dir": os.environ.get("ANCHOR_PROJECT_DIR", ""),
    "access_log": os.environ.get("ACCESS_LOG_RESOLVED", ""),
    "site_hosts": hosts,
    "access_is_global": os.environ.get("ACCESS_IS_GLOBAL", "") == "true",
    "auth_log": os.environ.get("AUTH_LOG_RESOLVED", ""),
    "audit_log": os.environ.get("AUDIT_LOG_RESOLVED", ""),
}
Path(os.environ["ANCHOR_PATH"]).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
    then
        log_message "ERROR" "无法写入入侵锚点: $anchor_path"
        return 0
    fi

    if [ "${WEBSHELL_INVESTIGATE_DRY_RUN:-}" = "1" ]; then
        record_investigate_command "dry-run ${SCRIPT_HOME}/webshell_investigate.sh --site ${SITE_ID} --anchor ${anchor_path}"
        log_message "INFO" "入侵分析 dry-run incident=$incident_id"
        return 0
    fi

    record_investigate_command "systemd-run --scope --collect --unit=webshell-investigate-${SITE_ID}-${incident_id} --property=RuntimeMaxSec=20 ${SCRIPT_HOME}/webshell_investigate.sh --site ${SITE_ID} --anchor ${anchor_path}"
    log_message "INFO" "已启动入侵分析 incident=$incident_id"
    systemd-run --scope --collect \
        --unit="webshell-investigate-${SITE_ID}-${incident_id}" \
        --property=RuntimeMaxSec=20 \
        "$SCRIPT_HOME/webshell_investigate.sh" --site "$SITE_ID" --anchor "$anchor_path" &
    return 0
}

quarantine_file() {
    local file=$1
    local reason=$2
    local alert_mode="${3:-}"

    if [ ! -f "$file" ]; then
        return 1
    fi

    local timestamp basename_q quarantine_path incident_id event_time
    timestamp=$(date +%Y%m%d_%H%M%S)
    basename_q=$(basename "$file")
    local key
    key=$(backup_key_for_file "$file")
    quarantine_path="$QUARANTINE_DIR/${timestamp}_${key}"

    if cp -p "$file" "$quarantine_path" 2>/dev/null; then
        rm -f "$file"
        incident_id=$(incident_id_for "$file")
        event_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        log_message "CRITICAL" "文件已隔离: $file -> $quarantine_path incident=$incident_id"
        log_message "CRITICAL" "隔离原因: $reason"
        launch_investigation "$file" "$quarantine_path" "$reason" "$incident_id" "$event_time" || true

        if [ "$alert_mode" != "no_alert" ]; then
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
        fi
        return 0
    else
        log_message "ERROR" "无法隔离文件: $file"
        return 1
    fi
}

handle_create() {
    local file=$1
    if is_noisy_path "$file"; then
        return 0
    fi
    if is_whitelisted "$file"; then
        return 0
    fi

    local is_dangerous=0
    if [[ "$file" =~ \.($DANGEROUS_EXTENSIONS)$ ]]; then
        is_dangerous=1
    fi

    if [ "$is_dangerous" -eq 1 ]; then
        log_message "WARNING" "监控目录新增可疑文件: $file"
        sleep 1
        if [ "${STRICT_QUARANTINE:-false}" = "true" ] || check_file_content "$file"; then
            local reason="包含Webshell特征代码"
            if [ "${STRICT_QUARANTINE:-false}" = "true" ] && ! check_file_content "$file"; then
                reason="STRICT_QUARANTINE: 危险扩展名文件"
            fi
            # quarantine_file already send_alert — mark dedupe to suppress generic create alert
            if should_send_create_alert "$file"; then
                quarantine_file "$file" "$reason"
                mark_create_alert_sent "$file"
            else
                quarantine_file "$file" "$reason" "no_alert"
            fi
            return 0
        fi
        if should_send_create_alert "$file"; then
            local alert_msg
            alert_msg="监控目录新增可疑文件（需人工审查）
站点: $SITE_ID
文件: $file
时间: $(date '+%Y-%m-%d %H:%M:%S')
服务器: $(hostname)"
            send_alert "⚠️ 可疑文件创建 [$SITE_ID]" "$alert_msg"
            mark_create_alert_sent "$file"
        fi
        return 0
    fi

    log_message "WARNING" "监控目录新增文件: $file"

    if should_send_create_alert "$file"; then
        local alert_msg
        alert_msg="监控目录新增文件
站点: $SITE_ID
文件: $file
时间: $(date '+%Y-%m-%d %H:%M:%S')
服务器: $(hostname)"
        send_alert "⚠️ 监控目录新增文件 [$SITE_ID]" "$alert_msg"
        mark_create_alert_sent "$file"
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

file_is_immutable() {
    local f=$1
    command -v lsattr >/dev/null 2>&1 || return 1
    [ -f "$f" ] || return 1
    local flags
    flags=$(lsattr -d "$f" 2>/dev/null | awk '{print $1}')
    [[ "$flags" == *i* ]]
}

unlock_one_file() {
    local f=$1
    [ -f "$f" ] || return 0
    command -v chattr >/dev/null 2>&1 || return 0
    chattr -i "$f" 2>/dev/null || true
}

lock_one_file() {
    local f=$1
    if [ ! -f "$f" ]; then
        return 0
    fi
    if ! command -v chattr >/dev/null 2>&1; then
        log_message "WARNING" "未安装 chattr（e2fsprogs），无法锁定: $f"
        return 1
    fi
    if chattr +i "$f" 2>/dev/null; then
        log_message "INFO" "已锁定关键文件 (chattr +i): $f"
        return 0
    fi
    log_message "WARNING" "锁定失败（文件系统可能不支持 immutable）: $f"
    return 1
}

lock_all_critical_files() {
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
    local f ok=0 fail=0
    for f in "${CRITICAL_FILES[@]}"; do
        if [ -f "$f" ]; then
            if lock_one_file "$f"; then
                ok=$((ok + 1))
            else
                fail=$((fail + 1))
            fi
        fi
    done
    log_message "INFO" "关键文件锁定完成: 成功 ${ok}，失败 ${fail}"
}

unlock_all_critical_files() {
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
    local f
    for f in "${CRITICAL_FILES[@]}"; do
        if [ -f "$f" ]; then
            unlock_one_file "$f"
            log_message "INFO" "已解除锁定 (chattr -i): $f"
        fi
    done
}

list_lock_status() {
    echo "关键文件锁定状态 [site=$SITE_ID]  LOCK_CRITICAL_FILES=${LOCK_CRITICAL_FILES:-false}"
    echo "----------------------------------------"
    local f
    for f in "${CRITICAL_FILES[@]}"; do
        if [ ! -f "$f" ]; then
            echo "  缺失  $f"
            continue
        fi
        if file_is_immutable "$f"; then
            echo "  已锁定  $f"
        else
            echo "  未锁定  $f"
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
        unlock_one_file "$file"
        cp -p "$file" "$tampered_backup" 2>/dev/null || true
        cp -p "$backup_file" "$file"
        if [ "${LOCK_CRITICAL_FILES:-false}" = "true" ]; then
            lock_one_file "$file"
        fi
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
    if is_noisy_path "$file"; then
        return 0
    fi
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
    if [ "${#INOTIFY_PIDS[@]}" -gt 0 ]; then
        kill "${INOTIFY_PIDS[@]}" 2>/dev/null || true
    fi
    rm -f "${INOTIFY_FIFO:-}"
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
    # Always drop VCS/tooling trees even if a profile omit them.
    local combined_exclude
    combined_exclude="(${INOTIFY_EXCLUDE}|\\.git|\\.svn|\\.hg|node_modules|\\.venv|__pycache__|\\.db|\\.sqlite)"
    log_message "INFO" "inotify exclude: $combined_exclude"
    log_message "INFO" "监控根目录: ${WATCH_DIRS[*]}"
    # close_write instead of modify: one event per save, not per write() syscall.
    INOTIFY_FIFO="$QUARANTINE_DIR/.inotify.fifo"
    rm -f "$INOTIFY_FIFO"
    mkfifo "$INOTIFY_FIFO"
    INOTIFY_PIDS=()
    local root
    for root in "${WATCH_DIRS[@]}"; do
        inotifywait -m -r \
            --exclude "$combined_exclude" \
            -e create,close_write,moved_to \
            --format '%e %w%f' \
            "$root" >>"$INOTIFY_FIFO" 2>/dev/null &
        INOTIFY_PIDS+=($!)
    done
    while read -r event file; do
        case "$event" in
            *ISDIR*)
                ;;
            CREATE|MOVED_TO|CREATE,*)
                handle_create "$file"
                ;;
            CLOSE_WRITE*|MODIFY*)
                handle_modify "$file"
                ;;
        esac
    done <"$INOTIFY_FIFO"
}

run_self_test_create_alert() {
    local tmp test_file alert_count
    tmp=$(mktemp -d)
    self_test_alerts_log="$tmp/alerts.log"
    : >"$self_test_alerts_log"

    QUARANTINE_DIR="$tmp/quarantine"
    SITE_ID="test"
    PROJECT_DIR="$tmp/site"
    LOG_FILE="$tmp/monitor.log"
    LOG_RELAXED=1
    WHITELIST_DIRS=()
    mkdir -p "$QUARANTINE_DIR" "$PROJECT_DIR"
    touch "$LOG_FILE"

    send_alert() {
        {
            echo '---ALERT---'
            echo "$1"
        } >>"$self_test_alerts_log"
    }

    test_file="$PROJECT_DIR/newfile.txt"
    printf 'hello\n' >"$test_file"

    handle_create "$test_file"
    if ! grep -q "监控目录新增文件" "$LOG_FILE"; then
        echo "FAIL: log missing 监控目录新增文件" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [ ! -s "$self_test_alerts_log" ]; then
        echo "FAIL: expected non-empty alerts.log after first handle_create" >&2
        rm -rf "$tmp"
        return 1
    fi

    handle_create "$test_file"
    alert_count=$(grep -c '^---ALERT---' "$self_test_alerts_log" || true)
    if [ "$alert_count" -ne 1 ]; then
        echo "FAIL: dedupe expected 1 alert line, got $alert_count" >&2
        rm -rf "$tmp"
        return 1
    fi

    WHITELIST_DIRS=("$PROJECT_DIR")
    handle_create "$test_file"
    alert_count=$(grep -c '^---ALERT---' "$self_test_alerts_log" || true)
    if [ "$alert_count" -ne 1 ]; then
        echo "FAIL: whitelist should not add alerts, got $alert_count" >&2
        rm -rf "$tmp"
        return 1
    fi

    rm -rf "$tmp"
    echo "OK"
    return 0
}

anchor_for() {
    python3 - "$1" "$2" << 'PY'
import json
import os
import sys

root, want = sys.argv[1], sys.argv[2]
found = ""
for dirpath, _dirnames, names in os.walk(root):
    for name in names:
        if not name.endswith(".anchor.json"):
            continue
        path = os.path.join(dirpath, name)
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("file_path") == want:
            found = path
            break
    if found:
        break
if not found:
    sys.stderr.write("FAIL: anchor missing for %s\n" % want)
    sys.exit(1)
sys.stdout.write(found)
PY
}

check_anchor() {
    python3 - "$@" << 'PY'
import json
import re
import sys

path = sys.argv[1]
expect = {
    "file_path": sys.argv[2],
    "access_log": sys.argv[3],
    "site_id": sys.argv[4],
    "access_is_global": sys.argv[5] == "true",
    "site_hosts": sys.argv[6].split() if sys.argv[6] else [],
}
if len(sys.argv) > 7 and sys.argv[7]:
    expect["auth_log"] = sys.argv[7]
if len(sys.argv) > 8 and sys.argv[8]:
    expect["audit_log"] = sys.argv[8]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
errors = []
for key in ("incident_id", "file_path", "access_log", "site_id"):
    if not data.get(key):
        errors.append("missing %s" % key)
for key, want in expect.items():
    if data.get(key) != want:
        errors.append("%s got %r want %r" % (key, data.get(key), want))
incident = data.get("incident_id") or ""
if not re.match(r"^[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}$", incident):
    errors.append("incident_id %r" % incident)
event = data.get("event_time") or ""
if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", event):
    errors.append("event_time %r" % event)
if errors:
    sys.stderr.write("FAIL: " + "; ".join(errors) + "\n")
    sys.exit(1)
PY
}

run_self_test_investigate() {
    local tmp site_dir access_log conf_root incidents command_log
    local original quarantined anchor fallback_file fallback_anchor default_access
    tmp=$(mktemp -d)
    site_dir="$tmp/site"
    access_log="$tmp/access.log"
    incidents="$tmp/incidents"
    command_log="$tmp/commands.log"
    conf_root="$tmp/sites"
    mkdir -p "$site_dir/public" "$tmp/quarantine" "$tmp/backup" "$incidents" "$conf_root"
    printf 'ok\n' > "$access_log"
    : > "$command_log"
    LOG_FILE="$tmp/monitor.log"
    LOG_RELAXED=1
    : > "$LOG_FILE"

    cat > "$conf_root/blog.conf" << EOF
PROJECT_DIR="$site_dir"
ACCESS_LOG="$access_log"
SITE_HOSTS="blog.example www.blog.example"
LOG_FILE="$LOG_FILE"
QUARANTINE_DIR="$tmp/quarantine"
BACKUP_DIR="$tmp/backup"
EOF
    CONFIG_ROOT="$conf_root"
    load_site_config blog

    WEBSHELL_INVESTIGATE_DRY_RUN=1
    WEBSHELL_INCIDENTS_DIR="$incidents"
    WEBSHELL_INVESTIGATE_COMMAND_LOG="$command_log"
    systemd-run() {
        printf 'systemd-run %s\n' "$*" >> "$WEBSHELL_INVESTIGATE_COMMAND_LOG"
        return 0
    }

    original="$site_dir/public/x.php"
    quarantined="$tmp/quarantine/x.php"
    printf '<?php echo 1;\n' > "$quarantined"
    launch_investigation "$original" "$quarantined" "包含Webshell特征代码"

    anchor=$(anchor_for "$incidents" "$original") || {
        rm -rf "$tmp"
        return 1
    }
    case "$anchor" in
        "$incidents/$SITE_ID/"*.anchor.json) ;;
        *)
            echo "FAIL: anchor path $anchor" >&2
            rm -rf "$tmp"
            return 1
            ;;
    esac
    if ! check_anchor "$anchor" "$original" "$access_log" "$SITE_ID" "false" "blog.example www.blog.example" "" ""; then
        rm -rf "$tmp"
        return 1
    fi

    ACCESS_LOG=""
    WEBSHELL_INVESTIGATE_CONF="$tmp/investigate.conf"
    default_access="$tmp/default-access.log"
    printf 'default\n' > "$default_access"
    cat > "$WEBSHELL_INVESTIGATE_CONF" << EOF
DEFAULT_ACCESS_LOG="$default_access"
AUTH_LOG="$tmp/auth.log"
AUDIT_LOG="$tmp/audit.log"
EOF
    fallback_file="$site_dir/public/y.php"
    launch_investigation "$fallback_file" "$tmp/quarantine/y.php" "fallback"
    fallback_anchor=$(anchor_for "$incidents" "$fallback_file") || {
        rm -rf "$tmp"
        return 1
    }
    if ! check_anchor "$fallback_anchor" "$fallback_file" "$default_access" "$SITE_ID" "true" "blog.example www.blog.example" "$tmp/auth.log" "$tmp/audit.log"; then
        rm -rf "$tmp"
        return 1
    fi

    if ! grep -q 'dry-run' "$command_log"; then
        echo "FAIL: command log missing dry-run record" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q 'systemd-run' "$command_log"; then
        echo "FAIL: command log contains systemd-run" >&2
        rm -rf "$tmp"
        return 1
    fi

    rm -rf "$tmp"
    echo "OK"
    return 0
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
  $0 --site <id> --lock          对关键文件 chattr +i（阻止修改）
  $0 --site <id> --unlock        解除关键文件不可变属性（发版前）
  $0 --site <id> --lock-status   查看关键文件锁定状态
  $0 --site <id> --send-alert-file <文件>  发送入侵时间线告警后退出
  $0 --help                      显示帮助信息

调试（无需 --site）:
  $0 --print-watch-dirs          解析并输出监控根目录列表（每行一个）
  $0 --watch-file <列表文件>     覆盖 WATCH_DIRS_FILE（与 --print-watch-dirs 联用）
  $0 --project-dir <目录>        覆盖 PROJECT_DIR（与 --print-watch-dirs 联用）
  $0 --self-test-create-alert    新建文件告警逻辑自检（供 scripts/tests 使用）
  $0 --self-test-investigate     隔离后入侵分析启动自检（供 scripts/tests 使用）

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
        --send-alert-file)
            SEND_ALERT_FILE="$2"
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
        --lock)
            ACTION="lock"
            shift
            ;;
        --unlock)
            ACTION="unlock"
            shift
            ;;
        --lock-status)
            ACTION="lock-status"
            shift
            ;;
        --print-watch-dirs)
            PRINT_WATCH_DIRS=1
            shift
            ;;
        --watch-file)
            WATCH_DIRS_FILE="$2"
            shift 2
            ;;
        --project-dir)
            PROJECT_DIR="$2"
            shift 2
            ;;
        --self-test-create-alert)
            SELF_TEST_CREATE_ALERT=1
            shift
            ;;
        --self-test-investigate)
            SELF_TEST_INVESTIGATE=1
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

if [ "${PRINT_WATCH_DIRS:-0}" = "1" ]; then
    LOG_RELAXED=1
    if [ -z "$PROJECT_DIR" ] || [ ! -d "$PROJECT_DIR" ]; then
        echo "错误: PROJECT_DIR 无效: ${PROJECT_DIR:-<empty>}" >&2
        exit 1
    fi
    load_watch_dirs
    printf '%s\n' "${WATCH_DIRS[@]}"
    exit 0
fi

if [ "${SELF_TEST_CREATE_ALERT:-0}" = "1" ]; then
    set +e
    run_self_test_create_alert
    exit $?
fi

if [ "${SELF_TEST_INVESTIGATE:-0}" = "1" ]; then
    set +e
    run_self_test_investigate
    exit $?
fi

require_site_arg
load_site_config "$SITE_ID"

if [ -n "${SEND_ALERT_FILE:-}" ]; then
    send_alert_from_file "$SEND_ALERT_FILE" || exit $?
    exit 0
fi

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
    lock)
        lock_all_critical_files
        list_lock_status
        ;;
    unlock)
        unlock_all_critical_files
        list_lock_status
        ;;
    lock-status)
        list_lock_status
        ;;
    run)
        main
        ;;
esac
