/* SPDX-License-Identifier: LicenseRef-Nordic-5-Clause */

#ifndef TRACKER_UI_H_
#define TRACKER_UI_H_

enum tracker_ui_state {
	TRACKER_UI_GNSS,
	TRACKER_UI_LTE,
	TRACKER_UI_MQTT,
};

int tracker_ui_init(void);
void tracker_ui_set_state(enum tracker_ui_state state);

#endif /* TRACKER_UI_H_ */
