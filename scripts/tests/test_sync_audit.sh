#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/sites" "$TMP/www/blog" "$TMP/www/shop/uploads" "$TMP/www/shop/runtime"
cat > "$TMP/sites/blog.conf" << EOF
SITE_ID="blog"
PROJECT_DIR="$TMP/www/blog"
INOTIFY_EXCLUDE='(\\.git|/vendor/|/runtime/|/wp-content/upgrade/)'
EOF
cat > "$TMP/sites/shop.conf" << EOF
SITE_ID="shop"
PROJECT_DIR="$TMP/www/shop"
INOTIFY_EXCLUDE='(\\.git|/vendor/|/runtime/)'
EOF
printf '%s\n' "$TMP/www/shop/uploads" > "$TMP/sites/shop.whitelist"
printf '%s\n' "$TMP/www/blog" "$TMP/extra" > "$TMP/sites/blog.watch"
cat > "$TMP/local.rules" << 'EOF'
# operator exclude
-a never,exit -F dir=/tmp/skip-me -k webshell_exclude
EOF

WEBSHELL_AUDIT_DRY_RUN=1 \
  CONFIG_ROOT="$TMP/sites" \
  RULES_OUT="$TMP/webshell.rules" \
  LOCAL_RULES="$TMP/local.rules" \
  "$ROOT/scripts/sync_audit.sh"

RULES="$(cat "$TMP/webshell.rules")"
printf '%s\n' "$RULES" | grep -q 'overwritten'
printf '%s\n' "$RULES" | grep -q -- '-e 2' && { echo "FAIL: locked audit"; exit 1; }
printf '%s\n' "$RULES" | grep -q "$TMP/www/shop/uploads" && { echo "FAIL: whitelist excluded"; exit 1; }
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/www/shop/runtime"
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/www/shop/.git"
printf '%s\n' "$RULES" | grep -Fq -- '-F dir='"$TMP/www/shop/git" && { echo "FAIL: spurious git exclude (want .git)"; exit 1; }
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/www/blog/wp-content/upgrade"
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/www/blog"
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/extra"
printf '%s\n' "$RULES" | grep -q 'arch=b64'
printf '%s\n' "$RULES" | grep -q 'arch=b32'
printf '%s\n' "$RULES" | grep -q 'webshell_write'
printf '%s\n' "$RULES" | grep -q '/tmp/skip-me'
python3 - << PY
from pathlib import Path
text = Path("$TMP/webshell.rules").read_text()
never = text.find("-F dir=$TMP/www/shop/runtime")
always = text.find("-F dir=$TMP/www/shop ")
# shop project dir always-rule; runtime never must appear earlier
shop_always = text.find("dir=$TMP/www/shop -F perm=wa")
shop_never = text.find("dir=$TMP/www/shop/runtime")
assert shop_never != -1 and shop_always != -1 and shop_never < shop_always, (shop_never, shop_always)
print("order ok")
PY
echo OK