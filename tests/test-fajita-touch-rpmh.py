#!/usr/bin/env python3
"""Exercise the actual loader backend with only hardware/UEFI boundaries mocked."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-touch-rpmh"
OUT.mkdir(parents=True, exist_ok=True)
source = (ROOT / "devices/fajita/loader/fajita_touch_rpmh.inc").read_text()


def replace_body(name, body):
    global source
    start = source.index("{", source.index(name + "("))
    depth, end = 1, start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    source = source[:start] + "{ " + body + " }" + source[end:]


for name, body in {
    "fp_read": "return mock_read(pa);",
    "fp_write": "mock_write(pa, value);",
    "fp_frequency": "return frequency;",
    "fp_counter": "if (!stopped) ticks += 192; mock_advance(); return ticks;",
    "fp_daif": "return daif_state;",
    "fp_mask_interrupts": "assert(tpl_state == 31); daif_state |= 0xc0;",
    "fp_restore_interrupts": "assert(tpl_state == 4); daif_state = daif;",
    "fp_database": "assert(pa == db_pa); maps++; return table;",
    "fp_chip_layout": "chip_queries++; return mock_layout;",
}.items():
    replace_body(name, body)
# Retain the real hold/report path, replacing only its architectural WFE.
source = source.replace('__asm__ volatile ("wfe");', 'longjmp(held, 1);')
assert "__asm__" not in source

harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <setjmp.h>
#include "fajita_touch_board.h"
#include "fajita_rpmh_transaction.h"
#define EFIAPI
typedef uint64_t efi_status;
static struct { void *raise_tpl, *restore_tpl; } boot_services;
static typeof(boot_services) *BS = &boot_services;
static const int platform_fajita, other_platform;
static const int *PLAT = &platform_fajita;
static int screen_quiet;
static char log_buf[4096];
static size_t log_len;
static uint32_t framebuffer[64*32];
static struct { uint64_t base; uint32_t width,height,stride; } SCREEN;
static unsigned tpl_state, raises, restores, reads, writes, gpio_writes, rsc_writes, maps;
static unsigned triggers, selected, clean_calls, held_panel_calls;
static unsigned mock_layout, chip_queries;
static uint64_t frequency, ticks, daif_state;
static uint32_t table_size, db_pa, geometry;
static uint8_t table[512];
static uint32_t rsc[0x3000/4], pin[3][2];
static uint32_t last_gpio_io_write[3];
/* GPIO_IN is a physical sample, independent of the output latch. The mock
 * must never manufacture input-loopback success from a write to GPIO_OUT. */
static bool pin_input_high[3], pin_control_write_ignored[3], pin_output_write_ignored[3];
static bool stopped, suppress_completion_irq, no_command_completion, keep_engine_busy, bad_completion;
static bool deferred_completion, completion_pending;
static uint64_t completion_at;
static unsigned incomplete_reads, busy_reads, command_completions;
static jmp_buf held;
static struct {unsigned pin; bool high; uint64_t at;} gpio_events[32];
static unsigned ngpio;
static size_t str_len(const char *s) { return strlen(s); }
static int starts_with(const char *s, const char *prefix) { return !strncmp(s,prefix,strlen(prefix)); }
static void clean_dcache(uint64_t start,uint64_t end) { assert(start < end); clean_calls++; }
static void fajita_touch_panel_update(const void *live, uint64_t slide) {
    assert(live==NULL && slide==0 && screen_quiet && tpl_state==31);
    held_panel_calls++;
}
static void print(const char *fmt, ...) {
    /* Actual firmware console lowers TPL: the held path must suppress it. */
    assert(tpl_state != 31 || screen_quiet);
    size_t n = strlen(fmt); assert(log_len+n < sizeof(log_buf));
    memcpy(log_buf+log_len, fmt,n); log_len+=n;
}
static uint64_t mock_raise(uint64_t tpl) {
    assert(tpl==31 && tpl_state==4); raises++; tpl_state=31; daif_state|=0x80; return 4;
}
static void mock_restore(uint64_t tpl) {
    assert(tpl==4 && tpl_state==31 && (daif_state&0xc0)==0xc0);
    restores++; tpl_state=4; daif_state &= ~0x80u;
}
static uint32_t *gpio(uint64_t pa,unsigned *index,unsigned *offset) {
    const uint32_t bases[]={
        fajita_touch_gpio_pa(mock_layout,FAJITA_TOUCH_RESET_GPIO),
        fajita_touch_gpio_pa(mock_layout,FAJITA_TOUCH_VDDIO_GPIO),
        fajita_touch_gpio_pa(mock_layout,FAJITA_TOUCH_IRQ_GPIO)};
    for(unsigned i=0;i<3;i++) if(pa==bases[i] || pa==bases[i]+4u) {
        *index=i; *offset=(unsigned)(pa-bases[i])/4; return pin[i];
    }
    return NULL;
}
/* The engine advances only with the independent architectural timer, never
 * because production chose to read a particular CMD/IRQ/STATUS register. */
static void mock_advance(void) {
    if(!completion_pending || ticks<completion_at) return;
    assert(triggers==1 && selected<2);
    unsigned slot=selected*FAJITA_RPMH_TCS_STRIDE;
    if(!no_command_completion&&!bad_completion) {
        for(unsigned n=0;n<3;n++) {
            if(ticks>=completion_at+(uint64_t)n*1920) {
                unsigned at=(slot+FAJITA_RPMH_CMD_STATUS+n*FAJITA_RPMH_CMD_STRIDE)/4;
                if(rsc[at]!=FAJITA_RPMH_COMMAND_OK) command_completions++;
                rsc[at]=FAJITA_RPMH_COMMAND_OK;
            }
        }
    }
    if(ticks>=completion_at+5760) {
        if(!no_command_completion&&!keep_engine_busy) rsc[(slot+FAJITA_RPMH_STATUS)/4]=1;
        if(!no_command_completion&&!suppress_completion_irq) rsc[FAJITA_RPMH_IRQ_STATUS/4]|=1u<<selected;
        completion_pending=false;
    }
}
static uint32_t mock_read(uint64_t pa) {
    reads++;
    if(pa==FAJITA_TOUCH_CMD_DB_DICT_PA) return db_pa;
    if(pa==FAJITA_TOUCH_CMD_DB_DICT_PA+4u) return table_size;
    if(pa==FAJITA_TOUCH_RSC_CONFIG_PA+0xcu) return geometry;
    if(pa>=FAJITA_TOUCH_RSC_TCS_PA && pa<FAJITA_TOUCH_RSC_TCS_PA+0x3000u) {
        assert(tpl_state==31 && (daif_state&0xc0)==0xc0);
        unsigned off=(unsigned)(pa-FAJITA_TOUCH_RSC_TCS_PA);
        uint32_t value=rsc[off/4];
        if(triggers && selected<2) {
            unsigned slot=selected*FAJITA_RPMH_TCS_STRIDE;
            if(off==slot+FAJITA_RPMH_STATUS && !value) busy_reads++;
            for(unsigned n=0;n<3;n++) {
                if(off==slot+FAJITA_RPMH_CMD_STATUS+n*FAJITA_RPMH_CMD_STRIDE &&
                   (value&FAJITA_RPMH_COMMAND_OK)!=FAJITA_RPMH_COMMAND_OK) incomplete_reads++;
            }
        }
        return value;
    }
    unsigned index,off; uint32_t *p=gpio(pa,&index,&off); assert(p);
    return off ? ((p[1]&~1u)|(pin_input_high[index]?1u:0u)) : p[0];
}
static void mock_write(uint64_t pa,uint32_t v) {
    writes++;
    if(pa>=FAJITA_TOUCH_RSC_TCS_PA && pa<FAJITA_TOUCH_RSC_TCS_PA+0x3000u) {
        assert(tpl_state==31 && (daif_state&0xc0)==0xc0); rsc_writes++;
        unsigned off=(unsigned)(pa-FAJITA_TOUCH_RSC_TCS_PA);
        if(off==FAJITA_RPMH_IRQ_CLEAR) { rsc[FAJITA_RPMH_IRQ_STATUS/4]&=~v; return; }
        rsc[off/4]=v;
        if(off%FAJITA_RPMH_TCS_STRIDE==FAJITA_RPMH_CONTROL && (v&FAJITA_RPMH_TRIGGER)) {
            selected=off/FAJITA_RPMH_TCS_STRIDE; assert(selected<2); triggers++;
            assert(!(rsc[FAJITA_RPMH_IRQ_ENABLE/4]&(1u<<selected)));
            rsc[(selected*FAJITA_RPMH_TCS_STRIDE+FAJITA_RPMH_STATUS)/4]=0;
            for(unsigned n=0;n<3;n++) {
                unsigned cmd=selected*FAJITA_RPMH_TCS_STRIDE+n*FAJITA_RPMH_CMD_STRIDE;
                const unsigned data[]={3008,1,7};
                assert(rsc[(cmd+FAJITA_RPMH_MSGID)/4]==0x10108);
                assert(rsc[(cmd+FAJITA_RPMH_ADDR)/4]==0x40020+n*4);
                assert(rsc[(cmd+FAJITA_RPMH_DATA)/4]==data[n]);
                /* A new trigger resets every old command-status word. The
                 * deferred model exposes the reset before fresh completion. */
                rsc[(cmd+FAJITA_RPMH_CMD_STATUS)/4]=(deferred_completion||no_command_completion||bad_completion)?0:FAJITA_RPMH_COMMAND_OK;
            }
            if(deferred_completion) {
                completion_pending=true;completion_at=ticks+1920;
            }
            if(!deferred_completion&&!no_command_completion&&!keep_engine_busy) {
                rsc[(selected*FAJITA_RPMH_TCS_STRIDE+FAJITA_RPMH_STATUS)/4]=1;
            }
            if(!deferred_completion&&!suppress_completion_irq&&!no_command_completion) {
                rsc[FAJITA_RPMH_IRQ_STATUS/4]|=1u<<selected;
            }
        }
        return;
    }
    assert(tpl_state==4); unsigned index,off; uint32_t *p=gpio(pa,&index,&off); assert(p);
    gpio_writes++;
    if(off) {
        /* Input bit 0 is read-only; bit 1 can independently fail to latch. */
        last_gpio_io_write[index]=v;
        uint32_t mask=1u|(pin_output_write_ignored[index]?2u:0u);
        p[1]=(p[1]&mask)|(v&~mask);
        assert(ngpio<32);gpio_events[ngpio++]=(typeof(gpio_events[0])){index,(p[1]&2)!=0,ticks};
    } else if(!pin_control_write_ignored[index]) p[0]=v;
}
static void w16(unsigned o,unsigned v) { table[o]=(uint8_t)v;table[o+1]=(uint8_t)(v>>8); }
static void w32(unsigned o,unsigned v) { for(unsigned i=0;i<4;i++) table[o+i]=(uint8_t)(v>>(8*i)); }
static void init(void) {
    memset(table,0,sizeof(table));memset(rsc,0,sizeof(rsc));memset(pin,0,sizeof(pin));
    memset(last_gpio_io_write,0,sizeof(last_gpio_io_write));
    memset(pin_input_high,0,sizeof(pin_input_high));
    memset(pin_control_write_ignored,0,sizeof(pin_control_write_ignored));
    memset(pin_output_write_ignored,0,sizeof(pin_output_write_ignored));
    memset(framebuffer,0,sizeof(framebuffer));memset(log_buf,0,sizeof(log_buf));
    w32(0,1);w32(4,0x0c0330db);w16(8,4);w16(12,64);w16(14,1);
    memcpy(table+144,"ldoa28",6);w32(160,0x40020);
    table_size=sizeof(table);db_pa=FAJITA_TOUCH_CMD_DB_RESERVED_START;
    geometry=(8u<<(6*FAJITA_TOUCH_RSC_DRV_ID))|(3u<<27);
    tpl_state=4; daif_state=0x100;frequency=19200000;ticks=0;
    raises=restores=reads=writes=gpio_writes=rsc_writes=maps=triggers=ngpio=clean_calls=held_panel_calls=0;
    mock_layout=FAJITA_TOUCH_TLMM_LAYOUT_V2;chip_queries=0;
    incomplete_reads=busy_reads=command_completions=0;completion_at=0;
    deferred_completion=completion_pending=false;
    log_len=0;screen_quiet=0;stopped=suppress_completion_irq=no_command_completion=keep_engine_busy=bad_completion=false;PLAT=&platform_fajita;
    rsc[FAJITA_RPMH_STATUS/4]=1;rsc[(FAJITA_RPMH_TCS_STRIDE+FAJITA_RPMH_STATUS)/4]=1;
    rsc[FAJITA_RPMH_IRQ_ENABLE/4]=0x103;
    SCREEN.base=(uintptr_t)framebuffer;SCREEN.width=64;SCREEN.height=32;SCREEN.stride=256;
    uint64_t (*raise)(uint64_t)=mock_raise;void (*restore)(uint64_t)=mock_restore;
    memcpy(&BS->raise_tpl,&raise,sizeof(raise));memcpy(&BS->restore_tpl,&restore,sizeof(restore));
}
'''
tests = r'''
int main(void) {
    const char *on="fajita_touch=1 fajita_touch_power=1";
    const unsigned output_gpio[]={FAJITA_TOUCH_RESET_GPIO,FAJITA_TOUCH_VDDIO_GPIO};
    const unsigned layouts[]={FAJITA_TOUCH_TLMM_LAYOUT_V1,FAJITA_TOUCH_TLMM_LAYOUT_V2};
    const uint32_t output_controls[]={0,FAJITA_TOUCH_ACTIVE_IRQ_CTL};
    for(unsigned layout=0;layout<2;layout++)
    for(unsigned index=0;index<2;index++) for(unsigned input=0;input<2;input++)
    for(unsigned high=0;high<2;high++) for(unsigned control=0;control<2;control++) {
        init();mock_layout=layouts[layout];pin_input_high[index]=input!=0;
        uint32_t output_pa=fajita_touch_gpio_pa(mock_layout,output_gpio[index]);
        uint32_t old_control=0xa5a5fc00u|0x15u;
        uint32_t old_io=0x6b6b5a5cu|(high?0u:2u);
        pin[index][0]=old_control;pin[index][1]=old_io;
        uint32_t requested=(output_controls[control]|(1u<<9))&0x3ffu;
        assert(fp_pin_output(output_pa,high!=0,output_controls[control]));
        assert(pin[index][0]==((old_control&~0x3ffu)|requested));
        assert(pin[index][1]==((old_io&~2u)|(high?2u:0u)));
        assert(last_gpio_io_write[index]==((old_io&~3u)|input|(high?2u:0u)));
        assert((mock_read(output_pa+4u)&1u)==input);
        assert(gpio_writes==2 && ngpio==1 && !rsc_writes);
    }
    for(unsigned layout=0;layout<2;layout++)
    for(unsigned index=0;index<2;index++) for(unsigned input=0;input<2;input++)
    for(unsigned high=0;high<2;high++) for(unsigned fault=0;fault<2;fault++) {
        init();mock_layout=layouts[layout];pin_input_high[index]=input!=0;
        uint32_t output_pa=fajita_touch_gpio_pa(mock_layout,output_gpio[index]);
        pin[index][0]=0xa5a5fc00u|0x15u;
        pin[index][1]=0x6b6b5a5cu|(high?0u:2u);
        pin_control_write_ignored[index]=fault==0;
        pin_output_write_ignored[index]=fault==1;
        assert(!fp_pin_output(output_pa,high!=0,0));
        if(fault==0) assert(pin[index][0]==(0xa5a5fc00u|0x15u));
        else assert((pin[index][1]&2u)==(high?0u:2u));
        assert(!rsc_writes);
    }
    const uint32_t rejected_pa[]={0,FAJITA_TOUCH_IRQ_V1_PA,FAJITA_TOUCH_IRQ_V2_PA,
        FAJITA_TOUCH_RESET_PA+4u,FAJITA_TOUCH_RESET_PA+8u,
        FAJITA_TOUCH_VDDIO_V1_PA+4u,FAJITA_TOUCH_VDDIO_V2_PA+4u,UINT32_MAX};
    for(unsigned n=0;n<sizeof(rejected_pa)/sizeof(rejected_pa[0]);n++) {
        init();assert(!fp_pin_output(rejected_pa[n],true,0));
        assert(!reads && !writes && !gpio_writes && !rsc_writes && !ngpio);
    }
    init();mock_layout=0;
    assert(!fajita_touch_prepare_before_handoff(on,0,0x700));
    assert(chip_queries==1 && fajita_power_status==0x80080000u);
    /* The selected layout is carried to the boot loader for the
     * fajita_touch_tlmm= acknowledgement; an unknown chip must clear it. */
    assert(fajita_power_tlmm_layout==0);
    assert(!reads && !writes && !maps && !raises && !restores && !ngpio);
    const char *off[]={"", "fajita_touch=0 fajita_touch_power=1", "fajita_touch=1",
        "fajita_touch=11 fajita_touch_power=1", "xfajita_touch=1 fajita_touch_power=1",
        "fajita_touch=1 fajita_touch_power=1 fajita_touch=0"};
    for(unsigned i=0;i<sizeof(off)/sizeof(off[0]);i++) {
        init();assert(!fajita_touch_prepare_before_handoff(off[i],0,0x700));assert(!writes && !maps);
    }
    init();PLAT=&other_platform;assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes);
    init();assert(!fajita_touch_prepare_before_handoff(on,0x100,0x700));assert(!writes);
    init();assert(!fajita_touch_prepare_before_handoff(on,0,0));assert(!writes);
    init();BS->raise_tpl=NULL;assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes);
    init();frequency=0;assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes && !maps);
    init();frequency=1000000001;assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes);
    init();stopped=true;assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes && !maps);
    init();db_pa--;assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes && !maps);
    init();table_size=143;assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes && !maps);
    init();table[4]=0;assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes && maps==1);
    init();geometry=3u<<27;assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes);
    init();geometry=(8u<<12)|(2u<<27);assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!writes);
    init();rsc[FAJITA_RPMH_STATUS/4]=rsc[(FAJITA_RPMH_TCS_STRIDE+FAJITA_RPMH_STATUS)/4]=0;
    assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!rsc_writes && !triggers);
    assert(raises==1 && restores==1 && tpl_state==4 && daif_state==0x100);
    assert(fajita_power_status==0x80070003u);
    assert((fajita_power_rpmh_detail>>56)==FAJITA_RPMH_REASON_INITIAL_RESOURCE);
    init();rsc[FAJITA_RPMH_CONTROL/4]=rsc[(FAJITA_RPMH_TCS_STRIDE+FAJITA_RPMH_CONTROL)/4]=1;
    assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!rsc_writes && !triggers);
    assert((fajita_power_rpmh_detail>>56)==FAJITA_RPMH_REASON_NO_SLOT);
    assert((uint32_t)fajita_power_rpmh_detail==0x00020002u);
    for(unsigned mode=0;mode<2;mode++) {
        init();uint32_t control=FAJITA_RPMH_ENABLE|(mode?FAJITA_RPMH_TRIGGER:0);
        rsc[FAJITA_RPMH_CONTROL/4]=rsc[(FAJITA_RPMH_TCS_STRIDE+FAJITA_RPMH_CONTROL)/4]=control;
        assert(fajita_touch_prepare_before_handoff(on,0,0x700)==1);
        assert(triggers==1 && selected==0 && fajita_power_status==1);
        assert(rsc[FAJITA_RPMH_CONTROL/4]==FAJITA_RPMH_ENABLE);
        assert(rsc[(FAJITA_RPMH_TCS_STRIDE+FAJITA_RPMH_CONTROL)/4]==control);
        assert((fajita_power_rpmh_detail>>56)==FAJITA_RPMH_REASON_NONE);
        assert(tpl_state==4 && daif_state==0x100);
    }
    init();assert(fajita_touch_prepare_before_handoff(on,0,0x700)==1);
    assert(triggers==1 && selected==0 && raises==1 && restores==1 && daif_state==0x100);
    assert(ngpio==6 && gpio_events[0].pin==0 && !gpio_events[0].high);
    assert(gpio_events[1].pin==1 && gpio_events[1].high);
    for(unsigned n=1;n<5;n++) assert(gpio_events[n].at-gpio_events[n-1].at>=192000);
    assert(gpio_events[2].high && !gpio_events[3].high && gpio_events[4].high);
    assert(ticks-gpio_events[5].at>=1920000 && pin[2][0]==0x1c3);
    assert(rsc[FAJITA_RPMH_IRQ_ENABLE/4]==0x103 && rsc[FAJITA_RPMH_CONTROL/4]==0);
    /* Run the actual board sequence with every independent input-level
     * combination. Neither reset nor supply writes require an input echo. */
    for(unsigned layout=0;layout<2;layout++) for(unsigned inputs=0;inputs<8;inputs++) {
        init();mock_layout=layouts[layout];
        for(unsigned index=0;index<3;index++) pin_input_high[index]=(inputs&(1u<<index))!=0;
        for(unsigned index=0;index<2;index++) {
            pin[index][0]=0xa5a5fc00u;pin[index][1]=0x6b6b5a5cu;
        }
        assert(fajita_touch_prepare_before_handoff(on,0,0x700)==1);
        assert(chip_queries==1 && fajita_power_status==1 && triggers==1 && ngpio==6);
        assert(fajita_power_tlmm_layout==layouts[layout]);
        assert(pin[0][0]==(0xa5a5fc00u|FAJITA_TOUCH_ACTIVE_IRQ_CTL|(1u<<9)));
        assert(pin[1][0]==(0xa5a5fc00u|(1u<<9)));
        assert(pin[0][1]==(0x6b6b5a5cu|2u) && pin[1][1]==(0x6b6b5a5cu|2u));
        assert(!gpio_events[0].high && gpio_events[1].high && gpio_events[2].high);
        assert(!gpio_events[3].high && gpio_events[4].high && gpio_events[5].high);
        for(unsigned n=1;n<5;n++) assert(gpio_events[n].at-gpio_events[n-1].at>=192000);
        assert(ticks-gpio_events[5].at>=1920000);
    }
    /* Both kinds of failed GPIO write still fail the real board sequence. */
    for(unsigned layout=0;layout<2;layout++) for(unsigned fault=0;fault<2;fault++) {
        init();mock_layout=layouts[layout];
        pin_control_write_ignored[1]=fault==0;pin_output_write_ignored[1]=fault==1;
        assert(!fajita_touch_prepare_before_handoff(on,0,0x700));
        assert(triggers==1 && (fajita_power_status&0xffu)==FAJITA_TOUCH_POWER_VDDIO_ENABLE_FAILED);
        assert(pin[0][1]==0 && pin[1][1]==(fault==0?2u:0u));
    }
    init();suppress_completion_irq=deferred_completion=true;
    /* Seed stale success before the trigger. The timer-driven mock clears it
     * on trigger, then completes three commands and the engine independently. */
    for(unsigned n=0;n<3;n++) rsc[(FAJITA_RPMH_CMD_STATUS+n*FAJITA_RPMH_CMD_STRIDE)/4]=FAJITA_RPMH_COMMAND_OK;
    assert(fajita_touch_prepare_before_handoff(on,0,0x700)==1);
    assert(triggers==1 && fajita_power_status==1 && ngpio==6 && tpl_state==4 && daif_state==0x100);
    assert(rsc[FAJITA_RPMH_IRQ_STATUS/4]==0 && rsc[FAJITA_RPMH_IRQ_ENABLE/4]==0x103);
    assert(incomplete_reads>0 && command_completions==3 && !completion_pending);
    /* Without an observable busy/incomplete interval or completion IRQ, an
     * instantaneous all-OK sample cannot establish that this request ran. */
    init();suppress_completion_irq=true;
    if(!setjmp(held)) { (void)fajita_touch_prepare_before_handoff(on,0,0x700);assert(false); }
    assert(triggers==1 && restores==0 && tpl_state==31 && (daif_state&0xc0)==0xc0);
    assert(ngpio==1 && screen_quiet && !(rsc[FAJITA_RPMH_IRQ_ENABLE/4]&1u));
    assert(!incomplete_reads && !busy_reads && rsc[FAJITA_RPMH_IRQ_STATUS/4]==0);
    assert(rsc_writes==16 && rsc[FAJITA_RPMH_CONTROL/4]==(FAJITA_RPMH_ENABLE|FAJITA_RPMH_TRIGGER));
    assert(rsc[FAJITA_RPMH_CMD_ENABLE/4]==7 && rsc[FAJITA_RPMH_WAIT/4]==7 && rsc[FAJITA_RPMH_STATUS/4]==1);
    for(unsigned n=0;n<3;n++) assert(rsc[(FAJITA_RPMH_CMD_STATUS+n*FAJITA_RPMH_CMD_STRIDE)/4]==FAJITA_RPMH_COMMAND_OK);
    assert((fajita_power_rpmh_detail>>56)==FAJITA_RPMH_REASON_UNWITNESSED_COMPLETION);
    assert(FAJITA_RPMH_REASON_UNWITNESSED_COMPLETION==8);
    assert(strstr(log_buf,"handoff HELD") && clean_calls==2 && held_panel_calls==1);
    assert(framebuffer[64*16]==0x00ff0000 && framebuffer[64*16-1]==0);
    init();daif_state=0x3c0;rsc[FAJITA_RPMH_STATUS/4]=0;
    rsc[FAJITA_RPMH_CMD_ENABLE/4]=1;rsc[FAJITA_RPMH_ADDR/4]=0x70000;
    assert(fajita_touch_prepare_before_handoff(on,0,0x700)==1 && selected==1 && daif_state==0x3c0);
    init();rsc[FAJITA_RPMH_STATUS/4]=0;rsc[FAJITA_RPMH_CMD_ENABLE/4]=1u<<15;
    rsc[(FAJITA_RPMH_ADDR+15*FAJITA_RPMH_CMD_STRIDE)/4]=0x40028;
    assert(!fajita_touch_prepare_before_handoff(on,0,0x700));assert(!rsc_writes && !triggers && restores==1);
    for(unsigned mode=0;mode<3;mode++) {
        init();no_command_completion=mode==0;bad_completion=mode==1;keep_engine_busy=mode==2;
        if(!setjmp(held)) { (void)fajita_touch_prepare_before_handoff(on,0,0x700);assert(false); }
        assert(triggers==1 && restores==0 && tpl_state==31 && (daif_state&0xc0)==0xc0);
        assert(ngpio==1 && screen_quiet && !(rsc[FAJITA_RPMH_IRQ_ENABLE/4]&1u));
        assert(strstr(log_buf,"handoff HELD") && clean_calls==2 && held_panel_calls==1);
        assert((fajita_power_rpmh_detail>>56)==FAJITA_RPMH_REASON_POSTTRIGGER_FATAL);
        assert(framebuffer[64*16]==0x00ff0000 && framebuffer[64*16-1]==0);
    }
    puts("loader power integration: both TLMM layouts, unknown-chip no-MMIO gate, GPIO independent input/output, RMW preservation and faults; gates, timer, DB, geometry, critical section, exact sequence, both slots and fatal hold passed");
}
'''
path = OUT / "backend.c"
path.write_text(harness + "\n" + source + "\n" + tests)
includes = ["-I" + str(ROOT / "devices/fajita/kernel"), "-I" + str(ROOT / "devices/fajita/loader")]
for name, flags in (("strict", []), ("sanitized", ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"])):
    binary = OUT / name
    subprocess.run(["clang", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O1", *flags,
                    *includes, str(path), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
