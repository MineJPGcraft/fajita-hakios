#!/usr/bin/env python3
"""Decode the four 16-digit fields from fajita_touch_probe=2 (no device access)."""
import argparse
import json


def field(value: str) -> bytes:
    value = value.removeprefix("0x")
    if len(value) != 16:
        raise argparse.ArgumentTypeError("each field must contain exactly 16 hex digits")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid hexadecimal field") from exc


def decode(q5_field: bytes, q8_field: bytes, bases: bytes, control: bytes) -> dict:
    size5, size8 = q5_field[0], q8_field[0]
    q5 = (q5_field[1:] + q8_field[6:])[:min(size5, 9)]
    q8 = q8_field[1:6][:min(size8, 5)]
    q0, status, mode, irq, objects, maximum, report, flags = control
    has_descriptors = bool(q0 & 1) if flags & 0x40 or size5 else None
    controls = [n for n in range(64) if 1 + n // 8 < len(q5) and
                q5[1 + n // 8] & (1 << (n % 8))]
    data = [n for n in range(32) if 1 + n // 8 < len(q8) and
            q8[1 + n // 8] & (1 << (n % 8))]

    def address(base: int, present: list[int], number: int, width: int = 1):
        if number not in present:
            return None
        start = base + sum(n < number for n in present)
        return f"0x{start:02x}" if start + width <= 255 else "INVALID_PAGE_RANGE"

    result = {
        "four_byte_pdt": {
            "f01_query_command_control_data": bases[:4].hex(),
            "f12_query_command_control_data": bases[4:].hex(),
            "f01_differs_from_six_byte_read": bool(flags & 1),
            "f12_differs_from_six_byte_read": bool(flags & 2),
        },
        "query0_hex": f"0x{q0:02x}",
        "has_register_descriptors": has_descriptors,
        "query5_advertised_size": size5,
        "query5_captured_hex": q5.hex(),
        "query8_advertised_size": size8,
        "query8_captured_hex": q8.hex(),
        "control_registers_present": controls if flags & 4 else None,
        "data_registers_present": data if flags & 8 else None,
        "control_addresses": {
            str(n): address(bases[6], controls, n, {8: 4, 20: 3, 23: 2}.get(n, 1))
            for n in (8, 20, 23, 28)
        } if flags & 4 else None,
        "data_addresses": {
            str(n): address(bases[7], data, n, 2 if n == 15 else 1) for n in (1, 15)
        } if flags & 8 else None,
        "f01_readback": {
            "status_hex": f"0x{status:02x}",
            "control_hex": f"0x{mode:02x}",
            "irq_mask_hex": f"0x{irq:02x}",
            "sleep_mode": mode & 3,
            "configured": bool(mode & 0x80),
        } if flags & 0x80 else None,
        "control23_object_enable_hex": f"0x{objects:02x}" if flags & 0x10 else None,
        "control23_max_objects": maximum if flags & 0x10 else None,
        "control28_report_enable_hex": f"0x{report:02x}" if flags & 0x20 else None,
        "flags_hex": f"0x{flags:02x}",
        "note": "A snapshot before scan setup; unknown fields are null, not measured zeros. Product ID alone does not establish the data layout.",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for label in ("q5", "q8", "c1", "c2"):
        parser.add_argument("--" + label, required=True, type=field)
    args = parser.parse_args()
    print(json.dumps(decode(args.q5, args.q8, args.c1, args.c2), indent=2))


if __name__ == "__main__":
    main()
