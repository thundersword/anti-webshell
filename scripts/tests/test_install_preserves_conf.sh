#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

awk '/^cmd_install\(\)/,/^cmd_add_site\(\)/' "$ROOT/scripts/manage_sites.sh" \
  | grep -q render_site_config && { echo "FAIL: install rewrites site conf"; exit 1; }

# shellcheck disable=SC1091
source /dev/null
eval "$(sed -n '/^prepare_investigate_conf/,/^}/p' "$ROOT/scripts/manage_sites.sh")"
prepare_investigate_conf "$TMP/investigate.conf" "$TMP"
test -f "$TMP/investigate.conf"
# second call must not overwrite a custom path
printf '%s\n' 'DEFAULT_ACCESS_LOG="/keep/me.log"' 'AUTH_LOG="/var/log/auth.log"' 'AUDIT_LOG="/var/log/audit/audit.log"' > "$TMP/investigate.conf"
prepare_investigate_conf "$TMP/investigate.conf" "$TMP"
grep -q '/keep/me.log' "$TMP/investigate.conf"

STUB_INSTALL="$TMP/install"
mkdir -p "$STUB_INSTALL" "$TMP/cfg"
printf '%s\n' '#!/usr/bin/env bash' 'exit 1' > "$STUB_INSTALL/sync_audit.sh"
chmod +x "$STUB_INSTALL/sync_audit.sh"

YELLOW='' NC=''
eval "$(sed -n '/^print_warning()/p' "$ROOT/scripts/manage_sites.sh")"
eval "$(sed -n '/^cmd_sync_audit/,/^}/p' "$ROOT/scripts/manage_sites.sh")"
require_root() { :; }

INSTALL_DIR="$STUB_INSTALL"
CONFIG_ROOT="$TMP/cfg"

set +e
sync_out="$(cmd_sync_audit 2>&1)"
sync_st=$?
set -e

[[ "$sync_st" -eq 1 ]] || { echo "FAIL: cmd_sync_audit status=$sync_st want 1"; exit 1; }
printf '%s\n' "$sync_out" | grep -q '审计规则未加载' \
  || { echo "FAIL: cmd_sync_audit missing audit load warning"; exit 1; }

STUB_BIN="$TMP/bin"
mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/systemctl" << 'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "is-active" ]]; then
  exit 3
fi
exit 0
EOF
chmod +x "$STUB_BIN/systemctl"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$STUB_INSTALL/sync_audit.sh"

set +e
sync_out="$(PATH="$STUB_BIN:$PATH" cmd_sync_audit 2>&1)"
sync_st=$?
set -e

[[ "$sync_st" -eq 1 ]] || { echo "FAIL: cmd_sync_audit inactive auditd status=$sync_st want 1"; exit 1; }
printf '%s\n' "$sync_out" | grep -q 'auditd 未运行' \
  || { echo "FAIL: cmd_sync_audit missing inactive auditd warning"; exit 1; }

echo OK
