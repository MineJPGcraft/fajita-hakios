#!/usr/bin/env python3
"""Audit both silicon layouts; reject mixed mappings and stale PASS reports."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".work/tests/fajita-touch-dt-audit"
OUT.mkdir(parents=True, exist_ok=True)
source = (ROOT / "devices/fajita/kernel/fajita_touch_board.h").read_text()
script = ROOT / "scripts/audit-fajita-touch-dt.py"
positive_report = OUT / "positive.json"
positive = subprocess.run(["python3", str(script), "--out", str(positive_report)],
                          capture_output=True, text=True)
assert positive.returncode == 0, positive.stdout + positive.stderr
report = json.loads(positive_report.read_text())
assert report["status"] == "pass"
assert [b["msm_id"] for b in report["bases"]] == [[321, 0x20001], [321, 0x10000], [321, 0x20000]]
assert [b["tlmm"]["compatible"] for b in report["bases"]] == [
    ["qcom,sdm845-pinctrl-v2"], ["qcom,sdm845-pinctrl"], ["qcom,sdm845-pinctrl-v2"]]
assert [b["model"] for b in report["bases"]] == [
    "Qualcomm Technologies, Inc. SDM845 v2.1 SoC",
    "Qualcomm Technologies, Inc. SDM845 v1 SoC",
    "Qualcomm Technologies, Inc. SDM845 v2 SoC"]
assert [b["board_id"] for b in report["bases"]] == [[0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
layouts = report["vendor_pinctrl"]["layouts"]
assert {k: layouts["v1"]["gpio"][k]["ctl_pa"] for k in ("49", "50", "88", "99", "125")} == {
    "49": 0x03931000, "50": 0x03932000, "88": 0x03D58000,
    "99": 0x03963000, "125": 0x0397D000}
assert {k: layouts["v2"]["gpio"][k]["ctl_pa"] for k in ("49", "50", "88", "99", "125")} == {
    "49": 0x03931000, "50": 0x03932000, "88": 0x03558000,
    "99": 0x03963000, "125": 0x0357D000}
assert [b["layout"] for b in report["vendor_pinctrl"]["base_selections"]] == ["v2", "v1", "v2"]

cases = {
    "voltage": source.replace("3008000u", "3000000u"),
    "reset": source.replace("0x03963000u", "0x03563000u"),
    "dictionary": source.replace("0x0c3f000cu", "0x85fe0000u"),
    "drv": source.replace("RSC_DRV_ID 2u", "RSC_DRV_ID 0u"),
    "active": source.replace("ACTIVE_TCS_FIRST 0u", "ACTIVE_TCS_FIRST 2u"),
    "reserved": source.replace("0x8ab00000u", "0xffffffffu"),
    "duplicate": source + "\n#define FAJITA_TOUCH_AVDD_UV 3008000u\n",
    "expression": source.replace("AVDD_UV 3008000u", "AVDD_UV (3008000u)"),
    "vddio-v1-uses-v2": source.replace("0x03d58000u", "0x03558000u"),
    "vddio-v2-uses-v1": source.replace("0x03558000u", "0x03d58000u"),
    "irq-v1-uses-v2": source.replace("0x0397d000u", "0x0357d000u"),
    "irq-v2-uses-v1": source.replace("0x0357d000u", "0x0397d000u"),
    "layout-v2-uses-v1": source.replace("TLMM_LAYOUT_V2 2u", "TLMM_LAYOUT_V2 1u"),
    "chip-id": source.replace("CHIP_ID 321u", "CHIP_ID 322u"),
    "unversioned-vddio": source + "\n#define FAJITA_TOUCH_VDDIO_PA 0x03d58000u\n",
    "unversioned-irq": source + "\n#define FAJITA_TOUCH_IRQ_PA 0x0397d000u\n",
}
for name, changed in cases.items():
    assert changed != source
    header, report = OUT / (name + ".h"), OUT / (name + ".json")
    header.write_text(changed)
    report.write_text('{"status":"pass"}')
    run = subprocess.run(["python3", str(script),
                          "--board-header", str(header), "--out", str(report)], capture_output=True, text=True)
    assert run.returncode != 0, name
    assert json.loads(report.read_text())["status"] == "fail", name

# Exercise actual per-base selector/mapper with altered metadata, without
# modifying any original EDL DTB or vendor source on disk.
spec = importlib.util.spec_from_file_location("fajita_touch_dt_audit", script)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
base_cases = {}
original_bases = json.loads(positive_report.read_text())["bases"]
for index, label, compatible in ((0, "v21-uses-v1", "qcom,sdm845-pinctrl"),
                                  (1, "v1-uses-v2", "qcom,sdm845-pinctrl-v2"),
                                  (2, "v2-uses-v1", "qcom,sdm845-pinctrl")):
    changed = copy.deepcopy(original_bases)
    changed[index]["tlmm"]["compatible"] = [compatible]
    base_cases[label] = changed
for label, key, value in (("unknown-revision", "msm_id", [321, 0x20002]),
                           ("wrong-chip", "msm_id", [322, 0x20001]),
                           ("missing-revision", "msm_id", [321])):
    changed = copy.deepcopy(original_bases)
    changed[0][key] = value
    base_cases[label] = changed
for label, value in (("unknown-compatible", ["qcom,unknown-pinctrl"]),
                      ("ambiguous-compatible", ["qcom,sdm845-pinctrl-v2", "qcom,sdm845-pinctrl"]),
                      ("missing-compatible", [])):
    changed = copy.deepcopy(original_bases)
    changed[0]["tlmm"]["compatible"] = value
    base_cases[label] = changed
changed = copy.deepcopy(original_bases)
changed[2]["tlmm"]["reg"][0] += 0x100000
base_cases["same-layout-different-resource"] = changed
for name, changed in base_cases.items():
    try:
        audit.vendor_mapping(changed)
    except ValueError:
        pass
    else:
        raise AssertionError(name)
print(f"DT audit: both revision mappings retained; {len(cases)} invalid board headers rejected with stale PASS cleared; "
      f"{len(base_cases)} incompatible/unknown SoC descriptions rejected")
