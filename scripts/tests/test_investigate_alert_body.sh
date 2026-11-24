#!/usr/bin/env bash
# webshell_investigate.sh failure and pass-through alert bodies.
# Stubs python3 and webshell_monitor.sh on a private PATH so no mail is sent
# and the real monitor is not started.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/scripts/webshell_investigate.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

HIDE="$TMP/bin"
mkdir -p "$HIDE"
for cmd in dirname pwd mktemp rm sed cat head cp; do
  src="$(command -v "$cmd")"
  ln -s "$src" "$HIDE/$cmd"
done

cat > "$HIDE/webshell_monitor.sh" << 'EOF'
#!/bin/sh
out="${ALERT_CAPTURE:?}"
file=""
while [ $# -gt 0 ]; do
  case "$1" in
    --send-alert-file)
      file=$2
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
/bin/cp "$file" "$out"
exit 0
EOF
chmod +x "$HIDE/webshell_monitor.sh"

ANCHOR="$TMP/incident.anchor.json"
cat > "$ANCHOR" << 'EOF'
{
  "incident_id": "20261124T034211Z_ab12cd34",
  "site_id": "blog",
  "file_path": "/www/wwwroot/blog/public/x.php"
}
EOF

run_investigate() {
  local capture="$1"
  ALERT_CAPTURE="$capture" PATH="$HIDE" "$BASH" "$SCRIPT" --site blog --anchor "$ANCHOR"
}

# python3 missing: failure body, real file path, specific reason.
rm -f "$HIDE/python3"
capture="$TMP/missing.txt"
run_investigate "$capture"
first="$(head -n 1 "$capture")"
[ "$first" = "未能生成入侵时间线 [blog]" ] || {
  echo "FAIL: missing python3 first line: $first" >&2
  exit 1
}
grep -qx '文件: /www/wwwroot/blog/public/x.php' "$capture" || {
  echo "FAIL: missing python3 file line" >&2
  cat "$capture" >&2
  exit 1
}
grep -qx '原因: python3 不可用' "$capture" || {
  echo "FAIL: missing python3 reason" >&2
  cat "$capture" >&2
  exit 1
}

# python3 exits 1 with empty stdout: failure body, first line as specified.
cat > "$HIDE/python3" << 'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$HIDE/python3"
capture="$TMP/exit1.txt"
run_investigate "$capture"
first="$(head -n 1 "$capture")"
[ "$first" = "未能生成入侵时间线 [blog]" ] || {
  echo "FAIL: exit 1 first line: $first" >&2
  cat "$capture" >&2
  exit 1
}
grep -qx '原因: 分析失败' "$capture" || {
  echo "FAIL: exit 1 reason" >&2
  cat "$capture" >&2
  exit 1
}

# non-empty stdout is forwarded unchanged, including a successful body.
cat > "$HIDE/python3" << 'EOF'
#!/bin/sh
printf '%s\n' '入侵路径 [blog] 置信度: 高'
exit 0
EOF
chmod +x "$HIDE/python3"
capture="$TMP/ok.txt"
run_investigate "$capture"
printf '%s\n' '入侵路径 [blog] 置信度: 高' > "$TMP/want-ok.txt"
cmp -s "$TMP/want-ok.txt" "$capture" || {
  echo "FAIL: success body was not forwarded unchanged" >&2
  cat "$capture" >&2
  exit 1
}

echo "OK"
