#!/usr/bin/env python3
"""Build a fajita one-shot XNUP from explicit loader, kernel and APFS inputs."""
import argparse
import struct
from pathlib import Path


HEADER = struct.Struct("<II")
ENTRY = struct.Struct("<16sII")
PAYLOAD_BASE = 0xF0000000
PAYLOAD_MAX = 0x04000000
REQUIRED = {"loader.efi", "flashboot", "kernel", "ramdisk", "cmdline", "oneshot"}


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def parse(data: bytes) -> list[tuple[str, int, bytes]]:
    if len(data) < HEADER.size:
        raise ValueError("truncated XNUP header")
    magic, body_size = HEADER.unpack_from(data)
    if magic != 0x50554E58 or body_size != len(data) - HEADER.size:
        raise ValueError("invalid XNUP magic or declared length")
    entries: list[tuple[str, int, bytes]] = []
    offset = HEADER.size
    while offset + ENTRY.size <= len(data):
        raw_name, size, reserved = ENTRY.unpack_from(data, offset)
        name = raw_name.split(b"\0", 1)[0].decode("ascii")
        offset += ENTRY.size
        if not name:
            if size or reserved or offset != len(data):
                raise ValueError("invalid XNUP terminator or trailing data")
            return entries
        if reserved:
            raise ValueError(f"nonzero reserved field in XNUP entry: {name}")
        end = offset + size
        padded_end = offset + align(size, 16)
        if end > len(data) or padded_end > len(data):
            raise ValueError(f"truncated XNUP entry: {name}")
        entries.append((name, reserved, data[offset:end]))
        if any(data[end:padded_end]):
            raise ValueError(f"nonzero padding in XNUP entry: {name}")
        offset = padded_end
    raise ValueError("XNUP has no terminator")


def encode(entries: list[tuple[str, int, bytes]]) -> bytes:
    body = bytearray()
    for name, reserved, blob in entries:
        encoded_name = name.encode("ascii")
        if len(encoded_name) >= 16:
            raise ValueError(f"XNUP entry name is too long: {name}")
        body.extend(ENTRY.pack(encoded_name, len(blob), reserved))
        body.extend(blob)
        body.extend(bytes(align(len(blob), 16) - len(blob)))
    body.extend(ENTRY.pack(b"", 0, 0))
    return HEADER.pack(0x50554E58, len(body)) + body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loader", required=True, type=Path)
    ap.add_argument("--kernel", required=True, type=Path)
    ap.add_argument("--flashboot", required=True, type=Path)
    ap.add_argument("--ramdisk", required=True, type=Path)
    ap.add_argument("--cmdline-file", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    cmdline = args.cmdline_file.read_text(encoding="ascii").strip()
    if not cmdline or "\0" in cmdline or "\n" in cmdline:
        raise SystemExit("cmdline must be one non-empty ASCII line")
    if "bootcpu=7" not in cmdline.split() or "cpus=1" not in cmdline.split() or "ufs=2" not in cmdline.split():
        raise SystemExit("fajita UFS profile requires bootcpu=7 cpus=1 ufs=2")
    entries = [
        ("loader.efi", 0, args.loader.read_bytes()),
        ("flashboot", 0, args.flashboot.read_bytes()),
        ("kernel", 0, args.kernel.read_bytes()),
        ("ramdisk", 0, args.ramdisk.read_bytes()),
        ("cmdline", 0, cmdline.encode("ascii") + b"\0"),
        ("oneshot", 0, b"1\0"),
    ]
    for name, _, blob in entries:
        if not blob:
            raise SystemExit(f"empty payload entry: {name}")
        if name in {"flashboot", "ramdisk"} and (len(blob) < 4096 or blob[32:36] != b"NXSB"):
            raise SystemExit(f"not an APFS container: {name}")
    result = encode(entries)
    if align(len(result), 16) > PAYLOAD_MAX - 0x100000:
        raise SystemExit(f"XNUP is too large for the BootShim window: {len(result)} bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result)
    checked = parse(result)
    if [(name, reserved) for name, reserved, _ in checked] != [(name, reserved) for name, reserved, _ in entries]:
        raise SystemExit("XNUP round-trip changed entry ordering or flags")
    print(f"{args.output}: {len(result)} bytes; built {len(entries)} entries from explicit inputs")


if __name__ == "__main__":
    main()
