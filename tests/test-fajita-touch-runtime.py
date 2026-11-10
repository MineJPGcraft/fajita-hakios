#!/usr/bin/env python3
"""Compile the production runtime recovery/report path against scripted I2C."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-touch-runtime"
OUT.mkdir(parents=True, exist_ok=True)
source = (ROOT / "devices/fajita/kernel/fajita_synaptics.c").read_text()
# Keep the production page/configuration helper, CRC loop, publication and poll
# routines intact. Only bus, sleep, input-post and cache-publication boundaries
# are supplied by the harness; the production report decoder is also included.
config = source[source.index("static bool ft_page_zero(void)"):
                source.index("static uint64_t ft_probe_pack_bytes")]
runtime = source[source.index("static bool ft_runtime_status(uint8_t status[2])\n{"):
                 source.index("static void ft_thread(void *arg,")]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "fajita_touch_report.h"
#include "fajita_touch_live.h"

enum { REDWOOD_TOUCH_UP, REDWOOD_TOUCH_DOWN, REDWOOD_TOUCH_MOVE };
struct operation {
    bool read, success;
    uint8_t reg;
    size_t length;
    uint8_t bytes[81];
};
struct event { unsigned kind, slot; uint16_t x, y; };
static struct operation operations[128];
static size_t queued, consumed;
static struct event events[32];
static size_t event_count;
static unsigned sleeps, slept_ms, metric_count;
static uint8_t ft_f01[6], ft_f12[6];
static uint16_t ft_max_x, ft_max_y;
static struct fajita_touch_report ft_previous;
static int ft_reports;
static uint64_t ft_posted_events;
static uint32_t ft_dropped_reports;
/* Identification step codes: the helper records the step and publishes it for
 * the panel; the host only needs to observe the published detail. */
static uint32_t ft_identify_fail;
static uint32_t ft_mask_readback;
static uint64_t last_phase_state;
static void ft_phase(uint32_t phase, uint32_t detail)
{
    last_phase_state = ((uint64_t)phase << 32) | detail;
}
/* Defined in the production source after this slice; the helpers below are
 * exercised directly so the harness only needs the declaration. */
static void ft_fail(uint32_t step);
/* The production poll path reports the first frame containing out-of-range
 * objects through the loader log channel; the host runs it silently. */
#define printf(...) ((void)0)
struct fajita_touch_live fajita_touch_live;

static struct operation *next_op(bool read, uint8_t reg, size_t length)
{
    assert(consumed < queued);
    struct operation *op = &operations[consumed];
    if (!(op->read == read && op->reg == reg && op->length == length)) {
        fprintf(stderr, "op %zu: expected %s reg=0x%02x len=%zu, queued %s reg=0x%02x len=%zu\n",
            consumed, read ? "read" : "write", reg, length,
            op->read ? "read" : "write", op->reg, op->length);
        fflush(stderr);
        assert(false);
    }
    consumed++;
    return op;
}
static bool fajita_i2c_read(uint8_t reg, uint8_t *bytes, size_t length)
{
    struct operation *op = next_op(true, reg, length);
    if (op->success) { memcpy(bytes, op->bytes, length); }
    return op->success;
}
static bool fajita_i2c_write(uint8_t reg, const uint8_t *bytes, size_t length)
{
    struct operation *op = next_op(false, reg, length);
    assert(memcmp(bytes, op->bytes, length) == 0);
    return op->success;
}
static void ft_sleep_ms(unsigned ms)
{
    assert(ms == 20 || ms == 100); sleeps++; slept_ms += ms;
}
static void redwood_touch_post(unsigned kind, unsigned slot, uint16_t x, uint16_t y)
{
    assert(event_count < sizeof(events) / sizeof(events[0]));
    events[event_count++] = (struct event){kind, slot, x, y};
}
static void ft_metric(volatile uint64_t *field, uint64_t value)
{
	assert(field == &fajita_touch_live.reports || field == &fajita_touch_live.attention_active ||
	    field == &fajita_touch_live.posted_events);
    metric_count++; *field = value;
}
'''
tests = r'''
static void queue_op(bool read, uint8_t reg, const uint8_t *bytes, size_t length, bool success)
{
    assert(queued < sizeof(operations) / sizeof(operations[0]));
    assert(length <= sizeof(operations[0].bytes));
    struct operation *op = &operations[queued++];
    *op = (struct operation){.read=read, .success=success, .reg=reg, .length=length};
    if (bytes) { memcpy(op->bytes, bytes, length); }
}
static void queue_read(uint8_t reg, const uint8_t *bytes, size_t length)
{
    queue_op(true, reg, bytes, length, true);
}
static void queue_write(uint8_t reg, const uint8_t *bytes, size_t length)
{
    queue_op(false, reg, bytes, length, true);
}
static void queue_page(void)
{
    const uint8_t zero = 0;
    queue_write(0xff, &zero, 1);
}
static void queue_status(uint8_t status, uint8_t irq)
{
    const uint8_t bytes[2] = {status, irq};
    queue_read(ft_f01[3], bytes, sizeof(bytes));
}
static void queue_attention(uint16_t mask)
{
    const uint8_t bytes[2] = {(uint8_t)mask, (uint8_t)(mask >> 8)};
    queue_read(0x0b, bytes, sizeof(bytes));
}
static void reset_test(void)
{
    assert(consumed == queued);
    queued = consumed = event_count = 0;
    sleeps = slept_ms = metric_count = 0;
    memset(&fajita_touch_live, 0, sizeof(fajita_touch_live));
    memset(&ft_previous, 0, sizeof(ft_previous));
    ft_f01[2] = 0x20; ft_f01[3] = 0x30;
    ft_f12[2] = 0x40; ft_f12[3] = 0x50;
	ft_max_x = 1080; ft_max_y = 2340; ft_reports = 0; ft_posted_events = 0;
	ft_dropped_reports = 0; ft_identify_fail = 0; last_phase_state = 0;
}
static void done(void) { assert(consumed == queued); }
static void old_contact(void)
{
    ft_previous.active_mask = 1;
    ft_previous.contact[0].x = 123; ft_previous.contact[0].y = 456;
}
static void expect_release(void)
{
    assert(event_count == 1 && events[0].kind == REDWOOD_TOUCH_UP);
    assert(events[0].slot == 0 && events[0].x == 123 && events[0].y == 456);
    assert(ft_previous.active_mask == 0);
}
static void queue_configuration(bool verified)
{
    /* Normal/configuration preserves unrelated F01 control bits and all
     * gesture bytes except the specified byte-2 bit. Interrupt arming follows
     * the vendor order: read F01 data1 to clear latched attention, write
     * F01 control1, then read it back to prove the controller kept 0x7f. */
    const uint8_t mode = 0x3f, configured = 0xb8;
    const uint8_t gesture[3] = {0xa5, 0x5a, 0xff};
    const uint8_t updated[3] = {0xa5, 0x5a, 0xfd};
    const uint8_t pending = 0xff, mask = 0x7f;
    const uint8_t readback = verified ? mask : 0x3f;
    queue_page();
    queue_read(ft_f01[2], &mode, 1);
    queue_write(ft_f01[2], &configured, 1);
    queue_read((uint8_t)(ft_f12[2] + 6), gesture, sizeof(gesture));
    queue_write((uint8_t)(ft_f12[2] + 6), updated, sizeof(updated));
    /* ft_interrupt_mask() selects page zero for itself. */
    queue_page();
    queue_read((uint8_t)(ft_f01[3] + 1), &pending, 1);
    queue_write((uint8_t)(ft_f01[2] + 1), &mask, 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &readback, 1);
}
static void queue_soft_reset(bool status_ok)
{
    const uint8_t zero = 0, one = 1, readback = status_ok ? 0 : 1;
    /* Masking uses the full vendor helper: page select, write 0x00, readback.
     * The mask is deliberately left clear across the reset and re-armed by
     * ft_configure_scan() afterwards. */
    queue_page();
    queue_write((uint8_t)(ft_f01[2] + 1), &zero, 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &readback, 1);
    queue_write(ft_f01[1], &one, 1);
    queue_page();
    queue_status(0, 0);
}
int main(void)
{
    uint8_t status[2] = {0xff, 0xff};

    reset_test(); queue_soft_reset(true);
    assert(ft_vendor_soft_reset()); done();
    assert(sleeps == 1 && slept_ms == 100 && ft_identify_fail == 0);

    /* A readback that differs from the written mask is recorded and then the
     * sequence continues, exactly as the vendor driver does. This is the case
     * the observed controller hit at address 0x0e. */
    reset_test(); queue_page();
    const uint8_t zero = 0, mismatch = 0x0e;
    queue_write((uint8_t)(ft_f01[2] + 1), &zero, 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &mismatch, 1);
    queue_write(ft_f01[1], &((const uint8_t){1}), 1);
    queue_page(); queue_status(0, 0);
    assert(ft_vendor_soft_reset()); done();
    assert(ft_identify_fail == 0 && ft_mask_readback == 0x0e && slept_ms == 100);

    /* Soft-reset command write fails. */
    reset_test(); queue_page(); queue_write((uint8_t)(ft_f01[2] + 1), &zero, 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &zero, 1);
    queue_op(false, ft_f01[1], &((const uint8_t){1}), 1, false);
    assert(!ft_vendor_soft_reset()); done(); assert(sleeps == 0);
    assert(ft_identify_fail == 14);

    reset_test(); queue_page(); queue_write((uint8_t)(ft_f01[2] + 1), &zero, 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &zero, 1);
    /* ft_runtime_status() rejects flash-programming mode (0x40) itself, so
     * this reports the status-read step rather than the post-reset checks. */
    queue_write(ft_f01[1], &((const uint8_t){1}), 1); queue_page(); queue_status(0x40, 0);
    assert(!ft_vendor_soft_reset()); done(); assert(slept_ms == 100);
    assert(ft_identify_fail == 16);

    /* A post-reset error state other than flash mode reports its own code. */
    reset_test(); queue_page(); queue_write((uint8_t)(ft_f01[2] + 1), &zero, 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &zero, 1);
    queue_write(ft_f01[1], &((const uint8_t){1}), 1); queue_page(); queue_status(0x02, 0);
    assert(!ft_vendor_soft_reset()); done(); assert(ft_identify_fail == 18);

    /* ft_interrupt_mask() is the only place that writes F01 control1. It must
     * select page zero, clear latched attention only when enabling, write the
     * vendor value, and publish the readback sample. */
    reset_test(); queue_page();
    queue_read((uint8_t)(ft_f01[3] + 1), &((const uint8_t){0xff}), 1);
    queue_write((uint8_t)(ft_f01[2] + 1), &((const uint8_t){0x7f}), 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &((const uint8_t){0x7f}), 1);
    assert(ft_interrupt_mask(true)); done();
    assert(ft_mask_readback == 0x7f);
    /* A readback that differs from the written value is telemetry, not a
     * failure: the vendor driver discards this sample entirely, and treating
     * it as fatal aborted identification on the observed controller. */
    reset_test(); queue_page();
    queue_write((uint8_t)(ft_f01[2] + 1), &((const uint8_t){0x00}), 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &((const uint8_t){0x0e}), 1);
    assert(ft_interrupt_mask(false)); done();
    assert(ft_mask_readback == 0x0e);
    reset_test(); queue_page();
    queue_write((uint8_t)(ft_f01[2] + 1), &((const uint8_t){0x00}), 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &((const uint8_t){0x00}), 1);
    assert(ft_interrupt_mask(false)); done();
    /* A mismatched readback is recorded but does not fail the helper, matching
     * the vendor driver, which reads this register and discards the result. */
    reset_test(); queue_page();
    queue_write((uint8_t)(ft_f01[2] + 1), &((const uint8_t){0x00}), 1);
    queue_read((uint8_t)(ft_f01[2] + 1), &((const uint8_t){0x7f}), 1);
    assert(ft_interrupt_mask(false)); done();
    assert(ft_mask_readback == 0x7f);
    reset_test(); queue_op(false, 0xff, (const uint8_t[]){0}, 1, false);
    assert(!ft_interrupt_mask(true)); done();
    /* Readback transfer failure is still a bus failure and must fail. */
    reset_test(); queue_page();
    queue_write((uint8_t)(ft_f01[2] + 1), &((const uint8_t){0x00}), 1);
    queue_op(true, (uint8_t)(ft_f01[2] + 1), NULL, 1, false);
    assert(!ft_interrupt_mask(false)); done();
    /* First failure wins: a coarse caller code must not hide the precise one,
     * and a later step must not overwrite an already-recorded failure. */
    reset_test(); ft_fail(13); assert(ft_identify_fail == 13);
    ft_fail(11); assert(ft_identify_fail == 13 && (uint32_t)last_phase_state == (0x100u | 13u));

    reset_test();
    queue_status(6, 0); queue_status(6, 0); queue_status(0, 4);
    assert(ft_runtime_status(status)); done();
    assert(status[0] == 0 && status[1] == 4 && sleeps == 2 && slept_ms == 40);

    reset_test();
    for (unsigned i=0; i<20; i++) { queue_status(6, 0); }
    assert(!ft_runtime_status(status)); done();
    assert(consumed == 20 && sleeps == 20 && slept_ms == 400);

    reset_test(); queue_status(0xc6, 1);
    assert(!ft_runtime_status(status)); done(); assert(sleeps == 0);
    reset_test(); queue_op(true, ft_f01[3], NULL, 2, false);
    assert(!ft_runtime_status(status)); done(); assert(sleeps == 0);
    reset_test(); queue_status(6, 0); queue_op(true, ft_f01[3], NULL, 2, false);
    assert(!ft_runtime_status(status)); done(); assert(sleeps == 1);

    /* Unconfigured recovery runs before the generic low-status rejection. */
    reset_test(); old_contact(); queue_page(); queue_status(0x83, 0);
    queue_configuration(true);
    assert(ft_poll_report()); done(); expect_release();
    assert(ft_reports == 0 && metric_count == 1 &&
        fajita_touch_live.posted_events == 1);

    /* The F01 reset interrupt is compared after masking unrelated high bit. */
    reset_test(); old_contact(); queue_page(); queue_status(3, 0x81);
    queue_configuration(true);
    assert(ft_poll_report()); done(); expect_release();
    assert(metric_count == 1 && fajita_touch_live.posted_events == 1);

    /* A readback that differs from the written mask no longer aborts the
     * re-arm, so recovery succeeds and releases the stale press. */
    reset_test(); old_contact(); queue_page(); queue_status(0x80, 0);
    queue_configuration(false);
    assert(ft_poll_report()); done(); expect_release();
    assert(metric_count == 1 && fajita_touch_live.posted_events == 1);

    reset_test(); old_contact(); queue_page(); queue_status(3, 0);
    assert(!ft_poll_report()); done();
    assert(event_count == 0 && ft_previous.active_mask == 1 && metric_count == 0);

    reset_test(); old_contact(); queue_page(); queue_status(0xc0, 1);
    assert(!ft_poll_report()); done();
    assert(event_count == 0 && ft_previous.active_mask == 1);

    reset_test(); queue_op(false, 0xff, (const uint8_t[]){0}, 1, false);
    assert(!ft_poll_report()); done();

    reset_test(); queue_page();
    for (unsigned i=0; i<20; i++) { queue_status(6, 0); }
    assert(!ft_poll_report()); done(); assert(slept_ms == 400);

    reset_test(); queue_page(); queue_status(6, 0); queue_status(0, 4);
    queue_attention(1);
    /* A read fetches the vendor's extra trailing byte, while the decoder
     * consumes the actual first eight bytes without a shifted slot. */
    const uint8_t contact[9] = {1, 0x38, 4, 0x24, 9, 128, 0x17, 0x28, 0xee};
    queue_read(ft_f12[3], contact, sizeof(contact));
    assert(ft_poll_report()); done();
	assert(sleeps == 1 && ft_reports == 1 && metric_count == 3);
	assert(fajita_touch_live.reports == 1 && fajita_touch_live.attention_active == 0x00010001 &&
	    fajita_touch_live.posted_events == 1 && event_count == 1);
    assert(events[0].kind == REDWOOD_TOUCH_DOWN && events[0].slot == 0);
    assert(events[0].x == UINT16_MAX && events[0].y == UINT16_MAX);

    /* No-contact polling releases the old point without a frame transfer. */
    queued = consumed = event_count = 0;
    queue_page(); queue_status(0, 0); queue_attention(0);
    assert(ft_poll_report()); done();
	assert(ft_reports == 2 && metric_count == 6 && fajita_touch_live.reports == 2);
	assert(fajita_touch_live.attention_active == 0 && fajita_touch_live.posted_events == 2);
    assert(event_count == 1 && events[0].kind == REDWOOD_TOUCH_UP);
    assert(ft_previous.active_mask == 0);

    reset_test(); queue_page(); queue_status(0, 0); queue_attention(0x0400);
    assert(!ft_poll_report()); done(); assert(metric_count == 0);

    /* An object past the sensor range is dropped on its own; the frame still
     * publishes (here as an empty report that releases the prior press) so a
     * single glitched slot cannot stall the polling thread. */
    reset_test(); old_contact(); queue_page(); queue_status(0, 0); queue_attention(1);
    const uint8_t out_of_range[9] = {1, 0xff, 0x0f, 0, 0, 1, 1, 1, 0};
    queue_read(ft_f12[3], out_of_range, sizeof(out_of_range));
    assert(ft_poll_report()); done();
    expect_release();
    /* One UP for the released prior press, plus attention/report telemetry. */
    assert(ft_dropped_reports == 1 && ft_reports == 1 && metric_count == 3);

    reset_test(); queue_page(); queue_status(0, 0);
    queue_op(true, 0x0b, NULL, 2, false);
    assert(!ft_poll_report()); done();

    reset_test(); queue_page(); queue_status(0, 0); queue_attention(1);
    queue_op(true, ft_f12[3], NULL, 9, false);
    assert(!ft_poll_report()); done();

    reset_test(); ft_reports = 0x7fffffff;
    queue_page(); queue_status(0, 0); queue_attention(0);
    assert(ft_poll_report()); done();
	assert(ft_reports == 0x7fffffff && fajita_touch_live.reports == UINT64_C(0x7fffffff));
	assert(metric_count == 2 && fajita_touch_live.attention_active == 0 &&
	    fajita_touch_live.posted_events == 0);
    puts("touch runtime: bounded CRC, flash guard, reset ordering, scan readback, reports, releases and telemetry passed");
}
'''
path = OUT / "runtime.c"
path.write_text(harness + "\n" + config + "\n" + runtime + "\n" + tests)
binary = OUT / "runtime"
subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g",
                "-I", str(ROOT / "devices/fajita/kernel"), str(path), "-o", str(binary)], check=True)
env = dict(os.environ)
env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
subprocess.run([str(binary)], check=True, env=env)
