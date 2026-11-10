/* SPDX-License-Identifier: GPL-2.0-only
 * OnePlus 6T Synaptics S3706 / RMI touch input for HakiOS.
 * Protocol reference: OnePlusOSS/android_kernel_oneplus_sdm845,
 * 222822c2d0042067b7964c6302ef4d756dc4bed9,
 * drivers/input/touchscreen/synaptics_driver_s3320.c.
 * Own polling/input glue; no F34 firmware erase/program operation exists here.
 */
#include <kern/clock.h>
#include <kern/thread.h>
#include <kern/thread_call.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>
#include <sys/sysctl.h>
#include <sys/fajita_touch_live.h>
#include "fajita_touch_report.h"

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
extern kern_return_t thread_terminate(thread_t);
extern void loader_log_line(const char *);

struct fajita_touch_live fajita_touch_live __attribute__((aligned(64))) = {
	.magic = FAJITA_TOUCH_LIVE_MAGIC, .size = sizeof(struct fajita_touch_live),
	.telemetry_magic = FAJITA_TOUCH_TELEMETRY_MAGIC
};
static void ft_metric(volatile uint64_t *field, uint64_t value)
{
	__atomic_store_n(field, value, __ATOMIC_RELEASE);
	__asm__ volatile ("dc cvac, %0\ndsb sy" : : "r" (&fajita_touch_live) : "memory");
}
static void ft_transport_note(uint64_t value) { ft_metric(&fajita_touch_live.transport, value); }
static void ft_phase(uint32_t phase, uint32_t detail)
{
	/* Publish without formatting, log locks or peripheral access. */
	__atomic_store_n(&fajita_touch_live.state, ((uint64_t)phase << 32) | detail, __ATOMIC_RELEASE);
	__asm__ volatile ("dc cvac, %0\ndsb sy" : : "r" (&fajita_touch_live.state) : "memory");
}

/* Keep messages in the loader's in-memory log even with the screen quiet. */
static int ft_say(const char *fmt, ...) __printflike(1, 2);
static int ft_say(const char *fmt, ...)
{
	char line[256]; va_list ap;
	va_start(ap, fmt); vsnprintf(line, sizeof(line), fmt, ap); va_end(ap);
	loader_log_line(line);
	return 0;
}
#define printf ft_say
static uint32_t ft_diag_enabled, ft_start_delay_ms = 30000;
static thread_call_t ft_start_call;
static void ft_diag_step(const char *stage, uint64_t detail)
{
	if (!ft_diag_enabled) { return; }
	/* Repeat slowly so the resident loader's two-second log-tail sample can
	 * show the last completed boundary before a fatal peripheral access. */
	for (unsigned n = 0; n < 4; n++) {
		printf("fajita-touch-diag: %s detail=%#llx\n", stage, (unsigned long long)detail);
		delay_for_interval(1000, NSEC_PER_MSEC);
	}
}
#include "fajita_touch_transport.inc"

static uint8_t ft_f01[6], ft_f12[6];
static uint16_t ft_max_x, ft_max_y;
static struct fajita_touch_report ft_previous;
static uint32_t ft_poll_us = 8000;
static uint64_t ft_posted_events;
static uint32_t ft_dropped_reports; /* out-of-range objects; logged on first only */
static uint32_t ft_identify_fail;
static uint32_t ft_mask_readback; /* last F01 control+1 sample; telemetry only */
static int ft_ready, ft_error, ft_reports;
static uint32_t ft_probe_only;
SYSCTL_INT(_kern, OID_AUTO, fajita_touch_ready, CTLFLAG_RD | CTLFLAG_LOCKED, &ft_ready, 0, "S3706 scanning initialized");
SYSCTL_INT(_kern, OID_AUTO, fajita_touch_error, CTLFLAG_RD | CTLFLAG_LOCKED, &ft_error, 0, "S3706 stage that failed");
SYSCTL_INT(_kern, OID_AUTO, fajita_touch_reports, CTLFLAG_RD | CTLFLAG_LOCKED, &ft_reports, 0, "Valid S3706 touch reports");

static bool ft_page_zero(void)
{
	uint8_t page = 0;
	return fajita_i2c_write(0xff, &page, 1);
}

/* OnePlus synaptics_enable_interrupt(): clear the latched attention state by
 * reading F01 data1, then set F01 control1 to 0x7f (enable) or 0x00 (mask).
 * The vendor arms interrupts only after the IRQ is installable, and a masked
 * controller still accepts reports but raises no attention line. */
static bool ft_interrupt_mask(bool enable)
{
	uint8_t value = enable ? 0x7f : 0x00, pending, check;
	if (!ft_page_zero()) { return false; }
	if (enable && !fajita_i2c_read(ft_f01[3] + 1, &pending, 1)) { return false; }
	if (!fajita_i2c_write(ft_f01[2] + 1, &value, 1)) { return false; }
	/* The vendor driver reads the mask back and then discards the value, so
	 * only the bus outcome gates success. Do not impose a readback equality
	 * check the vendor never performs: this controller reported a mask write
	 * that did not read back as written (observed address 0x0e), and a hard
	 * comparison aborted the whole identification sequence. The sample is
	 * still published as telemetry so a real mismatch stays visible. */
	if (!fajita_i2c_read(ft_f01[2] + 1, &check, 1)) { return false; }
	ft_mask_readback = check;
	return true;
}

/* Publish which identification step failed. D=3 alone covered twelve
 * distinct failures from page selection through scan arming, which made a
 * real no-contact run indistinguishable from a bus or layout fault. The
 * published detail is 0x1N where N is the step, so the panel still reads as
 * a small number and step 0 is never ambiguous with the generic code. */
static void ft_fail(uint32_t step)
{
	/* First failure wins: an inner helper reports the precise step, and the
	 * caller's coarse "this call failed" must not overwrite it. */
	if (ft_identify_fail) { return; }
	ft_identify_fail = step;
	ft_phase(FT_PHASE_STOPPED, 0x100u | (step & 0xffu));
}

static bool ft_configure_scan(void)
{
	uint8_t mode, gesture[3];
	if (!ft_page_zero() || !fajita_i2c_read(ft_f01[2], &mode, 1)) { return false; }
	/* Normal, configured; retain charger and other unrelated control bits. */
	mode = (mode & 0xf8) | 0x80;
	if (!fajita_i2c_write(ft_f01[2], &mode, 1)) { return false; }
	/* Vendor gesture control: preserve the three bytes, clearing only bit 1 of byte 2. */
	if (!fajita_i2c_read(ft_f12[2] + 6, gesture, sizeof(gesture))) { return false; }
	gesture[2] &= (uint8_t)~2u;
	if (!fajita_i2c_write(ft_f12[2] + 6, gesture, sizeof(gesture))) { return false; }
	return ft_interrupt_mask(true);
}

static bool ft_runtime_status(uint8_t status[2]);

/* OnePlus S3706 probe sequence: mask F01 IRQs, issue the controller's
 * software reset, allow its documented 100 ms restart, then require the
 * application to leave flash/CRC/error state before scan setup continues.
 * The mask stays cleared across the reset on purpose; ft_configure_scan()
 * re-arms it afterwards, which is also where the vendor re-enables it. */
static bool ft_vendor_soft_reset(void)
{
	uint8_t reset = 1, status[2];
	if (!ft_interrupt_mask(false)) { ft_fail(13); return false; }
	if (!fajita_i2c_write(ft_f01[1], &reset, 1)) { ft_fail(14); return false; }
	ft_sleep_ms(100);
	if (!ft_page_zero()) { ft_fail(15); return false; }
	if (!ft_runtime_status(status)) { ft_fail(16); return false; }
	/* Flash-programming mode or a non-normal error state after restart. */
	if ((status[0] & 0x40u) != 0) { ft_fail(17); return false; }
	if ((status[0] & 0x0fu) > 1u) { ft_fail(18); return false; }
	return true;
}

static uint64_t ft_probe_pack_bytes(const uint8_t *bytes, size_t count)
{
	uint64_t value = 0;
	if (count > 8) { return 0; }
	for (size_t i = 0; i < count; i++) {
		value |= (uint64_t)bytes[i] << (56u - (unsigned)i * 8u);
	}
	return value;
}

static void ft_probe_publish(const uint8_t id[10], bool id_valid)
{
	ft_metric(&fajita_touch_live.transport, id_valid ? ft_probe_pack_bytes(id, 8) : 0);
	ft_metric(&fajita_touch_live.reports, id_valid ? ft_probe_pack_bytes(id + 8, 2) : 0);
	ft_metric(&fajita_touch_live.attention_active,
	    ft_probe_pack_bytes(ft_f01, sizeof(ft_f01)) >> 16);
	ft_metric(&fajita_touch_live.posted_events,
	    ft_probe_pack_bytes(ft_f12, sizeof(ft_f12)) >> 16);
}

/* Read the fixed fajita RMI page-zero descriptors and F01 Product ID only.
 * The sole controller write is the standard 0xff page selector; no scan,
 * reset, interrupt-mask, firmware or configuration register is written. */
static bool ft_probe_identity(uint32_t *result)
{
	uint8_t id[10] = {0}, status = 0xff;
	bool id_valid = false;
	*result = 1;
	if (!ft_page_zero()) { ft_probe_publish(id, false); return false; }
	ft_phase(FT_PHASE_PDT, 0x20);
	if (!fajita_i2c_read(0xe3, ft_f01, sizeof(ft_f01)) ||
	    !fajita_i2c_read(0xdd, ft_f12, sizeof(ft_f12))) {
		*result = 2; ft_probe_publish(id, false); return false;
	}
	if (ft_f01[5] != 1 || ft_f01[0] > 234 || ft_f01[3] == 0xff) {
		*result = 3; ft_probe_publish(id, false); return false;
	}
	if (!fajita_i2c_read((uint8_t)(ft_f01[0] + 11), id, sizeof(id))) {
		*result = 4; ft_probe_publish(id, false); return false;
	}
	id_valid = true;
	if (!fajita_i2c_read(ft_f01[3], &status, 1)) {
		*result = 5; ft_probe_publish(id, id_valid); return false;
	}
	ft_probe_publish(id, id_valid);
	*result = status;
	return true;
}

#include "fajita_touch_layout_probe.inc"

static bool ft_identify(void)
{
	uint8_t status = 0xff, extents[4]; char id[11] = {0};
	unsigned n;
	ft_phase(FT_PHASE_PAGE_ZERO, 0x20);
	ft_diag_step("before-page-zero", 0x20);
	if (!ft_page_zero()) { ft_fail(1); return false; }
	ft_phase(FT_PHASE_PDT, 0x20);
	ft_diag_step("before-PDT-read", 0x20);
	if (!fajita_i2c_read(0xe3, ft_f01, sizeof(ft_f01))) { ft_fail(2); return false; }
	if (!fajita_i2c_read(0xdd, ft_f12, sizeof(ft_f12))) { ft_fail(3); return false; }
	/* The fixed PDT locations are used by this specific vendor S3706 image.
	 * Reject a different function layout rather than writing guessed bases. */
	if (ft_f01[5] != 1 || ft_f12[5] != 0x12 ||
	    ft_f01[0] > 234 || ft_f01[2] > 253 ||
	    ft_f01[3] > 253 || ft_f12[2] > 246 || ft_f12[3] > 174) {
		printf("fajita-touch: incompatible F01/F12 descriptor layout\n"); ft_fail(4); return false;
	}
	if (!fajita_i2c_read(ft_f01[0] + 11, (uint8_t *)id, 10)) { ft_fail(5); return false; }
	printf("fajita-touch: Product ID %s, F01 q/c/d %#x/%#x/%#x, F12 c/d %#x/%#x\n",
	    id, ft_f01[0], ft_f01[2], ft_f01[3], ft_f12[2], ft_f12[3]);
	if (strncmp(id, "S3706", 5) != 0) { ft_fail(6); return false; }
	for (n = 0; n < 20; n++) {
		if (!fajita_i2c_read(ft_f01[3], &status, 1)) { ft_fail(7); return false; }
		if ((status & 15) != 6) { break; } /* CRC in progress */
		ft_sleep_ms(20);
	}
	if ((status & 0x40) || (status & 15) > 1) {
		printf("fajita-touch: status %#x; no firmware operation will be attempted\n", status);
		ft_fail(8); return false;
	}
	if (!fajita_i2c_read(ft_f12[2], extents, sizeof(extents))) { ft_fail(9); return false; }
	ft_max_x = extents[0] | ((uint16_t)extents[1] << 8);
	ft_max_y = extents[2] | ((uint16_t)extents[3] << 8);
	if (!ft_max_x || !ft_max_y || ft_max_x > 4095 || ft_max_y > 4095) {
		printf("fajita-touch: invalid sensor range %u x %u\n", ft_max_x, ft_max_y);
		ft_fail(10); return false;
	}
	printf("fajita-touch: sensor range %u x %u; screen 1080 x 2340, no axis flip\n", ft_max_x, ft_max_y);
	ft_phase(FT_PHASE_CONFIGURE_SCAN, ft_f12[2]);
	ft_diag_step("before-scan-configuration", ft_f12[2]);
	if (!ft_vendor_soft_reset()) { ft_fail(11); return false; }
	if (!ft_configure_scan()) { ft_fail(12); return false; }
	return true;
}

/* CRC-in-progress is transient, not a permanent input-thread failure.
 * Both the wait and its retry count are bounded. Never reconfigure while
 * flash-programming mode is asserted or CRC remains in progress. */
static bool ft_runtime_status(uint8_t status[2])
{
	for (unsigned n = 0; n < 20; n++) {
		if (!fajita_i2c_read(ft_f01[3], status, 2) || (status[0] & 0x40)) { return false; }
		if ((status[0] & 15u) != 6u) { return true; }
		ft_sleep_ms(20);
	}
	return false;
}

static void ft_publish(const struct fajita_touch_report *next)
{
	for (unsigned i = 0; i < 10; i++) {
		uint16_t bit = (uint16_t)(1u << i);
		bool old = (ft_previous.active_mask & bit) != 0, now = (next->active_mask & bit) != 0;
		if (old && !now) {
			redwood_touch_post(REDWOOD_TOUCH_UP, i, ft_previous.contact[i].x, ft_previous.contact[i].y);
			if (ft_posted_events != UINT64_MAX) { ft_posted_events++; }
			ft_metric(&fajita_touch_live.posted_events, ft_posted_events);
		} else if (now && !old) {
			redwood_touch_post(REDWOOD_TOUCH_DOWN, i, next->contact[i].x, next->contact[i].y);
			if (ft_posted_events != UINT64_MAX) { ft_posted_events++; }
			ft_metric(&fajita_touch_live.posted_events, ft_posted_events);
		} else if (now && (ft_previous.contact[i].x != next->contact[i].x || ft_previous.contact[i].y != next->contact[i].y)) {
			redwood_touch_post(REDWOOD_TOUCH_MOVE, i, next->contact[i].x, next->contact[i].y);
			if (ft_posted_events != UINT64_MAX) { ft_posted_events++; }
			ft_metric(&fajita_touch_live.posted_events, ft_posted_events);
		}
	}
	ft_previous = *next;
}
static void ft_release_all(void)
{
	const struct fajita_touch_report empty = {0};
	ft_publish(&empty);
}

static bool ft_poll_report(void)
{
	uint8_t status[2], attention[2], bytes[81];
	size_t slots;
	struct fajita_touch_report frame;
	if (!ft_page_zero() || !ft_runtime_status(status)) { return false; }
	if ((status[0] & 0x80) || ((status[1] & 0x7f) == 1)) {
		ft_release_all(); return ft_configure_scan();
	}
	if ((status[0] & 15) > 1) { return false; }
	/* The timer also polls when no IRQ is delivered: this driver never installs
	 * a GIC handler, and an asserted-low IRQ must not be required for releases. */
	if (!fajita_i2c_read(0x0b, attention, sizeof(attention))) { return false; }
	uint16_t mask = attention[0] | ((uint16_t)attention[1] << 8);
	if (!fajita_touch_slot_count(mask, &slots)) { return false; }
	if (slots && !fajita_i2c_read(ft_f12[3], bytes, slots * 8 + 1)) { return false; }
	if (!fajita_touch_decode(mask, slots ? bytes : NULL, slots * 8, ft_max_x, ft_max_y, &frame)) { return false; }
	/* A coordinate outside the sensor range drops only that object. Report it
	 * once per frame so a genuine range/tuning mismatch stays visible without
	 * discarding the contacts that did decode. */
	if (frame.dropped && ft_dropped_reports != 0xffffffffu) {
		ft_dropped_reports += frame.dropped;
		if (ft_dropped_reports == frame.dropped) {
			printf("fajita-touch: dropped %u out-of-range object(s) of %u in attention %#x\n",
			    (unsigned)frame.dropped, (unsigned)frame.slots, (unsigned)mask);
		}
	}
	ft_publish(&frame);
	ft_metric(&fajita_touch_live.attention_active,
	    ((uint64_t)frame.active_mask << 16) | mask);
	if (ft_reports < 0x7fffffff) { ft_reports++; }
	ft_metric(&fajita_touch_live.reports, (uint32_t)ft_reports);
	return true;
}

static void ft_thread(void *arg, wait_result_t result)
{
	(void)arg; (void)result;
	ft_phase(FT_PHASE_THREAD_ENTERED, 0);
	loader_log_line("fajita-touch-phase: input thread entered after deferred startup\n");
	ft_phase(FT_PHASE_BEFORE_MAPPING, 0);
	ft_diag_step("before-GPIO-mapping", 0);
	ft_error = 1;
	if (!fajita_bus_init()) {
		if (ft_probe_only) {
			fajita_bus_restore();
			ft_diag_enabled = 0;
			ft_phase(ft_probe_only == 2 ? FT_PHASE_LAYOUT_FAILED : FT_PHASE_PROBE_FAILED, 6);
			thread_terminate(current_thread());
			return;
		}
		goto stopped;
	}
	/* Probe the prepared/inherited controller. Supply/reset remain loader-owned. */
	ft_sleep_ms(10);
	if (ft_probe_only) {
		uint32_t probe_result = 0;
		bool identified = ft_probe_only == 2 ? ft_probe_layout(&probe_result) :
		    ft_probe_identity(&probe_result);
		ft_ready = 0; fajita_bus_restore();
		ft_diag_enabled = 0;
		ft_phase(ft_probe_only == 2 ?
		    (identified ? FT_PHASE_LAYOUT_DONE : FT_PHASE_LAYOUT_FAILED) :
		    (identified ? FT_PHASE_PROBE_DONE : FT_PHASE_PROBE_FAILED), probe_result);
		printf("fajita-touch-probe: %s (result %#x); no scan configuration issued\n",
		    identified ? "identity captured" : "identity read failed", probe_result);
		thread_terminate(current_thread());
		return;
	}
	ft_error = 3;
	if (!ft_identify()) { goto stopped; }
	ft_ready = 1; ft_error = 0;
	ft_phase(FT_PHASE_SCANNING, ft_poll_us);
	printf("fajita-touch: S3706 scanning ready, polling %u us\n", ft_poll_us);
	for (unsigned idle = 0, errors = 0;;) {
		if (fajita_irq_pending() || ft_previous.active_mask || ++idle >= 10) {
			idle = 0;
			if (ft_poll_report()) { errors = 0; ft_phase(FT_PHASE_SCANNING, ft_poll_us); }
			else if (++errors >= 8) { ft_error = 4; break; }
		}
		delay_for_interval(ft_poll_us, NSEC_PER_USEC);
	}
stopped:
	ft_ready = 0; ft_release_all();
	/* GPIO restoration still writes TLMM registers, and diagnostic mode
	 * publishes every access. Publish the terminal state only after the mux
	 * is restored, otherwise the last GPIO address overwrites the failure
	 * code and the panel shows a register address instead. */
	fajita_bus_restore();
	ft_diag_enabled = 0;
	if (ft_error == 3 && ft_identify_fail) {
		ft_phase(FT_PHASE_STOPPED, 0x100u | (ft_identify_fail & 0xffu));
		printf("fajita-touch: stopped at identification step %u (see touchscreen.md)\n",
		    (unsigned)ft_identify_fail);
	} else {
		ft_phase(FT_PHASE_STOPPED, (uint32_t)ft_error);
		printf("fajita-touch: stopped at stage %d (1 map, 3 identify/scan, 4 report); GPIO mux restored\n", ft_error);
	}
	thread_terminate(current_thread());
}

static void ft_start_deferred(thread_call_param_t arg, thread_call_param_t unused)
{
	thread_t thread;
	(void)arg; (void)unused;
	/* Never hold up bsd_init/devfs initialization for touch bring-up. */
	ft_phase(FT_PHASE_CREATE_THREAD, 0);
	loader_log_line("fajita-touch-phase: deferred startup reached; creating input thread\n");
	if (kernel_thread_start(ft_thread, NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
		loader_log_line("fajita-touch-phase: input thread creation returned successfully\n");
	} else {
		ft_error = 5;
		ft_phase(FT_PHASE_SKIPPED, 5);
		loader_log_line("fajita-touch-phase: input thread creation failed; touch skipped\n");
	}
}

void fajita_touch_init(void)
{
	uint32_t enabled = 0, interval = 0, inherited = 0, diagnostic = 0, startup_delay = 0;
	uint32_t power = 0, prepared = 0, layout = 0, probe = 0;
	uint64_t deadline;
	if (!PE_parse_boot_argn("fajita_touch", &enabled, sizeof(enabled)) || enabled != 1) { return; }
	if (ft_start_call != NULL) { return; }
	ft_phase(FT_PHASE_INIT, 0);
	/* Firmware power mode requires its FINAL appended acknowledgement.
	 * The explicit legacy inherited-power mode is kept for old controls. */
	PE_parse_boot_argn("fajita_touch_power", &power, sizeof(power));
	PE_parse_boot_argn("fajita_touch_prepared", &prepared, sizeof(prepared));
	PE_parse_boot_argn("fajita_touch_inherit", &inherited, sizeof(inherited));
	if ((power == 1 && prepared != 1) || (power != 1 && inherited != 1)) {
		ft_error = 6;
		ft_phase(FT_PHASE_SKIPPED, 6);
		loader_log_line("fajita-touch-phase: skipped; firmware preparation failed or inherited probe not requested\n");
		return;
	}
	/* Firmware and kernel must use the same silicon-specific TLMM tiles.
	 * This also requires an explicit layout for legacy inherited probes. */
	PE_parse_boot_argn("fajita_touch_tlmm", &layout, sizeof(layout));
	if (!fajita_bus_select_layout(layout)) {
		ft_error = 7;
		ft_phase(FT_PHASE_SKIPPED, 7);
		loader_log_line("fajita-touch-phase: skipped; missing or unsupported TLMM layout\n");
		return;
	}
	if (PE_parse_boot_argn("fajita_touch_diag", &diagnostic, sizeof(diagnostic)) && diagnostic == 1) {
		ft_diag_enabled = 1;
	}
	if (PE_parse_boot_argn("fajita_touch_probe", &probe, sizeof(probe)) &&
	    (probe == 1 || probe == 2)) {
		ft_probe_only = probe;
	}
	if (PE_parse_boot_argn("fajita_touch_delay_ms", &startup_delay, sizeof(startup_delay)) &&
	    startup_delay >= 1000 && startup_delay <= 60000) {
		ft_start_delay_ms = startup_delay;
	}
	if (PE_parse_boot_argn("fajita_touch_poll_us", &interval, sizeof(interval)) && interval >= 2000 && interval <= 20000) {
		ft_poll_us = interval;
	}
	/* The tested touch=0 image reaches the desktop. Preserve that early init
	 * order: schedule only, and create the input thread after this deadline. */
	ft_start_call = thread_call_allocate(ft_start_deferred, NULL);
	if (ft_start_call == NULL) {
		ft_error = 5;
		ft_phase(FT_PHASE_SKIPPED, 5);
		loader_log_line("fajita-touch-phase: could not allocate deferred startup; touch skipped\n");
		return;
	}
	clock_interval_to_deadline(ft_start_delay_ms, NSEC_PER_MSEC, &deadline);
	ft_phase(FT_PHASE_DEFERRED_ARMED, ft_start_delay_ms);
	thread_call_enter_delayed(ft_start_call, deadline);
	loader_log_line("fajita-touch-phase: deferred startup armed; no touch hardware accessed\n");
}
