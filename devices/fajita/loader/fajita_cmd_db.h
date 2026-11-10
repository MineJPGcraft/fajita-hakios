/* SPDX-License-Identifier: GPL-2.0-only
 * Bounded, unaligned-safe reader for the vendor command database layout.
 * Source: OnePlus cmd-db.c at 222822c2d0042067b7964c6302ef4d756dc4bed9.
 * No MMIO, guessed database address or PMIC peripheral ID is used here.
 */
#ifndef FAJITA_CMD_DB_H
#define FAJITA_CMD_DB_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct fajita_cmd_db_resource {
	uint32_t address, priority[2];
	uint16_t version, aux_length;
};
static inline uint16_t fcmd_u16(const volatile uint8_t *p)
{
	return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}
static inline uint32_t fcmd_u32(const volatile uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
	    ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* The dictionary stores {physical start, byte count}. Validate it before
 * dereferencing that start. Its allowed carveout comes from the local DT.
 */
static inline bool
fajita_cmd_db_range(uint32_t pa, uint32_t size, uint32_t low, uint32_t high)
{
	return (pa & 3u) == 0 && pa >= low && pa < high &&
	    size >= 144u && size <= 0x20000u && size <= high - pa;
}

static inline bool
fajita_cmd_db_ldoa28(const volatile void *buffer, size_t size,
    struct fajita_cmd_db_resource *out)
{
	const volatile uint8_t *p = buffer;
	const uint8_t name[8] = {'l', 'd', 'o', 'a', '2', '8', 0, 0};
	struct fajita_cmd_db_resource found = {0};
	bool matched = false;
	if (!p || !out || size < 144u || size > 0x20000u ||
	    fcmd_u32(p + 4) != 0x0c0330dbu) { return false; }
	for (unsigned s = 0; s < 8; s++) {
		const volatile uint8_t *slave = p + 8u + 16u * s;
		uint16_t id = fcmd_u16(slave), offset = fcmd_u16(slave + 2);
		uint16_t data = fcmd_u16(slave + 4), count = fcmd_u16(slave + 6);
		if (!id) { break; }
		if (count > 512u || offset > size - 144u ||
		    (size_t)count * 24u > size - 144u - offset) { return false; }
		for (unsigned i = 0; i < count; i++) {
			const volatile uint8_t *entry = p + 144u + offset + 24u * i;
			bool equal = true;
			for (unsigned c = 0; c < 8; c++) {
				if (entry[c] != name[c]) { equal = false; }
			}
			if (!equal) { continue; }
			uint32_t address = fcmd_u32(entry + 16);
			uint16_t length = fcmd_u16(entry + 20), aux = fcmd_u16(entry + 22);
			if (matched || id != 4u || ((address >> 16) & 7u) != 4u ||
			    !address || (address & 3u) || data > size - 144u ||
			    aux > size - 144u - data || length > size - 144u - data - aux) {
				return false;
			}
			found.address = address;
			found.priority[0] = fcmd_u32(entry + 8);
			found.priority[1] = fcmd_u32(entry + 12);
			found.version = fcmd_u16(slave + 8);
			found.aux_length = length;
			matched = true;
		}
	}
	if (!matched) { return false; }
	*out = found;
	return true;
}
#endif
