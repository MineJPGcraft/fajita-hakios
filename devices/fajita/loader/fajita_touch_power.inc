/* RETIRED, FAILED EXPERIMENT: not used by the default loader.
 * Its 3000 mV request below came from an incorrect manual DT conversion:
 * raw 0x2de600 is 3,008,000 uV. Kept to reproduce the archived experiment,
 * not as a correct board power backend.
 * Fajita AVDD setup, on the firmware core before PSCI CPU_ON.
 * ABI: QcomPkg/Include/Protocol/EFIPmicVreg.h (revision 0x10005).
 * The kernel must never borrow the firmware's RPMh TCS command slots.
 */
struct fajita_vreg_status {
	uint32_t pull_down, sw_mode, pin_controlled, sw_enable;
	uint8_t vreg_ok;
};
struct fajita_vreg_protocol {
	uint64_t revision;
	efi_status (EFIAPI *control)(uint32_t pmic, uint32_t id, uint8_t enable);
	efi_status (EFIAPI *set_level)(uint32_t pmic, uint32_t id, uint32_t millivolts);
	void *get_level, *set_power_mode, *multiphase_control;
	efi_status (EFIAPI *get_status)(uint32_t pmic, uint32_t id, struct fajita_vreg_status *status);
	void *set_level_uv;
};
_Static_assert(sizeof(struct fajita_vreg_status) == 20, "PMIC status ABI");
_Static_assert(offsetof(struct fajita_vreg_status, vreg_ok) == 16, "PMIC status flag ABI");
_Static_assert(offsetof(struct fajita_vreg_protocol, get_status) == 48, "PMIC protocol ABI");

static int
fajita_touch_power_prepare(const char *cmdline)
{
	static const efi_guid guid = { 0x22d38d3d, 0xe8b6, 0x4f8f,
	    { 0x9c, 0x26, 0xbc, 0xeb, 0x07, 0xd6, 0xcb, 0x68 } };
	struct fajita_vreg_protocol *vreg = NULL;
	struct fajita_vreg_status state = {0};
	int requested = 0;
	efi_status status;
	/* Do not initialize touch on dipper or on an argument prefix match. */
	if (PLAT != &platform_fajita) { return 0; }
	for (const char *c = cmdline; *c; c++) {
		if ((c == cmdline || c[-1] == ' ') && starts_with(c, "fajita_touch=1") &&
		    (c[14] == 0 || c[14] == ' ')) { requested = 1; }
	}
	if (!requested) { return 0; }
	status = BS->locate_protocol(&guid, NULL, (void **)&vreg);
	if (EFI_ERROR(status) || vreg == NULL || vreg->revision < 0x10005 ||
	    vreg->control == NULL || vreg->set_level == NULL || vreg->get_status == NULL) {
		print("fajita-touch: firmware Vreg unavailable (%x); touch skipped\n", status);
		return 0;
	}
	/* EFI_PM_LDO_1 is zero: physical PM8998 L28 has protocol ID 27.
	 * Historical request was 3000 mV; the DT actually requests 3008 mV. */
	status = vreg->set_level(0, 27, 3000);
	if (EFI_ERROR(status)) {
		print("fajita-touch: L28 voltage request failed (%x); touch skipped\n", status);
		return 0;
	}
	status = vreg->control(0, 27, 1);
	if (EFI_ERROR(status)) {
		print("fajita-touch: L28 enable failed (%x); touch skipped\n", status);
		return 0;
	}
	for (unsigned attempt = 0; attempt < 50; attempt++) {
		status = vreg->get_status(0, 27, &state);
		if (EFI_ERROR(status)) { break; }
		if (state.sw_enable == 1 && state.vreg_ok) {
			print("fajita-touch: firmware enabled PM8998 L28 at 3000 mV\n");
			return 1;
		}
		status = BS->stall(1000);
		if (EFI_ERROR(status)) { break; }
	}
	print("fajita-touch: L28 not ready (%x, enable %d, ok %d); touch skipped\n",
	    status, (uint64_t)state.sw_enable, (uint64_t)state.vreg_ok);
	return 0;
}
