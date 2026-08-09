/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_MODEM_H_
#define TRACKER_MODEM_H_

#include <zephyr/kernel.h>

int tracker_modem_init(void);
int tracker_modem_prepare_upload(k_timeout_t timeout);
int tracker_modem_enter_low_power(k_timeout_t timeout);
int tracker_modem_return_to_gnss(void);

#endif /* TRACKER_MODEM_H_ */
