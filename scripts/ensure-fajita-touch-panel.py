#!/usr/bin/env python3
"""Stage an opt-in framebuffer panel without enabling loader USB/console."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def add_once(text, anchor, addition):
    if text.count(anchor) != 1:
        raise SystemExit(f"touch panel: expected one anchor {anchor!r}")
    if addition in text:
        if text.count(addition) != 1 or anchor + addition not in text:
            raise SystemExit("duplicate or misplaced touch panel integration")
        return text
    return text.replace(anchor, anchor + addition, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loader", required=True, type=Path)
    args = ap.parse_args()
    for name in ("fajita_touch_panel.h", "fajita_touch_panel.inc"):
        source = ROOT / "devices/fajita/loader" / name
        target = args.loader.parent / name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            target.write_bytes(source.read_bytes())
    text = args.loader.read_text()
    text = add_once(text, 'static int fajita_touch_diag_requested;\n', 'static int fajita_touch_panel_requested;\n')
    text = add_once(text, '_Static_assert(sizeof(struct kernel_live) == 120, "paired kernel live size");\n',
                    'static void fajita_touch_panel_update(const struct kernel_live *live, uint64_t slide);\n')
    text = add_once(text, '#include "fajita_touch_rpmh.inc"\n', '#include "fajita_touch_panel.inc"\n')
    text = add_once(text, '\tfajita_touch_diag_requested = 0;\n', '\tfajita_touch_panel_requested = 0;\n')
    anchor = ('\tint fajita_touch_prepared = fajita_touch_prepare_before_handoff(pi.cmdline, this_mpidr, kernel_mpidr);\n')
    text = add_once(text, anchor,
                    '\tfajita_touch_panel_requested = PLAT == &platform_fajita && fp_arg(pi.cmdline, "fajita_touch_panel=");\n')
    old = 'if (fajita_touch_diag_requested && noted <= 120) { screen_quiet = 0; }'
    new = 'if (!fajita_touch_panel_requested && fajita_touch_diag_requested && noted <= 120) { screen_quiet = 0; }'
    if new in text:
        if text.count(new) != 1 or old in text:
            raise SystemExit("touch panel: duplicate diagnostic console gate")
    else:
        if text.count(old) != 1:
            raise SystemExit("touch panel: unrecognized diagnostic console gate")
        text = text.replace(old, new, 1)
    text = add_once(text, '\t\t\t\t\tscreen_quiet = restore_quiet;\n',
                    '\t\t\t\t\tif (fajita_touch_panel_requested) { fajita_touch_panel_update(live, slide); }\n')
    if text != args.loader.read_text():
        args.loader.write_text(text)
    print("opt-in touch panel staged; console stays quiet and only the small framebuffer region is refreshed")


if __name__ == "__main__":
    main()
