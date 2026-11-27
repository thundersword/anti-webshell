#!/usr/bin/env bash
# unlock keeps controlplane from quarantining a pull; lock records the new files.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/scripts/webshell_monitor.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/repo/backend" "$TMP/project" "$TMP/backup" "$TMP/sites"
printf '%s\n' "$TMP/repo" > "$TMP/watch"
printf '%s\n' "$TMP/repo/backend/app.py" > "$TMP/critical"
printf 'OLD\n' > "$TMP/repo/backend/app.py"
printf 'OLD\n' > "$TMP/backup/backend__app.py.backup"
cat > "$TMP/sites/controlplane.conf" << EOF
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

run_monitor() {
  WEBSHELL_CONFIG_ROOT="$TMP/sites" \
  WEBSHELL_RELEASE_FLAG="$TMP/release" \
    "$SCRIPT" --site controlplane "$@"
}

[ "$(run_monitor --release-status)" = "enforce" ]
run_monitor --unlock >/dev/null
[ -f "$TMP/release" ]
[ "$(run_monitor --release-status)" = "release" ]
printf 'NEW\n' > "$TMP/repo/backend/app.py"
run_monitor --lock >/dev/null
grep -q 'NEW' "$TMP/backup/backend__app.py.backup"
grep -q 'NEW' "$TMP/repo/backend/app.py"
[ ! -f "$TMP/release" ]
[ "$(run_monitor --release-status)" = "enforce" ]

echo OK
