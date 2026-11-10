/* SPDX-License-Identifier: GPL-2.0-only
 * Board-specific order with device-independent callbacks. This does not
 * provide a PMIC/GPIO backend or establish successful hardware bring-up.
 * Sequence: OnePlus synaptics_driver_s3320.c, tpd_hw_pwron(); board values
 * come from fajita_touch_board.h, checked against the phone's DT/DTBO.
 */
#ifndef FAJITA_TOUCH_POWER_SEQ_H
#define FAJITA_TOUCH_POWER_SEQ_H

#include <stdbool.h>
#include <stdint.h>
#include "fajita_touch_board.h"

enum fajita_touch_power_result {
	FAJITA_TOUCH_POWER_READY = 0,
	FAJITA_TOUCH_POWER_BAD_OPS,
	FAJITA_TOUCH_POWER_RESET_HOLD_FAILED,
	FAJITA_TOUCH_POWER_AVDD_ENABLE_FAILED,
	FAJITA_TOUCH_POWER_AVDD_WAIT_FAILED,
	FAJITA_TOUCH_POWER_VDDIO_ENABLE_FAILED,
	FAJITA_TOUCH_POWER_VDDIO_WAIT_FAILED,
	FAJITA_TOUCH_POWER_RESET_HIGH_FAILED,
	FAJITA_TOUCH_POWER_RESET_HIGH_WAIT_FAILED,
	FAJITA_TOUCH_POWER_RESET_LOW_FAILED,
	FAJITA_TOUCH_POWER_RESET_LOW_WAIT_FAILED,
	FAJITA_TOUCH_POWER_RESET_RELEASE_FAILED,
	FAJITA_TOUCH_POWER_ACTIVE_PINS_FAILED,
	FAJITA_TOUCH_POWER_SETTLE_WAIT_FAILED
};

enum fajita_touch_power_cleanup_failure {
	FAJITA_TOUCH_POWER_CLEANUP_RESET = 1u << 0,
	FAJITA_TOUCH_POWER_CLEANUP_VDDIO = 1u << 1,
	FAJITA_TOUCH_POWER_CLEANUP_AVDD = 1u << 2
};

struct fajita_touch_power_ops {
	bool (*reset_set)(void *ctx, bool high);
	bool (*avdd_enable)(void *ctx, uint32_t microvolts, uint32_t load_ua);
	bool (*vddio_set)(void *ctx, bool high);
	bool (*active_pins)(void *ctx);
	bool (*wait_ms)(void *ctx, unsigned milliseconds);
	/* Optional, non-mutating ownership queries. True means acquired by THIS
	 * attempt, including partial acquisition before a failing callback.
	 * Inherited-power backends must return false. A missing query is false.
	 * Initialize backend attempt/ownership state before calling power_on().
	 */
	bool (*avdd_owned)(void *ctx);
	bool (*vddio_owned)(void *ctx);
	/* Only called after avdd_owned() says this attempt owns the rail/vote.
	 * Release this attempt's acquisition, not another owner's supply.
	 */
	bool (*avdd_release)(void *ctx);
};

/* READY means every callback succeeded, including the final 100 ms settle.
 * It is not a controller identity or I2C-response check. On any failure,
 * retry reset-low and unwind only supplies owned by this attempt. Cleanup
 * is best-effort: its failure mask is separate from the original error.
 * A NULL cleanup_failures pointer is allowed. Invalid ops perform no I/O.
 */
static inline enum fajita_touch_power_result
fajita_touch_power_on(const struct fajita_touch_power_ops *ops, void *ctx,
    unsigned *cleanup_failures)
{
	enum fajita_touch_power_result result;
	bool avdd_attempted = false, vddio_attempted = false;
	unsigned cleanup = 0;

	if (cleanup_failures) { *cleanup_failures = 0; }
	if (!ops || !ops->reset_set || !ops->avdd_enable ||
	    !ops->vddio_set || !ops->active_pins || !ops->wait_ms) {
		return FAJITA_TOUCH_POWER_BAD_OPS;
	}
	result = FAJITA_TOUCH_POWER_RESET_HOLD_FAILED;
	if (!ops->reset_set(ctx, false)) { goto failed; }
	result = FAJITA_TOUCH_POWER_AVDD_ENABLE_FAILED;
	avdd_attempted = true;
	if (!ops->avdd_enable(ctx, FAJITA_TOUCH_AVDD_UV,
	    FAJITA_TOUCH_AVDD_LOAD_UA)) { goto failed; }
	result = FAJITA_TOUCH_POWER_AVDD_WAIT_FAILED;
	if (!ops->wait_ms(ctx, 10)) { goto failed; }
	result = FAJITA_TOUCH_POWER_VDDIO_ENABLE_FAILED;
	vddio_attempted = true;
	if (!ops->vddio_set(ctx, true)) { goto failed; }
	result = FAJITA_TOUCH_POWER_VDDIO_WAIT_FAILED;
	if (!ops->wait_ms(ctx, 10)) { goto failed; }
	result = FAJITA_TOUCH_POWER_RESET_HIGH_FAILED;
	if (!ops->reset_set(ctx, true)) { goto failed; }
	result = FAJITA_TOUCH_POWER_RESET_HIGH_WAIT_FAILED;
	if (!ops->wait_ms(ctx, 10)) { goto failed; }
	result = FAJITA_TOUCH_POWER_RESET_LOW_FAILED;
	if (!ops->reset_set(ctx, false)) { goto failed; }
	result = FAJITA_TOUCH_POWER_RESET_LOW_WAIT_FAILED;
	if (!ops->wait_ms(ctx, 10)) { goto failed; }
	result = FAJITA_TOUCH_POWER_RESET_RELEASE_FAILED;
	if (!ops->reset_set(ctx, true)) { goto failed; }
	result = FAJITA_TOUCH_POWER_ACTIVE_PINS_FAILED;
	if (!ops->active_pins(ctx)) { goto failed; }
	result = FAJITA_TOUCH_POWER_SETTLE_WAIT_FAILED;
	if (!ops->wait_ms(ctx, 100)) { goto failed; }
	return FAJITA_TOUCH_POWER_READY;

failed:
	if (!ops->reset_set(ctx, false)) {
		cleanup |= FAJITA_TOUCH_POWER_CLEANUP_RESET;
	}
	if (vddio_attempted && ops->vddio_owned && ops->vddio_owned(ctx) &&
	    !ops->vddio_set(ctx, false)) {
		cleanup |= FAJITA_TOUCH_POWER_CLEANUP_VDDIO;
	}
	if (avdd_attempted && ops->avdd_owned && ops->avdd_owned(ctx)) {
		if (!ops->avdd_release || !ops->avdd_release(ctx)) {
			cleanup |= FAJITA_TOUCH_POWER_CLEANUP_AVDD;
		}
	}
	if (cleanup_failures) { *cleanup_failures = cleanup; }
	return result;
}

#endif
