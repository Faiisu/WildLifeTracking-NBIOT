/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "tracker_backlog.h"
#include "tracker_gnss.h"
#include "tracker_modem.h"
#include "tracker_mqtt.h"
#include "tracker_status.h"
#include "tracker_ui.h"

LOG_MODULE_REGISTER(gps_mqtt_tracker, LOG_LEVEL_INF);

enum tracker_state {
	TRACKER_STATE_WAIT_FOR_GNSS,
	TRACKER_STATE_CONNECT_LTE,
	TRACKER_STATE_CONNECT_MQTT,
	TRACKER_STATE_PUBLISH_BACKLOG,
	TRACKER_STATE_DISCONNECT_MQTT,
	TRACKER_STATE_ENTER_LOW_POWER,
};

static void location_fix_from_pvt(struct tracker_location_fix *fix,
	const struct nrf_modem_gnss_pvt_data_frame *pvt)
{
	fix->latitude = pvt->latitude;
	fix->longitude = pvt->longitude;
	fix->altitude = pvt->altitude;
	fix->accuracy = pvt->accuracy;
	fix->datetime = pvt->datetime;
}

static int tracker_init(void)
{
	int err;

	tracker_status_set("SYSTEM INIT");
	err = tracker_ui_init();
	if (err != 0) {
		LOG_WRN("Tracker UI initialization failed: %d", err);
	}

	LOG_INF("===============================================");
	LOG_INF("GPS MQTT Asset Tracker Application Started");
	LOG_INF("Version: %d.%d.%d", CONFIG_TRACKER_VERSION_MAJOR,
		CONFIG_TRACKER_VERSION_MINOR, CONFIG_TRACKER_VERSION_PATCH);
	LOG_INF("===============================================");

	/* The modem must be initialized before MQTT, AT, LTE, or GNSS APIs. */
	err = tracker_modem_init();
	if (err != 0) {
		return err;
	}

	err = tracker_mqtt_init();
	if (err != 0) {
		return err;
	}

	return tracker_gnss_start();
}

int main(void)
{
	struct nrf_modem_gnss_pvt_data_frame location;
	enum tracker_state state = TRACKER_STATE_WAIT_FOR_GNSS;
	int err;

	err = tracker_init();
	if (err != 0) {
		tracker_status_set("SYSTEM ERROR");
		LOG_ERR("Tracker initialization failed: %d", err);
		return 0;
	}

	while (true) {
		switch (state) {
		case TRACKER_STATE_WAIT_FOR_GNSS:
			tracker_ui_set_state(TRACKER_UI_GNSS);
			err = tracker_gnss_wait_for_fix(&location);
			if (err == 0) {
				struct tracker_location_fix fix;

				location_fix_from_pvt(&fix, &location);
				/*
				 * Buffer the fix immediately, before attempting any
				 * upload. If LTE/MQTT/publish fails below, the fix
				 * simply stays in the backlog for a later cycle
				 * instead of being lost -- see ticket 09.
				 */
				tracker_backlog_add(&fix);
			}
			state = (err == 0) ? TRACKER_STATE_CONNECT_LTE :
				TRACKER_STATE_ENTER_LOW_POWER;
			break;

		case TRACKER_STATE_CONNECT_LTE:
			tracker_ui_set_state(TRACKER_UI_LTE);
			err = tracker_modem_prepare_upload(K_SECONDS(60));
			state = (err == 0) ? TRACKER_STATE_CONNECT_MQTT :
				TRACKER_STATE_ENTER_LOW_POWER;
			break;

		case TRACKER_STATE_CONNECT_MQTT:
			tracker_ui_set_state(TRACKER_UI_LTE_CONNECTED);
			err = tracker_mqtt_connect(K_SECONDS(15));
			state = (err == 0) ? TRACKER_STATE_PUBLISH_BACKLOG :
				TRACKER_STATE_DISCONNECT_MQTT;
			break;

		case TRACKER_STATE_PUBLISH_BACKLOG: {
			struct tracker_location_fix fix;
			size_t delivered = 0;

			while (tracker_backlog_peek_oldest(&fix)) {
				err = tracker_mqtt_publish_location(&fix, K_SECONDS(15));
				if (err != 0) {
					LOG_ERR("Location delivery failed: %d", err);
					break;
				}
				/*
				 * Only remove after tracker_mqtt_publish_location()
				 * confirms the broker's PUBACK (QoS 1) -- an
				 * unacknowledged publish must stay in the backlog.
				 */
				tracker_backlog_remove_oldest();
				delivered++;
			}
			if (delivered > 0) {
				LOG_INF("Delivered %zu buffered location(s)", delivered);
			}
			state = TRACKER_STATE_DISCONNECT_MQTT;
			break;
		}

		case TRACKER_STATE_DISCONNECT_MQTT:
			err = tracker_mqtt_disconnect(K_SECONDS(10));
			if (err != 0) {
				LOG_WRN("MQTT disconnect did not complete cleanly: %d", err);
			}
			state = TRACKER_STATE_ENTER_LOW_POWER;
			break;

		case TRACKER_STATE_ENTER_LOW_POWER:
			tracker_status_set("LOW POWER PREPARE");
			tracker_ui_set_state(TRACKER_UI_IDLE);
			err = tracker_modem_enter_low_power(
				K_SECONDS(CONFIG_TRACKER_PSM_ENTRY_TIMEOUT_SECONDS));
			if (err != 0) {
				LOG_ERR("Failed to enter a GNSS-safe low-power state: %d", err);
			}
			state = TRACKER_STATE_WAIT_FOR_GNSS;
			break;
		}
	}

	return 0;
}
