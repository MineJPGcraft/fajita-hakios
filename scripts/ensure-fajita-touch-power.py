#!/usr/bin/env python3
"""Stage firmware-owned touch power and hook it before the XNU handoff."""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def insert_once(text: str, anchor: str, addition: str) -> str:
    if addition in text:
        if text.count(addition) != 1:
            raise SystemExit("duplicate firmware touch power hook")
        return text
    if text.count(anchor) != 1:
        raise SystemExit(f"expected one loader anchor: {anchor!r}")
    return text.replace(anchor, anchor + addition, 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loader", type=Path, required=True)
    ap.add_argument("--experimental-vreg", action="store_true",
                    help="stage the failed Vreg experiment for host analysis only")
    args = ap.parse_args()
    if not args.experimental_vreg:
        # Runtime test stopped immediately before/in the Vreg call. Restore the
        # exact quiet-baseline loader delta rather than trying more PMIC writes.
        text = args.loader.read_text()
        markers = ('#include "fajita_touch_power.inc"', 'fajita_touch_power_prepare(pi.cmdline)',
                   'fajita_touch_vreg=')
        if not any(marker in text for marker in markers):
            print("retired Vreg hooks absent; any new power backend is staged separately")
            return
        patch = ROOT / "patches/fajita-loader-touch-power.patch"
        repo = args.loader.resolve().parent.parent
        subprocess.run(["git", "-C", str(repo), "apply", "--reverse", "--check", str(patch)], check=True)
        subprocess.run(["git", "-C", str(repo), "apply", "--reverse", str(patch)], check=True)
        if any(marker in args.loader.read_text() for marker in markers):
            raise SystemExit("incomplete removal of failed firmware touch power hooks")
        print("removed failed Vreg experiment; restored quiet loader handoff")
        return
    source = ROOT / "devices/fajita/loader/fajita_touch_power.inc"
    target = args.loader.parent / source.name
    data = source.read_bytes()
    if not target.exists() or target.read_bytes() != data:
        target.write_bytes(data)
    text = args.loader.read_text()
    text = insert_once(text, "static int starts_with(const char *s, const char *prefix);\n",
                       '\n#include "fajita_touch_power.inc"\n')
    text = insert_once(text, '\tif (battery_requested) {\n\t\tcharger_start();\n\t}\n',
                       '\tint touch_power_ready = fajita_touch_power_prepare(pi.cmdline);\n')
    # Remove the pre-build prototype hook: this XNU parser uses the LAST numeric
    # argument, so the acknowledgement must follow payload-provided arguments.
    obsolete = ('\t/* Firmware owns the supply; this leading argument cannot be supplied by the payload. */\n'
                '\tif (PLAT == &platform_fajita) {\n'
                '\t\tconst char *ack = touch_power_ready ? "fajita_touch_vreg=1 " : "fajita_touch_vreg=0 ";\n'
                '\t\tfor (const char *c = ack; *c && n < BOOT_LINE_LENGTH - 1; c++) {\n'
                '\t\t\tba->commandLine[n++] = *c;\n'
                '\t\t}\n\t}\n')
    text = text.replace(obsolete, "")
    anchor = ('\tfor (const char *c = pi.cmdline; *c && n < BOOT_LINE_LENGTH - 1; c++) {\n'
              '\t\tba->commandLine[n++] = *c;\n\t}\n')
    addition = ('\t/* XNU uses the last numeric boot argument: firmware acknowledgement comes last. */\n'
                '\tif (PLAT == &platform_fajita) {\n'
                '\t\tconst char *ack = touch_power_ready ? " fajita_touch_vreg=1" : " fajita_touch_vreg=0";\n'
                '\t\tif (n + sizeof(" fajita_touch_vreg=0") > BOOT_LINE_LENGTH) {\n'
                '\t\t\tprint("fajita-touch: no room for supply acknowledgement; refusing handoff\\n");\n'
                '\t\t\treturn 0;\n\t\t}\n'
                '\t\tfor (const char *c = ack; *c; c++) {\n'
                '\t\t\tba->commandLine[n++] = *c;\n'
                '\t\t}\n\t}\n'
                '\tba->commandLine[n] = 0;\n')
    text = insert_once(text, anchor, addition)
    if text != args.loader.read_text():
        args.loader.write_text(text)
    print("firmware touch power staged before handoff; generated acknowledgement follows payload arguments")


if __name__ == "__main__":
    main()
