#!/usr/bin/env bash
# Apply the fajita-specific delta after the fixed dipper SDM845 source tree
# has been prepared. This is host-only and never accesses a device.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DIPPER_ROOT=${DIPPER_ROOT:-"$ROOT/ref/dipper-xnu"}
CMDLINE_FILE=${FAJITA_BOOTARGS_FILE:-"$ROOT/devices/fajita/bootargs.txt"}
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

ensure_fault_observation() {
	# The later cache-scope patch changes context around two observation hunks.
	# Recognize the complete observation layer before using git's reverse check.
	local source="$HACKIOS_ROOT/xnuloader/loader.c" marker
	local complete=1
	for marker in 'UEFI fault held' 'UEFI context saved, preparing caches' \
	    'cache preparation complete' 'masking firmware-core interrupts before PSCI' \
	    'requesting PSCI CPU_ON core' 'secondary core reached the entry stub'; do
		rg -Fq "$marker" "$source" || complete=0
	done
	if [ "$complete" -eq 1 ]; then
		echo "screen fault observation already applied"
	else
		apply_once "$HACKIOS_ROOT" "$ROOT/patches/fajita-loader-fault-observation.patch" 1 "screen fault observation"
	fi
}

apply_once "$HACKIOS_ROOT" "$ROOT/patches/fajita-after-dipper-loader.patch" 1 "fajita platform patch"
ensure_reproducible_loader
ensure_midr_fallback
apply_once "$HACKIOS_ROOT" "$ROOT/patches/fajita-loader-ufs-handoff.patch" 1 "UFS handoff guard"
ensure_fault_observation
apply_once "$HACKIOS_ROOT" "$ROOT/patches/fajita-loader-secondary-cache.patch" 1 "secondary-core cache scope"
apply_once "$HACKIOS_ROOT" "$ROOT/patches/fajita-loader-framebuffer-owner.patch" 1 "desktop framebuffer ownership"
python3 "$ROOT/scripts/ensure-fajita-touch-power.py" --loader "$HACKIOS_ROOT/xnuloader/loader.c"
if rg -q 'fajita_log_tail_spans\(' "$HACKIOS_ROOT/xnuloader/loader.c" && \
   rg -q 'note_touch_progress\(live, slide, noted\)' "$HACKIOS_ROOT/xnuloader/loader.c"; then
	echo "paired touch diagnostic reader already staged"
else
	apply_once "$HACKIOS_ROOT" "$ROOT/patches/fajita-loader-touch-observe.patch" 1 "opt-in touch diagnostic log visibility"
fi
ensure_gic_timer_patch "$HACKIOS_ROOT" "$HACKIOS_ROOT/xnu/files/osfmk/arm64/gic_v3_irq.c" 1
ensure_gic_timer_patch "$XNU_SRC" "$XNU_SRC/osfmk/arm64/gic_v3_irq.c" 3
python3 "$ROOT/scripts/audit-fajita-touch-dt.py"
python3 "$ROOT/scripts/ensure-fajita-touch.py" --xnu "$XNU_SRC" --extra-files "$HACKIOS_ROOT/xnu/files"
python3 "$ROOT/scripts/ensure-fajita-gpu.py" --xnu "$XNU_SRC" --extra-files "$HACKIOS_ROOT/xnu/files"
# hakios-metal is built from the hakios-xnu-kernel checkout, not from the dipper work tree.
python3 "$ROOT/scripts/ensure-fajita-metal-a630.py" --hakios "$ROOT/ref/hakios-xnu-kernel"
python3 "$ROOT/scripts/ensure-fajita-gpu-ready.py" --xnu "$XNU_SRC"
python3 "$ROOT/scripts/ensure-fajita-gpu-startup-log.py" --xnu "$XNU_SRC"
python3 "$ROOT/scripts/verify-fajita-gpu.py" --dtb "$ROOT/.work/edl-extract/boot/fajita-edl-a-0.dtb"
python3 "$ROOT/scripts/ensure-fajita-live.py" --xnu "$XNU_SRC" --extra-files "$HACKIOS_ROOT/xnu/files" --loader "$HACKIOS_ROOT/xnuloader/loader.c"
python3 "$ROOT/scripts/ensure-fajita-touch-rpmh.py" --loader "$HACKIOS_ROOT/xnuloader/loader.c"
python3 "$ROOT/scripts/ensure-fajita-touch-panel.py" --loader "$HACKIOS_ROOT/xnuloader/loader.c"
python3 "$ROOT/scripts/ensure-fajita-gpu-diag.py" --loader "$HACKIOS_ROOT/xnuloader/loader.c"
python3 "$ROOT/scripts/ensure-fajita-log-window.py" --loader "$HACKIOS_ROOT/xnuloader/loader.c"
python3 "$ROOT/scripts/verify-fajita-touch.py" --xnu "$XNU_SRC"

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
BUILD_DIR="${FAJITA_BUILD_DIR:-$ROOT/.work/build/fajita}"
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
for source in "$GIC_SOURCE" "$XNU_SRC/bsd/dev/fajita_synaptics.c" \
    "$XNU_SRC/bsd/dev/fajita_touch_transport.inc" "$XNU_SRC/bsd/dev/fajita_touch_report.h" \
    "$XNU_SRC/bsd/dev/fajita_touch_board.h" "$XNU_SRC/bsd/dev/fajita_touch_layout_probe.inc" \
    "$XNU_SRC/bsd/sys/fajita_touch_live.h" "$XNU_SRC/osfmk/arm64/loader_return.c" \
    "$XNU_SRC/osfmk/arm64/fajita_touch_live.h" \
    "$XNU_SRC/bsd/conf/files" "$XNU_SRC/bsd/dev/redwood_input.c" "$XNU_SRC/bsd/sys/redwood_input.h"; do
	if [ "$source" -nt "$KERNEL" ]; then
		echo "XNU kernel is older than $source; refusing a stale kernel" >&2
		exit 1
	fi
done
if ! llvm-nm "$KERNEL" | rg '_fajita_touch_init$' > /dev/null; then
	echo "XNU kernel lacks fajita_touch_init; refusing a kernel without the touch driver" >&2
	exit 1
fi
if [ "$XNU_STATUS" -ne 0 ] && ! rg -q 'check_all_exports' "$BUILD_DIR/xnu-build.log"; then
	echo "XNU build failed outside the documented post-link export check; see $BUILD_DIR/xnu-build.log" >&2
	exit "$XNU_STATUS"
fi
echo "XNU kernel available after incremental build: $KERNEL ($(stat -c %s "$KERNEL") bytes)"
python3 "$ROOT/scripts/verify-fajita-live.py" --kernel "$KERNEL" --loader "$HACKIOS_ROOT/xnuloader/loader.efi"
# EDK2's bundled BaseTools predate the host's GCC and treat old-code warnings as
# errors (-Werror). The reference tree stays untouched: BUILD_OPTFLAGS is
# appended after every BUILD_CFLAGS definition, so this only relaxes warnings.
# It never changes -D flags (e.g. LzmaCompress's -D_7ZIP_ST) or code generation.
BASETOOLS="$EDK2_ROOT/Common/edk2/BaseTools"
if ! make -C "$BASETOOLS" BUILD_OPTFLAGS="-Wno-error" > "$BUILD_DIR/basetools-build.log" 2>&1; then
	tail -40 "$BUILD_DIR/basetools-build.log" >&2
	exit 1
fi
if ! (cd "$EDK2_ROOT" && bash build.sh -d fajita) > "$BUILD_DIR/edk2-build.log" 2>&1; then
	tail -80 "$BUILD_DIR/edk2-build.log" >&2
	exit 1
fi
# A review can update project inputs while the firmware build is running.
# Refuse a package whose staged sources no longer match those inputs.
python3 "$ROOT/scripts/verify-fajita-source.py" --edk2 "$EDK2_ROOT" --loader "$HACKIOS_ROOT/xnuloader/loader.c"
python3 "$ROOT/scripts/verify-fajita-touch.py" --xnu "$XNU_SRC"

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
ONE_SHOT="$BUILD_DIR/boot-fajita-touch-panel.img"
python3 "$ROOT/scripts/rebuild-fajita-payload.py" \
	--loader "$HACKIOS_ROOT/xnuloader/loader.efi" --kernel "$KERNEL" \
	--flashboot "$FLASHBOOT" --ramdisk "$RAMDISK" \
		--cmdline-file "$CMDLINE_FILE" --output "$PAYLOAD"
python3 "$ROOT/scripts/repack-fajita-uefi.py" \
	--boot "$BASE_IMAGE" --uefi-image "$UEFI_PREFIX" --payload "$PAYLOAD" \
	--mkbootimg "$EDK2_ROOT/tools/mkbootimg.py" --output "$ONE_SHOT"
python3 "$ROOT/scripts/verify-fajita-boot.py" "$ONE_SHOT" \
	--uefi-prefix "$UEFI_PREFIX" \
	--dtb "$EDK2_ROOT/Platform/Oneplus/sdm845/FdtBlob_compat/fajita.dtb" \
	--loader "$HACKIOS_ROOT/xnuloader/loader.efi" --kernel "$KERNEL" \
	--flashboot "$FLASHBOOT" --ramdisk "$RAMDISK" \
		--cmdline-file "$CMDLINE_FILE"
sha256sum "$ONE_SHOT" > "$BUILD_DIR/SHA256SUMS"
echo "fajita one-shot image rebuilt and verified: $ONE_SHOT. No device operation was performed."
