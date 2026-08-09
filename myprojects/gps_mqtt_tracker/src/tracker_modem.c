/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <modem/lte_lc.h>
#include <modem/nrf_modem_lib.h>

#include "tracker_modem.h"

LOG_MODULE_REGISTER(tracker_modem, LOG_LEVEL_INF);

static K_SEM_DEFINE(lte_connected, 0, 1);
static K_SEM_DEFINE(psm_entered, 0, 1);
static atomic_t lte_registered;
static atomic_t psm_granted;
static atomic_t modem_in_psm;

static void lte_event_handler(const struct lte_lc_evt *const evt)
{
	switch (evt->type) {
	case LTE_LC_EVT_NW_REG_STATUS:
		if (evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ||
		    evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_ROAMING) {
			atomic_set(&lte_registered, 1);
			LOG_INF("LTE registered (%s)",
				evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ?
				"home network" : "roaming");
			k_sem_give(&lte_connected);
		} else {
			atomic_clear(&lte_registered);
		}
		break;
	case LTE_LC_EVT_PSM_UPDATE:
		LOG_INF("PSM parameters granted: TAU=%d s, active time=%d s",
			evt->psm_cfg.tau, evt->psm_cfg.active_time);
		if (evt->psm_cfg.tau >= 0 && evt->psm_cfg.active_time >= 0) {
			atomic_set(&psm_granted, 1);
		} else {
			atomic_clear(&psm_granted);
			LOG_WRN("Network did not grant PSM");
		}
		break;
	case LTE_LC_EVT_RRC_UPDATE:
		LOG_INF("RRC mode: %s", evt->rrc_mode == LTE_LC_RRC_MODE_CONNECTED ?
			"connected" : "idle");
		break;
	case LTE_LC_EVT_MODEM_SLEEP_ENTER:
		if (evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
			atomic_set(&modem_in_psm, 1);
			LOG_INF("Modem entered PSM for %lld ms", evt->modem_sleep.time);
			k_sem_give(&psm_entered);
		}
		break;
	case LTE_LC_EVT_MODEM_SLEEP_EXIT:
		if (evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
			atomic_clear(&modem_in_psm);
			LOG_INF("Modem exited PSM");
		}
		break;
	default:
		break;
	}
}

int tracker_modem_init(void)
{
	int err;

	LOG_INF("Initializing nRF modem library...");
	err = nrf_modem_lib_init();
	if (err != 0) {
		LOG_ERR("Failed to initialize nRF modem library: %d", err);
		return err;
	}

	lte_lc_register_handler(lte_event_handler);

	err = lte_lc_psm_param_set_seconds(CONFIG_TRACKER_PSM_PERIODIC_TAU_SECONDS,
		CONFIG_TRACKER_PSM_ACTIVE_TIME_SECONDS);
	if (err != 0) {
		LOG_ERR("Failed to configure PSM parameters: %d", err);
		return err;
	}

	err = lte_lc_psm_req(true);
	if (err != 0) {
		LOG_WRN("Failed to request PSM: %d", err);
	}

	err = tracker_modem_return_to_gnss();
	if (err != 0) {
		return err;
	}

	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_ACTIVATE_GNSS);
	if (err != 0) {
		LOG_ERR("Failed to activate GNSS: %d", err);
	}

	return err;
}

int tracker_modem_prepare_upload(k_timeout_t timeout)
{
	int err;

	if (atomic_get(&lte_registered)) {
		LOG_INF("LTE registration retained; preparing uplink from low-power mode");
		return 0;
	}

	LOG_INF("Activating LTE for location upload...");
	k_sem_reset(&lte_connected);
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_NORMAL);
	if (err != 0) {
		LOG_ERR("Failed to activate LTE: %d", err);
		return err;
	}

	err = k_sem_take(&lte_connected, timeout);
	if (err != 0) {
		LOG_ERR("LTE registration timed out");
		return -ETIMEDOUT;
	}

	return 0;
}

int tracker_modem_enter_low_power(k_timeout_t timeout)
{
	int err;

	if (!atomic_get(&lte_registered) || !atomic_get(&psm_granted)) {
		LOG_WRN("PSM is unavailable; deactivating LTE for the next GNSS cycle");
		return tracker_modem_return_to_gnss();
	}

	k_sem_reset(&psm_entered);
	if (atomic_get(&modem_in_psm)) {
		return 0;
	}

	LOG_INF("Waiting for modem to enter PSM...");
	err = k_sem_take(&psm_entered, timeout);
	if (err == 0 || atomic_get(&modem_in_psm)) {
		return 0;
	}

	LOG_WRN("PSM entry timed out; deactivating LTE for the next GNSS cycle");
	return tracker_modem_return_to_gnss();
}

int tracker_modem_return_to_gnss(void)
{
	int err;

	LOG_INF("Deactivating LTE to return radio control to GNSS...");
	atomic_clear(&lte_registered);
	atomic_clear(&psm_granted);
	atomic_clear(&modem_in_psm);
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_DEACTIVATE_LTE);
	if (err != 0) {
		LOG_ERR("Failed to deactivate LTE: %d", err);
		return err;
	}

	return 0;
}
