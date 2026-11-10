/* SPDX-License-Identifier: MIT
 * Memory-only diagnostic overlay for a validated 32-bit framebuffer.
 * No firmware calls, allocation, I/O or cache maintenance are performed.
 */
#ifndef FAJITA_TOUCH_PANEL_H
#define FAJITA_TOUCH_PANEL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct fajita_touch_panel_surface {
	volatile uint32_t *pixels;
	uint32_t width, height, stride_bytes;
	uint64_t capacity_bytes;
};

struct fajita_touch_panel_state {
	uint32_t power, phase, detail;
	uint64_t transport, reports;
	uint64_t rpmh;
	uint64_t attention_active, posted_events;
	bool identity_probe;
};

#define FAJITA_TOUCH_PANEL_WIDTH 390u
#define FAJITA_TOUCH_PANEL_HEIGHT 132u
#define FAJITA_TOUCH_PANEL_BOTTOM_GAP 80u
#define FAJITA_TOUCH_PANEL_SCALE 3u
#define FAJITA_TOUCH_PANEL_PAD 6u
#define FAJITA_TOUCH_PANEL_CELL_WIDTH 18u
#define FAJITA_TOUCH_PANEL_LINE_HEIGHT 24u
/* Mirrored terminal phases; the resident reader asserts the shared ABI. */
#define FAJITA_TOUCH_PANEL_LAYOUT_DONE 19u
#define FAJITA_TOUCH_PANEL_LAYOUT_FAILED 20u

/* Five-bit rows, most significant pixel first. Unknown glyphs are blank. */
static inline uint8_t
fajita_touch_panel_glyph_row(char ch, unsigned row)
{
	static const uint8_t glyphs[][7] = {
		{14, 17, 19, 21, 25, 17, 14}, /* 0 */
		{4, 12, 4, 4, 4, 4, 14},    /* 1 */
		{14, 17, 1, 2, 4, 8, 31},   /* 2 */
		{30, 1, 1, 14, 1, 1, 30},   /* 3 */
		{2, 6, 10, 18, 31, 2, 2},   /* 4 */
		{31, 16, 16, 30, 1, 1, 30}, /* 5 */
		{14, 16, 16, 30, 17, 17, 14}, /* 6 */
		{31, 1, 2, 4, 8, 8, 8},     /* 7 */
		{14, 17, 17, 14, 17, 17, 14}, /* 8 */
		{14, 17, 17, 15, 1, 1, 14}, /* 9 */
		{14, 17, 17, 31, 17, 17, 17}, /* A */
		{30, 17, 17, 30, 17, 17, 30}, /* B */
		{14, 17, 16, 16, 16, 17, 14}, /* C */
		{30, 17, 17, 17, 17, 17, 30}, /* D */
		{31, 16, 16, 30, 16, 16, 31}, /* E */
		{31, 16, 16, 30, 16, 16, 16}, /* F */
		{30, 17, 17, 30, 16, 16, 16}, /* P */
		{17, 25, 25, 21, 19, 19, 17}, /* N */
		{0, 0, 31, 0, 31, 0, 0},     /* = */
		{30, 17, 17, 30, 20, 18, 17}, /* R */
		{14, 17, 17, 17, 21, 18, 13}, /* Q */
		{31, 4, 4, 4, 4, 4, 31}       /* I */
	};
	unsigned index;
	if (row >= 7) { return 0; }
	if (ch >= '0' && ch <= '9') { index = (unsigned)(ch - '0'); }
	else if (ch >= 'A' && ch <= 'F') { index = 10u + (unsigned)(ch - 'A'); }
	else if (ch == 'P') { index = 16; }
	else if (ch == 'N') { index = 17; }
	else if (ch == '=') { index = 18; }
	else if (ch == 'R') { index = 19; }
	else if (ch == 'Q') { index = 20; }
	else if (ch == 'I') { index = 21; }
	else { return 0; }
	return glyphs[index][row];
}

static inline void
fajita_touch_panel_character(volatile uint32_t *pixels, uint32_t stride,
    uint32_t x, uint32_t y, char ch)
{
	for (unsigned row = 0; row < 7; row++) {
		uint8_t bits = fajita_touch_panel_glyph_row(ch, row);
		for (unsigned col = 0; col < 5; col++) {
			if (!(bits & (1u << (4u - col)))) { continue; }
			for (unsigned dy = 0; dy < FAJITA_TOUCH_PANEL_SCALE; dy++) {
				uint64_t at = (uint64_t)(y + row * FAJITA_TOUCH_PANEL_SCALE + dy) * stride +
				    x + col * FAJITA_TOUCH_PANEL_SCALE;
				for (unsigned dx = 0; dx < FAJITA_TOUCH_PANEL_SCALE; dx++) {
					pixels[(size_t)(at + dx)] = UINT32_C(0x00ffffff);
				}
			}
		}
	}
}

static inline void
fajita_touch_panel_field(volatile uint32_t *pixels, uint32_t stride,
    uint32_t x, uint32_t y, char label, uint64_t value, unsigned digits)
{
	static const char hex[] = "0123456789ABCDEF";
	fajita_touch_panel_character(pixels, stride, x, y, label);
	x += FAJITA_TOUCH_PANEL_CELL_WIDTH;
	fajita_touch_panel_character(pixels, stride, x, y, '=');
	for (unsigned n = 0; n < digits; n++) {
		unsigned shift = (digits - n - 1u) * 4u;
		x += FAJITA_TOUCH_PANEL_CELL_WIDTH;
		fajita_touch_panel_character(pixels, stride, x, y,
		    hex[(value >> shift) & UINT64_C(15)]);
	}
}

static inline void
fajita_touch_panel_tagged_field(volatile uint32_t *pixels, uint32_t stride,
	    uint32_t x, uint32_t y, char first, char second, uint64_t value,
	    unsigned digits)
{
	static const char hex[] = "0123456789ABCDEF";
	fajita_touch_panel_character(pixels, stride, x, y, first);
	x += FAJITA_TOUCH_PANEL_CELL_WIDTH;
	fajita_touch_panel_character(pixels, stride, x, y, second);
	x += FAJITA_TOUCH_PANEL_CELL_WIDTH;
	fajita_touch_panel_character(pixels, stride, x, y, '=');
	for (unsigned n = 0; n < digits; n++) {
		unsigned shift = (digits - n - 1u) * 4u;
		x += FAJITA_TOUCH_PANEL_CELL_WIDTH;
		fajita_touch_panel_character(pixels, stride, x, y,
		    hex[(value >> shift) & UINT64_C(15)]);
	}
}

/*
 * On success the only modified pixels are the black/white rectangle:
 *   x = 0, y = height - BOTTOM_GAP - HEIGHT, size WIDTH by HEIGHT.
 * Its bottom edge is exactly BOTTOM_GAP pixels above the framebuffer bottom.
 * P/F/D/N/A/Q are eight hexadecimal digits (N is the report counter, A is the
 * raw/decoded attention sample, and Q is posted events); E/R are sixteen.
 *
 * Caller owns serialization, pixel-format validation and cache cleaning.
 * Clean WIDTH * 4 bytes on each of HEIGHT scanlines starting at y; a single
 * conservative contiguous clean span is [y * stride_bytes,
 *   (y + HEIGHT - 1) * stride_bytes + WIDTH * 4), relative to pixels.
 * Metadata, pointer/extent overflow, full-surface capacity and minimum size
 * are checked before ANY pixel write; invalid arguments return false.
 */
static inline bool
fajita_touch_panel_draw(struct fajita_touch_panel_surface *surface,
    const struct fajita_touch_panel_state *state)
{
	if (surface == NULL || state == NULL) { return false; }
	struct fajita_touch_panel_surface fb = *surface;
	if (fb.pixels == NULL || fb.width < FAJITA_TOUCH_PANEL_WIDTH ||
	    fb.width > 4096u || fb.height < FAJITA_TOUCH_PANEL_HEIGHT + FAJITA_TOUCH_PANEL_BOTTOM_GAP ||
	    fb.height > 8192u || (fb.stride_bytes & 3u) != 0 ||
	    (uint64_t)fb.stride_bytes < (uint64_t)fb.width * sizeof(uint32_t)) {
		return false;
	}
	uintptr_t base = (uintptr_t)fb.pixels;
	uint64_t required = (uint64_t)fb.stride_bytes * fb.height;
	if ((base & 3u) != 0 || required > (uint64_t)SIZE_MAX ||
	    required > (uint64_t)PTRDIFF_MAX || fb.capacity_bytes < required ||
	    fb.capacity_bytes > (uint64_t)UINTPTR_MAX - base) {
		return false;
	}
	/* Snapshot before writing, so the source state can safely overlap pixels. */
	struct fajita_touch_panel_state snapshot = *state;
	uint32_t y = fb.height - FAJITA_TOUCH_PANEL_BOTTOM_GAP - FAJITA_TOUCH_PANEL_HEIGHT;
	uint32_t stride = fb.stride_bytes / (uint32_t)sizeof(uint32_t);
	for (uint32_t row = 0; row < FAJITA_TOUCH_PANEL_HEIGHT; row++) {
		uint64_t at = (uint64_t)(y + row) * stride;
		for (uint32_t x = 0; x < FAJITA_TOUCH_PANEL_WIDTH; x++) {
			fb.pixels[(size_t)(at + x)] = UINT32_C(0x00000000);
		}
	}
	y += FAJITA_TOUCH_PANEL_PAD;
	bool layout_probe = snapshot.phase == FAJITA_TOUCH_PANEL_LAYOUT_DONE ||
	    snapshot.phase == FAJITA_TOUCH_PANEL_LAYOUT_FAILED;
	if (snapshot.identity_probe || layout_probe) {
		fajita_touch_panel_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    'F', snapshot.phase, 8);
		fajita_touch_panel_field(fb.pixels, stride,
		    FAJITA_TOUCH_PANEL_PAD + 11u * FAJITA_TOUCH_PANEL_CELL_WIDTH, y,
		    'D', snapshot.detail, 8);
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		fajita_touch_panel_tagged_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    layout_probe ? 'Q' : 'I', layout_probe ? '5' : '1', snapshot.transport, 16);
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		fajita_touch_panel_tagged_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    layout_probe ? 'Q' : 'I', layout_probe ? '8' : '2', snapshot.reports, 16);
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		fajita_touch_panel_tagged_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    layout_probe ? 'C' : 'F', '1', snapshot.attention_active, layout_probe ? 16 : 12);
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		fajita_touch_panel_tagged_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    layout_probe ? 'C' : 'F', '2', snapshot.posted_events, layout_probe ? 16 : 12);
	} else {
		fajita_touch_panel_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    'P', snapshot.power, 8);
		fajita_touch_panel_field(fb.pixels, stride,
		    FAJITA_TOUCH_PANEL_PAD + 11u * FAJITA_TOUCH_PANEL_CELL_WIDTH, y,
		    'F', snapshot.phase, 8);
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		fajita_touch_panel_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    'D', snapshot.detail, 8);
		fajita_touch_panel_field(fb.pixels, stride,
		    FAJITA_TOUCH_PANEL_PAD + 11u * FAJITA_TOUCH_PANEL_CELL_WIDTH, y,
		    'N', (uint32_t)snapshot.reports, 8);
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		fajita_touch_panel_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    'A', snapshot.attention_active, 8);
		fajita_touch_panel_field(fb.pixels, stride,
		    FAJITA_TOUCH_PANEL_PAD + 11u * FAJITA_TOUCH_PANEL_CELL_WIDTH, y,
		    'Q', (uint32_t)snapshot.posted_events, 8);
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		fajita_touch_panel_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    'E', snapshot.transport, 16);
		y += FAJITA_TOUCH_PANEL_LINE_HEIGHT;
		fajita_touch_panel_field(fb.pixels, stride, FAJITA_TOUCH_PANEL_PAD, y,
		    'R', snapshot.rpmh, 16);
	}
	return true;
}

#endif
