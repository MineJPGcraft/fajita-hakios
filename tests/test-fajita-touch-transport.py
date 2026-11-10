#!/usr/bin/env python3
"""Run the production GPIO/I2C routines against scripted host GPIO registers."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-touch-transport"
OUT.mkdir(parents=True, exist_ok=True)
source = (ROOT / "devices/fajita/kernel/fajita_touch_transport.inc").read_text()
# Replace only the peripheral boundary. Pinmux and every I2C routine below
# remain the actual production implementation, not a second bus algorithm.
begin = source.index("static uint32_t ft_rd(")
end = source.index("static void ft_sleep_ms", begin)
source = source[:begin] + """
static uint32_t ft_rd(vm_offset_t p) { return mock_read(p); }
static void ft_wr(vm_offset_t p, uint32_t v) { mock_write(p, v); }
""" + source[end:]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "fajita_touch_board.h"
#include "fajita_touch_live.h"
typedef uintptr_t vm_offset_t;
#define NSEC_PER_MSEC 1000000u
#define NSEC_PER_USEC 1000u
static uint32_t regs[3][2];
static uint64_t mapped_pa[3];
static uint32_t expected_irq_pa;
static unsigned maps, writes, rises, starts, stops, bit_at, bit_count;
static uint8_t bits[256], edges[256];
static bool force_scl_low, trace;
static uint64_t ticks;
static uint64_t transport_note;
static void ft_transport_note(uint64_t value) { transport_note=value; }
static void ft_phase(uint32_t p, uint32_t d) { (void)p; (void)d; }
static void ft_diag_step(const char *p, uint64_t d) { (void)p; (void)d; }
static void delay_for_interval(uint32_t n, uint32_t scale) { ticks += (uint64_t)n*scale; }
static uint64_t mach_absolute_time(void) { ticks++; return ticks; }
static void nanoseconds_to_absolutetime(uint64_t ns, uint64_t *out) { *out=ns; }
static vm_offset_t ml_io_map(uint64_t pa, size_t length) {
    assert(length==0x1000 && maps<3); mapped_pa[maps++]=pa;
    if (pa==FAJITA_TOUCH_SDA_PA) return (vm_offset_t)regs[0];
    if (pa==FAJITA_TOUCH_SCL_PA) return (vm_offset_t)regs[1];
    assert(pa==expected_irq_pa); return (vm_offset_t)regs[2];
}
static unsigned pin_at(vm_offset_t p, unsigned *off) {
    for (unsigned i=0;i<3;i++) {
        vm_offset_t base=(vm_offset_t)regs[i];
        if (p==base || p==base+4) { *off=(unsigned)(p-base)/4; return i; }
    }
    assert(false); return 0;
}
static bool released(unsigned pin) { return !(regs[pin][0]&(1u<<9)); }
static uint32_t mock_read(vm_offset_t p) {
    unsigned off, pin=pin_at(p,&off);
    if (!off) return regs[pin][0];
    if (!released(pin)) return regs[pin][1]&2u;
    if (pin==1) return force_scl_low ? 0 : 1;
    if (pin==0 && trace) { assert(bit_at<bit_count); return bits[bit_at++]; }
    return (regs[pin][1]&2u)|1u;
}
static void mock_write(vm_offset_t p,uint32_t value) {
    unsigned off, pin=pin_at(p,&off);
    bool sda=released(0), scl=released(1);
    regs[pin][off]=value; writes++;
    /* SDA/SCL must never actively drive a high level. */
    if (pin<2 && !released(pin)) assert(!(regs[pin][1]&2u));
    if (!trace) return;
    if (scl && released(1) && sda!=released(0)) {
        if (released(0)) stops++; else starts++;
    }
    if (!scl && released(1) && !force_scl_low) {
        assert(rises<sizeof(edges)); edges[rises++]=released(0);
    }
}
'''
tests = r'''
static void queued(unsigned v) { assert(bit_count<sizeof(bits)); bits[bit_count++]=(uint8_t)v; }
static void queue_byte(unsigned value) { for(int n=7;n>=0;n--) queued((value>>n)&1u); }
static unsigned sent(unsigned start) {
    unsigned v=0; assert(start+8<=rises);
    for(unsigned n=0;n<8;n++) v=(v<<1)|edges[start+n];
    return v;
}
static void begin_trace(void) {
    bit_at=bit_count=rises=starts=stops=0; trace=true;
}
static void end_trace(void) { assert(bit_at==bit_count); trace=false; }
static void exercise_bus(uint32_t layout) {
    maps=writes=rises=starts=stops=bit_at=bit_count=0;
    force_scl_low=trace=false; ticks=transport_note=0;
    memset(mapped_pa,0,sizeof(mapped_pa));
    expected_irq_pa=layout==1 ? 0x0397d000u : 0x0357d000u;
    const uint32_t original[3][2]={{4,0},{4,0},{0x103,2}};
    memcpy(regs,original,sizeof(regs));
    assert(FAJITA_TOUCH_ACTIVE_IRQ_CTL==0x1c3u);
    assert(fajita_bus_select_layout(layout));
    assert(fajita_bus_init() && maps==3);
    assert(mapped_pa[0]==0x03931000u && mapped_pa[1]==0x03932000u && mapped_pa[2]==expected_irq_pa);
    /* A running transport may not change the silicon mapping underneath I/O. */
    assert(!fajita_bus_select_layout(layout==1 ? 2 : 1));
    assert(ft_pin_pa[FT_IRQ]==expected_irq_pa);
    assert(regs[0][0]==0 && regs[1][0]==0 && regs[2][0]==0x1c3u);
    assert(ft_gpio_pa((vm_offset_t)regs[0]+4)==FAJITA_TOUCH_SDA_PA+4);
    assert(ft_gpio_pa(1)==0 && !fajita_irq_pending());
    ft_sleep_ms(1);

    begin_trace(); queued(1); queued(0); queued(0); queued(0); queued(1); queued(1);
    const uint8_t page=0;
    assert(fajita_i2c_write(0xff,&page,1)); end_trace();
    assert(sent(0)==0x40 && sent(9)==0xff && sent(18)==0);
    assert(starts==1 && stops==1 && released(0) && released(1));
    assert((transport_note&0xffu)==FT_BUS_OK && ((transport_note>>16)&0xffu)==0xff);

    begin_trace(); queued(1); queued(0); queued(0); queued(1); queued(0);
    queue_byte(0x53); queue_byte(0xa6); queued(1); queued(1);
    uint8_t bytes[2]={0};
    assert(fajita_i2c_read(0x13,bytes,2)); end_trace();
    assert(bytes[0]==0x53 && bytes[1]==0xa6);
    assert(sent(0)==0x40 && sent(9)==0x13 && sent(19)==0x41);
    assert(starts==2 && stops==1);
    /* Read byte 1 is ACKed; byte 2 is NACKed before STOP. */
    assert(edges[36]==0 && edges[45]==1);
    assert((transport_note&0xffu)==FT_BUS_OK && (transport_note&(1u<<15)));

    begin_trace(); queued(1); queued(1); queued(1); queued(1); queued(1);
    assert(!fajita_i2c_write(0xff,&page,1)); end_trace();
    assert(sent(0)==0x40 && starts==1 && stops==2);
    assert(released(0) && released(1));
    assert((transport_note&0xffu)==FT_BUS_WRITE_ADDRESS_NACK);

    unsigned before=writes;
    assert(!fajita_i2c_read(0xff,bytes,2));
    assert(!fajita_i2c_write(0xff,bytes,2));
    assert(!fajita_i2c_read(0,bytes,0) && !fajita_i2c_write(0,NULL,1));
    assert(writes==before);
    assert((transport_note&0xffu)==FT_BUS_ARGUMENT);

    force_scl_low=true; ticks=0;
    assert(!fajita_i2c_write(0xff,&page,1));
    assert(ticks<UINT64_C(10000000)); /* bounded stretch + recovery */
    assert((transport_note&0xffu)==FT_BUS_SCL_TIMEOUT);
    force_scl_low=false;
    fajita_bus_restore();
    for(unsigned i=0;i<3;i++) {
        assert(regs[i][0]==original[i][0] && regs[i][1]==(original[i][1]&2u));
    }
    before=writes; fajita_bus_restore(); assert(writes==before);
    /* Unknown line levels must not turn a write into a read in telemetry. */
    assert(!fajita_i2c_write(0,NULL,1));
    assert((transport_note&0xffu)==FT_BUS_ARGUMENT);
    assert((transport_note&(1u<<10)) && !(transport_note&(1u<<15)));
    assert(((transport_note>>8)&3u)==0 && writes==before);
    assert(!fajita_i2c_read(0,bytes,0));
    assert((transport_note&(1u<<10)) && (transport_note&(1u<<15)));
    assert(((transport_note>>8)&3u)==0 && writes==before);
}
int main(void) {
    assert(!fajita_bus_init() && !maps && !writes);
    for (uint32_t layout=0;layout<5;layout++) {
        if(layout==1 || layout==2) continue;
        assert(!fajita_bus_select_layout(layout));
        assert(!fajita_bus_init() && !maps && !writes);
        for(unsigned pin=0;pin<FT_PIN_COUNT;pin++) assert(ft_pin_pa[pin]==0 && ft_pin[pin]==0);
    }
    exercise_bus(1); exercise_bus(2);
    assert(!fajita_bus_select_layout(0));
    unsigned prior_maps=maps,prior_writes=writes;
    assert(!fajita_bus_init() && maps==prior_maps && writes==prior_writes);
    puts("touch transport: v1/v2 layout gates, DT pinmux, three-page scope, addresses, repeated START, ACK/NACK, recovery and restore passed");
}
'''
path = OUT / "transport.c"
path.write_text(harness + "\n" + source + "\n" + tests)
binary = OUT / "transport"
subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g",
                "-I", str(ROOT / "devices/fajita/kernel"), str(path), "-o", str(binary)], check=True)
env = dict(os.environ)
env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
subprocess.run([str(binary)], check=True, env=env)
