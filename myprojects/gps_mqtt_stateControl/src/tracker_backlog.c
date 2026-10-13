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

/*
 * Adaptive stride sampling: see docs/adr/0005-adaptive-stride-backlog-retention.md
 * for the full comparison against the two alternatives that were simulated
 * and rejected (a flat active/anchor decimation cascade, and logarithmic
 * stride buckets alone).
 *
 * A single global stride keeps only every Nth acquired fix. When the
 * fixed-capacity buffer fills, the stride doubles and the existing buffer
 * is refiltered to the new, coarser stride, freeing room to keep accepting
 * new fixes. This keeps the retained set evenly spaced across the entire
 * outage seen so far -- reach scales with elapsed time -- rather than
 * converging to a small, fixed recent-only window the way a decimation
 * cascade does.
 */
struct backlog_slot {
	struct tracker_location_fix fix;
	uint32_t seq;
};

static struct backlog_slot entries[CONFIG_TRACKER_BACKLOG_CAPACITY];
static size_t count;
static uint32_t next_seq = 1;
static uint32_t stride = 1;

void tracker_backlog_add(const struct tracker_location_fix *fix)
{
	uint32_t seq;

	if (count == 0) {
		/* Backlog was fully drained: a new outage episode starts back
		 * at full resolution rather than inheriting a stride grown
		 * during some earlier, unrelated outage.
		 */
		stride = 1;
	}

	seq = next_seq++;
	if (seq % stride != 0) {
		/* Doesn't qualify at the current stride; a later fix will
		 * eventually land on a multiple of it.
		 */
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
			LOG_INF("Backlog full; stride now %u, this fix waits for the next multiple",
				stride);
			return;
		}
	}

	entries[count].fix = *fix;
	entries[count].seq = seq;
	count++;

	tracker_status_set("BACKLOG FIX STORED");
	LOG_INF("Buffered fix for later delivery (%zu/%d, stride %u)",
		count, CONFIG_TRACKER_BACKLOG_CAPACITY, stride);
}

size_t tracker_backlog_count(void)
{
	return count;
}

bool tracker_backlog_peek_oldest(struct tracker_location_fix *out)
{
	if (count == 0) {
		return false;
	}

	*out = entries[0].fix;
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
