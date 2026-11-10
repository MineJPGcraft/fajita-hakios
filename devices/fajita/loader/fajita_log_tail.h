/* SPDX-License-Identifier: MIT
 * Pure geometry for the XNU loader's fixed head + rolling log buffer.
 * No memory access, firmware call, allocation or cache maintenance is done here.
 */
#ifndef FAJITA_LOG_TAIL_H
#define FAJITA_LOG_TAIL_H

#include <stdint.h>

struct fajita_log_span {
	uint64_t offset;
	uint64_t length;
};

/*
 * Return -1 for an inconsistent/out-of-bounds metadata snapshot, 0 for an
 * empty request/log, otherwise the number (1 or 2) of chronological spans.
 * Offsets are relative to the beginning of the whole head + ring buffer.
 *
 * After wrapping, only the ring is sampled, so requested is capped at ring.
 * This keeps the result to at most two spans. log_len may be capacity - 1:
 * the original kernel writer freezes at that value on the first wrap.
 * The cursor, not log_len, identifies the newest bytes after wrapping.
 */
static inline int
fajita_log_tail_spans(uint64_t head, uint64_t ring, uint32_t log_len,
    uint32_t ring_at, uint32_t wrapped, uint64_t requested,
    struct fajita_log_span spans[2])
{
	uint64_t capacity, count, start, first;
	if (spans == 0) { return -1; }
	spans[0] = (struct fajita_log_span){0, 0};
	spans[1] = (struct fajita_log_span){0, 0};
	/* Kernel length/cursor fields are 32 bit. Avoid both arithmetic overflow
	 * and accepting a geometry the producer cannot represent. */
	if (ring == 0 || head > UINT32_MAX || ring > UINT32_MAX - head || wrapped > 1) {
		return -1;
	}
	capacity = head + ring;
	if (log_len > capacity || ring_at >= ring) { return -1; }
	if (!wrapped) {
		uint64_t expected = log_len > head ? log_len - head : 0;
		if (ring_at != expected) { return -1; }
		count = requested < log_len ? requested : log_len;
		if (count == 0) { return 0; }
		spans[0] = (struct fajita_log_span){log_len - count, count};
		return 1;
	}
	if ((uint64_t)log_len + 1 < capacity) { return -1; }
	count = requested < ring ? requested : ring;
	if (count == 0) { return 0; }
	/* ring_at is where the NEXT byte will be written. */
	start = ((uint64_t)ring_at + ring - count) % ring;
	first = count < ring - start ? count : ring - start;
	spans[0] = (struct fajita_log_span){head + start, first};
	if (first == count) { return 1; }
	spans[1] = (struct fajita_log_span){head, count - first};
	return 2;
}

#endif
