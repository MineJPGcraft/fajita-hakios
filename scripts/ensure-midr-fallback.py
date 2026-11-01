#!/usr/bin/env python3
"""Insert the fajita MIDR fallback before the loader reports its platform."""
import sys
from pathlib import Path


MARKER = "/* GOP is optional on fajita; identify the SoC before failure/reset paths use platform fields. */"
ANCHOR = '\tprint("platform %s\\n", PLAT->name);'
FALLBACK = """\
\t/* GOP is optional on fajita; identify the SoC before failure/reset paths use platform fields. */
\tif (PLAT == &platform_qemu_virt) {
\t\tuint64_t midr = READ_SYSREG(MIDR_EL1);
\t\tfor (unsigned i = 0; i < sizeof(platforms) / sizeof(platforms[0]); i++)
\t\t\tfor (int j = 0; j < 2; j++)
\t\t\t\tif (platforms[i]->midr_part[j] && (midr >> 24 & 0xFF) == 0x51 &&
\t\t\t\t    (midr >> 4 & 0xFFF) == platforms[i]->midr_part[j])
\t\t\t\t\tPLAT = platforms[i];
\t}
"""


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ensure-midr-fallback.py LOADER_C")
    path = Path(sys.argv[1])
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        if (source.count(MARKER) != 1 or source.count(FALLBACK) != 1 or
                source.count(ANCHOR) != 1 or source.index(MARKER) > source.index(ANCHOR)):
            raise SystemExit(f"MIDR fallback is duplicated or misplaced in {path}")
        print("MIDR fallback already applied")
        return
    if source.count(ANCHOR) != 1:
        raise SystemExit(f"cannot locate unique platform report in {path}")
    path.write_text(source.replace(ANCHOR, FALLBACK + ANCHOR, 1), encoding="utf-8")
    print("MIDR fallback applied")


if __name__ == "__main__":
    main()
