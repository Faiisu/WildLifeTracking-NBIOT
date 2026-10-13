/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */
#ifndef STATE_CONTROL_GNSS_H_
#define STATE_CONTROL_GNSS_H_
#include <zephyr/kernel.h>
#include <nrf_modem_gnss.h>
int tracker_gnss_start(void);
int tracker_gnss_collect_until_stop(struct nrf_modem_gnss_pvt_data_frame *pvt,
	struct k_sem *stop_requested);
#endif
