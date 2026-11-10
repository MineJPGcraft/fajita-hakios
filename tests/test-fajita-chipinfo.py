#!/usr/bin/env python3
"""Check the real metadata query against staged ABI and built GUID evidence."""
import os
from pathlib import Path
import re
import struct
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-chipinfo"
OUT.mkdir(parents=True, exist_ok=True)
EDK2 = ROOT / "ref/dipper-xnu/work/edk2"
QCOM = EDK2 / "Silicon/Qualcomm/QcomPkg"
# EDK2 resolves gEfiChipInfoProtocolGuid from QcomPkg.dec. The standalone
# EFI_CHIPINFO_PROTOCOL_GUID header macro is a different, stale value and
# must not serve as both the implementation's input and the mock's oracle.
declaration = re.search(
    r"^\s*gEfiChipInfoProtocolGuid\s*=\s*(\{[^\n]+\})\s*$",
    (QCOM / "QcomPkg.dec").read_text(), re.MULTILINE)
assert declaration, "staged QcomPkg.dec has no ChipInfo protocol declaration"
guid_words = [int(value, 16) for value in re.findall(r"0x[0-9a-fA-F]+", declaration[1])]
assert len(guid_words) == 11, "malformed staged ChipInfo GUID declaration"
actual_guid_bytes = struct.pack("<IHH8B", *guid_words)
actual_guid = uuid.UUID(bytes_le=actual_guid_bytes)
assert str(actual_guid).upper() == "B0760469-970C-487A-A4B5-28DB7B45CEF1"
xref = EDK2 / "workspace/Build/fajita/RELEASE_CLANG38/FV/Guid.xref"
xref_guids = [line.split()[0] for line in xref.read_text().splitlines()
              if len(line.split()) == 2 and line.split()[1] == "gEfiChipInfoProtocolGuid"]
assert len(xref_guids) == 1 and uuid.UUID(xref_guids[0]) == actual_guid, \
    "built FV GUID reference differs from staged QcomPkg.dec"
driver = EDK2 / "Platform/EFI_Binaries/Drivers/sdm845/ChipInfo/ChipInfo.efi"
driver_bytes = driver.read_bytes()
assert actual_guid_bytes in driver_bytes, "actual SDM845 ChipInfo driver lacks .dec protocol GUID"
old_guid_bytes = uuid.UUID("61224FBE-B240-4D53-B661-A9A1F343B05C").bytes_le
assert old_guid_bytes not in driver_bytes, "stale header GUID unexpectedly exists in actual driver"
guid_initializer = "{ " + ", ".join(hex(value) for value in guid_words[:3]) + ", { " + \
    ", ".join(hex(value) for value in guid_words[3:]) + " } }"
print(f"ChipInfo GUID evidence: staged .dec = built FV Guid.xref = SDM845 ChipInfo.efi: {actual_guid}")
source = (ROOT / "devices/fajita/loader/fajita_touch_rpmh.inc").read_text()
start = source.index("static uint32_t fp_chip_layout(void)")
end = source.index("static bool fp_pin_output(", start)
query = source[start:end]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "efi.h"
typedef uint32_t UINT32;
typedef uint64_t UINT64;
typedef char CHAR8;
typedef efi_status EFI_STATUS;
typedef efi_guid EFI_GUID;
#define IN
#define OUT
#include "EFIChipInfo.h"
#include "fajita_touch_board.h"
#include "fajita_chipinfo.h"
_Static_assert(offsetof(struct fajita_chipinfo_protocol, revision) ==
    offsetof(EFI_CHIPINFO_PROTOCOL, Revision), "real revision offset");
_Static_assert(offsetof(struct fajita_chipinfo_protocol, get_chip_version) ==
    offsetof(EFI_CHIPINFO_PROTOCOL, GetChipVersion), "real version offset");
_Static_assert(offsetof(struct fajita_chipinfo_protocol, get_chip_id) ==
    offsetof(EFI_CHIPINFO_PROTOCOL, GetChipId), "real ID offset");
_Static_assert(sizeof(EFIChipInfoVersionType) == sizeof(uint32_t), "version width");
_Static_assert(sizeof(EFIChipInfoIdType) == sizeof(uint32_t), "ID width");
_Static_assert(EFICHIPINFO_ID_SDM845 == FAJITA_TOUCH_CHIP_ID, "real chip ID");
static efi_boot_services services;
static efi_boot_services *BS = &services;
static struct fajita_chipinfo_protocol chip;
static uint32_t id, version;
static uint32_t fajita_power_chip_detail;
static unsigned locates, ids, versions, prints;
static bool locate_error, no_protocol, id_error, version_error;
static void print(const char *fmt, ...) { assert(strstr(fmt,"TLMM layout")); prints++; }
static efi_status get_id(struct fajita_chipinfo_protocol *self, uint32_t *out) {
    assert(self == &chip); ids++;
    if (id_error) return EFI_ERROR_BIT | 24;
    *out = id; return 0;
}
static efi_status get_version(struct fajita_chipinfo_protocol *self, uint32_t *out) {
    assert(self == &chip); versions++;
    if (version_error) return EFI_ERROR_BIT | 24;
    *out = version; return 0;
}
static efi_status locate(const efi_guid *guid, void *registration, void **out) {
    const efi_guid expected = ACTUAL_GUID_FROM_STAGED_DEC;
    assert(!memcmp(guid,&expected,sizeof(expected)) && registration == NULL);
    locates++;
    if (locate_error) return EFI_ERROR_BIT | 14;
    *out = no_protocol ? NULL : &chip; return 0;
}
static void reset(void) {
    memset(&services,0,sizeof(services)); memset(&chip,0,sizeof(chip));
    services.locate_protocol=locate; chip.revision=EFI_CHIPINFO_PROTOCOL_REVISION;
    chip.get_chip_id=get_id; chip.get_chip_version=get_version;
    id=321; version=EFI_CHIPINFO_VERSION(2,1);
    locates=ids=versions=prints=0;
    /* A fresh query must reset a prior failure, including on success. */
    fajita_power_chip_detail=UINT32_MAX;
    locate_error=no_protocol=id_error=version_error=false;
}
'''
harness = harness.replace("ACTUAL_GUID_FROM_STAGED_DEC", guid_initializer)
tests = r'''
int main(void) {
    const uint32_t versions_to_test[]={0,0x10000,0x10001,0x20000,0x20001,0x20002,0x30000,UINT32_MAX};
    const uint32_t ids_to_test[]={0,320,321,322,UINT32_MAX};
    for(unsigned i=0;i<sizeof(ids_to_test)/sizeof(ids_to_test[0]);i++)
    for(unsigned v=0;v<sizeof(versions_to_test)/sizeof(versions_to_test[0]);v++) {
        reset();id=ids_to_test[i];version=versions_to_test[v];
        unsigned expected=0;
        if(id==321 && version==0x10000) expected=1;
        if(id==321 && (version==0x20000 || version==0x20001)) expected=2;
        assert(fp_chip_layout()==expected);
        assert(fajita_power_chip_detail==(expected?0u:8u));
        assert(locates==1 && ids==1 && versions==1 && prints==1);
    }
    reset(); services.locate_protocol=NULL;
    assert(!fp_chip_layout() && !locates && !ids && !versions);
    assert(fajita_power_chip_detail==1);
    reset(); locate_error=true; assert(!fp_chip_layout() && locates==1 && !ids && !versions);
    assert(fajita_power_chip_detail==2);
    reset(); no_protocol=true; assert(!fp_chip_layout() && locates==1 && !ids && !versions);
    assert(fajita_power_chip_detail==3);
    const uint64_t invalid_revision[]={0,0xffff,0x20000,UINT64_MAX};
    for(unsigned i=0;i<sizeof(invalid_revision)/sizeof(invalid_revision[0]);i++) {
        reset();chip.revision=invalid_revision[i];
        assert(!fp_chip_layout() && !ids && !versions);
        assert(fajita_power_chip_detail==4);
    }
    reset();chip.get_chip_id=NULL; assert(!fp_chip_layout() && !ids && !versions);
    assert(fajita_power_chip_detail==5);
    reset();chip.get_chip_version=NULL; assert(!fp_chip_layout() && !ids && !versions);
    assert(fajita_power_chip_detail==5);
    reset();id_error=true; assert(!fp_chip_layout() && ids==1 && !versions && !prints);
    assert(fajita_power_chip_detail==6);
    reset();version_error=true; assert(!fp_chip_layout() && ids==1 && versions==1 && !prints);
    assert(fajita_power_chip_detail==7);
    /* Exercise consecutive calls without reset(): success clears failure,
     * and a later failure replaces (rather than preserves) old success. */
    version_error=false;
    assert(fp_chip_layout()==2 && fajita_power_chip_detail==0);
    id=320;
    assert(!fp_chip_layout() && fajita_power_chip_detail==8);
    id=321;version=0x10000;
    assert(fp_chip_layout()==1 && fajita_power_chip_detail==0);
    locate_error=true;
    assert(!fp_chip_layout() && fajita_power_chip_detail==2);
    locate_error=false;version=0x20000;
    assert(fp_chip_layout()==2 && fajita_power_chip_detail==0);
    assert(fajita_touch_gpio_pa(1,88)==0x03d58000 && fajita_touch_gpio_pa(2,88)==0x03558000);
    assert(fajita_touch_gpio_pa(1,125)==0x0397d000 && fajita_touch_gpio_pa(2,125)==0x0357d000);
    assert(!fajita_touch_gpio_pa(0,88) && !fajita_touch_gpio_pa(3,88));
    assert(!fajita_touch_gpio_pa(1,0) && !fajita_touch_gpio_pa(2,150));
    puts("ChipInfo: staged Qualcomm ABI, independent .dec/FV/driver GUID, real ID, known revisions, failure reasons, repeated-query reset and no default tile passed");
}
'''
path = OUT / "chipinfo.c"
path.write_text(harness + "\n" + query + "\n" + tests)
includes = [ROOT / "devices/fajita/kernel", ROOT / "devices/fajita/loader",
            ROOT / "ref/dipper-xnu/work/hackios/xnuloader",
            QCOM / "Include/Protocol"]
binary = OUT / "chipinfo"
subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                *("-I" + str(p) for p in includes), str(path), "-o", str(binary)], check=True)
subprocess.run([str(binary)], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
