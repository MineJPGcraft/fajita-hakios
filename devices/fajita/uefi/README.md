# fajita UEFI platform

The files under `files/Platform/Oneplus/sdm845/` are the public fajita
platform from `ref/edk2-sdm845`. They provide the SDM845 memory map, ACPI
tables, framebuffer dimensions and the compatibility DTB used by the
provisional port.

The active build uses the prepared `ref/dipper-xnu/work/edk2` tree and its
SDM845 firmware drivers. `scripts/apply-fajita-to-dipper.sh` stages these
files and builds the one-shot image. This is the dipper EDK2 route, not the
separate Mu-Silicium integration from `ref/hakios-uefi`.

The fajita DSC sets a 1080x2340 framebuffer and selects the local memory-map
library, which splits each RAM profile around the reserved 64 MiB XNUP
window at 0xF0000000. `files/XnuLoaderPkg/Driver/` registers a BootOrder
entry for BDS; it must not start the loader during DXE dispatch.

Qualcomm USB drivers stay enabled. The embedded XNUP has `oneshot` and no
`usb`, so the loader does not start or poll its USB transfer service.
The current image has passed host checks, but has not booted on this phone.
