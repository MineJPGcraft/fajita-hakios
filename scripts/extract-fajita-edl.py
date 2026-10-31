#!/usr/bin/env python3
"""Extract appended DTBs and DTBO entries from read-only fajita EDL dumps."""
import argparse
import struct
from pathlib import Path
import zlib


def extract_boot(path: Path, out: Path) -> int:
    data = path.read_bytes()
    if data[:8] != b"ANDROID!":
        raise ValueError(f"{path}: not an Android boot image")
    kernel_size = struct.unpack_from("<I", data, 8)[0]
    page_size = struct.unpack_from("<I", data, 36)[0]
    dec = zlib.decompressobj(31)
    dec.decompress(data[page_size:page_size + kernel_size])
    tail = dec.unused_data
    out.mkdir(parents=True, exist_ok=True)
    pos = count = 0
    while pos + 8 <= len(tail) and tail[pos:pos + 4] == b"\xd0\x0d\xfe\xed":
        size = struct.unpack_from(">I", tail, pos + 4)[0]
        if size < 40 or pos + size > len(tail):
            raise ValueError(f"{path}: invalid DTB at offset {pos:#x}")
        (out / f"{path.stem}-{count}.dtb").write_bytes(tail[pos:pos + size])
        pos += size
        count += 1
    if pos != len(tail):
        raise ValueError(f"{path}: trailing bytes after DTBs")
    return count


def extract_dtbo(path: Path, out: Path) -> int:
    data = path.read_bytes()
    if len(data) < 32:
        raise ValueError(f"{path}: short DTBO image")
    magic, total, header, entry_size, count, entries, _, _ = struct.unpack_from(">8I", data)
    if magic != 0xD7B7AB1E or header < 32 or entry_size < 32:
        raise ValueError(f"{path}: not a DTBO table")
    if total > len(data) or entries + count * entry_size > total:
        raise ValueError(f"{path}: invalid DTBO bounds")
    out.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        size, offset = struct.unpack_from(">2I", data, entries + index * entry_size)
        if offset + size > total:
            raise ValueError(f"{path}: invalid DTBO entry {index}")
        (out / f"overlay-{index:02}.dtbo").write_bytes(data[offset:offset + size])
    return count


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=Path)
    ap.add_argument("--dtbo", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if not args.boot and not args.dtbo:
        ap.error("provide --boot and/or --dtbo")
    if args.boot:
        print(f"{args.boot}: extracted {extract_boot(args.boot, args.out / 'boot')} DTBs")
    if args.dtbo:
        print(f"{args.dtbo}: extracted {extract_dtbo(args.dtbo, args.out / 'dtbo')} DTBOs")


if __name__ == "__main__":
    main()
