#!/usr/bin/env python3
"""Check fajita boot ordering, payload reservation and UFS handoff before building."""
import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP = "Platform/Oneplus/sdm845/Library/PlatformMemoryMapLib/PlatformMemoryMapLib"
DRIVER = "XnuLoaderPkg/Driver/XnuLoaderDxe/XnuLoaderDxe.c"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--edk2", required=True, type=Path)
    ap.add_argument("--loader", required=True, type=Path)
    args = ap.parse_args()
    overlay = ROOT / "devices/fajita/uefi/files"
    for relative in (MAP + ".c", MAP + ".inf", DRIVER,
                     "XnuLoaderPkg/Driver/XnuLoaderDxe/XnuLoaderDxe.inf"):
        if (args.edk2 / relative).read_bytes() != (overlay / relative).read_bytes():
            raise SystemExit(f"staged UEFI source differs from project overlay: {relative}")
    dxe = (args.edk2 / DRIVER).read_text()
    if "gBS->StartImage" in dxe or "gBS->LoadImage" in dxe:
        raise SystemExit("loader must be launched by BDS, not DXE dispatch")
    dsc = (args.edk2 / "Platform/Oneplus/sdm845/fajita.dsc").read_text()
    if "PlatformMemoryMapLib|" + MAP + ".inf" not in dsc:
        raise SystemExit("fajita DSC does not select its reserved-payload memory map")
    memory = (args.edk2 / (MAP + ".c")).read_text()
    rows = re.findall(r'\{"([^"]+)",\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),'
                      r'\s*(\w+),\s*\w+,\s*\w+,\s*(\w+),\s*\w+\}', memory)
    for tag, end in (("Mem4G", 0x17DFA0000), ("Mem6G", 0x1FAE00000),
                     ("Mem8G", 0x180000000), ("Mem10G", 0x2F4AC0000)):
        profile = sorted((int(a, 16), int(n, 16), typ) for _, a, n, t, typ in rows
                         if t == tag and int(a, 16) < end)
        cursor = 0xD0000000
        reserved = []
        for start, length, typ in profile:
            if start != cursor or length <= 0 or typ not in {"Conv", "Reserv"}:
                raise SystemExit(f"{tag}: gap, overlap or unexpected memory type at {start:#x}")
            cursor += length
            if typ == "Reserv":
                reserved.append((start, length))
        if cursor != end or reserved != [(0xF0000000, 0x04000000)]:
            raise SystemExit(f"{tag}: RAM endpoint or 64 MiB payload reservation changed")
        print(f"{tag}: reserved 0xf0000000..0xf4000000; RAM endpoint preserved")
    loader = args.loader.read_text()
    if "ufs_requested >= 2 && kernel_mpidr == this_mpidr && checkpoint != CHECKPOINT_SELFTEST" not in loader:
        raise SystemExit("loader lacks same-core UFS handoff guard")
    if "if (!pi.oneshot && usb_start())" not in loader or "if (USB == NULL)" not in loader:
        raise SystemExit("loader one-shot path no longer bypasses USB protocol use")
    print("source checks passed: BDS entry, payload memory map, UFS core guard, USB bypass")


if __name__ == "__main__":
    main()
