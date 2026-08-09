/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <nrf_modem_at.h>
#include <net/mqtt_helper.h>

#include "tracker_mqtt.h"

LOG_MODULE_REGISTER(tracker_mqtt, LOG_LEVEL_INF);

#define IMEI_LEN 15
#define CGSN_RESPONSE_LENGTH (IMEI_LEN + 6 + 1)
#define CLIENT_ID_LEN (sizeof("nrf-") + IMEI_LEN)
#define MQTT_PAYLOAD_MAX_LEN 256

static K_SEM_DEFINE(mqtt_conn_sem, 0, 1);
static K_SEM_DEFINE(mqtt_puback_sem, 0, 1);
static K_SEM_DEFINE(mqtt_disconnected_sem, 0, 1);

static char client_id_buf[CLIENT_ID_LEN];
static enum mqtt_conn_return_code connack_result;
static int puback_result;
static uint16_t pending_publish_id;
static atomic_t connect_attempted;

static int client_id_get(char *buffer, size_t buffer_len)
{
	char imei_buf[CGSN_RESPONSE_LENGTH];
	int len;
	int err;

	if (strlen(CONFIG_MQTT_CLIENT_ID) > 0) {
		len = snprintf(buffer, buffer_len, "%s", CONFIG_MQTT_CLIENT_ID);
		if (len < 0 || (size_t)len >= buffer_len) {
			LOG_ERR("Configured MQTT client ID is too long");
			return -EMSGSIZE;
		}
		return 0;
	}

	err = nrf_modem_at_cmd(imei_buf, sizeof(imei_buf), "AT+CGSN");
	if (err != 0) {
		LOG_ERR("Failed to obtain IMEI with AT+CGSN: %d", err);
		return err;
	}

	imei_buf[IMEI_LEN] = '\0';
	len = snprintf(buffer, buffer_len, "nrf-%.*s", IMEI_LEN, imei_buf);
	if (len < 0 || (size_t)len >= buffer_len) {
		LOG_ERR("Generated MQTT client ID is too long");
		return -EMSGSIZE;
	}

	LOG_INF("Generated MQTT client ID: %s", buffer);
	return 0;
}

static void on_mqtt_connack(enum mqtt_conn_return_code return_code, bool session_present)
{
	ARG_UNUSED(session_present);

	connack_result = return_code;
	if (return_code == MQTT_CONNECTION_ACCEPTED) {
		LOG_INF("Connected to MQTT broker: %s:%d", CONFIG_MQTT_BROKER_HOSTNAME,
			CONFIG_MQTT_BROKER_PORT);
	} else {
		LOG_ERR("MQTT broker rejected connection: %d", return_code);
	}

	k_sem_give(&mqtt_conn_sem);
}

static void on_mqtt_disconnect(int result)
{
	atomic_clear(&connect_attempted);
	if (result == 0) {
		LOG_INF("MQTT client disconnected");
	} else {
		LOG_WRN("MQTT client disconnected with result: %d", result);
	}
	k_sem_give(&mqtt_disconnected_sem);
}

static void on_mqtt_puback(uint16_t message_id, int result)
{
	if (message_id != pending_publish_id) {
		LOG_WRN("Unexpected MQTT PUBACK ID: %u", message_id);
		return;
	}

	puback_result = result;
	if (result != 0) {
		LOG_ERR("MQTT PUBACK failed for ID %u: %d", message_id, result);
	} else {
		LOG_INF("MQTT broker acknowledged location payload (ID: %u)", message_id);
	}
	k_sem_give(&mqtt_puback_sem);
}

int tracker_mqtt_init(void)
{
	struct mqtt_helper_cfg mqtt_cfg = {
		.cb = {
			.on_connack = on_mqtt_connack,
			.on_disconnect = on_mqtt_disconnect,
			.on_puback = on_mqtt_puback,
		},
	};
	int err;

	err = mqtt_helper_init(&mqtt_cfg);
	if (err != 0) {
		LOG_ERR("Failed to initialize MQTT helper: %d", err);
		return err;
	}

	return client_id_get(client_id_buf, sizeof(client_id_buf));
}

int tracker_mqtt_connect(k_timeout_t timeout)
{
	struct mqtt_helper_conn_params conn_params = {
		.hostname.ptr = CONFIG_MQTT_BROKER_HOSTNAME,
		.hostname.size = strlen(CONFIG_MQTT_BROKER_HOSTNAME),
		.device_id.ptr = client_id_buf,
		.device_id.size = strlen(client_id_buf),
	};
	int err;

	LOG_INF("Connecting to MQTT broker (%s:%d)...", CONFIG_MQTT_BROKER_HOSTNAME,
		CONFIG_MQTT_BROKER_PORT);
	k_sem_reset(&mqtt_conn_sem);
	connack_result = MQTT_UNACCEPTABLE_PROTOCOL_VERSION;
	err = mqtt_helper_connect(&conn_params);
	if (err != 0) {
		LOG_ERR("MQTT connection attempt failed: %d", err);
		return err;
	}
	atomic_set(&connect_attempted, 1);

	err = k_sem_take(&mqtt_conn_sem, timeout);
	if (err != 0) {
		LOG_ERR("MQTT CONNACK timed out");
		return -ETIMEDOUT;
	}
	if (connack_result != MQTT_CONNECTION_ACCEPTED) {
		return -ECONNREFUSED;
	}

	return 0;
}

int tracker_mqtt_publish_location(
	const struct nrf_modem_gnss_pvt_data_frame *pvt, k_timeout_t timeout)
{
	char payload[MQTT_PAYLOAD_MAX_LEN];
	struct mqtt_publish_param param = {0};
	int payload_len;
	int err;

	if (pvt == NULL) {
		return -EINVAL;
	}

	payload_len = snprintf(payload, sizeof(payload),
		"{\"lat\":%.06f,\"lon\":%.06f,\"alt\":%.01f,\"acc\":%.01f,"
		"\"time\":\"%04u-%02u-%02uT%02u:%02u:%02uZ\"}",
		pvt->latitude, pvt->longitude, (double)pvt->altitude,
		(double)pvt->accuracy, pvt->datetime.year, pvt->datetime.month,
		pvt->datetime.day, pvt->datetime.hour, pvt->datetime.minute,
		pvt->datetime.seconds);
	if (payload_len < 0 || (size_t)payload_len >= sizeof(payload)) {
		LOG_ERR("Location payload formatting failed or was truncated");
		return -EMSGSIZE;
	}

	param.message.payload.data = (uint8_t *)payload;
	param.message.payload.len = payload_len;
	param.message.topic.qos = MQTT_QOS_1_AT_LEAST_ONCE;
	param.message.topic.topic.utf8 = CONFIG_MQTT_PUB_TOPIC;
	param.message.topic.topic.size = strlen(CONFIG_MQTT_PUB_TOPIC);
	pending_publish_id = mqtt_helper_msg_id_get();
	param.message_id = pending_publish_id;

	LOG_INF("Publishing location to '%s': %s", CONFIG_MQTT_PUB_TOPIC, payload);
	k_sem_reset(&mqtt_puback_sem);
	puback_result = 0;
	err = mqtt_helper_publish(&param);
	if (err != 0) {
		LOG_ERR("Failed to publish MQTT message: %d", err);
		return err;
	}

	err = k_sem_take(&mqtt_puback_sem, timeout);
	if (err != 0) {
		LOG_ERR("MQTT PUBACK timed out");
		return -ETIMEDOUT;
	}

	return puback_result;
}

int tracker_mqtt_disconnect(k_timeout_t timeout)
{
	int err;

	if (!atomic_get(&connect_attempted)) {
		return 0;
	}

	k_sem_reset(&mqtt_disconnected_sem);
	err = mqtt_helper_disconnect();
	if (err != 0) {
		LOG_ERR("Failed to disconnect MQTT: %d", err);
		return err;
	}

	err = k_sem_take(&mqtt_disconnected_sem, timeout);
	if (err != 0) {
		LOG_WRN("MQTT disconnect timed out");
		return -ETIMEDOUT;
	}

	return 0;
}
