#!/usr/bin/env python3
"""Verify the final Mach-O touch/live ABI, not only the staged source."""
import argparse
import json
from pathlib import Path
import struct
import subprocess


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--kernel', type=Path, required=True)
    ap.add_argument('--loader', type=Path, required=True)
    args = ap.parse_args()
    data = args.kernel.read_bytes()
    if len(data) < 32 or struct.unpack_from('<I', data)[0] != 0xfeedfacf:
        raise SystemExit('expected a little-endian 64-bit Mach-O kernel')
    segments = []
    offset = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, offset)
        if size < 8 or offset + size > len(data):
            raise SystemExit('invalid Mach-O load command')
        if command == 0x19:
            _, _, _, va, vm_size, file_offset, file_size, *_ = struct.unpack_from('<II16sQQQQIIII', data, offset)
            if file_offset + file_size > len(data) or file_size > vm_size:
                raise SystemExit('invalid Mach-O segment boundary')
            segments.append((va, vm_size, file_offset, file_size))
        offset += size

    def read_va(address: int, count: int) -> bytes:
        for va, _, file_offset, file_size in segments:
            if address >= va and address + count <= va + file_size:
                return data[file_offset + address - va:file_offset + address - va + count]
        raise SystemExit(f'diagnostic object not file-backed: {address:#x}+{count}')

    def mapped(address: int, count: int) -> bool:
        return any(address >= va and address + count <= va + size for va, size, _, _ in segments)

    symbols = {}
    for line in subprocess.check_output(['llvm-nm', '-n', str(args.kernel)], text=True).splitlines():
        fields = line.split()
        if len(fields) == 3:
            try:
                symbols[fields[2]] = int(fields[0], 16)
            except ValueError:
                pass
    live_address = symbols['_loader_live']
    touch_address = symbols['_fajita_touch_live']
    words = struct.unpack('<15Q', read_va(live_address, 120))
    if words[0] != 0x214556494c554e58 or words[10] != 0x324556494c554e58:
        raise SystemExit('old live ABI prefix was changed')
    if words[12:] != (0x334556494c554e58, 120, touch_address):
        raise SystemExit('compiled appended live ABI does not match the loader')
    if touch_address & 63:
        raise SystemExit('touch status object must own an aligned cache line')
    touch = struct.unpack('<8Q', read_va(touch_address, 64))
    if touch[:3] != (0x314556494c505446, 64, 0):
        raise SystemExit('compiled touch status magic/size/initial-state mismatch')
    if touch[7] != 0x31544d4c505446:
        raise SystemExit('compiled optional transport/report telemetry marker mismatch')
    for address, count in ((words[2], words[6] + words[7]), (words[3], 4),
                           (words[4], 4), (words[5], 1), (touch_address, 64)):
        if not mapped(address, count):
            raise SystemExit('live diagnostic pointer leaves the loaded kernel segments')
    loader = args.loader.read_bytes()
    for marker in (b'FT=%d data=%x', b'log tail unavailable: metadata changed', b'TLMM-read'):
        if marker not in loader:
            raise SystemExit(f'loader lacks compiled diagnostic reader: {marker!r}')
    print(json.dumps({'result': 'compiled live ABI and pointers verified',
                      'old_prefix_bytes': 96, 'live_size': 120,
                      'loader_live_va': hex(live_address), 'touch_live_va': hex(touch_address),
                      'touch_object_size': 64, 'initial_state': 0}, indent=2))


if __name__ == '__main__':
    main()
