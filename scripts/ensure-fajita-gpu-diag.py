#!/usr/bin/env python3
"""Stage fajita_gpu_diag=1 support into the resident loader.

The loader shows only the last 240 bytes of the kernel log, and only while
`fajita_touch_diag=1` asks it to unblank the screen. Touch writes an `FT=` line
into that same tail every sample, which pushes the GPU driver's step log out of
the window before it can be read. This adds a separate `fajita_gpu_diag=1`
switch that keeps the tail visible but suppresses touch's own lines, so the GPU
bring-up can be observed without changing anything else about touch.

The edit is applied from `patches/fajita-loader-gpu-diag.patch`, on top of the
loader as the other ensure-* steps leave it, and is a no-op once applied.
"""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/fajita-loader-gpu-diag.patch"
MARKER = "fajita_gpu_diag_requested"


def git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True).returncode == 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loader", type=Path, required=True)
    args = ap.parse_args()

    text = args.loader.read_text()
    present = text.count(MARKER)

    if present:
        if present != 4:
            raise SystemExit(
                f"loader has {present} references to {MARKER}, expected 4; "
                "a partial application would leave the switch half wired")
        print("fajita GPU diagnostic switch already staged")
        return

    repo = args.loader.parent.parent
    if not (repo / ".git").is_dir():
        raise SystemExit(f"loader's tree is not a git checkout: {repo}")

    if not git_ok(repo, "apply", "--check", str(PATCH)):
        raise SystemExit(
            "patches/fajita-loader-gpu-diag.patch no longer applies; the loader "
            "around the touch diagnostic sample has changed")
    subprocess.run(["git", "-C", str(repo), "apply", str(PATCH)], check=True)

    staged = args.loader.read_text().count(MARKER)
    if staged != 4:
        raise SystemExit(f"after applying the patch: {staged} references, expected 4")
    print("fajita GPU diagnostic switch staged; the log tail keeps its 240 bytes")


if __name__ == "__main__":
    main()
