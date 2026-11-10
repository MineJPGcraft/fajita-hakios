/* SPDX-License-Identifier: GPL-2.0-only
 * Read-only S3706 packet-layout diagnostic. The only controller write is
 * RMI page selection; no reset, report-enable or scan configuration here.
 * Query5/8 are packet registers, not a linear dump of adjacent queries.
 */
static bool ft_probe_range(unsigned base, unsigned offset, size_t count,
    uint8_t *bytes)
{
	if (!count || base >= 255u || offset >= 255u ||
	    base + offset >= 255u || count > 255u - base - offset) { return false; }
	return fajita_i2c_read((uint8_t)(base + offset), bytes, count);
}

static bool ft_probe_control(const uint8_t query5[9], uint8_t size,
    unsigned number, unsigned *offset)
{
	unsigned byte = 1u + number / 8u;
	if (number >= 64u || byte >= size ||
	    !(query5[byte] & (1u << (number % 8u)))) { return false; }
	*offset = 0;
	for (unsigned n = 0; n < number; n++) {
		if (query5[1u + n / 8u] & (1u << (n % 8u))) { (*offset)++; }
	}
	return true;
}

/* Four cache-line ABI fields, each in screen byte order:
 * Q5: advertised length, query5[0..6].
 * Q8: advertised length, query8[0..4], query5[7..8].
 * C1: four-byte F01 descriptor prefix, four-byte F12 descriptor prefix.
 * C2: Query0, F01 status/control/IRQ mask, Control23[0..1], Control28, flags.
 * flags: bit0/1 four/six-byte F01/F12 differ; bit2/3 complete control/data
 * presence prefixes; bit4/5 Control23/28 captured; bit6 no descriptors;
 * bit7 F01 status/control/mask captured. A zero without its flag is unknown.
 */
static bool ft_probe_layout(uint32_t *result)
{
	uint8_t f01[6] = {0}, f12[6] = {0}, short01[4] = {0}, short12[4] = {0};
	uint8_t id[10] = {0}, q0 = 0, q5[9] = {0}, q8[5] = {0}, size5 = 0, size8 = 0;
	uint8_t first[8] = {0}, second[8] = {0}, bases[8] = {0}, control[8] = {0};
	uint8_t flags = 0;
	unsigned offset = 0;
	bool ok = false;
	*result = 1;
	if (!ft_page_zero()) { goto out; }
	*result = 2;
	if (!fajita_i2c_read(0xe3, f01, sizeof(f01)) ||
	    !fajita_i2c_read(0xdd, f12, sizeof(f12))) { goto out; }
	*result = 3;
	if (f01[5] != 1 || f12[5] != 0x12) { goto out; }
	*result = 4;
	if (!fajita_i2c_read(0xe3, short01, sizeof(short01)) ||
	    !fajita_i2c_read(0xdd, short12, sizeof(short12))) { goto out; }
	memcpy(bases, short01, 4); memcpy(bases + 4, short12, 4);
	if (memcmp(f01, short01, 4) != 0) { flags |= 1u; }
	if (memcmp(f12, short12, 4) != 0) { flags |= 2u; }
	*result = 5;
	if (!ft_probe_range(short01[0], 11, sizeof(id), id) ||
	    memcmp(id, "S3706", 5) != 0) { goto out; }
	*result = 6;
	if (!ft_probe_range(short01[3], 0, 1, control + 1) ||
	    !ft_probe_range(short01[2], 0, 1, control + 2) ||
	    !ft_probe_range(short01[2], 1, 1, control + 3)) { goto out; }
	flags |= 0x80u;
	*result = 7;
	if (!ft_probe_range(short12[0], 0, 1, &q0)) { goto out; }
	control[0] = q0;
	/* Replacement/use-case firmware can explicitly advertise NO descriptor
	 * table. In that case do not issue unsupported extended-query reads or
	 * infer Control23/28 from an assumed OEM map. */
	if (!(q0 & 1u)) { flags |= 0x40u; ok = true; goto out; }
	*result = 8;
	if (!ft_probe_range(short12[0], 4, 1, &size5)) { goto out; }
	*result = 9;
	if (!ft_probe_range(short12[0], 5, size5 < sizeof(q5) ? size5 : sizeof(q5), q5)) { goto out; }
	if (size5 >= 5) { flags |= 4u; }
	*result = 10;
	if (!ft_probe_range(short12[0], 7, 1, &size8)) { goto out; }
	*result = 11;
	if (!ft_probe_range(short12[0], 8, size8 < sizeof(q8) ? size8 : sizeof(q8), q8)) { goto out; }
	if (size8 >= 3) { flags |= 8u; }
	/* Prefix reads only, and only for controls proven present in Query5. */
	if (ft_probe_control(q5, size5 < sizeof(q5) ? size5 : sizeof(q5), 23, &offset)) {
		*result = 12;
		if (!ft_probe_range(short12[2], offset, 2, control + 4)) { goto out; }
		flags |= 0x10u;
	}
	if (ft_probe_control(q5, size5 < sizeof(q5) ? size5 : sizeof(q5), 28, &offset)) {
		*result = 13;
		if (!ft_probe_range(short12[2], offset, 1, control + 6)) { goto out; }
		flags |= 0x20u;
	}
	ok = true;
out:
	first[0] = size5; memcpy(first + 1, q5, 7);
	second[0] = size8; memcpy(second + 1, q8, 5);
	second[6] = q5[7]; second[7] = q5[8]; control[7] = flags;
	ft_metric(&fajita_touch_live.transport, ft_probe_pack_bytes(first, 8));
	ft_metric(&fajita_touch_live.reports, ft_probe_pack_bytes(second, 8));
	ft_metric(&fajita_touch_live.attention_active, ft_probe_pack_bytes(bases, 8));
	ft_metric(&fajita_touch_live.posted_events, ft_probe_pack_bytes(control, 8));
	if (ok) { *result = 0; }
	return ok;
}
