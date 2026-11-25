/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <errno.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <modem/lte_lc.h>
#include <modem/nrf_modem_lib.h>

#include "tracker_modem.h"
#include "tracker_status.h"

LOG_MODULE_REGISTER(tracker_modem, LOG_LEVEL_INF);

static K_SEM_DEFINE(lte_connected, 0, 1);
static K_SEM_DEFINE(cell_meas_sem, 0, 1);

static atomic_t lte_registered;
static atomic_t meas_active;

/*
 * Written by lte_event_handler() only while holding the meas_active token
 * (claimed via atomic_cas) before giving cell_meas_sem; read by the tracker
 * thread only after taking cell_meas_sem.
 */
static struct tracker_cell_report latest_meas;
static int meas_result;

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
	case LTE_LC_NW_REG_UICC_FAIL:
		return "LTE UICC FAILURE";
	case LTE_LC_NW_REG_NO_SUITABLE_CELL:
		return "LTE NO SUITABLE CELL";
	default:
		return "LTE REGISTRATION OTHER";
	}
}

static void handle_neighbor_cell_meas(const struct lte_lc_cells_info *cells)
{
	if (!atomic_cas(&meas_active, 1, 0)) {
		return;
	}

	memset(&latest_meas, 0, sizeof(latest_meas));
	if (cells->current_cell.id == LTE_LC_CELL_EUTRAN_ID_INVALID) {
		meas_result = -ENODATA;
		tracker_status_set("LTE CELL MEAS NO SERVING CELL");
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
	tracker_status_set("LTE CELL MEAS COMPLETE");
	LOG_INF("Cell measurement: MCC=%d MNC=%d TAC=%u CID=%u PCI=%u RSRP=%d dBm (neighbors=%u)",
		latest_meas.mcc, latest_meas.mnc, latest_meas.tac,
		latest_meas.cell_id, latest_meas.pci, latest_meas.rsrp_dbm,
		latest_meas.ncell_count);
	k_sem_give(&cell_meas_sem);
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
		LOG_INF("PSM update: TAU=%d s, active time=%d s",
			evt->psm_cfg.tau, evt->psm_cfg.active_time);
		break;
	case LTE_LC_EVT_RRC_UPDATE:
		LOG_INF("RRC mode: %s", evt->rrc_mode == LTE_LC_RRC_MODE_CONNECTED ?
			"connected" : "idle");
		break;
	case LTE_LC_EVT_CELL_UPDATE:
		LOG_INF("LTE cell update: id=%u, TAC=%u", evt->cell.id, evt->cell.tac);
		break;
	case LTE_LC_EVT_NEIGHBOR_CELL_MEAS:
		handle_neighbor_cell_meas(&evt->cells_info);
		break;
	case LTE_LC_EVT_MODEM_SLEEP_ENTER:
		if (evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
			tracker_status_set("MODEM SLEEP PSM");
			LOG_INF("Modem entered PSM for %lld ms", evt->modem_sleep.time);
		}
		break;
	case LTE_LC_EVT_MODEM_SLEEP_EXIT:
		if (evt->modem_sleep.type == LTE_LC_MODEM_SLEEP_PSM) {
			tracker_status_set("MODEM WAKE PSM");
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

	tracker_status_set("MODEM LIBRARY INIT");
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

	tracker_status_set("MODEM READY");
	return 0;
}

int tracker_modem_connect(k_timeout_t timeout)
{
	int err;

	if (atomic_get(&lte_registered)) {
		tracker_status_set("LTE CONNECTION RETAINED");
		LOG_INF("LTE registration retained across sleep");
		return 0;
	}

	tracker_status_set("LTE CONNECTION");
	LOG_INF("Activating LTE for cell measurement and upload...");
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

int tracker_modem_measure_cells(struct tracker_cell_report *out, k_timeout_t timeout)
{
	struct lte_lc_ncellmeas_params params = {
		.search_type = LTE_LC_NEIGHBOR_SEARCH_TYPE_DEFAULT,
	};
	int err;

	if (out == NULL) {
		return -EINVAL;
	}

	tracker_status_set("LTE CELL MEAS START");
	LOG_INF("Starting LTE serving and neighbor cell measurement...");
	k_sem_reset(&cell_meas_sem);
	meas_result = -ENODATA;
	atomic_set(&meas_active, 1);

	err = lte_lc_neighbor_cell_measurement(&params);
	if (err != 0) {
		atomic_clear(&meas_active);
		LOG_ERR("Failed to start neighbor cell measurement: %d", err);
		return err;
	}

	err = k_sem_take(&cell_meas_sem, timeout);
	if (err != 0) {
		if (atomic_cas(&meas_active, 1, 0)) {
			(void)lte_lc_neighbor_cell_measurement_cancel();
			tracker_status_set("LTE CELL MEAS TIMEOUT");
			LOG_ERR("LTE cell measurement timed out");
			return -ETIMEDOUT;
		}
		/* Callback claimed meas_active just as the wait expired. */
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
