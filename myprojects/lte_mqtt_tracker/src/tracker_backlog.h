/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_BACKLOG_H_
#define TRACKER_BACKLOG_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define TRACKER_RSRP_DBM_INVALID INT16_MIN
#define TRACKER_RSRQ_INVALID     INT16_MIN

struct tracker_neighbor_cell {
	uint32_t earfcn;
	uint16_t pci;
	int16_t rsrp_dbm;
	int16_t rsrq;
};

struct tracker_cell_report {
	int64_t uptime_ms;
	uint64_t meas_time_ms;
	int mcc;
	int mnc;
	uint32_t tac;
	uint32_t cell_id;
	uint32_t earfcn;
	uint16_t pci;
	int16_t rsrp_dbm;
	int16_t rsrq;
	uint8_t ncell_count;
#if CONFIG_TRACKER_MAX_NEIGHBOR_CELLS > 0
	struct tracker_neighbor_cell ncells[CONFIG_TRACKER_MAX_NEIGHBOR_CELLS];
#endif
};

/*
 * Add a measured cell report to the RAM backlog.
 * Not synchronized: call only from the tracker's single application thread.
 */
void tracker_backlog_add(const struct tracker_cell_report *report);

/*
 * Read the oldest backlog entry without removing it.
 * Returns false if the backlog is empty.
 */
bool tracker_backlog_peek_oldest(struct tracker_cell_report *out);

/*
 * Remove the oldest backlog entry. Call only after confirmation (e.g. PUBACK)
 * that the entry was delivered.
 */
void tracker_backlog_remove_oldest(void);

#endif /* TRACKER_BACKLOG_H_ */
