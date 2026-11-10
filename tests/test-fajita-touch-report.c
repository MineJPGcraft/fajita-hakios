/* Host tests for the original fajita S3706 wire decoder. No device access. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../devices/fajita/kernel/fajita_touch_report.h"

static bool
decode(uint16_t attention, const uint8_t *data, size_t length,
    struct fajita_touch_report *out)
{
	return fajita_touch_decode(attention, data, length,
	    FAJITA_TOUCH_MAX_X, FAJITA_TOUCH_MAX_Y, out);
}

static void
test_transfer_bound(void)
{
	size_t count = 99;

	assert(fajita_touch_slot_count(0, &count) && count == 0);
	assert(fajita_touch_slot_count(1, &count) && count == 1);
	assert(fajita_touch_slot_count(0x0200, &count) && count == 10);
	assert(fajita_touch_slot_count(0x03ff, &count) && count == 10);
	count = 99;
	assert(!fajita_touch_slot_count(0x0400, &count) && count == 99);
	assert(!fajita_touch_slot_count(0xffff, &count) && count == 99);
	assert(!fajita_touch_slot_count(1, NULL));
}

static void
test_endpoints_and_fields(void)
{
	struct fajita_touch_report r;
	const uint8_t origin[8] = { 1, 0, 0, 0, 0, 0, 0xa5, 0xbc };
	/* Vendor's inclusive 1080x2340 endpoint: 0x438, 0x924. */
	const uint8_t edge[9] = { 0x82, 0x38, 0x04, 0x24, 0x09, 255, 15, 15, 0xee };
	/* Upper nibbles of X/Y MSBs and width bytes are not coordinate bits. */
	const uint8_t middle[8] = { 3, 0x1c, 0xf2, 0x92, 0xf4, 7, 0xa9, 0xb2 };

	assert(decode(1, origin, sizeof(origin), &r));
	assert(r.active_mask == 1 && r.slots == 1);
	assert(r.contact[0].x == 0 && r.contact[0].y == 0);
	assert(r.contact[0].pressure == 0); /* Zero pressure does not release an object. */
	assert(r.contact[0].width_x == 5 && r.contact[0].width_y == 12);
	assert(decode(1, edge, sizeof(edge), &r));
	assert(r.contact[0].x == 65535 && r.contact[0].y == 65535);
	assert(r.contact[0].raw_x == 1080 && r.contact[0].raw_y == 2340);
	assert(r.contact[0].status == 2 && r.contact[0].pressure == 255);
	assert(decode(1, middle, sizeof(middle), &r));
	assert(r.contact[0].raw_x == 540 && r.contact[0].raw_y == 1170);
	assert(r.contact[0].x == 32767 && r.contact[0].y == 32767);
	assert(r.contact[0].status == 3);
	assert(r.contact[0].width_x == 9 && r.contact[0].width_y == 2);
}

static void
test_slots_and_release(void)
{
	struct fajita_touch_report r;
	struct fajita_touch_report empty = { 0 };
	uint8_t data[80] = { 0 };

	data[0] = 1;
	data[1] = 1;
	data[3] = 2;
	data[8] = 0x80; /* Low two bits alone determine active status. */
	data[9] = 0xff;
	data[10] = 0xff; /* Ignore garbage coordinates of an inactive slot. */
	data[72] = 1;
	data[73] = 0x38;
	data[74] = 0x04;
	data[75] = 0x24;
	data[76] = 0x09;
	assert(decode(0x0201, data, sizeof(data), &r));
	assert(r.slots == 10 && r.active_mask == 0x0201);
	assert(r.contact[9].x == 65535 && r.contact[9].y == 65535);
	assert(r.contact[1].status == 0 && r.contact[1].raw_x == 0);
	/* A high attention bit bounds all preceding slots; status remains authoritative. */
	assert(decode(0x0200, data, sizeof(data), &r) && r.active_mask == 0x0201);
	/* No attention is a full release, not a short-read failure. */
	assert(decode(0, NULL, 0, &r));
	assert(memcmp(&r, &empty, sizeof(r)) == 0);
	memset(data, 0, sizeof(data));
	assert(decode(0x03ff, data, sizeof(data), &r));
	assert(r.slots == 10 && r.active_mask == 0);
}

static void
test_rejection_is_atomic(void)
{
	struct fajita_touch_report r;
	struct fajita_touch_report saved;
	uint8_t data[80] = { 0 };

	memset(&saved, 0xa5, sizeof(saved));
	r = saved;
	assert(!decode(1, data, 7, &r));
	assert(memcmp(&r, &saved, sizeof(r)) == 0);
	assert(!decode(0x0200, data, 79, &r));
	assert(memcmp(&r, &saved, sizeof(r)) == 0);
	assert(!decode(1, NULL, 8, &r));
	assert(!decode(0x0400, data, sizeof(data), &r));
	assert(!decode(1, data, sizeof(data), NULL));
	assert(!fajita_touch_decode(0, NULL, 0, 0, 2340, &r));
	assert(!fajita_touch_decode(0, NULL, 0, 1080, 0, &r));
	assert(!fajita_touch_decode(0, NULL, 0, 4096, 2340, &r));
	assert(!fajita_touch_decode(0, NULL, 0, 1080, 4096, &r));
}

/* Out-of-range objects are dropped individually, not as a whole frame. */
static void
test_out_of_range_is_local(void)
{
	struct fajita_touch_report r;
	uint8_t data[80] = { 0 };

	r.active_mask = 0x0001; /* Prior press must be releasable by a drop. */
	data[0] = 1;
	data[1] = 0x39; /* 1081: one beyond the inclusive X maximum. */
	data[2] = 0x04;
	assert(decode(1, data, 8, &r));
	assert(r.active_mask == 0 && r.dropped == 1 && r.slots == 1);
	data[1] = 0;
	data[2] = 0;
	data[3] = 0x25; /* 2341: one beyond the inclusive Y maximum. */
	data[4] = 0x09;
	assert(decode(1, data, 8, &r));
	assert(r.active_mask == 0 && r.dropped == 1);
	/* One bad slot must not suppress a valid peer in the same frame: the
	 * vendor driver never range-checks, and a whole-frame rejection would
	 * let a single glitch hide every other reported contact. */
	memset(data, 0, sizeof(data));
	data[0] = 1;
	data[1] = 0x38;
	data[2] = 0x04;
	data[3] = 0x24;
	data[4] = 0x09;
	data[72] = 1;
	data[73] = 0x39; /* Second object is one past the X maximum. */
	data[74] = 0x04;
	assert(decode(0x0201, data, sizeof(data), &r));
	assert(r.slots == 10 && r.active_mask == 0x0001 && r.dropped == 1);
	assert(r.contact[0].raw_x == 1080 && r.contact[0].raw_y == 2340);
	assert(r.contact[9].status == 0 && r.contact[9].raw_x == 0);
	/* Both endpoints off-range and a clean release report no drops. */
	assert(decode(0, NULL, 0, &r) && r.active_mask == 0 && r.dropped == 0);
}

int
main(void)
{
	test_transfer_bound();
	test_endpoints_and_fields();
	test_slots_and_release();
	test_rejection_is_atomic();
	test_out_of_range_is_local();
	puts("fajita S3706 report tests passed");
	return 0;
}
