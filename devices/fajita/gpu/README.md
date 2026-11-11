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

## Device results (2026-11-11)

Run on the phone with `fastboot boot` only; no partition was written.

| Steps | Result |
| --- | --- |
| 1..6 | **pass** — `fajitagpu: 6 steps: kkkkkk` |
| 1..9 | **pass** — `fajitagpu: 9 steps: kkkkkkkkk` |
| 1..10 | **failed twice, screen black, device left USB entirely** |

Steps 1-9 passing is the substantive result: the GCC GPU branch bits, the RPMh
supply names, the GPUCC power switches, the CX domain, the GMU clocks and the
GMU's own always-on counter all behave as the SDM845 documents say. The counter
being seen to count is what proves the GMU clock really runs. The SMMU and its
context banks read back as the boot loader left them, and a two-word write to
ITCM/DTCM is accepted and restorable.

### Why step 10 fails, and what was wrong twice

Writing the 32 KB `a630_gmu.bin` image kills the screen. Two corrections were
attempted and neither fixed it; the third is built but **not yet run**.

1. *Assumed the write ran past DTCM.* The register map puts
   `GMU_CM3_ITCM_START` at word `0xc00` and `GMU_CM3_DTCM_START` at `0x1c00`,
   only `0x4000` bytes apart, while the image is `0x8000` long. Checking Mesa's
   `a6xx_gmu.xml` confirmed both offsets are exactly what the code uses, and
   Linux's own legacy path (`a6xx_gmu.c:701-714`) writes the same `0x8000` from
   the same base in one go. **That hypothesis was wrong**; the offsets and the
   linear write are both correct.
2. *Assumed the order was wrong.* Linux runs `gmu_write(GENERAL_7, 1)` and
   `a6xx_rpmh_start()` before `a6xx_gmu_fw_load()`, and this driver had the
   RSC/PDC step at 11, after the load. Reordering brought step 10 forward but
   **called the wrong function**: `step_rsc_pdc()` only *reads* the PDC and
   reports whether the reads match what was there, so it powers nothing on.
   The machine still went black.
3. *Fixed the function, not just the order.* `gmu_rpmh_start()` now does what
   `a6xx_gmu.c:458-485` does and nothing else: write `RSCC_CONTROL_REQ = 1<<1`,
   `dsb sy`, poll `RSCC_CONTROL_ACK` for bit 1, poll `RSCC_SEQ_BUSY_DRV0` for
   idle, write `REQ = 0`. It returns before the load if any step fails. This is
   deliberately **not** `gmu_rsc_sleep_wake()`, which sleeps the RSC first and
   restores both halves -- a reversible probe for a later step, not the
   pre-load power-on Linux performs.

Step 9 passing while step 10 fails is consistent with this: reads and a
two-word write work on an unpowered domain, a full image does not.

**Next run should start at `fajitagpu=10` again.** If it goes black a third
time, the remaining suspects are the write primitive (Linux uses one
`memcpy_toio` plus `wmb()`; this driver issues 8192 separate `str` writes with
no barrier) and whether `step_rsc_pdc`'s read-modify-restore of the PDC is
itself unsafe before the GMU is up.

## Build state

- `fajita_adreno.o` builds clean, exports `_fajita_adreno_init`, and the kernel
  links it alongside `_mi8_adreno_init` with no symbol conflict.
- The only build failure is the tree's documented post-link `check_all_exports`
  problem, which predates this work and is unrelated to it.
- The bring-up is opt-in behind `fajitagpu=N`, which runs steps 1..N and stops.
  With no such argument the driver touches nothing, so it is inert in every
  image that does not pass it -- including the one touch was verified on.

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
