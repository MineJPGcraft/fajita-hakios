#!/usr/bin/env python3
"""Build hakios-metal for the Adreno 630 instead of the A635 family.

The Metal driver's chip facts (has_lpac, instr_cache_size, fibers_per_sp) come
from Mesa's freedreno_devices.py and size the streams its emitters write, so a
build is for one part. This stages the per-chip selection -- a HAKIOS_METAL_CHIP_A630
switch in a6xx.hpp, the CHIP=a630 knob in the Makefile, and tests that hold for
either part rather than only for the gen4 numbers they were written against.

Nothing here touches the driver's behaviour on the A635: without the switch the
constants are exactly what they were.
"""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/fajita-metal-a630.patch"
MARKER = "HAKIOS_METAL_CHIP_A630"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hakios", type=Path, required=True,
                    help="the hakios-xnu-kernel checkout holding hakios-metal/")
    args = ap.parse_args()

    metal = args.hakios / "hakios-metal"
    hpp = metal / "include/hakios-metal/a6xx.hpp"
    if not hpp.exists():
        raise SystemExit(f"hakios-metal not found under {args.hakios}")

    if MARKER in hpp.read_text():
        print("hakios-metal already knows the Adreno 630")
        return

    if subprocess.run(["git", "-C", str(args.hakios), "apply", "--check", str(PATCH)],
                      capture_output=True).returncode != 0:
        raise SystemExit("patches/fajita-metal-a630.patch no longer applies")
    subprocess.run(["git", "-C", str(args.hakios), "apply", str(PATCH)], check=True)
    if MARKER not in hpp.read_text():
        raise SystemExit("after applying the patch the marker is still absent")
    print("hakios-metal knows the Adreno 630; build it with CHIP=a630")


if __name__ == "__main__":
    main()
