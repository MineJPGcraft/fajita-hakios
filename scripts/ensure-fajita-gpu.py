#!/usr/bin/env python3
"""Stage the opt-in fajita Adreno 630 driver into the prepared XNU tree.

The driver is the SDM845 Adreno 630 bring-up from the dipper tree, retargeted
for fajita: same SoC, same GPU (chip id 0x06030001), same register bases. Every
address in it was checked against fajita's own EDL device tree:

    GPUCC   0x05090000   /soc/qcom,gpucc@5090000,  /soc/qcom,gfxcc@5090000
    GMU     0x0506a000   /soc/qcom,gmu  kgsl_gmu_reg, 0x30000 long
    SMMU    0x05040000   /soc/arm,smmu-kgsl@5040000
    3D core 0x05000000   /soc/qcom,kgsl-3d0@5000000, kgsl_3d0_reg_memory

Nothing here runs without the driver's own boot-arg, which keeps this file
harmless on a tree that never passes it.
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("fajita_adreno.c", "fajita_a630_hw_init.inc")


def ensure_after(path: Path, anchor: str, line: str) -> None:
    """Add one build/declaration line after a unique prepared-tree anchor."""
    text = path.read_text()
    if line in text.splitlines():
        if text.splitlines().count(line) != 1:
            raise SystemExit(f"duplicate GPU integration in {path}")
        return
    matches = [old for old in text.splitlines() if old.startswith(anchor)]
    if len(matches) != 1:
        raise SystemExit(f"expected one prepared anchor {anchor!r} in {path}")
    path.write_text(text.replace(matches[0] + "\n", matches[0] + "\n" + line + "\n", 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xnu", type=Path, required=True)
    ap.add_argument("--extra-files", type=Path, required=True)
    args = ap.parse_args()

    for name in FILES:
        staged = args.xnu / "bsd/dev" / name
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes((ROOT / "devices/fajita/gpu" / name).read_bytes())

    # The driver is built like the other phone's, but only its own boot-arg
    # makes it touch the GPU; it must be in the same list either way so that a
    # build carrying the flag has the code.
    ensure_after(args.xnu / "bsd/conf/files", "bsd/dev/mi8_adreno.c",
                 "bsd/dev/fajita_adreno.c\tstandard")

    # Without this call the driver is linked but never reached. It goes beside
    # the other bring-up initialisers, and returns at once unless fajitagpu=N.
    ensure_after(args.xnu / "bsd/dev/redwood_input.c", "\tmi8_adreno_init();",
                 "\tfajita_adreno_init();")
    ensure_after(args.xnu / "bsd/sys/redwood_input.h", "void mi8_adreno_init(void);",
                 "void fajita_adreno_init(void);          /* boot-arg fajitagpu=N: "
                 "the first N steps of waking the 6T's GPU (Adreno 630) */")

    print("fajita Adreno 630 driver staged; build list present")


if __name__ == "__main__":
    main()
