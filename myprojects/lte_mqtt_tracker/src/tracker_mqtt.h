/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_MQTT_H_
#define TRACKER_MQTT_H_

#include <zephyr/kernel.h>

#include "tracker_backlog.h"

int tracker_mqtt_init(void);
int tracker_mqtt_connect(k_timeout_t timeout);
int tracker_mqtt_publish_cell_report(
	const struct tracker_cell_report *report, k_timeout_t timeout);
int tracker_mqtt_disconnect(k_timeout_t timeout);

#endif /* TRACKER_MQTT_H_ */
