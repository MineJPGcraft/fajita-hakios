#!/usr/bin/env bash
# Apply the fajita-specific delta after the fixed dipper SDM845 source tree
# has been prepared. This is host-only and never accesses a device.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DIPPER_ROOT=${DIPPER_ROOT:-"$ROOT/ref/dipper-xnu"}
HACKIOS_ROOT="$DIPPER_ROOT/work/hackios"
EDK2_ROOT="$DIPPER_ROOT/work/edk2"

[ -d "$HACKIOS_ROOT/.git" ] || { echo "prepared dipper HakiOS tree not found: $HACKIOS_ROOT" >&2; exit 1; }
[ -d "$EDK2_ROOT/.git" ] || { echo "prepared EDK2 tree not found: $EDK2_ROOT" >&2; exit 1; }

patch="$ROOT/patches/fajita-after-dipper-loader.patch"
if git -C "$HACKIOS_ROOT" apply --check "$patch" 2>/dev/null; then
	git -C "$HACKIOS_ROOT" apply "$patch"
elif git -C "$HACKIOS_ROOT" apply --reverse --check "$patch" 2>/dev/null; then
	echo "fajita-after-dipper-loader.patch already applied"
else
	echo "cannot apply fajita-after-dipper-loader.patch cleanly" >&2
	exit 1
fi

mkdir -p "$EDK2_ROOT/Platform/Oneplus/sdm845"
cp -a "$ROOT/devices/fajita/uefi/files/Platform/Oneplus/sdm845/." "$EDK2_ROOT/Platform/Oneplus/sdm845/"

if [ ! -f "$HACKIOS_ROOT/xnuloader/identity.h" ]; then
	python3 "$HACKIOS_ROOT/xnuloader/mkidentity.py" --placeholder > "$HACKIOS_ROOT/xnuloader/identity.h"
fi
(cd "$HACKIOS_ROOT/xnuloader" && ./build.sh)

echo "fajita delta applied and loader.efi built; XNU/UEFI image build and device operation were not performed"
