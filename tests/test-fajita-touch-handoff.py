#!/usr/bin/env python3
"""Replay staging and execute the generated final-acknowledgement code."""
import hashlib
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-touch-handoff"
OUT.mkdir(parents=True, exist_ok=True)
loader = ROOT / "ref/dipper-xnu/work/hackios/xnuloader/loader.c"
target = OUT / "loader.c"
target.write_bytes(loader.read_bytes())
command = ["python3", str(ROOT / "scripts/ensure-fajita-touch-rpmh.py"), "--loader", str(target)]
subprocess.run(command, check=True, capture_output=True)
text = target.read_text()


def staged_hashes():
    result = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob("fajita_*")}
    result["loader.c"] = hashlib.sha256(target.read_bytes()).hexdigest()
    return result


def stage_and_check(label, initial):
    target.write_text(initial)
    subprocess.run(command, check=True, capture_output=True)
    assert target.read_text() == text, label
    before = staged_hashes()
    subprocess.run(command, check=True, capture_output=True)
    assert staged_hashes() == before, label + " idempotence"


ack_begin = text.index('const char *ack = " fajita_touch_prepared=0 fajita_touch_tlmm=0";')
ack_end = text.index('if (n + str_len(ack) + 1 > BOOT_LINE_LENGTH) {', ack_begin)
ack_end += len('if (n + str_len(ack) + 1 > BOOT_LINE_LENGTH) {')
old_ack = ('const char *ack = fajita_touch_prepared ? " fajita_touch_prepared=1" : " fajita_touch_prepared=0";\n'
           '\t\tif (n + sizeof(" fajita_touch_prepared=0") > BOOT_LINE_LENGTH) {')
historical = text[:ack_begin] + old_ack + text[ack_end:]
stage_and_check("historical ready-only upgrade", historical)
include = '\n#include "fajita_touch_rpmh.inc"\n'
prepare = '\tint fajita_touch_prepared = fajita_touch_prepare_before_handoff(pi.cmdline, this_mpidr, kernel_mpidr);\n'
block_start = text.index('\t/* Only this final firmware-produced acknowledgement authorizes touch MMIO. */\n')
block_end = text.index('\tba->commandLine[n] = 0;\n', block_start) + len('\tba->commandLine[n] = 0;\n')
fresh = (text[:block_start] + text[block_end:]).replace(include, '', 1).replace(prepare, '', 1)
assert include not in fresh and prepare not in fresh
stage_and_check("fresh integration", fresh)
stage_and_check("current integration", text)
anchor = '\tfor (const char *c = pi.cmdline; *c && n < BOOT_LINE_LENGTH - 1; c++) {'
start = text.index(anchor)
end = text.index('\tba->commandLine[n] = 0;', start) + len('\tba->commandLine[n] = 0;')
code = text[start:end]
parser_path = ROOT / "ref/dipper-xnu/work/hackios/xnu-src/xnu-8796.121.2/pexpert/gen/bootargs.c"
parser = parser_path.read_text()
parser = re.sub(r'^#include[^\n]*\n', '', parser, flags=re.M)
parser = parser[:parser.index('boolean_t\nPE_imgsrc_mount_supported()')]
parser = ('typedef int boolean_t;\n#define TRUE 1\n#define FALSE 0\n'
          'static char *parser_line;\nstatic char *PE_boot_args(void) { return parser_line; }\n' + parser)
harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define BOOT_LINE_LENGTH 128
static const int platform_fajita, other;
static const int *PLAT=&platform_fajita;
static unsigned warnings, fajita_power_tlmm_layout;
static size_t str_len(const char *text) { return strlen(text); }
static void print(const char *text) { assert(strstr(text,"no room")); warnings++; }
struct boot_args { char commandLine[BOOT_LINE_LENGTH]; unsigned guard; };
static int assemble(struct boot_args *ba,const char *line,unsigned prefix,int fajita_touch_prepared,unsigned layout) {
    fajita_power_tlmm_layout=layout;
    struct { const char *cmdline; } pi={line};
    unsigned n=prefix;
    assert(prefix<BOOT_LINE_LENGTH);
    memset(ba->commandLine,' ',prefix);ba->guard=0x73594a21;
'''
tests = r'''
    return 1;
}
static int last_value(const char *line,const char *name) {
    int32_t value=-1;
    parser_line=(char *)line;
    (void)PE_parse_boot_argn(name,&value,sizeof(value));
    return value;
}
static void check_ack(const struct boot_args *ba,unsigned ready,unsigned layout) {
    assert(last_value(ba->commandLine,"fajita_touch_prepared")==((int)ready));
    assert(last_value(ba->commandLine,"fajita_touch_tlmm")==((int)layout));
    assert(ba->guard==0x73594a21);
}
int main(void) {
    struct boot_args ba;
    assert(israngesep('_')); /* Retained parser helper is otherwise unused. */
    const char *old[]={"", "fajita_touch_prepared=1 fajita_touch_tlmm=2",
        "fajita_touch_prepared=0 fajita_touch_tlmm=1",
        "fajita_touch_tlmm=7 fajita_touch_prepared=9"};
    for(unsigned prepared=0;prepared<2;prepared++) {
        for(unsigned layout=0;layout<4;layout++) {
            unsigned ready=prepared && (layout==1 || layout==2);
            unsigned chosen=ready ? layout : 0;
            for(unsigned i=0;i<sizeof(old)/sizeof(old[0]);i++) {
                assert(assemble(&ba,old[i],0,(int)prepared,layout));
                check_ack(&ba,ready,chosen);
            }
            /* All 128 prefix positions, for each selector outcome. */
            char expected[80];
            int count=snprintf(expected,sizeof(expected),
                " fajita_touch_prepared=%u fajita_touch_tlmm=%u",ready,chosen);
            assert(count>0 && (unsigned)count<sizeof(expected));
            unsigned ack=(unsigned)strlen(expected);
            const unsigned lengths[]={0,1,22,41,127,128,255};
            for(unsigned j=0;j<sizeof(lengths)/sizeof(lengths[0]);j++) {
                char payload[256];memset(payload,'x',lengths[j]);payload[lengths[j]]=0;
                for(unsigned n=0;n<BOOT_LINE_LENGTH;n++) {
                    memset(&ba,0xa5,sizeof(ba));
                    int ok=assemble(&ba,payload,n,(int)prepared,layout);
                    unsigned copied=lengths[j];
                    if(copied>BOOT_LINE_LENGTH-1-n) copied=BOOT_LINE_LENGTH-1-n;
                    assert(ok==(n+copied+ack+1<=BOOT_LINE_LENGTH));
                    if(ok) {
                        assert(strlen(ba.commandLine)==n+copied+ack);
                        assert(!strcmp(ba.commandLine+n+copied,expected));
                        check_ack(&ba,ready,chosen);
                    }
                    assert(ba.guard==0x73594a21);
                }
            }
        }
    }
    PLAT=&other;
    for(unsigned n=0;n<BOOT_LINE_LENGTH;n++) {
        assert(assemble(&ba,"other=1",n,1,2));
        assert(last_value(ba.commandLine,"fajita_touch_prepared")==-1);
        assert(last_value(ba.commandLine,"fajita_touch_tlmm")==-1);
        assert(ba.guard==0x73594a21);
    }
    /* Other platforms retain existing keys; this path appends neither key. */
    assert(assemble(&ba,"fajita_touch_prepared=9 fajita_touch_tlmm=7",0,1,2));
    check_ack(&ba,9,7);
    assert(warnings>0);
    puts("handoff: fresh/historical/idempotent staging, real XNU numeric parser, final ready+layout overrides, all prefix/line bounds and non-fajita gate passed");
}
'''
source = OUT / "handoff.c"
tests = tests.replace('static int last_value(', parser + '\nstatic int last_value(', 1)
source.write_text(harness + code + tests)
binary = OUT / "handoff"
subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-O1", "-fsanitize=address,undefined", str(source), "-o", str(binary)], check=True)
subprocess.run([str(binary)], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
