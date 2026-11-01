#!/usr/bin/env python3
"""Make the PE timestamp deterministic for loader builds."""
import sys
from pathlib import Path

OLD = "lld-link /subsystem:efi_application /entry:efi_main /nodefaultlib /base:0xA1000000 /out:loader.efi loader.obj"
NEW = "lld-link /subsystem:efi_application /entry:efi_main /nodefaultlib /base:0xA1000000 /timestamp:0 /out:loader.efi loader.obj"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ensure-loader-repro.py BUILD_SH")
    path = Path(sys.argv[1])
    source = path.read_text(encoding="utf-8")
    if source.count(NEW) == 1 and source.count(OLD) == 0:
        print("reproducible loader timestamp already enabled")
        return
    if source.count(OLD) != 1:
        raise SystemExit(f"cannot locate unique lld-link command in {path}")
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    print("reproducible loader timestamp enabled")


if __name__ == "__main__":
    main()
