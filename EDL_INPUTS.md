# EDL read-only inputs for fajita

The phone has no root, so the next porting pass should use read-only EDL
partition dumps. Do not dump or upload `userdata`, `metadata`, `persist`, or
any partition containing personal data. Keep raw dumps outside Git and record
only their SHA-256 values in local notes.

Ask the EDL tool to show the GPT first. Partition labels vary by firmware
release; use the labels reported by that command rather than assuming a slot
layout. For the current first pass, the only requested input is the device
tree:

| Input | Why it is needed |
| --- | --- |
| `boot_a` and `boot_b` | Android kernel image; some OxygenOS builds carry the base DTB here |
| `vendor_boot_a` and `vendor_boot_b` | Android vendor ramdisk and base DTB; extract only the DTB if your EDL tool supports it |
| `dtbo_a` and `dtbo_b` | Board overlays for the selected firmware slot, including panel, touch, GPIO and regulators |

If the tool can extract a merged/live DTB from the matching OxygenOS slot,
provide that DTB plus the firmware build identifier. Otherwise keep the
partition dumps local and provide only their SHA-256 values first. Do not mix
files from different OxygenOS builds. We will request `xbl`, `modem`, `dsp` or
other firmware partitions only when a specific UEFI or driver task needs them;
they are not needed to start parsing the device tree.

Before sharing anything, remove serial numbers, IMEI-bearing logs and personal
paths. A partition name list plus SHA-256 and the firmware build identifier is
enough for triage; raw UEFI and firmware files should be shared only when a
specific driver or loader step requires them.

## Inputs found in this workspace

The local EDL folder currently contains `fajita_boot_edl_a.img` and
`fajita_dtbo_edl_a.img`. The boot image is Android boot v1 and contains three
appended Qualcomm SDM845 base DTBs. The DTBO table has 16 entries. Their
metadata says Qualcomm MTP, which describes the Qualcomm base platform used by
the OnePlus port; it does not by itself invalidate the EDL dump. Retain these
as this handset's observed EDL inputs and validate individual panel/peripheral
nodes against the overlay contents and vendor configuration before enabling
drivers.

The official sparse images under `.work/.official/fajita_sparse/` identify
OnePlus 6T Android 10 build `2101210516` (`QKQ1.190716.003`). The EDL boot
image has an embedded Android 9 build string. This version difference does not
block porting: Android itself is not the target OS. Use EDL trees as the
hardware baseline and official vendor files as a separate source of compatible
firmware and configuration; avoid assuming cross-version binary compatibility
for any specific firmware until checked.

The official sparse directory is a set of ext4 filesystem images: `system`,
`vendor`, `odm`, `data`, `op1`, and `op2`. It is a complete system-side dump,
but it does not contain an Android boot image (`ANDROID!`) or a DTBO table
(`d7b7ab1e`). Boot-chain partitions are separate inputs even when the official
system/vendor dump is complete.

The EDL tree extraction is reproducible with
`scripts/extract-fajita-edl.py`; extracted working copies live under `.work/`
and stay out of Git. The base DTBs produce the same SDM845 `soc.args` values
as the public compatibility tree. Remaining useful EDL input is the GPT map
and, when the partition layout needs to be reproduced, the relevant boot
control metadata. No Android userspace partition is needed to boot the iOS
kernel. Do not alter the phone's partition table until a tested iOS image and
a device-specific reversible installation plan exist.
