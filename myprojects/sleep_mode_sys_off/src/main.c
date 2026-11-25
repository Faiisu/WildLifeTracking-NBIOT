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
#include <zephyr/drivers/hwinfo.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/pm/device.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/poweroff.h>

#include <modem/lte_lc.h>
#include <modem/nrf_modem_lib.h>

#include "tracker_cell.h"
#include "tracker_mqtt.h"

LOG_MODULE_REGISTER(sleep_mode_sys_off, LOG_LEVEL_INF);

enum app_state {
	STATE_START,
	STATE_CONNECT_LTE,
	STATE_GET_LOCATION,
	STATE_MQTT_SEND,
	STATE_SYSTEM_OFF,
};

static K_SEM_DEFINE(lte_connected_sem, 0, 1);
static K_SEM_DEFINE(cell_meas_sem, 0, 1);

static atomic_t lte_registered;
static atomic_t meas_active;

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

#if defined(CONFIG_SLEEP_APP_MOTION_WAKEUP)
static const struct gpio_dt_spec accel_irq =
	GPIO_DT_SPEC_GET(DT_ALIAS(accel0), irq_gpios);
#endif

#if defined(CONFIG_SLEEP_APP_BUTTON_WAKEUP) && DT_NODE_HAS_STATUS(DT_ALIAS(sw0), okay)
static const struct gpio_dt_spec wake_button = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
#endif

static void log_reset_cause(void)
{
	uint32_t reset_cause = 0;
	int err = hwinfo_get_reset_cause(&reset_cause);

	if (err != 0) {
		LOG_WRN("Could not read reset cause: %d", err);
		return;
	}

	if (reset_cause & RESET_LOW_POWER_WAKE) {
		LOG_INF("Boot reason: WAKEUP FROM SYSTEM OFF (GPIO DETECT)");
	} else if (reset_cause & RESET_DEBUG) {
		LOG_INF("Boot reason: debugger reset");
	} else if (reset_cause & RESET_PIN) {
		LOG_INF("Boot reason: reset pin");
	} else if (reset_cause & RESET_POR) {
		LOG_INF("Boot reason: power-on reset");
	} else {
		LOG_INF("Boot reason: 0x%08x", reset_cause);
	}

	err = hwinfo_clear_reset_cause();
	if (err != 0) {
		LOG_WRN("Could not clear reset cause: %d", err);
	}
}

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

	default:
		break;
	}
}

static int modem_connect_lte(k_timeout_t timeout)
{
	int err;

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

static void shutdown_modem_cleanly(void)
{
	int err = lte_lc_power_off();

	if (err != 0) {
		LOG_WRN("lte_lc_power_off failed: %d", err);
	}

	err = nrf_modem_lib_shutdown();
	if (err != 0) {
		LOG_WRN("nrf_modem_lib_shutdown failed: %d", err);
	} else {
		LOG_INF("Modem cleanly powered off and shut down");
	}
}

#if defined(CONFIG_SLEEP_APP_MOTION_WAKEUP)
static void accel_dummy_handler(const struct device *dev,
				const struct sensor_trigger *trig)
{
	ARG_UNUSED(dev);
	ARG_UNUSED(trig);
}

static int arm_accel_sys_off_wakeup(void)
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

	if (!gpio_is_ready_dt(&accel_irq)) {
		LOG_ERR("Accelerometer IRQ GPIO is not ready");
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

	err = sensor_trigger_set(accel, &motion_trig, accel_dummy_handler);
	if (err != 0) {
		LOG_ERR("Failed to enable LIS2DH any-motion trigger: %d", err);
		return err;
	}

	/* Wait for deferred LIS2DH I2C register writes to complete. */
	k_sleep(K_MSEC(100));

	err = gpio_pin_configure_dt(&accel_irq, GPIO_INPUT);
	if (err != 0) {
		LOG_ERR("Failed to configure accel IRQ pin as input: %d", err);
		return err;
	}

	if (gpio_pin_get_dt(&accel_irq) > 0) {
		LOG_WRN("Accel IRQ pin is currently active; waiting 150 ms to settle");
		k_sleep(K_MSEC(150));
	}

	err = gpio_pin_interrupt_configure_dt(&accel_irq, GPIO_INT_LEVEL_ACTIVE);
	if (err != 0) {
		LOG_ERR("Failed to arm accel IRQ level sense for System OFF: %d", err);
		return err;
	}

	LOG_INF("LIS2DH12 System OFF wake-up armed on P0.%d (threshold=%d m/s^2, dur=%d)",
		accel_irq.pin,
		CONFIG_SLEEP_APP_MOTION_THRESHOLD_MS2,
		CONFIG_SLEEP_APP_MOTION_DURATION_SAMPLES);

	return 0;
}
#else
static int arm_accel_sys_off_wakeup(void)
{
	const struct device *accel = DEVICE_DT_GET(DT_ALIAS(accel0));

	if (device_is_ready(accel)) {
		struct sensor_value odr = { .val1 = 0, .val2 = 0 };
		int err = sensor_attr_set(accel, SENSOR_CHAN_ACCEL_XYZ,
					  SENSOR_ATTR_SAMPLING_FREQUENCY, &odr);
		if (err != 0) {
			LOG_WRN("Failed to power down LIS2DH12: %d", err);
		} else {
			LOG_INF("LIS2DH12 powered down (ODR=0)");
		}
	}

	return 0;
}
#endif

static int arm_button_sys_off_wakeup(void)
{
#if defined(CONFIG_SLEEP_APP_BUTTON_WAKEUP) && DT_NODE_HAS_STATUS(DT_ALIAS(sw0), okay)
	int err;

	if (!gpio_is_ready_dt(&wake_button)) {
		LOG_ERR("Wake button sw0 GPIO not ready");
		return -ENODEV;
	}

	err = gpio_pin_configure_dt(&wake_button, GPIO_INPUT);
	if (err != 0) {
		LOG_ERR("Failed to configure sw0 as input: %d", err);
		return err;
	}

	/* Wait if MODE button is currently held down so we don't wake immediately. */
	while (gpio_pin_get_dt(&wake_button) > 0) {
		LOG_INF("Waiting for MODE button release...");
		k_sleep(K_MSEC(100));
	}

	err = gpio_pin_interrupt_configure_dt(&wake_button, GPIO_INT_LEVEL_ACTIVE);
	if (err != 0) {
		LOG_ERR("Failed to arm sw0 System OFF wake-up: %d", err);
		return err;
	}

	LOG_INF("MODE button sw0 (P0.%d) System OFF wake-up armed", wake_button.pin);
#endif
	return 0;
}

int main(void)
{
	enum app_state state = STATE_START;
	struct tracker_cell_report cell_report;
	int err;

	status_led_init();
	LOG_INF("=== System 2: LTE(30s) -> Location -> MQTT -> System OFF ===");
	LOG_INF("Wake source: %s%s",
		IS_ENABLED(CONFIG_SLEEP_APP_BUTTON_WAKEUP) ? "MODE button (sw0)" : "",
		IS_ENABLED(CONFIG_SLEEP_APP_MOTION_WAKEUP) ? " + LIS2DH12 motion" : "");

	log_reset_cause();
	suspend_external_flash();

	err = nrf_modem_lib_init();
	if (err != 0) {
		LOG_ERR("nrf_modem_lib_init failed: %d", err);
		return 0;
	}
	lte_lc_register_handler(lte_event_handler);

	err = tracker_mqtt_init();
	if (err != 0) {
		LOG_WRN("MQTT init failed (%d); MQTT sending will be skipped", err);
		mqtt_ready = false;
	} else {
		mqtt_ready = true;
	}

	while (true) {
		switch (state) {
		case STATE_START:
			LOG_INF("[STATE] START");
			state = STATE_CONNECT_LTE;
			break;

		case STATE_CONNECT_LTE:
			LOG_INF("[STATE] LTE (wait up to %d s)",
				CONFIG_SLEEP_APP_LTE_CONNECT_TIMEOUT_SECONDS);
			err = modem_connect_lte(
				K_SECONDS(CONFIG_SLEEP_APP_LTE_CONNECT_TIMEOUT_SECONDS));
			if (err != 0) {
				LOG_WRN("LTE not found -> direct to SYSTEM OFF");
				state = STATE_SYSTEM_OFF;
			} else {
				state = STATE_GET_LOCATION;
			}
			break;

		case STATE_GET_LOCATION:
			LOG_INF("[STATE] GET LOCATION (LTE cell measurement)");
			err = modem_measure_cells(
				&cell_report,
				K_SECONDS(CONFIG_SLEEP_APP_CELL_MEAS_TIMEOUT_SECONDS));
			if (err != 0) {
				LOG_WRN("Get location failed (%d) -> SYSTEM OFF", err);
				state = STATE_SYSTEM_OFF;
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
			state = STATE_SYSTEM_OFF;
			break;

		case STATE_SYSTEM_OFF:
			LOG_INF("[STATE] SYSTEM OFF");
			shutdown_modem_cleanly();

			err = arm_accel_sys_off_wakeup();
			if (err != 0) {
				LOG_ERR("Failed to prepare accel (%d); aborting System OFF", err);
				return 0;
			}
			err = arm_button_sys_off_wakeup();
			if (err != 0) {
				LOG_ERR("Failed to arm MODE button wake-up (%d); aborting System OFF",
					err);
				return 0;
			}

			LOG_INF("Entering nRF9160 System OFF now (press MODE button to wake)...");
			k_sleep(K_MSEC(150));
			sys_poweroff();
			break;
		}
	}

	return 0;
}
