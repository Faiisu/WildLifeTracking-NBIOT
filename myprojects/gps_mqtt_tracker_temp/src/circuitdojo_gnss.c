/*
 * Copyright (c) 2021 Circuit Dojo LLC
 * Copyright (c) 2026 GPS MQTT tracker contributors
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <errno.h>

#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(gnss_board_setup, LOG_LEVEL_INF);

#if defined(CONFIG_GNSS_HEARTBEAT_LED)
static const struct gpio_dt_spec heartbeat_led =
	GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
static struct k_work_delayable heartbeat_work;

static void heartbeat_handler(struct k_work *work)
{
	int err;

	ARG_UNUSED(work);
	err = gpio_pin_toggle_dt(&heartbeat_led);
	if (err != 0) {
		LOG_ERR("Failed to toggle heartbeat LED: %d", err);
		return;
	}
	err = k_work_reschedule(&heartbeat_work,
		K_MSEC(CONFIG_GNSS_HEARTBEAT_INTERVAL_MS));
	if (err < 0) {
		LOG_ERR("Failed to reschedule heartbeat LED: %d", err);
	}
}
#endif

static int circuit_dojo_gnss_setup(void)
{
	const struct device *gpio = DEVICE_DT_GET(DT_NODELABEL(gpio0));
	int err;

	if (!device_is_ready(gpio)) {
		return -ENODEV;
	}

	err = gpio_pin_configure(gpio, CONFIG_TRACKER_GNSS_POWER_MODE_PIN,
				  GPIO_OUTPUT_LOW);
	if (err != 0) {
		return err;
	}

#if defined(CONFIG_GNSS_HEARTBEAT_LED)
	if (!gpio_is_ready_dt(&heartbeat_led)) {
		return -ENODEV;
	}
	err = gpio_pin_configure_dt(&heartbeat_led, GPIO_OUTPUT_INACTIVE);
	if (err != 0) {
		return err;
	}
	k_work_init_delayable(&heartbeat_work, heartbeat_handler);
	err = k_work_schedule(&heartbeat_work,
		K_MSEC(CONFIG_GNSS_HEARTBEAT_INTERVAL_MS));
	if (err < 0) {
		return err;
	}
	LOG_INF("Heartbeat LED enabled: %d ms on/off",
		CONFIG_GNSS_HEARTBEAT_INTERVAL_MS);
#endif
	return 0;
}

SYS_INIT(circuit_dojo_gnss_setup, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
