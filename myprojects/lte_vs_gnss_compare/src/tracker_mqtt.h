/*
 * Copyright (c) 2026 Cellular Asset Tracker Project
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef TRACKER_MQTT_H_
#define TRACKER_MQTT_H_

#include <zephyr/kernel.h>

#include "tracker_data.h"

int tracker_mqtt_init(void);
int tracker_mqtt_connect(k_timeout_t timeout, struct k_sem *abort_sem);

/*
 * Publish unified telemetry JSON containing both "gnss" and "lte" objects.
 * Pass NULL for whichever subsystem was not acquired in the current cycle
 * (emitted as JSON null). At least one of gnss or lte must be non-NULL.
 */
int tracker_mqtt_publish_telemetry(const struct tracker_gnss_fix *gnss,
				   const struct tracker_cell_report *lte,
				   k_timeout_t timeout,
				   struct k_sem *abort_sem);

int tracker_mqtt_disconnect(k_timeout_t timeout);

#endif /* TRACKER_MQTT_H_ */
