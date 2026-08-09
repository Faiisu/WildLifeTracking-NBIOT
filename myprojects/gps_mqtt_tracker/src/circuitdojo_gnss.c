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

static int circuit_dojo_gnss_setup(void)
{
	const struct device *gpio = DEVICE_DT_GET(DT_NODELABEL(gpio0));

	if (!device_is_ready(gpio)) {
		return -ENODEV;
	}

	return gpio_pin_configure(gpio, CONFIG_TRACKER_GNSS_POWER_MODE_PIN,
				  GPIO_OUTPUT_LOW);
}

SYS_INIT(circuit_dojo_gnss_setup, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
