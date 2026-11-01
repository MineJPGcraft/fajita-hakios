#!/usr/bin/env python3
"""Verify a fajita Android boot v1 image and its embedded one-shot XNUP."""
import argparse
import hashlib
import lzma
import struct
import uuid
import zlib
from pathlib import Path


ENTRY = struct.Struct("<16sII")
REQUIRED = {"loader.efi", "flashboot", "kernel", "ramdisk", "cmdline", "oneshot"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def section_header(data: bytes, offset: int) -> tuple[int, int, int]:
    if offset + 4 > len(data):
        raise ValueError("truncated FV section header")
    size = int.from_bytes(data[offset:offset + 3], "little")
    kind = data[offset + 3]
    header = 4
    if size == 0xFFFFFF:
        if offset + 8 > len(data):
            raise ValueError("truncated extended FV section header")
        size = struct.unpack_from("<I", data, offset + 4)[0]
        header = 8
    if size < header or offset + size > len(data):
        raise ValueError("FV section exceeds its container")
    return size, kind, header


def verify_embedded_loader(prefix: bytes, loader: bytes) -> None:
    """Read the loader from the actual LZMA-compressed FV inside the boot image."""
    guided = uuid.UUID("ee4e5898-3914-4259-9d6e-dc7bd79403cf").bytes_le
    loader_guid = uuid.UUID("d82f808b-ecca-4bb8-8e9f-9b9bafc490f5").bytes_le
    # The decompressor's own executable also contains this GUID. Accept only
    # a structurally valid GUID-defined section, not the first byte match.
    candidates = []
    pos = prefix.find(guided)
    while pos >= 0:
        if pos >= 4:
            try:
                size, kind, header = section_header(prefix, pos - 4)
                if kind == 2 and header == 4:
                    candidates.append((pos, size))
            except ValueError:
                pass
        pos = prefix.find(guided, pos + 16)
    if len(candidates) != 1:
        raise ValueError(f"expected one compressed FV section, found {len(candidates)}")
    pos, size = candidates[0]
    start = pos - 4
    data_offset, attributes = struct.unpack_from("<HH", prefix, pos + 16)
    if data_offset < 24 or data_offset >= size or not attributes & 1:
        raise ValueError("invalid LZMA FV section metadata")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    raw = decoder.decompress(prefix[start + data_offset:start + size], max_length=0x04000000)
    if not decoder.eof:
        raise ValueError("compressed FV is incomplete or exceeds 64 MiB")
    fv_size, fv_kind, fv_header = section_header(raw, 0)
    if fv_kind != 0x17:
        raise ValueError("decompressed section is not a firmware volume")
    fv = raw[fv_header:fv_size]
    if len(fv) < 56 or fv[40:44] != b"_FVH" or struct.unpack_from("<Q", fv, 32)[0] != len(fv):
        raise ValueError("invalid decompressed firmware volume header")
    off = (struct.unpack_from("<H", fv, 48)[0] + 7) & ~7
    applications = []
    while off + 24 <= len(fv) and fv[off:off + 24] != b"\xff" * 24:
        file_size = int.from_bytes(fv[off + 20:off + 23], "little")
        file_header = 24
        if fv[off + 19] & 1:
            if off + 32 > len(fv):
                raise ValueError("truncated extended FFS header")
            file_size = struct.unpack_from("<Q", fv, off + 24)[0]
            file_header = 32
        if file_size < file_header or off + file_size > len(fv):
            raise ValueError("FFS file exceeds its firmware volume")
        if fv[off:off + 16] == loader_guid and fv[off + 18] == 9:
            applications.append(fv[off + file_header:off + file_size])
        off += (file_size + 7) & ~7
    if len(applications) != 1:
        raise ValueError(f"expected one loader APPLICATION FFS, found {len(applications)}")
    sections = applications[0]
    pe_size, pe_kind, pe_header = section_header(sections, 0)
    if pe_kind != 0x10 or sections[pe_header:pe_size] != loader:
        raise ValueError("compressed firmware's loader differs from the XNUP loader")


def parse_payload(data: bytes) -> dict[str, bytes]:
    if len(data) < 8 or data[:4] != b"XNUP":
        raise ValueError("XNUP magic missing")
    declared = struct.unpack_from("<I", data, 4)[0]
    if declared != len(data) - 8:
        raise ValueError("XNUP declared size does not match its container")
    entries: dict[str, bytes] = {}
    offset = 8
    while offset + ENTRY.size <= len(data):
        raw_name, size, reserved = ENTRY.unpack_from(data, offset)
        offset += ENTRY.size
        name = raw_name.split(b"\0", 1)[0].decode("ascii")
        if not name:
            if size or reserved or offset != len(data):
                raise ValueError("invalid XNUP terminator")
            return entries
        end = offset + size
        padded_end = offset + ((size + 15) & ~15)
        if end > len(data) or padded_end > len(data) or reserved:
            raise ValueError(f"invalid XNUP entry bounds or flags: {name}")
        if name in entries:
            raise ValueError(f"duplicate XNUP entry: {name}")
        if any(data[end:padded_end]):
            raise ValueError(f"nonzero XNUP padding: {name}")
        entries[name] = data[offset:end]
        offset = padded_end
    raise ValueError("XNUP terminator missing")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--uefi-prefix", required=True, type=Path, help="BootShim + SDM845 UEFI FD bytes")
    ap.add_argument("--dtb", required=True, type=Path, help="fajita DTB expected at the end of the boot kernel")
    ap.add_argument("--loader", required=True, type=Path)
    ap.add_argument("--kernel", required=True, type=Path)
    ap.add_argument("--flashboot", required=True, type=Path)
    ap.add_argument("--ramdisk", required=True, type=Path)
    ap.add_argument("--cmdline-file", required=True, type=Path)
    args = ap.parse_args()

    image = args.image.read_bytes()
    if image[:8] != b"ANDROID!":
        raise SystemExit("Android boot image magic missing")
    kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr, tags_addr, page_size, version, _ = struct.unpack_from("<10I", image, 8)
    if version != 1 or page_size != 2048 or second_size:
        raise SystemExit(f"expected boot header v1/page 2048/no second image; got v{version}, page={page_size}, second={second_size}")
    if (kernel_addr, ramdisk_addr, tags_addr) != (0x10000000, 0x10000000, 0x10000000):
        raise SystemExit("Android boot load addresses differ from the reviewed fajita template")
    kernel_start = page_size
    kernel_end = kernel_start + kernel_size
    ramdisk_start = kernel_start + ((kernel_size + page_size - 1) // page_size) * page_size
    if kernel_end > len(image) or ramdisk_start + ramdisk_size > len(image):
        raise SystemExit("Android boot image sections extend past EOF")
    # EDK2 build.sh creates this with `echo > ramdisk`. It is only an Android
    # boot-header placeholder; the two actual APFS disks live inside XNUP.
    if ramdisk_size != 1 or image[ramdisk_start:ramdisk_start + ramdisk_size] != b"\n":
        raise SystemExit("Android boot ramdisk differs from EDK2's one-byte placeholder")
    decoder = zlib.decompressobj(31)
    boot_blob = decoder.decompress(image[kernel_start:kernel_end]) + decoder.flush()
    dtb = decoder.unused_data
    if not dtb.startswith(b"\xd0\x0d\xfe\xed") or len(dtb) < 8:
        raise SystemExit("appended FDT is missing")
    dtb_size = struct.unpack_from(">I", dtb, 4)[0]
    if dtb_size != len(dtb):
        raise SystemExit("appended FDT size does not match its header")
    if dtb != args.dtb.read_bytes():
        raise SystemExit("appended FDT differs from the fajita DTB supplied to the EDK2 build")
    prefix = args.uefi_prefix.read_bytes()
    if boot_blob[:len(prefix)] != prefix:
        raise SystemExit("BootShim + UEFI FD prefix differs from the supplied build")
    payload_header = boot_blob[len(prefix):len(prefix) + 8]
    if len(payload_header) != 8 or payload_header[:4] != b"XNUP":
        raise SystemExit("XNUP is not immediately after BootShim + UEFI FD")
    payload_size = struct.unpack_from("<I", payload_header, 4)[0] + 8
    payload = parse_payload(boot_blob[len(prefix):len(prefix) + payload_size])
    aligned_payload_end = len(prefix) + ((payload_size + 15) & ~15)
    if aligned_payload_end > len(boot_blob) or any(boot_blob[len(prefix) + payload_size:aligned_payload_end]):
        raise SystemExit("XNUP alignment padding is truncated or nonzero")
    if any(boot_blob[aligned_payload_end:]):
        raise SystemExit("unexpected data follows the aligned XNUP container")
    if set(payload) != REQUIRED:
        raise SystemExit(f"XNUP entries do not match no-USB one-shot requirements: {sorted(payload)}")
    if payload["oneshot"] != b"1\0":
        raise SystemExit("oneshot entry has an unexpected value")
    if payload["loader.efi"] != args.loader.read_bytes():
        raise SystemExit("XNUP loader.efi differs from the loader build")
    verify_embedded_loader(prefix, payload["loader.efi"])
    if payload["kernel"] != args.kernel.read_bytes():
        raise SystemExit("XNUP kernel differs from the XNU build")
    for name, path in (("flashboot", args.flashboot), ("ramdisk", args.ramdisk)):
        if payload[name] != path.read_bytes() or payload[name][32:36] != b"NXSB":
            raise SystemExit(f"XNUP {name} differs from the APFS input")
    expected_cmdline = args.cmdline_file.read_text(encoding="ascii").strip().encode("ascii") + b"\0"
    if payload["cmdline"] != expected_cmdline:
        raise SystemExit("XNUP command line differs from the reviewed fajita configuration")
    if not {b"bootcpu=7", b"cpus=1", b"ufs=2"}.issubset(payload["cmdline"].rstrip(b"\0").split()):
        raise SystemExit("UFS firmware service requires XNU on another core")
    if ((payload_size + 15) & ~15) > 0x04000000 - 0x100000:
        raise SystemExit("XNUP overlaps the final 1 MiB panic log")
    print(f"boot v1: {len(image)} bytes; kernel_addr=0x{kernel_addr:x}; ramdisk_addr=0x{ramdisk_addr:x}; tags_addr=0x{tags_addr:x}")
    print(f"UEFI prefix: {len(prefix)} bytes; compressed FV loader matches XNUP; FDT: {len(dtb)} bytes sha256={sha256(dtb)}")
    for name, data in payload.items():
        print(f"XNUP {name}: {len(data)} bytes sha256={sha256(data)}")
    print(f"verified no-USB one-shot image sha256={sha256(image)}")


if __name__ == "__main__":
    main()
