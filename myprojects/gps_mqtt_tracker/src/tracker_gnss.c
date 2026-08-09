/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <nrf_modem_gnss.h>

#include "tracker_gnss.h"

LOG_MODULE_REGISTER(tracker_gnss, LOG_LEVEL_INF);

K_MSGQ_DEFINE(gnss_pvt_queue, sizeof(struct nrf_modem_gnss_pvt_data_frame), 2, 4);
#if defined(CONFIG_TRACKER_GNSS_DIAGNOSTICS)
K_MSGQ_DEFINE(gnss_nmea_queue, sizeof(struct nrf_modem_gnss_nmea_data_frame), 20, 4);

static void print_satellite_stats(const struct nrf_modem_gnss_pvt_data_frame *pvt)
{
	uint8_t tracked = 0;
	uint8_t in_fix = 0;
	uint8_t unhealthy = 0;

	for (int i = 0; i < NRF_MODEM_GNSS_MAX_SATELLITES; i++) {
		if (pvt->sv[i].sv == 0) {
			continue;
		}

		tracked++;
		if ((pvt->sv[i].flags & NRF_MODEM_GNSS_SV_FLAG_USED_IN_FIX) != 0) {
			in_fix++;
		}
		if ((pvt->sv[i].flags & NRF_MODEM_GNSS_SV_FLAG_UNHEALTHY) != 0) {
			unhealthy++;
		}
	}

	LOG_RAW("Tracking: %2d Using: %2d Unhealthy: %d\n", tracked, in_fix, unhealthy);
}

static void print_gnss_flags(const struct nrf_modem_gnss_pvt_data_frame *pvt)
{
	if ((pvt->flags & NRF_MODEM_GNSS_PVT_FLAG_DEADLINE_MISSED) != 0) {
		LOG_RAW("GNSS operation blocked by LTE\n");
	}
	if ((pvt->flags & NRF_MODEM_GNSS_PVT_FLAG_NOT_ENOUGH_WINDOW_TIME) != 0) {
		LOG_RAW("Insufficient GNSS time windows\n");
	}
	if ((pvt->flags & NRF_MODEM_GNSS_PVT_FLAG_SLEEP_BETWEEN_PVT) != 0) {
		LOG_RAW("Sleep period(s) between PVT notifications\n");
	}
	if ((pvt->flags & NRF_MODEM_GNSS_PVT_FLAG_SCHED_DOWNLOAD) != 0) {
		LOG_RAW("Scheduled navigation data download\n");
	}
}

static void print_fix_data(const struct nrf_modem_gnss_pvt_data_frame *pvt)
{
	LOG_RAW("Latitude:          %.06f\n", pvt->latitude);
	LOG_RAW("Longitude:         %.06f\n", pvt->longitude);
	LOG_RAW("Accuracy:          %.01f m\n", (double)pvt->accuracy);
	LOG_RAW("Altitude:          %.01f m\n", (double)pvt->altitude);
	LOG_RAW("Altitude accuracy: %.01f m\n", (double)pvt->altitude_accuracy);
	LOG_RAW("Speed:             %.01f m/s\n", (double)pvt->speed);
	LOG_RAW("Speed accuracy:    %.01f m/s\n", (double)pvt->speed_accuracy);
	LOG_RAW("V. speed:          %.01f m/s\n", (double)pvt->vertical_speed);
	LOG_RAW("V. speed accuracy: %.01f m/s\n", (double)pvt->vertical_speed_accuracy);
	LOG_RAW("Heading:           %.01f deg\n", (double)pvt->heading);
	LOG_RAW("Heading accuracy:  %.01f deg\n", (double)pvt->heading_accuracy);
	LOG_RAW("Date:              %04u-%02u-%02u\n", pvt->datetime.year,
		pvt->datetime.month, pvt->datetime.day);
	LOG_RAW("Time (UTC):        %02u:%02u:%02u.%03u\n", pvt->datetime.hour,
		pvt->datetime.minute, pvt->datetime.seconds, pvt->datetime.ms);
	LOG_RAW("PDOP:              %.01f\n", (double)pvt->pdop);
	LOG_RAW("HDOP:              %.01f\n", (double)pvt->hdop);
	LOG_RAW("VDOP:              %.01f\n", (double)pvt->vdop);
	LOG_RAW("TDOP:              %.01f\n", (double)pvt->tdop);
}

static void print_gnss_status(const struct nrf_modem_gnss_pvt_data_frame *pvt)
{
	static const char update_indicator[] = {'\\', '|', '/', '-'};
	static int64_t fix_timestamp;
	static uint8_t indicator_count;
	struct nrf_modem_gnss_nmea_data_frame nmea;

	if (fix_timestamp == 0) {
		fix_timestamp = k_uptime_get();
	}

	LOG_RAW("\033[1;1H");
	LOG_RAW("\033[2J");
	print_satellite_stats(pvt);
	print_gnss_flags(pvt);
	LOG_RAW("-----------------------------------\n");

	if ((pvt->flags & NRF_MODEM_GNSS_PVT_FLAG_FIX_VALID) != 0) {
		fix_timestamp = k_uptime_get();
		print_fix_data(pvt);
	} else {
		LOG_RAW("Seconds since last fix: %u\n",
			(uint32_t)((k_uptime_get() - fix_timestamp) / 1000));
		indicator_count++;
		LOG_RAW("Searching [%c]\n", update_indicator[indicator_count % 4]);
	}

	LOG_RAW("\nNMEA strings:\n\n");
	while (k_msgq_get(&gnss_nmea_queue, &nmea, K_NO_WAIT) == 0) {
		LOG_RAW("%s", nmea.nmea_str);
	}
}
#endif

static void gnss_event_handler(int event)
{
	struct nrf_modem_gnss_pvt_data_frame pvt;
	int err;

	switch (event) {
	case NRF_MODEM_GNSS_EVT_PVT:
		err = nrf_modem_gnss_read(&pvt, sizeof(pvt), NRF_MODEM_GNSS_DATA_PVT);
		if (err != 0) {
			LOG_ERR("Failed to read GNSS PVT data: %d", err);
			break;
		}

		err = k_msgq_put(&gnss_pvt_queue, &pvt, K_NO_WAIT);
		if (err != 0) {
			LOG_WRN("GNSS PVT queue full; dropping one status frame");
		}
		break;

	case NRF_MODEM_GNSS_EVT_FIX:
		LOG_INF("GNSS fix event detected");
		break;

#if defined(CONFIG_TRACKER_GNSS_DIAGNOSTICS)
	case NRF_MODEM_GNSS_EVT_NMEA: {
		struct nrf_modem_gnss_nmea_data_frame nmea;

		err = nrf_modem_gnss_read(&nmea, sizeof(nmea), NRF_MODEM_GNSS_DATA_NMEA);
		if (err != 0) {
			LOG_ERR("Failed to read GNSS NMEA data: %d", err);
			break;
		}

		if (k_msgq_put(&gnss_nmea_queue, &nmea, K_NO_WAIT) != 0) {
			LOG_DBG("GNSS NMEA queue full; dropping one sentence");
		}
		break;
	}
#endif

	case NRF_MODEM_GNSS_EVT_PERIODIC_WAKEUP:
		LOG_INF("GNSS woke up for periodic fix cycle");
		break;
	case NRF_MODEM_GNSS_EVT_BLOCKED:
		LOG_WRN("GNSS blocked by LTE activity");
		break;
	case NRF_MODEM_GNSS_EVT_UNBLOCKED:
		LOG_INF("GNSS unblocked; radio time is available again");
		break;
	case NRF_MODEM_GNSS_EVT_SLEEP_AFTER_FIX:
		LOG_INF("GNSS sleeping after a valid fix");
		break;
	case NRF_MODEM_GNSS_EVT_SLEEP_AFTER_TIMEOUT:
		LOG_WRN("GNSS fix timeout reached without valid fix");
		break;
	case NRF_MODEM_GNSS_EVT_REF_ALT_EXPIRED:
		LOG_WRN("GNSS reference altitude expired");
		break;
	default:
		break;
	}
}

int tracker_gnss_start(void)
{
	uint16_t nmea_mask = 0;
	uint8_t use_case = NRF_MODEM_GNSS_USE_CASE_MULTIPLE_HOT_START;
	int err;

#if defined(CONFIG_TRACKER_GNSS_DIAGNOSTICS)
	nmea_mask = NRF_MODEM_GNSS_NMEA_RMC_MASK |
		NRF_MODEM_GNSS_NMEA_GGA_MASK | NRF_MODEM_GNSS_NMEA_GLL_MASK |
		NRF_MODEM_GNSS_NMEA_GSA_MASK | NRF_MODEM_GNSS_NMEA_GSV_MASK;
#endif

#if defined(CONFIG_GNSS_HIGH_ACCURACY_TIMING_SOURCE)
	err = nrf_modem_gnss_timing_source_set(NRF_MODEM_GNSS_TIMING_SOURCE_TCXO);
	if (err != 0) {
		LOG_ERR("Failed to set TCXO timing source: %d", err);
		return err;
	}
#endif

#if defined(CONFIG_GNSS_LOW_ACCURACY)
	use_case |= NRF_MODEM_GNSS_USE_CASE_LOW_ACCURACY;
#endif

	err = nrf_modem_gnss_use_case_set(use_case);
	if (err != 0) {
		LOG_ERR("Failed to set GNSS use case: %d", err);
		return err;
	}

	err = nrf_modem_gnss_event_handler_set(gnss_event_handler);
	if (err != 0) {
		LOG_ERR("Failed to set GNSS event handler: %d", err);
		return err;
	}

	err = nrf_modem_gnss_nmea_mask_set(nmea_mask);
	if (err != 0) {
		LOG_ERR("Failed to configure GNSS NMEA output: %d", err);
		return err;
	}

	err = nrf_modem_gnss_fix_interval_set(CONFIG_TRACKER_PERIODIC_INTERVAL);
	if (err != 0) {
		LOG_ERR("Failed to set GNSS fix interval: %d", err);
		return err;
	}

	err = nrf_modem_gnss_fix_retry_set(CONFIG_TRACKER_PERIODIC_TIMEOUT);
	if (err != 0) {
		LOG_ERR("Failed to set GNSS fix retry timeout: %d", err);
		return err;
	}

	err = nrf_modem_gnss_start();
	if (err != 0) {
		LOG_ERR("Failed to start GNSS module: %d", err);
		return err;
	}

	err = nrf_modem_gnss_prio_mode_enable();
	if (err != 0) {
		LOG_ERR("Failed to enable GNSS priority mode: %d", err);
		return err;
	}

	LOG_INF("GNSS started (interval: %ds, timeout: %ds)",
		CONFIG_TRACKER_PERIODIC_INTERVAL, CONFIG_TRACKER_PERIODIC_TIMEOUT);
	return 0;
}

int tracker_gnss_wait_for_fix(struct nrf_modem_gnss_pvt_data_frame *pvt)
{
	int err;

	if (pvt == NULL) {
		return -EINVAL;
	}

	LOG_INF("Waiting for GNSS fix...");
	do {
		err = k_msgq_get(&gnss_pvt_queue, pvt, K_FOREVER);
		if (err != 0) {
			LOG_ERR("Failed to receive GNSS PVT status: %d", err);
			return err;
		}
#if defined(CONFIG_TRACKER_GNSS_DIAGNOSTICS)
		print_gnss_status(pvt);
#endif
	} while ((pvt->flags & NRF_MODEM_GNSS_PVT_FLAG_FIX_VALID) == 0);

	return 0;
}
