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
set +e
python3 "$SCRIPT_HOME/webshell_investigate.py" --anchor "$ANCHOR" --output "$OUT" > "$BODY"
set -e
"$SCRIPT_HOME/webshell_monitor.sh" --site "$SITE" --send-alert-file "$BODY" || true
rm -f "$BODY"
