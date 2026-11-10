/* SPDX-License-Identifier: GPL-2.0-only
 * Fajita board facts, not a claim of initialized hardware.
 * Sources: EDL base DTB + fajita overlays 06..12, and OnePlus SDM845
 * pinctrl-sdm845.c / pinctrl-sdm845-v2.c at
 * 222822c2d0042067b7964c6302ef4d756dc4bed9.
 * scripts/audit-fajita-touch-dt.py checks these against the raw inputs.
 * Values are shared by the loader power backend and the kernel transport.
 * Constants and host tests do not establish successful electrical bring-up.
 */
#ifndef FAJITA_TOUCH_BOARD_H
#define FAJITA_TOUCH_BOARD_H

#include <stdint.h>

#define FAJITA_TOUCH_I2C_ADDRESS 32u
/* DT verifies the pair gpio49/gpio50 and qup12, but its pins list does not
 * label individual SDA/SCL roles. These roles retain the bring-up driver's
 * assignment; no successful electrical transaction has validated them yet.
 */
#define FAJITA_TOUCH_SDA_GPIO 49u
#define FAJITA_TOUCH_SCL_GPIO 50u
#define FAJITA_TOUCH_IRQ_GPIO 125u
#define FAJITA_TOUCH_RESET_GPIO 99u
#define FAJITA_TOUCH_VDDIO_GPIO 88u
#define FAJITA_TOUCH_I2C_DRIVE_MA 2u
#define FAJITA_TOUCH_IRQ_DRIVE_MA 16u
#define FAJITA_TOUCH_AVDD_UV 3008000u
#define FAJITA_TOUCH_AVDD_LOAD_UA 20000u

#define FAJITA_TOUCH_SDA_PA 0x03931000u
#define FAJITA_TOUCH_SCL_PA 0x03932000u
#define FAJITA_TOUCH_RESET_PA 0x03963000u
#define FAJITA_TOUCH_IRQ_V1_PA 0x0397d000u
#define FAJITA_TOUCH_IRQ_V2_PA 0x0357d000u
#define FAJITA_TOUCH_VDDIO_V1_PA 0x03d58000u
#define FAJITA_TOUCH_VDDIO_V2_PA 0x03558000u
#define FAJITA_TOUCH_CHIP_ID 321u
#define FAJITA_TOUCH_TLMM_LAYOUT_V1 1u
#define FAJITA_TOUCH_TLMM_LAYOUT_V2 2u

/* An appended Android DTB bundle contains several silicon revisions, not
 * several descriptions of one layout. Select using firmware ChipInfo,
 * BEFORE any touch MMIO. These are the three revisions present in the
 * extracted DTBs; an unknown ID/version has no safe default address. */
static inline uint32_t
fajita_touch_layout_for_chip(uint32_t chip_id, uint32_t version)
{
	if (chip_id != FAJITA_TOUCH_CHIP_ID) { return 0; }
	if (version == 0x00010000u) { return FAJITA_TOUCH_TLMM_LAYOUT_V1; }
	if (version == 0x00020000u || version == 0x00020001u) {
		return FAJITA_TOUCH_TLMM_LAYOUT_V2;
	}
	return 0;
}

static inline uint32_t
fajita_touch_gpio_pa(uint32_t layout, uint32_t gpio)
{
	if (layout != FAJITA_TOUCH_TLMM_LAYOUT_V1 &&
	    layout != FAJITA_TOUCH_TLMM_LAYOUT_V2) { return 0; }
	switch (gpio) {
	case FAJITA_TOUCH_SDA_GPIO: return FAJITA_TOUCH_SDA_PA;
	case FAJITA_TOUCH_SCL_GPIO: return FAJITA_TOUCH_SCL_PA;
	case FAJITA_TOUCH_RESET_GPIO: return FAJITA_TOUCH_RESET_PA;
	case FAJITA_TOUCH_IRQ_GPIO:
		return layout == FAJITA_TOUCH_TLMM_LAYOUT_V1 ?
		    FAJITA_TOUCH_IRQ_V1_PA : FAJITA_TOUCH_IRQ_V2_PA;
	case FAJITA_TOUCH_VDDIO_GPIO:
		return layout == FAJITA_TOUCH_TLMM_LAYOUT_V1 ?
		    FAJITA_TOUCH_VDDIO_V1_PA : FAJITA_TOUCH_VDDIO_V2_PA;
	default: return 0;
	}
}

/* DT dictionary, not a guessed address of the database itself. */
#define FAJITA_TOUCH_CMD_DB_DICT_PA 0x0c3f000cu
#define FAJITA_TOUCH_CMD_DB_RESERVED_START 0x85fc0000u
#define FAJITA_TOUCH_CMD_DB_RESERVED_END 0x8ab00000u
#define FAJITA_TOUCH_RSC_CONFIG_PA 0x179e0000u
#define FAJITA_TOUCH_RSC_TCS_PA 0x179e0d00u
#define FAJITA_TOUCH_RSC_DRV_ID 2u
/* Vendor qcom,tcs-mbox.h: ACTIVE_TCS=2. DT begins with <2 2>. */
#define FAJITA_TOUCH_ACTIVE_TCS_FIRST 0u
#define FAJITA_TOUCH_ACTIVE_TCS_COUNT 2u

/* DT ts_active/config: gpio99/gpio125, 16 mA, bias-pull-up.
 * SDM845 GPIO_CTL: pull[1:0], function[5:2], drive[8:6], output[9].
 * This is an input: IRQ polarity/flags are not GPIO pull/drive bits.
 */
#define FAJITA_TOUCH_ACTIVE_IRQ_CTL \
	(3u | ((FAJITA_TOUCH_IRQ_DRIVE_MA / 2u - 1u) << 6))

/* The S3706 + oem,support_1080x2340_tp branch overrides panel-coords
 * 1080x2160 in the vendor driver. These are inclusive raw maxima.
 */
#define FAJITA_TOUCH_MAX_X 1080u
#define FAJITA_TOUCH_MAX_Y 2340u

#endif
