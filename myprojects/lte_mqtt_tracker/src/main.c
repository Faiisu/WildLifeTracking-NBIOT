/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "tracker_backlog.h"
#include "tracker_modem.h"
#include "tracker_mqtt.h"
#include "tracker_status.h"
#include "tracker_ui.h"

LOG_MODULE_REGISTER(lte_mqtt_tracker, LOG_LEVEL_INF);

enum tracker_state {
	TRACKER_STATE_CONNECT_LTE,
	TRACKER_STATE_MEASURE_CELLS,
	TRACKER_STATE_CONNECT_MQTT,
	TRACKER_STATE_PUBLISH_BACKLOG,
	TRACKER_STATE_DISCONNECT_MQTT,
	TRACKER_STATE_WAIT_NEXT_CYCLE,
};

static int tracker_init(void)
{
	int err;

	tracker_status_set("SYSTEM INIT");
	err = tracker_ui_init();
	if (err != 0) {
		LOG_WRN("Tracker UI initialization failed: %d", err);
	}

	LOG_INF("===============================================");
	LOG_INF("LTE Cell MQTT Asset Tracker Application Started");
	LOG_INF("Version: %d.%d.%d", CONFIG_TRACKER_VERSION_MAJOR,
		CONFIG_TRACKER_VERSION_MINOR, CONFIG_TRACKER_VERSION_PATCH);
	LOG_INF("===============================================");

	/* The modem must be initialized before MQTT, AT, or LTE APIs. */
	err = tracker_modem_init();
	if (err != 0) {
		return err;
	}

	return tracker_mqtt_init();
}

int main(void)
{
	enum tracker_state state = TRACKER_STATE_CONNECT_LTE;
	int err;

	err = tracker_init();
	if (err != 0) {
		tracker_status_set("SYSTEM ERROR");
		LOG_ERR("Tracker initialization failed: %d", err);
		return 0;
	}

	while (true) {
		switch (state) {
		case TRACKER_STATE_CONNECT_LTE:
			tracker_ui_set_state(TRACKER_UI_LTE);
			err = tracker_modem_connect(K_FOREVER);
			if (err != 0) {
				LOG_ERR("LTE connect failed: %d; retrying in 2 s", err);
				k_sleep(K_SECONDS(2));
				break;
			}
			state = TRACKER_STATE_MEASURE_CELLS;
			break;

		case TRACKER_STATE_MEASURE_CELLS: {
			struct tracker_cell_report report;

			tracker_ui_set_state(TRACKER_UI_CELL_SCAN);
			err = tracker_modem_measure_cells(&report,
				K_SECONDS(CONFIG_TRACKER_CELL_MEAS_TIMEOUT));
			if (err == 0) {
				tracker_backlog_add(&report);
				state = TRACKER_STATE_CONNECT_MQTT;
			} else {
				LOG_WRN("Cell measurement failed: %d", err);
				state = TRACKER_STATE_WAIT_NEXT_CYCLE;
			}
			break;
		}

		case TRACKER_STATE_CONNECT_MQTT:
			tracker_ui_set_state(TRACKER_UI_MQTT);
			err = tracker_mqtt_connect(K_SECONDS(15));
			state = (err == 0) ? TRACKER_STATE_PUBLISH_BACKLOG :
				TRACKER_STATE_DISCONNECT_MQTT;
			break;

		case TRACKER_STATE_PUBLISH_BACKLOG: {
			struct tracker_cell_report report;
			size_t delivered = 0;

			while (tracker_backlog_peek_oldest(&report)) {
				err = tracker_mqtt_publish_cell_report(&report, K_SECONDS(15));
				if (err != 0) {
					LOG_ERR("Cell report delivery failed: %d", err);
					break;
				}
				tracker_backlog_remove_oldest();
				delivered++;
			}
			if (delivered > 0) {
				LOG_INF("Delivered %zu buffered cell report(s)", delivered);
			}
			state = TRACKER_STATE_DISCONNECT_MQTT;
			break;
		}

		case TRACKER_STATE_DISCONNECT_MQTT:
			err = tracker_mqtt_disconnect(K_SECONDS(10));
			if (err != 0) {
				LOG_WRN("MQTT disconnect did not complete cleanly: %d", err);
			}
			state = TRACKER_STATE_WAIT_NEXT_CYCLE;
			break;

		case TRACKER_STATE_WAIT_NEXT_CYCLE:
			tracker_status_set("CYCLE WAIT");
			tracker_ui_set_state(TRACKER_UI_IDLE);
			LOG_INF("Waiting %d s before next LTE cell measurement",
				CONFIG_TRACKER_PERIODIC_INTERVAL);
			k_sleep(K_SECONDS(CONFIG_TRACKER_PERIODIC_INTERVAL));
			state = TRACKER_STATE_CONNECT_LTE;
			break;
		}
	}

	return 0;
}
