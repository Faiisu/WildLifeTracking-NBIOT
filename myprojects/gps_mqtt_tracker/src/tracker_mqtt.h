/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_MQTT_H_
#define TRACKER_MQTT_H_

#include <zephyr/kernel.h>
#include <nrf_modem_gnss.h>

int tracker_mqtt_init(void);
int tracker_mqtt_connect(k_timeout_t timeout);
int tracker_mqtt_publish_location(
	const struct nrf_modem_gnss_pvt_data_frame *pvt, k_timeout_t timeout);
int tracker_mqtt_disconnect(k_timeout_t timeout);

#endif /* TRACKER_MQTT_H_ */
