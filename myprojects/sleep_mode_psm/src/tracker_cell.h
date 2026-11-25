/*
 * Copyright (c) 2026 Cellular Asset Tracker Project
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef TRACKER_CELL_H_
#define TRACKER_CELL_H_

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

#endif /* TRACKER_CELL_H_ */
