#!/usr/bin/env bash
# A control-plane save must replace the site conf and its backup without a manual unlock.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/scripts/webshell_monitor.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/etc/webshell-monitor/sites" "$TMP/project" "$TMP/backup" "$TMP/stamps"
printf '%s\n' "$TMP/etc/webshell-monitor" > "$TMP/watch"
printf '%s\n' "$TMP/etc/webshell-monitor/sites/huchuan.conf" > "$TMP/critical"
cat > "$TMP/etc/webshell-monitor/sites/controlplane.conf" << EOF
SITE_ID="controlplane"
PROJECT_DIR="$TMP/project"
LOG_FILE="$TMP/monitor.log"
BACKUP_DIR="$TMP/backup"
QUARANTINE_DIR="$TMP/quarantine"
WATCH_DIRS_FILE="$TMP/watch"
CRITICAL_FILES_FILE="$TMP/critical"
WHITELIST_DIRS_FILE="$TMP/empty"
LOCK_CRITICAL_FILES="false"
EOF

printf 'OLD\n' > "$TMP/etc/webshell-monitor/sites/huchuan.conf"
mkdir -p "$TMP/backup"
printf 'OLD\n' > "$TMP/backup/sites__huchuan.conf.backup"

run_monitor() {
  WEBSHELL_CONFIG_ROOT="$TMP/etc/webshell-monitor/sites" \
  WEBSHELL_CONFIG_WRITE_DIR="$TMP/stamps" \
    "$SCRIPT" --site controlplane "$@"
}

if run_monitor --apply-config-write "$TMP/etc/webshell-monitor/sites/huchuan.conf" >"$TMP/denied.out" 2>"$TMP/denied.err"; then
  echo "FAIL: unstamped write was accepted" >&2
  exit 1
fi
grep -q '未放行' "$TMP/denied.err"
grep -q 'OLD' "$TMP/backup/sites__huchuan.conf.backup"

run_monitor --prepare-config-write "$TMP/etc/webshell-monitor/sites/huchuan.conf" >"$TMP/prepare.out"
printf 'NEW\n' > "$TMP/etc/webshell-monitor/sites/huchuan.conf"
run_monitor --apply-config-write "$TMP/etc/webshell-monitor/sites/huchuan.conf" >"$TMP/apply.out"
grep -q '控制面更新配置，已接受' "$TMP/apply.out"
grep -q 'NEW' "$TMP/backup/sites__huchuan.conf.backup"
grep -q 'NEW' "$TMP/etc/webshell-monitor/sites/huchuan.conf"

echo OK
