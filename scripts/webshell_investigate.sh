#!/usr/bin/env bash
set -euo pipefail
SCRIPT_HOME="$(cd "$(dirname "$0")" && pwd)"
SITE=""
ANCHOR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site) SITE="$2"; shift 2 ;;
    --anchor) ANCHOR="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 1 ;;
  esac
done
OUT="${ANCHOR%.anchor.json}.json"
BODY="$(mktemp)"
ERR="$(mktemp)"
trap 'rm -f "$BODY" "$ERR"' EXIT

anchor_file_path() {
  local path="未知" line
  if [ -n "${ANCHOR:-}" ] && [ -r "$ANCHOR" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        *'"file_path"'*)
          line="${line#*\"file_path\"}"
          line="${line#*:}"
          line="${line#*\"}"
          line="${line%%\"*}"
          if [ -n "$line" ]; then
            path="$line"
          fi
          break
          ;;
      esac
    done < "$ANCHOR"
  fi
  printf '%s' "$path"
}

write_failure_body() {
  local reason=$1
  local file_path
  file_path=$(anchor_file_path)
  printf '%s\n' \
    "未能生成入侵时间线 [$SITE]" \
    "文件: $file_path" \
    "原因: $reason" \
    > "$BODY"
}

short_reason() {
  local line=""
  if [ -f "$ERR" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line#"${line%%[![:space:]]*}"}"
      line="${line%"${line##*[![:space:]]}"}"
      if [ -n "$line" ]; then
        printf '%s' "${line:0:200}"
        return 0
      fi
    done < "$ERR"
  fi
  printf '%s' "分析失败"
}

py_rc=0
if ! command -v python3 >/dev/null 2>&1; then
  write_failure_body "python3 不可用"
else
  set +e
  python3 "$SCRIPT_HOME/webshell_investigate.py" --anchor "$ANCHOR" --output "$OUT" > "$BODY" 2>"$ERR"
  py_rc=$?
  set -e
  if [ ! -s "$BODY" ]; then
    if [ "$py_rc" -eq 127 ]; then
      write_failure_body "python3 不可用"
    else
      write_failure_body "$(short_reason)"
    fi
  fi
fi

# Prefer PATH so a test can stub the monitor. Installed units are not on PATH
# and keep using the sibling script.
monitor="$SCRIPT_HOME/webshell_monitor.sh"
if monitor_on_path=$(command -v webshell_monitor.sh 2>/dev/null); then
  monitor="$monitor_on_path"
fi
"$monitor" --site "$SITE" --send-alert-file "$BODY" || true
exit 0
