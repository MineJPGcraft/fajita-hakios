/* SPDX-License-Identifier: GPL-2.0-only
 * Minimal stable EFIChipInfo protocol prefix, following the local Qualcomm
 * EFIChipInfo.h. The GUID must come from the built BSP's QcomPkg.dec and
 * ChipInfo.efi: this BSP's header macro names a different, absent GUID.
 * This queries identity/version only; no PMIC protocol.
 */
#ifndef FAJITA_CHIPINFO_H
#define FAJITA_CHIPINFO_H

struct fajita_chipinfo_protocol {
	uint64_t revision;
	efi_status (EFIAPI *get_chip_version)(struct fajita_chipinfo_protocol *, uint32_t *);
	void *get_raw_chip_version;
	efi_status (EFIAPI *get_chip_id)(struct fajita_chipinfo_protocol *, uint32_t *);
};
_Static_assert(offsetof(struct fajita_chipinfo_protocol, get_chip_version) == 8,
    "ChipInfo GetChipVersion ABI");
_Static_assert(offsetof(struct fajita_chipinfo_protocol, get_chip_id) == 24,
    "ChipInfo GetChipId ABI");

#endif
