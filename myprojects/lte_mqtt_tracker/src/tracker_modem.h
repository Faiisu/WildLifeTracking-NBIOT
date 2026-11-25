/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_MODEM_H_
#define TRACKER_MODEM_H_

#include <zephyr/kernel.h>

#include "tracker_backlog.h"

int tracker_modem_init(void);
int tracker_modem_connect(k_timeout_t timeout);
int tracker_modem_measure_cells(struct tracker_cell_report *out, k_timeout_t timeout);

#endif /* TRACKER_MODEM_H_ */
