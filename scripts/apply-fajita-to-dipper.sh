#!/usr/bin/env bash
# Apply the fajita-specific delta after the fixed dipper SDM845 source tree
# has been prepared. This is host-only and never accesses a device.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DIPPER_ROOT=${DIPPER_ROOT:-"$ROOT/ref/dipper-xnu"}
HACKIOS_ROOT="$DIPPER_ROOT/work/hackios"
EDK2_ROOT="$DIPPER_ROOT/work/edk2"
XNU_SRC="$HACKIOS_ROOT/xnu-src/xnu-8796.121.2"

[ -d "$HACKIOS_ROOT/.git" ] || { echo "prepared dipper HakiOS tree not found: $HACKIOS_ROOT" >&2; exit 1; }
[ -d "$EDK2_ROOT/.git" ] || { echo "prepared EDK2 tree not found: $EDK2_ROOT" >&2; exit 1; }
[ -d "$XNU_SRC/.git" ] || { echo "prepared XNU source tree not found: $XNU_SRC" >&2; exit 1; }

apply_once() {
	local repo="$1" patch="$2" strip="${3:-1}" label="$4"
	if git -C "$repo" apply --check -p"$strip" "$patch" 2>/dev/null; then
		git -C "$repo" apply -p"$strip" "$patch"
	elif git -C "$repo" apply --reverse --check -p"$strip" "$patch" 2>/dev/null; then
		echo "$label already applied"
	else
		echo "cannot apply $label cleanly in $repo" >&2
		exit 1
	fi
}

ensure_gic_timer_patch() {
	local repo="$1" source="$2" strip="$3"
	if rg -q '^#define GIC_INTID_VTIMER_DEFAULT[[:space:]]+27' "$source" &&\
	   rg -q '^static uint32_t gic_intid_vtimer[[:space:]]*=' "$source" &&\
	   rg -q '^#define GIC_INTID_VTIMER[[:space:]]+gic_intid_vtimer' "$source" &&\
	   rg -Fq 'PE_parse_boot_argn("vtimer_intid"' "$source" &&\
	   rg -Fq 'mine = 1u << GIC_INTID_VTIMER;' "$source"; then
		echo "GIC virtual timer boot-arg already present in $source"
	else
		apply_once "$repo" "$ROOT/patches/fajita-gic-timer.patch" "$strip" "GIC virtual timer boot-arg"
	fi
}

ensure_midr_fallback() {
	python3 "$ROOT/scripts/ensure-midr-fallback.py" "$HACKIOS_ROOT/xnuloader/loader.c"
}

ensure_reproducible_loader() {
	python3 "$ROOT/scripts/ensure-loader-repro.py" "$HACKIOS_ROOT/xnuloader/build.sh"
}

apply_once "$HACKIOS_ROOT" "$ROOT/patches/fajita-after-dipper-loader.patch" 1 "fajita platform patch"
ensure_reproducible_loader
ensure_midr_fallback
apply_once "$HACKIOS_ROOT" "$ROOT/patches/fajita-loader-ufs-handoff.patch" 1 "UFS handoff guard"
ensure_gic_timer_patch "$HACKIOS_ROOT" "$HACKIOS_ROOT/xnu/files/osfmk/arm64/gic_v3_irq.c" 1
ensure_gic_timer_patch "$XNU_SRC" "$XNU_SRC/osfmk/arm64/gic_v3_irq.c" 3

mkdir -p "$EDK2_ROOT/Platform/Oneplus/sdm845"
cp -a "$ROOT/devices/fajita/uefi/files/Platform/Oneplus/sdm845/." "$EDK2_ROOT/Platform/Oneplus/sdm845/"
cp -a "$ROOT/devices/fajita/uefi/files/XnuLoaderPkg/Driver/." "$EDK2_ROOT/XnuLoaderPkg/Driver/"
python3 "$ROOT/scripts/verify-fajita-source.py" --edk2 "$EDK2_ROOT" --loader "$HACKIOS_ROOT/xnuloader/loader.c"

if [ ! -f "$HACKIOS_ROOT/xnuloader/identity.h" ]; then
	python3 "$HACKIOS_ROOT/xnuloader/mkidentity.py" --placeholder > "$HACKIOS_ROOT/xnuloader/identity.h"
fi
(cd "$HACKIOS_ROOT/xnuloader" && ./build.sh)

mkdir -p "$EDK2_ROOT/XnuLoaderPkg/Binary"
cp "$HACKIOS_ROOT/xnuloader/loader.efi" "$EDK2_ROOT/XnuLoaderPkg/Binary/loader.efi"
BUILD_DIR="$ROOT/.work/build/fajita"
KERNEL="$XNU_SRC/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837"
GIC_SOURCE="$XNU_SRC/osfmk/arm64/gic_v3_irq.c"
mkdir -p "$BUILD_DIR"
if bash "$HACKIOS_ROOT/xnu-build.sh" -j8 -k > "$BUILD_DIR/xnu-build.log" 2>&1; then
	XNU_STATUS=0
else
	XNU_STATUS=$?
fi
if [ ! -s "$KERNEL" ]; then
	echo "XNU build did not produce a kernel; see $BUILD_DIR/xnu-build.log" >&2
	exit 1
fi
if [ "$GIC_SOURCE" -nt "$KERNEL" ]; then
	echo "XNU kernel is older than its GIC timer source; rebuild did not update the kernel" >&2
	exit 1
fi
if [ "$XNU_STATUS" -ne 0 ] && ! rg -q 'check_all_exports' "$BUILD_DIR/xnu-build.log"; then
	echo "XNU build failed outside the documented post-link export check; see $BUILD_DIR/xnu-build.log" >&2
	exit "$XNU_STATUS"
fi
echo "XNU kernel available after incremental build: $KERNEL ($(stat -c %s "$KERNEL") bytes)"
if ! (cd "$EDK2_ROOT" && bash build.sh -d fajita) > "$BUILD_DIR/edk2-build.log" 2>&1; then
	tail -80 "$BUILD_DIR/edk2-build.log" >&2
	exit 1
fi

BASE_IMAGE="$EDK2_ROOT/boot-fajita.img"
FLASHBOOT="$HACKIOS_ROOT/userland/flashboot.apfs"
RAMDISK="$HACKIOS_ROOT/userland/root.apfs"
UEFI_PREFIX="$EDK2_ROOT/workspace/Build/fajita/RELEASE_CLANG38/FV/SDM845_UEFI.fd-bootshim"
for input in "$BASE_IMAGE" "$FLASHBOOT" "$RAMDISK" "$UEFI_PREFIX"; do
	[ -s "$input" ] || { echo "required one-shot template/build input missing: $input" >&2; exit 1; }
done
cp "$UEFI_PREFIX" "$BUILD_DIR/uefi-bootshim-fd.bin"
UEFI_PREFIX="$BUILD_DIR/uefi-bootshim-fd.bin"
PAYLOAD="$BUILD_DIR/uefi-payload.bin"
ONE_SHOT="$BUILD_DIR/boot-fajita-ufs-no-usb.img"
python3 "$ROOT/scripts/rebuild-fajita-payload.py" \
	--loader "$HACKIOS_ROOT/xnuloader/loader.efi" --kernel "$KERNEL" \
	--flashboot "$FLASHBOOT" --ramdisk "$RAMDISK" \
	--cmdline-file "$ROOT/devices/fajita/bootargs.txt" --output "$PAYLOAD"
python3 "$ROOT/scripts/repack-fajita-uefi.py" \
	--boot "$BASE_IMAGE" --uefi-image "$UEFI_PREFIX" --payload "$PAYLOAD" \
	--mkbootimg "$EDK2_ROOT/tools/mkbootimg.py" --output "$ONE_SHOT"
python3 "$ROOT/scripts/verify-fajita-boot.py" "$ONE_SHOT" \
	--uefi-prefix "$UEFI_PREFIX" \
	--dtb "$EDK2_ROOT/Platform/Oneplus/sdm845/FdtBlob_compat/fajita.dtb" \
	--loader "$HACKIOS_ROOT/xnuloader/loader.efi" --kernel "$KERNEL" \
	--flashboot "$FLASHBOOT" --ramdisk "$RAMDISK" \
	--cmdline-file "$ROOT/devices/fajita/bootargs.txt"
sha256sum "$ONE_SHOT" > "$BUILD_DIR/SHA256SUMS"
echo "fajita one-shot image rebuilt and verified: $ONE_SHOT. No device operation was performed."
