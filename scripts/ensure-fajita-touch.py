#!/usr/bin/env python3
"""Stage the opt-in fajita touch driver into the prepared dipper XNU tree."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("fajita_synaptics.c", "fajita_touch_transport.inc", "fajita_touch_report.h", "fajita_touch_board.h", "fajita_touch_layout_probe.inc")


def ensure_after(path: Path, anchor: str, line: str) -> None:
    text = path.read_text()
    if line in text.splitlines():
        if text.splitlines().count(line) != 1:
            raise SystemExit(f"duplicate touch integration in {path}")
        return
    matches = [old for old in text.splitlines() if old.startswith(anchor)]
    if len(matches) != 1:
        raise SystemExit(f"expected one prepared dipper anchor {anchor!r} in {path}")
    path.write_text(text.replace(matches[0] + "\n", matches[0] + "\n" + line + "\n", 1))


def ensure_exit_gate(path: Path) -> None:
    text = path.read_text()
    anchor = '\texit_call = thread_call_allocate(exit_hold_elapsed, NULL);\n'
    replacement = ('\t/* Fajita bring-up must not treat noisy contacts as an exit request. */\n'
                   '\tuint32_t fajita_on = 0, fajita_exit = 0;\n'
                   '\tif (PE_parse_boot_argn("fajita_touch", &fajita_on, sizeof(fajita_on)) && fajita_on == 1 &&\n'
                   '\t    !(PE_parse_boot_argn("fajita_touch_exit", &fajita_exit, sizeof(fajita_exit)) && fajita_exit == 1)) {\n'
                   '\t\texit_call = NULL;\n'
                   '\t\tloader_log_line("fajita-touch-phase: five-finger exit disabled during bring-up\\n");\n'
                   '\t} else {\n'
                   '\t\texit_call = thread_call_allocate(exit_hold_elapsed, NULL);\n'
                   '\t}\n')
    previous = replacement.replace('loader_log_line("fajita-touch-phase:', 'redwood_tl("fajita-touch:')
    if previous in text:
        path.write_text(text.replace(previous, replacement, 1))
        return
    if replacement in text:
        return
    if text.count(anchor) != 1:
        raise SystemExit("unrecognized touch-exit initialization; refusing to replace it")
    path.write_text(text.replace(anchor, replacement, 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xnu", type=Path, required=True)
    ap.add_argument("--extra-files", type=Path, required=True)
    args = ap.parse_args()
    for name in FILES:
        data = (ROOT / "devices/fajita/kernel" / name).read_bytes()
        for parent in (args.xnu / "bsd/dev", args.extra_files / "bsd/dev"):
            parent.mkdir(parents=True, exist_ok=True)
            target = parent / name
            if not target.exists() or target.read_bytes() != data:
                target.write_bytes(data)
    ensure_after(args.xnu / "bsd/conf/files", "bsd/dev/mi8_fts.c", "bsd/dev/fajita_synaptics.c\tstandard")
    ensure_after(args.xnu / "bsd/dev/redwood_input.c", "\tmi8_fts_init();", "\tfajita_touch_init();")
    ensure_exit_gate(args.xnu / "bsd/dev/redwood_input.c")
    ensure_after(args.xnu / "bsd/sys/redwood_input.h", "void mi8_fts_init(void);", "void fajita_touch_init(void);           /* boot-arg fajita_touch=1: OnePlus 6T S3706 RMI */")
    print("fajita touch source staged; input hook, declaration and build list present")


if __name__ == "__main__":
    main()
