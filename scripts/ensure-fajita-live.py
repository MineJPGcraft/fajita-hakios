#!/usr/bin/env python3
"""Stage paired, append-only touch diagnostics without accessing a device."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def copy_if_changed(source: Path, target: Path) -> None:
    data = source.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_bytes() != data:
        target.write_bytes(data)


def apply_once(tree: Path) -> None:
    base = ["patch", "--batch", "--fuzz=0", "--no-backup-if-mismatch", "-p1",
            "-d", str(tree.resolve()), "-i", str(ROOT / "patches/fajita-kernel-touch-live.patch")]
    result = subprocess.run(base + ["--forward", "--dry-run"], capture_output=True, text=True)
    if result.returncode == 0:
        subprocess.run(base + ["--forward"], check=True)
    elif subprocess.run(base + ["--reverse", "--dry-run"], capture_output=True).returncode != 0:
        raise SystemExit(f"unrecognized/partial touch live ABI in {tree}:\n{result.stdout}{result.stderr}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xnu", type=Path, required=True)
    ap.add_argument("--extra-files", type=Path, required=True)
    ap.add_argument("--loader", type=Path, required=True)
    args = ap.parse_args()
    for tree in (args.xnu, args.extra_files):
        copy_if_changed(ROOT / "devices/fajita/kernel/fajita_touch_live.h", tree / "bsd/sys/fajita_touch_live.h")
        copy_if_changed(ROOT / "devices/fajita/kernel/fajita_touch_live.h", tree / "osfmk/arm64/fajita_touch_live.h")
        apply_once(tree)
    for source in (ROOT / "devices/fajita/kernel/fajita_touch_live.h",
                   ROOT / "devices/fajita/loader/fajita_log_tail.h"):
        copy_if_changed(source, args.loader.parent / source.name)
    print("paired touch live ABI and ring-tail headers staged; old 96-byte live layout preserved")


if __name__ == "__main__":
    main()
