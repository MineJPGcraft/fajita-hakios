/* SPDX-License-Identifier: GPL-2.0-only
 * Append-only diagnostic ABI shared by the fajita kernel and resident loader.
 */
#ifndef FAJITA_TOUCH_LIVE_H
#define FAJITA_TOUCH_LIVE_H
#define FAJITA_LIVE_MAGIC3 0x334556494c554e58ULL
#define FAJITA_TOUCH_LIVE_MAGIC 0x314556494c505446ULL
#define FAJITA_TOUCH_TELEMETRY_MAGIC 0x31544d4c505446ULL
enum fajita_touch_bus_error {
	FT_BUS_OK = 0, FT_BUS_ARGUMENT = 1, FT_BUS_BUSY = 2,
	FT_BUS_SCL_TIMEOUT = 3, FT_BUS_WRITE_ADDRESS_NACK = 4,
	FT_BUS_REGISTER_NACK = 5, FT_BUS_DATA_NACK = 6,
	FT_BUS_READ_ADDRESS_NACK = 7, FT_BUS_STOP = 8
};
enum fajita_touch_phase {
	FT_PHASE_DISABLED = 0,
	FT_PHASE_INIT = 1,
	FT_PHASE_DEFERRED_ARMED = 2,
	FT_PHASE_CREATE_THREAD = 3,
	FT_PHASE_THREAD_ENTERED = 4,
	FT_PHASE_BEFORE_MAPPING = 5,
	FT_PHASE_MAP_GPIO = 6,
	FT_PHASE_SNAPSHOT_GPIO = 7,
	FT_PHASE_PINMUX = 8,
	FT_PHASE_TLMM_READ = 9,
	FT_PHASE_TLMM_WRITE = 10,
	FT_PHASE_PAGE_ZERO = 11,
	FT_PHASE_PDT = 12,
	FT_PHASE_CONFIGURE_SCAN = 13,
	FT_PHASE_SCANNING = 14,
	FT_PHASE_STOPPED = 15,
	FT_PHASE_SKIPPED = 16,
	FT_PHASE_PROBE_DONE = 17,
	FT_PHASE_PROBE_FAILED = 18,
	FT_PHASE_LAYOUT_DONE = 19,
	FT_PHASE_LAYOUT_FAILED = 20
};
/* state is one aligned 64-bit publication: phase in high32, detail in low32.
 * It owns a cache line; publishing cannot write back unrelated kernel data.
 */
struct fajita_touch_live {
	uint64_t magic, size;
	volatile uint64_t state;
	/* Independently atomic samples, not a coherent multi-field snapshot.
	 * transport: attempt[63:32], register[23:16], read[15],
	 * physical SDA/SCL[9:8], unavailable[10], error[7:0].
	 * Line levels are valid only when bit 10 is clear.
	 * attention_active: active slot mask[31:16], raw F12 attention[15:0]. */
	volatile uint64_t transport, reports;
	volatile uint64_t attention_active, posted_events;
	uint64_t telemetry_magic;
};
_Static_assert(sizeof(struct fajita_touch_live) == 64, "touch live ABI is one cache line");
extern struct fajita_touch_live fajita_touch_live;
#endif
