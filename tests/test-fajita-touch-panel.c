/* Host-only tests of the production memory-only touch diagnostic panel. */
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../devices/fajita/loader/fajita_touch_panel.h"

#define SENTINEL UINT32_C(0x5aa56bb6)
#define GUARD_WORDS 16u

struct fixture {
	uint32_t *allocation;
	uint32_t *pixels;
	size_t words;
	struct fajita_touch_panel_surface surface;
};

static struct fixture fixture(uint32_t width, uint32_t height, uint32_t padding)
{
	struct fixture f;
	uint32_t stride_words = width + padding;
	f.words = (size_t)stride_words * height;
	f.allocation = malloc((f.words + 2u * GUARD_WORDS) * sizeof(uint32_t));
	assert(f.allocation != NULL);
	f.pixels = f.allocation + GUARD_WORDS;
	f.surface = (struct fajita_touch_panel_surface){f.pixels, width, height,
	    stride_words * (uint32_t)sizeof(uint32_t), f.words * sizeof(uint32_t)};
	for (size_t n = 0; n < f.words + 2u * GUARD_WORDS; n++) {
		f.allocation[n] = SENTINEL;
	}
	return f;
}

static void destroy(struct fixture *f)
{
	for (unsigned n = 0; n < GUARD_WORDS; n++) {
		assert(f->allocation[n] == SENTINEL);
		assert(f->pixels[f->words + n] == SENTINEL);
	}
	free(f->allocation);
}

static uint32_t pixel(const struct fixture *f, uint32_t x, uint32_t y)
{
	return f->pixels[(size_t)y * (f->surface.stride_bytes / 4u) + x];
}

static void assert_untouched(const struct fixture *f)
{
	for (size_t n = 0; n < f->words + 2u * GUARD_WORDS; n++) {
		assert(f->allocation[n] == SENTINEL);
	}
}

/* Independent expected bitmap, including 3x3 replication and cell spacing. */
static void expect_glyph(const struct fixture *f, uint32_t x, uint32_t y,
    const uint8_t rows[7])
{
	for (uint32_t row = 0; row < 24u; row++) {
		for (uint32_t col = 0; col < 18u; col++) {
			bool white = row < 21u && col < 15u &&
			    (rows[row / 3u] & (1u << (4u - col / 3u))) != 0;
			assert(pixel(f, x + col, y + row) == (white ? UINT32_C(0x00ffffff) : 0u));
		}
	}
}

static const uint8_t zero[7] = {14, 17, 19, 21, 25, 17, 14};
static const uint8_t one[7] = {4, 12, 4, 4, 4, 4, 14};
static const uint8_t letter_p[7] = {30, 17, 17, 30, 16, 16, 16};
static const uint8_t letter_f[7] = {31, 16, 16, 30, 16, 16, 16};
static const uint8_t letter_d[7] = {30, 17, 17, 17, 17, 17, 30};
static const uint8_t letter_e[7] = {31, 16, 16, 30, 16, 16, 31};
static const uint8_t letter_n[7] = {17, 25, 25, 21, 19, 19, 17};
static const uint8_t letter_r[7] = {30, 17, 17, 30, 20, 18, 17};
static const uint8_t letter_q[7] = {14, 17, 17, 17, 21, 18, 13};
static const uint8_t equals[7] = {0, 0, 31, 0, 31, 0, 0};
static const uint8_t letter_a[7] = {14, 17, 17, 31, 17, 17, 17};
static const uint8_t letter_b[7] = {30, 17, 17, 30, 17, 17, 30};
static const uint8_t letter_c[7] = {14, 17, 16, 16, 16, 17, 14};
static const uint8_t letter_i[7] = {31, 4, 4, 4, 4, 4, 31};
static const uint8_t two[7] = {14, 17, 1, 2, 4, 8, 31};
static const uint8_t three[7] = {30, 1, 1, 14, 1, 1, 30};
static const uint8_t four[7] = {2, 6, 10, 18, 31, 2, 2};
static const uint8_t five[7] = {31, 16, 16, 30, 1, 1, 30};
static const uint8_t six[7] = {14, 16, 16, 30, 17, 17, 14};
static const uint8_t seven[7] = {31, 1, 2, 4, 8, 8, 8};
static const uint8_t eight[7] = {14, 17, 17, 14, 17, 17, 14};
static const uint8_t nine[7] = {14, 17, 17, 15, 1, 1, 14};
static const uint8_t *const hex_glyphs[16] = {
	zero, one, two, three, four, five, six, seven,
	eight, nine, letter_a, letter_b, letter_c, letter_d, letter_e, letter_f
};

static void expect_hex(const struct fixture *f, uint32_t x, uint32_t y,
    const char *hex)
{
	for (size_t n = 0; hex[n] != '\0'; n++) {
		fprintf(stderr, "expect_hex %s n=%zu x=%u y=%u\\n", hex, n, x + (uint32_t)n * 18u, y);
		unsigned v = hex[n] <= '9' ? (unsigned)(hex[n] - '0') :
		    10u + (unsigned)(hex[n] - 'A');
		expect_glyph(f, x + (uint32_t)n * 18u, y, hex_glyphs[v]);
	}
}

static void test_layout_and_preservation(void)
{
	struct fixture f = fixture(1080, 2340, 13);
	struct fajita_touch_panel_state state = {
		UINT32_C(0x01234567), UINT32_C(0x89abcdef), UINT32_C(0xabcdef01),
		UINT64_C(0x0123456789abcdef), UINT64_C(0xfeedbeef12345678),
		UINT64_C(0xffeeddccbbaa9988), UINT64_C(0x1122334455667788),
			UINT64_C(0x99aabbccddeeff00), false
	};
	assert(fajita_touch_panel_draw(&f.surface, &state));
	uint32_t top = f.surface.height - 80u - 132u;
	uint32_t y = top + 6u;
	expect_glyph(&f, 6, y, letter_p);
	expect_glyph(&f, 24, y, equals);
	expect_hex(&f, 42, y, "01234567");
	expect_glyph(&f, 204, y, letter_f);
	expect_glyph(&f, 222, y, equals);
	expect_hex(&f, 240, y, "89ABCDEF");
	y += 24u;
	expect_glyph(&f, 6, y, letter_d);
	expect_glyph(&f, 24, y, equals);
	expect_hex(&f, 42, y, "ABCDEF01");
	expect_glyph(&f, 204, y, letter_n);
	expect_glyph(&f, 222, y, equals);
	expect_hex(&f, 240, y, "12345678");
	y += 24u;
	expect_glyph(&f, 6, y, letter_a);
	expect_glyph(&f, 24, y, equals);
	expect_hex(&f, 42, y, "55667788");
	expect_glyph(&f, 204, y, letter_q);
	expect_glyph(&f, 222, y, equals);
	expect_hex(&f, 240, y, "DDEEFF00");
	y += 24u;
	expect_glyph(&f, 6, y, letter_e);
	expect_glyph(&f, 24, y, equals);
	expect_hex(&f, 42, y, "0123456789ABCDEF");
	y += 24u;
	expect_glyph(&f, 6, y, letter_r);
	expect_glyph(&f, 24, y, equals);
	expect_hex(&f, 42, y, "FFEEDDCCBBAA9988");
	/* Check every scanline, including stride padding and all four edges. */
	uint32_t stride = f.surface.stride_bytes / 4u;
	for (uint32_t row = 0; row < f.surface.height; row++) {
		for (uint32_t col = 0; col < stride; col++) {
			uint32_t actual = pixel(&f, col, row);
			if (row < top || row >= top + 132u || col >= 390u) {
				assert(actual == SENTINEL);
			} else {
				assert(actual == 0u || actual == UINT32_C(0x00ffffff));
			}
		}
	}
	for (uint32_t col = 0; col < 390u; col++) {
		assert(pixel(&f, col, top) == 0u);
		assert(pixel(&f, col, top + 131u) == 0u);
	}
	/* Repaint clears old white strokes; an unchanged paint is deterministic. */
	state = (struct fajita_touch_panel_state){0};
	assert(fajita_touch_panel_draw(&f.surface, &state));
	expect_hex(&f, 42, top + 6u, "00000000");
	expect_hex(&f, 240, top + 30u, "00000000");
	expect_hex(&f, 42, top + 54u, "00000000");
	expect_hex(&f, 240, top + 54u, "00000000");
	expect_hex(&f, 42, top + 78u, "0000000000000000");
	expect_glyph(&f, 6, top + 102u, letter_r);
	expect_hex(&f, 42, top + 102u, "0000000000000000");
	destroy(&f);
}

static void rejected(struct fixture *f, struct fajita_touch_panel_surface bad,
    const struct fajita_touch_panel_state *state)
{
	assert(!fajita_touch_panel_draw(&bad, state));
	assert_untouched(f);
}

static void test_invalid_has_no_writes(void)
{
	struct fixture f = fixture(400, 200, 7);
	const struct fajita_touch_panel_state state = {0};
	struct fajita_touch_panel_surface bad = f.surface;
	assert(!fajita_touch_panel_draw(NULL, &state));
	assert(!fajita_touch_panel_draw(NULL, NULL));
	rejected(&f, f.surface, NULL);
	bad.pixels = NULL; rejected(&f, bad, &state);
	bad = f.surface; bad.width = 0; rejected(&f, bad, &state);
	bad = f.surface; bad.width = 389; rejected(&f, bad, &state);
	bad = f.surface; bad.width = 4097; rejected(&f, bad, &state);
	bad = f.surface; bad.width = UINT32_MAX; rejected(&f, bad, &state);
	bad = f.surface; bad.height = 0; rejected(&f, bad, &state);
	bad = f.surface; bad.height = 187; rejected(&f, bad, &state);
	bad = f.surface; bad.height = 8193; rejected(&f, bad, &state);
	bad = f.surface; bad.height = UINT32_MAX; rejected(&f, bad, &state);
	bad = f.surface; bad.stride_bytes = 0; rejected(&f, bad, &state);
	bad = f.surface; bad.stride_bytes = 1596; rejected(&f, bad, &state);
	bad = f.surface; bad.stride_bytes += 1u; rejected(&f, bad, &state);
	bad = f.surface; bad.stride_bytes += 2u; rejected(&f, bad, &state);
	bad = f.surface; bad.stride_bytes += 3u; rejected(&f, bad, &state);
	bad = f.surface; bad.stride_bytes = UINT32_MAX; rejected(&f, bad, &state);
	bad = f.surface; bad.capacity_bytes = 0; rejected(&f, bad, &state);
	bad = f.surface; bad.capacity_bytes--; rejected(&f, bad, &state);
	/* Capacity must include trailing scanline padding, not only visible bytes. */
	bad = f.surface; bad.capacity_bytes -= 28u; rejected(&f, bad, &state);
	bad = f.surface; bad.capacity_bytes = UINT64_MAX; rejected(&f, bad, &state);
	bad = f.surface; bad.pixels = (volatile uint32_t *)((uintptr_t)f.pixels + 1u);
	rejected(&f, bad, &state);
	bad = f.surface; bad.pixels = (volatile uint32_t *)(UINTPTR_MAX & ~(uintptr_t)3u);
	rejected(&f, bad, &state);
	/* All 32-bit inputs participate in 64-bit size arithmetic before access. */
	bad = f.surface; bad.stride_bytes = UINT32_MAX - 3u;
	bad.height = 8192; bad.capacity_bytes = UINT32_MAX;
	rejected(&f, bad, &state);
	destroy(&f);
}

static void test_geometry_limits(void)
{
	const struct fajita_touch_panel_state state = {UINT32_MAX, UINT32_MAX,
	    UINT32_MAX, UINT64_MAX, UINT64_MAX, UINT64_MAX, UINT64_MAX, UINT64_MAX, false};
	struct fixture minimum = fixture(390, 212, 0);
	assert(fajita_touch_panel_draw(&minimum.surface, &state));
	expect_glyph(&minimum, 6, 6, letter_p);
	for (uint32_t y = 132; y < 212; y++) {
		for (uint32_t x = 0; x < 390; x++) {
			assert(pixel(&minimum, x, y) == SENTINEL);
		}
	}
	destroy(&minimum);
	/* Each maximum is exercised without making sanitizer runs allocate 128 MiB. */
	struct fixture wide = fixture(4096, 212, 3);
	assert(fajita_touch_panel_draw(&wide.surface, &state));
	assert(pixel(&wide, 390, 0) == SENTINEL);
	assert(pixel(&wide, 4098, 107) == SENTINEL);
	destroy(&wide);
	struct fixture tall = fixture(390, 8192, 1);
	assert(fajita_touch_panel_draw(&tall.surface, &state));
	assert(pixel(&tall, 0, 7979) == SENTINEL);
	expect_glyph(&tall, 6, 7986, letter_p);
	assert(pixel(&tall, 390, 8111) == SENTINEL);
	assert(pixel(&tall, 0, 8112) == SENTINEL);
	destroy(&tall);
}

static void test_identity_probe_layout(void)
{
	struct fixture f = fixture(390, 2340, 0);
	const struct fajita_touch_panel_state state = {
		.power = 0, .phase = 17, .detail = 1,
		.transport = UINT64_C(0x5333373036422d31),
		.reports = UINT64_C(0x3233000000000000),
		.attention_active = UINT64_C(0x0000101120300001),
		.posted_events = UINT64_C(0x0000404150600112),
		.identity_probe = true
	};
	assert(fajita_touch_panel_draw(&f.surface, &state));
	uint32_t top = f.surface.height - 80u - FAJITA_TOUCH_PANEL_HEIGHT;
	uint32_t y = top + FAJITA_TOUCH_PANEL_PAD;
	expect_glyph(&f, 6, y, letter_f);
	expect_glyph(&f, 204, y, letter_d);
	y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
	expect_glyph(&f, 6, y, letter_i);
	expect_glyph(&f, 24, y, one);
	expect_glyph(&f, 42, y, equals);
	expect_hex(&f, 60, y, "5333373036422D31");
	y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
	expect_glyph(&f, 24, y, two);
	expect_hex(&f, 60, y, "3233000000000000");
	y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
	expect_glyph(&f, 24, y, one);
	expect_hex(&f, 60, y, "101120300001");
	y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
	expect_glyph(&f, 24, y, two);
	expect_hex(&f, 60, y, "404150600112");
	destroy(&f);
}

static void test_query_layout_probe(void)
{
	for (uint32_t phase = FAJITA_TOUCH_PANEL_LAYOUT_DONE;
	    phase <= FAJITA_TOUCH_PANEL_LAYOUT_FAILED; phase++) {
		struct fixture f = fixture(390, 2340, 11);
		const struct fajita_touch_panel_state state = {
			.phase = phase, .detail = 13,
			.transport = UINT64_C(0x0900809010800000),
			.reports = UINT64_C(0x0300028000000000),
			.attention_active = UINT64_C(0x2b220d064a001308),
			.posted_events = UINT64_C(0x0100807f010affbf)
		};
		assert(fajita_touch_panel_draw(&f.surface, &state));
		uint32_t top = f.surface.height - 80u - FAJITA_TOUCH_PANEL_HEIGHT;
		uint32_t y = top + FAJITA_TOUCH_PANEL_PAD;
		expect_glyph(&f, 6, y, letter_f);
		expect_glyph(&f, 204, y, letter_d);
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		expect_glyph(&f, 6, y, letter_q); expect_glyph(&f, 24, y, five);
		expect_glyph(&f, 42, y, equals); expect_hex(&f, 60, y, "0900809010800000");
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		expect_glyph(&f, 6, y, letter_q); expect_glyph(&f, 24, y, eight);
		expect_hex(&f, 60, y, "0300028000000000");
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		expect_glyph(&f, 6, y, letter_c); expect_glyph(&f, 24, y, one);
		expect_hex(&f, 60, y, "2B220D064A001308");
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		expect_glyph(&f, 6, y, letter_c); expect_glyph(&f, 24, y, two);
		expect_hex(&f, 60, y, "0100807F010AFFBF");
		for (uint32_t row = 0; row < f.surface.height; row++) {
			for (uint32_t col = 0; col < f.surface.stride_bytes / 4u; col++) {
				if (row < top || row >= top + 132u || col >= 390u) {
					assert(pixel(&f, col, row) == SENTINEL);
				}
			}
		}
		destroy(&f);
	}
}

int main(void)
{
	test_layout_and_preservation();
	test_invalid_has_no_writes();
	test_geometry_limits();
	test_identity_probe_layout();
	test_query_layout_probe();
	puts("fajita memory-only touch panel tests passed");
	return 0;
}
