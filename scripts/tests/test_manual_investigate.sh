#!/usr/bin/env bash
# Manual --investigate leaves the file in place; --quarantine isolates it once.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/scripts/webshell_monitor.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/sites" "$TMP/www/uploads" "$TMP/outside" "$TMP/quarantine" "$TMP/backup" "$TMP/incidents"
printf 'x\n' > "$TMP/www/uploads/a.jpg"
printf 'y\n' > "$TMP/www/uploads/b.jpg"
printf 'z\n' > "$TMP/outside/c.jpg"
cat > "$TMP/sites/blog.conf" << EOF
SITE_ID="blog"
PROJECT_DIR="$TMP/www"
LOG_FILE="$TMP/blog.log"
QUARANTINE_DIR="$TMP/quarantine"
BACKUP_DIR="$TMP/backup"
EOF

run_investigate() {
  WEBSHELL_CONFIG_ROOT="$TMP/sites" \
  WEBSHELL_INVESTIGATE_DRY_RUN=1 \
  WEBSHELL_INCIDENTS_DIR="$TMP/incidents" \
  WEBSHELL_INVESTIGATE_COMMAND_LOG="$TMP/commands.log" \
    "$SCRIPT" --site blog "$@"
}

OUT="$(run_investigate --investigate "$TMP/www/uploads/a.jpg")"
printf '%s\n' "$OUT" > "$TMP/track.out"
grep -q "手动追踪: $TMP/www/uploads/a.jpg incident=" "$TMP/track.out"
grep -q '^incident=' "$TMP/track.out"
[ -f "$TMP/www/uploads/a.jpg" ]
INCIDENT="$(sed -n 's/^incident=//p' "$TMP/track.out" | head -n 1)"
ANCHOR="$TMP/incidents/blog/${INCIDENT}.anchor.json"
grep -q "$TMP/www/uploads/a.jpg" "$ANCHOR"
python3 - "$ANCHOR" "$TMP/www/uploads/a.jpg" << 'PY'
import json, sys
anchor = json.load(open(sys.argv[1], encoding="utf-8"))
assert anchor["file_path"] == sys.argv[2]
assert anchor["quarantine_path"] == sys.argv[2]
assert anchor["reason"] == "手动追踪"
PY
grep -q 'systemd-run' "$TMP/commands.log" && { echo "FAIL: dry-run called systemd-run"; exit 1; }
grep -q 'dry-run' "$TMP/commands.log"

if run_investigate --investigate "$TMP/outside/c.jpg" >"$TMP/outside.out" 2>"$TMP/outside.err"; then
  echo "FAIL: path outside the site was accepted" >&2
  exit 1
fi
grep -q '文件不在监控目录内' "$TMP/outside.err"

if run_investigate --investigate uploads/a.jpg >/dev/null 2>"$TMP/rel.err"; then
  echo "FAIL: relative path was accepted" >&2
  exit 1
fi
grep -q '必须是绝对路径' "$TMP/rel.err"

BEFORE="$(wc -l < "$TMP/commands.log" | tr -d ' ')"
OUT="$(run_investigate --investigate "$TMP/www/uploads/b.jpg" --quarantine)"
printf '%s\n' "$OUT" > "$TMP/quarantine.out"
grep -q "文件已隔离: $TMP/www/uploads/b.jpg" "$TMP/quarantine.out"
grep -q '^incident=' "$TMP/quarantine.out"
[ ! -f "$TMP/www/uploads/b.jpg" ]
[ "$(find "$TMP/quarantine" -type f | wc -l | tr -d ' ')" -ge 1 ]
AFTER="$(wc -l < "$TMP/commands.log" | tr -d ' ')"
[ "$((AFTER - BEFORE))" -eq 1 ]

echo OK
