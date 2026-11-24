#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/sites" "$TMP/www/blog/wp-content/upgrade" "$TMP/www/shop/uploads" \
  "$TMP/www/shop/runtime" "$TMP/www/shop/.git" "$TMP/extra" \
  "$TMP/skip-me" "$TMP/extra-watch"
cat > "$TMP/sites/blog.conf" << EOF
SITE_ID="blog"
PROJECT_DIR="$TMP/www/blog"
INOTIFY_EXCLUDE='(\\.git|/vendor/|/runtime/|/wp-content/upgrade/|\\.db$)'
EOF
cat > "$TMP/sites/shop.conf" << EOF
SITE_ID="shop"
PROJECT_DIR="$TMP/www/shop"
INOTIFY_EXCLUDE='(\\.git|/vendor/|/runtime/)'
EOF
printf '%s\n' "$TMP/www/shop/uploads" > "$TMP/sites/shop.whitelist"
printf '%s\n' "$TMP/www/blog" "$TMP/extra" > "$TMP/sites/blog.watch"
cat > "$TMP/local.rules" << EOF
# operator exclude
-a never,exit -F dir=$TMP/skip-me -k webshell_exclude
-a never,exit -F dir=$TMP/missing-local -k webshell_exclude
-a always,exit -F dir=$TMP/extra-watch -F perm=wa -k webshell_write
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
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/www/blog/.git" && { echo "FAIL: missing .git was emitted"; exit 1; }
printf '%s\n' "$RULES" | grep -Fq '.db$' && { echo "FAIL: file-suffix token became a dir rule"; exit 1; }
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/missing-local" && { echo "FAIL: missing local dir was emitted"; exit 1; }
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/www/blog/wp-content/upgrade"
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/www/blog"
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/extra"
printf '%s\n' "$RULES" | grep -q 'arch=b64'
printf '%s\n' "$RULES" | grep -q 'arch=b32'
printf '%s\n' "$RULES" | grep -q 'webshell_write'
printf '%s\n' "$RULES" | grep -q -- '-F dir='"$TMP/skip-me"
python3 - << PY
from pathlib import Path
text = Path("$TMP/webshell.rules").read_text()
never = text.find("-F dir=$TMP/www/shop/runtime")
always = text.find("-F dir=$TMP/www/shop ")
# shop project dir always-rule; runtime never must appear earlier
shop_always = text.find("dir=$TMP/www/shop -F perm=wa")
shop_never = text.find("dir=$TMP/www/shop/runtime")
assert shop_never != -1 and shop_always != -1 and shop_never < shop_always, (shop_never, shop_always)
lines = text.splitlines()
skip = next(i for i, line in enumerate(lines) if "/skip-me" in line)
first_perm = next(i for i, line in enumerate(lines) if "perm=wa" in line)
generated_write = [
    i for i, line in enumerate(lines)
    if "webshell_write" in line and "arch=" in line
]
assert generated_write, "no generated webshell_write"
last_generated = generated_write[-1]
extra = next(i for i, line in enumerate(lines) if "/extra-watch" in line)
comment = next(i for i, line in enumerate(lines) if line.strip() == "# operator exclude")
assert skip < first_perm, (skip, first_perm)
assert extra > last_generated, (extra, last_generated)
assert comment > last_generated, (comment, last_generated)
print("order ok")
PY
echo OK