/*
 * Copyright (c) 2026 Cellular Asset Tracker Project
 * SPDX-License-Identifier: Apache-2.0
 */

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/pm/device.h>
#include <zephyr/sys/atomic.h>

#include <modem/lte_lc.h>
#include <modem/nrf_modem_lib.h>

#include "tracker_cell.h"
#include "tracker_mqtt.h"

LOG_MODULE_REGISTER(sleep_mode_psm, LOG_LEVEL_INF);

enum app_state {
	STATE_START,
	STATE_CONNECT_LTE,
	STATE_GET_LOCATION,
	STATE_MQTT_SEND,
	STATE_SLEEP,
};

static K_SEM_DEFINE(lte_connected_sem, 0, 1);
static K_SEM_DEFINE(cell_meas_sem, 0, 1);
static K_SEM_DEFINE(psm_entered_sem, 0, 1);
static K_SEM_DEFINE(wake_sem, 0, 1);

static atomic_t lte_registered;
static atomic_t meas_active;
static atomic_t psm_granted;
static atomic_t modem_in_psm;
static atomic_t motion_detected;
static atomic_t button_pressed;

#if defined(CONFIG_SLEEP_APP_BUTTON_WAKEUP) && DT_NODE_HAS_STATUS(DT_ALIAS(sw0), okay)
static const struct gpio_dt_spec wake_button = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
static struct gpio_callback wake_button_cb_data;

static void wake_button_isr(const struct device *dev, struct gpio_callback *cb,
			    uint32_t pins)
{
	ARG_UNUSED(dev);
	ARG_UNUSED(cb);
	ARG_UNUSED(pins);

	atomic_set(&button_pressed, 1);
	k_sem_give(&wake_sem);
}
#endif

static struct tracker_cell_report latest_meas;
static int meas_result;
static bool mqtt_ready;

#if DT_NODE_HAS_STATUS(DT_ALIAS(led0), okay) && defined(CONFIG_SLEEP_APP_STATUS_LED)
static const struct gpio_dt_spec status_led = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
static bool status_led_ready;
static bool lte_blink_led_on;
static atomic_t lte_blinking;
static struct k_work_delayable lte_blink_work;

static void lte_blink_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	if (!status_led_ready || !atomic_get(&lte_blinking)) {
		return;
	}

	lte_blink_led_on = !lte_blink_led_on;
	(void)gpio_pin_set_dt(&status_led, lte_blink_led_on ? 1 : 0);
	(void)k_work_reschedule(&lte_blink_work,
				K_MSEC(CONFIG_SLEEP_APP_LTE_BLINK_INTERVAL_MS));
}
#endif

static void status_led_init(void)
{
#if DT_NODE_HAS_STATUS(DT_ALIAS(led0), okay) && defined(CONFIG_SLEEP_APP_STATUS_LED)
	if (!gpio_is_ready_dt(&status_led)) {
		LOG_WRN("Status LED GPIO not ready");
		return;
	}

	int err = gpio_pin_configure_dt(&status_led, GPIO_OUTPUT_INACTIVE);
	if (err != 0) {
		LOG_WRN("Failed to configure status LED: %d", err);
		return;
	}

	k_work_init_delayable(&lte_blink_work, lte_blink_work_handler);
	status_led_ready = true;
#endif
}

static void status_led_lte_blink_start(void)
{
#if DT_NODE_HAS_STATUS(DT_ALIAS(led0), okay) && defined(CONFIG_SLEEP_APP_STATUS_LED)
	if (!status_led_ready) {
		return;
	}

	atomic_set(&lte_blinking, 1);
	lte_blink_led_on = true;
	(void)gpio_pin_set_dt(&status_led, 1);
	(void)k_work_reschedule(&lte_blink_work,
				K_MSEC(CONFIG_SLEEP_APP_LTE_BLINK_INTERVAL_MS));
#endif
}

static void status_led_lte_blink_stop(void)
{
#if DT_NODE_HAS_STATUS(DT_ALIAS(led0), okay) && defined(CONFIG_SLEEP_APP_STATUS_LED)
	struct k_work_sync sync;

	if (!status_led_ready) {
		return;
	}

	atomic_clear(&lte_blinking);
	(void)k_work_cancel_delayable_sync(&lte_blink_work, &sync);
	lte_blink_led_on = false;
	(void)gpio_pin_set_dt(&status_led, 0);
#endif
}

static void suspend_external_flash(void)
{
#if DT_NODE_HAS_STATUS(DT_NODELABEL(w25q32jv), okay) && \
	defined(CONFIG_PM_DEVICE) && defined(CONFIG_SPI_NOR)
	const struct device *flash_dev = DEVICE_DT_GET(DT_NODELABEL(w25q32jv));

	if (!device_is_ready(flash_dev)) {
		LOG_WRN("External flash w25q32jv not ready; skipping suspend");
		return;
	}

	int err = pm_device_action_run(flash_dev, PM_DEVICE_ACTION_SUSPEND);
	if (err != 0 && err != -EALREADY) {
		LOG_WRN("Failed to suspend external flash: %d", err);
	} else {
		LOG_INF("External flash suspended (Deep Power-Down)");
	}
#endif
}

static int16_t convert_rsrp_dbm(int16_t rsrp_idx)
{
	if (rsrp_idx == LTE_LC_CELL_RSRP_INVALID) {
		return TRACKER_RSRP_DBM_INVALID;
	}
	return (rsrp_idx < 0) ? (rsrp_idx - 140) : (rsrp_idx - 141);
}

static int16_t convert_rsrq(int16_t rsrq_idx)
{
	if (rsrq_idx == LTE_LC_CELL_RSRQ_INVALID) {
		return TRACKER_RSRQ_INVALID;
	}
	return rsrq_idx;
}

static void handle_neighbor_cell_meas(const struct lte_lc_cells_info *cells)
{
	if (!atomic_cas(&meas_active, 1, 0)) {
		return;
	}

	memset(&latest_meas, 0, sizeof(latest_meas));
	if (cells->current_cell.id == LTE_LC_CELL_EUTRAN_ID_INVALID) {
		meas_result = -ENODATA;
		LOG_WRN("Cell measurement completed without a valid serving cell");
		k_sem_give(&cell_meas_sem);
		return;
	}

	latest_meas.uptime_ms = k_uptime_get();
	latest_meas.meas_time_ms = cells->current_cell.measurement_time;
	latest_meas.mcc = cells->current_cell.mcc;
	latest_meas.mnc = cells->current_cell.mnc;
	latest_meas.tac = cells->current_cell.tac;
	latest_meas.cell_id = cells->current_cell.id;
	latest_meas.earfcn = cells->current_cell.earfcn;
	latest_meas.pci = cells->current_cell.phys_cell_id;
	latest_meas.rsrp_dbm = convert_rsrp_dbm(cells->current_cell.rsrp);
	latest_meas.rsrq = convert_rsrq(cells->current_cell.rsrq);

#if CONFIG_TRACKER_MAX_NEIGHBOR_CELLS > 0
	if (cells->neighbor_cells != NULL && cells->ncells_count > 0) {
		uint8_t copy_count = cells->ncells_count;

		if (copy_count > CONFIG_TRACKER_MAX_NEIGHBOR_CELLS) {
			copy_count = CONFIG_TRACKER_MAX_NEIGHBOR_CELLS;
		}
		latest_meas.ncell_count = copy_count;
		for (uint8_t i = 0; i < copy_count; i++) {
			latest_meas.ncells[i].earfcn = cells->neighbor_cells[i].earfcn;
			latest_meas.ncells[i].pci = cells->neighbor_cells[i].phys_cell_id;
			latest_meas.ncells[i].rsrp_dbm =
				convert_rsrp_dbm(cells->neighbor_cells[i].rsrp);
			latest_meas.ncells[i].rsrq =
				convert_rsrq(cells->neighbor_cells[i].rsrq);
		}
	}
#endif

	meas_result = 0;
	LOG_INF("Cell location: MCC=%d MNC=%d TAC=%u CID=%u PCI=%u RSRP=%d dBm (ncells=%u)",
		latest_meas.mcc, latest_meas.mnc, latest_meas.tac,
		latest_meas.cell_id, latest_meas.pci, latest_meas.rsrp_dbm,
		latest_meas.ncell_count);
	k_sem_give(&cell_meas_sem);
}

static void lte_event_handler(const struct lte_lc_evt *const evt)
{
	switch (evt->type) {
	case LTE_LC_EVT_NW_REG_STATUS:
		if (evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ||
		    evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_ROAMING) {
			atomic_set(&lte_registered, 1);
			LOG_INF("LTE registered (%s)",
				evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ?
				"home" : "roaming");
			k_sem_give(&lte_connected_sem);
		} else {
			atomic_clear(&lte_registered);
		}
		break;

	case LTE_LC_EVT_NEIGHBOR_CELL_MEAS:
		handle_neighbor_cell_meas(&evt->cells_info);
		break;

	case LTE_LC_EVT_PSM_UPDATE:
		LOG_INF("PSM update: TAU=%d s, active_time=%d s",
			evt->psm_cfg.tau, evt->psm_cfg.active_time);
		if (evt->psm_cfg.tau >= 0 && evt->psm_cfg.active_time >= 0) {
			atomic_set(&psm_granted, 1);
		} else {
			atomic_clear(&psm_granted);
			LOG_WRN("Network did not grant PSM");
		}
		break;

	case LTE_LC_EVT_MODEM_SLEEP_ENTER:
		if (evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
			atomic_set(&modem_in_psm, 1);
			LOG_INF("Modem entered PSM for %lld ms", evt->modem_sleep.time);
			k_sem_give(&psm_entered_sem);
		}
		break;

	case LTE_LC_EVT_MODEM_SLEEP_EXIT:
		if (evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
			atomic_clear(&modem_in_psm);
			LOG_INF("Modem exited PSM");
		}
		break;

	case LTE_LC_EVT_RRC_UPDATE:
		LOG_INF("RRC mode: %s",
			evt->rrc_mode == LTE_LC_RRC_MODE_CONNECTED ? "connected" : "idle");
		break;

	default:
		break;
	}
}

static int modem_setup(void)
{
	int err;

	err = nrf_modem_lib_init();
	if (err != 0) {
		LOG_ERR("nrf_modem_lib_init failed: %d", err);
		return err;
	}

	lte_lc_register_handler(lte_event_handler);

	err = lte_lc_psm_param_set_seconds(CONFIG_SLEEP_APP_PSM_PERIODIC_TAU_SECONDS,
					   CONFIG_SLEEP_APP_PSM_ACTIVE_TIME_SECONDS);
	if (err != 0) {
		LOG_ERR("lte_lc_psm_param_set_seconds failed: %d", err);
		return err;
	}

	err = lte_lc_psm_req(true);
	if (err != 0) {
		LOG_WRN("lte_lc_psm_req(true) failed: %d", err);
	}

	return 0;
}

static int modem_connect_lte(k_timeout_t timeout)
{
	int err;

	if (atomic_get(&lte_registered)) {
		LOG_INF("LTE registration already retained");
		return 0;
	}

	k_sem_reset(&lte_connected_sem);
	status_led_lte_blink_start();

	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_NORMAL);
	if (err != 0) {
		LOG_ERR("Failed to activate LTE: %d", err);
		status_led_lte_blink_stop();
		return err;
	}

	err = k_sem_take(&lte_connected_sem, timeout);
	status_led_lte_blink_stop();
	if (err != 0) {
		LOG_WRN("LTE not found within %d s",
			CONFIG_SLEEP_APP_LTE_CONNECT_TIMEOUT_SECONDS);
		return -ETIMEDOUT;
	}

	return 0;
}

static int modem_measure_cells(struct tracker_cell_report *out, k_timeout_t timeout)
{
	struct lte_lc_ncellmeas_params params = {
		.search_type = LTE_LC_NEIGHBOR_SEARCH_TYPE_DEFAULT,
	};
	int err;

	k_sem_reset(&cell_meas_sem);
	meas_result = -ENODATA;
	atomic_set(&meas_active, 1);

	err = lte_lc_neighbor_cell_measurement(&params);
	if (err != 0) {
		atomic_clear(&meas_active);
		LOG_ERR("lte_lc_neighbor_cell_measurement failed: %d", err);
		return err;
	}

	err = k_sem_take(&cell_meas_sem, timeout);
	if (err != 0) {
		if (atomic_cas(&meas_active, 1, 0)) {
			(void)lte_lc_neighbor_cell_measurement_cancel();
			LOG_WRN("LTE cell measurement timed out");
			return -ETIMEDOUT;
		}
		err = k_sem_take(&cell_meas_sem, K_MSEC(100));
		if (err != 0) {
			return -ETIMEDOUT;
		}
	}

	if (meas_result != 0) {
		return meas_result;
	}

	*out = latest_meas;
	return 0;
}

static void modem_enter_low_power(bool wait_for_psm)
{
	int err;

	if (wait_for_psm && atomic_get(&lte_registered) && atomic_get(&psm_granted)) {
		if (atomic_get(&modem_in_psm)) {
			LOG_INF("Modem already in PSM");
			return;
		}

		k_sem_reset(&psm_entered_sem);
		LOG_INF("Waiting for modem PSM entry (timeout %d s)...",
			CONFIG_SLEEP_APP_PSM_ENTRY_TIMEOUT_SECONDS);
		err = k_sem_take(&psm_entered_sem,
				 K_SECONDS(CONFIG_SLEEP_APP_PSM_ENTRY_TIMEOUT_SECONDS));
		if (err == 0 || atomic_get(&modem_in_psm)) {
			return;
		}
		LOG_WRN("PSM entry timed out; deactivating LTE radio");
	} else {
		LOG_INF("Deactivating LTE radio before sleep");
	}

	atomic_clear(&lte_registered);
	atomic_clear(&psm_granted);
	atomic_clear(&modem_in_psm);
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_DEACTIVATE_LTE);
	if (err != 0) {
		LOG_ERR("Failed to deactivate LTE: %d", err);
	}
}

#if defined(CONFIG_SLEEP_APP_MOTION_WAKEUP)
static void accel_motion_handler(const struct device *dev,
				 const struct sensor_trigger *trig)
{
	ARG_UNUSED(dev);
	ARG_UNUSED(trig);

	atomic_set(&motion_detected, 1);
	k_sem_give(&wake_sem);
}

static int accel_setup(void)
{
	const struct device *accel = DEVICE_DT_GET(DT_ALIAS(accel0));
	struct sensor_value slope_th = {
		.val1 = CONFIG_SLEEP_APP_MOTION_THRESHOLD_MS2,
		.val2 = 0,
	};
	struct sensor_value slope_dur = {
		.val1 = CONFIG_SLEEP_APP_MOTION_DURATION_SAMPLES,
		.val2 = 0,
	};
	static const struct sensor_trigger motion_trig = {
		.type = SENSOR_TRIG_DELTA,
		.chan = SENSOR_CHAN_ACCEL_XYZ,
	};
	int err;

	if (!device_is_ready(accel)) {
		LOG_ERR("Accelerometer accel0 is not ready");
		return -ENODEV;
	}

	err = sensor_attr_set(accel, SENSOR_CHAN_ACCEL_XYZ,
			      SENSOR_ATTR_SLOPE_TH, &slope_th);
	if (err != 0) {
		LOG_ERR("Failed to set LIS2DH slope threshold: %d", err);
		return err;
	}

	err = sensor_attr_set(accel, SENSOR_CHAN_ACCEL_XYZ,
			      SENSOR_ATTR_SLOPE_DUR, &slope_dur);
	if (err != 0) {
		LOG_ERR("Failed to set LIS2DH slope duration: %d", err);
		return err;
	}

	err = sensor_trigger_set(accel, &motion_trig, accel_motion_handler);
	if (err != 0) {
		LOG_ERR("Failed to enable LIS2DH any-motion trigger: %d", err);
		return err;
	}

	LOG_INF("LIS2DH12 motion wake-up armed (threshold=%d m/s^2, dur=%d samples)",
		CONFIG_SLEEP_APP_MOTION_THRESHOLD_MS2,
		CONFIG_SLEEP_APP_MOTION_DURATION_SAMPLES);

	return 0;
}
#else
static int accel_setup(void)
{
	const struct device *accel = DEVICE_DT_GET(DT_ALIAS(accel0));

	if (device_is_ready(accel)) {
		struct sensor_value odr = { .val1 = 0, .val2 = 0 };
		int err = sensor_attr_set(accel, SENSOR_CHAN_ACCEL_XYZ,
					  SENSOR_ATTR_SAMPLING_FREQUENCY, &odr);
		if (err != 0) {
			LOG_WRN("Failed to power down LIS2DH12: %d", err);
		}
	}

	return 0;
}
#endif

static int button_setup(void)
{
#if defined(CONFIG_SLEEP_APP_BUTTON_WAKEUP) && DT_NODE_HAS_STATUS(DT_ALIAS(sw0), okay)
	int err;

	if (!gpio_is_ready_dt(&wake_button)) {
		LOG_ERR("MODE button sw0 GPIO is not ready");
		return -ENODEV;
	}

	err = gpio_pin_configure_dt(&wake_button, GPIO_INPUT);
	if (err != 0) {
		LOG_ERR("Failed to configure sw0 as input: %d", err);
		return err;
	}

	err = gpio_pin_interrupt_configure_dt(&wake_button, GPIO_INT_EDGE_TO_ACTIVE);
	if (err != 0) {
		LOG_ERR("Failed to configure sw0 interrupt: %d", err);
		return err;
	}

	gpio_init_callback(&wake_button_cb_data, wake_button_isr, BIT(wake_button.pin));
	err = gpio_add_callback(wake_button.port, &wake_button_cb_data);
	if (err != 0) {
		LOG_ERR("Failed to add sw0 callback: %d", err);
		return err;
	}

	LOG_INF("MODE button sw0 (P0.%d) wake-up armed", wake_button.pin);
#endif
	return 0;
}

int main(void)
{
	enum app_state state = STATE_START;
	struct tracker_cell_report cell_report;
	uint32_t cycle_count = 0;
	bool lte_found = false;
	int err;

	status_led_init();
	LOG_INF("=== System 1: LTE(30s) -> Location -> MQTT -> Sleep(30s) ===");

	suspend_external_flash();

	err = modem_setup();
	if (err != 0) {
		LOG_ERR("Modem setup failed: %d", err);
		return 0;
	}

	err = tracker_mqtt_init();
	if (err != 0) {
		LOG_WRN("MQTT init failed (%d); MQTT sending will be skipped", err);
		mqtt_ready = false;
	} else {
		mqtt_ready = true;
	}

	err = accel_setup();
	if (err != 0) {
		LOG_ERR("Accelerometer setup failed: %d", err);
		return 0;
	}

	err = button_setup();
	if (err != 0) {
		LOG_ERR("Button setup failed: %d", err);
		return 0;
	}

	/* Allow deferred LIS2DH trigger work to complete before first cycle. */
	k_sleep(K_MSEC(100));

	while (true) {
		switch (state) {
		case STATE_START:
			cycle_count++;
			lte_found = false;
			LOG_INF("[STATE] START (cycle %u)", cycle_count);
			state = STATE_CONNECT_LTE;
			break;

		case STATE_CONNECT_LTE:
			LOG_INF("[STATE] LTE (wait up to %d s)",
				CONFIG_SLEEP_APP_LTE_CONNECT_TIMEOUT_SECONDS);
			err = modem_connect_lte(
				K_SECONDS(CONFIG_SLEEP_APP_LTE_CONNECT_TIMEOUT_SECONDS));
			if (err != 0) {
				LOG_WRN("LTE not found -> direct to SLEEP");
				state = STATE_SLEEP;
			} else {
				lte_found = true;
				state = STATE_GET_LOCATION;
			}
			break;

		case STATE_GET_LOCATION:
			LOG_INF("[STATE] GET LOCATION (LTE cell measurement)");
			err = modem_measure_cells(
				&cell_report,
				K_SECONDS(CONFIG_SLEEP_APP_CELL_MEAS_TIMEOUT_SECONDS));
			if (err != 0) {
				LOG_WRN("Get location failed (%d) -> SLEEP", err);
				state = STATE_SLEEP;
			} else {
				state = STATE_MQTT_SEND;
			}
			break;

		case STATE_MQTT_SEND:
			LOG_INF("[STATE] MQTT SENDING");
			if (!mqtt_ready) {
				LOG_WRN("MQTT not initialized (check CA cert/credentials)");
			} else {
				err = tracker_mqtt_connect(K_SECONDS(15));
				if (err == 0) {
					err = tracker_mqtt_publish_cell_report(
						&cell_report, K_SECONDS(15));
					if (err != 0) {
						LOG_ERR("MQTT publish failed: %d", err);
					}
				}
				(void)tracker_mqtt_disconnect(K_SECONDS(10));
			}
			state = STATE_SLEEP;
			break;

		case STATE_SLEEP:
			LOG_INF("[STATE] SLEEP (%d s, button wake=%s, motion wake=%s)",
				CONFIG_SLEEP_APP_INTERVAL_SECONDS,
				IS_ENABLED(CONFIG_SLEEP_APP_BUTTON_WAKEUP) ? "on" : "off",
				IS_ENABLED(CONFIG_SLEEP_APP_MOTION_WAKEUP) ? "on" : "off");
			modem_enter_low_power(lte_found);

			atomic_clear(&motion_detected);
			atomic_clear(&button_pressed);
			k_sem_reset(&wake_sem);

			err = k_sem_take(&wake_sem,
					 K_SECONDS(CONFIG_SLEEP_APP_INTERVAL_SECONDS));
			if (err == 0 && atomic_get(&button_pressed)) {
				LOG_INF("Woke up early by MODE button");
			} else if (err == 0 && atomic_get(&motion_detected)) {
				LOG_INF("Woke up early by LIS2DH12 MOTION");
			} else if (err == -EAGAIN) {
				LOG_INF("Woke up by %d s TIMER",
					CONFIG_SLEEP_APP_INTERVAL_SECONDS);
			}
			state = STATE_START;
			break;
		}
	}

	return 0;
}
