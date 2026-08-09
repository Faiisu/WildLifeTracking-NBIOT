/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_GNSS_H_
#define TRACKER_GNSS_H_

#include <nrf_modem_gnss.h>

int tracker_gnss_start(void);
int tracker_gnss_wait_for_fix(struct nrf_modem_gnss_pvt_data_frame *pvt);

#endif /* TRACKER_GNSS_H_ */
