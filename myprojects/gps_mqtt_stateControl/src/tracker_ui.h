/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_UI_H_
#define TRACKER_UI_H_

enum tracker_ui_state {
	TRACKER_UI_IDLE,
	TRACKER_UI_LOW_POWER,
	TRACKER_UI_GNSS,
	TRACKER_UI_LTE,
	TRACKER_UI_LTE_CONNECTED,
};

int tracker_ui_init(void);
/* Call from the main thread; low-power entry waits for LED work cancellation. */
void tracker_ui_set_state(enum tracker_ui_state state);

#endif /* TRACKER_UI_H_ */
