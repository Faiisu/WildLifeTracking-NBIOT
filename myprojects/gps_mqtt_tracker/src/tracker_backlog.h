/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_BACKLOG_H_
#define TRACKER_BACKLOG_H_

#include <stdbool.h>
#include <stddef.h>
#include <nrf_modem_gnss.h>

/*
 * Only the fields tracker_mqtt_publish_location() needs to publish --
 * deliberately not the full struct nrf_modem_gnss_pvt_data_frame, which
 * carries a per-satellite array the backlog has no use for and cannot
 * afford to store CONFIG_TRACKER_BACKLOG_CAPACITY copies of.
 */
struct tracker_location_fix {
	double latitude;
	double longitude;
	float altitude;
	float accuracy;
	struct nrf_modem_gnss_datetime datetime;
};

/*
 * Add a fix to the backlog, to be published once connectivity returns.
 * Not synchronized: call only from the tracker's single application thread.
 */
void tracker_backlog_add(const struct tracker_location_fix *fix);

/* Number of fixes currently held in the backlog. */
size_t tracker_backlog_count(void);

/*
 * Read the oldest backlog entry without removing it.
 * Returns false (and leaves *out untouched) if the backlog is empty.
 */
bool tracker_backlog_peek_oldest(struct tracker_location_fix *out);

/*
 * Remove the oldest backlog entry. Call only after
 * tracker_backlog_peek_oldest() and confirmation (e.g. a PUBACK) that it
 * was successfully delivered.
 */
void tracker_backlog_remove_oldest(void);

#endif /* TRACKER_BACKLOG_H_ */
