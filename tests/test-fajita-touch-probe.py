#!/usr/bin/env python3
"""Compile the production RMI identity probe against scripted I2C."""
from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-touch-probe"
OUT.mkdir(parents=True, exist_ok=True)
source = (ROOT / "devices/fajita/kernel/fajita_synaptics.c").read_text()
# The identity probe may only select a page and read; exclude every helper that
# can write controller configuration, including the interrupt-mask helper.
config = source[source.index("static bool ft_page_zero(void)"):
                source.index("static bool ft_interrupt_mask(bool enable)")]
probe = source[source.index("static uint64_t ft_probe_pack_bytes"):
               source.index('#include "fajita_touch_layout_probe.inc"')]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "fajita_touch_live.h"

struct operation { bool read; uint8_t reg; size_t length; uint8_t bytes[16]; };
static struct operation operations[8];
static size_t queued, consumed;
static uint8_t ft_f01[6], ft_f12[6];
struct fajita_touch_live fajita_touch_live;

static struct operation *next_op(bool read, uint8_t reg, size_t length)
{
    assert(consumed < queued);
    struct operation *op = &operations[consumed++];
    assert(op->read == read && op->reg == reg && op->length == length);
    return op;
}
static bool fajita_i2c_read(uint8_t reg, uint8_t *bytes, size_t length)
{
    struct operation *op = next_op(true, reg, length);
    memcpy(bytes, op->bytes, length);
    return true;
}
static bool fajita_i2c_write(uint8_t reg, const uint8_t *bytes, size_t length)
{
    struct operation *op = next_op(false, reg, length);
    assert(memcmp(bytes, op->bytes, length) == 0);
    return true;
}
static void ft_phase(uint32_t phase, uint32_t detail) { (void)phase; (void)detail; }
static unsigned metrics;
static void ft_metric(volatile uint64_t *field, uint64_t value)
{
    assert(field == &fajita_touch_live.transport || field == &fajita_touch_live.reports ||
        field == &fajita_touch_live.attention_active || field == &fajita_touch_live.posted_events);
    *field = value; metrics++;
}
'''
tests = r'''
static void queue(bool read, uint8_t reg, const uint8_t *bytes, size_t length)
{
    assert(queued < sizeof(operations) / sizeof(operations[0]) && length <= sizeof(operations[0].bytes));
    struct operation *op = &operations[queued++];
    *op = (struct operation){.read=read,.reg=reg,.length=length};
    memcpy(op->bytes,bytes,length);
}
static void queue_read(uint8_t reg,const uint8_t *bytes,size_t length) { queue(true,reg,bytes,length); }
static void queue_write(uint8_t reg,const uint8_t *bytes,size_t length) { queue(false,reg,bytes,length); }
static void queue_page(void) { const uint8_t zero=0; queue_write(0xff,&zero,1); }
static void reset(void)
{
    queued=consumed=metrics=0; memset(operations,0,sizeof(operations));
    memset(ft_f01,0,sizeof(ft_f01)); memset(ft_f12,0,sizeof(ft_f12));
    memset(&fajita_touch_live,0,sizeof(fajita_touch_live));
}
static void done(void) { assert(consumed==queued); }

int main(void)
{
    const uint8_t f01[6]={0x10,0x11,0x20,0x30,0,1};
    const uint8_t f12[6]={0x40,0x41,0x50,0x60,1,0x12};
    const uint8_t id[10]={'S','3','7','0','6','B','-','1','2','3'};
    const uint8_t status=0x01;
    uint32_t result=0;

    reset(); queue_page(); queue_read(0xe3,f01,6); queue_read(0xdd,f12,6);
    queue_read(0x1b,id,10); queue_read(0x30,&status,1);
    assert(ft_probe_identity(&result)); done();
    assert(result==status && metrics==4);
    assert(fajita_touch_live.transport==UINT64_C(0x5333373036422d31));
    assert(fajita_touch_live.reports==UINT64_C(0x3233000000000000));
    assert(fajita_touch_live.attention_active==UINT64_C(0x0000101120300001));
    assert(fajita_touch_live.posted_events==UINT64_C(0x0000404150600112));

    /* F01 identity remains usable when this panel exposes a different F12 descriptor. */
    reset(); queue_page(); queue_read(0xe3,f01,6);
    const uint8_t alternate_f12[6]={0x40,0x41,0x50,0x60,1,0x34};
    queue_read(0xdd,alternate_f12,6); queue_read(0x1b,id,10); queue_read(0x30,&status,1);
    assert(ft_probe_identity(&result)); done();
    assert(result==status && fajita_touch_live.transport==UINT64_C(0x5333373036422d31));
    assert(fajita_touch_live.posted_events==UINT64_C(0x0000404150600134));

    /* A non-RMI F01 is displayed raw, but never used to derive an ID address. */
    reset(); queue_page();
    const uint8_t wrong_f01[6]={0x10,0x11,0x20,0x30,0,0x55};
    queue_read(0xe3,wrong_f01,6);
    const uint8_t wrong_f12[6]={0x40,0x41,0x50,0x60,1,0x55};
    queue_read(0xdd,wrong_f12,6);
    assert(!ft_probe_identity(&result)); done();
    assert(result==3 && metrics==4 && fajita_touch_live.transport==0 &&
        fajita_touch_live.reports==0 && fajita_touch_live.attention_active==UINT64_C(0x0000101120300055));

    /* Reject a query base whose +11 address would wrap to register zero. */
    reset(); queue_page();
    const uint8_t wrapping_f01[6]={236,0x11,0x20,0x30,0,1};
    queue_read(0xe3,wrapping_f01,6); queue_read(0xdd,f12,6);
    assert(!ft_probe_identity(&result)); done();
    assert(result==3 && metrics==4 && fajita_touch_live.transport==0);

    /* Query base 234 keeps the final Product ID byte below page select. */
    reset();
    const uint8_t last_valid_f01[6]={234,0x11,0x20,0x30,0,1};
    queue_page(); queue_read(0xe3,last_valid_f01,6); queue_read(0xdd,f12,6);
    queue_read(245,id,10); queue_read(0x30,&status,1);
    assert(ft_probe_identity(&result)); done();
    assert(result==status && fajita_touch_live.transport==UINT64_C(0x5333373036422d31));

    /* Query base 235 would include the 0xff page selector. */
    reset();
    const uint8_t query_wrap_f01[6]={235,0x11,0x20,0x30,0,1};
    queue_page(); queue_read(0xe3,query_wrap_f01,6); queue_read(0xdd,f12,6);
    assert(!ft_probe_identity(&result)); done();
    assert(result==3 && metrics==4 && fajita_touch_live.transport==0);

    /* Never treat the RMI page selector itself as the F01 status register. */
    reset();
    const uint8_t page_status_f01[6]={0x10,0x11,0x20,0xff,0,1};
    queue_page(); queue_read(0xe3,page_status_f01,6); queue_read(0xdd,f12,6);
    assert(!ft_probe_identity(&result)); done();
    assert(result==3 && metrics==4 && fajita_touch_live.transport==0);

    puts("touch probe: fixed-address page/PDT/Product-ID/status reads, no scan writes, packed identity and mismatch guard passed");
}
'''
path = OUT / "probe.c"
path.write_text(harness + "\n" + config + "\n" + probe + "\n" + tests)
binary = OUT / "probe"
subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g",
                "-I", str(ROOT / "devices/fajita/kernel"), str(path), "-o", str(binary)], check=True)
env = dict(os.environ)
env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
subprocess.run([str(binary)], check=True, env=env)
