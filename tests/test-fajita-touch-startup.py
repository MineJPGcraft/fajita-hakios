#!/usr/bin/env python3
"""Exercise the actual deferred startup functions with host-only kernel mocks."""
from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-touch-startup"
OUT.mkdir(parents=True, exist_ok=True)
driver = (ROOT / "devices/fajita/kernel/fajita_synaptics.c").read_text()
startup = driver[driver.index("static void ft_start_deferred("):]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#define NSEC_PER_MSEC 1000000u
#define KERN_SUCCESS 0
typedef void *thread_call_param_t;
typedef uintptr_t thread_t;
typedef void (*thread_call_func_t)(thread_call_param_t, thread_call_param_t);
struct fake_call { thread_call_func_t callback; void *arg; uint64_t deadline; };
typedef struct fake_call *thread_call_t;
static struct fake_call call;
static thread_call_t ft_start_call;
static uint32_t ft_diag_enabled, ft_start_delay_ms = 30000, ft_poll_us = 8000;
static uint32_t ft_probe_only;
static int ft_error;
static unsigned allocations, arms, starts, releases, lines, selections;
static uint32_t selected_layout;
static bool fail_allocate, fail_start;
static const char *last_line;
static uint64_t last_state;
static void ft_phase(uint32_t phase,uint32_t detail) { last_state=((uint64_t)phase<<32)|detail; }
struct boot_arg { const char *name; uint32_t value; };
static struct boot_arg args[16];
static size_t arg_count;
static void ft_thread(void *arg, int result) { (void)arg; (void)result; assert(false); }
static bool PE_parse_boot_argn(const char *key, void *value, int size) {
    assert(size == 4);
    for (size_t i=0; i<arg_count; i++) if (!strcmp(args[i].name,key)) {
        memcpy(value,&args[i].value,4); return true;
    }
    return false;
}
static bool fajita_bus_select_layout(uint32_t layout) {
    selections++; selected_layout=layout; return layout==1 || layout==2;
}
static void loader_log_line(const char *line) { lines++; last_line=line; }
static thread_call_t thread_call_allocate(thread_call_func_t fn, void *arg) {
    allocations++; call.callback=fn; call.arg=arg; return fail_allocate ? NULL : &call;
}
static void clock_interval_to_deadline(uint32_t interval, uint32_t scale, uint64_t *deadline) {
    assert(scale==NSEC_PER_MSEC); *deadline=UINT64_C(123)+((uint64_t)interval*scale);
}
static bool thread_call_enter_delayed(thread_call_t c, uint64_t deadline) {
    assert(c==&call); arms++; c->deadline=deadline; return false;
}
static int kernel_thread_start(void (*fn)(void *,int), void *arg, thread_t *thread) {
    assert(fn==ft_thread && arg==NULL); starts++; *thread=42; return fail_start ? 5 : KERN_SUCCESS;
}
static void thread_deallocate(thread_t thread) { assert(thread==42); releases++; }
'''
tests = r'''
static void reset(void) {
    memset(&call,0,sizeof(call)); ft_start_call=NULL;
    ft_diag_enabled=0; ft_probe_only=0; ft_start_delay_ms=30000; ft_poll_us=8000; ft_error=0;
    allocations=arms=starts=releases=lines=selections=0; selected_layout=0; fail_allocate=fail_start=false;
    last_line=NULL; arg_count=0; last_state=0;
}
static void arg(const char *name,uint32_t value) { assert(arg_count<16); args[arg_count++]=(struct boot_arg){name,value}; }
static void enable(void) { arg("fajita_touch",1); arg("fajita_touch_inherit",1); arg("fajita_touch_tlmm",2); }
int main(void) {
    reset(); fajita_touch_init(); assert(!allocations && !starts && !lines);
    reset(); arg("fajita_touch",0); fajita_touch_init(); assert(!allocations && !starts);
    reset(); arg("fajita_touch",1); fajita_touch_init(); assert(ft_error==6 && !allocations && !starts && !selections);
    reset(); enable(); arg("fajita_touch_power",1); fajita_touch_init(); assert(ft_error==6 && !allocations && !starts && !selections);
    reset(); enable(); arg("fajita_touch_power",1); arg("fajita_touch_prepared",0); fajita_touch_init(); assert(ft_error==6 && !allocations && !starts && !selections);
    reset(); arg("fajita_touch",1); arg("fajita_touch_power",1); arg("fajita_touch_prepared",1); fajita_touch_init();
    assert(ft_error==7 && selections==1 && selected_layout==0 && !allocations && !arms && !starts);
    reset(); arg("fajita_touch",1); arg("fajita_touch_inherit",1); fajita_touch_init();
    assert(ft_error==7 && selections==1 && !allocations && !starts);
    for (uint32_t layout=1;layout<=2;layout++) {
        reset(); arg("fajita_touch",1); arg("fajita_touch_power",1); arg("fajita_touch_prepared",1);
        arg("fajita_touch_tlmm",layout); fajita_touch_init();
        assert(allocations==1 && arms==1 && !starts && selections==1 && selected_layout==layout);
    }
    reset(); arg("fajita_touch",1); arg("fajita_touch_power",1); arg("fajita_touch_prepared",1);
    arg("fajita_touch_tlmm",3); fajita_touch_init();
    assert(ft_error==7 && selections==1 && !allocations && !arms && !starts);
    reset(); enable(); fajita_touch_init();
    assert(allocations==1 && arms==1 && starts==0 && releases==0);
    assert((last_state>>32)==FT_PHASE_DEFERRED_ARMED && (uint32_t)last_state==30000);
    assert(call.deadline==UINT64_C(30000000123));
    assert(strstr(last_line,"no touch hardware accessed"));
    fajita_touch_init(); assert(allocations==1 && arms==1 && !starts);
    call.callback(call.arg,NULL); assert(starts==1 && releases==1 && !ft_error);
    reset(); enable(); arg("fajita_touch_delay_ms",60000); arg("fajita_touch_poll_us",2000); arg("fajita_touch_diag",1);
    fajita_touch_init(); assert(call.deadline==UINT64_C(60000000123) && ft_poll_us==2000 && ft_diag_enabled==1 && !starts);
    reset(); enable(); arg("fajita_touch_delay_ms",999); arg("fajita_touch_poll_us",20001); arg("fajita_touch_diag",2);
    fajita_touch_init(); assert(ft_start_delay_ms==30000 && ft_poll_us==8000 && !ft_diag_enabled && !starts);
    reset(); enable(); arg("fajita_touch_delay_ms",1000); arg("fajita_touch_poll_us",20000);
    fajita_touch_init(); assert(call.deadline==UINT64_C(1000000123) && ft_poll_us==20000 && !starts);
    reset(); enable(); arg("fajita_touch_probe",1); fajita_touch_init();
    assert(ft_probe_only==1 && allocations==1 && arms==1 && !starts);
    call.callback(call.arg,NULL); assert(starts==1 && releases==1);
    reset(); enable(); arg("fajita_touch_probe",2); fajita_touch_init();
    assert(ft_probe_only==2 && allocations==1 && arms==1 && !starts);
    reset(); enable(); arg("fajita_touch_probe",3); fajita_touch_init();
    assert(ft_probe_only==0 && allocations==1 && arms==1 && !starts);
    reset(); enable(); fail_allocate=true; fajita_touch_init();
    assert(ft_error==5 && allocations==1 && !arms && !starts && !ft_start_call);
    reset(); enable(); fail_start=true; fajita_touch_init(); call.callback(call.arg,NULL);
    assert(ft_error==5 && starts==1 && !releases && strstr(last_line,"creation failed"));
    puts("touch startup: disabled/inherit/layout gates, deferred execution, once-only scheduling, bounds and failures passed");
}
'''
source = OUT / "startup.c"
source.write_text('#include <stdint.h>\n#include "' + str(ROOT / 'devices/fajita/kernel/fajita_touch_live.h') + '"\n' + harness + "\n" + startup + "\n" + tests)
binary = OUT / "startup"
subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g",
                str(source), "-o", str(binary)], check=True)
# This harness has no heap allocations. LSan's process scan is unavailable
# under the host's tracing sandbox; keep ASan's memory checks and UBSan active.
env = dict(os.environ)
env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
subprocess.run([str(binary)], check=True, env=env)
