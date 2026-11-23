/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/input/input.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
#include <zephyr/logging/log_ctrl.h>
#include <modem/lte_lc.h>

#include "tracker_gnss.h"
#include "tracker_modem.h"
#include "tracker_mqtt.h"
#include "tracker_ui.h"

LOG_MODULE_REGISTER(gps_mqtt_state_control, LOG_LEVEL_INF);

enum tracker_state {
	TRACKER_STATE_GPS,
	TRACKER_STATE_FIX,
	TRACKER_STATE_LTE_NO_PSM,
	TRACKER_STATE_LTE_PSM,
	TRACKER_STATE_MQTT_CONNECT,
	TRACKER_STATE_MQTT_SENDING,
	TRACKER_STATE_MQTT_DISCONNECT,
	TRACKER_STATE_LOW_POWER,
};

#define BUTTON_NODE DT_ALIAS(sw0)
#if !DT_NODE_HAS_STATUS(BUTTON_NODE, okay)
#error "GPS state control requires a sw0 button alias"
#endif
#define BUTTON_DEVICE DEVICE_DT_GET(DT_PARENT(BUTTON_NODE))

static K_SEM_DEFINE(step_requested, 0, 1);
static K_SEM_DEFINE(psm_sleep_entered, 0, 1);
static atomic_t waiting_for_button;
static atomic_t modem_in_psm;
static atomic_t lte_online;
static struct nrf_modem_gnss_pvt_data_frame location;
static bool has_fix;
static bool gnss_needs_stop;
static struct tracker_location_fix report;
static bool mqtt_send_retry;

static void modem_event(const struct lte_lc_evt *const evt)
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
		LOG_INF("PSM sleeping");
		k_sem_give(&psm_sleep_entered);
	} else if (evt->type == LTE_LC_EVT_MODEM_SLEEP_EXIT &&
		   evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
		atomic_clear(&modem_in_psm);
		LOG_INF("PSM awake");
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
	case TRACKER_STATE_GPS: return "GPS";
	case TRACKER_STATE_FIX: return "FIX";
	case TRACKER_STATE_LTE_NO_PSM: return "LTE_NO_PSM";
	case TRACKER_STATE_LTE_PSM: return "LTE_PSM";
	case TRACKER_STATE_MQTT_CONNECT: return "MQTT_CONNECT";
	case TRACKER_STATE_MQTT_SENDING: return "MQTT_SENDING";
	case TRACKER_STATE_MQTT_DISCONNECT: return "MQTT_DISCONNECT";
	case TRACKER_STATE_LOW_POWER: return "LOW_POWER";
	}
	return "UNKNOWN";
}

static enum tracker_state next_state(enum tracker_state state)
{
	return state == TRACKER_STATE_LOW_POWER ? TRACKER_STATE_GPS : state + 1;
}

static int wait_for_mode(void)
{
	int err;

	k_sem_reset(&step_requested);
	atomic_set(&waiting_for_button, 1);
	err = k_sem_take(&step_requested, K_FOREVER);
	atomic_clear(&waiting_for_button);
	return err;
}

static int stop_gnss(void)
{
	int err = 0;

	if (gnss_needs_stop) {
		err = nrf_modem_gnss_stop();
		if (err == 0) {
			gnss_needs_stop = false;
		}
	}
	return err;
}

static int connect_lte(bool request_psm)
{
	int err = tracker_modem_return_to_gnss();

	if (err != 0) {
		return err;
	}
	atomic_clear(&lte_online);
	atomic_clear(&modem_in_psm);
	k_sem_reset(&psm_sleep_entered);
	err = lte_lc_psm_req(request_psm);
	if (err != 0) {
		return err;
	}
	tracker_ui_set_state(TRACKER_UI_LTE);
	LOG_INF("LTE connecting... [PSM request %s]", request_psm ? "enabled" : "disabled");
	return tracker_modem_prepare_upload(K_SECONDS(60));
}

static int connect_mqtt(void)
{
	/* Clean up a previous rejected/timed-out connection before retrying. */
	int err = tracker_mqtt_disconnect(K_SECONDS(10));

	if (err == 0) {
		err = tracker_modem_prepare_upload(K_SECONDS(60));
	}
	if (err == 0) {
		tracker_ui_set_state(TRACKER_UI_LTE_CONNECTED);
		err = tracker_mqtt_connect(K_SECONDS(15));
	}
	return err;
}

static int enter_state(enum tracker_state state)
{
	int err;

	LOG_INF("ENTER: %s", state_name(state));
	switch (state) {
	case TRACKER_STATE_GPS: {
		bool stopped_by_button = false;
		int stop_err;

		has_fix = false;
		report = (struct tracker_location_fix){0};
		mqtt_send_retry = false;
		err = stop_gnss();
		if (err != 0) {
			return err;
		}
		err = tracker_modem_return_to_gnss();
		if (err == 0) {
			err = lte_lc_psm_req(false);
		}
		if (err != 0) {
			return err;
		}
		tracker_ui_set_state(TRACKER_UI_GNSS);
		gnss_needs_stop = true;
		err = tracker_gnss_start();
		if (err == 0) {
			k_sem_reset(&step_requested);
			atomic_set(&waiting_for_button, 1);
			err = tracker_gnss_collect_until_stop(&location, &step_requested,
				&stopped_by_button);
			atomic_clear(&waiting_for_button);
			k_sem_reset(&step_requested);
			has_fix = err == 0;
		}
		stop_err = stop_gnss();
		if (stop_err != 0) {
			return stop_err;
		}
		if (err != 0 && err != -ENODATA && err != -ETIMEDOUT) {
			return err;
		}
		if (!stopped_by_button) {
			tracker_ui_set_state(TRACKER_UI_IDLE);
			LOG_INF("GPS session ended; press MODE to show FIX");
			return wait_for_mode();
		}
		return 0;
	}
	case TRACKER_STATE_FIX:
		tracker_ui_set_state(TRACKER_UI_IDLE);
		if (has_fix) {
			report.has_fix = true;
			report.latitude = location.latitude;
			report.longitude = location.longitude;
			report.altitude = location.altitude;
			report.accuracy = location.accuracy;
			report.datetime = location.datetime;
			LOG_INF("FIX: {\"lat\":%.06f,\"lon\":%.06f,\"alt\":%.01f,\"acc\":%.01f,"
				"\"time\":\"%04u-%02u-%02uT%02u:%02u:%02uZ\"}",
				location.latitude, location.longitude, (double)location.altitude,
				(double)location.accuracy, location.datetime.year,
				location.datetime.month, location.datetime.day, location.datetime.hour,
				location.datetime.minute, location.datetime.seconds);
		} else {
			LOG_INF("FIX: {\"lat\":null,\"lon\":null,\"alt\":null,\"acc\":null,\"time\":null}");
		}
		return 0;
	case TRACKER_STATE_LTE_NO_PSM:
		return connect_lte(false);
	case TRACKER_STATE_LTE_PSM:
		err = connect_lte(true);
		if (err != 0) {
			return err;
		}
		tracker_ui_set_state(TRACKER_UI_IDLE);
		err = k_sem_take(&psm_sleep_entered,
			K_SECONDS(CONFIG_TRACKER_PSM_ENTRY_TIMEOUT_SECONDS));
		if (err != 0 || !atomic_get(&modem_in_psm)) {
			LOG_WRN("PSM entry unconfirmed; LTE remains active, press MODE for MQTT_CONNECT");
		}
		return 0;
	case TRACKER_STATE_MQTT_CONNECT:
		err = connect_mqtt();
		if (err == 0) {
			mqtt_send_retry = false;
		}
		return err;
	case TRACKER_STATE_MQTT_SENDING:
		tracker_ui_set_state(TRACKER_UI_LTE_CONNECTED);
		if (mqtt_send_retry) {
			err = connect_mqtt();
			if (err != 0) {
				return err;
			}
		}
		err = tracker_mqtt_publish_location(&report, K_SECONDS(15));
		mqtt_send_retry = err != 0;
		return err;
	case TRACKER_STATE_MQTT_DISCONNECT:
		return tracker_mqtt_disconnect(K_SECONDS(10));
	case TRACKER_STATE_LOW_POWER:
		err = stop_gnss();
		if (err == 0) {
			err = tracker_modem_return_to_gnss();
		}
		if (err == 0) {
			err = lte_lc_psm_req(false);
		}
		if (err != 0) {
			return err;
		}
		atomic_clear(&lte_online);
		atomic_clear(&modem_in_psm);
		tracker_ui_set_state(TRACKER_UI_LOW_POWER);
		LOG_INF("LOW_POWER: LTE off, GNSS stopped, LED off; MODE -> GPS");
		return 0;
	}
	return -EINVAL;
}

int main(void)
{
	enum tracker_state state = TRACKER_STATE_GPS;
	int source_id = log_source_id_get("tracker_modem");
	int err;

	if (!device_is_ready(BUTTON_DEVICE)) {
		LOG_ERR("User button device is not ready");
		return 0;
	}
	if (source_id >= 0) {
		(void)log_filter_set(NULL, Z_LOG_LOCAL_DOMAIN_ID, source_id, LOG_LEVEL_WRN);
	}
	err = tracker_ui_init();
	if (err != 0) {
		LOG_WRN("Tracker UI initialization failed: %d", err);
	}
	err = tracker_modem_init();
	if (err != 0) {
		LOG_ERR("Modem initialization failed: %d", err);
		return 0;
	}
	lte_lc_register_handler(modem_event);
	err = tracker_mqtt_init();
	if (err != 0) {
		LOG_ERR("MQTT initialization failed: %d", err);
		return 0;
	}
	LOG_INF("GPS/LTE/MQTT power test started; eight states controlled by MODE");

	while (true) {
		err = enter_state(state);
		if (err != 0) {
			LOG_ERR("%s failed: %d; press MODE to retry", state_name(state), err);
			if (wait_for_mode() != 0) {
				return 0;
			}
			continue;
		}
		/* GPS already consumed the press that advances to FIX. */
		if (state != TRACKER_STATE_GPS) {
			if (state != TRACKER_STATE_LOW_POWER) {
				tracker_ui_set_state(TRACKER_UI_IDLE);
			}
			LOG_INF("HOLD: %s; MODE -> %s", state_name(state), state_name(next_state(state)));
			if (wait_for_mode() != 0) {
				return 0;
			}
		}
		state = next_state(state);
	}
	return 0;
}
