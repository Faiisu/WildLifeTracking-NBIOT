/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#if defined(CONFIG_STARTUP_LED)
#include <zephyr/drivers/gpio.h>
#endif
#if IS_ENABLED(CONFIG_ENABLE_LEDS)
#include <dk_buttons_and_leds.h>
#endif

#include "tracker_ui.h"

LOG_MODULE_REGISTER(tracker_ui, LOG_LEVEL_INF);

#if defined(CONFIG_STARTUP_LED)
#define STARTUP_LED_NODE DT_ALIAS(led0)

#if !DT_NODE_HAS_STATUS(STARTUP_LED_NODE, okay)
#error "CONFIG_STARTUP_LED requires a ready led0 devicetree alias"
#endif

static const struct gpio_dt_spec startup_led = GPIO_DT_SPEC_GET(STARTUP_LED_NODE, gpios);
static struct k_work_delayable working_led_work;
static bool working_led_state;

static void working_led_blink_handler(struct k_work *work)
{
	int err;

	ARG_UNUSED(work);
	working_led_state = !working_led_state;
	err = gpio_pin_set_dt(&startup_led, working_led_state);
	if (err != 0) {
		LOG_ERR("Failed to update working LED: %d", err);
		return;
	}

	err = k_work_reschedule(&working_led_work,
		K_MSEC(CONFIG_WORKING_LED_BLINK_INTERVAL_MS));
	if (err < 0) {
		LOG_ERR("Failed to reschedule working LED: %d", err);
	}
}

static int working_led_start(void)
{
	int err;

	if (!gpio_is_ready_dt(&startup_led)) {
		return -ENODEV;
	}

	err = gpio_pin_configure_dt(&startup_led, GPIO_OUTPUT_ACTIVE);
	if (err != 0) {
		return err;
	}

	working_led_state = true;
	k_work_init_delayable(&working_led_work, working_led_blink_handler);
	err = k_work_schedule(&working_led_work,
		K_MSEC(CONFIG_WORKING_LED_BLINK_INTERVAL_MS));
	return err < 0 ? err : 0;
}
#endif

int tracker_ui_init(void)
{
#if defined(CONFIG_STARTUP_LED) || defined(CONFIG_ENABLE_LEDS)
	int err;
#endif

#if defined(CONFIG_STARTUP_LED)
	err = working_led_start();
	if (err != 0) {
		return err;
	}
	LOG_INF("Working LED blinking every %d ms",
		CONFIG_WORKING_LED_BLINK_INTERVAL_MS);
#endif

#if IS_ENABLED(CONFIG_ENABLE_LEDS)
	err = dk_leds_init();
	if (err != 0) {
		LOG_ERR("Failed to initialize status LEDs: %d", err);
		return err;
	}
#endif

	return 0;
}

void tracker_ui_set_state(enum tracker_ui_state state)
{
#if IS_ENABLED(CONFIG_ENABLE_LEDS)
	(void)dk_set_leds(0);
	switch (state) {
	case TRACKER_UI_GNSS:
		(void)dk_set_led_on(DK_LED2);
		break;
	case TRACKER_UI_LTE:
		(void)dk_set_led_on(DK_LED1);
		break;
	case TRACKER_UI_MQTT:
		(void)dk_set_led_on(DK_LED4);
		break;
	}
#else
	ARG_UNUSED(state);
#endif
}
