/* Host-only tests of the actual transaction helper. No MMIO/USB access. */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "../devices/fajita/loader/fajita_rpmh_transaction.h"

#define WORDS (0x3000u / 4u)
#define OTHER_IRQ (1u << 9)
#define RESOURCE 0x40000u
struct write_event { uint32_t offset, value; };
struct mock {
	uint32_t reg[WORDS], original[WORDS];
	struct write_event writes[96];
	unsigned nwrites, nreads, delays, irq_polls, command_polls, trigger_at, selected;
	unsigned ready_after, fail_write_at, fail_delay_at;
	unsigned hardware_ticks, command_done_at[3], engine_idle_at, irq_at;
	unsigned trigger_accept_at;
	bool triggered, completed, no_completion, keep_busy, fault_active;
	bool sticky_fault, ignore_irq_clear, suppress_irq, accepted;
	bool observed_busy, observed_incomplete, observed_irq;
	int bad_command;
	uint32_t bad_command_status, fault_offset;
	uint32_t mutate_offset, mutate_value;
	unsigned mutate_on_read, mutation_reads;
};
static uint32_t slot_offset(unsigned index, uint32_t offset)
{
	return FAJITA_RPMH_TCS_STRIDE * index + offset;
}
static void init(struct mock *m)
{
	memset(m, 0, sizeof(*m));
	m->ready_after = 1; m->fail_write_at = UINT32_MAX;
	m->fail_delay_at = UINT32_MAX; m->bad_command = -1;
	m->selected = FAJITA_RPMH_NO_TCS;
	m->reg[FAJITA_RPMH_IRQ_ENABLE / 4] = 0x1073u;
	m->reg[FAJITA_RPMH_IRQ_STATUS / 4] = OTHER_IRQ;
	for (unsigned slot = 0; slot < 8; slot++) {
		m->reg[slot_offset(slot, FAJITA_RPMH_STATUS) / 4] = 1;
		for (unsigned cmd = 0; cmd < 3; cmd++) {
			for (unsigned field = 0; field < 3; field++) {
				m->reg[slot_offset(slot, FAJITA_RPMH_MSGID) / 4 + cmd * 5 + field] =
				    0x80000000u | slot * 0x100u | cmd * 0x10u | field;
			}
		}
	}
	memcpy(m->original, m->reg, sizeof(m->reg));
}
/* Trigger submission and acceptance are distinct. A posted trigger may leave
 * old completion flags and idle visible for several delay(10) ticks, or never
 * be accepted at all. Neither acceptance nor progress is driven by MMIO reads.
 * Separate command, engine and IRQ schedules permit misleading partial states.
 */
static void hardware_progress(struct mock *m)
{
	assert(m->triggered && m->selected < 2);
	if (!m->accepted) {
		if (m->hardware_ticks < m->trigger_accept_at) { return; }
		m->accepted = true;
		uint32_t slot = slot_offset(m->selected, 0);
		m->reg[(slot + FAJITA_RPMH_STATUS) / 4] = 0;
		for (unsigned n = 0; n < 3; n++) {
			m->reg[(slot + FAJITA_RPMH_CMD_STATUS + n * FAJITA_RPMH_CMD_STRIDE) / 4] = 0;
		}
	}
	bool all_responded = true;
	for (unsigned n = 0; n < 3; n++) {
		unsigned due = m->command_done_at[n] ? m->command_done_at[n] : m->ready_after;
		if (m->no_completion || m->hardware_ticks < due) { all_responded = false; continue; }
		m->reg[(slot_offset(m->selected, FAJITA_RPMH_CMD_STATUS) +
		    n * FAJITA_RPMH_CMD_STRIDE) / 4] =
		    (int)n == m->bad_command ? m->bad_command_status : FAJITA_RPMH_COMMAND_OK;
	}
	m->completed = all_responded;
	unsigned idle = m->engine_idle_at ? m->engine_idle_at : m->ready_after;
	if (!m->keep_busy && m->hardware_ticks >= idle) {
		m->reg[slot_offset(m->selected, FAJITA_RPMH_STATUS) / 4] = 1;
	}
	if (!m->suppress_irq && ((m->irq_at && m->hardware_ticks >= m->irq_at) ||
	    (!m->irq_at && all_responded))) { m->reg[FAJITA_RPMH_IRQ_STATUS / 4] |= 1u << m->selected; }
}
static void assert_safe_cleanup(const struct mock *m)
{
	assert(m->triggered && m->accepted && m->selected < 2);
	assert(m->reg[slot_offset(m->selected, FAJITA_RPMH_STATUS) / 4] != 0);
	for (unsigned n = 0; n < 3; n++) {
		uint32_t status = m->reg[(slot_offset(m->selected, FAJITA_RPMH_CMD_STATUS) +
		    n * FAJITA_RPMH_CMD_STRIDE) / 4];
		assert((status & FAJITA_RPMH_COMMAND_OK) == FAJITA_RPMH_COMMAND_OK);
	}
}
static uint32_t rd(void *ctx, uint32_t offset)
{
	struct mock *m = ctx;
	assert((offset & 3u) == 0 && offset / 4 < WORDS);
	m->nreads++;
	if (m->mutate_on_read && offset == m->mutate_offset &&
	    ++m->mutation_reads == m->mutate_on_read) { m->reg[offset / 4] = m->mutate_value; }
	if (offset == FAJITA_RPMH_IRQ_STATUS && m->triggered && !m->completed) {
		m->irq_polls++;
	}
	if (m->triggered && offset == slot_offset(m->selected, FAJITA_RPMH_CMD_STATUS)) { m->command_polls++; }
	uint32_t result = m->reg[offset / 4];
	if (m->fault_active && offset == m->fault_offset) { result ^= 1u; }
	if (m->triggered) {
		uint32_t first_status = slot_offset(m->selected, FAJITA_RPMH_CMD_STATUS);
		if (offset == slot_offset(m->selected, FAJITA_RPMH_STATUS) && !result) { m->observed_busy = true; }
		if (offset >= first_status && (offset - first_status) / FAJITA_RPMH_CMD_STRIDE < 3 &&
		    (offset - first_status) % FAJITA_RPMH_CMD_STRIDE == 0 &&
		    (result & FAJITA_RPMH_COMMAND_OK) != FAJITA_RPMH_COMMAND_OK) { m->observed_incomplete = true; }
		if (offset == FAJITA_RPMH_IRQ_STATUS && (result & (1u << m->selected))) { m->observed_irq = true; }
	}
	return result;
}
static bool command_field(unsigned index, uint32_t offset)
{
	uint32_t slot = slot_offset(index, 0);
	if (offset < slot + FAJITA_RPMH_MSGID) { return false; }
	uint32_t relative = offset - slot - FAJITA_RPMH_MSGID;
	return relative / FAJITA_RPMH_CMD_STRIDE < 3 &&
	    relative % FAJITA_RPMH_CMD_STRIDE <= 8 && (relative & 3u) == 0;
}
static void wr(void *ctx, uint32_t offset, uint32_t value)
{
	struct mock *m = ctx;
	assert((offset & 3u) == 0 && offset / 4 < WORDS && m->nwrites < 96);
	unsigned at = m->nwrites++;
	m->writes[at] = (struct write_event){offset, value};
	if (m->fault_active && offset == m->fault_offset && !m->sticky_fault) {
		m->fault_active = false;
	}
	if (at == m->fail_write_at) {
		m->fault_offset = offset; m->fault_active = true;
	}
	if (offset == FAJITA_RPMH_IRQ_CLEAR) {
		assert(m->selected < 2 && value == (1u << m->selected));
		assert_safe_cleanup(m);
		if (at != m->fail_write_at && !m->ignore_irq_clear) {
			m->reg[FAJITA_RPMH_IRQ_STATUS / 4] &= ~value;
		}
		return;
	}
	if (offset != FAJITA_RPMH_IRQ_ENABLE) {
		unsigned index = offset / FAJITA_RPMH_TCS_STRIDE;
		uint32_t within = offset % FAJITA_RPMH_TCS_STRIDE;
		assert(index < 2); /* Never write SLEEP/WAKE/CONTROL TCSes. */
		assert(within == FAJITA_RPMH_CONTROL || within == FAJITA_RPMH_WAIT ||
		    within == FAJITA_RPMH_CMD_ENABLE || command_field(index, offset));
		if (m->selected == FAJITA_RPMH_NO_TCS) { m->selected = index; }
		assert(m->selected == index);
		/* A partial response or idle engine alone never permits cleanup. */
		if (m->triggered) { assert_safe_cleanup(m); }
		if (!m->triggered && command_field(index, offset)) {
			/* Both checked quiesce writes precede every first command write.
			 * No command can be overwritten under inherited AMC enable/trigger.
			 */
			assert(m->reg[slot_offset(index, FAJITA_RPMH_CONTROL) / 4] == 0);
			assert(!(m->reg[FAJITA_RPMH_IRQ_ENABLE / 4] & (1u << index)));
			assert(at >= 3 && m->writes[1].offset == slot_offset(index, FAJITA_RPMH_CONTROL));
			assert(m->writes[1].value ==
			    (m->original[slot_offset(index, FAJITA_RPMH_CONTROL) / 4] & ~FAJITA_RPMH_TRIGGER));
			assert(m->writes[2].offset == slot_offset(index, FAJITA_RPMH_CONTROL) && !m->writes[2].value);
		}
	}
	m->reg[offset / 4] = value;
	if (offset % FAJITA_RPMH_TCS_STRIDE == FAJITA_RPMH_CONTROL &&
	    (value & FAJITA_RPMH_TRIGGER)) {
		unsigned index = offset / FAJITA_RPMH_TCS_STRIDE;
		assert(!m->triggered && index < 2);
		uint32_t slot = slot_offset(index, 0);
		assert(value == (FAJITA_RPMH_ENABLE | FAJITA_RPMH_TRIGGER));
		assert(!(m->reg[FAJITA_RPMH_IRQ_ENABLE / 4] & (1u << index)));
		assert(m->reg[(slot + FAJITA_RPMH_WAIT) / 4] == 7);
		assert(m->reg[(slot + FAJITA_RPMH_CMD_ENABLE) / 4] == 7);
		static const uint32_t data[3] = {3008u, 1u, 7u};
		for (unsigned n = 0; n < 3; n++) {
			uint32_t command = slot + n * FAJITA_RPMH_CMD_STRIDE;
			assert(m->reg[(command + FAJITA_RPMH_MSGID) / 4] == 0x10108u);
			assert(m->reg[(command + FAJITA_RPMH_ADDR) / 4] == RESOURCE + n * 4);
			assert(m->reg[(command + FAJITA_RPMH_DATA) / 4] == data[n]);
		}
		m->triggered = true; m->trigger_at = at; m->selected = index;
		/* At tick zero acceptance can be immediate, delayed or absent. */
		hardware_progress(m);
	}
}
static bool delay_us(void *ctx, unsigned us)
{
	struct mock *m = ctx;
	assert(us == 1 || us == 10);
	bool success = m->delays++ != m->fail_delay_at;
	if (success && us == 10 && m->triggered) { m->hardware_ticks++; hardware_progress(m); }
	return success;
}
static const struct fajita_rpmh_ops ops = {rd, wr, delay_us};

static void assert_restored(const struct mock *m, unsigned chosen)
{
	assert(m->reg[FAJITA_RPMH_IRQ_ENABLE / 4] == m->original[FAJITA_RPMH_IRQ_ENABLE / 4]);
	assert(m->reg[FAJITA_RPMH_IRQ_STATUS / 4] == m->original[FAJITA_RPMH_IRQ_STATUS / 4]);
	for (unsigned slot = 0; slot < 8; slot++) {
		uint32_t base = slot_offset(slot, 0);
		uint32_t control = m->original[(base + FAJITA_RPMH_CONTROL) / 4];
		if (slot == chosen) { control &= ~FAJITA_RPMH_TRIGGER; }
		assert(m->reg[(base + FAJITA_RPMH_CONTROL) / 4] == control);
		assert(m->reg[(base + FAJITA_RPMH_WAIT) / 4] == m->original[(base + FAJITA_RPMH_WAIT) / 4]);
		assert(m->reg[(base + FAJITA_RPMH_CMD_ENABLE) / 4] == m->original[(base + FAJITA_RPMH_CMD_ENABLE) / 4]);
		for (unsigned n = 0; n < 3; n++) {
			for (unsigned field = 0; field < 3; field++) {
				unsigned word = (base + FAJITA_RPMH_MSGID + n * FAJITA_RPMH_CMD_STRIDE) / 4 + field;
				assert(m->reg[word] == m->original[word]);
			}
		}
	}
	for (unsigned n = 0; n < m->nwrites; n++) {
		if (m->writes[n].offset == FAJITA_RPMH_IRQ_CLEAR) {
			assert(m->writes[n].value == (1u << chosen));
		}
	}
}
static void test_success(void)
{
	static const uint32_t states[] = {0, FAJITA_RPMH_ENABLE,
	    FAJITA_RPMH_ENABLE | FAJITA_RPMH_TRIGGER};
	for (unsigned index = 0; index < 2; index++) {
		for (unsigned state = 0; state < sizeof(states) / sizeof(states[0]); state++) {
			for (unsigned polls = 1; polls < 4; polls++) {
				struct mock m; init(&m);
				if (index) {
					m.reg[FAJITA_RPMH_STATUS / 4] = 0;
					m.reg[FAJITA_RPMH_CMD_ENABLE / 4] = 1;
					m.reg[FAJITA_RPMH_ADDR / 4] = 0x70000;
				}
				m.reg[slot_offset(index, FAJITA_RPMH_CONTROL) / 4] = states[state];
				memcpy(m.original, m.reg, sizeof(m.reg));
				m.ready_after = polls;
				unsigned chosen = 99;
				struct fajita_rpmh_diagnostic diag;
				assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_READY);
				assert(diag.reason == FAJITA_RPMH_REASON_NONE && diag.slot == index);
				assert(chosen == index && m.triggered && m.completed && m.irq_polls == polls);
				assert(m.delays == polls && m.command_polls == polls + 1);
				assert_restored(&m, chosen);
				assert(m.nwrites == 32);
				/* Explicit ordering around trigger and cleanup. */
				assert(m.writes[1].value == (states[state] & ~FAJITA_RPMH_TRIGGER));
				assert(m.writes[2].value == 0 && m.writes[3].offset == slot_offset(index, FAJITA_RPMH_MSGID));
				assert(m.writes[14].value == FAJITA_RPMH_ENABLE);
				assert(m.writes[15].value == (FAJITA_RPMH_ENABLE | FAJITA_RPMH_TRIGGER));
				assert(m.writes[16].value == FAJITA_RPMH_ENABLE && m.writes[17].value == 0);
			}
		}
	}
}
static void test_selection_guards(void)
{
	static const uint32_t states[] = {0, FAJITA_RPMH_ENABLE,
	    FAJITA_RPMH_ENABLE | FAJITA_RPMH_TRIGGER};
	static const uint32_t offsets[] = {FAJITA_RPMH_STATUS, FAJITA_RPMH_CONTROL,
	    FAJITA_RPMH_CMD_ENABLE, FAJITA_RPMH_WAIT, FAJITA_RPMH_IRQ_STATUS};
	static const uint32_t flags[] = {FAJITA_RPMH_REJECT_STATUS, FAJITA_RPMH_REJECT_CONTROL,
	    FAJITA_RPMH_REJECT_CMD_ENABLE, FAJITA_RPMH_REJECT_WAIT, FAJITA_RPMH_REJECT_IRQ};
	for (unsigned state = 0; state < sizeof(states) / sizeof(states[0]); state++) {
		for (unsigned n = 0; n < sizeof(offsets) / sizeof(offsets[0]); n++) {
			struct mock m; init(&m);
			for (unsigned index = 0; index < 2; index++) {
				m.reg[slot_offset(index, FAJITA_RPMH_CONTROL) / 4] = states[state];
				uint32_t off = offsets[n] == FAJITA_RPMH_IRQ_STATUS ?
				    offsets[n] : slot_offset(index, offsets[n]);
				m.reg[off / 4] = offsets[n] == FAJITA_RPMH_STATUS ? 0 :
				    offsets[n] == FAJITA_RPMH_IRQ_STATUS ? OTHER_IRQ | 3u : 1;
			}
			for (unsigned index = 0; index < 2; index++) {
				assert(fajita_rpmh_slot_rejections(&ops, &m, index) == flags[n]);
			}
			unsigned chosen = 99;
			assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_SKIP);
			assert(chosen == FAJITA_RPMH_NO_TCS && !m.nwrites && !m.triggered);
		}
	}
}
static void test_pretrigger_failures(void)
{
	static const uint32_t states[] = {0, FAJITA_RPMH_ENABLE,
	    FAJITA_RPMH_ENABLE | FAJITA_RPMH_TRIGGER};
	for (unsigned state = 0; state < sizeof(states) / sizeof(states[0]); state++) {
		for (unsigned fault = 0; fault < 15; fault++) {
			struct mock m; init(&m); m.fail_write_at = fault;
			m.reg[FAJITA_RPMH_CONTROL / 4] = states[state];
			memcpy(m.original, m.reg, sizeof(m.reg));
			unsigned chosen = 99;
			struct fajita_rpmh_diagnostic diag;
			enum fajita_rpmh_result result = fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag);
			assert(chosen == 0 && !m.triggered);
			assert(m.delays == FAJITA_RPMH_SYNC_POLLS - 1);
			/* Failed CONTROL readback is uncertain (the mock exposes an unknown
			 * bit), and must hold. Other one-shot failures can roll back safely.
			 */
			if (fault == 1 || fault == 2 || fault == 14) {
				assert(result == FAJITA_RPMH_FATAL && diag.reason == FAJITA_RPMH_REASON_RESTORE_FATAL);
				assert(!(m.reg[FAJITA_RPMH_IRQ_ENABLE / 4] & 1));
			} else {
				assert(result == FAJITA_RPMH_SKIP && diag.reason == FAJITA_RPMH_REASON_PRETRIGGER_ROLLBACK);
				assert(diag.slot == 0 && diag.offset == m.writes[fault].offset &&
				    diag.value == (m.writes[fault].value ^ 1u));
				assert_restored(&m, chosen);
			}
		}
	}
	/* Delay failure before trigger still restores state, if rollback succeeds. */
	struct mock m; init(&m); m.fail_write_at = 3; m.fail_delay_at = 0;
	unsigned chosen;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_SKIP);
	assert(!m.triggered && m.delays == 1);
	assert_restored(&m, chosen);
	/* A failed rollback is FATAL even when no trigger was sent. */
	init(&m); m.fail_write_at = 3; m.sticky_fault = true;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_FATAL);
	assert(!m.triggered && !(m.reg[FAJITA_RPMH_IRQ_ENABLE / 4] & 1));
}
static void test_inflight_resource_conflicts(void)
{
	for (unsigned index = 0; index < 2; index++) {
		for (unsigned cmd = 0; cmd < FAJITA_RPMH_MAX_COMMANDS; cmd++) {
			for (unsigned reg = 0; reg < 3; reg++) {
				struct mock m; init(&m);
				m.reg[slot_offset(index, FAJITA_RPMH_STATUS) / 4] = 0;
				m.reg[slot_offset(index, FAJITA_RPMH_CMD_ENABLE) / 4] = 1u << cmd;
				m.reg[(slot_offset(index, FAJITA_RPMH_ADDR) + cmd * FAJITA_RPMH_CMD_STRIDE) / 4] = RESOURCE + reg * 4;
				unsigned chosen = 99;
				assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_SKIP);
				assert(chosen == FAJITA_RPMH_NO_TCS && !m.nwrites && !m.triggered);
			}
		}
		struct mock m; unsigned chosen; init(&m);
		m.reg[slot_offset(index, FAJITA_RPMH_STATUS) / 4] = 0;
		assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_SKIP && !m.nwrites);
		init(&m);m.reg[slot_offset(index, FAJITA_RPMH_CMD_ENABLE) / 4] = 1u << 16;
		assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_SKIP && !m.nwrites);
	}
}
static void assert_inflight_preserved(const struct mock *m, unsigned chosen)
{
	assert(m->triggered && m->nwrites == m->trigger_at + 1);
	assert(!(m->reg[FAJITA_RPMH_IRQ_ENABLE / 4] & (1u << chosen)));
	assert(m->reg[slot_offset(chosen, FAJITA_RPMH_CMD_ENABLE) / 4] == 7);
	assert(m->reg[slot_offset(chosen, FAJITA_RPMH_WAIT) / 4] == 7);
	assert((m->reg[FAJITA_RPMH_IRQ_STATUS / 4] & OTHER_IRQ) != 0);
}
static void test_posttrigger_failures(void)
{
	static const uint32_t states[] = {0, FAJITA_RPMH_ENABLE,
	    FAJITA_RPMH_ENABLE | FAJITA_RPMH_TRIGGER};
	struct mock m; unsigned chosen;
	for (unsigned state = 0; state < sizeof(states) / sizeof(states[0]); state++) {
		init(&m); m.no_completion = true;
		m.reg[FAJITA_RPMH_CONTROL / 4] = states[state];
		memcpy(m.original, m.reg, sizeof(m.reg));
		struct fajita_rpmh_diagnostic diag;
		assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_FATAL);
		assert(diag.reason == FAJITA_RPMH_REASON_POSTTRIGGER_FATAL && diag.slot == 0);
		assert(m.irq_polls == FAJITA_RPMH_COMPLETION_POLLS && m.command_polls == FAJITA_RPMH_COMPLETION_POLLS);
		assert(m.delays == FAJITA_RPMH_COMPLETION_POLLS - 1);
		assert_inflight_preserved(&m, chosen);
	}
	init(&m); m.ready_after = 2; m.fail_delay_at = 0;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_FATAL);
	assert(m.delays == 1 && m.irq_polls == 1 && m.command_polls == 1);
	assert_inflight_preserved(&m, chosen);
	for (unsigned command = 0; command < 3; command++) {
		static const uint32_t bad[] = {0, 1u << 8, 1u << 16};
		for (unsigned n = 0; n < sizeof(bad) / sizeof(bad[0]); n++) {
			init(&m); m.bad_command = (int)command; m.bad_command_status = bad[n];
			assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_FATAL);
			assert_inflight_preserved(&m, chosen);
		}
	}
	init(&m); m.keep_busy = true;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_FATAL);
	assert_inflight_preserved(&m, chosen);
}
static void test_cleanup_failures(void)
{
	for (unsigned fault = 16; fault < 32; fault++) {
		struct mock m; init(&m); m.fail_write_at = fault;
		unsigned chosen;
		assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_FATAL);
		assert(m.triggered && m.completed);
		assert(!(m.reg[FAJITA_RPMH_IRQ_ENABLE / 4] & (1u << chosen)));
		assert(m.reg[FAJITA_RPMH_IRQ_STATUS / 4] & OTHER_IRQ);
	}
}
static void test_completion_without_irq_dependency(void)
{
	struct mock m; unsigned chosen; struct fajita_rpmh_diagnostic diag;
	/* No IRQ status is ever asserted, but all commands and the engine finish. */
	init(&m); m.suppress_irq = true; m.ready_after = 2;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_READY);
	assert(m.hardware_ticks == 2 && m.command_polls == 3 && m.irq_polls == 2);
	assert_restored(&m, chosen);
	/* An early IRQ, early engine idle and first command done must not allow
	 * cleanup while either of the other two command responses is outstanding.
	 */
	init(&m); m.command_done_at[0] = 1; m.command_done_at[1] = 3;
	m.command_done_at[2] = 4; m.engine_idle_at = 1; m.irq_at = 1;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_READY);
	assert(m.hardware_ticks == 4 && m.command_polls == 5 && m.irq_polls == 4);
	assert_restored(&m, chosen);
	init(&m); m.command_done_at[0] = 1; m.command_done_at[1] = UINT32_MAX;
	m.command_done_at[2] = UINT32_MAX; m.engine_idle_at = 1; m.irq_at = 1;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_FATAL);
	assert(diag.reason == FAJITA_RPMH_REASON_POSTTRIGGER_FATAL &&
	    diag.offset == FAJITA_RPMH_CMD_STATUS + FAJITA_RPMH_CMD_STRIDE && !diag.value);
	assert(m.reg[FAJITA_RPMH_STATUS / 4] && m.reg[FAJITA_RPMH_CMD_STATUS / 4] == FAJITA_RPMH_COMMAND_OK);
	assert_inflight_preserved(&m, chosen);
	/* All command responses may precede engine idle; wait, without cleanup. */
	init(&m); m.engine_idle_at = 4;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_READY);
	assert(m.hardware_ticks == 4 && m.command_polls == 5);
	assert_restored(&m, chosen);
	init(&m); m.keep_busy = true;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_FATAL);
	assert(diag.reason == FAJITA_RPMH_REASON_POSTTRIGGER_FATAL && diag.offset == FAJITA_RPMH_STATUS && !diag.value);
	assert_inflight_preserved(&m, chosen);
	/* A late IRQ is likewise not a completion prerequisite. */
	init(&m); m.irq_at = 8;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_READY);
	assert(m.hardware_ticks == 1 && !(m.reg[FAJITA_RPMH_IRQ_STATUS / 4] & (1u << chosen)));
	assert_restored(&m, chosen);
	/* With immediate acceptance, old flags are replaced before current
	 * responses arrive. Delayed acceptance is tested separately below.
	 */
	init(&m); m.ready_after = 3;
	for (unsigned n = 0; n < 3; n++) {
		m.reg[(FAJITA_RPMH_CMD_STATUS + n * FAJITA_RPMH_CMD_STRIDE) / 4] = FAJITA_RPMH_COMMAND_OK;
	}
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_READY);
	assert(m.hardware_ticks == 3 && m.command_polls == 4);
	init(&m); m.no_completion = true;
	for (unsigned n = 0; n < 3; n++) {
		m.reg[(FAJITA_RPMH_CMD_STATUS + n * FAJITA_RPMH_CMD_STRIDE) / 4] = FAJITA_RPMH_COMMAND_OK;
	}
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_FATAL);
	assert(!m.reg[FAJITA_RPMH_CMD_STATUS / 4] && m.command_polls == FAJITA_RPMH_COMPLETION_POLLS);
	assert_inflight_preserved(&m, chosen);
	/* Exactly the last permitted poll succeeds; one tick later times out. */
	init(&m); m.ready_after = FAJITA_RPMH_COMPLETION_POLLS - 1;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_READY);
	assert(m.command_polls == FAJITA_RPMH_COMPLETION_POLLS && m.delays == FAJITA_RPMH_COMPLETION_POLLS - 1);
	assert_restored(&m, chosen);
	init(&m); m.ready_after = FAJITA_RPMH_COMPLETION_POLLS;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_FATAL);
	assert(m.command_polls == FAJITA_RPMH_COMPLETION_POLLS && m.delays == FAJITA_RPMH_COMPLETION_POLLS - 1);
	assert_inflight_preserved(&m, chosen);
}
static void seed_old_completion(struct mock *m)
{
	for (unsigned n = 0; n < 3; n++) {
		m->reg[(FAJITA_RPMH_CMD_STATUS + n * FAJITA_RPMH_CMD_STRIDE) / 4] = FAJITA_RPMH_COMMAND_OK;
	}
}
static void test_completion_freshness(void)
{
	struct mock m; unsigned chosen; struct fajita_rpmh_diagnostic diag;
	/* A posted trigger leaves old 3xOK+idle visible for four polls. Only
	 * after acceptance do the commands clear, the engine run, and new replies
	 * arrive. Suppressed IRQ must not prevent progress-witnessed success.
	 */
	init(&m); seed_old_completion(&m); m.trigger_accept_at = 4;
	m.ready_after = 7; m.engine_idle_at = 8; m.suppress_irq = true;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_READY);
	assert(m.accepted && m.hardware_ticks == 8 && m.command_polls == 9);
	assert(m.observed_busy && m.observed_incomplete && !m.observed_irq);
	assert_restored(&m, chosen);
	/* Command progress alone is sufficient even if busy was never visible. */
	init(&m); seed_old_completion(&m); m.trigger_accept_at = 2;
	m.ready_after = 4; m.engine_idle_at = 1; m.suppress_irq = true;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_READY);
	assert(m.hardware_ticks == 4 && !m.observed_busy && m.observed_incomplete && !m.observed_irq);
	assert_restored(&m, chosen);
	/* No acceptance: stale OK+idle and unrelated IRQs are not a receipt for
	 * this request. No cleanup or command rollback is permitted on timeout.
	 */
	init(&m); seed_old_completion(&m); m.trigger_accept_at = UINT32_MAX;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_FATAL);
	assert(diag.reason == FAJITA_RPMH_REASON_UNWITNESSED_COMPLETION && diag.slot == 0 &&
	    diag.offset == FAJITA_RPMH_IRQ_STATUS && diag.value == OTHER_IRQ);
	assert(!m.accepted && !m.observed_busy && !m.observed_incomplete && !m.observed_irq);
	assert(m.command_polls == FAJITA_RPMH_COMPLETION_POLLS && m.delays == FAJITA_RPMH_COMPLETION_POLLS - 1);
	assert_inflight_preserved(&m, chosen);
	/* A genuinely instantaneous response can expose no busy/incomplete
	 * sample; a new selected IRQ supplies independent freshness evidence.
	 */
	init(&m); seed_old_completion(&m); m.ready_after = 0;
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, &chosen) == FAJITA_RPMH_READY);
	assert(m.accepted && !m.hardware_ticks && m.command_polls == 1);
	assert(!m.observed_busy && !m.observed_incomplete && m.observed_irq);
	assert_restored(&m, chosen);
	/* Without either progress or a fresh IRQ, even an actually instant
	 * completion is unprovable to the caller and must conservatively hold.
	 */
	init(&m); seed_old_completion(&m); m.ready_after = 0; m.suppress_irq = true;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_FATAL);
	assert(diag.reason == FAJITA_RPMH_REASON_UNWITNESSED_COMPLETION && m.accepted);
	assert(!m.observed_busy && !m.observed_incomplete && !m.observed_irq);
	assert_inflight_preserved(&m, chosen);
}
static void test_invalid_arguments(void)
{
	struct mock m; init(&m); unsigned chosen = 99;
	assert(fajita_rpmh_avdd_transaction(NULL, &m, RESOURCE, &chosen) == FAJITA_RPMH_SKIP);
	assert(chosen == FAJITA_RPMH_NO_TCS && !m.nreads && !m.nwrites);
	for (unsigned n = 0; n < 3; n++) {
		struct fajita_rpmh_ops bad = ops;
		if (n == 0) { bad.rd = NULL; }
		if (n == 1) { bad.wr = NULL; }
		if (n == 2) { bad.delay_us = NULL; }
		assert(fajita_rpmh_avdd_transaction(&bad, &m, RESOURCE, &chosen) == FAJITA_RPMH_SKIP);
		assert(chosen == FAJITA_RPMH_NO_TCS && !m.nreads && !m.nwrites);
	}
	static const uint32_t bases[] = {0, 1, 0x40002u, UINT32_MAX - 3u};
	for (unsigned n = 0; n < sizeof(bases) / sizeof(bases[0]); n++) {
		assert(fajita_rpmh_avdd_transaction(&ops, &m, bases[n], &chosen) == FAJITA_RPMH_SKIP);
		assert(!m.nreads && !m.nwrites);
	}
	assert(fajita_rpmh_avdd_transaction(&ops, &m, RESOURCE, NULL) == FAJITA_RPMH_READY);
}

static void test_idle_control_rejections(void)
{
	static const uint32_t rejected[] = {FAJITA_RPMH_TRIGGER, 1u,
	    FAJITA_RPMH_ENABLE | 1u,
	    FAJITA_RPMH_ENABLE | FAJITA_RPMH_TRIGGER | (1u << 31)};
	for (unsigned n = 0; n < sizeof(rejected) / sizeof(rejected[0]); n++) {
		struct mock m; init(&m);
		for (unsigned index = 0; index < 2; index++) {
			m.reg[slot_offset(index, FAJITA_RPMH_CONTROL) / 4] = rejected[n];
		}
		unsigned chosen; struct fajita_rpmh_diagnostic diag;
		assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_SKIP);
		assert(!m.nwrites && chosen == FAJITA_RPMH_NO_TCS);
		assert(diag.reason == FAJITA_RPMH_REASON_NO_SLOT && diag.slot == FAJITA_RPMH_NO_TCS &&
		    diag.value == (FAJITA_RPMH_REJECT_CONTROL | (FAJITA_RPMH_REJECT_CONTROL << 16)));
	}
}

static void test_diagnostics_and_recheck(void)
{
	struct mock m; unsigned chosen; struct fajita_rpmh_diagnostic diag;
	init(&m);
	assert(fajita_rpmh_avdd_transaction_diagnostic(NULL, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_SKIP);
	assert(diag.reason == FAJITA_RPMH_REASON_INVALID_ARGUMENT && !m.nreads && !m.nwrites);
	init(&m); m.reg[FAJITA_RPMH_CONTROL / 4] = 1;
	m.reg[slot_offset(1, FAJITA_RPMH_WAIT) / 4] = 1;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_SKIP);
	assert(diag.reason == FAJITA_RPMH_REASON_NO_SLOT &&
	    diag.value == (FAJITA_RPMH_REJECT_CONTROL | (FAJITA_RPMH_REJECT_WAIT << 16)) && !m.nwrites);
	/* A state change after selection/snapshot is rejected before mutation. */
	init(&m); m.mutate_offset = FAJITA_RPMH_CONTROL; m.mutate_on_read = 3; m.mutate_value = 1;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_SKIP);
	assert(diag.reason == FAJITA_RPMH_REASON_RECHECK && diag.slot == 0 &&
	    diag.value == FAJITA_RPMH_REJECT_CONTROL && chosen == FAJITA_RPMH_NO_TCS && !m.nwrites);
	/* Cross-slot conflicts are checked again after the snapshot. */
	init(&m); uint32_t other = slot_offset(1, 0);
	m.reg[(other + FAJITA_RPMH_STATUS) / 4] = 0;
	m.reg[(other + FAJITA_RPMH_CMD_ENABLE) / 4] = 1;
	m.reg[(other + FAJITA_RPMH_ADDR) / 4] = 0x70000;
	m.mutate_offset = other + FAJITA_RPMH_ADDR; m.mutate_on_read = 2; m.mutate_value = RESOURCE;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_SKIP);
	assert(diag.reason == FAJITA_RPMH_REASON_RECHECK && diag.slot == 1 &&
	    diag.offset == other + FAJITA_RPMH_ADDR && diag.value == RESOURCE && !m.nwrites);
	/* Unknown busy geometry and high enable masks remain initial refusals. */
	init(&m); m.reg[FAJITA_RPMH_STATUS / 4] = 0;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_SKIP);
	assert(diag.reason == FAJITA_RPMH_REASON_INITIAL_RESOURCE && diag.offset == FAJITA_RPMH_STATUS && !m.nwrites);
	init(&m); m.reg[FAJITA_RPMH_CMD_ENABLE / 4] = 1u << 16;
	assert(fajita_rpmh_avdd_transaction_diagnostic(&ops, &m, RESOURCE, &chosen, &diag) == FAJITA_RPMH_SKIP);
	assert(diag.reason == FAJITA_RPMH_REASON_INITIAL_RESOURCE && diag.offset == FAJITA_RPMH_CMD_ENABLE &&
	    diag.value == (1u << 16) && !m.nwrites);
}
int main(void)
{
	test_success(); test_selection_guards(); test_pretrigger_failures();
	test_posttrigger_failures(); test_cleanup_failures(); test_invalid_arguments();
	test_inflight_resource_conflicts();
	test_idle_control_rejections(); test_diagnostics_and_recheck();
	test_completion_without_irq_dependency();
	test_completion_freshness();
	puts("RPMh transaction: idle states, guards, fresh all-command completion, posted triggers, optional IRQ, engine idle, rollback and fatal paths passed");
	return 0;
}
