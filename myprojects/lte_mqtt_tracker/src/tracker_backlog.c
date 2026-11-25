/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "tracker_backlog.h"
#include "tracker_status.h"

LOG_MODULE_REGISTER(tracker_backlog, LOG_LEVEL_INF);

struct backlog_slot {
	struct tracker_cell_report report;
	uint32_t seq;
};

static struct backlog_slot entries[CONFIG_TRACKER_BACKLOG_CAPACITY];
static size_t count;
static uint32_t next_seq = 1;
static uint32_t stride = 1;

void tracker_backlog_add(const struct tracker_cell_report *report)
{
	uint32_t seq;

	if (count == 0) {
		stride = 1;
	}

	seq = next_seq++;
	if (seq % stride != 0) {
		return;
	}

	if (count == CONFIG_TRACKER_BACKLOG_CAPACITY) {
		size_t write = 0;

		stride *= 2;
		for (size_t i = 0; i < count; i++) {
			if (entries[i].seq % stride == 0) {
				entries[write++] = entries[i];
			}
		}
		count = write;

		if (seq % stride != 0) {
			tracker_status_set("BACKLOG STRIDE DOUBLED");
			LOG_INF("Backlog full; stride now %u", stride);
			return;
		}
	}

	entries[count].report = *report;
	entries[count].seq = seq;
	count++;

	tracker_status_set("BACKLOG REPORT STORED");
	LOG_INF("Buffered cell report (%zu/%d, stride %u)",
		count, CONFIG_TRACKER_BACKLOG_CAPACITY, stride);
}

bool tracker_backlog_peek_oldest(struct tracker_cell_report *out)
{
	if (count == 0) {
		return false;
	}

	*out = entries[0].report;
	return true;
}

void tracker_backlog_remove_oldest(void)
{
	if (count == 0) {
		return;
	}

	memmove(&entries[0], &entries[1], (count - 1) * sizeof(entries[0]));
	count--;
}
