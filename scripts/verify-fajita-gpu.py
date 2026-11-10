#!/usr/bin/env python3
"""Check that the staged fajita Adreno driver still describes fajita's hardware.

The driver was ported from another SDM845 phone, so the one thing worth
guarding is that its fixed addresses still match fajita's own device tree.
This reads the extracted EDL base DTB, not a restatement of the numbers.
"""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# name in the driver, the DT node, the reg[0] fajita's tree gives it
EXPECTED = (
    ("GPU_PHYS", "qcom,kgsl-3d0@5000000", 0x05000000),
    ("GMU_PHYS", "qcom,gmu", 0x0506a000),
    ("SMMU_PHYS", "arm,smmu-kgsl@5040000", 0x05040000),
    ("GPUCC_PHYS", "qcom,gpucc@5090000", 0x05090000),
)


def dtb_text(path: Path) -> str:
    return subprocess.run(["dtc", "-I", "dtb", "-O", "dts", str(path)],
                          capture_output=True, text=True, check=True).stdout


def node_reg(dts: str, needle: str) -> int:
    """reg[0] of the first node whose label or name contains needle."""
    for line in dts.splitlines():
        if needle in line and line.rstrip().endswith("{"):
            start = dts.index(line)
            block = dts[start:start + 1200]
            found = re.search(r"reg = <0x([0-9a-fA-F]+)", block)
            if found:
                return int(found.group(1), 16)
    raise SystemExit(f"device tree has no node for {needle}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dtb", type=Path, required=True,
                    help="fajita EDL base DTB (boot_a)")
    ap.add_argument("--driver", type=Path,
                    default=ROOT / "devices/fajita/gpu/fajita_adreno.c")
    args = ap.parse_args()

    dts = dtb_text(args.dtb)
    source = args.driver.read_text()
    problems = []

    for macro, node, expected in EXPECTED:
        found = re.search(rf"^#define\s+{macro}\s+0x([0-9a-fA-F]+)", source, re.MULTILINE)
        if not found:
            problems.append(f"{macro}: not defined in the driver")
            continue
        drv = int(found.group(1), 16)
        tree = node_reg(dts, node)
        if drv != tree:
            problems.append(f"{macro}: driver 0x{drv:08x} != device tree 0x{tree:08x} ({node})")
        elif drv != expected:
            problems.append(f"{macro}: driver 0x{drv:08x} != recorded 0x{expected:08x}")

    # The chip id decides which firmware and which register generation apply.
    if "0x06030001" not in source and "06030001" not in source:
        problems.append("chip id 0x06030001 (Adreno 630) is not named in the driver")
    if "fajita_a630_hw_init.inc" not in source:
        problems.append("the A630 register table is not included")

    table = (ROOT / "devices/fajita/gpu/fajita_a630_hw_init.inc").read_text()
    entries = len(re.findall(r"^\s*\{ 0x[0-9a-f]+u,", table, re.MULTILINE))
    if entries < 300:
        problems.append(f"A630 register table lost entries: {entries}")

    if problems:
        raise SystemExit("GPU checks failed:\n  " + "\n  ".join(problems))
    print(f"fajita GPU checks passed: 4 register bases match the device tree, "
          f"chip 0x06030001, {entries}-entry A630 init table")


if __name__ == "__main__":
    main()
