#!/usr/bin/env python3
"""Make RedwoodGPUStart() say whether the service registered.

Every failure branch of RedwoodGPUStart() already logs through IOLog, but a
service that never registered and a window server that chose not to draw look
identical from the outside: both end in a black screen with no GPU submission.
Without a line for the success case there is no way to tell which layer to
look at, and a whole bring-up round can go to the wrong one.
"""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/fajita-gpu-startup-log.patch"
MARKER = "RedwoodGPURegistryReport"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xnu", type=Path, required=True)
    args = ap.parse_args()

    target = args.xnu / "iokit/Kernel/RedwoodGPU.cpp"
    if not target.exists():
        raise SystemExit(f"RedwoodGPU.cpp not found under {args.xnu}")
    if MARKER in target.read_text():
        print("RedwoodGPUStart already reports its outcome")
        return

    if subprocess.run(["git", "-C", str(args.xnu), "apply", "--check", str(PATCH)],
                      capture_output=True).returncode != 0:
        raise SystemExit("patches/fajita-gpu-startup-log.patch no longer applies")
    subprocess.run(["git", "-C", str(args.xnu), "apply", str(PATCH)], check=True)
    if MARKER not in target.read_text():
        raise SystemExit("after applying the patch the marker is still absent")
    print("RedwoodGPUStart now reports whether the service registered")


if __name__ == "__main__":
    main()
