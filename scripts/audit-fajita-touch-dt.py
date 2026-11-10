#!/usr/bin/env python3
"""Audit local fajita touch DT descriptions against the compiled board constants.

No USB/device access is performed. Matching descriptions do not prove that a
rail is enabled, that the controller responds, or that its Product ID is S3706.
"""
import argparse
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR_COMMIT = "222822c2d0042067b7964c6302ef4d756dc4bed9"
LIMITATION = ("Description audit only: this does not prove runtime power, "
              "controller identity, I2C access, or working touch.")
TLMM_LAYOUTS = {
    "qcom,sdm845-pinctrl": {
        "name": "v1", "layout": 1, "versions": (0x10000,),
        "source": "drivers/pinctrl/qcom/pinctrl-sdm845.c",
        "tiles": {49: "NORTH", 50: "NORTH", 88: "SOUTH", 99: "NORTH", 125: "NORTH"},
    },
    "qcom,sdm845-pinctrl-v2": {
        "name": "v2", "layout": 2, "versions": (0x20000, 0x20001),
        "source": "drivers/pinctrl/qcom/pinctrl-sdm845-v2.c",
        "tiles": {49: "NORTH", 50: "NORTH", 88: "EAST", 99: "NORTH", 125: "EAST"},
    },
}


class Fdt:
    def __init__(self, tool, path):
        self.tool, self.path = str(tool), path

    def query(self, mode, node, prop=None):
        args = [self.tool, *mode, str(self.path), node]
        if prop is not None:
            args.append(prop)
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"{self.path}:{node}:{prop}: {result.stderr.strip()}")
        return result.stdout.strip()

    def properties(self, node):
        return self.query(["-p"], node).splitlines()

    def children(self, node):
        return self.query(["-l"], node).splitlines()

    def raw(self, node, prop):
        return bytes(int(v, 16) for v in self.query(["-t", "bx"], node, prop).split())

    def cells(self, node, prop):
        raw = self.raw(node, prop)
        require(len(raw) % 4 == 0, f"unaligned cells: {node}:{prop}")
        return list(struct.unpack(f">{len(raw) // 4}I", raw))

    def strings(self, node, prop):
        raw = self.raw(node, prop)
        require(raw.endswith(b"\0"), f"not a string list: {node}:{prop}")
        return raw[:-1].decode("ascii").split("\0")

    def symbol(self, label):
        values = self.strings("/__symbols__", label)
        require(len(values) == 1, f"ambiguous symbol: {label}")
        return values[0]

    def metadata(self):
        return {"path": str(self.path.relative_to(ROOT)),
                "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest()}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pin_state(dt, path):
    mux, config = path + "/mux", path + "/config"
    mux_pins = dt.strings(mux, "pins")
    require(mux_pins == dt.strings(config, "pins"), f"pin mux/config mismatch: {path}")
    properties = dt.properties(config)
    biases = sorted(p for p in properties if p.startswith("bias-"))
    return {"pins": mux_pins, "function": dt.strings(mux, "function"),
            "drive_ma": dt.cells(config, "drive-strength"), "bias": biases}


def base_info(dt):
    bus = dt.symbol("qupv3_se12_i2c")
    active = dt.symbol("qupv3_se12_i2c_active")
    supply = dt.symbol("pm8998_l28")
    tlmm = dt.symbol("tlmm")
    model = dt.strings("/", "model")
    require(len(model) == 1, "missing or ambiguous base model")
    chip = {"model": model[0], "msm_id": dt.cells("/", "qcom,msm-id"),
            "board_id": dt.cells("/", "qcom,board-id")}
    tlmm_info = {"path": tlmm, "reg": dt.cells(tlmm, "reg"),
                 "compatible": dt.strings(tlmm, "compatible")}
    layout_for_base({**chip, "tlmm": tlmm_info})
    reg = dt.cells(bus, "reg")
    pins = pin_state(dt, active)
    require(reg == [0xA90000, 0x4000], f"unexpected SE12 register range: {reg}")
    require(dt.strings(bus, "compatible") == ["qcom,i2c-geni"], "unexpected I2C binding")
    require(dt.cells(bus, "pinctrl-0") == dt.cells(active, "phandle"),
            "SE12 default pinctrl does not select its active state")
    require(pins == {"pins": ["gpio49", "gpio50"], "function": ["qup12"],
                     "drive_ma": [2], "bias": ["bias-disable"]},
            f"unexpected SE12 pin configuration: {pins}")
    require(dt.strings(supply, "regulator-name") == ["pm8998_l28"], "supply label/name mismatch")
    rsc, dictionary = "/soc/mailbox@179e0000", "/soc/qcom,cmd-db@861e0000"
    reserved = "/reserved-memory/removed_region@85fc0000"
    parent = supply.rsplit("/", 1)[0]
    power = {"rsc_reg": dt.cells(rsc, "reg"), "drv_id": dt.cells(rsc, "qcom,drv-id"),
             "tcs_config": dt.cells(rsc, "qcom,tcs-config"),
             "dictionary_reg": dt.cells(dictionary, "reg"),
             "reserved_reg": dt.cells(reserved, "reg"),
             "resource_name": dt.strings(parent, "qcom,resource-name"),
             "regulator_type": dt.strings(parent, "qcom,regulator-type"),
             "supported_modes": dt.cells(parent, "qcom,supported-modes"),
             "mode_thresholds_ua": dt.cells(parent, "qcom,mode-threshold-currents")}
    require(power == {"rsc_reg": [0x179e0000, 0x100, 0x179e0d00, 0x3000],
                     "drv_id": [2], "tcs_config": [2, 2, 0, 3, 1, 3, 3, 1],
                     "dictionary_reg": [0xc3f000c, 8],
                     "reserved_reg": [0, 0x85fc0000, 0, 0x4b40000],
                     "resource_name": ["ldoa28"], "regulator_type": ["pmic4-ldo"],
                     "supported_modes": [2, 4], "mode_thresholds_ua": [0, 1]},
            f"unreviewed RPMh/command DB description: {power}")
    require(dt.strings(parent, "compatible") == ["qcom,rpmh-vrm-regulator"], "not a VRM rail")
    require(dt.cells(parent, "mboxes")[0:1] == dt.cells(rsc, "phandle"),
            "ldoa28 does not use the selected apps RSC")
    return {**dt.metadata(), **chip, "power": power, "bus": {"path": bus, "reg": reg,
            "base_status": dt.strings(bus, "status"), "active_path": active,
            "active": pins}, "supply": {"path": supply, "name": "pm8998_l28",
            "voltage_min_uv": dt.cells(supply, "regulator-min-microvolt"),
            "voltage_max_uv": dt.cells(supply, "regulator-max-microvolt")},
            "tlmm": tlmm_info}


def overlay_info(dt):
    fixups = {p: dt.strings("/__fixups__", p) for p in dt.properties("/__fixups__")}
    targets = [v for v in fixups.get("qupv3_se12_i2c", []) if v.endswith(":target:0")]
    require(len(targets) == 1, "missing/ambiguous SE12 target fixup")
    fragment = targets[0].split(":")[0]
    overlay = fragment + "/__overlay__"
    nodes = [overlay + "/" + n for n in dt.children(overlay) if n == "synaptics-rmi-ts@20"]
    require(len(nodes) == 1, "missing/ambiguous Synaptics child on SE12")
    node = nodes[0]
    require(dt.cells(fragment, "target") == [0xFFFFFFFF], "bus target is not externally fixed up")
    require(dt.strings(overlay, "status") == ["ok"], "overlay does not enable SE12")
    require(node + ":vdd_2v8-supply:0" in fixups.get("pm8998_l28", []),
            "touch AVDD is not fixed up to pm8998_l28")
    expected_gpio = {"synaptics,reset-gpio": [0xFFFFFFFF, 99, 0],
                     "synaptics,irq-gpio": [0xFFFFFFFF, 125, 0x2008],
                     "synaptics,1v8-gpio": [0xFFFFFFFF, 88, 0]}
    for prop, values in expected_gpio.items():
        require(dt.cells(node, prop) == values, f"unexpected {prop}")
        require(node + ":" + prop + ":0" in fixups.get("tlmm", []),
                f"{prop} is not fixed up to TLMM")
    require(node + ":interrupt-parent:0" in fixups.get("tlmm", []), "IRQ parent is not TLMM")
    cells = {p: dt.cells(node, p) for p in
             ("reg", "interrupts", "synaptics,avdd-voltage", "synaptics,avdd-current",
              "synaptics,display-coords", "synaptics,panel-coords", "synaptics,tx-rx-num")}
    expected = {"reg": [32], "interrupts": [125, 0x2008],
                "synaptics,avdd-voltage": [3008000, 3008000],
                "synaptics,avdd-current": [20000],
                "synaptics,display-coords": [1080, 2160],
                "synaptics,panel-coords": [1080, 2160], "synaptics,tx-rx-num": [15, 30]}
    require(cells == expected, f"unexpected touch parameters: {cells}")
    compatible = dt.strings(node, "compatible")
    require(compatible == ["HWK,synaptics,s3320"], "unexpected touch compatible")
    properties = dt.properties(node)
    flags = sorted(p for p in properties if p.startswith("oem,"))
    require(flags == ["oem,fajta", "oem,support_1080x2160_tp", "oem,support_1080x2340_tp",
                      "oem,support_hw_poweroff"], f"unexpected OEM flags: {flags}")
    for flag in flags:
        require(dt.raw(node, flag) == b"", f"OEM flag has an unexpected value: {flag}")
    require("synaptics,enable2v8-gpio" not in properties and "vcc_i2c_1v8-supply" not in properties,
            "additional power resource needs review")
    active = dt.symbol("ts_active")
    require(dt.cells(node, "pinctrl-0") == dt.cells(active, "phandle"),
            "touch pinctrl-0 does not select ts_active")
    require(dt.cells("/__local_fixups__" + node, "pinctrl-0") == [0],
            "touch active pinctrl phandle is not locally fixed up")
    pins = pin_state(dt, active)
    require(pins == {"pins": ["gpio99", "gpio125"], "function": ["gpio"],
                     "drive_ma": [16], "bias": ["bias-pull-up"]},
            f"unexpected touch active pin configuration: {pins}")
    description = {"node": node, "compatible": compatible, "bus_target": "qupv3_se12_i2c",
                   "bus_fragment": fragment, "avdd_supply": "pm8998_l28", "gpio": expected_gpio,
                   "cells": cells, "flags": flags, "active_path": active, "active": pins,
                   "all_touch_properties_hex": {p: dt.raw(node, p).hex() for p in properties}}
    return {**dt.metadata(), "description": description}


def literal_macro(source, name):
    require(len(re.findall(r"^\s*#\s*define\s+" + name + r"\b", source, re.M)) == 1,
            f"board/source macro must occur exactly once: {name}")
    match = re.search(r"^[ \t]*#[ \t]*define[ \t]+" + name +
                      r"[ \t]+(0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*[ \t]*"
                      r"(?://[^\n]*|/\*[^\n]*\*/[ \t]*)?$", source, re.M)
    require(match is not None, f"missing literal macro: {name}")
    return int(match[1], 16 if match[1].lower().startswith("0x") else 10)


def layout_for_base(base):
    compatible = base["tlmm"]["compatible"]
    require(len(compatible) == 1 and compatible[0] in TLMM_LAYOUTS,
            f"unsupported TLMM compatible: {compatible}")
    description = TLMM_LAYOUTS[compatible[0]]
    msm_id = base["msm_id"]
    require(len(msm_id) == 2 and msm_id[0] == 321 and msm_id[1] in description["versions"],
            f"TLMM compatible/SoC revision mismatch: {compatible}, msm-id {msm_id}")
    return description


def vendor_mapping(bases):
    repo = ROOT / "ref/oneplus-sdm845-q"
    sources, layouts, selections = {}, {}, []
    for base in bases:
        description = layout_for_base(base)
        name, relative = description["name"], description["source"]
        if relative not in sources:
            path = repo / relative
            source_bytes = path.read_bytes()
            original = subprocess.run(["git", "-C", str(repo), "show", VENDOR_COMMIT + ":" + relative],
                                      capture_output=True)
            require(original.returncode == 0 and source_bytes == original.stdout,
                    f"local pinctrl source does not match the fixed vendor commit: {relative}")
            source = source_bytes.decode()
            require(re.search(r"\.ctl_reg\s*=\s*base\s*\+\s*REG_SIZE\s*\*\s*id", source),
                    f"unrecognized vendor GPIO_CTL address formula: {relative}")
            sources[relative] = (source, hashlib.sha256(source_bytes).hexdigest())
        source, digest = sources[relative]
        stride = literal_macro(source, "REG_SIZE")
        offsets = {tile: literal_macro(source, tile) for tile in sorted(set(description["tiles"].values()))}
        reg = base["tlmm"]["reg"]
        require(len(reg) >= 2, "missing DT TLMM resource")
        tlmm_base, tlmm_size = reg[:2]
        pins = {}
        for gpio, tile in description["tiles"].items():
            groups = re.findall(r"\bPINGROUP\(\s*" + str(gpio) + r"\s*,\s*(NORTH|SOUTH|EAST)\s*,", source)
            require(groups == [tile], f"unexpected {name} tile for GPIO{gpio}: {groups}")
            pa = tlmm_base + offsets[tile] + stride * gpio
            require(tlmm_base <= pa and pa + stride <= tlmm_base + tlmm_size,
                    f"{name} GPIO{gpio} is outside the DT TLMM resource")
            pins[str(gpio)] = {"tile": tile, "ctl_pa": pa}
        mapping = {"path": str((repo / relative).relative_to(ROOT)), "commit": VENDOR_COMMIT,
                   "sha256": digest, "compatible": base["tlmm"]["compatible"],
                   "layout": description["layout"], "chip_versions": list(description["versions"]),
                   "tile_offsets": offsets, "tlmm_resource_pa": tlmm_base,
                   "gpio_stride": stride, "gpio": pins}
        require(name not in layouts or layouts[name] == mapping,
                f"TLMM mappings differ within {name} base DTBs")
        layouts[name] = mapping
        selections.append({"path": base["path"], "model": base["model"],
                           "msm_id": base["msm_id"], "board_id": base["board_id"], "layout": name})
    require(set(layouts) == {"v1", "v2"}, "both SDM845 v1 and v2 descriptions must be audited")
    for gpio in ("49", "50", "99"):
        require(layouts["v1"]["gpio"][gpio] == layouts["v2"]["gpio"][gpio],
                f"shared GPIO{gpio} differs between SDM845 v1/v2")
    return {"layouts": layouts, "base_selections": selections,
            "revision_limitation": "These input variants do not identify this phone's silicon revision; runtime ChipInfo selects the layout.",
            "i2c_role_provenance": "unknown: DT and pinctrl do not label SDA versus SCL"}


def vendor_power(bases):
    repo = ROOT / "ref/oneplus-sdm845-q"
    sources, evidence = {}, []
    for relative in ("drivers/soc/qcom/cmd-db.c", "drivers/mailbox/qcom-rpmh-mailbox.c",
                     "drivers/regulator/rpmh-regulator.c",
                     "include/dt-bindings/soc/qcom,tcs-mbox.h",
                     "include/dt-bindings/regulator/qcom,rpmh-regulator.h"):
        raw = (repo / relative).read_bytes()
        fixed = subprocess.run(["git", "-C", str(repo), "show", VENDOR_COMMIT + ":" + relative],
                               capture_output=True)
        require(fixed.returncode == 0 and fixed.stdout == raw,
                f"power source differs from pinned vendor commit: {relative}")
        sources[Path(relative).name] = raw.decode()
        evidence.append({"path": str((repo / relative).relative_to(ROOT)), "commit": VENDOR_COMMIT,
                         "sha256": hashlib.sha256(raw).hexdigest()})
    enum = sources["qcom,tcs-mbox.h"]
    require([literal_macro(enum, n) for n in ("SLEEP_TCS", "WAKE_TCS", "ACTIVE_TCS", "CONTROL_TCS")]
            == [0, 1, 2, 3], "vendor TCS binding order changed")
    mailbox = sources["qcom-rpmh-mailbox.c"]
    for name, value in {"RSC_DRV_TCS_OFFSET": 672, "RSC_DRV_CMD_OFFSET": 20,
                        "MAX_CMDS_PER_TCS": 16,
                        "DRV_PRNT_CHLD_CONFIG": 12, "DRV_NUM_TCS_SHIFT": 6,
                        "DRV_NCPT_SHIFT": 27}.items():
        require(literal_macro(mailbox, name) == value, f"vendor RSC geometry changed: {name}")
    require("tcs = &drv->tcs[val[2 * i]];" in mailbox and
            "tcs->tcs_offset = st;" in mailbox and "st += tcs->num_tcs;" in mailbox,
            "vendor DT-to-TCS index allocation changed")
    modes = sources["qcom,rpmh-regulator.h"]
    require(literal_macro(modes, "RPMH_REGULATOR_MODE_LPM") == 2 and
            literal_macro(modes, "RPMH_REGULATOR_MODE_HPM") == 4, "generic mode enum changed")
    regulator = sources["rpmh-regulator.c"]
    require(literal_macro(regulator, "RPMH_REGULATOR_MODE_PMIC4_LDO_HPM") == 7,
            "pmic4 LDO HPM encoding changed")
    require(re.search(r"rpmh_regulator_mode_map_pmic4_ldo\[.*?\[RPMH_REGULATOR_MODE_HPM\].*?"
                      r"\.pmic_mode\s*=\s*RPMH_REGULATOR_MODE_PMIC4_LDO_HPM", regulator, re.S),
            "pmic4 LDO map no longer maps HPM to 7")
    require("mv = DIV_ROUND_UP(min_uv, 1000);" in regulator and
            "mode[i].min_load_ua <= load_ua" in regulator, "voltage/load conversion changed")
    for name, value in (("VOLTAGE", 0), ("ENABLE", 1), ("MODE", 2)):
        require(re.search(r"RPMH_REGULATOR_REG_VRM_" + name + r"\s*=\s*" + str(value) + r"\s*,", regulator),
                "VRM register order changed")
    cmd = sources["cmd-db.c"]
    require("res.start = readl_relaxed(dict);" in cmd and
            "res.end = res.start + readl_relaxed(dict + 0x4);" in cmd,
            "command DB dictionary indirection changed")
    p = bases[0]["power"]
    pairs = list(zip(p["tcs_config"][::2], p["tcs_config"][1::2]))
    active = next((sum(n for kind, n in pairs[:i] if kind != 3), count)
                  for i, (kind, count) in enumerate(pairs) if kind == 2)
    load = 20000
    mode = next(m for m, threshold in reversed(list(zip(p["supported_modes"], p["mode_thresholds_ua"])))
                if load >= threshold)
    require(active == (0, 2) and mode == 4, "unexpected active pool or selected regulator mode")
    return {"sources": evidence, "active_first": active[0], "active_count": active[1],
            "request": {"uv": 3008000, "load_ua": load, "mv": 3008, "enable": 1,
                        "generic_mode": mode, "hardware_mode": 7},
            "limitation": "DT and vendor encoding do not prove firmware slot ownership or an electrical rail state."}


def check_header(path, vendor, power, base):
    source = path.read_text()
    v1, v2 = vendor["layouts"]["v1"], vendor["layouts"]["v2"]
    require(not re.search(r"^\s*#\s*define\s+FAJITA_TOUCH_(?:IRQ|VDDIO)_PA\b", source, re.M),
            "unversioned IRQ/VDDIO addresses must not bypass runtime layout selection")
    expected = {"I2C_ADDRESS": 32, "SDA_GPIO": 49, "SCL_GPIO": 50, "IRQ_GPIO": 125,
                "RESET_GPIO": 99, "VDDIO_GPIO": 88, "IRQ_DRIVE_MA": 16,
                "I2C_DRIVE_MA": 2, "AVDD_UV": 3008000, "AVDD_LOAD_UA": 20000,
                "CHIP_ID": 321, "TLMM_LAYOUT_V1": v1["layout"], "TLMM_LAYOUT_V2": v2["layout"],
                "SDA_PA": v1["gpio"]["49"]["ctl_pa"],
                "SCL_PA": v1["gpio"]["50"]["ctl_pa"],
                "IRQ_V1_PA": v1["gpio"]["125"]["ctl_pa"],
                "IRQ_V2_PA": v2["gpio"]["125"]["ctl_pa"],
                "RESET_PA": v1["gpio"]["99"]["ctl_pa"],
                "VDDIO_V1_PA": v1["gpio"]["88"]["ctl_pa"],
                "VDDIO_V2_PA": v2["gpio"]["88"]["ctl_pa"],
                "CMD_DB_DICT_PA": base["dictionary_reg"][0],
                "CMD_DB_RESERVED_START": base["reserved_reg"][1],
                "CMD_DB_RESERVED_END": base["reserved_reg"][1] + base["reserved_reg"][3],
                "RSC_CONFIG_PA": base["rsc_reg"][0], "RSC_TCS_PA": base["rsc_reg"][2],
                "RSC_DRV_ID": base["drv_id"][0], "ACTIVE_TCS_FIRST": power["active_first"],
                "ACTIVE_TCS_COUNT": power["active_count"], "MAX_X": 1080, "MAX_Y": 2340}
    checked = {}
    for suffix, value in expected.items():
        name = "FAJITA_TOUCH_" + suffix
        actual = literal_macro(source, name)
        require(actual == value, f"board/DT mismatch: {name}={actual}, expected {value}")
        checked[name] = actual
    return {"path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "macros": checked,
            "pa_note": "PA checks select each fixed vendor PINGROUP source using that base's TLMM compatible and msm-id; GPIO88/125 differ between v1/v2.",
            "coordinate_note": "1080x2340 comes from the vendor S3706 branch with the "
                "oem,support_1080x2340_tp flag, overriding DT panel-coords 1080x2160.",
            "i2c_role_limitation": "DT identifies the gpio49/gpio50 QUP12 pair but does not label "
                "SDA versus SCL. This audit checks the existing board assignment, not its wire-role provenance."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fdtget", type=Path, default=ROOT / ".work/fajita-tools/root/usr/bin/fdtget")
    parser.add_argument("--board-header", type=Path, default=ROOT / "devices/fajita/kernel/fajita_touch_board.h")
    parser.add_argument("--out", type=Path, default=ROOT / ".work/fajita-touch-dt-audit.json")
    args = parser.parse_args()
    try:
        bases = [base_info(Fdt(args.fdtget, ROOT / f".work/edl-extract/boot/fajita-edl-a-{i}.dtb"))
                 for i in range(3)]
        overlays = [overlay_info(Fdt(args.fdtget, ROOT / f".work/edl-extract/dtbo/overlay-{i:02}.dtbo"))
                    for i in range(6, 13)]
        require(all(o["description"] == overlays[0]["description"] for o in overlays),
                "fajita touch descriptions differ across overlays 06..12")
        normalized = [{k: b[k] for k in ("bus", "supply", "power")} for b in bases]
        require(all(b == normalized[0] for b in normalized), "touch bus/power descriptions differ across base DTBs")
        vendor = vendor_mapping(bases)
        power = vendor_power(bases)
        report = {"status": "pass", "limitation": LIMITATION, "bases": bases, "overlays": overlays,
                  "vendor_pinctrl": vendor, "vendor_power": power,
                  "board_header": check_header(args.board_header.resolve(), vendor, power, bases[0]["power"])}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    except (ValueError, OSError) as error:
        # A failed rerun must not leave an earlier PASS at the requested path.
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({"status": "fail", "error": str(error),
                                           "limitation": LIMITATION}, indent=2) + "\n")
        except OSError:
            pass
        raise SystemExit(f"touch DT audit failed: {error}") from error
    print(f"PASS: 3 revision-specific base DTBs, v1/v2 TLMM mappings, 7 consistent fajita overlays, board constants; {args.out}")
    print(LIMITATION)


if __name__ == "__main__":
    main()
