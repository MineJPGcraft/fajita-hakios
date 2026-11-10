/* SPDX-License-Identifier: GPL-2.0-only
 * Fajita-only, synchronous board preparation on the UEFI CPU BEFORE CPU_ON.
 * DT: cmd-db dictionary, DRV2 resources and active TCS geometry. Vendor
 * cmd-db.c/rpmh-regulator.c/qcom-rpmh-mailbox.c define the wire operations.
 * No Vreg/SPMI LDO-number guess, kernel RPMh writer, or USB operation.
 */
#include "fajita_touch_power_seq.h"
#include "fajita_cmd_db.h"
#include "fajita_rpmh_transaction.h"
#include "fajita_chipinfo.h"

struct fajita_power_context {
	uint32_t resource, tcs, rpmh_result;
	uint32_t tlmm_layout;
};
/* Diagnostic outcome only. 1=prepared; 0=not requested; 0x800nxxxx=skip.
 * Does not change the firmware acknowledgement or authorize kernel access. */
static uint32_t fajita_power_status;
static uint32_t fajita_power_tlmm_layout;
/* Low sixteen P bits explain the chip metadata gate. Zero is reserved for
 * an unspecified failure; 1..8 identify the exact rejected query step. */
static uint32_t fajita_power_chip_detail;
/* reason[63:56], slot[55:48], offset[47:32], observed value[31:0].
 * Diagnostic only; never authorizes the kernel prepared gate. */
static uint64_t fajita_power_rpmh_detail;
typedef uint64_t (EFIAPI *fajita_raise_tpl_fn)(uint64_t);
typedef void (EFIAPI *fajita_restore_tpl_fn)(uint64_t);
_Static_assert(sizeof(fajita_raise_tpl_fn) == sizeof(BS->raise_tpl), "UEFI TPL ABI");

static uint32_t fp_read(uint64_t pa)
{
	return *(volatile uint32_t *)(uintptr_t)pa;
}
static void fp_write(uint64_t pa, uint32_t value)
{
	*(volatile uint32_t *)(uintptr_t)pa = value;
	__asm__ volatile ("dsb sy" ::: "memory");
}
static uint64_t fp_frequency(void) { return READ_SYSREG(CNTFRQ_EL0); }
static uint64_t fp_counter(void) { return READ_SYSREG(CNTVCT_EL0); }
static uint64_t fp_daif(void) { return READ_SYSREG(DAIF); }
static void fp_mask_interrupts(void)
{
	__asm__ volatile ("msr daifset, #3\nisb" ::: "memory");
}
static void fp_restore_interrupts(uint64_t daif)
{
	__asm__ volatile ("msr daif, %0\nisb" : : "r" (daif) : "memory");
}
static const volatile void *fp_database(uint32_t pa)
{
	return (const volatile void *)(uintptr_t)pa;
}
static bool fp_timer_ready(void)
{
	uint64_t frequency = fp_frequency(), start = fp_counter();
	if (!frequency || frequency > 1000000000u) { return false; }
	for (unsigned n = 0; n < 1024; n++) {
		if (fp_counter() != start) { return true; }
	}
	return false;
}
/* No firmware console, Boot Service, cleanup or return is allowed at TPL31
 * after an uncertain transaction. Keep an in-memory report and a red band
 * in the already mapped framebuffer. Neither path invokes a UEFI protocol.
 */
static _Noreturn void fp_hold(unsigned tcs)
{
	fajita_power_status = 0xf0000000u | (tcs & 0xffu);
	screen_quiet = 1;
	print("fajita-power: RPMh transaction uncertain, TCS %d; handoff HELD\n", (uint64_t)tcs);
	clean_dcache((uint64_t)(uintptr_t)log_buf,
	    (uint64_t)(uintptr_t)log_buf + sizeof(log_buf));
	if (SCREEN.base && SCREEN.width && SCREEN.width <= 4096 &&
	    SCREEN.height >= 16 && SCREEN.height <= 8192 &&
	    SCREEN.stride >= SCREEN.width * 4u && SCREEN.stride <= 32768) {
		uint64_t begin = SCREEN.base + (uint64_t)(SCREEN.height - 16) * SCREEN.stride;
		for (unsigned y = 0; y < 16; y++) {
			volatile uint32_t *pixels = (volatile uint32_t *)(uintptr_t)(begin + (uint64_t)y * SCREEN.stride);
			for (unsigned x = 0; x < SCREEN.width; x++) { pixels[x] = 0x00ff0000u; }
		}
		clean_dcache(begin, begin + (uint64_t)16 * SCREEN.stride);
	}
	/* CPU_ON has not happened, so the resident refresh loop cannot display
	 * the diagnostic. Draw it once here using validated framebuffer memory;
	 * the reader makes no firmware/USB call and live=NULL avoids XNU memory. */
	fajita_touch_panel_update(NULL, 0);
	for (;;) { __asm__ volatile ("wfe"); }
}
static bool fp_delay_us(void *ctx, unsigned us)
{
	uint64_t frequency = fp_frequency(), start = fp_counter();
	(void)ctx;
	if (!frequency || frequency > 1000000000u || us > 100000u) { return false; }
	uint64_t ticks = (frequency * us + 999999u) / 1000000u;
	/* A stopped counter must not turn a bounded RPMh timeout into a hang. */
	for (unsigned n = 0; n < 10000000u; n++) {
		if (fp_counter() - start >= ticks) { return true; }
	}
	return false;
}
static bool fp_wait_ms(void *ctx, unsigned ms)
{
	return ms <= 100 && fp_delay_us(ctx, ms * 1000u);
}
static uint32_t fp_rsc_read(void *ctx, uint32_t offset)
{
	(void)ctx;
	return fp_read(FAJITA_TOUCH_RSC_TCS_PA + offset);
}
static void fp_rsc_write(void *ctx, uint32_t offset, uint32_t value)
{
	(void)ctx;
	fp_write(FAJITA_TOUCH_RSC_TCS_PA + offset, value);
}
/* Metadata only: no pin, PMIC, RSC or USB operation. Unknown firmware or
 * silicon must leave touch disabled rather than probe alternative tiles. */
static uint32_t fp_chip_layout(void)
{
	/* The header macro is stale in this BSP. Use the protocol actually
	 * declared in its QcomPkg.dec and installed by its ChipInfo.efi. */
	static const efi_guid guid = { 0xb0760469, 0x970c, 0x487a,
	    { 0xa4, 0xb5, 0x28, 0xdb, 0x7b, 0x45, 0xce, 0xf1 } };
	struct fajita_chipinfo_protocol *chip = NULL;
	uint32_t id = 0, version = 0;
	fajita_power_chip_detail = 0;
	if (!BS->locate_protocol) { fajita_power_chip_detail = 1; return 0; }
	if (EFI_ERROR(BS->locate_protocol(&guid, NULL, (void **)&chip))) {
		fajita_power_chip_detail = 2; return 0;
	}
	if (!chip) { fajita_power_chip_detail = 3; return 0; }
	if (chip->revision < 0x10000u || (chip->revision >> 16) != 1u) {
		fajita_power_chip_detail = 4; return 0;
	}
	if (!chip->get_chip_version || !chip->get_chip_id) {
		fajita_power_chip_detail = 5; return 0;
	}
	if (EFI_ERROR(chip->get_chip_id(chip, &id))) { fajita_power_chip_detail = 6; return 0; }
	if (EFI_ERROR(chip->get_chip_version(chip, &version))) { fajita_power_chip_detail = 7; return 0; }
	uint32_t layout = fajita_touch_layout_for_chip(id, version);
	if (!layout) { fajita_power_chip_detail = 8; }
	print("fajita-power: chip %x version %x, TLMM layout %d\n",
	    (uint64_t)id, (uint64_t)version, (uint64_t)layout);
	return layout;
}
static bool fp_pin_output(uint32_t pa, bool high, uint32_t control)
{
	/* Only callers with a selected silicon layout may pass one of these
	 * output pins. Never turn a failed selection into a zero-page access. */
	if (pa != FAJITA_TOUCH_RESET_PA && pa != FAJITA_TOUCH_VDDIO_V1_PA &&
	    pa != FAJITA_TOUCH_VDDIO_V2_PA) { return false; }
	uint32_t desired = (control & 0x3ffu) | (1u << 9);
	uint32_t io = fp_read(pa + 4u), ctl = fp_read(pa);
	fp_write(pa + 4u, (io & ~2u) | (high ? 2u : 0u));
	fp_write(pa, (ctl & ~0x3ffu) | desired);
	/* Bit 1 is the programmed output latch. Bit 0 is an independent input
	 * sample, not proof of whether the output register accepted this write. */
	return (fp_read(pa) & 0x3ffu) == desired &&
	    (fp_read(pa + 4u) & 2u) == (high ? 2u : 0u);
}
static bool fp_reset_set(void *ctx, bool high)
{
	struct fajita_power_context *power = ctx;
	print("fajita-power: reset %d\n", (uint64_t)high);
	return fp_pin_output(fajita_touch_gpio_pa(power->tlmm_layout,
	    FAJITA_TOUCH_RESET_GPIO), high, 0);
}
static bool fp_vddio_set(void *ctx, bool high)
{
	struct fajita_power_context *power = ctx;
	print("fajita-power: GPIO88 %d\n", (uint64_t)high);
	return fp_pin_output(fajita_touch_gpio_pa(power->tlmm_layout,
	    FAJITA_TOUCH_VDDIO_GPIO), high, 0);
}
static bool fp_active_pins(void *ctx)
{
	struct fajita_power_context *power = ctx;
	uint32_t reset = fajita_touch_gpio_pa(power->tlmm_layout, FAJITA_TOUCH_RESET_GPIO);
	uint32_t irq = fajita_touch_gpio_pa(power->tlmm_layout, FAJITA_TOUCH_IRQ_GPIO);
	if (!reset || !irq) { return false; }
	print("fajita-power: active pinctrl\n");
	if (!fp_pin_output(reset, true, FAJITA_TOUCH_ACTIVE_IRQ_CTL)) { return false; }
	fp_write(irq, (fp_read(irq) & ~0x3ffu) | FAJITA_TOUCH_ACTIVE_IRQ_CTL);
	return (fp_read(irq) & 0x3ffu) == FAJITA_TOUCH_ACTIVE_IRQ_CTL;
}
static bool fp_avdd_enable(void *opaque, uint32_t uv, uint32_t load)
{
	struct fajita_power_context *ctx = opaque;
	const struct fajita_rpmh_ops ops = {fp_rsc_read, fp_rsc_write, fp_delay_us};
	fajita_raise_tpl_fn raise;
	fajita_restore_tpl_fn restore;
	uint64_t tpl, daif;
	if (uv != FAJITA_TOUCH_AVDD_UV || load != FAJITA_TOUCH_AVDD_LOAD_UA ||
	    !BS->raise_tpl || !BS->restore_tpl) { return false; }
	memcpy(&raise, &BS->raise_tpl, sizeof(raise));
	memcpy(&restore, &BS->restore_tpl, sizeof(restore));
	print("fajita-power: RPMh ldoa28 %x, %d uV, %d uA\n",
	    (uint64_t)ctx->resource, (uint64_t)uv, (uint64_t)load);
	/* UEFI is single-threaded here, XNU has not been started. Raise TPL AND
	 * mask this CPU's IRQ/FIQ; no Boot Service is called inside the lease.
	 * Slot idle checks alone would not exclude firmware callbacks. */
	daif = fp_daif();
	tpl = raise(31); /* TPL_HIGH_LEVEL */
	fp_mask_interrupts();
	struct fajita_rpmh_diagnostic diagnostic;
	enum fajita_rpmh_result result = fajita_rpmh_avdd_transaction_diagnostic(&ops, ctx,
	    ctx->resource, &ctx->tcs, &diagnostic);
	fajita_power_rpmh_detail = ((uint64_t)(diagnostic.reason & 0xffu) << 56) |
	    ((uint64_t)(diagnostic.slot & 0xffu) << 48) |
	    ((uint64_t)(diagnostic.offset & 0xffffu) << 32) | diagnostic.value;
	ctx->rpmh_result = result;
	if (result == FAJITA_RPMH_FATAL) {
		/* Do not return into generic power cleanup (which prints through
		 * firmware). Never restore TPL/interrupts while a slot is uncertain. */
		fp_hold(ctx->tcs);
	}
	restore(tpl);
	fp_restore_interrupts(daif);
	print("fajita-power: RPMh result %d, TCS %d\n", (uint64_t)result, (uint64_t)ctx->tcs);
	return result == FAJITA_RPMH_READY;
}

static int fp_arg(const char *line, const char *name)
{
	int value = 0;
	size_t length = str_len(name);
	for (const char *p = line; *p; p++) {
		if ((p == line || p[-1] == ' ') && starts_with(p, name) &&
		    (p[length] == '0' || p[length] == '1') &&
		    (p[length + 1] == 0 || p[length + 1] == ' ')) {
			value = p[length] == '1';
		}
	}
	return value;
}

/* 1 ready; 0 opt-out/ordinary failure; fatal uncertain transactions HOLD. */
static int fajita_touch_prepare_before_handoff(const char *line, uint64_t this_mpidr,
    uint64_t kernel_mpidr)
{
	struct fajita_cmd_db_resource resource;
	struct fajita_power_context ctx = {0};
	const struct fajita_touch_power_ops power = {
		.reset_set = fp_reset_set, .avdd_enable = fp_avdd_enable,
		.vddio_set = fp_vddio_set, .active_pins = fp_active_pins, .wait_ms = fp_wait_ms
	};
	unsigned cleanup = 0;
	fajita_power_status = 0;
	fajita_power_tlmm_layout = 0;
	fajita_power_chip_detail = 0;
	fajita_power_rpmh_detail = 0;
	if (PLAT != &platform_fajita || !fp_arg(line, "fajita_touch=") ||
	    !fp_arg(line, "fajita_touch_power=")) { return 0; }
	if (this_mpidr != 0 || kernel_mpidr == this_mpidr || !BS->raise_tpl || !BS->restore_tpl) {
		fajita_power_status = 0x80010000u;
		print("fajita-power: unsupported firmware CPU/context; touch skipped\n"); return 0;
	}
	if (!fp_timer_ready()) {
		fajita_power_status = 0x80020000u;
		print("fajita-power: invalid/stopped architectural timer; touch skipped\n"); return 0;
	}
	ctx.tlmm_layout = fp_chip_layout();
	if (!ctx.tlmm_layout) {
		fajita_power_status = 0x80080000u | (fajita_power_chip_detail & 0xffffu);
		print("fajita-power: unknown chip/TLMM layout; touch skipped before MMIO\n");
		return 0;
	}
	fajita_power_tlmm_layout = ctx.tlmm_layout;
	print("fajita-power: reading DT command-database dictionary\n");
	uint32_t pa = fp_read(FAJITA_TOUCH_CMD_DB_DICT_PA);
	uint32_t size = fp_read(FAJITA_TOUCH_CMD_DB_DICT_PA + 4u);
	if (!fajita_cmd_db_range(pa, size, FAJITA_TOUCH_CMD_DB_RESERVED_START,
	    FAJITA_TOUCH_CMD_DB_RESERVED_END)) {
		fajita_power_status = 0x80030000u;
		print("fajita-power: invalid command database range; touch skipped\n"); return 0;
	}
	if (!fajita_cmd_db_ldoa28(fp_database(pa), size, &resource)) {
		fajita_power_status = 0x80040000u;
		print("fajita-power: invalid/missing ldoa28 command database; touch skipped\n"); return 0;
	}
	/* Match the DT's DRV2: eight ordinary TCS, >=3 commands per TCS.
	 * Vendor ACTIVE_TCS=2; ordered <2 2> gives active indices 0..1. */
	uint32_t config = fp_read(FAJITA_TOUCH_RSC_CONFIG_PA + 0x0cu);
	if (((config >> (6u * FAJITA_TOUCH_RSC_DRV_ID)) & 0x3fu) < 8u ||
	    ((config >> 27) & 0x1fu) < 3u) {
		fajita_power_status = 0x80050000u;
		print("fajita-power: DRV2 geometry mismatch; touch skipped\n"); return 0;
	}
	ctx.resource = resource.address;
	ctx.tcs = FAJITA_RPMH_NO_TCS;
	enum fajita_touch_power_result result = fajita_touch_power_on(&power, &ctx, &cleanup);
	if (result != FAJITA_TOUCH_POWER_READY) {
		fajita_power_status = (ctx.rpmh_result == FAJITA_RPMH_SKIP ? 0x80070000u : 0x80060000u) |
		    ((cleanup & 0xffu) << 8) | (uint32_t)result;
		print("fajita-power: initialization failed at %d, cleanup %d; touch skipped\n",
		    (uint64_t)result, (uint64_t)cleanup);
		return 0;
	}
	/* No release callback: the old aggregate DRV vote is not distinguishable
	 * from an inherited vote, so a failure must never turn another user off. */
	print("fajita-power: board sequence complete; controller identity still requires I2C\n");
	fajita_power_status = 1;
	return 1;
}
