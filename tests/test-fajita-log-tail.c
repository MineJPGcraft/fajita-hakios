/* Host-only tests of the production log-tail geometry. */
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../devices/fajita/loader/fajita_log_tail.h"

static void expect(uint64_t head, uint64_t ring, uint32_t len, uint32_t at,
    uint32_t wrapped, uint64_t wanted, int n,
    uint64_t a, uint64_t alen, uint64_t b, uint64_t blen)
{
	struct fajita_log_span spans[2] = {{99, 99}, {99, 99}};
	int result = fajita_log_tail_spans(head, ring, len, at, wrapped, wanted, spans);
	assert(result == n);
	assert(spans[0].offset == a && spans[0].length == alen);
	assert(spans[1].offset == b && spans[1].length == blen);
	if (result > 0) {
		for (int i = 0; i < result; i++) {
			assert(spans[i].length > 0);
			assert(spans[i].offset <= head + ring);
			assert(spans[i].length <= head + ring - spans[i].offset);
		}
	}
}

static void test_linear(void)
{
	expect(4, 8, 0, 0, 0, 8, 0, 0, 0, 0, 0);
	expect(4, 8, 3, 0, 0, 8, 1, 0, 3, 0, 0);
	expect(4, 8, 4, 0, 0, 3, 1, 1, 3, 0, 0);
	expect(4, 8, 7, 3, 0, 5, 1, 2, 5, 0, 0);
	expect(4, 8, 11, 7, 0, 240, 1, 0, 11, 0, 0);
	expect(4, 8, 7, 3, 0, 0, 0, 0, 0, 0, 0);
	expect(0, 8, 7, 7, 0, 3, 1, 4, 3, 0, 0);
}

static void test_wrapped(void)
{
	/* Original writer freezes len at capacity - 1. Both that and a corrected
	 * full-capacity count must work; neither determines the current tail. */
	expect(4, 8, 11, 0, 1, 3, 1, 9, 3, 0, 0);
	expect(4, 8, 12, 0, 1, 3, 1, 9, 3, 0, 0);
	expect(4, 8, 11, 2, 1, 5, 2, 9, 3, 4, 2);
	expect(4, 8, 11, 5, 1, 3, 1, 6, 3, 0, 0);
	expect(4, 8, 11, 2, 1, 8, 2, 6, 6, 4, 2);
	expect(4, 8, 11, 2, 1, UINT64_MAX, 2, 6, 6, 4, 2);
	expect(4, 8, 11, 0, 1, 8, 1, 4, 8, 0, 0);
	expect(4, 8, 11, 3, 1, 0, 0, 0, 0, 0, 0);
	expect(0, 1, 0, 0, 1, 240, 1, 0, 1, 0, 0);
}

static void test_invalid(void)
{
	expect(4, 0, 0, 0, 0, 8, -1, 0, 0, 0, 0);
	expect(UINT64_MAX, 8, 0, 0, 0, 8, -1, 0, 0, 0, 0);
	expect(8, UINT64_MAX, 0, 0, 0, 8, -1, 0, 0, 0, 0);
	expect(UINT32_MAX, 1, 0, 0, 0, 8, -1, 0, 0, 0, 0);
	expect(4, 8, 13, 0, 0, 8, -1, 0, 0, 0, 0);
	expect(4, 8, 11, 8, 1, 8, -1, 0, 0, 0, 0);
	expect(4, 8, 3, 1, 0, 8, -1, 0, 0, 0, 0);
	expect(4, 8, 7, 2, 0, 8, -1, 0, 0, 0, 0);
	expect(4, 8, 9, 2, 1, 8, -1, 0, 0, 0, 0);
	expect(4, 8, 11, 2, 2, 8, -1, 0, 0, 0, 0);
	assert(fajita_log_tail_spans(4, 8, 11, 2, 1, 8, NULL) == -1);
}

static void test_marker_across_boundary(void)
{
	/* A fixed head followed by an 8-byte ring. NEW! starts at its last byte,
	 * then continues at the start. The stale physical tail contains OLD. */
	const char buffer[12] = {'H', 'E', 'A', 'D', 'E', 'W', '!', 'x', 'O', 'L', 'D', 'N'};
	struct fajita_log_span spans[2];
	char tail[5] = {0};
	int n = fajita_log_tail_spans(4, 8, 11, 3, 1, 4, spans);
	assert(n == 2);
	size_t at = 0;
	for (int i = 0; i < n; i++) {
		memcpy(tail + at, buffer + spans[i].offset, (size_t)spans[i].length);
		at += (size_t)spans[i].length;
	}
	assert(strcmp(tail, "NEW!") == 0);
	assert(memcmp(buffer + 7, "xOLD", 4) == 0); /* old len-based sample */
}

static void test_real_geometry(void)
{
	const uint64_t head = 256 * 1024, ring = 1024 * 1024;
	expect(head, ring, (uint32_t)(head + ring - 1), 68, 1, 240,
	    2, head + ring - 172, 172, head, 68);
	expect(head, ring, (uint32_t)(head + 68), 68, 0, 240,
	    1, head + 68 - 240, 240, 0, 0);
	/* Largest representable capacity must not overflow its arithmetic. */
	expect(0, UINT32_MAX, UINT32_MAX, 0, 1, 1,
	    1, (uint64_t)UINT32_MAX - 1, 1, 0, 0);
}

int main(void)
{
	test_linear(); test_wrapped(); test_invalid();
	test_marker_across_boundary(); test_real_geometry();
	puts("fajita chronological log-tail geometry tests passed");
	return 0;
}
