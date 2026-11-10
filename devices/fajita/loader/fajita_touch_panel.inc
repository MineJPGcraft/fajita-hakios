/* Opt-in, memory-only status display. No firmware console/USB service. */
#include "fajita_touch_panel.h"
_Static_assert(FAJITA_TOUCH_PANEL_LAYOUT_DONE == FT_PHASE_LAYOUT_DONE &&
    FAJITA_TOUCH_PANEL_LAYOUT_FAILED == FT_PHASE_LAYOUT_FAILED, "paired layout phases");

static void
fajita_touch_panel_update(const struct kernel_live *live, uint64_t slide)
{
	struct fajita_touch_panel_state state = {
		.power = fajita_power_status, .phase = UINT32_MAX, .rpmh = fajita_power_rpmh_detail
	};
	if (live && !((uintptr_t)live & 7u) &&
	    live_range_contains((uint64_t)(uintptr_t)live, sizeof(*live))) {
		flush_dcache((uint64_t)(uintptr_t)&live->magic3, (uint64_t)(uintptr_t)&live->touch_live + 8);
		if (live->magic3 == FAJITA_LIVE_MAGIC3 && live->size == sizeof(*live) && live->touch_live) {
			uint64_t pa = live->touch_live + slide;
			if (!(pa & 63u) && live_range_contains(pa, sizeof(struct fajita_touch_live))) {
				flush_dcache(pa, pa + sizeof(struct fajita_touch_live));
				const volatile struct fajita_touch_live *touch = (const volatile void *)(uintptr_t)pa;
				if (touch->magic == FAJITA_TOUCH_LIVE_MAGIC && touch->size == sizeof(*touch)) {
					uint64_t sample = touch->state;
					state.phase = (uint32_t)(sample >> 32); state.detail = (uint32_t)sample;
						state.identity_probe = state.phase == FT_PHASE_PROBE_DONE ||
						    state.phase == FT_PHASE_PROBE_FAILED;
					if (touch->telemetry_magic == FAJITA_TOUCH_TELEMETRY_MAGIC) {
						state.transport = touch->transport; state.reports = touch->reports;
						state.attention_active = touch->attention_active;
						state.posted_events = touch->posted_events;
					}
				}
			}
		}
	}
	/* SCREEN was selected from GOP before handoff. Its backing extent must
	 * still cover the surface; never invoke GOP itself while XNU is running. */
	if (!GOP || !GOP->mode || !GOP->mode->info ||
	    GOP->mode->info->pixel_format > 1u || GOP->mode->fb_base != SCREEN.base) { return; }
	struct fajita_touch_panel_surface fb = {
		.pixels = (volatile void *)(uintptr_t)SCREEN.base,
		.width = SCREEN.width, .height = SCREEN.height, .stride_bytes = SCREEN.stride,
		.capacity_bytes = GOP->mode->fb_size
	};
	if (fajita_touch_panel_draw(&fb, &state)) {
		uint64_t begin = SCREEN.base + (uint64_t)(SCREEN.height - FAJITA_TOUCH_PANEL_HEIGHT -
		    FAJITA_TOUCH_PANEL_BOTTOM_GAP) * SCREEN.stride;
		for (unsigned row = 0; row < FAJITA_TOUCH_PANEL_HEIGHT; row++) {
			uint64_t start = begin + (uint64_t)row * SCREEN.stride;
			clean_dcache(start, start + FAJITA_TOUCH_PANEL_WIDTH * 4u);
		}
	}
}
