/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <modem/lte_lc.h>
#include <modem/modem_key_mgmt.h>
#include <net/mqtt_helper.h>
#include <nrf_modem_at.h>

#include "tracker_mqtt.h"
#include "tracker_status.h"

LOG_MODULE_REGISTER(tracker_mqtt, LOG_LEVEL_INF);

#define IMEI_LEN 15
#define CGSN_RESPONSE_LENGTH (IMEI_LEN + 6 + 1)
#define CLIENT_ID_LEN (sizeof("nrf-") + IMEI_LEN)
#define MQTT_PAYLOAD_MAX_LEN 1024

static K_SEM_DEFINE(mqtt_conn_sem, 0, 1);
static K_SEM_DEFINE(mqtt_puback_sem, 0, 1);
static K_SEM_DEFINE(mqtt_disconnected_sem, 0, 1);

static char client_id_buf[CLIENT_ID_LEN];
static char payload_buf[MQTT_PAYLOAD_MAX_LEN];

static enum mqtt_conn_return_code connack_result;
static int puback_result;
static uint16_t pending_publish_id;
static atomic_t connect_attempted;

static const unsigned char broker_ca_certificate[] = {
#if __has_include("ca-cert.pem")
#include "ca-cert.pem"
#else
""
#endif
};

static int append_fmt(char *buf, size_t cap, size_t *pos, const char *fmt, ...)
{
	va_list args;
	int written;

	if (*pos >= cap) {
		return -EMSGSIZE;
	}

	va_start(args, fmt);
	written = vsnprintf(buf + *pos, cap - *pos, fmt, args);
	va_end(args);

	if (written < 0 || (size_t)written >= (cap - *pos)) {
		return -EMSGSIZE;
	}

	*pos += (size_t)written;
	return 0;
}

static int append_optional_int16(char *buf, size_t cap, size_t *pos,
	int16_t value, int16_t invalid_sentinel)
{
	if (value == invalid_sentinel) {
		return append_fmt(buf, cap, pos, "null");
	}
	return append_fmt(buf, cap, pos, "%d", value);
}

static int format_cell_report_json(const struct tracker_cell_report *report,
	char *buf, size_t cap, size_t *out_len)
{
	size_t pos = 0;
	int err;

	err = append_fmt(buf, cap, &pos,
		"{\"device_id\":\"%s\",\"uptime_ms\":%lld,"
		"\"source\":\"lte\",\"gnss\":null,\"lte\":{"
		"\"mcc\":%d,\"mnc\":%d,\"tac\":",
		client_id_buf, (long long)report->uptime_ms,
		report->mcc, report->mnc);
	if (err != 0) {
		return err;
	}

	if (report->tac == LTE_LC_CELL_TAC_INVALID) {
		err = append_fmt(buf, cap, &pos, "null");
	} else {
		err = append_fmt(buf, cap, &pos, "%u", report->tac);
	}
	if (err != 0) {
		return err;
	}

	err = append_fmt(buf, cap, &pos,
		",\"cid\":%u,\"earfcn\":%u,\"pci\":%u,\"rsrp\":",
		report->cell_id, report->earfcn, report->pci);
	if (err != 0) {
		return err;
	}

	err = append_optional_int16(buf, cap, &pos, report->rsrp_dbm,
		TRACKER_RSRP_DBM_INVALID);
	if (err != 0) {
		return err;
	}

	err = append_fmt(buf, cap, &pos, ",\"rsrq\":");
	if (err != 0) {
		return err;
	}

	err = append_optional_int16(buf, cap, &pos, report->rsrq,
		TRACKER_RSRQ_INVALID);
	if (err != 0) {
		return err;
	}

	err = append_fmt(buf, cap, &pos, ",\"ncells\":[");
	if (err != 0) {
		return err;
	}

#if CONFIG_TRACKER_MAX_NEIGHBOR_CELLS > 0
	for (uint8_t i = 0; i < report->ncell_count &&
	     i < CONFIG_TRACKER_MAX_NEIGHBOR_CELLS; i++) {
		const struct tracker_neighbor_cell *nc = &report->ncells[i];

		err = append_fmt(buf, cap, &pos,
			"%s{\"earfcn\":%u,\"pci\":%u,\"rsrp\":",
			(i > 0) ? "," : "", nc->earfcn, nc->pci);
		if (err != 0) {
			return err;
		}
		err = append_optional_int16(buf, cap, &pos, nc->rsrp_dbm,
			TRACKER_RSRP_DBM_INVALID);
		if (err != 0) {
			return err;
		}
		err = append_fmt(buf, cap, &pos, ",\"rsrq\":");
		if (err != 0) {
			return err;
		}
		err = append_optional_int16(buf, cap, &pos, nc->rsrq,
			TRACKER_RSRQ_INVALID);
		if (err != 0) {
			return err;
		}
		err = append_fmt(buf, cap, &pos, "}");
		if (err != 0) {
			return err;
		}
	}
#endif

	err = append_fmt(buf, cap, &pos, "]}}");
	if (err != 0) {
		return err;
	}

	*out_len = pos;
	return 0;
}

static int broker_ca_provision(void)
{
	bool exists;
	int err;
	int comparison;

	if (sizeof(broker_ca_certificate) <= 1) {
		LOG_ERR("TLS is enabled but credentials/ca-cert.pem is missing");
		return -ENOENT;
	}

	tracker_status_set("MQTT TLS CA CHECK");
	err = modem_key_mgmt_exists(CONFIG_MQTT_HELPER_SEC_TAG,
		MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN, &exists);
	if (err != 0) {
		LOG_ERR("Failed to check MQTT broker CA: %d", err);
		return err;
	}

	if (exists) {
		comparison = modem_key_mgmt_cmp(CONFIG_MQTT_HELPER_SEC_TAG,
			MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN, broker_ca_certificate,
			sizeof(broker_ca_certificate) - 1);
		if (comparison == 0) {
			tracker_status_set("MQTT TLS CA READY");
			LOG_INF("MQTT broker CA is already provisioned");
			return 0;
		}

		tracker_status_set("MQTT TLS CA DELETE");
		err = modem_key_mgmt_delete(CONFIG_MQTT_HELPER_SEC_TAG,
			MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN);
		if (err != 0) {
			LOG_ERR("Failed to replace MQTT broker CA: %d", err);
			return err;
		}
	}

	tracker_status_set("MQTT TLS CA WRITE");
	err = modem_key_mgmt_write(CONFIG_MQTT_HELPER_SEC_TAG,
		MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN, broker_ca_certificate,
		sizeof(broker_ca_certificate) - 1);
	if (err != 0) {
		LOG_ERR("Failed to provision MQTT broker CA: %d", err);
		return err;
	}

	tracker_status_set("MQTT TLS CA READY");
	LOG_INF("MQTT broker CA provisioned in sec tag %d", CONFIG_MQTT_HELPER_SEC_TAG);
	return 0;
}

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
		tracker_status_set("MQTT CONNECTED");
		LOG_INF("Connected to MQTT broker: %s:%d", CONFIG_MQTT_BROKER_HOSTNAME,
			CONFIG_MQTT_BROKER_PORT);
	} else {
		tracker_status_set("MQTT CONNECTION REJECTED");
		LOG_ERR("MQTT broker rejected connection: %d", return_code);
	}

	k_sem_give(&mqtt_conn_sem);
}

static void on_mqtt_disconnect(int result)
{
	atomic_clear(&connect_attempted);
	tracker_status_set("MQTT DISCONNECTED");
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
		tracker_status_set("MQTT PUBLISH ACK ERROR");
		LOG_ERR("MQTT PUBACK failed for ID %u: %d", message_id, result);
	} else {
		tracker_status_set("MQTT PUBLISH ACKNOWLEDGED");
		LOG_INF("MQTT broker acknowledged cell report (ID: %u)", message_id);
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

	tracker_status_set("MQTT INIT");

	if (strlen(CONFIG_MQTT_BROKER_PASSWORD) > 0 &&
	    strlen(CONFIG_MQTT_BROKER_USERNAME) == 0) {
		LOG_ERR("MQTT password is configured without a username");
		return -EINVAL;
	}

	if (IS_ENABLED(CONFIG_MQTT_LIB_TLS)) {
		err = broker_ca_provision();
		if (err != 0) {
			return err;
		}
	}

	err = mqtt_helper_init(&mqtt_cfg);
	if (err != 0) {
		LOG_ERR("Failed to initialize MQTT helper: %d", err);
		return err;
	}

	err = client_id_get(client_id_buf, sizeof(client_id_buf));
	if (err == 0) {
		tracker_status_set("MQTT READY");
	}

	return err;
}

int tracker_mqtt_connect(k_timeout_t timeout)
{
	struct mqtt_helper_conn_params conn_params = {
		.hostname.ptr = CONFIG_MQTT_BROKER_HOSTNAME,
		.hostname.size = strlen(CONFIG_MQTT_BROKER_HOSTNAME),
		.device_id.ptr = client_id_buf,
		.device_id.size = strlen(client_id_buf),
		.user_name.ptr = CONFIG_MQTT_BROKER_USERNAME,
		.user_name.size = strlen(CONFIG_MQTT_BROKER_USERNAME),
		.password.ptr = CONFIG_MQTT_BROKER_PASSWORD,
		.password.size = strlen(CONFIG_MQTT_BROKER_PASSWORD),
	};
	int err;

	tracker_status_set("MQTT CONNECTION");
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
		tracker_status_set("MQTT CONNECTION TIMEOUT");
		LOG_ERR("MQTT CONNACK timed out");
		return -ETIMEDOUT;
	}
	if (connack_result != MQTT_CONNECTION_ACCEPTED) {
		return -ECONNREFUSED;
	}

	return 0;
}

int tracker_mqtt_publish_cell_report(
	const struct tracker_cell_report *report, k_timeout_t timeout)
{
	struct mqtt_publish_param param = {0};
	size_t payload_len = 0;
	int err;

	if (report == NULL) {
		return -EINVAL;
	}

	err = format_cell_report_json(report, payload_buf, sizeof(payload_buf), &payload_len);
	if (err != 0) {
		LOG_ERR("Cell report JSON formatting failed or was truncated: %d", err);
		return err;
	}

	param.message.payload.data = (uint8_t *)payload_buf;
	param.message.payload.len = payload_len;
	param.message.topic.qos = (enum mqtt_qos)CONFIG_MQTT_PUB_QOS;
	param.message.topic.topic.utf8 = CONFIG_MQTT_PUB_TOPIC;
	param.message.topic.topic.size = strlen(CONFIG_MQTT_PUB_TOPIC);
	pending_publish_id = mqtt_helper_msg_id_get();
	param.message_id = pending_publish_id;

	tracker_status_set("MQTT PUBLISH");
	LOG_INF("Publishing cell report to '%s' (QoS %d, %zu bytes): %s",
		CONFIG_MQTT_PUB_TOPIC, CONFIG_MQTT_PUB_QOS, payload_len, payload_buf);
	if (CONFIG_MQTT_PUB_QOS == MQTT_QOS_1_AT_LEAST_ONCE) {
		k_sem_reset(&mqtt_puback_sem);
		puback_result = 0;
	}

	err = mqtt_helper_publish(&param);
	if (err != 0) {
		LOG_ERR("Failed to publish MQTT message: %d", err);
		return err;
	}

	if (CONFIG_MQTT_PUB_QOS == MQTT_QOS_0_AT_MOST_ONCE) {
		tracker_status_set("MQTT PUBLISH COMPLETE");
		return 0;
	}

	err = k_sem_take(&mqtt_puback_sem, timeout);
	if (err != 0) {
		tracker_status_set("MQTT PUBLISH ACK TIMEOUT");
		LOG_ERR("MQTT PUBACK timed out");
		return -ETIMEDOUT;
	}

	return puback_result;
}

int tracker_mqtt_disconnect(k_timeout_t timeout)
{
	int err;

	if (!atomic_get(&connect_attempted)) {
		tracker_status_set("MQTT DISCONNECTED");
		return 0;
	}

	tracker_status_set("MQTT DISCONNECT");
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
