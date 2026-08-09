/*
 * Copyright (c) 2026
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdbool.h>

#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(sparkfun_blinky, CONFIG_LOG_DEFAULT_LEVEL);

#define LED0_NODE DT_ALIAS(led0)

#if !DT_NODE_HAS_STATUS_OKAY(LED0_NODE)
#error "This application requires a board with an enabled led0 devicetree alias"
#endif

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(LED0_NODE, gpios);

int main(void)
{
	bool led_is_on = true;
	int err;

	if (!gpio_is_ready_dt(&led)) {
		LOG_ERR("LED GPIO controller is not ready");
		return 0;
	}

	err = gpio_pin_configure_dt(&led, GPIO_OUTPUT_ACTIVE);
	if (err != 0) {
		LOG_ERR("Failed to configure LED GPIO: %d", err);
		return 0;
	}

	LOG_INF("Blinking the onboard LED every %d ms",
		CONFIG_BLINK_INTERVAL_MS);

	while (true) {
		k_msleep(CONFIG_BLINK_INTERVAL_MS);

		err = gpio_pin_toggle_dt(&led);
		if (err != 0) {
			LOG_ERR("Failed to toggle LED GPIO: %d", err);
			return 0;
		}

		led_is_on = !led_is_on;
		LOG_INF("LED state: %s", led_is_on ? "ON" : "OFF");
	}

	return 0;
}
