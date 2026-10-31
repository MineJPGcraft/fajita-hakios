# fajita UEFI platform

The files under `files/Platform/Oneplus/sdm845/` are the public fajita
platform from `ref/edk2-sdm845`. They provide the SDM845 memory map, ACPI
tables, framebuffer dimensions and the compatibility DTB used by the
provisional port.

The platform is not a complete bootable UEFI image yet. HakiOS's common UEFI
tree must be prepared with `ref/hakios-uefi`, and the vendor DXE drivers must
be extracted from this phone's own XBL through EDL. Those proprietary drivers
stay outside Git; use `EDL_INPUTS.md` and keep them under the local
`files/Binaries/fajita/` directory when the build is ready.

The public fajita DSC sets a 1080x2340 framebuffer and device code name
`fajita`. It is suitable for host-side integration checks only until the
firmware build is run with matching OnePlus 6T XBL drivers.
