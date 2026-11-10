/* Host-only tests: no firmware, GPIO, PMIC or USB access. */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "../devices/fajita/kernel/fajita_touch_power_seq.h"

enum action { RESET_LOW, AVDD_ON, WAIT_10, VDDIO_ON, RESET_HIGH,
	ACTIVE_PINS, WAIT_100, VDDIO_OFF, AVDD_RELEASE };
static const enum action sequence[] = {
	RESET_LOW, AVDD_ON, WAIT_10, VDDIO_ON, WAIT_10, RESET_HIGH,
	WAIT_10, RESET_LOW, WAIT_10, RESET_HIGH, ACTIVE_PINS, WAIT_100
};
static const enum fajita_touch_power_result errors[] = {
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
#define COUNT(a) (sizeof(a) / sizeof((a)[0]))
struct mock {
	enum action events[32];
	size_t count;
	int fail_at;
	bool fail_after_change, inherited, inherit_avdd, inherit_vddio, cleanup;
	bool own_avdd, own_vddio, reset_high;
	unsigned cleanup_fail_mask, avdd_queries, vddio_queries;
};

static bool record(struct mock *m, enum action action)
{
	assert(m->count < COUNT(m->events));
	m->events[m->count++] = action;
	if (!m->cleanup && m->fail_at >= 0 &&
	    m->count - 1 == (size_t)m->fail_at) {
		m->cleanup = true;
		return false;
	}
	return true;
}
static bool mock_reset(void *ctx, bool high)
{
	struct mock *m = ctx;
	bool was_cleanup = m->cleanup;
	bool ok = record(m, high ? RESET_HIGH : RESET_LOW);
	if (was_cleanup && (m->cleanup_fail_mask & FAJITA_TOUCH_POWER_CLEANUP_RESET)) {
		return false;
	}
	if (ok || m->fail_after_change) { m->reset_high = high; }
	return ok;
}
static bool mock_avdd(void *ctx, uint32_t uv, uint32_t load)
{
	struct mock *m = ctx;
	assert(uv == FAJITA_TOUCH_AVDD_UV && uv == 3008000u);
	assert(load == FAJITA_TOUCH_AVDD_LOAD_UA && load == 20000u);
	bool ok = record(m, AVDD_ON);
	if ((ok || m->fail_after_change) && !m->inherited && !m->inherit_avdd) {
		m->own_avdd = true;
	}
	return ok;
}
static bool mock_vddio(void *ctx, bool high)
{
	struct mock *m = ctx;
	bool was_cleanup = m->cleanup;
	bool ok = record(m, high ? VDDIO_ON : VDDIO_OFF);
	if (was_cleanup && (m->cleanup_fail_mask & FAJITA_TOUCH_POWER_CLEANUP_VDDIO)) {
		return false;
	}
	if ((ok || m->fail_after_change) && !m->inherited && !m->inherit_vddio) {
		m->own_vddio = high;
	}
	return ok;
}
static bool mock_active(void *ctx) { return record(ctx, ACTIVE_PINS); }
static bool mock_wait(void *ctx, unsigned ms)
{
	assert(ms == 10 || ms == 100);
	return record(ctx, ms == 10 ? WAIT_10 : WAIT_100);
}
static bool mock_avdd_owned(void *ctx)
{
	struct mock *m = ctx; m->avdd_queries++; return m->own_avdd;
}
static bool mock_vddio_owned(void *ctx)
{
	struct mock *m = ctx; m->vddio_queries++; return m->own_vddio;
}
static bool mock_release(void *ctx)
{
	struct mock *m = ctx;
	assert(m->own_avdd && !m->inherited && !m->inherit_avdd);
	(void)record(m, AVDD_RELEASE);
	if (m->cleanup_fail_mask & FAJITA_TOUCH_POWER_CLEANUP_AVDD) { return false; }
	m->own_avdd = false;
	return true;
}
static struct fajita_touch_power_ops ops(void)
{
	return (struct fajita_touch_power_ops){
		.reset_set = mock_reset, .avdd_enable = mock_avdd,
		.vddio_set = mock_vddio, .active_pins = mock_active,
		.wait_ms = mock_wait, .avdd_owned = mock_avdd_owned,
		.vddio_owned = mock_vddio_owned, .avdd_release = mock_release
	};
}
static struct mock initial(void) { return (struct mock){ .fail_at = -1 }; }
static void assert_prefix(const struct mock *m, size_t length)
{
	assert(m->count >= length);
	for (size_t n = 0; n < length; n++) { assert(m->events[n] == sequence[n]); }
}
static void test_success(void)
{
	for (unsigned inherited = 0; inherited < 2; inherited++) {
		struct mock m = initial(); m.inherited = inherited != 0;
		struct fajita_touch_power_ops o = ops();
		unsigned cleanup = 99;
		assert(fajita_touch_power_on(&o, &m, &cleanup) == FAJITA_TOUCH_POWER_READY);
		assert(cleanup == 0 && m.count == COUNT(sequence) && m.reset_high);
		assert(m.own_avdd == !m.inherited && m.own_vddio == !m.inherited);
		assert(!m.avdd_queries && !m.vddio_queries);
		assert_prefix(&m, COUNT(sequence));
	}
}
static void test_each_failure(void)
{
	for (unsigned inherited = 0; inherited < 2; inherited++) {
		for (unsigned partial = 0; partial < 2; partial++) {
			for (size_t n = 0; n < COUNT(sequence); n++) {
				struct mock m = initial(); m.fail_at = (int)n;
				m.inherited = inherited != 0; m.fail_after_change = partial != 0;
				struct fajita_touch_power_ops o = ops();
				unsigned cleanup = 99;
				assert(fajita_touch_power_on(&o, &m, &cleanup) == errors[n]);
				assert_prefix(&m, n + 1);
				size_t at = n + 1;
				assert(m.events[at++] == RESET_LOW);
				bool vddio_owned = !inherited && (n > 3 || (n == 3 && partial));
				bool avdd_owned = !inherited && (n > 1 || (n == 1 && partial));
				if (vddio_owned) { assert(m.events[at++] == VDDIO_OFF); }
				if (avdd_owned) { assert(m.events[at++] == AVDD_RELEASE); }
				assert(m.count == at && cleanup == 0 && !m.reset_high);
				assert(!m.own_avdd && !m.own_vddio);
				assert(m.avdd_queries == (n >= 1) && m.vddio_queries == (n >= 3));
			}
		}
	}
}
static void test_cleanup_failures(void)
{
	for (unsigned flags = 1; flags < 8; flags++) {
		struct mock m = initial(); m.fail_at = 11; m.cleanup_fail_mask = flags;
		struct fajita_touch_power_ops o = ops();
		unsigned cleanup = 0;
		assert(fajita_touch_power_on(&o, &m, &cleanup) == FAJITA_TOUCH_POWER_SETTLE_WAIT_FAILED);
		assert(cleanup == flags && m.count == 15);
		assert(m.events[12] == RESET_LOW && m.events[13] == VDDIO_OFF &&
		    m.events[14] == AVDD_RELEASE);
	}
	struct mock m = initial(); m.fail_at = 11;
	struct fajita_touch_power_ops o = ops(); o.avdd_release = NULL;
	unsigned cleanup = 0;
	assert(fajita_touch_power_on(&o, &m, &cleanup) == FAJITA_TOUCH_POWER_SETTLE_WAIT_FAILED);
	assert(cleanup == FAJITA_TOUCH_POWER_CLEANUP_AVDD && m.own_avdd);
}
static void test_optional_ownership(void)
{
	struct mock m = initial(); m.fail_at = 11; m.inherited = true;
	struct fajita_touch_power_ops o = ops();
	o.avdd_owned = NULL; o.vddio_owned = NULL; o.avdd_release = NULL;
	assert(fajita_touch_power_on(&o, &m, NULL) == FAJITA_TOUCH_POWER_SETTLE_WAIT_FAILED);
	assert(m.count == 13 && m.events[12] == RESET_LOW);
	assert(!m.avdd_queries && !m.vddio_queries);
}
static void test_independent_ownership(void)
{
	for (unsigned mask = 0; mask < 4; mask++) {
		struct mock m = initial(); m.fail_at = 11;
		m.inherit_avdd = (mask & 1u) != 0;
		m.inherit_vddio = (mask & 2u) != 0;
		struct fajita_touch_power_ops o = ops();
		unsigned cleanup = 99;
		assert(fajita_touch_power_on(&o, &m, &cleanup) == FAJITA_TOUCH_POWER_SETTLE_WAIT_FAILED);
		size_t at = 12;
		assert(m.events[at++] == RESET_LOW);
		if (!m.inherit_vddio) { assert(m.events[at++] == VDDIO_OFF); }
		if (!m.inherit_avdd) { assert(m.events[at++] == AVDD_RELEASE); }
		assert(m.count == at && cleanup == 0 && !m.reset_high);
		assert(!m.own_avdd && !m.own_vddio);
	}
}
static void test_invalid_ops(void)
{
	struct mock m = initial(); unsigned cleanup = 99;
	assert(fajita_touch_power_on(NULL, &m, &cleanup) == FAJITA_TOUCH_POWER_BAD_OPS);
	assert(cleanup == 0 && m.count == 0);
	for (unsigned i = 0; i < 5; i++) {
		struct fajita_touch_power_ops o = ops();
		switch (i) {
		case 0: o.reset_set = NULL; break;
		case 1: o.avdd_enable = NULL; break;
		case 2: o.vddio_set = NULL; break;
		case 3: o.active_pins = NULL; break;
		case 4: o.wait_ms = NULL; break;
		}
		cleanup = 99;
		assert(fajita_touch_power_on(&o, &m, &cleanup) == FAJITA_TOUCH_POWER_BAD_OPS);
		assert(cleanup == 0 && m.count == 0);
	}
}
int main(void)
{
	_Static_assert(COUNT(sequence) == COUNT(errors), "one expected failure for each step");
	test_success(); test_each_failure(); test_cleanup_failures();
	test_optional_ownership(); test_independent_ownership(); test_invalid_ops();
	puts("touch power sequence: exact order, 48 step failures, ownership and cleanup failures passed");
	return 0;
}
