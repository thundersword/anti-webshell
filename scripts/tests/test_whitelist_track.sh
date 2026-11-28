#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

OUT="$("$ROOT/scripts/webshell_monitor.sh" --self-test-whitelist-track 2>&1)" || {
  echo "FAIL: --self-test-whitelist-track exited non-zero"
  echo "$OUT"
  exit 1
}

echo "$OUT" | grep -q "OK" || {
  echo "FAIL: expected OK in output"
  echo "$OUT"
  exit 1
}

echo "OK"