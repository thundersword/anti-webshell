#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/proj" "$TMP/out" "$TMP/log"
: > "$TMP/log/access.log"
PROFILES_DIR="$ROOT/scripts/profiles" \
  "$ROOT/scripts/render_site_config.sh" blog "$TMP/proj" plain_php \
  --output-dir "$TMP/out" \
  --access-log "$TMP/log/access.log" \
  --site-hosts "blog.example www.blog.example"
grep -q 'ACCESS_LOG="'"$TMP/log/access.log"'"' "$TMP/out/blog.conf"
grep -q 'SITE_HOSTS="blog.example www.blog.example"' "$TMP/out/blog.conf"
# rendering again without the flags yields empty values, it does not invent a path
PROFILES_DIR="$ROOT/scripts/profiles" \
  "$ROOT/scripts/render_site_config.sh" blog "$TMP/proj" plain_php \
  --output-dir "$TMP/out"
grep -q 'ACCESS_LOG=""' "$TMP/out/blog.conf"
grep -q 'SITE_HOSTS=""' "$TMP/out/blog.conf"
echo OK