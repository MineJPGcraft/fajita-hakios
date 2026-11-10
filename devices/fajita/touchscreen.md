# Fajita touchscreen evidence and current status

## WORKING ON DEVICE (2026-11-10)

Candidate `.work/candidates/fajita-touch-working-device-tested/boot-fajita-touch-panel.img`,
12,840,960 bytes, SHA-256 `e66921026ee908608c0df436949233ff0660938586303055bc59660e8ec065c0`.
The user confirmed **touch responds on the phone** for the first time. It was
started with `fastboot boot` only; no partition was written. Host suite passes
and the compressed-FV loader matches the XNUP loader.

**The decisive fix was removing a check, not adding one.** `ft_interrupt_mask()`
had gated success on reading F01 `control1` back and comparing it to the value
just written. The pinned vendor driver reads that register and then discards
the result (`synaptics_driver_s3320.c:984-986`), returning success on bus
outcome alone. On this panel the write is accepted but the register does not
read back as written (observed address `0x0e`), so the comparison aborted the
entire identification sequence with `D=0x10D`. The readback is now published as
`ft_mask_readback` telemetry instead of gating anything.

### Diagnostic trail that located it

Each round's panel data invalidated a concrete assumption; three of the four
defects were in the diagnostics themselves, not the touch logic.

| `D` observed | Real cause | Correction |
| --- | --- | --- |
| `0x0357d000` | Diagnostic mode publishes every TLMM access, and `fajita_bus_restore()` ran *after* the failure code was published, so the last GPIO address (GPIO125 v2) overwrote it | Publish the terminal state after restoration; clear `ft_diag_enabled` at every termination point |
| `0x10B` | The outer `ft_fail(11)` overwrote the inner soft-reset step code, so the coarse code always won | `ft_fail()` is now first-failure-wins |
| `0x10D` | The readback equality check described above | Readback is telemetry, matching the vendor |
| — | A bare `D=3` covered twelve distinct failures | Every identification step now publishes `0x100|N`, steps 13..18 cover the soft-reset sequence |

Because the panel `D` field is the only feedback channel on this device, the
diagnostics had to be trustworthy before the touch logic could be judged. That
ordering is what made the final result possible: the first two fixes produced
the precise `0x10D` that pointed at the vendor divergence.

## Source corrections (2026-11-10, host checked; superseded by the working build above)

Candidate `.work/candidates/fajita-touch-irq-mask-report-drop-hostchecked/boot-fajita-touch-panel.img`,
12,840,960 bytes, SHA-256 `493571d157f77dbc6470f850c6bfa2d20d5a7af1f555f85f84d00753eb5fa694`.
Host suite passes; the compressed-FV loader matches the XNUP loader and the
FDT/flashboot/root APFS/kernel payloads verify. The kernel is a fresh build
with only the documented post-link `check_all_exports` failure remaining.
**It has not been booted on the phone; no partition was written.**

Two source-level divergences from the pinned vendor driver were corrected.

**Interrupt-mask lifecycle.** `ft_configure_scan()` used to be the only place
that wrote F01 control1, and it never verified the controller kept the value.
`ft_vendor_soft_reset()` additionally cleared the mask to `0x00` before the
reset and never restored it, so a failed or partial re-arm left the driver
polling an attention line the controller was told not to assert. The writes
are now owned by one `ft_interrupt_mask(bool)` helper, matching the vendor's
`synaptics_enable_interrupt()`: select page zero, read F01 data1 to clear
latched attention **only** when enabling, and write control1 (`0x7f`/`0x00`).
The readback is sampled into `ft_mask_readback` but does **not** gate success:
the vendor reads the same register and discards the value, and requiring
equality aborted identification on this panel (see the working-build section
above). The mask intentionally stays clear across the 100 ms restart;
`ft_configure_scan()` re-arms it afterwards, which is where the vendor
re-enables interrupts too.

This is a conformance finding against the vendor source. The device-tested
identity probe already recorded F01 descriptors `2B2200060101` and Product ID
`S3706B`, so the *addresses* are right; nothing measured so far shows the mask
itself was the reason no contact was reported. A silently dropped mask write
would now fail the scan configuration and be visible as `F=0000000D` instead
of an indefinite `A=0`.

**Identification step codes.** A bare `D=3` covered twelve distinct failures,
which made a no-contact run indistinguishable from a bus or layout fault. The
`D` field now carries `0x100|N`:

| `D` | Step |
| --- | --- |
| `0x101` | page-zero selection |
| `0x102` / `0x103` | F01 / F12 PDT read |
| `0x104` | descriptor layout rejected |
| `0x105` / `0x106` | Product ID read / not S3706 |
| `0x107` / `0x108` | status read / status rejected |
| `0x109` / `0x10A` | sensor range read / range invalid |
| `0x10B` / `0x10C` | soft reset / scan configuration |
| `0x10D` | mask write readback transfer failed |
| `0x10E` | soft-reset command write |
| `0x10F` | post-reset page select |
| `0x110` | post-reset status read |
| `0x111` / `0x112` | status flash mode / status error state |

`ft_fail()` is first-failure-wins so an outer "this call failed" code never
hides the precise inner step. The panel publishes the terminal state only after
`fajita_bus_restore()`, and diagnostic publication is frozen at every
termination point, because TLMM accesses publish their own phase and address.

**Out-of-range objects.** The decoder rejected an entire frame when any active
object reported `x > max_x` or `y > max_y`. The vendor driver never
range-checks at all, and whole-frame rejection lets one glitched slot hide
every other contact in the same report; eight consecutive such frames stop the
polling thread. Out-of-range objects are now dropped individually: the slot
stays inactive, a new `dropped` counter records them, and the first such frame
writes `fajita-touch: dropped N out-of-range object(s)` to the in-memory log.
Release semantics are unchanged because a dropped object is simply absent, so
a previously published press reports UP. Structural faults — truncated
frames, impossible attention words, invalid parameters — still reject
atomically and leave the output untouched.

**Diagnostic-baseline repair.** `tests/test-fajita-touch-rpmh.py` no longer
failed to compile: `fajita_power_tlmm_layout` is the value carried into the
`fajita_touch_tlmm=` acknowledgement, so the test now asserts it instead of
discarding it. The full `scripts/test-fajita-touch-host.sh` suite passes.

Deliberately unchanged: supply/reset sequencing, SDA/SCL roles, RPMh
transactions, TLMM layout selection, the 64-byte live ABI and the panel field
layout. The `dropped` count is not exposed through the panel because `A`
already spends all 32 bits on attention and the active-slot mask; the ABI is
strictly 64 bytes and extending it would break the paired reader.

## Runtime status (updated 2026-11-03)

### F12 layout and replacement-controller investigation (2026-11-03)

The user confirmed the S3706 software-reset candidate reaches the desktop but touch is still unresponsive. No new P/F/D/N/A/Q readout was supplied. Do not reuse the older event-trace panel values as measurements of that candidate.

There is a specific compatibility question to test before another scan change: the six-byte live F01 descriptor reports Control base `0x00`. The OnePlus vendor path reads the first **four** PDT bytes, whereas the current implementation reads six. A [Linux input patch series for replacement controllers](https://lore.kernel.org/linux-input/20260731-synaptics-rmi4-v9-0-f088c47af6c5@ixit.cz/) includes a four-byte descriptor re-read and a no-register-descriptor F12 fallback. These are useful hypotheses, not identification of this replacement panel. A successful Product ID read does not establish the F12 subregister map or the validity of hardcoded Data15 attention `0x0b`.

`fajita_touch_probe=2` now captures this evidence in one diagnostic run. It reads the same fixed F01/F12 descriptors at both six and four bytes, validates the Product ID through the four-byte F01 prefix, reads F01 status/control/IRQ-mask, then reads F12 Query0. Only when Query0 bit0 advertises register descriptors does it separately read Query4/Query5 and Query7/Query8 packets. Query5/8 captures are capped at nine/five bytes; advertised sizes remain visible. Control23's two-byte prefix and Control28's one-byte report-enable value are read only when their presence bits establish an address. All reads stay below page selector `0xff`. It then restores the GPIO mux and exits before software reset or scan configuration. As in identity mode, the only controller write in the probe is page selection; the established loader power/reset sequence still runs during boot.

The five-row panel uses `F=00000013` for completed capture and `F=00000014` for failure. D=0 on success; failure stages 1..13 mean page selection, six-byte PDT, function validation, four-byte PDT, Product ID/range, F01 readback, Query0, Query5 size, Query5 packet/range, Query8 size, Query8 packet/range, Control23, Control28. D=6 also covers earlier bus-mapping failure; missing fields then remain zero with no validity flags.

The remaining rows are all **16 hex digits**:

| Field | Bytes, left to right |
| --- | --- |
| Q5 | Advertised Query5 length; Query5 bytes 0..6 |
| Q8 | Advertised Query8 length; Query8 bytes 0..4; Query5 bytes 7..8 |
| C1 | Four-byte F01 prefix, then four-byte F12 prefix (query/command/control/data each) |
| C2 | Query0, F01 status/control/IRQ mask, Control23 bytes 0..1, Control28, validity flags |

C2 flags: bit0/1 mean four/six-byte F01/F12 prefixes differ; bit2/3 mean sufficient Control0..31/Data0..15 presence prefixes were captured; bit4/5 mean Control23/28 were read; bit6 means Query0 advertises no descriptors; bit7 means the F01 readback completed. A zero without a validity bit is **unknown**, not a measured zero. This diagnostic intentionally does not enable touch scanning.

`scripts/decode-fajita-touch-layout.py --q5 HEX --q8 HEX --c1 HEX --c2 HEX` decodes the capture and derives the relevant control/data addresses. `scripts/make-fajita-touch-probe-bootargs.py --source devices/fajita/bootargs.txt --output PATH --mode layout` creates its boot arguments. Host mocks exercise packet addresses (not a flat byte array), all transport failures, absence of descriptor support, oversized/truncated queries, no configuration writes, and page-wrap guards. Candidate build/runtime status is recorded separately below.

### Host-checked controller identity probe

Candidate `.work/candidates/fajita-touch-probe-hostchecked/boot-fajita-touch-panel.img`, SHA-256 `e8d43a3ce8e0e4aa11f2c75768a37cd187d5d70fa3c74ed2630ad7412e788426`, passed host tests and packaged-source verification. It was temporarily started with `fastboot boot` on 2026-11-02 (`Sending` and `Booting` returned `OKAY`); the user reported Product ID `S3706B`, F01 descriptor `2B2200060101`, F12 descriptor `4A0013080112`, and F01 status `0`. No partition was written. The candidate bootargs file in the same directory appends `fajita_touch_probe=1` to the current device arguments.

The probe uses only the established QUP12/I2C path and address `0x20`. It selects RMI page zero, reads the F01/F12 six-byte descriptors, reads the ten-byte Product ID from F01 Query base + 11 only when the complete range stays below the `0xff` page selector (Query base at most 234), and reads the F01 status. It captures raw F12 bytes even if its function number differs. It then restores the GPIO mux and terminates before `ft_configure_scan()`. The probe thread never writes reset, scan, interrupt, firmware, or vendor configuration registers and never probes alternate bus addresses or protocols. The RMI page-select write (`0xff = 0`) is required to address page zero, so “read-only” here means no controller configuration writes. **The full boot still runs the existing loader RPMh/LDO and GPIO88/99 supply/reset sequence before XNU, so this candidate does have board-level power/reset side effects.**

The dedicated panel keeps the result visible without the suppressed UEFI log tail:

- `F=00000011` means probe reads completed; `D` is the raw F01 status byte. `F=00000012` means a read/validation stage failed; `D` is the stage code (1 page select, 2 PDT read, 3 invalid F01 descriptor/address, 4 Product ID read, 5 status read, 6 GPIO mapping).
- `I1` and `I2` together contain all ten Product ID bytes as hex, in byte order. Decode the hex pairs as ASCII to get the controller string. For example, `53 33 37 30 36 42` spells `S3706B`.
- `F1` and `F2` are the six raw descriptor bytes for F01 and F12, respectively.

The reported descriptors decode to F01 Query/Control/Data `0x2b/0x00/0x06` and F12 Query/Control/Data `0x4a/0x13/0x08`. These are the six-byte read results; they do not independently validate the register map. This identifies the controller/firmware family, not the replacement display module's vendor or tuning. F01 status `0` is normal. The EDL overlay selects the OEM 1080x2340 path, for which OnePlus's Linux driver names `fw_synaptics_18801.img`. Its payload is locally available: `.work/official-fajita-fw/fw_synaptics_18801.img`, extracted from `/etc/firmware/tp/fw_synaptics_18801.img` in official vendor image `segment_4_d2cc4000.img`, 103,920 bytes, SHA-256 `71ab494348d127610a851303b721517fb6b2e9e82bf9172f5412eb1827236293`. The version-0x10 container's GENERAL_INFORMATION carries build ID 2852315 (`0x2b85db`) and raw product text `S3706B`; its CORE_CONFIG begins with the 32-byte ID `ED01701H` plus 24 NULs. The live configuration/build IDs remain unread, so matching the product string does not establish compatible tuning. No firmware programming is implemented or requested.

Host tests cover the exact page-select/read sequence, ensure there are no configuration writes, validate the F01 address guard, exercise ID packing and renderer layout, and verify the startup bootarg.

### S3706 initialization candidate

`.work/candidates/fajita-touch-s3706-reset-hostchecked/boot-fajita-touch-panel.img`, SHA-256 `f3473539910804b9fc0d541df39b2e55423f6b5c4127160a00bd3a7e31c6924b`, adds the OnePlus driver's explicit F01 interrupt-mask disable, software reset command `0x01`, 100 ms wait, and bounded post-reset status/CRC check before scan setup. It does not write firmware. The full host suite and packaged loader/kernel verification pass. On 2026-11-02, `fastboot boot` returned Sending/Booting `OKAY`; the user subsequently confirmed the desktop appeared but touch remained unresponsive. No new panel values were supplied. No partition was written. As with all current boot candidates, the loader still performs the established board power/reset sequence before XNU.

### Current candidate: controller-to-HID event trace

`.work/candidates/fajita-touch-event-path-hostchecked/boot-fajita-touch-panel.img`, SHA-256 `05bb83c83943978177b454861e9ed160375a352d92f183a38135463ca3433cc0`, passed the complete host suite, staged-source checks, live ABI verification and packaged FV/XNUP identity check. It adds `A` (raw attention and decoded active-slot mask), `Q` (events posted to `/dev/redwood-touch`) and dipper's existing `touch_lat=1` markers. The user confirms the desktop appeared but touch remains unresponsive, and reports `A=00000000 Q=00000000`, `P=00000001 F=00000009 D=0357d004 N=00000067 E=00000143000b8300 R=0001000000000000`. Thus the firmware power callbacks completed, 103 reports decoded, I2C register `0x0b` reads succeeded with both lines high, and RPMh completed; no active contact was reported and no kernel event was posted. Absence of `tl: p` is expected because panel mode suppresses the UEFI log tail; `tl: i` is not expected when Q=0. The OnePlus report decoder audit found no attention/status/coordinate mismatch, so the current boundary is before `/dev/redwood-touch`. Runtime record: `.work/candidates/fajita-touch-event-path-hostchecked/runtime-attempt-01.json`. No partition was written.

### Current loader correction: installed ChipInfo GUID

Candidate `.work/candidates/fajita-touch-chipinfo-guid-hostchecked/boot-fajita-touch-chipinfo-guid.img`, SHA-256 `76154ec9baf09869542a5f68b6a3dc9e66af0400cc1dfe64289ec838bb3bcc8f`, size 12,834,816 bytes: **temporary fastboot boot reached the iOS desktop**. The kernel is byte-identical to the desktop-tested TLMM candidate.

The user supplied `P=00000001 F=00000009 D=0357d004 N=00000252 E=00000704000b8300 R=0001000000000000` for the older four-row panel. This records acknowledged firmware preparation, repeated GPIO125 sampling, 594 successfully decoded report polls, and a successful read of F12 object-attention register `0x0b` with both bus lines high. Empty attention frames count in N. The current diagnostic panel additionally exposes `A` (raw attention plus decoded active-slot mask) and `Q` (events posted to `/dev/redwood-touch`); this older record has no A/Q values. The user has not yet explicitly stated whether fingers operate the UI, so touch response remains unconfirmed. Full record: `.work/candidates/fajita-touch-chipinfo-guid-hostchecked/runtime-attempt-01.json`.

The OnePlus S3706 vendor path uses the same object-attention register, eight-byte object records, status low bits, coordinate byte order, and 1080x2340 orientation currently used by this driver. After `ft_publish`, events enter XNU `/dev/redwood-touch`; dipper's injected `backboardd` `libredwood.dylib` must read them and dispatch virtual HID events. No runtime evidence yet confirms either nonempty contacts or a running user-space consumer.

The latter reported P80080000/F10/D6 and zero N/E/R: preparation was rejected before any touch GPIO, DB or RSC access, so no I2C success or physical v1/v2 selection was established. The stale header macro requests 61224FBE..., whereas the actual BSP DEC symbol, built FV index and SDM845 ChipInfo driver install B0760469-970C-487A-A4B5-28DB7B45CEF1. Binary analysis confirms revision0x10002 and the existing prefix offsets8/24. The loader now uses the installed GUID. Tests derive their expected GUID independently from the DEC and check the binary/FV index, rather than agreeing with the same stale header macro.

Failure P values now retain the gate plus reason: 80080001 no LocateProtocol method; 02 Locate error; 03 NULL interface; 04 unsupported revision; 05 missing query method; 06 ID-call error; 07 version-call error; 08 unknown ID/version. Success clears the diagnostic before board preparation. Unknown metadata still skips all touch MMIO; no raw-address or assumed-v2 fallback is added. Runtime P80080000 from the older image does not independently identify its exact failed query branch.

### Current source correction: silicon-specific TLMM layout

Host-checked candidate: `.work/candidates/fajita-touch-tlmm-layout-hostchecked/boot-fajita-touch-tlmm-layout.img`, 12,828,672 bytes, SHA-256 `5df2be03fa7330426704fd7733201b09655f41a38d6fdbb0d5039956a4bfaa7e`. Temporary `fastboot boot` attempt 01 returned Sending/Booting OKAY after authorized device detection. **User confirms desktop, but no touch response**. Panel P=80080000 F=10 D=6, N/E/R zero: the ChipInfo/layout gate rejected before touch MMIO; the silicon-specific GPIO path was not exercised. See the candidate runtime record. The complete host suite, compressed FV/XNUP loader match and compiled live ABI passed; known full-XNU export failures remain, with the same 126 normalized diagnostic identifiers.

The old audit lost the TLMM compatible string and used the v1 pinctrl
source for every appended base. This was incorrect: EDL bases 0/1/2 carry
versions 2.1/1.0/2.0 with compatibles v2/v1/v2. At the pinned OnePlus
source, GPIO88 is SOUTH `0x03d58000` on v1 but EAST `0x03558000` on v2;
GPIO125 is NORTH `0x0397d000` on v1 but EAST `0x0357d000` on v2.
GPIO49/50/99 remain NORTH on both. The packaged compatibility DT is v2.1,
which also contradicted the previous universal-v1 constants. Neither this
packaged DT nor the full multi-revision EDL bundle measures this phone's
actual revision, so neither is used as a runtime default.

The loader now queries the read-only EFIChipInfo protocol before touch
MMIO. Only chip ID 321 with version 0x10000, 0x20000 or 0x20001 is accepted.
Missing/failed metadata or unknown silicon returns P=80080000 without
accessing touch GPIO, command database or RSC. The selected layout drives
both GPIO88 and GPIO125. The final firmware-produced command line carries
`fajita_touch_prepared=1 fajita_touch_tlmm=1/2` only on successful board
preparation; otherwise both final values are zero. The kernel validates
preparation first and then layout, with SKIPPED/detail7 for invalid layout.
Legacy inherited probes cannot override the new loader's final zero gate.

A second definite bug was the GPIO output verification: input sample bit0
was treated as synchronous with output latch bit1. The code now preserves
unrelated CTL/IO bits and checks the output latch independently. The mock
keeps input and output separate, and tests both layouts and failed writes.
These are source corrections, not proof of the exact physical exception or
working touch. No further device test is performed during this review.

### Previous implementation: synchronous command completion

Previous candidate: `.work/candidates/fajita-touch-command-poll-guarded-hostchecked/boot-fajita-touch-command-poll-guarded.img`.
SHA-256: `67ac41d2579b8e0a2bbe8db99744b5e17fb504504214f0664911d36d900e8b9a`.
Host tests, patch replay, final source checks and compressed-FV/XNUP loader
matching pass. It was then **device tested and failed**: fastboot Sending/Booting returned OKAY, but the user reported 9008 and last text `fajita-power: GPT100a 1` (possibly the pre-write GPIO88 print; exact glyphs unconfirmed). See its `runtime-attempt-01.json`. Its kernel remains
byte-identical to the device-tested three-row panel image; USB hardware,
partitions and the touch power-acknowledgement gate are unchanged.

The synchronous request previously masked the selected RSC IRQ and then
required IRQ_STATUS before checking any command result. The local vendor
driver uses enabled interrupts and an IRQ handler; it does not establish
IRQ_STATUS behavior under this loader's masked-IRQ polling policy. Firmware
HAL inspection also does not establish hardware gating. Therefore an IRQ
gating explanation for the reported hold remains a hypothesis.

The new helper removes IRQ_STATUS as a mandatory completion prerequisite.
Every poll checks all three CMD_STATUS values for ISSUED|COMPLETE, then
requires an idle engine before cleanup. Acceptance also requires evidence
of this request's progress: a non-complete command/busy engine was observed,
or the previously absent selected IRQ became pending. If all commands
finish while the engine is still busy, it continues within the existing
bounded wait. If only retained OK/idle state is visible and no progress or
new IRQ is observed, it holds with reason `08` instead of cleaning up.
This conservative rule can reject a real instantaneous completion when
neither progress nor a new IRQ is observable. IRQ_ENABLE policy,
pretrigger guards, trigger ordering, rollback rules and posttrigger FATAL
behavior are retained. U-Boot's synchronous implementation at commit
`5942ad0ddb54` likewise polls command status instead of IRQ_STATUS; its
first-command check is not copied as a three-command success criterion.

The C mock advances hardware from an independent delay clock, not a read
of IRQ_STATUS. The initial polling draft was rejected in review because
its mock synchronously cleared old flags at trigger submission. Tests now
separate submission from potentially delayed hardware acceptance, and
cover absent/early/late IRQs, partial completion, delayed/busy engines,
timeout boundaries and old flags that stay visible. Every cleanup write
independently checks three completed commands and an idle engine. The loader harness separately models
suppressed IRQ versus missing command completion/busy engine. The former
must succeed and the latter retain FATAL HOLD. A mock's trigger-clear
contract is not a measurement of the physical device.

Reference source:
`https://github.com/u-boot/u-boot/blob/5942ad0ddb54/drivers/soc/qcom/rpmh-rsc.c`

### Latest report: power-line hold, image identity pending

The holder subsequently reports a stationary last line matching
`fajita-power: RPMh ldoa28 ...`. The exact image and presence of a red bottom
band have been requested but are not yet known. This line is printed before
TPL31/RPMh transaction access. It does not by itself identify the exact
instruction, a timeout, or a fatal hold; the transcribed numeric glyphs are
not treated as verified requests.

A separate, confirmed diagnostic gap is fixed: a fatal transaction holds
before CPU_ON, so the normal resident refresh loop cannot display `R`.
`fp_hold()` now draws the validated memory-only panel once, after its red
band and before WFE, with a NULL live pointer and no firmware/USB service.
Host integration and the actual panel reader pass. The new image is
`.work/candidates/fajita-touch-held-panel-hostchecked/boot-fajita-touch-held-panel.img`,
SHA-256 `ee2e6fc81a5af760c9ce746e2807b72442fe4caf5fee03864e09ad2077d8b912`.
It is not device tested and was built after the report; it cannot be the
image responsible for that preceding observation.

### Current candidate: completed ACTIVE AMC state compatibility

`boot-fajita-touch-idle-amc.img` is archived under
`.work/candidates/fajita-touch-idle-amc-hostchecked/`, size 12,832,768 bytes,
SHA-256 `a68a8f1ebdfc868d5c5574f8fd7939473eb68420946bcf8d0ccb695163bcc972`.
The full host suite, final staged-source checks, compressed FV/XNUP loader
match and production-delta replay pass. It has **not been device tested**.
The kernel matches the device-tested three-row panel image byte-for-byte;
this candidate changes loader transaction handling and its small panel.
The known full XNU `all` export-check failure remains.

The device-tested three-row panel established the first runtime boundary:
`P=80070003 F=00000010 D=00000006 N=0 E=0`. Firmware returned RPMh SKIP
at AVDD preparation, and the kernel deliberately did not schedule touch.
This excludes Product ID/report parsing as the immediate cause of this
attempt's no-touch result. It does not identify which RPMh pretrigger check
or rollback produced SKIP.

A concrete source mismatch was found in the pinned vendor mailbox driver.
ACTIVE completion clears CMD_ENABLE/WAIT and its IRQ, but retains CONTROL;
the next trigger clears TRIGGER then ENABLE before reuse. The old helper
required CONTROL=0, excluding these completed idle states. The new helper
accepts only CONTROL=0, ENABLE, or ENABLE|TRIGGER, while keeping STATUS idle,
zero CMD_ENABLE/WAIT, no pending selected IRQ, both-slot resource-conflict
checks and the final recheck. TRIGGER-only and unknown bits remain rejected.
After masking the selected IRQ it clears historical TRIGGER then ENABLE
before writing any command. Cleanup restores the saved AMC mode with
TRIGGER clear: it must never replay an old trigger edge. Uncertain state
or any failure after a new trigger remains a fatal hold.

The host suite exercises all three completed-idle encodings, both slots,
per-write failures, rollback, fatal preservation and unchanged cross-slot
guards. It also checks the loader's packed diagnostic rather than only the
header API. These checks do not prove that the observed phone's SKIP was
caused by CONTROL; no register snapshot was available in the tested image.

The current panel has five rows, 390x132 pixels, retaining the 80-pixel bottom
gap. `A` shows raw F12 attention in the low 16 bits and decoded active slots in
the next 16 bits; `Q` is the low 32 bits of events posted to
`/dev/redwood-touch`. `R` adds a 16-digit hexadecimal RPMh diagnostic: reason `[63:56]`,
slot `[55:48]` (`FF` means none), DRV-relative offset `[47:32]`, observed
value `[31:0]`. Reason `00` is success/no failure; `01` invalid arguments;
`02` initial resource rejection; `03` no eligible slot; `04` final recheck;
`05` pretrigger failure successfully rolled back; `06` posttrigger failure;
`07` unsafe restore; the current guarded poller adds `08` when completion
could not be attributed to this trigger. With reason `03`, the low/high halves of value contain
TCS0/TCS1 rejection flags: status `1`, control `2`, command enable `4`,
wait `8`, IRQ `10` (all hexadecimal). This is diagnostic only and cannot
bypass `fajita_touch_prepared`.

### Device-tested candidate: persistent status panel and bounded CRC recovery

The last device-tested RPMh image reaches the desktop but touch does not
respond. No FT/power message was observed. Desktop success alone does not
establish that firmware prepared power, that the input thread ran, or that
I2C acknowledged a transaction. Do not change pin roles or repeat supply
experiments on that assumption.

The older `boot-fajita-touch-panel.img` is archived at
`.work/candidates/fajita-touch-panel-hostchecked/`, SHA-256
`67ec39dffff4ba68df06eeeb9002e2d854dff03435a27318a81e1201657c33f8`.
It is **host checked and dispatched by authorized `fastboot boot`; device
observation confirms desktop but no touch**. With `fajita_touch_panel=1`, the
resident loader paints only a 390x132 rectangle at the lower left, 80 pixels
above the bottom, every two seconds. The former full-screen log exposure is
suppressed. The panel uses validated existing framebuffer memory, supports
only GOP RGB/BGR 32-bit formats, and adds no firmware/USB protocol call.
The holder read all three rows on the physical screen after startup;
this confirms visibility for this test, not compositor synchronization.

All fields are hexadecimal:

| Field | Meaning |
| --- | --- |
| `P` | Firmware power result: `00000001` prepared; `00000000` not requested. `8001xxxx` context, `8002xxxx` timer, `8003xxxx` DB range, `8004xxxx` DB/resource, `8005xxxx` RSC geometry, `8006xxxx` GPIO/sequence failure, `8007xxxx` RPMh skip. Fatal uncertainty holds before XNU and paints the existing red bottom band. |
| `F` | Kernel phase: `02` deferred, `03/04` thread creation/entry, `06..0A` mapping/TLMM, `0B` page zero, `0C` PDT/identify, `0D` configuration, `0E` scanning, `0F` stopped, `10` skipped. `FFFFFFFF` means the validated shared state is unavailable. Values occupy eight digits. |
| `D` | Phase detail: GPIO physical address, delay, or stopped/skipped reason. |
| `N` | Successfully decoded report polls; includes empty frames, so a rising count alone does not prove a finger event reached iOS. |
| `A` | Low 16 bits are raw F12 attention; bits 16-31 are the decoded active-slot mask. `A=0` means no active contact was decoded. |
| `Q` | Number of DOWN/MOVE/UP events posted by the kernel to `/dev/redwood-touch`. `Q>0` proves kernel publication, not user-space HID delivery. |
| `E` | Last transport sample: attempt `[63:32]`, register `[23:16]`, read flag `[15]`, unavailable line sample `[10]`, physical SDA/SCL `[9:8]`, error `[7:0]`. Line levels are valid only with bit 10 clear. |

Transport error codes: `00` success, `01` invalid argument, `02` SDA/bus busy,
`03` SCL timeout, `04` write-address NACK, `05` register NACK, `06` data NACK,
`07` read-address NACK, `08` STOP failure. The existing 64-byte live object
retains its first 24 bytes; optional transport/report fields require a new
marker. These are independent atomic samples, not one coherent snapshot.

Actual driver code now waits at most 20 reads / 400 ms for runtime F01 CRC
status 6, rejects flash mode, and handles unconfigured/reset recovery before
the generic status rejection, following the pinned vendor ordering. This
fixes a demonstrated logic gap; no evidence yet shows this device hit it.
GPIO roles, voltage, reset sequence and boot-core selection are unchanged
from the last RPMh candidate.

The host suite includes the production renderer/reader, legal high-VA modular
translation, old ABI/alignment/range rejection, canary/padding preservation,
exact per-row cache bounds, staging idempotence and 41 malformed-loader
negative cases, plus actual CRC/poll functions and transport error telemetry.
The kernel linked and its compiled live ABI was checked; the known full XNU
`all` export checks still fail. The host build performed no device operation. The holder subsequently
confirmed manufacturer fastboot and approved temporary startup; Sending and
Booting both returned OKAY. No partition was flashed. The desktop/touch
result is desktop with no touch. The panel reads
`P=80070003 F=00000010 D=00000006 N=00000000 E=0000000000000000`.
This confirms RPMh SKIP at the AVDD callback and a kernel prepared-gate
skip. No I2C transaction ran; E=0 is not an acknowledgement. The existing
SKIP result does not distinguish initial rejection from a successful
pretrigger rollback. There was no trigger on a SKIP path.

### Previous candidate: pre-handoff power preparation

`loader/fajita_touch_rpmh.inc` now implements board power/reset preparation
before XNU CPU_ON. This is a **host-checked candidate, not a device-verified
touch fix**. The touch-disabled desktop control remains the runtime baseline.
No Product ID or valid touch report has yet been observed; previous resets
are still unexplained.

The backend reads the DT command-database dictionary at `0x0c3f000c`, checks
its returned address/length against the reserved carveout and parses the
bounded `ldoa28` VRM entry. It does not copy dipper's table address or infer
a physical PMIC LDO number from the logical rail name. In the pinned vendor
binding ACTIVE_TCS=2; the ordered DT pairs `<2 2>, <0 3>, <1 3>, <3 1>` assign
active slots **0..1**. The request is 3008 mV, enable=1, pmic4-LDO HPM=7,
followed by the vendor's AVDD/reset/pin sequencing and 10/10/10/10/100 ms waits.

The firmware core runs the synchronous transaction before XNU starts, at
TPL_HIGH_LEVEL with IRQ/FIQ masked. Busy slots skip touch. Completed requests
restore the borrowed command fields and interrupt-enable state. An uncertain
transaction or failed rollback **holds before handoff** rather than reusing
a potentially in-flight slot. The held path writes its report to memory and
paints a small red band at the bottom of the existing framebuffer without
calling a UEFI console/protocol or restoring TPL. Host review caught and fixed
an error in the initial backend: printing through GraphicsConsole at TPL31
can attempt an invalid RaiseTPL(TPL_NOTIFY). This was found before device
testing; it is not a claimed cause of earlier images' failures.

A second review found a concrete concurrency gap: a free TCS did not exclude
another active TCS already updating the same resource. The helper now checks
both slots' enabled commands, including all 16 possible command positions,
before selection and before its first mutation. An overlapping voltage,
enable or mode address, unknown busy state, or unsupported mask skips the
request without changing the RSC. Tests cover 96 slot/command/register conflict
combinations and the full loader path. This excludes that detected case; it
does not prove ownership against every firmware execution context.

The loader appends `fajita_touch_prepared=1/0` after payload arguments. With
`fajita_touch_power=1`, the kernel schedules touch only after ready=1;
`fajita_touch_inherit=1` cannot bypass a failed acknowledgement. An invalid or
stopped architectural timer is rejected before touch MMIO. GPIO/I2C stays
in the delayed XNU input thread; kernel RPMh and USB access are not added.

`scripts/test-fajita-touch-host.sh` exercises the actual production parser,
transaction, power sequence, GPIO/I2C, startup, final acknowledgement and
diagnostic readers with host mocks and ASan/UBSan. DT auditing pins the
mailbox/regulator/cmd-db source definitions and validates the new resources
across all three base DTBs. Tests cover completion errors, rollback, both
active slots, no-console fatal hold, DAIF/TPL restoration, malformed databases,
power timing order, duplicate ready arguments and command-line length bounds.
They do not establish electrical SDA/SCL roles, firmware TCS ownership in all
runtime contexts, an actual supply voltage or working touch. The existing
full XNU `all` export-check failure remains.

The new image is `boot-fajita-touch-rpmh.img`, SHA-256
`a0c4a78d4db47244412207c0f13b432d13c44f54e631df34f990ee972bd15d03`.
Loader/UEFI compilation and final compressed-FV/XNUP matching pass. The
candidate enables `fajita_touch_power=1`, retains independent diagnostics and
30-second deferred startup. Archive:
`.work/candidates/fajita-touch-rpmh-hostchecked/`. The host build performed no device operation. The candidate was subsequently
dispatched with authorized `fastboot boot`; Sending and Booting returned OKAY.
The user confirms the iOS desktop but no touch response, and did not see FT or
power messages. This does not prove successful power preparation or I2C.
`runtime-attempt-01.json` records the command result. Boot/recovery, GPT and
APFS were not flashed or modified.

### Earlier device observations

The user reports that `boot-fajita-ufs-cache-scope.img` reaches the iOS
desktop, with no response to touch, and subsequently confirms the quiet
desktop image works normally. Those baselines contain no fajita Synaptics
driver. Both complete baselines are archived under `.work/candidates/`.

`boot-fajita-touch.img` failed on the phone and returned to 9008. Its last
visible line was `handoff: secondary core reached the entry stub`. The loader
turns screen output off immediately afterward, so that marker does not locate
the later fault. The exact image and sources are archived in
`.work/candidates/fajita-touch-v1-failed/`.

`boot-fajita-touch-vreg.img` also failed on the phone. Fastboot transfer and
Booting returned OKAY; the user confirms 9008 after `handoff: UEFI core 0`.
The next added path was Vreg setup, but its exact failing method is unknown.
Its archived runtime record in `.work/candidates/fajita-touch-vreg-hostchecked/`
now says failed. Binary audit confirms the ABI but also real SPMI/PMIC writes;
error-return handling cannot recover a fault or reset inside firmware.

The tested `boot-fajita-touch-inherit.img` removed all loader Vreg hooks.
Loader source and binary match the tested quiet desktop baseline byte-for-byte.
The kernel requires `fajita_touch=1 fajita_touch_inherit=1`, probes existing
power, and never maps supply/reset GPIOs. It was dispatched once using
`fastboot boot` with send/Booting OKAY; **the user confirms 9008 after the
secondary-entry marker**. Touch-thread execution has not been observed.
Existing power may be insufficient for any controller response. This is
not a purely read-only driver: page selection and Product-ID-gated normal
scan configuration still use I2C writes. Unrelated ST/Goodix drivers remain off.

The new `boot-fajita-touch-observe.img` is a diagnostic candidate, not a fix
claim. Its thread waits 30 seconds before any touch MMIO, then repeats a
whole-line marker for four seconds before each access boundary. With exact
`fajita_touch_diag=1`, the loader temporarily exposes the existing two-second
kernel log-tail samples for up to 120 seconds; ordinary quiet mode is restored
after each sample. This uses the existing log channel, no new shared ABI,
USB service or PMIC call. Current kernel/control changes can still fail before
the touch thread; the delayed sequence is intended to distinguish that case.

Five driver-reported fingers held for 1500 ms previously invoked GPU shutdown
and `loader_return()`. Fajita now requires explicit `fajita_touch_exit=1` before
enabling that exit gesture. Spurious reports could otherwise confound bring-up;
there is no evidence that they caused the previous resets.

The diagnostic image was dispatched using `fastboot boot` (send and Booting
OKAY). The user reports a logo and periodic log samples, followed by 9008 in
less than 30 seconds, with no observed `fajita-touch-diag:` line. This is before
the configured 30-second touch-MMIO delay plus its first diagnostic boundary;
it does not identify a GPIO/I2C failure or prove that the touch thread started.
SHA-256:
`6870e6b494e9c53cdef5cfd86e7ed940a9b71af6513ca0f8c8da22d93d8ba8c2`.
Exact source, kernel, loader, payload and boot attempt are saved under
`.work/candidates/fajita-touch-observe-hostchecked/`. Host source checks,
diagnostic loader patch replay, hook idempotency and final compressed-FV/XNUP
matching passed. The known XNU post-link export-check limitation remains.

A control image under `.work/candidates/fajita-touch-control-hostchecked/`
keeps that exact kernel, loader, firmware prefix and APFS payloads, changing
only `fajita_touch=1` to `fajita_touch=0`. Its SHA-256 is
`9ee73d1ba031695cd5583444612e1c2eb13609bc0ccde57ab745f988d0bfc8ab`.
After send/Booting OKAY, the user confirms an iOS desktop stable for at least
one minute, with touch still unavailable as expected for `fajita_touch=0`.
The static SYSCTL registrations and diagnostic loader remain present in this
successful control. The regression therefore depends on a touch-enabled path
(including the exit-gesture gate, its logging, startup and the worker), rather
than being established as a general new-kernel or loader failure.

The next candidate `boot-fajita-touch-deferred.img` changes actual startup code:
the BSD/devfs hook only arms a `thread_call` deadline; the input thread is
created 30 seconds later. Early markers and the exit-gesture gate use literal
`loader_log_line()` messages; the touch logger no longer duplicates messages
to `printf`. GPIO/I2C probing still runs in the dedicated input thread, not in
the thread-call callback. This is a scoped early-startup change, not a proven
fix for the reset or for controller access. The delay is relative to arming
from BSD/devfs, not to the host's fastboot command. A newly started thread can
run before the callback's "creation returned successfully" message.

Exact archive: `.work/candidates/fajita-touch-deferred-hostchecked/`.
Image SHA-256:
`283905596830941f41bcddbc23a466ed85d0224b6185251f9c9bdc92660bd710`.
Only the kernel payload entry changed relative to the failed observe image;
loader, firmware prefix, APFS, FDT and the touch-enabled boot arguments remain
identical. Deferred-startup mocks and the report decoder pass strict host C
checks with ASan/UBSan (LSan disabled because this host traces processes).
Source staging is idempotent and compressed-FV/XNUP matching passes. The known
full XNU all/export checks still fail; compiler/linker error scanning found no
new errors and the 126 exported symbol/method names match the observe log.
This candidate was dispatched by `fastboot boot` with send/Booting OKAY;
the user confirms another return to 9008. Its last visible text was
`name=com.apple.mobileactivationd, flags = 0x8, requestor = lsd[29], error = 3:No such process`.
The time to reset for this image was not reported. The text is not a panic
backtrace and does not identify a touch fault. No partition was flashed.

### Confirmed diagnostic defect

The resident loader's old `note_kernel_progress()` always sampled physical
`log_len - 240 .. log_len`, ignoring `log_ring_at` and `log_wrapped`. After
the writer fills the ring, `log_len` freezes near the allocation end while
new text wraps elsewhere. The display can therefore repeat old process logs;
the reported activationd/lsd text may not be the most recent log. This defect
is reproduced by `tests/test-fajita-log-tail.c`; it is not a proven reset cause.

The reader now takes a checked metadata snapshot and samples up to two
chronological spans, at most 240 bytes. Inconsistent metadata is reported as
unavailable rather than falling back to a stale physical tail. A separate
64-byte touch status object publishes one atomic `phase<<32 | detail` value,
with its own cache line written back explicitly. The loader reads this through
an append-only live extension: the original 96-byte ABI stays unchanged;
magic3/size/pointer extend it to 120 bytes. The reader checks the extension,
object alignment and loaded-kernel ranges before accessing it. `FT=...` is
printed after the general log sample, so unrelated user-space text cannot
hide the latest published touch stage. Exact snapshots still cannot catch
every instruction immediately before an external reset.

No touchscreen power, reset, GPIO address or USB policy changed in this
diagnostic correction. It improves fault localization; touch operation and
the cause of 9008 are still unverified.

The corrected diagnostic image is archived as
`.work/candidates/fajita-touch-live-hostchecked/boot-fajita-touch-live.img`,
SHA-256 `fa150948589ecc40c84db50a2268767dba8b2c7612c9bb273e13163a24d8c17b`.
The adapter build and final FV/XNUP checks pass. Full XNU all/export checks
retain the known failure, with the same 126 reported symbol/method names.
This image has **not been booted on the phone**; no device access occurred
in this diagnostic-correction round. Stages of interest are `FT=2` deferred,
`FT=3/4` thread creation/entry, `FT=5/6` mapping, `FT=7/8` snapshot/pinmux,
`FT=9/10` TLMM read/write, and `FT=15/16` stopped/skipped. For read/write,
`data` is the physical register address; other stages use delay/error/context.

The prepared dipper kernel's `bsd/dev/mi8_fts.c` is an ST FTS driver for
I2C address `0x49`, serial engine 14, and Mi 8 GPIOs. Its `fts=1`
initialization is gated off by default. `bsd/dev/redwood_goodix.c` instead
targets Goodix/FocalTech SPI hardware on redwood. Neither matches the
following fajita evidence; enabling those boot arguments is not a touch fix.

## Evidence already available locally

| Item | Observed value | Source |
| --- | --- | --- |
| Board match | `oem,fajta`, board ID contains `0x4971` = 18801 | `.work/edl-extract/dtbo/overlay-06.dtbo` through `overlay-12.dtbo` |
| Touch node | `/fragment@70/__overlay__/synaptics-rmi-ts@20` | Same EDL overlays |
| Binding | `HWK,synaptics,s3320`, I2C address `0x20` | Same node; the compatible string is not a measured Product ID |
| Bus | `qupv3_se12_i2c`, `/soc/i2c@a90000`, register span `0x4000` | Overlay fixup and EDL base symbols |
| I2C pins | GPIO49/50, function `qup12`, drive strength 2, bias disabled | Base pinctrl and overlay bus enable |
| Interrupt | TLMM GPIO125, raw flags `0x2008` | Fajita overlay; overrides base generic GPIO122 |
| Reset | TLMM GPIO99, flags 0 | Touch node |
| I/O supply switch | TLMM GPIO88, flags 0 | Touch node |
| Analog supply | `pm8998_l28`; raw `0x2de600` = 3,008,000 uV / 20,000 uA | `vdd_2v8-supply` fixup and touch properties |
| Coordinates | Base properties `1080x2160`, plus `oem,support_1080x2340_tp` | Touch node; OEM height handling still needs a driver audit |

Official vendor image `.work/.official/fajita_sparse/segment_4_d2cc4000.img`
contains `/etc/firmware/tp/fw_synaptics_18801.img` (103,920 bytes), SHA-256
`71ab494348d127610a851303b721517fb6b2e9e82bf9172f5412eb1827236293`.
Its printable identity includes `S3706B` and `2018-11-0112:52:45SYNA`.
At the time of this earlier audit, the implementation target was only a
Synaptics RMI/S3706B candidate. The subsequent identity probe above has now
read `S3706B` on the phone; it has not established the module's tuning.

No additional EDL dump is needed for this driver work. The candidate now
implemented the matching RMI initialization/report decoding, SDM845 GPIO
open-drain I2C, a bounded Product-ID probe without power/reset changes, and
DOWN/MOVE/UP events into `/dev/redwood-touch`. Host checks do not establish
that the controller responds on this phone; the failed run did not provide a
Product-ID or stage log. No touch firmware programming is implemented.

### Device-tree review correction (2026-11-01)

The current driver does **not** consume the Android DT at runtime. It uses
board constants extracted from it, with a separately implemented RMI driver
and XNU input glue. The earlier version was an inherited-power bring-up probe, **not a complete
implementation of the vendor power/reset sequence**. No Product ID or valid
touch frame has yet been observed on this phone.

Re-reading the raw base DTBs and fajita overlays 06..12 identified two
previously missed facts:

- `ts_active/config` specifies pull-up and **16 mA** for GPIO99/125. The
  inherited-power transport used a 2 mA IRQ config. It now writes `0x1c3`
  (input, GPIO function, pull-up, 16 mA) for IRQ125; SDA/SCL remain 2 mA with
  no bias. This is a DT conformance fix, not an explanation of the resets.
- AVDD's raw value is **`0x2de600` = 3,008,000 uV**, not 3,000,000 uV.
  The retired, failed Vreg experiment really requested 3000 mV. Its source
  is retained as a historical experiment and now explicitly labels that
  conversion error. It remains absent from the default loader.

`kernel/fajita_touch_board.h` centralizes the checked address, GPIO, pinctrl,
rail and coordinate values. `scripts/audit-fajita-touch-dt.py` checks them
against the raw local DT inputs and records their hashes. The build runs
this check before staging the touch source, and rejects a kernel older than
the board header. DT consistency alone cannot confirm powered hardware,
SDA/SCL electrical behavior or a live controller identity.

The vendor's sequence is: parse DT and hold reset low; enable AVDD; wait
10 ms; drive GPIO88 high; wait 10 ms; reset high 10 ms, low 10 ms, then high;
select active pinctrl; wait 100 ms; probe register `0x13` with one retry;
read PDT/Product ID. This is in `synaptics_driver_s3320.c` lines 594..638,
5465..5659 and 5781..5867. The S3706 + 2340 property branch overrides the
base 1080x2160 coordinates to 1080x2340. The inherited-power driver did not
control AVDD, GPIO88 or GPIO99; inherited supply did not establish this
sequence. The new loader backend is described at the top of this document;
its runtime behavior and full firmware ownership still require verification.

The dipper `mi8_fts.c` is useful for XNU integration, clock checks, GPIO
bit-banging and bounded probes. Its GPIO23/31/32/33/34, SE14, ST FTS address
0x49 and 3.3 V sequence are Mi 8-specific. Its GPIO transport choice supports
using the same technique here; it does not validate fajita power or pin roles.
The base DT's `pins = "gpio49", "gpio50"` establishes the pair and mux,
not labels for SDA versus SCL. The current GPIO49=SDA/GPIO50=SCL assignment
has not been confirmed by a successful transaction; that distinction is
retained explicitly rather than treating array order as electrical proof.

`tests/test-fajita-touch-transport.py` executes the production transport with
mocked peripheral reads/writes. It checks active pinmux, mapped-page scope,
wire addresses, repeated START, read ACK/final NACK, bounded failure/recovery,
no actively driven-high SDA/SCL, and mux restoration. It does not exercise
the actual controller, MMIO fabric or supply rails. No phone operation is
part of these checks.

## Implementation and host checks

- `kernel/fajita_synaptics.c`: gated initialization, page-zero F01/F12 PDT,
  ten-byte Product ID, rejection of non-S3706 or flash/error state, normal
  scanning configuration, sensor range from F12 Control08, and report polling.
- `kernel/fajita_touch_transport.inc`: three mapped GPIO pages (SDA49, SCL50,
  IRQ125), saved mux/output-latch and restoration on ordinary failure, open-drain I2C at
  address `0x20` with repeated START, ACK/NACK, clock-stretch timeout, STOP and
  bounded recovery. No cmd-db, RPMh/TCS, GENI engine, clock or USB controller
  register is accessed. GPIO88/99 are not mapped; reset and supply are untouched.
- Retired experiment `loader/fajita_touch_power.inc`: before CPU_ON, locate Qualcomm Vreg protocol
  GUID `22d38d3d-e8b6-4f8f-9c26-bceb07d6cb68`, check ABI revision/methods,
  request PMIC0 LDO28 (zero-based ID 27) at 3000 mV, enable, and require both
  software enable and regulator-OK status within 50 bounded 1 ms delays.
  Error returns were intended to skip touch, but did not cover faults inside firmware.
  This uses `EFIPmicVreg.h`; the included SDM845 PmicDxe binary contains the
  GUID and is selected by both the SDM845 DXE and Apriori FDF lists. Presence
  in the image does not prove successful runtime protocol publication.
- `scripts/ensure-fajita-touch-power.py` now reverses the failed experiment's
  exported patch to restore the quiet loader. Repeated default runs are a
  no-op; partial removal is rejected. `--experimental-vreg` retains old staging
  only for host analysis and is not used by the default build.
- `kernel/fajita_touch_report.h`: atomic bounded decoder for at most ten
  eight-byte slots, sparse attention, 12-bit coordinates and normalized
  `0..65535` input coordinates. Missing/zero/error-exit contacts send UP.
- `scripts/ensure-fajita-touch.py`: idempotent staging plus the XNU build-list,
  input-init hook and prototype. `verify-fajita-touch.py` rejects stale staged
  sources and missing hooks. Final packaging also requires the new kernel to
  be newer than every touch input and to contain `_fajita_touch_init`.
- `tests/test-fajita-touch-report.c`: golden byte reports, ten/sparse slots,
  coordinates, release, short frame, bad attention and atomic rejection;
  strict host compilation and ASan/UBSan passed.
- `tests/test-fajita-touch-power.c`: tests the retired loader helper with
  fake firmware, exact opt-in/platform gates, ABI, exact rail/voltage arguments,
  success, missing/bad protocols, method errors, delayed readiness and bounded
  timeout. Strict C11 and ASan/UBSan passed, but did not establish hardware safety.

The polling thread clears status/IRQ, releases old contacts on reset, and
stops after eight consecutive transfer/report errors. Initialization failures
stop the input thread without requesting a system reset. Read-only sysctls
`kern.fajita_touch_ready`, `kern.fajita_touch_error` and
`kern.fajita_touch_reports` retain status for a future userspace connection.
The implementation contains no F34 erase/program/update path.

## First touch regression and remaining uncertainty

The removed kernel power path read `0x85fe0000`, copied from dipper without
fajita confirmation. Fajita's DT declares the cmd-db resource as
`reg=<0x0c3f000c 0x8>`, not that table address. Checking a magic after a read
does not prevent a fault on the first access. The DRV2/TCS address itself
does have local DT support, but the code had no ownership handshake with
UEFI, which continues serving UFS from another CPU. Checking an idle slot
was not arbitration. These are verified static defects, not a proven reset
instruction. The first firmware-supply replacement also failed on hardware.
That inherited-power version left supply/reset state alone and mapped three
TLMM pages. The new candidate adds the separate pre-handoff loader backend.

The inherited-power image is 12,810,240 bytes, SHA-256
`fa818bb35ae036a37b1ef5357e799d2087555b927dd98c8bdd2ed045b8def4e2`.
The packaged compressed FV loader and XNUP loader match byte-for-byte;
the kernel is current and exports `_fajita_touch_init`. The known post-link
XNU `check_all_exports` failure remains; this is not a clean full `all` build.
The Vreg and inherited-power images were each booted once using fastboot;
runtime outcomes are distinguished above. No boot flash, GPT or APFS write
was performed in these rounds. Touch remains unverified on hardware.

Firmware ownership was checked against the actual decompressed FV, rather than
the build log (the DSC compiles some components that the FDF does not include).
The FV contains `SynapticsTouchDeviceDxe` GUID ending `a310`, the device
publisher, but not `SynapticsTouchDxe` GUID ending `a309`, whose source starts
a 20 ms I2C polling timer. No extra disconnect is needed for that absent
polling driver. The final boot verifier now rejects its presence to prevent
a future firmware/XNU bus-ownership conflict. The 86-entry FFS inventory is
saved with the host verification evidence.

## Matching official driver reference

The OnePlus official SDM845 Android 10 branch is sparse-cloned at
`ref/oneplus-sdm845-q`, fixed commit
`222822c2d0042067b7964c6302ef4d756dc4bed9`. This is reference source only;
none of it is automatically built into the current XNU candidate.

`drivers/input/touchscreen/synaptics_driver_s3320.c` supplies the matching
implementation: `oem,fajta` selects project version 3 (line 5783); lines
5846-5867 read F01 Query11 Product ID and select `S3706`, firmware
`tp/fw_synaptics_18801.img`, and `1080x2340` coordinates. This explains the
old `s3320` binding name without treating it as a measured chip ID.

Its page-0 register discovery starts at line 685: register `0xff` selects
the page, `0xdd` supplies F12 bases, and `0xe3` supplies F01 bases. For S3706,
Control20 is at F12 control base +6 and Control27 at +9. Touch decoding at
lines 1605-1695 reads the 16-bit object-attention bitmap at `0x0b`, then up
to ten eight-byte slots from F12 data base. Each slot contains status, 12-bit
little-endian X/Y, pressure, and two width values. These are RMI reports,
not ST FTM5 FIFO commands.

`drivers/pinctrl/qcom/pinctrl-sdm845.c` lines 28 and 50-53 define the tile
offsets/register formula; lines 1466-1467, 1538, 1560 and 1609 identify the
following pins. With TLMM base `0x03400000`, their configuration registers are:

| GPIO | Tile | CTL physical address |
| --- | --- | --- |
| 49 (SDA, role unmeasured) | NORTH +`0x00500000` | `0x03931000` (both revisions) |
| 50 (SCL) | NORTH +`0x00500000` | `0x03932000` |
| 88 (1.8 V enable) | v1 SOUTH / v2 EAST | v1 `0x03d58000` / v2 `0x03558000` |
| 99 (reset) | NORTH +`0x00500000` | `0x03963000` |
| 125 (IRQ) | v1 NORTH / v2 EAST | v1 `0x0397d000` / v2 `0x0357d000` |

The IO register is CTL +4. GPIO88 requires the silicon-selected SOUTH/EAST tile; GPIO125 likewise changes tile. These addresses
are source/tree cross-checks; no register access has yet validated them under XNU.

## USB and remote operation

Authorized host `lsusb` enumeration during the reported desktop session
showed no phone/xnuloader device. The successful image has an embedded
one-shot payload without `usb`, and bypasses `usb_start()`. This session
therefore has no active xnuloader host command channel; the host cannot
fetch a screenshot or operate the UI through that channel. Re-enabling it
would require a separate build and runtime check of the existing Qualcomm
USB protocol problem. Qualcomm USB DXE/hardware itself remains enabled.
