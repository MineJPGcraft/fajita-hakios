#!/usr/bin/env python3
"""Check fajita boot ordering, payload reservation and UFS handoff before building."""
import argparse
import re
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP = "Platform/Oneplus/sdm845/Library/PlatformMemoryMapLib/PlatformMemoryMapLib"
DRIVER = "XnuLoaderPkg/Driver/XnuLoaderDxe/XnuLoaderDxe.c"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--edk2", required=True, type=Path)
    ap.add_argument("--loader", required=True, type=Path)
    args = ap.parse_args()
    overlay = ROOT / "devices/fajita/uefi/files"
    for relative in (MAP + ".c", MAP + ".inf", DRIVER,
                     "XnuLoaderPkg/Driver/XnuLoaderDxe/XnuLoaderDxe.inf"):
        if (args.edk2 / relative).read_bytes() != (overlay / relative).read_bytes():
            raise SystemExit(f"staged UEFI source differs from project overlay: {relative}")
    dxe = (args.edk2 / DRIVER).read_text()
    if "gBS->StartImage" in dxe or "gBS->LoadImage" in dxe:
        raise SystemExit("loader must be launched by BDS, not DXE dispatch")
    dsc = (args.edk2 / "Platform/Oneplus/sdm845/fajita.dsc").read_text()
    if "PlatformMemoryMapLib|" + MAP + ".inf" not in dsc:
        raise SystemExit("fajita DSC does not select its reserved-payload memory map")
    memory = (args.edk2 / (MAP + ".c")).read_text()
    rows = re.findall(r'\{"([^"]+)",\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),'
                      r'\s*(\w+),\s*\w+,\s*\w+,\s*(\w+),\s*\w+\}', memory)
    for tag, end in (("Mem4G", 0x17DFA0000), ("Mem6G", 0x1FAE00000),
                     ("Mem8G", 0x180000000), ("Mem10G", 0x2F4AC0000)):
        profile = sorted((int(a, 16), int(n, 16), typ) for _, a, n, t, typ in rows
                         if t == tag and int(a, 16) < end)
        cursor = 0xD0000000
        reserved = []
        for start, length, typ in profile:
            if start != cursor or length <= 0 or typ not in {"Conv", "Reserv"}:
                raise SystemExit(f"{tag}: gap, overlap or unexpected memory type at {start:#x}")
            cursor += length
            if typ == "Reserv":
                reserved.append((start, length))
        if cursor != end or reserved != [(0xF0000000, 0x04000000)]:
            raise SystemExit(f"{tag}: RAM endpoint or 64 MiB payload reservation changed")
        print(f"{tag}: reserved 0xf0000000..0xf4000000; RAM endpoint preserved")
    loader = args.loader.read_text()
    for source in (ROOT / 'devices/fajita/kernel/fajita_touch_live.h',
                   ROOT / 'devices/fajita/loader/fajita_log_tail.h'):
        if (args.loader.parent / source.name).read_bytes() != source.read_bytes():
            raise SystemExit(f'stale loader diagnostic header: {source.name}')
    if 'fajita_log_tail_spans(' not in loader or 'note_touch_progress(live, slide, noted)' not in loader:
        raise SystemExit('touch diagnostics must use the chronological ring tail and independent state')
    if 'live->magic3 != FAJITA_LIVE_MAGIC3' not in loader or 'sizeof(struct kernel_live) == 120' not in loader:
        raise SystemExit('loader must validate the paired, append-only touch live ABI')
    if any(marker in loader for marker in ('#include "fajita_touch_power.inc"',
                                           'fajita_touch_power_prepare(pi.cmdline)',
                                           'fajita_touch_vreg=')):
        raise SystemExit("failed firmware touch power experiment must be absent from the loader")
    for relative in ("loader/fajita_touch_rpmh.inc", "loader/fajita_cmd_db.h",
                     "loader/fajita_rpmh_transaction.h", "loader/fajita_chipinfo.h", "kernel/fajita_touch_board.h",
                     "kernel/fajita_touch_power_seq.h"):
        source = ROOT / "devices/fajita" / relative
        if (args.loader.parent / source.name).read_bytes() != source.read_bytes():
            raise SystemExit(f"stale pre-handoff touch power source: {source.name}")
    include = '#include "fajita_touch_rpmh.inc"'
    prepare = "int fajita_touch_prepared = fajita_touch_prepare_before_handoff(pi.cmdline, this_mpidr, kernel_mpidr);"
    ack = 'const char *ack = " fajita_touch_prepared=0 fajita_touch_tlmm=0";'
    if any(loader.count(marker) != 1 for marker in (include, prepare, ack)):
        raise SystemExit("touch power integration must include exactly one preparation and acknowledgement")
    if not loader.index(prepare) < loader.index('/* --- hand off'):
        raise SystemExit("firmware touch preparation must precede handoff and CPU_ON")
    cmdline_copy = '\tfor (const char *c = pi.cmdline; *c && n < BOOT_LINE_LENGTH - 1; c++) {'
    if not loader.index(cmdline_copy) < loader.index(ack) < loader.index('ba->commandLine[n] = 0;', loader.index(ack)):
        raise SystemExit("firmware acknowledgement must be appended after the payload command line")
    if 'n + str_len(ack) + 1 > BOOT_LINE_LENGTH' not in loader:
        raise SystemExit("touch acknowledgement requires a command line bound check")
    power = (args.loader.parent / "fajita_touch_rpmh.inc").read_text()
    # The BSP's EFIChipInfo.h macro disagrees with its DEC symbol and
    # installed binary. Check the actual build input, not that stale macro.
    dec = (args.edk2 / "Silicon/Qualcomm/QcomPkg/QcomPkg.dec").read_text()
    declared = re.findall(r'^\s*gEfiChipInfoProtocolGuid\s*=\s*(\{[^\n]+\})', dec, re.M)
    requested = re.findall(r'static const efi_guid guid\s*=\s*(\{[^;]+\})\s*;', power)
    if len(declared) != 1 or len(requested) != 1:
        raise SystemExit("ChipInfo GUID declarations must be unambiguous")
    def guid_bytes(value):
        fields = [int(v, 16) for v in re.findall(r'0x[0-9a-fA-F]+', value)]
        if len(fields) != 11:
            raise SystemExit("invalid ChipInfo GUID initializer")
        return struct.pack('<IHH8B', *fields)
    chip_guid = guid_bytes(declared[0])
    if guid_bytes(requested[0]) != chip_guid:
        raise SystemExit("loader ChipInfo GUID differs from actual BSP DEC symbol")
    chip_binary = args.edk2 / "Platform/EFI_Binaries/Drivers/sdm845/ChipInfo/ChipInfo.efi"
    if chip_guid not in chip_binary.read_bytes():
        raise SystemExit("BSP ChipInfo binary does not contain its declared protocol GUID")
    for layout in (1, 2):
        if f'ack = " fajita_touch_prepared=1 fajita_touch_tlmm={layout}";' not in loader:
            raise SystemExit("final acknowledgement must carry the selected TLMM layout")
    if power.index('ctx.tlmm_layout = fp_chip_layout();') > power.index('fp_read(FAJITA_TOUCH_CMD_DB_DICT_PA)'):
        raise SystemExit("chip layout must be validated before any touch MMIO")
    if 'fajita_power_status = 0x80080000u | (fajita_power_chip_detail & 0xffffu);' not in power:
        raise SystemExit("unknown silicon must skip touch before MMIO")
    critical = power[power.index('tpl = raise(31);'):power.index('restore(tpl);')]
    if 'fp_mask_interrupts();' not in critical or 'fp_hold(ctx->tcs);' not in critical or 'print(' in critical:
        raise SystemExit("uncertain RPMh operations must hold without firmware console or cleanup")
    held = power[power.index('static _Noreturn void fp_hold('):power.index('static bool fp_delay_us(')]
    if 'screen_quiet = 1;' not in held or held.index('screen_quiet = 1;') > held.index('print('):
        raise SystemExit("held RPMh report must suppress all UEFI console calls")
    if power.index('if (!fp_timer_ready())') > power.index('fp_read(FAJITA_TOUCH_CMD_DB_DICT_PA)'):
        raise SystemExit("architectural timer must be checked before touch MMIO")
    if 'if (!fajita_touch_panel_requested && fajita_touch_diag_requested && noted <= 120) { screen_quiet = 0; }' not in loader or 'screen_quiet = restore_quiet;' not in loader:
        raise SystemExit("touch diagnostics require opt-in, time limit and framebuffer-quiet restoration")
    for name in ("fajita_touch_panel.h", "fajita_touch_panel.inc"):
        if (args.loader.parent / name).read_bytes() != (ROOT / "devices/fajita/loader" / name).read_bytes():
            raise SystemExit(f"stale small status panel: {name}")
    if 'if (fajita_touch_panel_requested) { fajita_touch_panel_update(live, slide); }' not in loader:
        raise SystemExit("persistent panel must be refreshed without firmware console output")
    if "ufs_requested >= 2 && kernel_mpidr == this_mpidr && checkpoint != CHECKPOINT_SELFTEST" not in loader:
        raise SystemExit("loader lacks same-core UFS handoff guard")
    if "if (!pi.oneshot && usb_start())" not in loader or "if (USB == NULL)" not in loader:
        raise SystemExit("loader one-shot path no longer bypasses USB protocol use")
    exception = loader[loader.index("exception_handler(int64_t type, uint64_t *ctx)"):loader.index("static void\ninstall_exception_hook")]
    if "UEFI fault held" not in exception or exception.index("UEFI fault held") > exception.index("reset_system"):
        raise SystemExit("no-log UEFI exception report would be lost to an immediate reset")
    handoff = loader[loader.index("run_on_other_cpu(uint64_t mpidr"):loader.index("#define VIRT_WDT_CONTROL")]
    if "masking firmware-core interrupts before PSCI" not in handoff or handoff.index("watching_only = 1;") > handoff.index("status = psci_call(PSCI_CPU_ON"):
        raise SystemExit("no-log firmware IRQ isolation does not precede secondary-core launch")
    cache = loader[loader.index('print("handoff: UEFI context saved, preparing caches'):loader.index("if (checkpoint == CHECKPOINT_SELFTEST) {", loader.index("/* --- hand off"))]
    guard = cache.index("if (kernel_mpidr == this_mpidr) {")
    secondary = cache.index("} else {")
    flush = cache.index("flush_dcache(phys_base, top);")
    if not guard < cache.index("BS->get_memory_map") < cache.index("clean_dcache(sp_now") < secondary < flush:
        raise SystemExit("firmware map/stack cache sweep must be restricted to same-core handoff")
    if "clean_dcache(" in cache[secondary:] or "BS->get_memory_map" in cache[secondary:]:
        raise SystemExit("secondary-core handoff unexpectedly touches firmware cache ranges")
    if flush > cache.index('print("handoff: cache preparation complete'):
        raise SystemExit("cache-ready marker must follow the XNU range flush")
    for symbol in ("other_cpu_start", "other_cpu_stage", "other_cpu_done", "psci_by_hvc", "other_cpu_entry", "loader_ctx"):
        if not re.search(r"clean_dcache\([^;]*\b" + symbol + r"\b[^;]*;", handoff):
            raise SystemExit(f"secondary-core bootstrap publication missing: {symbol}")
    arrival = handoff[handoff.index("if (arrived && PLAT->no_log_write) {"):handoff.index("if (!arrived && seconds_since")]
    if "screen_quiet = 1;" not in arrival or "int saved_screen_quiet = screen_quiet;" not in handoff or "screen_quiet = saved_screen_quiet;" not in handoff:
        raise SystemExit("loader must release the framebuffer after secondary arrival and restore it on return")
    if "screen_quiet = 0;" not in exception or exception.index("screen_quiet = 0;") > exception.index("print("):
        raise SystemExit("UEFI exception must reclaim the screen before printing its report")
    print("source checks passed: BDS entry, payload map, UFS guard, USB bypass, fault hold, IRQ isolation, cache scope/publication, framebuffer ownership, staged pre-CPU_ON power and final acknowledgement")


if __name__ == "__main__":
    main()
