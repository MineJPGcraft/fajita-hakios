#!/usr/bin/env python3
"""Check panel staging on independent loader copies, without a firmware build."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-touch-panel-staging"
OUT.mkdir(parents=True, exist_ok=True)
STAGER = ROOT / "scripts/ensure-fajita-touch-panel.py"
SAVED = ROOT / ".work/candidates/fajita-touch-rpmh-hostchecked/loader.c"
CURRENT = ROOT / "ref/dipper-xnu/work/hackios/xnuloader/loader.c"

# These exact integration points are the contract under test. Keeping the
# fixture independent of the stager's implementation catches lost guards and
# accidental movement as well as repeated insertion.
PAIRS = (
    ("static int fajita_touch_diag_requested;\n",
     "static int fajita_touch_panel_requested;\n"),
    ('_Static_assert(sizeof(struct kernel_live) == 120, "paired kernel live size");\n',
     "static void fajita_touch_panel_update(const struct kernel_live *live, uint64_t slide);\n"),
    ('#include "fajita_touch_rpmh.inc"\n',
     '#include "fajita_touch_panel.inc"\n'),
    ("\tfajita_touch_diag_requested = 0;\n",
     "\tfajita_touch_panel_requested = 0;\n"),
    ("\tint fajita_touch_prepared = fajita_touch_prepare_before_handoff(pi.cmdline, this_mpidr, kernel_mpidr);\n",
     '\tfajita_touch_panel_requested = PLAT == &platform_fajita && fp_arg(pi.cmdline, "fajita_touch_panel=");\n'),
    ("\t\t\t\t\tscreen_quiet = restore_quiet;\n",
     "\t\t\t\t\tif (fajita_touch_panel_requested) { fajita_touch_panel_update(live, slide); }\n"),
)
OLD_CONSOLE = "if (fajita_touch_diag_requested && noted <= 120) { screen_quiet = 0; }"
NEW_CONSOLE = "if (!fajita_touch_panel_requested && fajita_touch_diag_requested && noted <= 120) { screen_quiet = 0; }"


def baseline_from(path):
    text = path.read_text()
    # The current tree may already be staged. Strip only the known complete
    # additions to construct the same pre-panel loader, without reverse patches
    # or depending on the historical archive being present on another host.
    for _, addition in PAIRS:
        assert text.count(addition) <= 1, (path, "duplicate fixture addition", addition)
        text = text.replace(addition, "")
    assert text.count(NEW_CONSOLE) <= 1, (path, "duplicate fixture console gate")
    text = text.replace(NEW_CONSOLE, OLD_CONSOLE)
    for anchor, addition in PAIRS:
        assert text.count(anchor) == 1 and addition not in text, (path, anchor)
    assert text.count(OLD_CONSOLE) == 1 and NEW_CONSOLE not in text
    return text


def loader_for(name, text):
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    loader = folder / "loader.c"
    loader.write_text(text)
    return loader


def stage(loader, success=True):
    before = loader.read_bytes()
    result = subprocess.run([sys.executable, str(STAGER), "--loader", str(loader)],
                            text=True, capture_output=True)
    if success:
        assert result.returncode == 0, (loader, result.stdout, result.stderr)
    else:
        assert result.returncode != 0, (loader, "malformed integration was accepted")
        assert loader.read_bytes() == before, (loader, "failure modified loader")
    return loader.read_text()


def check_staged(text):
    for anchor, addition in PAIRS:
        assert text.count(anchor) == 1 and text.count(addition) == 1
        assert anchor + addition in text, ("integration moved from anchor", addition)
    assert OLD_CONSOLE not in text and text.count(NEW_CONSOLE) == 1
    assert text.count('"fajita_touch_panel="') == 1
    assert text.count("fajita_touch_panel_update(live, slide);") == 1
    assert text.index(PAIRS[1][1]) < text.index(PAIRS[2][1])
    assert text.index(NEW_CONSOLE) < text.index(PAIRS[5][1])
    # The opted-in panel retains the original two-second, post-arrival sample
    # path and is not attached to CPU_ON, IRQ delivery or any USB event.
    update = text.index(PAIRS[5][1])
    beat = text.rfind("seconds_since(started) >= noted + 2", 0, update)
    arrived = text.rfind("if (!arrived)", 0, update)
    assert 0 <= beat < arrived < update


baseline = baseline_from(SAVED if SAVED.exists() else CURRENT)
loader = loader_for("normal", baseline)
staged = stage(loader)
check_staged(staged)
first = loader.read_bytes()
stage(loader)
assert loader.read_bytes() == first, "second staging must be byte-for-byte idempotent"
for name in ("fajita_touch_panel.h", "fajita_touch_panel.inc"):
    source = ROOT / "devices/fajita/loader" / name
    target = loader.parent / name
    assert target.read_bytes() == source.read_bytes(), ("header/include not synchronized", name)
    target.write_bytes(b"stale staging fixture\n")
stage(loader)
assert loader.read_bytes() == first, "header refresh must preserve staged loader bytes"
for name in ("fajita_touch_panel.h", "fajita_touch_panel.inc"):
    assert (loader.parent / name).read_bytes() == (ROOT / "devices/fajita/loader" / name).read_bytes()

# Also exercise fixture reconstruction from the currently staged source; no
# old candidate archive is required for this route.
fallback_loader = loader_for("current-source-fallback", baseline_from(CURRENT))
check_staged(stage(fallback_loader))
fallback_first = fallback_loader.read_bytes()
stage(fallback_loader)
assert fallback_loader.read_bytes() == fallback_first

bad_cases = 0
for number, (anchor, addition) in enumerate(PAIRS):
    for kind, changed in (
        ("missing", baseline.replace(anchor, "", 1)),
        ("duplicate", baseline.replace(anchor, anchor + anchor, 1)),
    ):
        stage(loader_for(f"baseline-{number}-{kind}", changed), success=False)
        bad_cases += 1
    # Corruption must not hide behind an addition already being present.
    for kind, changed in (
        ("missing-anchor", staged.replace(anchor, "", 1)),
        ("duplicate-anchor", staged.replace(anchor, anchor + anchor, 1)),
        ("duplicate-addition", staged.replace(addition, addition + addition, 1)),
        ("moved-addition", staged.replace(addition, "", 1) + "\n" + addition),
    ):
        stage(loader_for(f"staged-{number}-{kind}", changed), success=False)
        bad_cases += 1

for kind, changed in (
    ("missing", baseline.replace(OLD_CONSOLE, "", 1)),
    ("duplicate", baseline.replace(OLD_CONSOLE, OLD_CONSOLE + "\n" + OLD_CONSOLE, 1)),
    ("staged-missing", staged.replace(NEW_CONSOLE, "", 1)),
    ("staged-duplicate", staged.replace(NEW_CONSOLE, NEW_CONSOLE + "\n" + NEW_CONSOLE, 1)),
    ("mixed", staged + "\n" + OLD_CONSOLE + "\n"),
):
    stage(loader_for(f"console-{kind}", changed), success=False)
    bad_cases += 1

print(f"touch panel staging: headers, gates, positions, idempotence, current-source fallback and {bad_cases} rejected malformed loaders passed")
