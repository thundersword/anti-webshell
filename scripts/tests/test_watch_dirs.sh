#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/a" "$TMP/b"
printf '%s\n' "$TMP/a" "$TMP/b" "$TMP/missing" > "$TMP/watch.list"

# Extract/load helper by sourcing a tiny stub if needed; preferred: invoke
# a --print-watch-dirs debug flag once implemented.
# Until flag exists, this test should fail.
OUT="$("$ROOT/scripts/webshell_monitor.sh" --print-watch-dirs --watch-file "$TMP/watch.list" --project-dir "$TMP/a" 2>&1)" || true
echo "$OUT" | grep -q "$TMP/a"
echo "$OUT" | grep -q "$TMP/b"
echo "$OUT" | grep -vq "$TMP/missing" || echo "$OUT" | grep -q "skip.*missing"

# backup_key_for_file: secondary watch roots keep path under root (not bare basename).
eval "$(sed -n '/^backup_key_for_file/,/^}/p' "$ROOT/scripts/webshell_monitor.sh")"
WATCH_DIRS=("$TMP/a" "$TMP/b")
PROJECT_DIR="$TMP/a"
mkdir -p "$TMP/b/deep/path"
[ "$(backup_key_for_file "$TMP/b/deep/path/y.txt")" = "deep__path__y.txt" ]

echo "OK"