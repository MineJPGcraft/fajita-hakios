/* Host-only tests of the actual loader Vreg helper; no device access. */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define EFIAPI
typedef uint64_t efi_status;
typedef struct {
	uint32_t a;
	uint16_t b, c;
	uint8_t d[8];
} efi_guid;
#define EFI_ERROR(s) (((efi_status)(s) & (UINT64_C(1) << 63)) != 0)
#define TEST_ERROR ((UINT64_C(1) << 63) | UINT64_C(7))

struct platform { int identity; };
static const struct platform platform_fajita = { 1 };
static const struct platform platform_other = { 2 };
static const struct platform *PLAT;

struct fajita_vreg_status;
static efi_status mock_locate(const efi_guid *, void *, void **);
static efi_status mock_stall(uint64_t);
static efi_status mock_control(uint32_t, uint32_t, uint8_t);
static efi_status mock_set_level(uint32_t, uint32_t, uint32_t);
static efi_status mock_get_status(uint32_t, uint32_t, struct fajita_vreg_status *);

struct mock_boot_services {
	efi_status (*locate_protocol)(const efi_guid *, void *, void **);
	efi_status (*stall)(uint64_t);
};
static struct mock_boot_services boot_services = { mock_locate, mock_stall };
static struct mock_boot_services *BS = &boot_services;

static unsigned print_calls;
static void print(const char *format, ...)
{
	assert(format != NULL);
	print_calls++;
}
static int starts_with(const char *text, const char *prefix)
{
	return strncmp(text, prefix, strlen(prefix)) == 0;
}

#include "../devices/fajita/loader/fajita_touch_power.inc"

static struct fajita_vreg_protocol protocol;
static unsigned locate_calls, level_calls, control_calls, status_calls, stall_calls;
static efi_status locate_error, level_error, control_error, status_error, stall_error;
static bool locate_returns_null;
static unsigned ready_after, status_fail_at, stall_fail_at;
static uint32_t reported_enable;
static uint8_t reported_ok;
static uint64_t stalled_microseconds;

static void reset(void)
{
	PLAT = &platform_fajita;
	protocol = (struct fajita_vreg_protocol){0};
	protocol.revision = UINT64_C(0x10005);
	protocol.control = mock_control;
	protocol.set_level = mock_set_level;
	protocol.get_status = mock_get_status;
	locate_calls = level_calls = control_calls = status_calls = stall_calls = print_calls = 0;
	locate_error = level_error = control_error = status_error = stall_error = 0;
	locate_returns_null = false;
	ready_after = 1;
	status_fail_at = stall_fail_at = 0;
	reported_enable = reported_ok = 1;
	stalled_microseconds = 0;
}

static efi_status mock_locate(const efi_guid *guid, void *registration, void **result)
{
	static const uint8_t tail[8] = { 0x9c, 0x26, 0xbc, 0xeb, 0x07, 0xd6, 0xcb, 0x68 };
	assert(guid != NULL && registration == NULL && result != NULL);
	assert(guid->a == UINT32_C(0x22d38d3d) && guid->b == 0xe8b6 && guid->c == 0x4f8f);
	assert(memcmp(guid->d, tail, sizeof(tail)) == 0);
	locate_calls++;
	*result = locate_returns_null ? NULL : &protocol;
	return locate_error;
}

static efi_status mock_set_level(uint32_t pmic, uint32_t id, uint32_t millivolts)
{
	assert(pmic == 0 && id == 27 && millivolts == 3000);
	assert(level_calls == 0 && control_calls == 0 && status_calls == 0 && stall_calls == 0);
	level_calls++;
	return level_error;
}

static efi_status mock_control(uint32_t pmic, uint32_t id, uint8_t enable)
{
	assert(pmic == 0 && id == 27 && enable == 1);
	assert(level_calls == 1 && control_calls == 0 && status_calls == 0 && stall_calls == 0);
	control_calls++;
	return control_error;
}

static efi_status mock_get_status(uint32_t pmic, uint32_t id, struct fajita_vreg_status *state)
{
	assert(pmic == 0 && id == 27 && state != NULL);
	assert(level_calls == 1 && control_calls == 1);
	status_calls++;
	if (status_fail_at != 0 && status_calls == status_fail_at) {
		return status_error;
	}
	state->sw_enable = status_calls >= ready_after ? reported_enable : 0;
	state->vreg_ok = status_calls >= ready_after ? reported_ok : 0;
	return 0;
}

static efi_status mock_stall(uint64_t microseconds)
{
	assert(microseconds == 1000);
	assert(level_calls == 1 && control_calls == 1 && status_calls >= 1);
	stall_calls++;
	stalled_microseconds += microseconds;
	if (stall_fail_at != 0 && stall_calls == stall_fail_at) {
		return stall_error;
	}
	return 0;
}

static void assert_no_power_calls(void)
{
	assert(level_calls == 0 && control_calls == 0 && status_calls == 0 && stall_calls == 0);
}

static void test_argument_and_platform_gate(void)
{
	static const char *const rejected[] = {
		"", "bootcpu=7 cpus=1", "fajita_touch=0", "fajita_touch=10",
		"fajita_touch=11", "fajita_touch=1x", "xfajita_touch=1",
		"fajita_touch_vreg=1", "bootcpu=7 xfajita_touch=1 cpus=1",
		"fajita_touch=1\t", "\tfajita_touch=1", "fajita_touch=1\n"
	};
	static const char *const accepted[] = {
		"fajita_touch=1", "fajita_touch=1 ", " fajita_touch=1",
		"bootcpu=7 fajita_touch=1", "bootcpu=7 fajita_touch=1 cpus=1",
		"  fajita_touch=1  cpus=1"
	};
	for (size_t i = 0; i < sizeof(rejected) / sizeof(rejected[0]); i++) {
		reset();
		assert(fajita_touch_power_prepare(rejected[i]) == 0);
		assert(locate_calls == 0 && print_calls == 0);
		assert_no_power_calls();
	}
	for (size_t i = 0; i < sizeof(accepted) / sizeof(accepted[0]); i++) {
		reset();
		assert(fajita_touch_power_prepare(accepted[i]) == 1);
		assert(locate_calls == 1 && level_calls == 1 && control_calls == 1 && status_calls == 1);
		assert(stall_calls == 0);
	}
	reset();
	PLAT = &platform_other;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(locate_calls == 0 && print_calls == 0);
	assert_no_power_calls();
	reset();
	PLAT = NULL;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(locate_calls == 0);
	assert_no_power_calls();
}

static void test_unavailable_protocol(void)
{
	reset();
	locate_error = TEST_ERROR;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(locate_calls == 1);
	assert_no_power_calls();
	reset();
	locate_returns_null = true;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert_no_power_calls();
	reset();
	protocol.revision = 0x10004;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert_no_power_calls();
	reset();
	protocol.control = NULL;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert_no_power_calls();
	reset();
	protocol.set_level = NULL;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert_no_power_calls();
	reset();
	protocol.get_status = NULL;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert_no_power_calls();
	/* Future revisions retaining this prefix may still provide the known ABI. */
	reset();
	protocol.revision = 0x10006;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 1);
}

static void test_method_failures(void)
{
	reset();
	level_error = TEST_ERROR;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(level_calls == 1 && control_calls == 0 && status_calls == 0 && stall_calls == 0);
	reset();
	control_error = TEST_ERROR;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(level_calls == 1 && control_calls == 1 && status_calls == 0 && stall_calls == 0);
	reset();
	status_error = TEST_ERROR;
	status_fail_at = 1;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(status_calls == 1 && stall_calls == 0);
	reset();
	ready_after = 20;
	status_error = TEST_ERROR;
	status_fail_at = 3;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(status_calls == 3 && stall_calls == 2);
	reset();
	ready_after = 20;
	stall_error = TEST_ERROR;
	stall_fail_at = 3;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(status_calls == 3 && stall_calls == 3);
}

static void test_ready_and_bounded_timeout(void)
{
	reset();
	ready_after = 4;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 1);
	assert(status_calls == 4 && stall_calls == 3 && stalled_microseconds == 3000);
	/* Ready on the last allowed status read succeeds, without another stall. */
	reset();
	ready_after = 50;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 1);
	assert(status_calls == 50 && stall_calls == 49);
	/* One read beyond the budget must not occur. */
	reset();
	ready_after = 51;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(status_calls == 50 && stall_calls == 50 && stalled_microseconds == 50000);
	/* Enable and vreg_ok are both required; neither alone confirms power. */
	reset();
	reported_enable = 0;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(status_calls == 50 && stall_calls == 50);
	reset();
	reported_ok = 0;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(status_calls == 50 && stall_calls == 50);
	reset();
	reported_enable = 2;
	assert(fajita_touch_power_prepare("fajita_touch=1") == 0);
	assert(status_calls == 50 && stall_calls == 50);
}

int main(void)
{
	test_argument_and_platform_gate();
	test_unavailable_protocol();
	test_method_failures();
	test_ready_and_bounded_timeout();
	puts("fajita firmware Vreg helper tests passed");
	return 0;
}
