#!/usr/bin/env python3
"""Replace the XNUP in a fajita fastboot image while preserving its DTB and ramdisk."""
import argparse
import gzip
import os
import struct
import subprocess
import tempfile
import zlib


def align(value, page):
    return (value + page - 1) // page * page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", required=True, help="existing Android boot image")
    ap.add_argument("--uefi-image", required=True, help="fajita SDM845_UEFI.fd-bootshim ARM image")
    ap.add_argument("--payload", required=True, help="XNUP containing loader.efi")
    ap.add_argument("--mkbootimg", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    boot = open(args.boot, "rb").read()
    if boot[:8] != b"ANDROID!":
        raise SystemExit("input is not an Android boot image")
    fields = struct.unpack_from("<10I", boot, 8)
    kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr, tags_addr, page_size, version, _ = fields
    if version != 1 or page_size != 2048 or second_size:
        raise SystemExit(f"unsupported boot header: version={version}, page={page_size}, second={second_size}")
    kernel_start = page_size
    ramdisk_start = kernel_start + align(kernel_size, page_size)
    ramdisk = boot[ramdisk_start:ramdisk_start + ramdisk_size]
    if len(ramdisk) != ramdisk_size:
        raise SystemExit("truncated input ramdisk")

    compressed = boot[kernel_start:kernel_start + kernel_size]
    decoder = zlib.decompressobj(31)
    decoder.decompress(compressed)
    dtb = decoder.unused_data
    if not dtb.startswith(b"\xd0\x0d\xfe\xed"):
        raise SystemExit("input kernel does not have an appended FDT")

    image = bytearray(open(args.uefi_image, "rb").read())
    payload = open(args.payload, "rb").read()
    if payload[:4] != b"XNUP":
        raise SystemExit("payload is not XNUP")
    # Only accept the reviewed BootShim+FD prefix. Searching the firmware for
    # the bytes XNUP can mistake a loader constant/string for a container.
    if len(image) != 192 + 0x700000:
        raise SystemExit("expected a 192-byte BootShim plus 7 MiB FD without an old payload")
    image.extend(payload)
    image.extend(bytes(-len(payload) % 16))

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fajita-boot-", dir=out_dir) as temp:
        kernel_path = os.path.join(temp, "kernel")
        ramdisk_path = os.path.join(temp, "ramdisk")
        with open(kernel_path, "wb") as f:
            f.write(gzip.compress(bytes(image), mtime=0))
            f.write(dtb)
        with open(ramdisk_path, "wb") as f:
            f.write(ramdisk)
        subprocess.run([
            "python3", args.mkbootimg, "--kernel", kernel_path,
            "--ramdisk", ramdisk_path, "--pagesize", str(page_size),
            "--header_version", "1", "--base", hex(kernel_addr),
            "--kernel_offset", "0", "--ramdisk_offset", hex(ramdisk_addr - kernel_addr),
            "--tags_offset", hex(tags_addr - kernel_addr), "-o", args.output,
        ], check=True)
    print(f"{args.output}: embedded loader payload ({len(payload)} bytes), preserved DTB ({len(dtb)} bytes)")


if __name__ == "__main__":
    main()
