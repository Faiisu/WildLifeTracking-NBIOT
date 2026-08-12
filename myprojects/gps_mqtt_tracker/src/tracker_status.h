/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_STATUS_H_
#define TRACKER_STATUS_H_

/*
 * Emit a fixed transition marker for power-trace correlation. Keep label text
 * constant: put measurements and other variable data in a separate log line.
 */
void tracker_status_set(const char *label);

#endif /* TRACKER_STATUS_H_ */
