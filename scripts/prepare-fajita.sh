#!/usr/bin/env bash
# Stage this repository's provisional fajita device layer into a HakiOS checkout.
# This checks device/config integration only; it does not patch the loader, build,
# or touch a phone.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HAKIOS_ROOT=${HAKIOS_ROOT:-"$ROOT/ref/hakios-xnu-kernel"}

[ -d "$HAKIOS_ROOT/.git" ] || { echo "HakiOS checkout not found: $HAKIOS_ROOT" >&2; exit 1; }
[ -f "$HAKIOS_ROOT/tools/hakios-device.py" ] || { echo "not a HakiOS checkout: $HAKIOS_ROOT" >&2; exit 1; }

mkdir -p "$HAKIOS_ROOT/devices/fajita"
cp -a "$ROOT/devices/fajita/." "$HAKIOS_ROOT/devices/fajita/"

python3 "$HAKIOS_ROOT/tools/hakios-device.py" check fajita
make -C "$HAKIOS_ROOT" DEVICE=fajita fajita_defconfig
echo "fajita device layer staged and configuration generated; loader, XNU and UEFI source were not modified"
