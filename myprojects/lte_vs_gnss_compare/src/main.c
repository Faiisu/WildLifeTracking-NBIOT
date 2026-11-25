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
#include <nrf_errno.h>
#include <nrf_modem_gnss.h>

#include "tracker_data.h"
#include "tracker_mqtt.h"

LOG_MODULE_REGISTER(lte_vs_gnss_compare, LOG_LEVEL_INF);

enum compare_stage {
	STAGE_1_LTE = 1,
	STAGE_2_GNSS = 2,
};

static K_SEM_DEFINE(mode_btn_sem, 0, 1);
static K_SEM_DEFINE(lte_connected_sem, 0, 1);
static K_SEM_DEFINE(cell_meas_sem, 0, 1);
static K_SEM_DEFINE(gnss_fix_sem, 0, 1);

static atomic_t lte_registered;
static atomic_t meas_active;
static atomic_t gnss_fix_wanted;

static struct tracker_cell_report latest_cell_meas;
static int cell_meas_result;

static struct tracker_gnss_fix latest_gnss_fix;
static bool mqtt_ready;

/* MODE button (sw0, P0.12) with software debounce */
static const struct gpio_dt_spec mode_btn = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
static struct gpio_callback mode_btn_cb_data;
static int64_t last_mode_btn_ms;

static void mode_btn_isr(const struct device *dev, struct gpio_callback *cb,
			 uint32_t pins)
{
	int64_t now = k_uptime_get();

	ARG_UNUSED(dev);
	ARG_UNUSED(cb);
	ARG_UNUSED(pins);

	if ((now - last_mode_btn_ms) < CONFIG_COMPARE_BUTTON_DEBOUNCE_MS) {
		return;
	}

	last_mode_btn_ms = now;
	k_sem_give(&mode_btn_sem);
}

static int mode_btn_init(void)
{
	int err;

	if (!gpio_is_ready_dt(&mode_btn)) {
		LOG_ERR("MODE button sw0 GPIO not ready");
		return -ENODEV;
	}

	err = gpio_pin_configure_dt(&mode_btn, GPIO_INPUT);
	if (err != 0) {
		LOG_ERR("Failed to configure sw0 as input: %d", err);
		return err;
	}

	err = gpio_pin_interrupt_configure_dt(&mode_btn, GPIO_INT_EDGE_TO_ACTIVE);
	if (err != 0) {
		LOG_ERR("Failed to configure sw0 interrupt: %d", err);
		return err;
	}

	gpio_init_callback(&mode_btn_cb_data, mode_btn_isr, BIT(mode_btn.pin));
	err = gpio_add_callback(mode_btn.port, &mode_btn_cb_data);
	if (err != 0) {
		LOG_ERR("Failed to add sw0 callback: %d", err);
		return err;
	}

	LOG_INF("MODE button sw0 (P0.%d) ready (debounce=%d ms)",
		mode_btn.pin, CONFIG_COMPARE_BUTTON_DEBOUNCE_MS);
	return 0;
}

/* Status LED blinker: slow (500 ms) for Stage 1, fast (150 ms) for Stage 2, off in SLEEP */
#if DT_NODE_HAS_STATUS(DT_ALIAS(led0), okay) && defined(CONFIG_COMPARE_STATUS_LED)
static const struct gpio_dt_spec status_led = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
static bool status_led_ready;
static bool led_state_on;
static uint32_t led_interval_ms;
static atomic_t led_blinking;
static struct k_work_delayable led_blink_work;

static void led_blink_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	if (!status_led_ready || !atomic_get(&led_blinking)) {
		return;
	}

	led_state_on = !led_state_on;
	(void)gpio_pin_set_dt(&status_led, led_state_on ? 1 : 0);
	(void)k_work_reschedule(&led_blink_work, K_MSEC(led_interval_ms));
}
#endif

static void status_led_init(void)
{
#if DT_NODE_HAS_STATUS(DT_ALIAS(led0), okay) && defined(CONFIG_COMPARE_STATUS_LED)
	if (!gpio_is_ready_dt(&status_led)) {
		LOG_WRN("Status LED GPIO not ready");
		return;
	}

	int err = gpio_pin_configure_dt(&status_led, GPIO_OUTPUT_INACTIVE);
	if (err != 0) {
		LOG_WRN("Failed to configure status LED: %d", err);
		return;
	}

	k_work_init_delayable(&led_blink_work, led_blink_work_handler);
	status_led_ready = true;
#endif
}

static void status_led_start(uint32_t interval_ms)
{
#if DT_NODE_HAS_STATUS(DT_ALIAS(led0), okay) && defined(CONFIG_COMPARE_STATUS_LED)
	if (!status_led_ready) {
		return;
	}

	led_interval_ms = interval_ms;
	atomic_set(&led_blinking, 1);
	led_state_on = true;
	(void)gpio_pin_set_dt(&status_led, 1);
	(void)k_work_reschedule(&led_blink_work, K_MSEC(led_interval_ms));
#else
	ARG_UNUSED(interval_ms);
#endif
}

static void status_led_stop(void)
{
#if DT_NODE_HAS_STATUS(DT_ALIAS(led0), okay) && defined(CONFIG_COMPARE_STATUS_LED)
	struct k_work_sync sync;

	if (!status_led_ready) {
		return;
	}

	atomic_clear(&led_blinking);
	(void)k_work_cancel_delayable_sync(&led_blink_work, &sync);
	led_state_on = false;
	(void)gpio_pin_set_dt(&status_led, 0);
#endif
}

static void power_down_unused_peripherals(void)
{
#if DT_NODE_HAS_STATUS(DT_NODELABEL(w25q32jv), okay) && \
	defined(CONFIG_PM_DEVICE) && defined(CONFIG_SPI_NOR)
	const struct device *flash_dev = DEVICE_DT_GET(DT_NODELABEL(w25q32jv));

	if (device_is_ready(flash_dev)) {
		int err = pm_device_action_run(flash_dev, PM_DEVICE_ACTION_SUSPEND);
		if (err == 0 || err == -EALREADY) {
			LOG_INF("External flash suspended (Deep Power-Down)");
		} else {
			LOG_WRN("Failed to suspend external flash: %d", err);
		}
	}
#endif

#if DT_NODE_HAS_STATUS(DT_ALIAS(accel0), okay) && defined(CONFIG_LIS2DH)
	const struct device *accel = DEVICE_DT_GET(DT_ALIAS(accel0));

	if (device_is_ready(accel)) {
		struct sensor_value odr = { .val1 = 0, .val2 = 0 };
		int err = sensor_attr_set(accel, SENSOR_CHAN_ACCEL_XYZ,
					  SENSOR_ATTR_SAMPLING_FREQUENCY, &odr);
		if (err == 0) {
			LOG_INF("LIS2DH12 powered down (ODR=0)");
		} else {
			LOG_WRN("Failed to power down LIS2DH12: %d", err);
		}
	}
#endif
}

/* Wait on target_sem or abort immediately if MODE button is pressed. */
static int wait_or_mode_abort(struct k_sem *target_sem, k_timeout_t timeout)
{
	struct k_poll_event events[2] = {
		K_POLL_EVENT_STATIC_INITIALIZER(K_POLL_TYPE_SEM_AVAILABLE,
						K_POLL_MODE_NOTIFY_ONLY,
						&mode_btn_sem, 0),
		K_POLL_EVENT_STATIC_INITIALIZER(K_POLL_TYPE_SEM_AVAILABLE,
						K_POLL_MODE_NOTIFY_ONLY,
						target_sem, 0),
	};
	int err = k_poll(events, ARRAY_SIZE(events), timeout);

	if (err == -EAGAIN) {
		return -ETIMEDOUT;
	}
	if (err != 0) {
		return err;
	}

	if (events[0].state == K_POLL_STATE_SEM_AVAILABLE) {
		(void)k_sem_take(&mode_btn_sem, K_NO_WAIT);
		return -ECANCELED;
	}

	if (events[1].state == K_POLL_STATE_SEM_AVAILABLE) {
		(void)k_sem_take(target_sem, K_NO_WAIT);
		return 0;
	}

	return -ETIMEDOUT;
}

static bool mode_abort_pending(void)
{
	return k_sem_take(&mode_btn_sem, K_NO_WAIT) == 0;
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

	memset(&latest_cell_meas, 0, sizeof(latest_cell_meas));
	if (cells->current_cell.id == LTE_LC_CELL_EUTRAN_ID_INVALID) {
		cell_meas_result = -ENODATA;
		LOG_WRN("Cell measurement completed without a valid serving cell");
		k_sem_give(&cell_meas_sem);
		return;
	}

	latest_cell_meas.uptime_ms = k_uptime_get();
	latest_cell_meas.meas_time_ms = cells->current_cell.measurement_time;
	latest_cell_meas.mcc = cells->current_cell.mcc;
	latest_cell_meas.mnc = cells->current_cell.mnc;
	latest_cell_meas.tac = cells->current_cell.tac;
	latest_cell_meas.cell_id = cells->current_cell.id;
	latest_cell_meas.earfcn = cells->current_cell.earfcn;
	latest_cell_meas.pci = cells->current_cell.phys_cell_id;
	latest_cell_meas.rsrp_dbm = convert_rsrp_dbm(cells->current_cell.rsrp);
	latest_cell_meas.rsrq = convert_rsrq(cells->current_cell.rsrq);

#if CONFIG_TRACKER_MAX_NEIGHBOR_CELLS > 0
	if (cells->neighbor_cells != NULL && cells->ncells_count > 0) {
		uint8_t copy_count = cells->ncells_count;

		if (copy_count > CONFIG_TRACKER_MAX_NEIGHBOR_CELLS) {
			copy_count = CONFIG_TRACKER_MAX_NEIGHBOR_CELLS;
		}
		latest_cell_meas.ncell_count = copy_count;
		for (uint8_t i = 0; i < copy_count; i++) {
			latest_cell_meas.ncells[i].earfcn = cells->neighbor_cells[i].earfcn;
			latest_cell_meas.ncells[i].pci = cells->neighbor_cells[i].phys_cell_id;
			latest_cell_meas.ncells[i].rsrp_dbm =
				convert_rsrp_dbm(cells->neighbor_cells[i].rsrp);
			latest_cell_meas.ncells[i].rsrq =
				convert_rsrq(cells->neighbor_cells[i].rsrq);
		}
	}
#endif

	cell_meas_result = 0;
	LOG_INF("[STAGE 1] Cell location: MCC=%d MNC=%d TAC=%u CID=%u PCI=%u RSRP=%d dBm (ncells=%u)",
		latest_cell_meas.mcc, latest_cell_meas.mnc, latest_cell_meas.tac,
		latest_cell_meas.cell_id, latest_cell_meas.pci, latest_cell_meas.rsrp_dbm,
		latest_cell_meas.ncell_count);
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

static void gnss_event_handler(int event)
{
	struct nrf_modem_gnss_pvt_data_frame pvt;
	int err;

	if (event != NRF_MODEM_GNSS_EVT_PVT) {
		return;
	}

	err = nrf_modem_gnss_read(&pvt, sizeof(pvt), NRF_MODEM_GNSS_DATA_PVT);
	if (err != 0) {
		LOG_ERR("nrf_modem_gnss_read failed: %d", err);
		return;
	}

	if ((pvt.flags & NRF_MODEM_GNSS_PVT_FLAG_FIX_VALID) != 0 &&
	    atomic_cas(&gnss_fix_wanted, 1, 0)) {
		latest_gnss_fix.latitude = pvt.latitude;
		latest_gnss_fix.longitude = pvt.longitude;
		latest_gnss_fix.altitude = pvt.altitude;
		latest_gnss_fix.accuracy = pvt.accuracy;
		latest_gnss_fix.datetime = pvt.datetime;
		LOG_INF("[STAGE 2] GNSS fix: lat=%.6f lon=%.6f alt=%.1f m acc=%.1f m",
			latest_gnss_fix.latitude, latest_gnss_fix.longitude,
			(double)latest_gnss_fix.altitude, (double)latest_gnss_fix.accuracy);
		k_sem_give(&gnss_fix_sem);
	}
}

static void radio_turn_off_lte(void)
{
	int err;

	atomic_clear(&lte_registered);
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_DEACTIVATE_LTE);
	if (err != 0) {
		LOG_WRN("Failed to deactivate LTE: %d", err);
	} else {
		LOG_INF("LTE turned OFF");
	}
}

static void radio_turn_off_gnss(void)
{
	int err;

	atomic_clear(&gnss_fix_wanted);
	err = nrf_modem_gnss_stop();
	if (err != 0 && err != -NRF_EPERM) {
		LOG_WRN("nrf_modem_gnss_stop returned: %d", err);
	}

	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_DEACTIVATE_GNSS);
	if (err != 0) {
		LOG_WRN("Failed to deactivate GNSS: %d", err);
	} else {
		LOG_INF("GNSS turned OFF");
	}
}

static int radio_open_and_connect_lte(k_timeout_t timeout)
{
	int err;

	if (mode_abort_pending()) {
		return -ECANCELED;
	}

	k_sem_reset(&lte_connected_sem);
	LOG_INF("Opening LTE and connecting (timeout %d s)...",
		CONFIG_COMPARE_LTE_TIMEOUT_SECONDS);
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_NORMAL);
	if (err != 0) {
		LOG_ERR("Failed to open LTE: %d", err);
		return err;
	}

	err = wait_or_mode_abort(&lte_connected_sem, timeout);
	if (err == -ECANCELED) {
		LOG_WRN("LTE connect aborted by MODE button");
	} else if (err != 0) {
		LOG_WRN("LTE registration timed out");
	}

	return err;
}

static int stage1_get_cell_location(struct tracker_cell_report *out, k_timeout_t timeout)
{
	struct lte_lc_ncellmeas_params params = {
		.search_type = LTE_LC_NEIGHBOR_SEARCH_TYPE_DEFAULT,
	};
	int err;

	if (mode_abort_pending()) {
		return -ECANCELED;
	}

	k_sem_reset(&cell_meas_sem);
	cell_meas_result = -ENODATA;
	atomic_set(&meas_active, 1);

	LOG_INF("[STAGE 1] Getting LTE cell location...");
	err = lte_lc_neighbor_cell_measurement(&params);
	if (err != 0) {
		atomic_clear(&meas_active);
		LOG_ERR("lte_lc_neighbor_cell_measurement failed: %d", err);
		return err;
	}

	err = wait_or_mode_abort(&cell_meas_sem, timeout);
	if (err != 0) {
		if (atomic_cas(&meas_active, 1, 0)) {
			(void)lte_lc_neighbor_cell_measurement_cancel();
		}
		if (err == -ECANCELED) {
			LOG_WRN("[STAGE 1] Cell measurement aborted by MODE button");
		} else {
			LOG_WRN("[STAGE 1] Cell measurement timed out");
		}
		return err;
	}

	if (cell_meas_result != 0) {
		return cell_meas_result;
	}

	*out = latest_cell_meas;
	return 0;
}

static int stage2_open_and_get_gnss(struct tracker_gnss_fix *out, k_timeout_t timeout)
{
	uint8_t use_case = NRF_MODEM_GNSS_USE_CASE_MULTIPLE_HOT_START;
	int err;

	if (mode_abort_pending()) {
		return -ECANCELED;
	}

	/* Preserve GPS-first sequencing: LTE must be deactivated before GNSS. */
	radio_turn_off_lte();

	LOG_INF("[STAGE 2] Opening GNSS (timeout %d s)...",
		CONFIG_COMPARE_GNSS_TIMEOUT_SECONDS);
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_ACTIVATE_GNSS);
	if (err != 0) {
		LOG_ERR("Failed to activate GNSS: %d", err);
		return err;
	}

	if (IS_ENABLED(CONFIG_COMPARE_GNSS_LOW_ACCURACY)) {
		use_case |= NRF_MODEM_GNSS_USE_CASE_LOW_ACCURACY;
	}

	err = nrf_modem_gnss_event_handler_set(gnss_event_handler);
	if (err != 0) {
		LOG_ERR("nrf_modem_gnss_event_handler_set failed: %d", err);
		return err;
	}

	err = nrf_modem_gnss_use_case_set(use_case);
	if (err != 0) {
		LOG_ERR("nrf_modem_gnss_use_case_set failed: %d", err);
		return err;
	}

	/* Single-fix mode: fix_interval = 0, retry timeout configured by Kconfig. */
	err = nrf_modem_gnss_fix_interval_set(0);
	if (err != 0) {
		LOG_ERR("nrf_modem_gnss_fix_interval_set failed: %d", err);
		return err;
	}

	err = nrf_modem_gnss_fix_retry_set(CONFIG_COMPARE_GNSS_TIMEOUT_SECONDS);
	if (err != 0) {
		LOG_ERR("nrf_modem_gnss_fix_retry_set failed: %d", err);
		return err;
	}

	k_sem_reset(&gnss_fix_sem);
	atomic_set(&gnss_fix_wanted, 1);

	err = nrf_modem_gnss_start();
	if (err != 0) {
		atomic_clear(&gnss_fix_wanted);
		LOG_ERR("nrf_modem_gnss_start failed: %d", err);
		return err;
	}

	err = wait_or_mode_abort(&gnss_fix_sem, timeout);
	if (err == -ECANCELED) {
		LOG_WRN("[STAGE 2] GNSS search aborted by MODE button");
	} else if (err != 0) {
		LOG_WRN("[STAGE 2] GNSS fix timed out");
	} else {
		*out = latest_gnss_fix;
	}

	return err;
}

/*
 * Stage 1:
 * start -> open LTE -> connect LTE -> get location -> sending MQTT -> turn off LTE -> sleep
 */
static void run_stage1_lte(void)
{
	struct tracker_cell_report cell_report;
	int err;

	LOG_INF("================================================");
	LOG_INF("[STAGE 1: LTE] START (press MODE anytime -> SLEEP)");
	LOG_INF("================================================");
	status_led_start(500);

	err = radio_open_and_connect_lte(K_SECONDS(CONFIG_COMPARE_LTE_TIMEOUT_SECONDS));
	if (err != 0) {
		goto cleanup;
	}

	err = stage1_get_cell_location(&cell_report,
				       K_SECONDS(CONFIG_COMPARE_CELL_MEAS_TIMEOUT_SECONDS));
	if (err != 0) {
		goto cleanup;
	}

	if (mode_abort_pending()) {
		LOG_WRN("[STAGE 1] Aborted by MODE before MQTT");
		goto cleanup;
	}

	LOG_INF("[STAGE 1] Sending MQTT...");
	if (!mqtt_ready) {
		LOG_WRN("MQTT not initialized (check CA cert/credentials)");
	} else {
		err = tracker_mqtt_connect(K_SECONDS(15), &mode_btn_sem);
		if (err == 0) {
			err = tracker_mqtt_publish_telemetry(NULL, &cell_report,
							     K_SECONDS(15),
							     &mode_btn_sem);
			if (err != 0 && err != -ECANCELED) {
				LOG_ERR("[STAGE 1] MQTT publish failed: %d", err);
			}
		}
		(void)tracker_mqtt_disconnect(K_SECONDS(10));
	}

cleanup:
	status_led_stop();
	radio_turn_off_lte();
	LOG_INF("[STAGE 1: LTE] Done -> entering SLEEP");
}

/*
 * Stage 2:
 * start -> Open GNSS -> get location -> off GNSS -> connect LTE -> sending MQTT -> off LTE -> sleep
 */
static void run_stage2_gnss(void)
{
	struct tracker_gnss_fix gnss_fix;
	int err;

	LOG_INF("================================================");
	LOG_INF("[STAGE 2: GNSS] START (press MODE anytime -> SLEEP)");
	LOG_INF("================================================");
	status_led_start(150);

	err = stage2_open_and_get_gnss(&gnss_fix,
				       K_SECONDS(CONFIG_COMPARE_GNSS_TIMEOUT_SECONDS));
	/* Always turn GNSS off before LTE or SLEEP. */
	radio_turn_off_gnss();
	if (err != 0) {
		goto cleanup;
	}

	err = radio_open_and_connect_lte(K_SECONDS(CONFIG_COMPARE_LTE_TIMEOUT_SECONDS));
	if (err != 0) {
		goto cleanup;
	}

	if (mode_abort_pending()) {
		LOG_WRN("[STAGE 2] Aborted by MODE before MQTT");
		goto cleanup;
	}

	LOG_INF("[STAGE 2] Sending MQTT...");
	if (!mqtt_ready) {
		LOG_WRN("MQTT not initialized (check CA cert/credentials)");
	} else {
		err = tracker_mqtt_connect(K_SECONDS(15), &mode_btn_sem);
		if (err == 0) {
			err = tracker_mqtt_publish_telemetry(&gnss_fix, NULL,
							     K_SECONDS(15),
							     &mode_btn_sem);
			if (err != 0 && err != -ECANCELED) {
				LOG_ERR("[STAGE 2] MQTT publish failed: %d", err);
			}
		}
		(void)tracker_mqtt_disconnect(K_SECONDS(10));
	}

cleanup:
	status_led_stop();
	radio_turn_off_gnss();
	radio_turn_off_lte();
	LOG_INF("[STAGE 2: GNSS] Done -> entering SLEEP");
}

static void run_sleep_stage(enum compare_stage next_stage)
{
	k_timeout_t wait_time = (CONFIG_COMPARE_SLEEP_TIMEOUT_SECONDS > 0) ?
		K_SECONDS(CONFIG_COMPARE_SLEEP_TIMEOUT_SECONDS) : K_FOREVER;
	int err;

	status_led_stop();
	/* Clear any leftover button edge from aborting the previous stage. */
	k_sem_reset(&mode_btn_sem);

	LOG_INF("[SLEEP] Radio OFF (System ON Idle). Press MODE to wake -> Stage %d (%s)",
		(int)next_stage,
		(next_stage == STAGE_1_LTE) ? "LTE" : "GNSS");

	err = k_sem_take(&mode_btn_sem, wait_time);
	if (err == 0) {
		LOG_INF("[SLEEP] Woke up by MODE button -> Stage %d (%s)",
			(int)next_stage,
			(next_stage == STAGE_1_LTE) ? "LTE" : "GNSS");
	} else {
		LOG_INF("[SLEEP] Woke up by timer -> Stage %d (%s)",
			(int)next_stage,
			(next_stage == STAGE_1_LTE) ? "LTE" : "GNSS");
	}
}

int main(void)
{
	enum compare_stage current_stage = STAGE_1_LTE;
	int err;

	status_led_init();
	LOG_INF("=== LTE (Stage 1) vs GNSS (Stage 2) Comparison Firmware ===");

	power_down_unused_peripherals();

	err = mode_btn_init();
	if (err != 0) {
		LOG_ERR("MODE button init failed: %d", err);
		return 0;
	}

	err = nrf_modem_lib_init();
	if (err != 0) {
		LOG_ERR("nrf_modem_lib_init failed: %d", err);
		return 0;
	}
	lte_lc_register_handler(lte_event_handler);

	/* Start with both LTE and GNSS deactivated until Stage 1 opens LTE. */
	radio_turn_off_lte();
	radio_turn_off_gnss();

	err = tracker_mqtt_init();
	if (err != 0) {
		LOG_WRN("MQTT init failed (%d); MQTT sending will be skipped", err);
		mqtt_ready = false;
	} else {
		mqtt_ready = true;
	}

	while (true) {
		if (current_stage == STAGE_1_LTE) {
			run_stage1_lte();
			current_stage = STAGE_2_GNSS;
		} else {
			run_stage2_gnss();
			current_stage = STAGE_1_LTE;
		}

		run_sleep_stage(current_stage);
	}

	return 0;
}
