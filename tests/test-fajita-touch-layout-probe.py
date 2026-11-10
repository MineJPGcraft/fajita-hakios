#!/usr/bin/env python3
"""Exercise the production bounded F12 layout probe with packet I2C mocks."""
from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-touch-layout-probe"
OUT.mkdir(parents=True, exist_ok=True)
driver = (ROOT / "devices/fajita/kernel/fajita_synaptics.c").read_text()
pack = driver[driver.index("static uint64_t ft_probe_pack_bytes"):
              driver.index("static void ft_probe_publish")]
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "fajita_touch_live.h"
struct fajita_touch_live fajita_touch_live;
struct operation { bool read; uint8_t reg; size_t length; uint8_t bytes[16]; };
static struct operation ops[24];
static size_t queued, consumed, fail_at;
static unsigned metrics;
static struct operation *next(bool read, uint8_t reg, size_t length)
{
    assert(consumed < queued);
    struct operation *op = &ops[consumed++];
    assert(op->read == read && op->reg == reg && op->length == length);
    return op;
}
static bool fajita_i2c_read(uint8_t reg, uint8_t *bytes, size_t length)
{
    struct operation *op = next(true, reg, length);
    if (consumed == fail_at) { return false; }
    memcpy(bytes, op->bytes, length); return true;
}
static bool ft_page_zero(void)
{
    /* This is the ONLY allowed write, including failure paths. */
    struct operation *op = next(false, 0xff, 1);
    assert(op->bytes[0] == 0); return consumed != fail_at;
}
static void ft_metric(volatile uint64_t *field, uint64_t value)
{
    assert(field == &fajita_touch_live.transport || field == &fajita_touch_live.reports ||
        field == &fajita_touch_live.attention_active || field == &fajita_touch_live.posted_events);
    *field = value; metrics++;
}
'''
tests = r'''
static void reset(void)
{
    queued = consumed = fail_at = metrics = 0;
    memset(ops, 0, sizeof(ops)); memset(&fajita_touch_live, 0, sizeof(fajita_touch_live));
}
static void queue(bool read, uint8_t reg, size_t length, const uint8_t *bytes)
{
    assert(queued < 24 && length <= 16);
    ops[queued] = (struct operation){.read=read,.reg=reg,.length=length};
    memcpy(ops[queued++].bytes,bytes,length);
}
static void byte(uint8_t reg,uint8_t value) { queue(true,reg,1,&value); }
static void prefix(uint8_t q0)
{
    const uint8_t zero=0, f01[6]={0x2b,0x22,0,6,1,1}, f12[6]={0x4a,0,0x13,8,1,0x12};
    const uint8_t short01[4]={0x2b,0x22,0x0d,6};
    const uint8_t id[10]={'S','3','7','0','6','B',0,0,0,0};
    queue(false,0xff,1,&zero); queue(true,0xe3,6,f01); queue(true,0xdd,6,f12);
    queue(true,0xe3,4,short01); queue(true,0xdd,4,f12);
    queue(true,0x36,10,id); byte(6,0); byte(0x0d,0x80); byte(0x0e,0x7f); byte(0x4a,q0);
}
static void descriptors(uint8_t len5,uint8_t len8)
{
    const uint8_t q5[9]={2,0,1,0x80,0x10,0,0,0x55,0xaa};
    const uint8_t q8[5]={0,2,0x80,0x12,0x34}, c23[2]={1,10};
    byte(0x4e,len5); queue(true,0x4f,len5<9?len5:9,q5);
    byte(0x51,len8); queue(true,0x52,len8<5?len8:5,q8);
    if (len5>=4) { queue(true,0x14,2,c23); }
    if (len5>=5) { byte(0x15,0xff); }
}
static void full(void) { prefix(1); descriptors(9,5); }
int main(void)
{
    uint32_t result=999;
    reset(); full(); assert(ft_probe_layout(&result) && !result && consumed==queued && metrics==4);
    assert(fajita_touch_live.transport==UINT64_C(0x0902000180100000));
    assert(fajita_touch_live.reports==UINT64_C(0x05000280123455aa));
    assert(fajita_touch_live.attention_active==UINT64_C(0x2b220d064a001308));
    assert(fajita_touch_live.posted_events==UINT64_C(0x0100807f010affbd));

    /* Replacements without descriptors must not touch Query4/5/7/8/controls. */
    reset(); prefix(0); assert(ft_probe_layout(&result) && !result && consumed==queued && metrics==4);
    assert(!fajita_touch_live.transport && !fajita_touch_live.reports);
    assert(fajita_touch_live.posted_events==UINT64_C(0x0000807f000000c1));

    /* Advertised oversized packets are bounded but their original sizes survive. */
    reset(); prefix(1); descriptors(255,255);
    assert(ft_probe_layout(&result) && !result && consumed==queued);
    assert(fajita_touch_live.transport==UINT64_C(0xff02000180100000));
    assert(fajita_touch_live.reports==UINT64_C(0xff000280123455aa));

    /* Short presence maps never imply missing bits are present. */
    reset(); prefix(1); descriptors(2,1);
    assert(ft_probe_layout(&result) && !result && consumed==queued);
    assert((fajita_touch_live.posted_events & 0xff)==0x81);

    /* Stop immediately on every transport error; publish exactly four fields. */
    const uint32_t expected[16]={1,2,2,4,4,5,6,6,6,7,8,9,10,11,12,13};
    for (size_t n=1;n<=16;n++) {
        reset(); full(); fail_at=n;
        assert(!ft_probe_layout(&result) && result==expected[n-1] && consumed==n && metrics==4);
    }
    /* Wrong function IDs cannot be used as address bases. */
    reset(); full(); ops[1].bytes[5]=0x55;
    assert(!ft_probe_layout(&result) && result==3 && consumed==3 && metrics==4);
    reset(); full(); ops[2].bytes[5]=0x11;
    assert(!ft_probe_layout(&result) && result==3 && consumed==3);
    /* Wrong family: no F12 query/control reads. */
    reset(); full(); ops[5].bytes[0]='X';
    assert(!ft_probe_layout(&result) && result==5 && consumed==6);
    /* Identity must end before 0xff, never wrap uint8_t. */
    reset(); full(); ops[3].bytes[0]=235;
    assert(!ft_probe_layout(&result) && result==5 && consumed==5);
    /* Control+1 and extended-query ranges cannot hit the page selector. */
    reset(); full(); ops[3].bytes[2]=254;
    ops[7].reg=254;
    assert(!ft_probe_layout(&result) && result==6 && consumed==8);
    reset(); full(); ops[4].bytes[0]=251; ops[9].reg=251;
    assert(!ft_probe_layout(&result) && result==8 && consumed==10);
    reset(); full(); ops[10].bytes[0]=0;
    assert(!ft_probe_layout(&result) && result==9 && consumed==11);

    unsigned offset=999; uint8_t map[9]={0}; uint8_t dummy=0;
    assert(!ft_probe_control(map,9,64,&offset));
    for (unsigned n=0;n<64;n++) {
        memset(map,0,sizeof(map)); map[1+n/8]=(uint8_t)(1u<<(n%8));
        assert(ft_probe_control(map,9,n,&offset) && offset==0);
        assert(!ft_probe_control(map,(uint8_t)(1+n/8),n,&offset));
    }
    memset(map,0xff,sizeof(map));
    assert(ft_probe_control(map,9,63,&offset) && offset==63);
    reset(); assert(!ft_probe_range(254,1,1,&dummy) && !consumed);
    assert(!ft_probe_range(0,0,0,&dummy) && !consumed);
    assert(!ft_probe_range(254,0,2,&dummy) && !consumed);
    puts("touch layout probe: packet boundaries, four/six-byte PDT, Query0 gate, presence-derived control prefixes, failure/overflow guards passed");
}
'''
source = OUT / "layout.c"
source.write_text(prelude + pack + '\n#include "fajita_touch_layout_probe.inc"\n' + tests)
binary = OUT / "layout"
subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g",
                "-I", str(ROOT / "devices/fajita/kernel"), str(source), "-o", str(binary)], check=True)
env = dict(os.environ)
env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
subprocess.run([str(binary)], check=True, env=env)
