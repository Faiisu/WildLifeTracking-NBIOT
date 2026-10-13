/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#include <string.h>
#include <zephyr/logging/log.h>
#include "tracker_status.h"

LOG_MODULE_REGISTER(tracker_status, LOG_LEVEL_INF);

void tracker_status_set(const char *label)
{
	/* Preserve GNSS diagnostics while avoiding duplicate subsystem status lines. */
	if (strncmp(label, "GNSS ", 5) == 0) {
		LOG_INF("STATUS: %s", label);
	}
}
