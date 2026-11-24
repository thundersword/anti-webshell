#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$("$ROOT/scripts/webshell_monitor.sh" --self-test-investigate 2>&1)" || {
  echo "$OUT" >&2
  exit 1
}
echo "$OUT" | grep -q "OK"
