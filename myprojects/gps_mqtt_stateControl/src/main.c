/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/input/input.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
#include <zephyr/logging/log_ctrl.h>
#include <modem/lte_lc.h>

#include "tracker_backlog.h"
#include "tracker_gnss.h"
#include "tracker_modem.h"
#include "tracker_mqtt.h"
#include "tracker_status.h"
#include "tracker_ui.h"

LOG_MODULE_REGISTER(gps_mqtt_state_control, LOG_LEVEL_INF);

enum tracker_state {
	TRACKER_STATE_WAIT_FOR_GNSS,
	TRACKER_STATE_CONNECT_LTE,
	TRACKER_STATE_CONNECT_MQTT,
	TRACKER_STATE_PUBLISH_BACKLOG,
	TRACKER_STATE_DISCONNECT_MQTT,
	TRACKER_STATE_ENABLE_PSM,
	TRACKER_STATE_DISABLE_PSM,
	TRACKER_STATE_WAIT_FOR_LOW_POWER,
	TRACKER_STATE_LOW_POWER,
	TRACKER_STATE_ENTER_LOW_POWER,
};

#define BUTTON_NODE DT_ALIAS(sw0)
#if !DT_NODE_HAS_STATUS(BUTTON_NODE, okay)
#error "GPS MQTT state control requires a sw0 button alias"
#endif
#define BUTTON_DEVICE DEVICE_DT_GET(DT_PARENT(BUTTON_NODE))

static K_SEM_DEFINE(step_requested, 0, 1);
static K_SEM_DEFINE(psm_sleep_entered, 0, 1);
static atomic_t modem_in_psm;
static atomic_t lte_online;
static atomic_t waiting_for_button;
static bool gnss_needs_stop;

static void psm_event(const struct lte_lc_evt *const evt)
{
	if (evt->type == LTE_LC_EVT_NW_REG_STATUS) {
		bool connected = evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ||
			evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_ROAMING;

		atomic_set(&lte_online, connected);
		if (connected) {
			LOG_INF("LTE connected");
		}
	} else if (evt->type == LTE_LC_EVT_PSM_UPDATE && atomic_get(&lte_online)) {
		LOG_INF("LTE connected - [PSM %s]",
			evt->psm_cfg.tau >= 0 && evt->psm_cfg.active_time >= 0 ?
			"enabled" : "disabled");
	} else if (evt->type == LTE_LC_EVT_MODEM_SLEEP_ENTER &&
	    evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
		atomic_set(&modem_in_psm, 1);
		k_sem_give(&psm_sleep_entered);
	} else if (evt->type == LTE_LC_EVT_MODEM_SLEEP_EXIT &&
		   evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
		atomic_clear(&modem_in_psm);
	}
}

static void button_event(struct input_event *evt, void *user_data)
{
	ARG_UNUSED(user_data);
	if (evt->sync && evt->type == INPUT_EV_KEY &&
	    evt->code == DT_PROP(BUTTON_NODE, zephyr_code) && evt->value == 1 &&
	    atomic_cas(&waiting_for_button, 1, 0)) {
		k_sem_give(&step_requested);
	}
}

INPUT_CALLBACK_DEFINE(BUTTON_DEVICE, button_event, NULL);

static const char *state_name(enum tracker_state state)
{
	switch (state) {
	case TRACKER_STATE_WAIT_FOR_GNSS: return "WAIT_FOR_GNSS";
	case TRACKER_STATE_CONNECT_LTE: return "CONNECT_LTE";
	case TRACKER_STATE_CONNECT_MQTT: return "CONNECT_MQTT";
	case TRACKER_STATE_PUBLISH_BACKLOG: return "PUBLISH_BACKLOG";
	case TRACKER_STATE_DISCONNECT_MQTT: return "DISCONNECT_MQTT";
	case TRACKER_STATE_ENABLE_PSM: return "ENABLE_PSM";
	case TRACKER_STATE_DISABLE_PSM: return "DISABLE_PSM";
	case TRACKER_STATE_WAIT_FOR_LOW_POWER: return "WAIT_FOR_LOW_POWER";
	case TRACKER_STATE_LOW_POWER: return "LOW_POWER";
	case TRACKER_STATE_ENTER_LOW_POWER: return "ENTER_LOW_POWER";
	}
	return "UNKNOWN";
}

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

	const char *quiet_modules[] = {"tracker_modem", "tracker_backlog"};

	for (size_t i = 0; i < ARRAY_SIZE(quiet_modules); i++) {
		int source_id = log_source_id_get(quiet_modules[i]);

		if (source_id < 0 || log_filter_set(NULL, Z_LOG_LOCAL_DOMAIN_ID,
			source_id, LOG_LEVEL_WRN) != LOG_LEVEL_WRN) {
			LOG_WRN("Could not reduce log verbosity for %s", quiet_modules[i]);
		}
	}
	tracker_status_set("SYSTEM INIT");
	err = tracker_ui_init();
	if (err != 0) {
		LOG_WRN("Tracker UI initialization failed: %d", err);
	}

	LOG_INF("===============================================");
	LOG_INF("GPS MQTT Button State Control Started");
	LOG_INF("Version: %d.%d.%d", CONFIG_TRACKER_VERSION_MAJOR,
		CONFIG_TRACKER_VERSION_MINOR, CONFIG_TRACKER_VERSION_PATCH);
	LOG_INF("===============================================");

	/* The modem must be initialized before MQTT, AT, LTE, or GNSS APIs. */
	err = tracker_modem_init();
	if (err != 0) {
		return err;
	}

	lte_lc_register_handler(psm_event);

	/* Keep PSM disabled until the operator executes ENABLE_PSM. */
	tracker_status_set("PSM REQUEST DISABLE");
	err = lte_lc_psm_req(false);
	if (err != 0) {
		LOG_ERR("Failed to disable the initial PSM request: %d", err);
		return err;
	}

	err = tracker_mqtt_init();
	if (err != 0) {
		return err;
	}

	return 0;
}

static enum tracker_state run_state(enum tracker_state state)
{
	struct nrf_modem_gnss_pvt_data_frame location;
	int err;

	switch (state) {
	case TRACKER_STATE_WAIT_FOR_GNSS: {
		int stop_err;

		tracker_ui_set_state(TRACKER_UI_GNSS);
		/* LTE may have woken from PSM during a long manual pause. */
		err = tracker_modem_return_to_gnss();
		if (err != 0) {
			state = TRACKER_STATE_ENTER_LOW_POWER;
			break;
		}
		gnss_needs_stop = true;
		err = tracker_gnss_start();
		if (err == 0) {
			err = tracker_gnss_wait_for_fix(&location);
		}
		if (err == 0) {
			struct tracker_location_fix fix;

			location_fix_from_pvt(&fix, &location);
			/* Keep this fix even if the later upload fails. */
			tracker_backlog_add(&fix);
		}
		/* Stop periodic acquisition while the operator pauses between states. */
		stop_err = nrf_modem_gnss_stop();
		if (stop_err != 0) {
			LOG_ERR("Failed to stop GNSS: %d", stop_err);
			if (err == 0) {
				err = stop_err;
			}
		}
		if (stop_err == 0) {
			gnss_needs_stop = false;
		}
		state = (err == 0) ? TRACKER_STATE_CONNECT_LTE :
			TRACKER_STATE_ENTER_LOW_POWER;
		break;
	}

	case TRACKER_STATE_CONNECT_LTE:
		tracker_ui_set_state(TRACKER_UI_LTE);
		LOG_INF("LTE connecting...");
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
			LOG_DBG("Delivered %zu buffered location(s)", delivered);
		}
		state = TRACKER_STATE_DISCONNECT_MQTT;
		break;
	}

	case TRACKER_STATE_DISCONNECT_MQTT:
		err = tracker_mqtt_disconnect(K_SECONDS(10));
		if (err != 0) {
			LOG_WRN("MQTT disconnect did not complete cleanly; press again to retry: %d", err);
			break;
		}
		state = TRACKER_STATE_ENABLE_PSM;
		break;

	case TRACKER_STATE_ENABLE_PSM:
		tracker_ui_set_state(TRACKER_UI_LTE);
		/* Negotiate the new PSM request on a fresh LTE registration. */
		err = tracker_modem_return_to_gnss();
		k_sem_reset(&psm_sleep_entered);
		atomic_clear(&modem_in_psm);
		if (err == 0) {
			tracker_status_set("PSM REQUEST ENABLE");
			err = lte_lc_psm_req(true);
		}
		if (err == 0) {
			LOG_INF("LTE connecting...");
			err = tracker_modem_prepare_upload(K_SECONDS(60));
		}
		if (err == 0) {
			tracker_ui_set_state(TRACKER_UI_IDLE);
			tracker_status_set("MODEM PSM WAIT");
			err = k_sem_take(&psm_sleep_entered,
				K_SECONDS(CONFIG_TRACKER_PSM_ENTRY_TIMEOUT_SECONDS));
			if (atomic_get(&modem_in_psm)) {
				LOG_INF("PSM sleeping");
				err = 0;
			} else {
				tracker_status_set("PSM ENTRY UNCONFIRMED");
				LOG_WRN("PSM entry not confirmed; deactivating LTE as fallback");
				err = tracker_modem_return_to_gnss();
			}
		}
		if (err != 0) {
			LOG_ERR("PSM enable sequence failed: %d", err);
		}
		/* Request acceptance alone does not confirm that the modem entered PSM. */
		state = TRACKER_STATE_DISABLE_PSM;
		break;

	case TRACKER_STATE_DISABLE_PSM:
		tracker_ui_set_state(TRACKER_UI_IDLE);
		/* Leave PSM and release LTE radio control before the next GNSS step. */
		err = tracker_modem_return_to_gnss();
		if (err == 0) {
			atomic_clear(&modem_in_psm);
			tracker_status_set("PSM REQUEST DISABLE");
			err = lte_lc_psm_req(false);
		}
		if (err != 0) {
			LOG_ERR("Failed to disable PSM; press again to retry: %d", err);
			break;
		}
		atomic_clear(&lte_online);
		LOG_INF("LTE disconnected - [PSM disabled]");
		state = TRACKER_STATE_WAIT_FOR_LOW_POWER;
		break;

	case TRACKER_STATE_WAIT_FOR_LOW_POWER:
		state = TRACKER_STATE_LOW_POWER;
		break;

	case TRACKER_STATE_LOW_POWER:
		/* The main thread blocks on the button semaphore while in this mode. */
		state = TRACKER_STATE_WAIT_FOR_GNSS;
		break;

	case TRACKER_STATE_ENTER_LOW_POWER:
		tracker_status_set("LOW POWER PREPARE");
		tracker_ui_set_state(TRACKER_UI_IDLE);
		if (gnss_needs_stop) {
			err = nrf_modem_gnss_stop();
			if (err != 0) {
				LOG_ERR("Failed to stop GNSS; press again to retry: %d", err);
				break;
			}
			gnss_needs_stop = false;
		}
		err = tracker_modem_enter_low_power(
			K_SECONDS(CONFIG_TRACKER_PSM_ENTRY_TIMEOUT_SECONDS));
		if (err != 0) {
			LOG_ERR("Failed to enter a GNSS-safe low-power state: %d", err);
			break;
		}
		state = TRACKER_STATE_DISABLE_PSM;
		break;
	}
	return state;
}

int main(void)
{
	enum tracker_state state = TRACKER_STATE_WAIT_FOR_GNSS;
	int err;

	if (!device_is_ready(BUTTON_DEVICE)) {
		LOG_ERR("User button device is not ready");
		return 0;
	}
	err = tracker_init();
	if (err != 0) {
		tracker_status_set("SYSTEM ERROR");
		LOG_ERR("Tracker initialization failed: %d", err);
		return 0;
	}

	while (true) {
		tracker_ui_set_state(TRACKER_UI_IDLE);
		if (state == TRACKER_STATE_LOW_POWER) {
			LOG_INF("LOW_POWER: LTE off, GNSS stopped; press MODE to return to GPS");
		} else {
			LOG_INF("READY: %s; press the user button to execute", state_name(state));
		}
		atomic_set(&waiting_for_button, 1);
		err = k_sem_take(&step_requested, K_FOREVER);
		if (err != 0) {
			atomic_clear(&waiting_for_button);
			LOG_ERR("Button wait failed: %d", err);
			return 0;
		}
		LOG_INF("RUN: %s", state_name(state));
		state = run_state(state);
	}

	return 0;
}
