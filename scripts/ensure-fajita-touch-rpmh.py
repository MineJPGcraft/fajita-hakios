#!/usr/bin/env python3
"""Stage pre-CPU_ON fajita RPMh/GPIO preparation with a final kernel acknowledgement."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def insert_once(text, anchor, addition):
    if addition in text:
        if text.count(addition) != 1:
            raise SystemExit("duplicate fajita RPMh integration")
        return text
    if text.count(anchor) != 1:
        raise SystemExit(f"expected one loader anchor {anchor!r}")
    return text.replace(anchor, anchor + addition, 1)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loader", type=Path, required=True)
    args = ap.parse_args()
    sources = [ROOT / "devices/fajita/loader" / name for name in
               ("fajita_touch_rpmh.inc", "fajita_cmd_db.h", "fajita_rpmh_transaction.h", "fajita_chipinfo.h")]
    sources += [ROOT / "devices/fajita/kernel" / name for name in
                ("fajita_touch_board.h", "fajita_touch_power_seq.h")]
    for source in sources:
        target = args.loader.parent / source.name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            target.write_bytes(source.read_bytes())
    text = args.loader.read_text()
    # Upgrade the exact historical acknowledgement in an already-staged
    # tree. Fresh and repeated staging still use the same complete block.
    old_ack = ('const char *ack = fajita_touch_prepared ? " fajita_touch_prepared=1" : " fajita_touch_prepared=0";\n'
               '\t\tif (n + sizeof(" fajita_touch_prepared=0") > BOOT_LINE_LENGTH) {')
    new_ack = ('const char *ack = " fajita_touch_prepared=0 fajita_touch_tlmm=0";\n'
               '\t\tif (fajita_touch_prepared && fajita_power_tlmm_layout == 1) {\n'
               '\t\t\tack = " fajita_touch_prepared=1 fajita_touch_tlmm=1";\n'
               '\t\t} else if (fajita_touch_prepared && fajita_power_tlmm_layout == 2) {\n'
               '\t\t\tack = " fajita_touch_prepared=1 fajita_touch_tlmm=2";\n'
               '\t\t}\n'
               '\t\tif (n + str_len(ack) + 1 > BOOT_LINE_LENGTH) {')
    if text.count(old_ack) > 1:
        raise SystemExit("duplicate historical touch acknowledgement")
    text = text.replace(old_ack, new_ack)
    text = insert_once(text, "static int starts_with(const char *s, const char *prefix);\n",
                       '\n#include "fajita_touch_rpmh.inc"\n')
    anchor = '\tif (battery_requested) {\n\t\tcharger_start();\n\t}\n'
    text = insert_once(text, anchor,
                       '\tint fajita_touch_prepared = fajita_touch_prepare_before_handoff(pi.cmdline, this_mpidr, kernel_mpidr);\n')
    anchor = ('\tfor (const char *c = pi.cmdline; *c && n < BOOT_LINE_LENGTH - 1; c++) {\n'
              '\t\tba->commandLine[n++] = *c;\n\t}\n')
    text = insert_once(text, anchor,
        '\t/* Only this final firmware-produced acknowledgement authorizes touch MMIO. */\n'
        '\tif (PLAT == &platform_fajita) {\n'
        '\t\tconst char *ack = " fajita_touch_prepared=0 fajita_touch_tlmm=0";\n'
        '\t\tif (fajita_touch_prepared && fajita_power_tlmm_layout == 1) {\n'
        '\t\t\tack = " fajita_touch_prepared=1 fajita_touch_tlmm=1";\n'
        '\t\t} else if (fajita_touch_prepared && fajita_power_tlmm_layout == 2) {\n'
        '\t\t\tack = " fajita_touch_prepared=1 fajita_touch_tlmm=2";\n'
        '\t\t}\n'
        '\t\tif (n + str_len(ack) + 1 > BOOT_LINE_LENGTH) {\n'
        '\t\t\tprint("fajita-power: no room for acknowledgement; refusing handoff\\n");\n'
        '\t\t\treturn 0;\n\t\t}\n'
        '\t\tfor (const char *c = ack; *c; c++) { ba->commandLine[n++] = *c; }\n'
        '\t}\n\tba->commandLine[n] = 0;\n')
    if text != args.loader.read_text():
        args.loader.write_text(text)
    print("fajita RPMh/GPIO sequence staged before CPU_ON; final firmware acknowledgement installed")

if __name__ == "__main__":
    main()
