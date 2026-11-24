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
echo OK
