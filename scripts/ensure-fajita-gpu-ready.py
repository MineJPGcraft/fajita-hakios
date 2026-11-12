#!/usr/bin/env python3
"""Let redwood_gpu_ready() see a kept Adreno 630.

The 6T's GPU runs from bsd/dev/fajita_adreno.c, which keeps its own static
gpu_kept flag. redwood_gpu_ready() only asked mi8_gpu_kept() and redwood's own
flag, so a kept a630 looked absent: RedwoodGPUUserClient::start refused, the
window server fell back to software, and what the user saw was the logo
followed by a black screen -- with the GPU selftest reporting 200 of 200 right
immediately before the handover.
"""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/fajita-gpu-ready-kept.patch"
MARKER = "if (fajita_gpu_kept())"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xnu", type=Path, required=True)
    args = ap.parse_args()

    target = args.xnu / "bsd/dev/redwood_adreno.c"
    if not target.exists():
        raise SystemExit(f"redwood_adreno.c not found under {args.xnu}")
    text = target.read_text()

    if MARKER in text:
        print("redwood_gpu_ready already honours a kept a630")
        return

    if not subprocess.run(["git", "-C", str(args.xnu), "apply", "--check", str(PATCH)],
                          capture_output=True).returncode == 0:
        raise SystemExit("patches/fajita-gpu-ready-kept.patch no longer applies")
    subprocess.run(["git", "-C", str(args.xnu), "apply", str(PATCH)], check=True)
    if MARKER not in target.read_text():
        raise SystemExit("after applying the patch the marker is still absent")
    print("redwood_gpu_ready now honours a kept a630")


if __name__ == "__main__":
    main()
