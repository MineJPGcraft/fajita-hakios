/*
 * Original bounded S3706 report decoder for fajita-hakios.
 *
 * Wire-format facts were checked against OnePlusOSS/android_kernel_oneplus_sdm845,
 * commit 222822c2d0042067b7964c6302ef4d756dc4bed9,
 * drivers/input/touchscreen/synaptics_driver_s3320.c (touch reporting and
 * the S3706/18801 coordinate selection). No Linux driver code is included.
 *
 * This header has no XNU dependencies so the wire decoder can be tested on
 * the host before any GPIO/I2C access is enabled on the phone.
 */
#ifndef FAJITA_TOUCH_REPORT_H
#define FAJITA_TOUCH_REPORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "fajita_touch_board.h"

#define FAJITA_TOUCH_MAX_CONTACTS 10u
#define FAJITA_TOUCH_SLOT_BYTES 8u
#define FAJITA_TOUCH_ATTENTION_MASK UINT16_C(0x03ff)

struct fajita_touch_contact {
	uint16_t x, y;             /* normalized 0..65535, inclusive */
	uint16_t raw_x, raw_y;     /* controller's 12-bit coordinates */
	uint8_t status;            /* low two object-status bits */
	uint8_t pressure;
	uint8_t width_x, width_y;  /* low nibbles of the two width bytes */
};

struct fajita_touch_report {
	uint16_t active_mask;
	uint16_t slots;            /* number of slots needed for this attention word */
	uint16_t dropped;          /* objects discarded for out-of-range coordinates */
	struct fajita_touch_contact contact[FAJITA_TOUCH_MAX_CONTACTS];
};

/*
 * Object attention bounds the transfer through its highest set bit. As in
 * the vendor reporting path, each returned slot's status determines whether
 * that slot is active; gaps in attention do not change slot numbering.
 * Invalid attention words are rejected rather than silently truncated.
 */
static inline bool
fajita_touch_slot_count(uint16_t attention, size_t *slots)
{
	size_t n = 0;

	if (slots == NULL || (attention & (uint16_t)~FAJITA_TOUCH_ATTENTION_MASK) != 0) {
		return false;
	}
	for (; attention != 0; attention >>= 1) {
		n++;
	}
	*slots = n;
	return true;
}

/*
 * Decode contiguous eight-byte slots beginning at F12 data base. The vendor
 * bus read requests one extra byte; this decoder consumes only the complete
 * slots and accepts trailing bytes. A zero-attention release needs no buffer.
 *
 * max_x/max_y are inclusive raw maxima (18801 uses 1080 and 2340), not pixel
 * counts minus one. Normalization uses floor(raw * 65535 / maximum).
 *
 * A single object whose raw coordinates exceed the sensor range is dropped
 * individually instead of rejecting the whole frame. The vendor driver never
 * range-checks at all; rejecting the frame would let one glitched slot
 * suppress every other contact in the same report, and eight consecutive
 * such frames stop the polling thread. Dropping only that object keeps
 * release semantics (it is simply absent, so an earlier press reports UP).
 * Structural faults -- truncated frames, impossible attention words and
 * invalid parameters -- still reject atomically and leave *out untouched.
 * Inactive slots are zeroed.
 */
static inline bool
fajita_touch_decode(uint16_t attention, const uint8_t *data, size_t length,
    uint16_t max_x, uint16_t max_y, struct fajita_touch_report *out)
{
	struct fajita_touch_report decoded = { 0 };
	size_t slots;

	if (out == NULL || max_x == 0 || max_x > 0x0fffu || max_y == 0 || max_y > 0x0fffu ||
	    !fajita_touch_slot_count(attention, &slots) ||
	    (slots != 0 && (data == NULL || length < slots * FAJITA_TOUCH_SLOT_BYTES))) {
		return false;
	}
	decoded.slots = (uint16_t)slots;
	for (size_t i = 0; i < slots; i++) {
		const uint8_t *slot = data + i * FAJITA_TOUCH_SLOT_BYTES;
		struct fajita_touch_contact *point = &decoded.contact[i];
		uint8_t status = slot[0] & 3u;
		uint16_t x, y;

		if (status == 0) {
			continue;
		}
		x = (uint16_t)(slot[1] | (uint16_t)(slot[2] & 0x0fu) << 8);
		y = (uint16_t)(slot[3] | (uint16_t)(slot[4] & 0x0fu) << 8);
		if (x > max_x || y > max_y) {
			/* Keep the slot inactive; other objects still publish. */
			decoded.dropped++;
			continue;
		}
		point->raw_x = x;
		point->raw_y = y;
		point->x = (uint16_t)((uint32_t)x * UINT32_C(65535) / max_x);
		point->y = (uint16_t)((uint32_t)y * UINT32_C(65535) / max_y);
		point->status = status;
		point->pressure = slot[5];
		point->width_x = slot[6] & 0x0fu;
		point->width_y = slot[7] & 0x0fu;
		decoded.active_mask |= (uint16_t)(1u << i);
	}
	*out = decoded;
	return true;
}

#endif /* FAJITA_TOUCH_REPORT_H */
