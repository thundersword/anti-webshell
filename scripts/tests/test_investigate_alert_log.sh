#!/usr/bin/env bash
# Real monitor --site --incident-id --send-alert-file.
# The temp site conf stubs send_alert the way the in-script self-tests do,
# so the alert body is captured and no mail is sent.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MONITOR="$ROOT/scripts/webshell_monitor.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/sites" "$TMP/site"
incident="20261124T034211Z_ab12cd34"

cat > "$TMP/sites/blog.conf" << EOF
SITE_ID="blog"
PROJECT_DIR="$TMP/site"
LOG_FILE="$TMP/monitor.log"
ALERT_EMAIL=""
WECHAT_WEBHOOK=""
DINGTALK_WEBHOOK=""
send_alert() {
    printf '%s\n' "\$1" > "$TMP/subject"
    printf '%s\n' "\$2" > "$TMP/body"
}
EOF

cat > "$TMP/ok.txt" << 'EOF'
入侵路径 [blog] 置信度: 高
文件: /tmp/a.php
EOF

cat > "$TMP/fail.txt" << 'EOF'
未能生成入侵时间线 [blog]
文件: /tmp/a.php
原因: 分析超时
EOF

run_monitor() {
  WEBSHELL_CONFIG_ROOT="$TMP/sites" \
    "$MONITOR" --site blog --incident-id "$incident" --send-alert-file "$1"
}

run_monitor "$TMP/ok.txt"
grep -F "入侵结论 incident=${incident}" "$TMP/monitor.log" >/dev/null || {
  echo "FAIL: success log missing incident conclusion" >&2
  cat "$TMP/monitor.log" >&2
  exit 1
}
if grep -F '入侵路径 [blog] 置信度: 高' "$TMP/monitor.log" >/dev/null; then
  echo "FAIL: success log repeated the alert first line" >&2
  cat "$TMP/monitor.log" >&2
  exit 1
fi
first="$(head -n 1 "$TMP/body")"
[ "$first" = "入侵路径 [blog] 置信度: 高" ] || {
  echo "FAIL: success alert body first line: $first" >&2
  cat "$TMP/body" >&2
  exit 1
}
if grep -q 'incident=' "$TMP/body"; then
  echo "FAIL: success alert body contains incident=" >&2
  cat "$TMP/body" >&2
  exit 1
fi

run_monitor "$TMP/fail.txt"
grep -F "未能生成入侵时间线 incident=${incident} 原因: 分析超时" "$TMP/monitor.log" >/dev/null || {
  echo "FAIL: failure log line missing reason" >&2
  cat "$TMP/monitor.log" >&2
  exit 1
}
fail_first="$(head -n 1 "$TMP/body")"
[ "$fail_first" = "未能生成入侵时间线 [blog]" ] || {
  echo "FAIL: failure alert body first line: $fail_first" >&2
  cat "$TMP/body" >&2
  exit 1
}
if grep -q 'incident=' "$TMP/body"; then
  echo "FAIL: failure alert body contains incident=" >&2
  cat "$TMP/body" >&2
  exit 1
fi

echo OK
