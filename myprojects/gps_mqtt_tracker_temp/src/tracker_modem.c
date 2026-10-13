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
#include "tracker_status.h"

LOG_MODULE_REGISTER(tracker_modem, LOG_LEVEL_INF);

static K_SEM_DEFINE(lte_connected, 0, 1);
static K_SEM_DEFINE(psm_entered, 0, 1);
static atomic_t lte_registered;
static atomic_t psm_granted;
static atomic_t modem_in_psm;

static const char *modem_sleep_status(enum lte_lc_modem_sleep_type type, bool entering)
{
	switch (type) {
	case LTE_LC_MODEM_SLEEP_PSM:
		return entering ? "MODEM SLEEP PSM" : "MODEM WAKE PSM";
	case LTE_LC_MODEM_SLEEP_RF_INACTIVITY:
		return entering ? "MODEM SLEEP RF INACTIVITY" :
			"MODEM WAKE RF INACTIVITY";
	case LTE_LC_MODEM_SLEEP_LIMITED_SERVICE:
		return entering ? "MODEM SLEEP LIMITED SERVICE" :
			"MODEM WAKE LIMITED SERVICE";
	case LTE_LC_MODEM_SLEEP_FLIGHT_MODE:
		return entering ? "MODEM SLEEP FLIGHT MODE" :
			"MODEM WAKE FLIGHT MODE";
	case LTE_LC_MODEM_SLEEP_PROPRIETARY_PSM:
		return entering ? "MODEM SLEEP PROPRIETARY PSM" :
			"MODEM WAKE PROPRIETARY PSM";
	default:
		return entering ? "MODEM SLEEP UNKNOWN" : "MODEM WAKE UNKNOWN";
	}
}

static const char *network_registration_status(enum lte_lc_nw_reg_status status)
{
	switch (status) {
	case LTE_LC_NW_REG_NOT_REGISTERED:
		return "LTE NOT REGISTERED";
	case LTE_LC_NW_REG_REGISTERED_HOME:
		return "LTE REGISTERED HOME";
	case LTE_LC_NW_REG_SEARCHING:
		return "LTE SEARCHING";
	case LTE_LC_NW_REG_REGISTRATION_DENIED:
		return "LTE REGISTRATION DENIED";
	case LTE_LC_NW_REG_UNKNOWN:
		return "LTE REGISTRATION UNKNOWN";
	case LTE_LC_NW_REG_REGISTERED_ROAMING:
		return "LTE REGISTERED ROAMING";
	case LTE_LC_NW_REG_RX_ONLY_NOT_REGISTERED:
		return "LTE RX ONLY NOT REGISTERED";
	case LTE_LC_NW_REG_RX_ONLY_REGISTERED_HOME:
		return "LTE RX ONLY REGISTERED HOME";
	case LTE_LC_NW_REG_RX_ONLY_SEARCHING:
		return "LTE RX ONLY SEARCHING";
	case LTE_LC_NW_REG_RX_ONLY_REGISTRATION_DENIED:
		return "LTE RX ONLY REGISTRATION DENIED";
	case LTE_LC_NW_REG_RX_ONLY_UNKNOWN:
		return "LTE RX ONLY REGISTRATION UNKNOWN";
	case LTE_LC_NW_REG_RX_ONLY_REGISTERED_ROAMING:
		return "LTE RX ONLY REGISTERED ROAMING";
	case LTE_LC_NW_REG_UICC_FAIL:
		return "LTE UICC FAILURE";
	case LTE_LC_NW_REG_NO_SUITABLE_CELL:
		return "LTE NO SUITABLE CELL";
	default:
		return "LTE REGISTRATION OTHER";
	}
}

static const char *modem_event_status(enum lte_lc_modem_evt_type type)
{
	switch (type) {
	case LTE_LC_MODEM_EVT_LIGHT_SEARCH_DONE:
		return "MODEM LIGHT SEARCH DONE";
	case LTE_LC_MODEM_EVT_SEARCH_DONE:
		return "MODEM SEARCH DONE";
	case LTE_LC_MODEM_EVT_RESET_LOOP:
		return "MODEM RESET LOOP";
	case LTE_LC_MODEM_EVT_BATTERY_LOW:
		return "MODEM BATTERY LOW";
	case LTE_LC_MODEM_EVT_OVERHEATED:
		return "MODEM OVERHEATED";
	case LTE_LC_MODEM_EVT_NO_IMEI:
		return "MODEM NO IMEI";
	case LTE_LC_MODEM_EVT_CE_LEVEL:
		return "MODEM CE LEVEL UPDATE";
	case LTE_LC_MODEM_EVT_RF_CAL_NOT_DONE:
		return "MODEM RF CALIBRATION NOT DONE";
	case LTE_LC_MODEM_EVT_INVALID_BAND_CONF:
		return "MODEM INVALID BAND CONFIG";
	case LTE_LC_MODEM_EVT_DETECTED_COUNTRY:
		return "MODEM COUNTRY DETECTED";
	default:
		return "MODEM EVENT OTHER";
	}
}

static void lte_event_handler(const struct lte_lc_evt *const evt)
{
	switch (evt->type) {
	case LTE_LC_EVT_NW_REG_STATUS:
		tracker_status_set(network_registration_status(evt->nw_reg_status));
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
		tracker_status_set((evt->psm_cfg.tau >= 0 && evt->psm_cfg.active_time >= 0) ?
			"MODEM PSM GRANTED" : "MODEM PSM NOT GRANTED");
		LOG_INF("PSM parameters update: TAU=%d s, active time=%d s",
			evt->psm_cfg.tau, evt->psm_cfg.active_time);
		if (evt->psm_cfg.tau >= 0 && evt->psm_cfg.active_time >= 0) {
			atomic_set(&psm_granted, 1);
		} else {
			atomic_clear(&psm_granted);
			LOG_WRN("Network did not grant PSM");
		}
		break;
	case LTE_LC_EVT_RRC_UPDATE:
		tracker_status_set(evt->rrc_mode == LTE_LC_RRC_MODE_CONNECTED ?
			"MODEM RRC CONNECTED" : "MODEM RRC IDLE");
		LOG_INF("RRC mode: %s", evt->rrc_mode == LTE_LC_RRC_MODE_CONNECTED ?
			"connected" : "idle");
		break;
	case LTE_LC_EVT_CELL_UPDATE:
		tracker_status_set(evt->cell.id == LTE_LC_CELL_EUTRAN_ID_INVALID ?
			"MODEM CELL LOST" : "MODEM CELL CONNECTED");
		LOG_INF("LTE cell update: id=%u, TAC=%u", evt->cell.id, evt->cell.tac);
		break;
	case LTE_LC_EVT_LTE_MODE_UPDATE:
		switch (evt->lte_mode) {
		case LTE_LC_LTE_MODE_LTEM:
			tracker_status_set("MODEM LTE M MODE");
			break;
		case LTE_LC_LTE_MODE_NBIOT:
			tracker_status_set("MODEM NB IOT MODE");
			break;
		case LTE_LC_LTE_MODE_NTN_NBIOT:
			tracker_status_set("MODEM NTN NB IOT MODE");
			break;
		default:
			tracker_status_set("MODEM LTE MODE NONE");
			break;
		}
		break;
	case LTE_LC_EVT_MODEM_SLEEP_EXIT_PRE_WARNING:
		tracker_status_set("MODEM SLEEP EXIT PRE WARNING");
		LOG_INF("Modem sleep exit in %lld ms", evt->modem_sleep.time);
		break;
	case LTE_LC_EVT_MODEM_SLEEP_ENTER:
		tracker_status_set(modem_sleep_status(evt->modem_sleep.type, true));
		if (evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
			atomic_set(&modem_in_psm, 1);
			LOG_INF("Modem entered PSM for %lld ms", evt->modem_sleep.time);
			k_sem_give(&psm_entered);
		}
		break;
	case LTE_LC_EVT_MODEM_SLEEP_EXIT:
		tracker_status_set(modem_sleep_status(evt->modem_sleep.type, false));
		if (evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
			atomic_clear(&modem_in_psm);
			LOG_INF("Modem exited PSM");
		}
		break;
	case LTE_LC_EVT_MODEM_EVENT:
		tracker_status_set(modem_event_status(evt->modem_evt.type));
		LOG_INF("LTE modem event: %d", evt->modem_evt.type);
		break;
	default:
		tracker_status_set("MODEM EVENT UNHANDLED");
		LOG_INF("Unhandled LTE event type: %d", evt->type);
		break;
	}
}

int tracker_modem_init(void)
{
	int err;

	tracker_status_set("MODEM LIBRARY INIT");
	LOG_INF("Initializing nRF modem library...");
	err = nrf_modem_lib_init();
	if (err != 0) {
		LOG_ERR("Failed to initialize nRF modem library: %d", err);
		return err;
	}
	tracker_status_set("MODEM LIBRARY READY");

	tracker_status_set("MODEM EVENT HANDLER CONFIG");
	lte_lc_register_handler(lte_event_handler);

	tracker_status_set("MODEM PSM PARAMETER CONFIG");
	err = lte_lc_psm_param_set_seconds(CONFIG_TRACKER_PSM_PERIODIC_TAU_SECONDS,
		CONFIG_TRACKER_PSM_ACTIVE_TIME_SECONDS);
	if (err != 0) {
		LOG_ERR("Failed to configure PSM parameters: %d", err);
		return err;
	}

	tracker_status_set("MODEM PSM REQUEST");
	err = lte_lc_psm_req(true);
	if (err != 0) {
		LOG_WRN("Failed to request PSM: %d", err);
	}

	err = tracker_modem_return_to_gnss();
	if (err != 0) {
		return err;
	}

	tracker_status_set("MODEM GNSS ACTIVATE");
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_ACTIVATE_GNSS);
	if (err != 0) {
		LOG_ERR("Failed to activate GNSS: %d", err);
	}

	if (err == 0) {
		tracker_status_set("MODEM READY");
	}

	return err;
}

int tracker_modem_prepare_upload(k_timeout_t timeout)
{
	int err;

	if (atomic_get(&lte_registered)) {
		tracker_status_set("LTE CONNECTION RETAINED");
		LOG_INF("LTE registration retained; preparing uplink from low-power mode");
		return 0;
	}

	tracker_status_set("LTE CONNECTION");
	LOG_INF("Activating LTE for location upload...");
	k_sem_reset(&lte_connected);
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_NORMAL);
	if (err != 0) {
		LOG_ERR("Failed to activate LTE: %d", err);
		return err;
	}

	err = k_sem_take(&lte_connected, timeout);
	if (err != 0) {
		tracker_status_set("LTE CONNECTION TIMEOUT");
		LOG_ERR("LTE registration timed out");
		return -ETIMEDOUT;
	}

	return 0;
}

int tracker_modem_enter_low_power(k_timeout_t timeout)
{
	int err;

	if (!atomic_get(&lte_registered) || !atomic_get(&psm_granted)) {
		tracker_status_set("GNSS SAFE LOW POWER");
		LOG_WRN("PSM is unavailable; deactivating LTE for the next GNSS cycle");
		return tracker_modem_return_to_gnss();
	}

	k_sem_reset(&psm_entered);
	if (atomic_get(&modem_in_psm)) {
		tracker_status_set("MODEM SLEEP PSM");
		return 0;
	}

	tracker_status_set("MODEM PSM WAIT");
	LOG_INF("Waiting for modem to enter PSM...");
	err = k_sem_take(&psm_entered, timeout);
	if (err == 0 || atomic_get(&modem_in_psm)) {
		return 0;
	}

	LOG_WRN("PSM entry timed out; deactivating LTE for the next GNSS cycle");
	tracker_status_set("MODEM PSM TIMEOUT");
	return tracker_modem_return_to_gnss();
}

int tracker_modem_return_to_gnss(void)
{
	int err;

	tracker_status_set("MODEM LTE DEACTIVATE");
	LOG_INF("Deactivating LTE to return radio control to GNSS...");
	atomic_clear(&lte_registered);
	atomic_clear(&psm_granted);
	atomic_clear(&modem_in_psm);
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_DEACTIVATE_LTE);
	if (err != 0) {
		LOG_ERR("Failed to deactivate LTE: %d", err);
		return err;
	}
	tracker_status_set("MODEM LTE DEACTIVATED");

	return 0;
}
