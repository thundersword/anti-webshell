#!/bin/bash
#######################################
# Render controlplane self-guard site config
#
# Produces controlplane.conf, .watch, .critical, .whitelist under sites dir.
#######################################

set -euo pipefail

SCRIPT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILES_DIR="${PROFILES_DIR:-$SCRIPT_HOME/profiles}"
SITE_ID="controlplane"

CONTROLPLANE_ENV="/etc/webshell-monitor/controlplane.env"
if [ -f "$CONTROLPLANE_ENV" ]; then
    # shellcheck source=/dev/null
    source "$CONTROLPLANE_ENV"
fi

WEBSHELL_UI_ROOT="${WEBSHELL_UI_ROOT:-/www/wwwroot/anti-webshell}"
WEBSHELL_INSTALL_DIR="${WEBSHELL_INSTALL_DIR:-/opt/webshell_monitor}"
WEBSHELL_BACKEND_DIR="${WEBSHELL_BACKEND_DIR:-/opt/anti-webshell/backend}"

# Config parent (default /etc/webshell-monitor): prefer WEBSHELL_CONFIG_PARENT;
# else derive from WEBSHELL_CONFIG_ROOT — basename "sites" → parent is dirname,
# otherwise treat ROOT as the parent (e.g. /etc/webshell-monitor).
if [ -n "${WEBSHELL_CONFIG_PARENT:-}" ]; then
    :
elif [ -n "${WEBSHELL_CONFIG_ROOT:-}" ]; then
  _cfg_root="${WEBSHELL_CONFIG_ROOT%/}"
  if [ "$(basename "$_cfg_root")" = "sites" ]; then
    WEBSHELL_CONFIG_PARENT="$(dirname "$_cfg_root")"
  else
    WEBSHELL_CONFIG_PARENT="$_cfg_root"
  fi
else
  WEBSHELL_CONFIG_PARENT="/etc/webshell-monitor"
fi
OUTPUT_DIR="${WEBSHELL_CONFIG_PARENT}/sites"

LOG_ROOT="/var/log/webshell"
QUARANTINE_ROOT="/var/quarantine"
BACKUP_ROOT="/var/webshell_backup"

ALERT_EMAIL="${CONTROLPLANE_ALERT_EMAIL:-${ALERT_EMAIL:-}}"
WECHAT_WEBHOOK="${CONTROLPLANE_WECHAT_WEBHOOK:-${WECHAT_WEBHOOK:-}}"
DINGTALK_WEBHOOK="${CONTROLPLANE_DINGTALK_WEBHOOK:-${DINGTALK_WEBHOOK:-}}"
AUTO_RESTORE="${CONTROLPLANE_AUTO_RESTORE:-true}"
LOCK_CRITICAL_FILES="${CONTROLPLANE_LOCK_CRITICAL:-true}"

usage() {
    cat <<'EOF'
用法: render_controlplane.sh [--output-dir DIR]

环境变量: WEBSHELL_UI_ROOT, WEBSHELL_INSTALL_DIR, WEBSHELL_CONFIG_PARENT
  或 WEBSHELL_CONFIG_ROOT（sites 目录或其父目录）, WEBSHELL_BACKEND_DIR；
  告警可选 CONTROLPLANE_ALERT_EMAIL 等。
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        -h|--help) usage ;;
        --*) echo "未知选项: $1" >&2; usage ;;
        *) echo "未知参数: $1" >&2; usage ;;
    esac
done

PROFILE_FILE="$PROFILES_DIR/controlplane.conf"
if [ ! -f "$PROFILE_FILE" ]; then
    echo "错误: 找不到 profile: $PROFILE_FILE" >&2
    exit 1
fi

# shellcheck source=/dev/null
source "$PROFILE_FILE"

mkdir -p "$OUTPUT_DIR"
mkdir -p "$LOG_ROOT" 2>/dev/null || true
mkdir -p "$QUARANTINE_ROOT/$SITE_ID" 2>/dev/null || true
mkdir -p "$BACKUP_ROOT/$SITE_ID" 2>/dev/null || true

CONF_FILE="$OUTPUT_DIR/${SITE_ID}.conf"
WATCH_FILE="$OUTPUT_DIR/${SITE_ID}.watch"
CRITICAL_FILE="$OUTPUT_DIR/${SITE_ID}.critical"
WHITELIST_FILE="$OUTPUT_DIR/${SITE_ID}.whitelist"

WATCH_TMP="$(mktemp)"
CRITICAL_TMP="$(mktemp)"
WHITELIST_TMP="$(mktemp)"
CONF_TMP="$(mktemp)"
trap 'rm -f "$WATCH_TMP" "$CRITICAL_TMP" "$WHITELIST_TMP" "$CONF_TMP"' EXIT

: > "$WATCH_TMP"
: > "$CRITICAL_TMP"
: > "$WHITELIST_TMP"

abs_dir() {
    local d="$1"
    if [ -d "$d" ]; then
        (cd "$d" && pwd)
    fi
}

for root in "$WEBSHELL_UI_ROOT" "$WEBSHELL_INSTALL_DIR" "$WEBSHELL_CONFIG_PARENT" "$WEBSHELL_BACKEND_DIR"; do
    resolved="$(abs_dir "$root" || true)"
    if [ -n "$resolved" ]; then
        echo "$resolved" >> "$WATCH_TMP"
    fi
done

if [ -d "$WEBSHELL_UI_ROOT" ]; then
  ui_root="$(cd "$WEBSHELL_UI_ROOT" && pwd)"
  find "$ui_root" -type f \
    ! -path '*/.git/*' ! -path '*/node_modules/*' \
    ! -name '*.map' \
    2>/dev/null >> "$CRITICAL_TMP" || true
fi

if [ -d "$WEBSHELL_INSTALL_DIR" ]; then
  inst="$(cd "$WEBSHELL_INSTALL_DIR" && pwd)"
  find "$inst" -type f -name '*.sh' 2>/dev/null >> "$CRITICAL_TMP" || true
  if [ -d "$inst/profiles" ]; then
    find "$inst/profiles" -maxdepth 1 -type f -name '*.conf' 2>/dev/null >> "$CRITICAL_TMP" || true
  fi
  if [ -d "$inst/rules" ]; then
    find "$inst/rules" -type f 2>/dev/null >> "$CRITICAL_TMP" || true
  fi
fi

sites_dir="$OUTPUT_DIR"
if [ -d "$sites_dir" ]; then
  for suffix in conf critical watch; do
    for f in "$sites_dir"/*."$suffix"; do
      [ -f "$f" ] || continue
      base="$(basename "$f")"
      case "$base" in
        controlplane.conf|controlplane.critical|controlplane.watch)
          continue
          ;;
      esac
      echo "$f" >> "$CRITICAL_TMP"
    done
  done
fi

env_file="$WEBSHELL_CONFIG_PARENT/controlplane.env"
if [ -f "$env_file" ]; then
  echo "$env_file" >> "$CRITICAL_TMP"
fi

echo "$CONF_FILE" >> "$CRITICAL_TMP"
echo "$CRITICAL_FILE" >> "$CRITICAL_TMP"
echo "$WATCH_FILE" >> "$CRITICAL_TMP"

if [ -d "$WEBSHELL_BACKEND_DIR" ]; then
  backend="$(cd "$WEBSHELL_BACKEND_DIR" && pwd)"
  find "$backend" -type f -name '*.py' \
    ! -path '*/.venv/*' ! -path '*/__pycache__/*' \
    2>/dev/null >> "$CRITICAL_TMP" || true
fi

if [ -f "$WEBSHELL_BACKEND_DIR/.env" ] && [ "$(id -u)" -eq 0 ]; then
  chown root:root "$WEBSHELL_BACKEND_DIR/.env" 2>/dev/null || true
  chmod 600 "$WEBSHELL_BACKEND_DIR/.env" 2>/dev/null || true
fi

awk 'NF && !seen[$0]++' "$WATCH_TMP" > "${WATCH_TMP}.u" && mv "${WATCH_TMP}.u" "$WATCH_TMP"
awk 'NF && !seen[$0]++' "$CRITICAL_TMP" > "${CRITICAL_TMP}.u" && mv "${CRITICAL_TMP}.u" "$CRITICAL_TMP"

RULES_DIR="$WEBSHELL_INSTALL_DIR/rules"
if [ ! -d "$RULES_DIR" ]; then
  RULES_DIR="$SCRIPT_HOME/rules"
fi

STRICT_VAL="${STRICT_QUARANTINE:-true}"

mv "$WATCH_TMP" "$WATCH_FILE"
mv "$CRITICAL_TMP" "$CRITICAL_FILE"
: > "$WHITELIST_FILE"

cat > "$CONF_TMP" << EOF
# Auto-generated by render_controlplane.sh — do not edit by hand if using GUI
SITE_ID="$SITE_ID"
PROJECT_DIR="$WEBSHELL_UI_ROOT"
PROFILE="controlplane"
LOG_FILE="$LOG_ROOT/${SITE_ID}.log"
QUARANTINE_DIR="$QUARANTINE_ROOT/$SITE_ID"
BACKUP_DIR="$BACKUP_ROOT/$SITE_ID"
AUTO_RESTORE="$AUTO_RESTORE"
LOCK_CRITICAL_FILES="$LOCK_CRITICAL_FILES"
STRICT_QUARANTINE="$STRICT_VAL"
WATCH_DIRS_FILE="$WATCH_FILE"
ALERT_EMAIL="$ALERT_EMAIL"
WECHAT_WEBHOOK="$WECHAT_WEBHOOK"
DINGTALK_WEBHOOK="$DINGTALK_WEBHOOK"
DANGEROUS_EXTENSIONS="${DANGEROUS_EXTENSIONS}"
INOTIFY_EXCLUDE="${INOTIFY_EXCLUDE}"
CRITICAL_FILES_FILE="$CRITICAL_FILE"
WHITELIST_DIRS_FILE="$WHITELIST_FILE"
PHP_RULES_FILE="$RULES_DIR/php_webshell.rules"
HTML_RULES_FILE="$RULES_DIR/html_malware.rules"
EOF

mv "$CONF_TMP" "$CONF_FILE"
trap - EXIT

chmod 640 "$CONF_FILE" "$CRITICAL_FILE" "$WATCH_FILE" "$WHITELIST_FILE" 2>/dev/null || true

echo "已生成控制面自保配置: $CONF_FILE"
echo "  监控根列表: $WATCH_FILE ($(wc -l < "$WATCH_FILE" | tr -d ' ') 项)"
echo "  关键文件列表: $CRITICAL_FILE ($(wc -l < "$CRITICAL_FILE" | tr -d ' ') 项)"