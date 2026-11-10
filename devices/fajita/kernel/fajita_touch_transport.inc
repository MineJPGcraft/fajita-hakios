/* SPDX-License-Identifier: GPL-2.0-only
 * Fajita GPIO I2C transport. GPIO/timing facts: OnePlus SDM845_Q_10.0,
 * commit 222822c2d0042067b7964c6302ef4d756dc4bed9, pinctrl-sdm845.c/pinctrl-sdm845-v2.c and
 * synaptics_driver_s3320.c. Probe inherited power without touching PMIC,
 * supply or reset GPIOs. No cmd-db, RPMh/TCS, GENI, GCC clock or USB
 * controller is accessed by this transport.
 */
enum { FT_SDA, FT_SCL, FT_IRQ, FT_PIN_COUNT };
static vm_offset_t ft_pin[FT_PIN_COUNT];
static uint32_t ft_saved_ctl[FT_PIN_COUNT], ft_saved_io[FT_PIN_COUNT];
static bool ft_bus_owned;
static uint32_t ft_transactions;
static unsigned ft_wire_error;
/* Layout comes from the firmware's final chip-version acknowledgement.
 * No revision-sensitive pin address has an implicit v1 default. */
static uint64_t ft_pin_pa[FT_PIN_COUNT];

static bool fajita_bus_select_layout(uint32_t layout)
{
	const unsigned gpio[FT_PIN_COUNT] = {
		FAJITA_TOUCH_SDA_GPIO, FAJITA_TOUCH_SCL_GPIO, FAJITA_TOUCH_IRQ_GPIO
	};
	uint64_t pa[FT_PIN_COUNT];
	if (ft_bus_owned) { return false; }
	for (unsigned i = 0; i < FT_PIN_COUNT; i++) {
		pa[i] = fajita_touch_gpio_pa(layout, gpio[i]);
	}
	for (unsigned i = 0; i < FT_PIN_COUNT; i++) {
		ft_pin_pa[i] = pa[i];
		ft_pin[i] = 0;
	}
	return pa[FT_SDA] != 0 && pa[FT_SCL] != 0 && pa[FT_IRQ] != 0;
}

static uint32_t ft_gpio_pa(vm_offset_t p)
{
	for (unsigned i = 0; i < FT_PIN_COUNT; i++) {
		if (ft_pin[i] && p >= ft_pin[i] && p - ft_pin[i] < 8) {
			return (uint32_t)(ft_pin_pa[i] + p - ft_pin[i]);
		}
	}
	return 0;
}
static uint32_t ft_rd(vm_offset_t p)
{
	if (ft_diag_enabled) { ft_phase(FT_PHASE_TLMM_READ, ft_gpio_pa(p)); }
	return *(volatile uint32_t *)p;
}
static void ft_wr(vm_offset_t p, uint32_t v)
{
	if (ft_diag_enabled) { ft_phase(FT_PHASE_TLMM_WRITE, ft_gpio_pa(p)); }
	*(volatile uint32_t *)p = v;
	__asm__ volatile ("dsb sy" ::: "memory");
}
static void ft_sleep_ms(uint32_t ms) { delay_for_interval(ms, NSEC_PER_MSEC); }
static void ft_spin_us(uint32_t us)
{
	uint64_t ticks, began = mach_absolute_time();
	nanoseconds_to_absolutetime((uint64_t)us * NSEC_PER_USEC, &ticks);
	while (mach_absolute_time() - began < ticks) { }
}
static bool ft_level(unsigned pin) { return (ft_rd(ft_pin[pin] + 4) & 1) != 0; }
static void ft_bus_error(unsigned error) { if (!ft_wire_error) { ft_wire_error = error; } }
static void ft_bus_note(uint8_t reg, bool read)
{
	unsigned levels = ft_bus_owned ? ((unsigned)ft_level(FT_SDA) | ((unsigned)ft_level(FT_SCL) << 1)) : 4u;
	ft_transactions++;
	ft_transport_note(((uint64_t)ft_transactions << 32) | ((uint32_t)reg << 16) |
	    ((unsigned)read << 15) | (levels << 8) | ft_wire_error);
}
static void ft_input(unsigned pin, uint32_t config)
{
	/* GPIO function 0, input. High means released, never driven.
	 * I2C uses 2 mA/no bias; IRQ uses the distinct DT active config. */
	ft_wr(ft_pin[pin], config);
}
static void ft_output(unsigned pin, bool high)
{
	ft_wr(ft_pin[pin] + 4, high ? 2 : 0);
	ft_wr(ft_pin[pin], 1u << 9);
}

static bool fajita_bus_init(void)
{
	/* Unknown or absent layout must fail before any mapping or MMIO. */
	for (unsigned i = 0; i < FT_PIN_COUNT; i++) {
		if (ft_pin_pa[i] == 0) { return false; }
	}
	for (unsigned i = 0; i < FT_PIN_COUNT; i++) {
		ft_phase(FT_PHASE_MAP_GPIO, (uint32_t)ft_pin_pa[i]);
		ft_pin[i] = ml_io_map(ft_pin_pa[i], 0x1000);
		if (ft_pin[i] == 0) { return false; }
	}
	for (unsigned i = 0; i < FT_PIN_COUNT; i++) {
		ft_phase(FT_PHASE_SNAPSHOT_GPIO, (uint32_t)ft_pin_pa[i]);
		ft_diag_step("before-GPIO-snapshot", ft_pin_pa[i]);
		ft_saved_ctl[i] = ft_rd(ft_pin[i]);
		ft_saved_io[i] = ft_rd(ft_pin[i] + 4) & 2u;
	}
	ft_bus_owned = true;
	ft_phase(FT_PHASE_PINMUX, 0);
	ft_diag_step("before-GPIO-pinmux", 0);
	/* Existing board pull-ups supply I2C; IRQ uses its DT pull-up. */
	ft_input(FT_SDA, 0);
	ft_input(FT_SCL, 0);
	ft_input(FT_IRQ, FAJITA_TOUCH_ACTIVE_IRQ_CTL);
	return true;
}

static void fajita_bus_restore(void)
{
	if (!ft_bus_owned) { return; }
	/* Release I2C first, then restore its original output latch and mux. */
	ft_input(FT_SDA, 0); ft_input(FT_SCL, 0);
	for (unsigned i = 0; i < FT_PIN_COUNT; i++) {
		ft_wr(ft_pin[i] + 4, ft_saved_io[i]);
		ft_wr(ft_pin[i], ft_saved_ctl[i]);
	}
	ft_bus_owned = false;
}

static bool ft_scl_high(void)
{
	ft_input(FT_SCL, 0);
	for (unsigned n = 0; n < 1000; n++) {
		if (ft_level(FT_SCL)) { ft_spin_us(3); return true; }
		ft_spin_us(2);
	}
	ft_bus_error(FT_BUS_SCL_TIMEOUT);
	return false;
}
static bool ft_start(void)
{
	ft_input(FT_SDA, 0);
	if (!ft_scl_high()) { return false; }
	if (!ft_level(FT_SDA)) { ft_bus_error(FT_BUS_BUSY); return false; }
	ft_output(FT_SDA, false); ft_spin_us(3);
	ft_output(FT_SCL, false); return true;
}
static bool ft_stop(void)
{
	ft_output(FT_SCL, false); ft_output(FT_SDA, false); ft_spin_us(3);
	bool ok = ft_scl_high();
	ft_input(FT_SDA, 0); ft_spin_us(3);
	return ok && ft_level(FT_SDA);
}
static bool ft_send(uint8_t byte)
{
	for (unsigned n = 0; n < 8; n++) {
		if (byte & 0x80) { ft_input(FT_SDA, 0); } else { ft_output(FT_SDA, false); }
		byte <<= 1; ft_spin_us(3);
		if (!ft_scl_high()) { return false; }
		ft_output(FT_SCL, false);
	}
	ft_input(FT_SDA, 0); ft_spin_us(3);
	if (!ft_scl_high()) { return false; }
	bool ack = !ft_level(FT_SDA);
	ft_output(FT_SCL, false); return ack;
}
static bool ft_receive(uint8_t *byte, bool ack)
{
	uint8_t value = 0;
	ft_input(FT_SDA, 0);
	for (unsigned n = 0; n < 8; n++) {
		ft_spin_us(3); if (!ft_scl_high()) { return false; }
		value = (uint8_t)((value << 1) | ft_level(FT_SDA));
		ft_output(FT_SCL, false);
	}
	if (ack) { ft_output(FT_SDA, false); } else { ft_input(FT_SDA, 0); }
	ft_spin_us(3); if (!ft_scl_high()) { return false; }
	ft_output(FT_SCL, false); ft_input(FT_SDA, 0); *byte = value;
	return true;
}
static void ft_recover(void)
{
	ft_input(FT_SDA, 0);
	for (unsigned n = 0; n < 9; n++) {
		ft_output(FT_SCL, false); ft_spin_us(3);
		if (!ft_scl_high()) { break; }
	}
	(void)ft_stop(); ft_input(FT_SDA, 0); ft_input(FT_SCL, 0);
}
static bool fajita_i2c_write(uint8_t reg, const uint8_t *data, size_t length)
{
	ft_wire_error = FT_BUS_OK;
	if (!data || length == 0 || length > 256u - reg) {
		ft_bus_error(FT_BUS_ARGUMENT); ft_bus_note(reg, false); return false;
	}
	bool ok = ft_start();
	if (ok && !(ok = ft_send(FAJITA_TOUCH_I2C_ADDRESS << 1))) { ft_bus_error(FT_BUS_WRITE_ADDRESS_NACK); }
	if (ok && !(ok = ft_send(reg))) { ft_bus_error(FT_BUS_REGISTER_NACK); }
	for (size_t n = 0; ok && n < length; n++) {
		if (!(ok = ft_send(data[n]))) { ft_bus_error(FT_BUS_DATA_NACK); }
	}
	bool stopped = ft_stop();
	if (!stopped) { ft_bus_error(FT_BUS_STOP); }
	if (!ok || !stopped) { ft_recover(); ft_bus_note(reg, false); return false; }
	ft_bus_note(reg, false);
	return true;
}
static bool fajita_i2c_read(uint8_t reg, uint8_t *data, size_t length)
{
	ft_wire_error = FT_BUS_OK;
	if (!data || length == 0 || length > 256u - reg) {
		ft_bus_error(FT_BUS_ARGUMENT); ft_bus_note(reg, true); return false;
	}
	bool ok = ft_start();
	if (ok && !(ok = ft_send(FAJITA_TOUCH_I2C_ADDRESS << 1))) { ft_bus_error(FT_BUS_WRITE_ADDRESS_NACK); }
	if (ok && !(ok = ft_send(reg))) { ft_bus_error(FT_BUS_REGISTER_NACK); }
	if (ok) { ok = ft_start(); }
	if (ok && !(ok = ft_send((FAJITA_TOUCH_I2C_ADDRESS << 1) | 1u))) { ft_bus_error(FT_BUS_READ_ADDRESS_NACK); }
	for (size_t n = 0; ok && n < length; n++) { ok = ft_receive(&data[n], n + 1 < length); }
	bool stopped = ft_stop();
	if (!stopped) { ft_bus_error(FT_BUS_STOP); }
	if (!ok || !stopped) { ft_recover(); ft_bus_note(reg, true); return false; }
	ft_bus_note(reg, true);
	return true;
}
static bool fajita_irq_pending(void) { return !ft_level(FT_IRQ); }
