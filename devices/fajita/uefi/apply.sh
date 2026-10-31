#!/bin/bash
# Run this from a HakiOS checkout after copying this device directory into
# devices/fajita. The common HakiOS UEFI script consumes files/ below it.
set -e
ROOT=$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd)
exec "$ROOT/uefi/apply.sh" fajita
