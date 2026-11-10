#!/usr/bin/env python3
"""Check that the prepared kernel really contains the reviewed fajita touch inputs."""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xnu", type=Path, required=True)
    args = ap.parse_args()
    names = ("fajita_synaptics.c", "fajita_touch_transport.inc", "fajita_touch_report.h", "fajita_touch_board.h", "fajita_touch_layout_probe.inc")
    for name in names:
        if (args.xnu / "bsd/dev" / name).read_bytes() != (ROOT / "devices/fajita/kernel" / name).read_bytes():
            raise SystemExit(f"stale touch source in prepared XNU: {name}")
    if (args.xnu / 'bsd/sys/fajita_touch_live.h').read_bytes() != (ROOT / 'devices/fajita/kernel/fajita_touch_live.h').read_bytes():
        raise SystemExit('stale touch live ABI header in kernel')
    live = (args.xnu / 'osfmk/arm64/loader_return.c').read_text()
    if 'magic3 = FAJITA_LIVE_MAGIC3' not in live or '.touch_live = &fajita_touch_live' not in live:
        raise SystemExit('kernel must publish the separate touch state through the appended live ABI')
    for relative, line in (("bsd/conf/files", "bsd/dev/fajita_synaptics.c\tstandard"),
                           ("bsd/dev/redwood_input.c", "\tfajita_touch_init();")):
        if (args.xnu / relative).read_text().splitlines().count(line) != 1:
            raise SystemExit(f"missing or repeated touch integration: {relative}")
    header = (args.xnu / "bsd/sys/redwood_input.h").read_text()
    if header.count("void fajita_touch_init(void);") != 1:
        raise SystemExit("missing or repeated touch declaration")
    input_source = (args.xnu / "bsd/dev/redwood_input.c").read_text()
    if 'PE_parse_boot_argn("fajita_touch_exit"' not in input_source or 'exit_call = NULL;' not in input_source:
        raise SystemExit("fajita bring-up must opt in before enabling five-finger kernel exit")
    if input_source.index('five-finger exit disabled during bring-up') > input_source.index('\tfajita_touch_init();'):
        raise SystemExit("exit gesture must be gated before starting the touch driver")
    source = (args.xnu / "bsd/dev/fajita_synaptics.c").read_text()
    bus = (args.xnu / "bsd/dev/fajita_touch_transport.inc").read_text()
    if 'PE_parse_boot_argn("fajita_touch"' not in source or 'strncmp(id, "S3706", 5)' not in source:
        raise SystemExit("touch hardware must be opt-in and reject a non-S3706 controller")
    if re.search(r"!ft_f(?:01|12)\[", source):
        raise SystemExit("PDT base zero is a legal register address")
    board = (args.xnu / "bsd/dev/fajita_touch_board.h").read_text()
    for name, address in (("SDA", "0x03931000"), ("SCL", "0x03932000"),
                          ("IRQ_V1", "0x0397d000"), ("IRQ_V2", "0x0357d000")):
        if not re.search(rf"^#define FAJITA_TOUCH_{name}_PA {address}u$", board, re.MULTILINE):
            raise SystemExit(f"missing verified GPIO page: {name} {address}")
    if ('static const uint64_t ft_pin_pa' in bus or
            'fajita_touch_gpio_pa(layout, gpio[i])' not in bus or
            'if (ft_pin_pa[i] == 0)' not in bus):
        raise SystemExit("kernel GPIO pages must be selected by a supported firmware TLMM layout before mapping")
    if 'ft_input(FT_IRQ, FAJITA_TOUCH_ACTIVE_IRQ_CTL)' not in bus:
        raise SystemExit("IRQ must use the fajita DT active pull-up/16 mA config")
    if 'PE_parse_boot_argn("fajita_touch_inherit"' not in source or 'inherited != 1' not in source:
        raise SystemExit("touch MMIO requires explicit inherited-power probe opt-in")
    if '(power == 1 && prepared != 1)' not in source or 'PE_parse_boot_argn("fajita_touch_prepared"' not in source:
        raise SystemExit("firmware power mode must require its successful final acknowledgement")
    init = source[source.index("void fajita_touch_init(void)"):]
    if init.index('inherited != 1') > init.index("thread_call_allocate"):
        raise SystemExit("inherited-power guard must precede deferred startup allocation")
    if ('PE_parse_boot_argn("fajita_touch_tlmm"' not in init or
            'if (!fajita_bus_select_layout(layout))' not in init or
            not init.index('inherited != 1') < init.index('fajita_bus_select_layout(layout)') < init.index('thread_call_allocate')):
        raise SystemExit("firmware-ready guard must precede TLMM layout selection, which must precede deferred allocation")
    if 'kernel_thread_start' in init or 'printf(' in init:
        raise SystemExit("early touch initialization must only arm deferred startup without console formatting")
    if not init.index('clock_interval_to_deadline') < init.index('thread_call_enter_delayed'):
        raise SystemExit("touch thread creation must be deferred until the startup deadline")
    if re.search(r"0x(?:85fe0000|179e0000)|ft_avdd_on|ml_io_map_wcomb|ft_cmd_db|ft_rsc", bus):
        raise SystemExit("kernel touch transport must not access cmd-db/RPMh or reuse firmware TCS slots")
    if re.findall(r"0x[0-9a-f]{8}", bus):
        raise SystemExit("touch transport contains an unreviewed MMIO page")
    if any(token in bus for token in ('FT_VDDIO', 'FT_RESET', 'fajita_power_reset')):
        raise SystemExit("inherited-power probe must not touch reset or supply GPIOs")
    if 'fajita_bus_restore();' not in source:
        raise SystemExit("stopped touch driver must restore the original GPIO mux")
    thread = source[source.index('static void ft_thread('):source.index('void fajita_touch_init(void)')]
    deferred = source[source.index('static void ft_start_deferred('):source.index('void fajita_touch_init(void)')]
    if 'kernel_thread_start(ft_thread' not in deferred or 'thread_deallocate(thread)' not in deferred:
        raise SystemExit("deferred callback must start the input thread and release its reference")
    if 'fajita_bus_init()' in deferred or 'ft_say(' in deferred:
        raise SystemExit("deferred startup callback must not access touch hardware or format console output")
    if 'return printf(' in source:
        raise SystemExit("touch log helper must not duplicate memory logs to the kernel console")
    for marker in ('before-GPIO-mapping', 'before-page-zero', 'before-PDT-read', 'before-scan-configuration'):
        if marker not in source:
            raise SystemExit(f"missing visible touch diagnostic boundary: {marker}")
    if bus.index('before-GPIO-snapshot') > bus.index('ft_saved_ctl[i] = ft_rd'):
        raise SystemExit("GPIO snapshot boundary must precede the first peripheral read")
    if 'loader_log_line(line);' not in source:
        raise SystemExit("diagnostic markers must be published as whole lines")
    if source.index('strncmp(id, "S3706", 5)') > source.index("if (!ft_configure_scan())"):
        raise SystemExit("scan writes must follow product identification")
    identify = source[source.index("static bool ft_identify(void)"):
                      source.index("static bool ft_runtime_status(uint8_t status[2])\n{")]
    if ("ft_vendor_soft_reset()" not in identify or
            identify.index("ft_vendor_soft_reset()") > identify.index("if (!ft_configure_scan())")):
        raise SystemExit("S3706 scan setup must follow the vendor IRQ-mask/soft-reset sequence")
    if "static bool ft_vendor_soft_reset(void)" not in source or "ft_sleep_ms(100);" not in source:
        raise SystemExit("vendor S3706 soft reset must wait 100 ms before status recovery")
    # The vendor arms interrupts only after reset; masking must use one helper
    # and the re-arm must verify the controller kept the value.
    if source.count("fajita_i2c_write(ft_f01[2] + 1,") != 1:
        raise SystemExit("F01 control1 must only be written by the interrupt-mask helper")
    mask = source[source.index("static bool ft_interrupt_mask(bool enable)"):
                  source.index("static bool ft_configure_scan(void)")]
    for marker in ("fajita_i2c_read(ft_f01[3] + 1, &pending, 1)", "ft_mask_readback = check"):
        if marker not in mask:
            raise SystemExit(f"interrupt-mask helper is missing: {marker}")
    # The vendor reads this register and discards the value; a hard readback
    # equality check aborted identification on the observed controller.
    if "check == value" in mask:
        raise SystemExit("mask readback must stay telemetry, not a success condition")
    if "ft_interrupt_mask(false)" not in source or "ft_interrupt_mask(true)" not in source:
        raise SystemExit("scan setup must re-arm the interrupt mask cleared before soft reset")
    probe = source[source.index("static bool ft_probe_identity("):source.index("static bool ft_identify(void)")]
    if "ft_configure_scan" in probe or "fajita_touch_probe" not in source:
        raise SystemExit("identity probe must stop before the configuration path")
    # A bare D=3 covered twelve distinct failures; each identification step
    # must publish its own code so a no-contact run is diagnosable.
    steps = re.findall(r"ft_fail\((\d+)\)", identify)
    if len(set(steps)) != 12 or not all(int(s) in range(1, 13) for s in steps):
        raise SystemExit("every identification failure step must publish a distinct code")
    # The vendor soft-reset sequence has its own failures (13..18) that a bare
    # step 11 could not distinguish.
    soft = source[source.index("static bool ft_vendor_soft_reset(void)"):
                  source.index("static uint64_t ft_probe_pack_bytes")]
    substeps = re.findall(r"ft_fail\((\d+)\)", soft)
    if sorted(int(s) for s in substeps) != list(range(13, 19)):
        raise SystemExit("soft-reset failures must publish steps 13..18")
    if "ft_identify_fail" not in source or "0x100u | (step & 0xffu)" not in source:
        raise SystemExit("identification step codes must survive the stopped handler")
    if "FT_PHASE_PROBE_DONE" not in source or "FT_PHASE_PROBE_FAILED" not in source:
        raise SystemExit("identity probe must publish an explicit terminal phase")
    thread = source[source.index("static void ft_thread(void *arg,"):source.index("static void ft_start_deferred(")]
    if thread.index("if (ft_probe_only)") > thread.index("if (!ft_identify())"):
        raise SystemExit("identity-only mode must terminate before normal scan initialization")
    if "fajita_bus_restore();" not in thread or "FT_PHASE_PROBE_FAILED, 6" not in thread:
        raise SystemExit("identity probe must restore GPIO and report mapping failure")
    layout_probe = (args.xnu / "bsd/dev/fajita_touch_layout_probe.inc").read_text()
    if "ft_configure_scan" in layout_probe or "fajita_i2c_write" in layout_probe:
        raise SystemExit("layout probe must never write controller configuration")
    if 'if (!(q0 & 1u))' not in layout_probe or 'ft_probe_only == 2 ? ft_probe_layout' not in thread:
        raise SystemExit("layout probe must gate extended queries on Query0 and stop before scan setup")
    if "slot" not in source or "ft_release_all();" not in source or "errors >= 8" not in source:
        raise SystemExit("missing report/error release handling")
    print("touch checks passed: staged source, hooks, opt-in Product ID, firmware-ready/inherit/layout gates, three kernel GPIO pages, no kernel PMIC/reset, GPIO restore, report release")


if __name__ == "__main__":
    main()
