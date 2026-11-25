/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/logging/log.h>

#include "tracker_status.h"

LOG_MODULE_REGISTER(tracker_status, LOG_LEVEL_INF);

void tracker_status_set(const char *label)
{
	LOG_INF("STATUS: %s", label);
}
