#!/usr/bin/env python3
"""Stage fajita_log_window=N into the resident loader.

The loader shows the tail of the kernel log, and used to show a fixed 240 bytes
of it per two-second sample. A quiet boot fits; a GPU bring-up does not -- its
steps print tens of kilobytes, so the sample showed unrelated noise and the
driver's own progress never reached the screen. That is why a step-10 run that
black-screened looked identical to one that produced no GPU logging at all.

This makes the window a boot argument (bytes), clamped to 8192 so one print
stays bounded; the 1 MB ring behind it is unchanged and the sampling helper caps
at ring on its own. Without the argument the old 240-byte behaviour is kept.
"""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/fajita-loader-log-window.patch"
MARKER = "fajita_log_window"


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
        if present != 7:
            raise SystemExit(
                f"loader has {present} references to {MARKER}, expected 7; "
                "a partial application would leave the window half wired")
        print("fajita log window already staged")
        return

    repo = args.loader.parent.parent
    if not (repo / ".git").is_dir():
        raise SystemExit(f"loader's tree is not a git checkout: {repo}")

    if not git_ok(repo, "apply", "--check", str(PATCH)):
        raise SystemExit(
            "patches/fajita-loader-log-window.patch no longer applies; the "
            "kernel-log sampling call or its surroundings have changed")
    subprocess.run(["git", "-C", str(repo), "apply", str(PATCH)], check=True)

    staged = args.loader.read_text().count(MARKER)
    if staged != 7:
        raise SystemExit(f"after applying the patch: {staged} references, expected 7")
    print("fajita log window staged; the sample size is now fajita_log_window=N (default 240)")


if __name__ == "__main__":
    main()
