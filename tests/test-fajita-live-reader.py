#!/usr/bin/env python3
"""Host-test the actual loader readers with synthetic live metadata, never USB."""
from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/tests/fajita-live-reader'
OUT.mkdir(parents=True, exist_ok=True)
loader = (ROOT / 'ref/dipper-xnu/work/hackios/xnuloader/loader.c').read_text()
kernel = (ROOT / 'ref/dipper-xnu/work/hackios/xnu-src/xnu-8796.121.2/osfmk/arm64/loader_return.c').read_text()
schema = loader[loader.index('struct kernel_live {'):loader.index('/*\n * Battery state shared')]
schema = schema.replace('static void fajita_touch_panel_update(const struct kernel_live *live, uint64_t slide);\n', '')
kernel_schema = kernel[kernel.index('struct loader_live {'):kernel.index('/*\n * A screen recording')]
readers = loader[loader.index('static int\nlive_range_contains('):loader.index('/* A call of the firmware\'s power interface')]
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
'''
prelude += '#include "' + str(ROOT / 'devices/fajita/kernel/fajita_touch_live.h') + '"\n'
prelude += '#include "' + str(ROOT / 'devices/fajita/loader/fajita_log_tail.h') + '"\n'
mocks = r'''
static char output[8192];
static size_t used;
static unsigned flushes;
static uint64_t live_kernel_lo,live_kernel_hi;
static void put_char(char c) { assert(used+1<sizeof(output)); output[used++]=c; output[used]=0; }
static void append(const char *s) { while (*s) put_char(*s++); }
static void print(const char *format,...) {
    va_list ap; va_start(ap,format);
    char line[256];
    if (!strncmp(format,"FT=%d",5)) {
        uint64_t phase=va_arg(ap,uint64_t);
        unsigned long long detail=va_arg(ap,unsigned long long);
        const char *name=va_arg(ap,const char *);
        uint64_t seconds=va_arg(ap,uint64_t);
        snprintf(line,sizeof(line),"FT=%llu data=%llx %s (%llu s)\n",(unsigned long long)phase,detail,name,(unsigned long long)seconds);
        append(line);
    } else if (!strncmp(format,"%d s: the kernel",16)) {
        uint64_t seconds=va_arg(ap,uint64_t),len=va_arg(ap,uint64_t),beat=va_arg(ap,uint64_t);
        snprintf(line,sizeof(line),"%llu s len=%llu heartbeat=%llu\n",(unsigned long long)seconds,(unsigned long long)len,(unsigned long long)beat);
        append(line);
    } else if (!strcmp(format,"%d s\n")) {
        uint64_t seconds=va_arg(ap,uint64_t);
        snprintf(line,sizeof(line),"%llu s\n",(unsigned long long)seconds); append(line);
    } else { append(format); }
    va_end(ap);
}
static void flush_dcache(uint64_t start,uint64_t end) { assert(end>=start && end-start<=240); flushes++; }
'''
tests = r'''
_Static_assert(offsetof(struct kernel_live,touch_live)==112,"loader pointer offset");
_Static_assert(offsetof(struct loader_live,touch_live)==112,"kernel pointer offset");
_Static_assert(sizeof(struct loader_live)==sizeof(struct kernel_live),"paired live layouts");
static void reset(void) { used=flushes=0; output[0]=0; }
int main(void) {
    char buffer[12]={'H','E','A','D','E','W','!','x','O','L','D','N'};
    uint32_t len=11,at=3; uint8_t wrapped=1;
    struct fajita_touch_live touch __attribute__((aligned(64)))={
        .magic=FAJITA_TOUCH_LIVE_MAGIC,.size=sizeof(touch),
        .state=((uint64_t)FT_PHASE_TLMM_READ<<32)|0x03931004u
    };
    struct kernel_live live={.heartbeat=7,.log=(uintptr_t)buffer,.log_len=(uintptr_t)&len,
        .log_ring_at=(uintptr_t)&at,.log_wrapped=(uintptr_t)&wrapped,.log_head=4,.log_ring=8,
        .magic3=FAJITA_LIVE_MAGIC3,.size=sizeof(live),.touch_live=(uintptr_t)&touch};
    uint64_t addresses[]={(uintptr_t)buffer,(uintptr_t)&len,(uintptr_t)&at,(uintptr_t)&wrapped,(uintptr_t)&touch,(uintptr_t)&live};
    live_kernel_lo=UINT64_MAX; live_kernel_hi=0;
    for (unsigned i=0;i<6;i++) { if(addresses[i]<live_kernel_lo)live_kernel_lo=addresses[i]; if(addresses[i]+sizeof(live)>live_kernel_hi)live_kernel_hi=addresses[i]+sizeof(live); }
    reset(); note_kernel_progress(&live,0,32);
    assert(strstr(output,"xOLDNEW!\n") && !strstr(output,"HEADEW!"));
    reset(); wrapped=0; at=3; len=7; note_kernel_progress(&live,0,2);
    assert(strstr(output,"HEADEW!\n"));
    reset(); len=99; note_kernel_progress(&live,0,2);
    assert(strstr(output,"metadata changed") && !strstr(output,"HEADEW!"));
    reset(); note_touch_progress(&live,0,32);
    assert(strstr(output,"FT=9 data=3931004 TLMM-read (32 s)"));
    reset(); live.magic3=0; live.touch_live=0xbad;
    note_touch_progress(&live,0,32); assert(strstr(output,"not available") && flushes==1);
    reset(); live.magic3=FAJITA_LIVE_MAGIC3; live.size=96;
    note_touch_progress(&live,0,32); assert(strstr(output,"not available") && flushes==1);
    reset(); live.size=sizeof(live); live.touch_live=UINT64_MAX-8;
    note_touch_progress(&live,0,32); assert(strstr(output,"invalid state pointer") && flushes==1);
    reset(); live.touch_live=(uintptr_t)&touch; touch.magic=0;
    note_touch_progress(&live,0,32); assert(strstr(output,"ABI mismatch") && flushes==2);
    reset(); live.touch_live=(uintptr_t)&touch+1;
    note_touch_progress(&live,0,32); assert(strstr(output,"invalid state pointer") && flushes==1);
    reset(); live.touch_live=live_kernel_hi+64;
    note_touch_progress(&live,0,32); assert(strstr(output,"invalid state pointer") && flushes==1);
    reset(); live.touch_live=(uintptr_t)&touch;
    uint64_t saved_hi=live_kernel_hi; live_kernel_hi=(uintptr_t)&live+96;
    note_touch_progress(&live,0,32); assert(strstr(output,"not available") && !flushes); live_kernel_hi=saved_hi;
    reset(); live.log_len=0xbad;
    note_kernel_progress(&live,0,32); assert(strstr(output,"invalid live pointer/range") && !flushes); live.log_len=(uintptr_t)&len;
    reset(); touch.magic=FAJITA_TOUCH_LIVE_MAGIC; touch.state=((uint64_t)FT_PHASE_STOPPED<<32)|4;
    note_touch_progress(&live,0,60); assert(strstr(output,"FT=15 data=4 stopped"));
    reset(); note_kernel_progress(NULL,0,2); assert(!strcmp(output,"2 s\n"));
    puts("live reader: chronological tail, paired ABI, phase read and range/alignment/old-layout gates passed");
}
'''
source = OUT / 'reader.c'
source.write_text(prelude + schema + kernel_schema + mocks + readers + tests)
binary = OUT / 'reader'
subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-pedantic',
                '-fsanitize=address,undefined','-fno-omit-frame-pointer','-g',str(source),'-o',str(binary)],check=True)
env = dict(os.environ)
env['ASAN_OPTIONS'] = env.get('ASAN_OPTIONS','') + ':detect_leaks=0'
subprocess.run([str(binary)],check=True,env=env)
