#!/usr/bin/env python3
"""Host-test the actual memory-only loader panel reader, without device access."""
from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/tests/fajita-touch-panel'
OUT.mkdir(parents=True, exist_ok=True)
loader = (ROOT / 'ref/dipper-xnu/work/hackios/xnuloader/loader.c').read_text()
schema = loader[loader.index('struct kernel_live {'):loader.index('/*\n * Battery state shared')]
range_start = loader.index('static int\nlive_range_contains(')
range_end = loader.index('/* For the record:', range_start)
range_reader = loader[range_start:range_end]

prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
'''
prelude += '#include "' + str(ROOT / 'devices/fajita/kernel/fajita_touch_live.h') + '"\n'
prelude += '#include "' + str(ROOT / 'devices/fajita/loader/fajita_touch_panel.h') + '"\n'

mocks = r'''
#define FB_WIDTH 420u
#define FB_HEIGHT 220u
#define FB_STRIDE_WORDS 427u
#define GUARD_WORDS 16u
#define FB_WORDS (FB_STRIDE_WORDS * FB_HEIGHT)
#define SENTINEL UINT32_C(0x8a57c36d)

static uint32_t framebuffer[GUARD_WORDS + FB_WORDS + GUARD_WORDS];
static struct {
    uint64_t base;
    uint32_t width, height, stride;
} SCREEN;
/* Mirror the loader's efi_gop_mode_info/efi_gop_mode field layouts. */
struct mock_mode_info {
    uint32_t version, width, height, pixel_format;
    uint32_t pixel_mask[4];
    uint32_t pixels_per_scanline;
};
struct mock_mode {
    uint32_t max_mode, mode;
    struct mock_mode_info *info;
    uint64_t info_size, fb_base, fb_size;
};
struct mock_gop { struct mock_mode *mode; };
static struct mock_mode_info info;
static struct mock_mode mode;
static struct mock_gop gop;
static struct mock_gop *GOP;
static uint32_t fajita_power_status;
static uint64_t fajita_power_rpmh_detail;
static uint64_t live_kernel_lo, live_kernel_hi;
static uint64_t expected_live_address, expected_touch_address;
static unsigned flushes, cleans, draws;
static bool draw_result;
static struct fajita_touch_panel_state drawn;
static struct fajita_touch_panel_surface drawn_surface;

static void flush_dcache(uint64_t start, uint64_t end)
{
    bool live_extension = expected_live_address != 0 &&
        start == expected_live_address + offsetof(struct kernel_live, magic3) &&
        end == expected_live_address + sizeof(struct kernel_live);
    bool touch_line = start == expected_touch_address &&
        end == expected_touch_address + sizeof(struct fajita_touch_live);
    assert(end >= start && end - start <= sizeof(struct fajita_touch_live));
    assert(live_extension || touch_line);
    assert(flushes < 2u);
    assert(flushes == 0u ? live_extension : touch_line);
    flushes++;
}

static void clean_dcache(uint64_t start, uint64_t end)
{
    uint64_t top = FB_HEIGHT - FAJITA_TOUCH_PANEL_BOTTOM_GAP - FAJITA_TOUCH_PANEL_HEIGHT;
    uint64_t first = (uint64_t)(uintptr_t)(framebuffer + GUARD_WORDS) +
        top * FB_STRIDE_WORDS * sizeof(uint32_t);
    assert(cleans < FAJITA_TOUCH_PANEL_HEIGHT);
    assert(start == first + (uint64_t)cleans * FB_STRIDE_WORDS * sizeof(uint32_t));
    assert(end == start + FAJITA_TOUCH_PANEL_WIDTH * sizeof(uint32_t));
    assert(start > SCREEN.base && end < SCREEN.base + FB_WORDS * sizeof(uint32_t));
    /* Each clean is a panel scanline; never clean a full framebuffer/stride. */
    assert(end - start == 1560u && end - start < SCREEN.stride);
    cleans++;
}

/* Retain the real renderer, recording its input before its first pixel write. */
static bool capture_panel_draw(struct fajita_touch_panel_surface *surface,
    const struct fajita_touch_panel_state *state)
{
    draws++;
    drawn = *state;
    drawn_surface = *surface;
    draw_result = fajita_touch_panel_draw(surface, state);
    return draw_result;
}
'''

reader = '#define fajita_touch_panel_draw capture_panel_draw\n'
reader += '#include "' + str(ROOT / 'devices/fajita/loader/fajita_touch_panel.inc') + '"\n'
reader += '#undef fajita_touch_panel_draw\n'

tests = r'''
static const struct fajita_touch_panel_state fallback = {
    UINT32_C(0xa5b6c7d8), UINT32_MAX, 0, 0, 0, UINT64_C(0x123456789abcdef0), 0, 0, false
};
static const struct fajita_touch_panel_state current = {
    UINT32_C(0xa5b6c7d8), UINT32_C(0x12345678), UINT32_C(0x9abcdef0),
    UINT64_C(0x1122334455667788), UINT64_C(0x99aabbccddeeff00),
    UINT64_C(0x123456789abcdef0), UINT64_C(0x1122334455667788),
    UINT64_C(0x99aabbccddeeff00), false
};

static void reset(void)
{
    for (size_t n = 0; n < sizeof(framebuffer) / sizeof(framebuffer[0]); n++) {
        framebuffer[n] = SENTINEL;
    }
    SCREEN.base = (uint64_t)(uintptr_t)(framebuffer + GUARD_WORDS);
    SCREEN.width = FB_WIDTH;
    SCREEN.height = FB_HEIGHT;
    SCREEN.stride = FB_STRIDE_WORDS * (uint32_t)sizeof(uint32_t);
    info = (struct mock_mode_info){
        .width = FB_WIDTH, .height = FB_HEIGHT, .pixel_format = 0,
        .pixels_per_scanline = FB_STRIDE_WORDS
    };
    mode = (struct mock_mode){.info = &info, .info_size = sizeof(info),
        .fb_base = SCREEN.base, .fb_size = FB_WORDS * sizeof(uint32_t)};
    gop.mode = &mode;
    GOP = &gop;
    fajita_power_status = fallback.power;
    fajita_power_rpmh_detail = fallback.rpmh;
    flushes = cleans = draws = 0;
    draw_result = false;
    drawn = (struct fajita_touch_panel_state){0};
    drawn_surface = (struct fajita_touch_panel_surface){0};
    expected_live_address = 0;
}

static void mapped_objects(const struct kernel_live *live,
    const struct fajita_touch_live *touch)
{
    uint64_t live_address = (uint64_t)(uintptr_t)live;
    uint64_t touch_address = (uint64_t)(uintptr_t)touch;
    live_kernel_lo = live_address < touch_address ? live_address : touch_address;
    live_kernel_hi = live_address + sizeof(*live);
    if (touch_address + sizeof(*touch) > live_kernel_hi) {
        live_kernel_hi = touch_address + sizeof(*touch);
    }
    expected_touch_address = touch_address;
}

static void assert_canvas(bool painted)
{
    uint32_t top = FB_HEIGHT - FAJITA_TOUCH_PANEL_BOTTOM_GAP - FAJITA_TOUCH_PANEL_HEIGHT;
    for (size_t n = 0; n < GUARD_WORDS; n++) {
        assert(framebuffer[n] == SENTINEL);
        assert(framebuffer[GUARD_WORDS + FB_WORDS + n] == SENTINEL);
    }
    for (uint32_t y = 0; y < FB_HEIGHT; y++) {
        for (uint32_t x = 0; x < FB_STRIDE_WORDS; x++) {
            uint32_t p = framebuffer[GUARD_WORDS + y * FB_STRIDE_WORDS + x];
            bool in_panel = x < FAJITA_TOUCH_PANEL_WIDTH &&
                y >= top && y < top + FAJITA_TOUCH_PANEL_HEIGHT;
            if (!painted || !in_panel) { assert(p == SENTINEL); }
            else { assert(p == 0u || p == UINT32_C(0x00ffffff)); }
        }
    }
    if (painted) {
        /* Independent P and '=' pixels prove actual rasterization occurred. */
        size_t at = GUARD_WORDS + (size_t)(top + 6u) * FB_STRIDE_WORDS;
        assert(framebuffer[at + 6u] == UINT32_C(0x00ffffff));
        assert(framebuffer[at + 18u] == 0u);
        assert(framebuffer[at + 24u] == 0u);
        assert(framebuffer[at + 6u * FB_STRIDE_WORDS + 24u] == UINT32_C(0x00ffffff));
        /* The fifth-line R has a diagonal leg absent from the P bitmap. */
        size_t rpmh_at = GUARD_WORDS + (size_t)(top + 114u) * FB_STRIDE_WORDS;
        assert(framebuffer[rpmh_at + 9u] == 0u);
        assert(framebuffer[rpmh_at + 12u] == UINT32_C(0x00ffffff));
    }
}

static void assert_state(const struct fajita_touch_panel_state *expected)
{
    assert(drawn.power == expected->power);
    assert(drawn.phase == expected->phase);
    assert(drawn.detail == expected->detail);
    assert(drawn.transport == expected->transport);
    assert(drawn.reports == expected->reports);
    assert(drawn.rpmh == expected->rpmh);
    assert(drawn.attention_active == expected->attention_active);
    assert(drawn.posted_events == expected->posted_events);
    assert(drawn.identity_probe == expected->identity_probe);
}

static void expect_update(const struct kernel_live *live, uint64_t slide,
    const struct fajita_touch_panel_state *expected, unsigned expected_flushes)
{
    expected_live_address = (uint64_t)(uintptr_t)live;
    fajita_touch_panel_update(live, slide);
    assert(draws == 1u && draw_result);
    assert(flushes == expected_flushes);
    assert(cleans == FAJITA_TOUCH_PANEL_HEIGHT);
    assert_state(expected);
    assert(drawn_surface.pixels == framebuffer + GUARD_WORDS);
    assert(drawn_surface.width == FB_WIDTH && drawn_surface.height == FB_HEIGHT);
    assert(drawn_surface.stride_bytes == FB_STRIDE_WORDS * sizeof(uint32_t));
    assert(drawn_surface.capacity_bytes == FB_WORDS * sizeof(uint32_t));
    assert_canvas(true);
}

static void test_states(void)
{
    _Alignas(64) struct fajita_touch_live touch = {
        .magic = FAJITA_TOUCH_LIVE_MAGIC, .size = sizeof(touch),
        .state = UINT64_C(0x123456789abcdef0),
        .transport = UINT64_C(0x1122334455667788),
        .reports = UINT64_C(0x99aabbccddeeff00),
        .attention_active = UINT64_C(0x1122334455667788),
        .posted_events = UINT64_C(0x99aabbccddeeff00),
        .telemetry_magic = FAJITA_TOUCH_TELEMETRY_MAGIC
    };
    struct kernel_live live = {
        .magic3 = FAJITA_LIVE_MAGIC3, .size = sizeof(live),
        .touch_live = (uint64_t)(uintptr_t)&touch
    };
    mapped_objects(&live, &touch);
    reset(); expect_update(&live, 0, &current, 2);
    struct fajita_touch_panel_state changed_power = current;
    changed_power.power = UINT32_C(0x00112233);
    changed_power.rpmh = UINT64_C(0xfedcba9876543210);
    reset(); fajita_power_status = changed_power.power;
    fajita_power_rpmh_detail = changed_power.rpmh;
    expect_update(&live, 0, &changed_power, 2);

    /* The loader's physical slide is a modular difference. A high kernel VA
     * normally wraps through zero when translated to its valid low PA. */
    const uint64_t kernel_va = UINT64_C(0xfffffff007004000);
    const uint64_t slide = (uint64_t)(uintptr_t)&touch - kernel_va;
    assert(slide > UINT64_MAX - kernel_va);
    assert(kernel_va + slide == (uint64_t)(uintptr_t)&touch);
    live.touch_live = kernel_va;
    reset(); expect_update(&live, slide, &current, 2);
    uint64_t outside = (live_kernel_hi + 127u) & ~UINT64_C(63);
    reset(); expect_update(&live, outside - kernel_va, &fallback, 1);

    live.touch_live = (uint64_t)(uintptr_t)&touch;
    touch.telemetry_magic = 0;
    struct fajita_touch_panel_state no_telemetry = current;
    no_telemetry.transport = no_telemetry.reports = 0;
    no_telemetry.attention_active = no_telemetry.posted_events = 0;
    reset(); expect_update(&live, 0, &no_telemetry, 2);
    touch.telemetry_magic = FAJITA_TOUCH_TELEMETRY_MAGIC ^ UINT64_C(1);
    reset(); expect_update(&live, 0, &no_telemetry, 2);
    touch.telemetry_magic = FAJITA_TOUCH_TELEMETRY_MAGIC;

    touch.magic = 0;
    reset(); expect_update(&live, 0, &fallback, 2);
    touch.magic = FAJITA_TOUCH_LIVE_MAGIC;
    touch.size = 24;
    reset(); expect_update(&live, 0, &fallback, 2);
    touch.size = sizeof(touch) - 8u;
    reset(); expect_update(&live, 0, &fallback, 2);
    touch.size = sizeof(touch);

    live.magic3 = 0;
    reset(); expect_update(&live, 0, &fallback, 1);
    live.magic3 = FAJITA_LIVE_MAGIC3;
    live.size = 96;
    reset(); expect_update(&live, 0, &fallback, 1);
    live.size = sizeof(live) + 8u;
    reset(); expect_update(&live, 0, &fallback, 1);
    live.size = sizeof(live);
    live.touch_live = 0;
    reset(); expect_update(&live, 0, &fallback, 1);
    live.touch_live = (uint64_t)(uintptr_t)&touch + 1u;
    reset(); expect_update(&live, 0, &fallback, 1);
    live.touch_live = outside;
    reset(); expect_update(&live, 0, &fallback, 1);

    reset(); expect_update(NULL, 0, &fallback, 0);
    reset(); expect_update((const struct kernel_live *)(uintptr_t)4096, 0, &fallback, 0);
    live.touch_live = (uint64_t)(uintptr_t)&touch;
    live_kernel_lo = (uint64_t)(uintptr_t)&live;
    live_kernel_hi = live_kernel_lo + sizeof(live) - 1u;
    reset(); expect_update(&live, 0, &fallback, 0);
    live_kernel_hi = live_kernel_lo - 1u;
    reset(); expect_update(&live, 0, &fallback, 0);

    /* An actual 96-byte old object must be rejected before any extension
     * read/flush; ASan would catch accidental access beyond this allocation. */
    _Alignas(8) uint64_t old_live[12] = {0};
    live_kernel_lo = (uint64_t)(uintptr_t)old_live;
    live_kernel_hi = live_kernel_lo + sizeof(old_live);
    reset(); expect_update((const struct kernel_live *)(const void *)old_live,
        0, &fallback, 0);

    /* Include the whole unaligned object in range, so only alignment rejects. */
    _Alignas(64) unsigned char unaligned[sizeof(live) + 8u];
    memcpy(unaligned + 1, &live, sizeof(live));
    live_kernel_lo = (uint64_t)(uintptr_t)unaligned;
    live_kernel_hi = live_kernel_lo + sizeof(unaligned);
    reset(); expect_update((const struct kernel_live *)(const void *)(unaligned + 1),
        0, &fallback, 0);
}

static void expect_no_paint(unsigned expected_draws)
{
    fajita_touch_panel_update(NULL, 0);
    assert(draws == expected_draws && !draw_result && cleans == 0u && flushes == 0u);
    assert_canvas(false);
}

static void test_framebuffer_gates(void)
{
    reset(); info.pixel_format = 0; expect_update(NULL, 0, &fallback, 0);
    reset(); info.pixel_format = 1; expect_update(NULL, 0, &fallback, 0);
    reset(); GOP = NULL; expect_no_paint(0);
    reset(); gop.mode = NULL; expect_no_paint(0);
    reset(); mode.info = NULL; expect_no_paint(0);
    reset(); info.pixel_format = 2; expect_no_paint(0);
    reset(); info.pixel_format = 3; expect_no_paint(0);
    reset(); info.pixel_format = UINT32_MAX; expect_no_paint(0);
    reset(); mode.fb_base += 4u; expect_no_paint(0);
    reset(); mode.fb_size--; expect_no_paint(1);
    reset(); mode.fb_size = 0; expect_no_paint(1);
    reset(); mode.fb_size = UINT64_MAX; expect_no_paint(1);
    reset(); SCREEN.base = mode.fb_base = 0; expect_no_paint(1);
    reset(); SCREEN.base++; mode.fb_base = SCREEN.base; expect_no_paint(1);
    reset(); SCREEN.width = FAJITA_TOUCH_PANEL_WIDTH - 1u; expect_no_paint(1);
    reset(); SCREEN.width = 4097; expect_no_paint(1);
    reset(); SCREEN.height = FAJITA_TOUCH_PANEL_HEIGHT + FAJITA_TOUCH_PANEL_BOTTOM_GAP - 1u;
    expect_no_paint(1);
    reset(); SCREEN.height = 8193; expect_no_paint(1);
    reset(); SCREEN.stride = FB_WIDTH * 4u - 4u; expect_no_paint(1);
    reset(); SCREEN.stride++; expect_no_paint(1);
    reset(); SCREEN.base = mode.fb_base = UINT64_MAX - 3u; expect_no_paint(1);
}

int main(void)
{
    test_states();
    test_framebuffer_gates();
    puts("fajita panel reader: actual ABI/range/modular-slide/telemetry gates and bounded real raster/cache spans passed");
    return 0;
}
'''

source = OUT / 'panel-reader.c'
source.write_text(prelude + schema + mocks + range_reader + reader + tests)
binary = OUT / 'panel-reader'
subprocess.run([
    os.environ.get('CC', 'clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
    '-Wpedantic', '-Wconversion', '-Wshadow', '-fsanitize=address,undefined',
    '-fno-omit-frame-pointer', '-g', str(source), '-o', str(binary)
], check=True)
env = dict(os.environ)
# LeakSanitizer is unavailable under the host's ptrace sandbox; the fixture
# uses static/automatic storage, so there are no allocations to leak.
env['ASAN_OPTIONS'] = env.get('ASAN_OPTIONS', '') + ':detect_leaks=0:halt_on_error=1'
env['UBSAN_OPTIONS'] = env.get('UBSAN_OPTIONS', '') + ':halt_on_error=1'
subprocess.run([str(binary)], check=True, env=env)
