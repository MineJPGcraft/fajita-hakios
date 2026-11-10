/* SPDX-License-Identifier: GPL-2.0-only
 * Narrow, synchronous, pre-handoff RPMh transaction; no system-register I/O.
 * Reference: OnePlus SDM845_Q_10.0, commit
 * 222822c2d0042067b7964c6302ef4d756dc4bed9,
 * drivers/mailbox/qcom-rpmh-mailbox.c and
 * include/dt-bindings/soc/qcom,tcs-mbox.h.
 * In THIS binding ACTIVE_TCS=2. Fajita's ordered DT pairs are
 * <2 2>, <0 3>, <1 3>, <3 1>, so ACTIVE is TCS0..1 (not TCS2..4).
 * Every callback offset is relative to DRV reg_base 0x179e0d00 (span 0x3000),
 * not the separate configuration resource at 0x179e0000.
 *
 * Caller must validate DT/DRV hardware geometry and cmd-db resource first,
 * hold UEFI TPL_HIGH_LEVEL plus IRQ/FIQ masking on its firmware CPU, and
 * call only before XNU CPU_ON. Local idle checks are NOT cross-CPU locking.
 * Backend rd/wr must implement ordered device MMIO. This header alone is
 * not permission to access an arbitrary RSC or evidence of hardware success.
 */
#ifndef FAJITA_RPMH_TRANSACTION_H
#define FAJITA_RPMH_TRANSACTION_H
#include <stdbool.h>
#include <stdint.h>

enum fajita_rpmh_result {
	FAJITA_RPMH_READY = 0,
	FAJITA_RPMH_SKIP,
	FAJITA_RPMH_FATAL
};
#define FAJITA_RPMH_NO_TCS UINT32_MAX
#define FAJITA_RPMH_TCS_STRIDE 0x2a0u
#define FAJITA_RPMH_CMD_STRIDE 0x14u
#define FAJITA_RPMH_IRQ_ENABLE 0x00u
#define FAJITA_RPMH_IRQ_STATUS 0x04u
#define FAJITA_RPMH_IRQ_CLEAR 0x08u
#define FAJITA_RPMH_WAIT 0x10u
#define FAJITA_RPMH_CONTROL 0x14u
#define FAJITA_RPMH_STATUS 0x18u
#define FAJITA_RPMH_CMD_ENABLE 0x1cu
#define FAJITA_RPMH_MSGID 0x30u
#define FAJITA_RPMH_ADDR 0x34u
#define FAJITA_RPMH_DATA 0x38u
#define FAJITA_RPMH_CMD_STATUS 0x3cu
#define FAJITA_RPMH_ENABLE (1u << 16)
#define FAJITA_RPMH_TRIGGER (1u << 24)
#define FAJITA_RPMH_COMMAND_OK ((1u << 8) | (1u << 16))
#define FAJITA_RPMH_WRITE_RESPONSE 0x10108u
#define FAJITA_RPMH_SYNC_POLLS 32u
#define FAJITA_RPMH_COMPLETION_POLLS 10000u
#define FAJITA_RPMH_MAX_COMMANDS 16u

struct fajita_rpmh_ops {
	uint32_t (*rd)(void *ctx, uint32_t offset);
	void (*wr)(void *ctx, uint32_t offset, uint32_t value);
	bool (*delay_us)(void *ctx, unsigned microseconds);
};
struct fajita_rpmh_saved {
	uint32_t control, irq_enable;
	uint32_t command[3][3]; /* MSGID, ADDR, DATA for commands 0..2. */
};

enum fajita_rpmh_reason {
	FAJITA_RPMH_REASON_NONE = 0,
	FAJITA_RPMH_REASON_INVALID_ARGUMENT,
	FAJITA_RPMH_REASON_INITIAL_RESOURCE,
	FAJITA_RPMH_REASON_NO_SLOT,
	FAJITA_RPMH_REASON_RECHECK,
	FAJITA_RPMH_REASON_PRETRIGGER_ROLLBACK,
	FAJITA_RPMH_REASON_POSTTRIGGER_FATAL,
	FAJITA_RPMH_REASON_RESTORE_FATAL,
	FAJITA_RPMH_REASON_UNWITNESSED_COMPLETION
};
#define FAJITA_RPMH_REJECT_STATUS 1u
#define FAJITA_RPMH_REJECT_CONTROL 2u
#define FAJITA_RPMH_REJECT_CMD_ENABLE 4u
#define FAJITA_RPMH_REJECT_WAIT 8u
#define FAJITA_RPMH_REJECT_IRQ 16u
/* Optional RAM-only report. NO_SLOT packs TCS0/1 rejection flags into the
 * low/high 16 bits of value. Other failures identify a slot/register/value.
 * This is an observation, not a claim of firmware ownership of an idle slot.
 */
struct fajita_rpmh_diagnostic {
	uint32_t reason, slot, offset, value;
};

static inline void
fajita_rpmh_diagnose(struct fajita_rpmh_diagnostic *diag, uint32_t reason,
    uint32_t slot, uint32_t offset, uint32_t value)
{
	if (diag) { *diag = (struct fajita_rpmh_diagnostic){reason, slot, offset, value}; }
}

/* ACTIVE completion in the vendor leaves AMC enable/trigger uncleared.
 * Only these three known idle encodings are accepted; TRIGGER-only and all
 * unknown bits remain forbidden. All other idle guards are independent.
 */
static inline bool fajita_rpmh_idle_control(uint32_t control)
{
	return control == 0 || control == FAJITA_RPMH_ENABLE ||
	    control == (FAJITA_RPMH_ENABLE | FAJITA_RPMH_TRIGGER);
}

static inline bool
fajita_rpmh_write_checked_diagnostic(const struct fajita_rpmh_ops *ops, void *ctx,
    uint32_t offset, uint32_t value, struct fajita_rpmh_diagnostic *diag)
{
	uint32_t observed = 0;
	ops->wr(ctx, offset, value);
	for (unsigned n = 0; n < FAJITA_RPMH_SYNC_POLLS; n++) {
		observed = ops->rd(ctx, offset);
		if (observed == value) { return true; }
		if (n + 1 < FAJITA_RPMH_SYNC_POLLS && !ops->delay_us(ctx, 1)) {
			break;
		}
	}
	if (diag) { diag->offset = offset; diag->value = observed; }
	return false;
}

static inline bool
fajita_rpmh_write_checked(const struct fajita_rpmh_ops *ops, void *ctx,
    uint32_t offset, uint32_t value)
{
	return fajita_rpmh_write_checked_diagnostic(ops, ctx, offset, value, 0);
}

static inline uint32_t
fajita_rpmh_slot_rejections(const struct fajita_rpmh_ops *ops, void *ctx, unsigned index)
{
	uint32_t slot = FAJITA_RPMH_TCS_STRIDE * index;
	uint32_t rejected = 0;
	if (ops->rd(ctx, slot + FAJITA_RPMH_STATUS) == 0) { rejected |= FAJITA_RPMH_REJECT_STATUS; }
	if (!fajita_rpmh_idle_control(ops->rd(ctx, slot + FAJITA_RPMH_CONTROL))) { rejected |= FAJITA_RPMH_REJECT_CONTROL; }
	if (ops->rd(ctx, slot + FAJITA_RPMH_CMD_ENABLE) != 0) { rejected |= FAJITA_RPMH_REJECT_CMD_ENABLE; }
	if (ops->rd(ctx, slot + FAJITA_RPMH_WAIT) != 0) { rejected |= FAJITA_RPMH_REJECT_WAIT; }
	if (ops->rd(ctx, FAJITA_RPMH_IRQ_STATUS) & (1u << index)) { rejected |= FAJITA_RPMH_REJECT_IRQ; }
	return rejected;
}

static inline bool
fajita_rpmh_slot_free(const struct fajita_rpmh_ops *ops, void *ctx, unsigned index)
{
	return fajita_rpmh_slot_rejections(ops, ctx, index) == 0;
}

/* A free slot is insufficient: another active TCS can still be changing the
 * same DRV resource. Mirror the vendor check_for_req_inflight() address check
 * across BOTH slots. Also refuse unknown busy/unsupported command geometry.
 * Check armed commands even when STATUS is idle (conservative pending case).
 */
static inline bool
fajita_rpmh_resource_available_diagnostic(const struct fajita_rpmh_ops *ops, void *ctx,
    uint32_t resource_base, struct fajita_rpmh_diagnostic *diag)
{
	for (unsigned index = 0; index < 2; index++) {
		uint32_t slot = FAJITA_RPMH_TCS_STRIDE * index;
		uint32_t enabled = ops->rd(ctx, slot + FAJITA_RPMH_CMD_ENABLE);
		if ((enabled >> FAJITA_RPMH_MAX_COMMANDS) != 0) {
			if (diag) { diag->slot = index; diag->offset = slot + FAJITA_RPMH_CMD_ENABLE; diag->value = enabled; }
			return false;
		}
		if (!enabled && ops->rd(ctx, slot + FAJITA_RPMH_STATUS) == 0) {
			if (diag) { diag->slot = index; diag->offset = slot + FAJITA_RPMH_STATUS; diag->value = 0; }
			return false;
		}
		for (unsigned n = 0; n < FAJITA_RPMH_MAX_COMMANDS; n++) {
			if (!(enabled & (1u << n))) { continue; }
			uint32_t address = ops->rd(ctx, slot + FAJITA_RPMH_ADDR + n * FAJITA_RPMH_CMD_STRIDE);
			if (address == resource_base || address == resource_base + 4u ||
			    address == resource_base + 8u) {
				if (diag) { diag->slot = index; diag->offset = slot + FAJITA_RPMH_ADDR + n * FAJITA_RPMH_CMD_STRIDE; diag->value = address; }
				return false;
			}
		}
	}
	return true;
}

static inline bool
fajita_rpmh_resource_available(const struct fajita_rpmh_ops *ops, void *ctx,
    uint32_t resource_base)
{
	return fajita_rpmh_resource_available_diagnostic(ops, ctx, resource_base, 0);
}

/* Only our IRQ enable bit may be changed on a failed/uncertain transaction.
 * Caller retains CPU IRQ/FIQ masking after FATAL even if MMIO fails to stick.
 */
static inline void
fajita_rpmh_keep_irq_masked(const struct fajita_rpmh_ops *ops, void *ctx,
    unsigned index)
{
	uint32_t value = ops->rd(ctx, FAJITA_RPMH_IRQ_ENABLE);
	ops->wr(ctx, FAJITA_RPMH_IRQ_ENABLE, value & ~(1u << index));
}

static inline bool
fajita_rpmh_restore_diagnostic(const struct fajita_rpmh_ops *ops, void *ctx,
    unsigned index, const struct fajita_rpmh_saved *saved, bool completed,
    struct fajita_rpmh_diagnostic *diag)
{
	uint32_t slot = FAJITA_RPMH_TCS_STRIDE * index;
	uint32_t bit = 1u << index;
	uint32_t control = ops->rd(ctx, slot + FAJITA_RPMH_CONTROL);
	if (!fajita_rpmh_idle_control(control)) {
		if (diag) { diag->offset = slot + FAJITA_RPMH_CONTROL; diag->value = control; }
		goto unsafe;
	}
	if (ops->rd(ctx, slot + FAJITA_RPMH_STATUS) == 0) {
		if (diag) { diag->offset = slot + FAJITA_RPMH_STATUS; diag->value = 0; }
		goto unsafe;
	}
	/* completed=false is used only before THIS attempt writes TRIGGER.
	 * A known idle inherited trigger is cleared, never replayed; a new pending
	 * completion or uncertain CONTROL/STATUS still prevents rollback.
	 */
	if (!completed) {
		uint32_t pending = ops->rd(ctx, FAJITA_RPMH_IRQ_STATUS);
		if (pending & bit) {
			if (diag) { diag->offset = FAJITA_RPMH_IRQ_STATUS; diag->value = pending; }
			goto unsafe;
		}
	}
	/* HW requires clearing trigger before clearing AMC mode enable. */
	control &= ~FAJITA_RPMH_TRIGGER;
	if (!fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_CONTROL, control, diag)) { goto unsafe; }
	control &= ~FAJITA_RPMH_ENABLE;
	if (!fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_CONTROL, control, diag) ||
	    !fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_CMD_ENABLE, 0, diag) ||
	    !fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_WAIT, 0, diag)) { goto unsafe; }
	if (completed) {
		/* W1C only this TCS; never acknowledge unrelated pending IRQs. */
		ops->wr(ctx, FAJITA_RPMH_IRQ_CLEAR, bit);
		bool cleared = false;
		for (unsigned n = 0; n < FAJITA_RPMH_SYNC_POLLS; n++) {
			if ((ops->rd(ctx, FAJITA_RPMH_IRQ_STATUS) & bit) == 0) {
				cleared = true; break;
			}
			if (n + 1 < FAJITA_RPMH_SYNC_POLLS && !ops->delay_us(ctx, 1)) { break; }
		}
		if (!cleared) {
			if (diag) { diag->offset = FAJITA_RPMH_IRQ_STATUS; diag->value = ops->rd(ctx, FAJITA_RPMH_IRQ_STATUS); }
			goto unsafe;
		}
	}
	for (unsigned n = 0; n < 3; n++) {
		for (unsigned field = 0; field < 3; field++) {
			if (!fajita_rpmh_write_checked_diagnostic(ops, ctx,
			    slot + FAJITA_RPMH_MSGID + n * FAJITA_RPMH_CMD_STRIDE + field * 4,
			    saved->command[n][field], diag)) { goto unsafe; }
		}
	}
	/* Restore the saved idle AMC mode, never its historical trigger edge.
	 * Replaying saved ENABLE|TRIGGER could execute the restored old commands.
	 */
	if (!fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_CONTROL,
	    saved->control & ~FAJITA_RPMH_TRIGGER, diag) ||
	    !fajita_rpmh_write_checked_diagnostic(ops, ctx,
	    FAJITA_RPMH_IRQ_ENABLE, saved->irq_enable, diag)) { goto unsafe; }
	return true;
unsafe:
	fajita_rpmh_keep_irq_masked(ops, ctx, index);
	return false;
}

static inline bool
fajita_rpmh_restore(const struct fajita_rpmh_ops *ops, void *ctx,
    unsigned index, const struct fajita_rpmh_saved *saved, bool completed)
{
	return fajita_rpmh_restore_diagnostic(ops, ctx, index, saved, completed, 0);
}

/* One active request: ldoa28 voltage=3008mV, enable=1, HPM mode=7.
 * READY is zero. SKIP means no new trigger was sent; restored idle CONTROL
 * excludes a historical TRIGGER bit, which must never be replayed.
 * FATAL means do not CPU_ON or hand off; leave the firmware display visible
 * and retain IRQ/FIQ masking. Do not retry a FATAL slot or reinterpret it as
 * optional touch absence. chosen_index is optional; NO_TCS means no selection.
 */
static inline enum fajita_rpmh_result
fajita_rpmh_avdd_transaction_diagnostic(const struct fajita_rpmh_ops *ops, void *ctx,
    uint32_t resource_base, unsigned *chosen_index, struct fajita_rpmh_diagnostic *diag)
{
	struct fajita_rpmh_saved saved;
	unsigned index;
	uint32_t slot, bit, rejected[2] = {0, 0};
	static const uint32_t data[3] = {3008u, 1u, 7u};
	if (chosen_index) { *chosen_index = FAJITA_RPMH_NO_TCS; }
	fajita_rpmh_diagnose(diag, FAJITA_RPMH_REASON_NONE, FAJITA_RPMH_NO_TCS, 0, 0);
	if (!ops || !ops->rd || !ops->wr || !ops->delay_us ||
	    !resource_base || (resource_base & 3u) || resource_base > UINT32_MAX - 8u) {
		fajita_rpmh_diagnose(diag, FAJITA_RPMH_REASON_INVALID_ARGUMENT, FAJITA_RPMH_NO_TCS, 0, resource_base);
		return FAJITA_RPMH_SKIP;
	}
	if (!fajita_rpmh_resource_available_diagnostic(ops, ctx, resource_base, diag)) {
		if (diag) { diag->reason = FAJITA_RPMH_REASON_INITIAL_RESOURCE; }
		return FAJITA_RPMH_SKIP;
	}
	for (index = 0; index < 2; index++) {
		rejected[index] = fajita_rpmh_slot_rejections(ops, ctx, index);
		if (!rejected[index]) { break; }
	}
	if (index == 2) {
		fajita_rpmh_diagnose(diag, FAJITA_RPMH_REASON_NO_SLOT, FAJITA_RPMH_NO_TCS, 0,
		    rejected[0] | (rejected[1] << 16));
		return FAJITA_RPMH_SKIP;
	}
	slot = FAJITA_RPMH_TCS_STRIDE * index; bit = 1u << index;
	saved.control = ops->rd(ctx, slot + FAJITA_RPMH_CONTROL);
	saved.irq_enable = ops->rd(ctx, FAJITA_RPMH_IRQ_ENABLE);
	for (unsigned n = 0; n < 3; n++) {
		for (unsigned field = 0; field < 3; field++) {
			saved.command[n][field] = ops->rd(ctx,
			    slot + FAJITA_RPMH_MSGID + n * FAJITA_RPMH_CMD_STRIDE + field * 4);
		}
	}
	/* Recheck after snapshot, before the first mutation. */
	rejected[index] = fajita_rpmh_slot_rejections(ops, ctx, index);
	if (rejected[index] || !fajita_rpmh_idle_control(saved.control)) {
		fajita_rpmh_diagnose(diag, FAJITA_RPMH_REASON_RECHECK, index, 0,
		    rejected[index] | (!fajita_rpmh_idle_control(saved.control) ? FAJITA_RPMH_REJECT_CONTROL : 0));
		return FAJITA_RPMH_SKIP;
	}
	if (!fajita_rpmh_resource_available_diagnostic(ops, ctx, resource_base, diag)) {
		if (diag) { diag->reason = FAJITA_RPMH_REASON_RECHECK; }
		return FAJITA_RPMH_SKIP;
	}
	if (chosen_index) { *chosen_index = index; }
	if (diag) { diag->slot = index; }
	if (!fajita_rpmh_write_checked_diagnostic(ops, ctx, FAJITA_RPMH_IRQ_ENABLE,
	    saved.irq_enable & ~bit, diag)) { goto not_triggered; }
	/* Before touching any command word, quiesce inherited ACTIVE AMC mode in
	 * the vendor-required order: clear historical TRIGGER, then clear ENABLE.
	 * Both writes have TRIGGER clear; neither can replay an old transaction.
	 */
	if (!fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_CONTROL,
	    saved.control & ~FAJITA_RPMH_TRIGGER, diag) ||
	    !fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_CONTROL, 0, diag)) { goto not_triggered; }
	for (unsigned n = 0; n < 3; n++) {
		uint32_t command = slot + n * FAJITA_RPMH_CMD_STRIDE;
		if (!fajita_rpmh_write_checked_diagnostic(ops, ctx, command + FAJITA_RPMH_MSGID,
		    FAJITA_RPMH_WRITE_RESPONSE, diag) ||
		    !fajita_rpmh_write_checked_diagnostic(ops, ctx, command + FAJITA_RPMH_ADDR,
		    resource_base + n * 4, diag) ||
		    !fajita_rpmh_write_checked_diagnostic(ops, ctx, command + FAJITA_RPMH_DATA, data[n], diag)) {
			goto not_triggered;
		}
	}
	if (!fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_WAIT, 7, diag) ||
	    !fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_CMD_ENABLE, 7, diag) ||
	    !fajita_rpmh_write_checked_diagnostic(ops, ctx, slot + FAJITA_RPMH_CONTROL,
	    FAJITA_RPMH_ENABLE, diag)) { goto not_triggered; }
	/* From THIS write onward every error is FATAL. Never roll back commands
	 * while the hardware can still be executing them. Trigger may self-clear.
	 */
	ops->wr(ctx, slot + FAJITA_RPMH_CONTROL,
	    FAJITA_RPMH_ENABLE | FAJITA_RPMH_TRIGGER);
	if (diag) { diag->reason = FAJITA_RPMH_REASON_POSTTRIGGER_FATAL; }
	bool witnessed = false;
	for (unsigned n = 0; n < FAJITA_RPMH_COMPLETION_POLLS; n++) {
		/* Poll response-requested commands directly. IRQ_STATUS is not a
		 * prerequisite for synchronous completion; its visibility while the
		 * peripheral IRQ is masked has not been established on this firmware.
		 * Old CMD_STATUS flags and idle can briefly survive a posted trigger.
		 * Require post-trigger progress, or a newly pending selected IRQ (the
		 * slot guards established it absent), before accepting three OK flags.
		 */
		bool all_complete = true;
		for (unsigned command = 0; command < 3; command++) {
			uint32_t offset = slot + FAJITA_RPMH_CMD_STATUS +
			    command * FAJITA_RPMH_CMD_STRIDE;
			uint32_t status = ops->rd(ctx, offset);
			if ((status & FAJITA_RPMH_COMMAND_OK) != FAJITA_RPMH_COMMAND_OK) {
				if (all_complete && diag) { diag->offset = offset; diag->value = status; }
				all_complete = false;
			}
		}
		uint32_t engine = ops->rd(ctx, slot + FAJITA_RPMH_STATUS);
		uint32_t pending = ops->rd(ctx, FAJITA_RPMH_IRQ_STATUS);
		if (!all_complete || engine == 0 || (pending & bit)) { witnessed = true; }
		if (all_complete && engine != 0 && witnessed) {
			if (!fajita_rpmh_restore_diagnostic(ops, ctx, index, &saved, true, diag)) {
				if (diag) { diag->reason = FAJITA_RPMH_REASON_RESTORE_FATAL; }
				return FAJITA_RPMH_FATAL;
			}
			fajita_rpmh_diagnose(diag, FAJITA_RPMH_REASON_NONE, index, 0, 0);
			return FAJITA_RPMH_READY;
		}
		/* Commands can finish before the engine reports idle. Keep waiting
		 * within the same bound; do not clean up a still-running engine.
		 */
		if (all_complete && diag) {
			diag->offset = engine == 0 ? slot + FAJITA_RPMH_STATUS : FAJITA_RPMH_IRQ_STATUS;
			diag->value = engine == 0 ? 0 : pending;
		}
		if (n + 1 < FAJITA_RPMH_COMPLETION_POLLS && !ops->delay_us(ctx, 10)) {
			return FAJITA_RPMH_FATAL;
		}
	}
	if (!witnessed && diag) { diag->reason = FAJITA_RPMH_REASON_UNWITNESSED_COMPLETION; }
	return FAJITA_RPMH_FATAL;
not_triggered:
	if (!fajita_rpmh_restore_diagnostic(ops, ctx, index, &saved, false, diag)) {
		if (diag) { diag->reason = FAJITA_RPMH_REASON_RESTORE_FATAL; }
		return FAJITA_RPMH_FATAL;
	}
	if (diag) { diag->reason = FAJITA_RPMH_REASON_PRETRIGGER_ROLLBACK; }
	return FAJITA_RPMH_SKIP;
}

static inline enum fajita_rpmh_result
fajita_rpmh_avdd_transaction(const struct fajita_rpmh_ops *ops, void *ctx,
    uint32_t resource_base, unsigned *chosen_index)
{
	return fajita_rpmh_avdd_transaction_diagnostic(ops, ctx, resource_base, chosen_index, 0);
}
#endif
