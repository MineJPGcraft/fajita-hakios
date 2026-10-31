# fajita provisional device layer

This is the first HakiOS adaptation pass for the OnePlus 6T (`fajita`). It is
derived from this phone's EDL `boot_a` DTBs (Android 9 build `1812260627`),
with the public `edk2-sdm845` tree retained for UEFI compatibility files.

The EDL base trees confirm the SDM845 SoC arguments. The accompanying DTBO
metadata is Qualcomm MTP reference-board data, so driver claims still require
a matching board overlay and runtime test.

## Current boundary

- UEFI: `ref/edk2-sdm845/Platform/Oneplus/sdm845/fajita.dsc` and
  `fajita.fdf.inc` provide the platform files and a framebuffer size of
  1080x2340.
- XNU: only the device/configuration boundary is defined here. The SDM845
  kernel layer still needs to be imported from the dipper work and audited for
  fajita-specific addresses.
- Drivers: all phone-specific drivers are disabled until the phone's own DTB,
  firmware and runtime logs are available.

## Required next input

Continue with additional EDL partitions only when a specific hardware feature
requires them. Keep private partition dumps under `.work/` and out of Git.
