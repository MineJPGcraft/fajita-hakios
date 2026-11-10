# fajita GPU: Adreno 630 on SDM845

## What this is

The OnePlus 6T's GPU is an **Adreno 630**, chip id **`0x06030001`**, as its own
device tree states it (`/soc/qcom,kgsl-3d0@5000000`, `qcom,chipid = <0x6030001>`).

The driver in `devices/fajita/gpu/` is the SDM845 Adreno 630 bring-up ported
from the dipper (Xiaomi Mi 8) tree. Dipper is the **same SoC and the same GPU**,
so its register bases apply unchanged to fajita. Every one of them is checked
against fajita's own EDL device tree, not against dipper's:

| Block | Address | fajita device tree |
| --- | --- | --- |
| 3D core | `0x05000000` / `0x40000` | `/soc/qcom,kgsl-3d0@5000000`, `kgsl_3d0_reg_memory` |
| GMU | `0x0506a000` / `0x30000` | `/soc/qcom,gmu`, `kgsl_gmu_reg` |
| SMMU | `0x05040000` / `0x10000` | `/soc/arm,smmu-kgsl@5040000` |
| GPUCC | `0x05090000` / `0x9000` | `/soc/qcom,gpucc@5090000` |
| GFXCC | `0x05090000` / `0x9000` | `/soc/qcom,gfxcc@5090000` (same base, its own map) |
| HFI IRQ / GMU IRQ | `0x130` / `0x131` | `interrupt-names = "kgsl_hfi_irq", "kgsl_gmu_irq"` |

`scripts/verify-fajita-gpu.py` reads the extracted DTB and fails if any of these
drifts. Run it with:

```sh
python3 scripts/verify-fajita-gpu.py --dtb .work/edl-extract/boot/fajita-edl-a-0.dtb
```

## Why not the redwood (SC7280) driver

`hakios-xnu-kernel/drivers/gpu/hakios.adreno.c` targets the SC7280's **Adreno
642L**, chip id `0x06030500` — a different generation of the same a6xx family:

| | Adreno 630 (fajita) | Adreno 642L (redwood) |
| --- | --- | --- |
| chip id | `0x06030001` | `0x06030500` |
| 3D core | `0x05000000` | `0x03d00000` |
| GMU | `0x0506a000` / `0x30000` | `0x03d6a000` / `0x34000` |
| GPUCC | `0x05090000` | `0x03d90000` |
| separate GFXCC | yes | no |
| HFI IRQ | `0x130` | different |
| ZAP firmware | `a630_zap` | `a660_zap` |

Those offsets address different registers on the other chip, so no fallback is
offered: a chip id this driver does not name gets no bring-up at all rather than
one driven through another chip's writes.

## Firmware

Extracted from this phone's own vendor image
(`.work/.official/fajita_sparse/segment_4_d2cc4000.img`, ext4 "vendor",
`/firmware/`). All four are required and were taken from that image, not from
another device:

| File | Size | Use |
| --- | --- | --- |
| `a630_gmu.bin` | 32768 | the GMU's firmware (a "legacy" GMU takes one raw image) |
| `a630_sqe.fw` | 31980 | the 3D core's SQE microcode |
| `a630_zap.mdt` + `.b00/.b01/.b02` | 6684 + pieces | the signed ZAP shader TrustZone authenticates |

`devices/fajita/gpu/fw/SHA256SUMS` records their hashes. The driver asks for
them through the small image's `fw/` directory, the same way the other phones'
drivers do.

For the kernel to find them, place them into the small image's firmware
staging directory before building:

```sh
cp devices/fajita/gpu/fw/a630_* ~/.../hackios/fw-wifi/    # the image's fw/ source
```

## Building it in

```sh
python3 scripts/ensure-fajita-gpu.py --xnu <prepared XNU> --extra-files <hackios>/xnu/files
```

That copies `fajita_adreno.c` and `fajita_a630_hw_init.inc` into `bsd/dev/` and
adds `bsd/dev/fajita_adreno.c` to `bsd/conf/files`. It is idempotent.

`fajita_a630_hw_init.inc` carries **322 register writes** from Linux's
`a6xx_catalog.c` for the 0x06030001 SDM845 profile (`a630_hw_init_table`), in
the order `a6xx_gpu.c`'s `hw_init` requires. It is data, not a driver: the
caller owns GX power, clocks, the GMU OOB vote and security setup around it.

## State: compiled, not yet run on the phone

- `fajita_adreno.o` builds clean (509 KB), exports `_fajita_adreno_init`, and
  the kernel links it alongside `_mi8_adreno_init` with no symbol conflict.
- The only build failure is the tree's documented post-link
  `check_all_exports` problem, which predates this work and is unrelated to it.
- **Nothing has been run on the device.** The bring-up steps are opt-in behind
  boot-arg `fajitagpu=N`, which performs steps 1..N and stops. With no such
  argument the driver touches nothing, so it is inert in every image built so
  far — including the one touch was verified on.

### What the steps are

1. reads the GPU's branches in the global clock controller (GCC)
2. reads the names RPMh knows the GPU's supplies by (the loader's command database)
3. reads the GPU's own clock controller and the two power switches, but only if
   `GCC_GPU_CFG_AHB_CLK` is on — an unclocked access to it can hang the bus
4..5. writes the CX power switch and the clocks of the GMU/bus/SMMU half
6. reads the GMU's registers, including an always-on counter that must be seen
   counting if the GMU's clock really runs
7..8. reads the GPU's SMMU (ARM SMMU v2) and its context banks
9. writes and reads back two words in the GMU's instruction and data memory
10. writes `a630_gmu.bin` and reads all of it back; the processor stays in reset
11.. onwards: RSC/PDC, the GMU's SMMU, TrustZone's ZAP, GMU prepare and start,
   the HFI exchange, the 3D core's init, command processor, ZAP, and a first
   picture

Each step prints what it will touch before touching it, reads before writing,
and stops the sequence when it finds something it did not expect. That is why
step-by-step bring-up is worth more here than a single "start the GPU" call: a
phone that resets says nothing about which access did it, while
`fajitagpu=N` narrows the fault to one step per boot.

## Remaining before Metal can use it

The kernel-side GPU is only half of what the user asked for. `hakios-metal`
(`hakios-xnu-kernel/hakios-metal/`) is written entirely around chip id
`0x06030500` (`include/hakios-metal/a6xx.hpp`, `kChipId`), a6xx **gen4**:

| Constant | 7c+ Gen 3 (current) | Adreno 630 needs |
| --- | --- | --- |
| `kChipId` | `0x06030500` | `0x06030001` |
| `kHasLpac` | `true` | `false` (LPAC arrived after a630) |
| `kNumSpCores` | 2 | 1 (a630 has one SP core pair per its catalog entry) |
| `kInstrCacheSize` | 127 | the gen1 ceiling, to be taken from the catalog entry |
| `kFibersPerSp` | `128*2*16` | the a630 entry's value |
| `kCsSharedMemSize` | 32 KiB | the a630 entry's value |

Making Metal run on this phone means turning those constants into a per-chip
description selected by the chip id the kernel driver reports — the same shape
as the table idea above, but in `hakios-metal`. That work is not done yet; this
document records what the kernel side now has so it can be built on.

Verified on the host only. No GPU register has been written on the device.
