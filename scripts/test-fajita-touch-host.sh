#!/usr/bin/env bash
# Host-only production-code tests; no USB enumeration or device access.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT="$ROOT/.work/tests/touch-host-suite"
mkdir -p "$OUT"
export ASAN_OPTIONS=detect_leaks=0
for name in log-tail cmd-db rpmh-transaction touch-power-seq touch-report touch-panel; do
    clang -std=c11 -Wall -Wextra -Werror -O1 -fsanitize=address,undefined \
        -fno-omit-frame-pointer "$ROOT/tests/test-fajita-$name.c" -o "$OUT/$name"
    "$OUT/$name"
done
for name in chipinfo touch-startup touch-transport touch-runtime touch-probe touch-layout-probe touch-rpmh touch-handoff live-reader touch-panel-reader touch-panel-staging touch-dt-audit; do
    python3 "$ROOT/tests/test-fajita-$name.py"
done
python3 "$ROOT/scripts/audit-fajita-touch-dt.py"
