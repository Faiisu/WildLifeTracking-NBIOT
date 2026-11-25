/*
 * Copyright (c) 2026 Cellular Asset Tracker Project
 * SPDX-License-Identifier: Apache-2.0
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

LOG_MODULE_REGISTER(tracker_mqtt, LOG_LEVEL_INF);

#define IMEI_LEN             15
#define CGSN_RESPONSE_LENGTH (IMEI_LEN + 6 + 1)
#define CLIENT_ID_LEN        (sizeof("nrf-") + IMEI_LEN)
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

static int wait_sem_or_abort(struct k_sem *target_sem, struct k_sem *abort_sem,
			     k_timeout_t timeout)
{
	if (abort_sem == NULL) {
		return k_sem_take(target_sem, timeout);
	}

	struct k_poll_event events[2] = {
		K_POLL_EVENT_STATIC_INITIALIZER(K_POLL_TYPE_SEM_AVAILABLE,
						K_POLL_MODE_NOTIFY_ONLY,
						abort_sem, 0),
		K_POLL_EVENT_STATIC_INITIALIZER(K_POLL_TYPE_SEM_AVAILABLE,
						K_POLL_MODE_NOTIFY_ONLY,
						target_sem, 0),
	};
	int err = k_poll(events, ARRAY_SIZE(events), timeout);

	if (err == -EAGAIN) {
		return -ETIMEDOUT;
	}
	if (err != 0) {
		return err;
	}

	if (events[0].state == K_POLL_STATE_SEM_AVAILABLE) {
		(void)k_sem_take(abort_sem, K_NO_WAIT);
		return -ECANCELED;
	}

	if (events[1].state == K_POLL_STATE_SEM_AVAILABLE) {
		(void)k_sem_take(target_sem, K_NO_WAIT);
		return 0;
	}

	return -ETIMEDOUT;
}

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

static int append_gnss_object(const struct tracker_gnss_fix *gnss,
			      char *buf, size_t cap, size_t *pos)
{
	if (gnss == NULL) {
		return append_fmt(buf, cap, pos, "null");
	}

	return append_fmt(buf, cap, pos,
			  "{\"lat\":%.6f,\"lon\":%.6f,\"alt\":%.1f,\"acc\":%.1f,"
			  "\"time\":\"%04u-%02u-%02uT%02u:%02u:%02uZ\"}",
			  gnss->latitude, gnss->longitude,
			  (double)gnss->altitude, (double)gnss->accuracy,
			  gnss->datetime.year, gnss->datetime.month, gnss->datetime.day,
			  gnss->datetime.hour, gnss->datetime.minute, gnss->datetime.seconds);
}

static int append_lte_object(const struct tracker_cell_report *lte,
			     char *buf, size_t cap, size_t *pos)
{
	int err;

	if (lte == NULL) {
		return append_fmt(buf, cap, pos, "null");
	}

	err = append_fmt(buf, cap, pos, "{\"mcc\":%d,\"mnc\":%d,\"tac\":",
			 lte->mcc, lte->mnc);
	if (err != 0) {
		return err;
	}

	if (lte->tac == LTE_LC_CELL_TAC_INVALID) {
		err = append_fmt(buf, cap, pos, "null");
	} else {
		err = append_fmt(buf, cap, pos, "%u", lte->tac);
	}
	if (err != 0) {
		return err;
	}

	err = append_fmt(buf, cap, pos,
			 ",\"cid\":%u,\"earfcn\":%u,\"pci\":%u,\"rsrp\":",
			 lte->cell_id, lte->earfcn, lte->pci);
	if (err != 0) {
		return err;
	}

	err = append_optional_int16(buf, cap, pos, lte->rsrp_dbm,
				    TRACKER_RSRP_DBM_INVALID);
	if (err != 0) {
		return err;
	}

	err = append_fmt(buf, cap, pos, ",\"rsrq\":");
	if (err != 0) {
		return err;
	}

	err = append_optional_int16(buf, cap, pos, lte->rsrq,
				    TRACKER_RSRQ_INVALID);
	if (err != 0) {
		return err;
	}

	err = append_fmt(buf, cap, pos, ",\"ncells\":[");
	if (err != 0) {
		return err;
	}

#if CONFIG_TRACKER_MAX_NEIGHBOR_CELLS > 0
	for (uint8_t i = 0; i < lte->ncell_count &&
	     i < CONFIG_TRACKER_MAX_NEIGHBOR_CELLS; i++) {
		const struct tracker_neighbor_cell *nc = &lte->ncells[i];

		err = append_fmt(buf, cap, pos,
				 "%s{\"earfcn\":%u,\"pci\":%u,\"rsrp\":",
				 (i > 0) ? "," : "", nc->earfcn, nc->pci);
		if (err != 0) {
			return err;
		}
		err = append_optional_int16(buf, cap, pos, nc->rsrp_dbm,
					    TRACKER_RSRP_DBM_INVALID);
		if (err != 0) {
			return err;
		}
		err = append_fmt(buf, cap, pos, ",\"rsrq\":");
		if (err != 0) {
			return err;
		}
		err = append_optional_int16(buf, cap, pos, nc->rsrq,
					    TRACKER_RSRQ_INVALID);
		if (err != 0) {
			return err;
		}
		err = append_fmt(buf, cap, pos, "}");
		if (err != 0) {
			return err;
		}
	}
#endif

	return append_fmt(buf, cap, pos, "]}");
}

static int format_unified_telemetry_json(const struct tracker_gnss_fix *gnss,
					 const struct tracker_cell_report *lte,
					 char *buf, size_t cap, size_t *out_len)
{
	const char *source = (gnss != NULL && lte != NULL) ? "hybrid" :
			     (gnss != NULL) ? "gnss" : "lte";
	int64_t uptime_ms = (lte != NULL) ? lte->uptime_ms : k_uptime_get();
	size_t pos = 0;
	int err;

	err = append_fmt(buf, cap, &pos,
			 "{\"device_id\":\"%s\",\"uptime_ms\":%lld,"
			 "\"source\":\"%s\",\"gnss\":",
			 client_id_buf, (long long)uptime_ms, source);
	if (err != 0) {
		return err;
	}

	err = append_gnss_object(gnss, buf, cap, &pos);
	if (err != 0) {
		return err;
	}

	err = append_fmt(buf, cap, &pos, ",\"lte\":");
	if (err != 0) {
		return err;
	}

	err = append_lte_object(lte, buf, cap, &pos);
	if (err != 0) {
		return err;
	}

	err = append_fmt(buf, cap, &pos, "}");
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

	err = modem_key_mgmt_exists(CONFIG_MQTT_HELPER_SEC_TAG,
				    MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN, &exists);
	if (err != 0) {
		LOG_ERR("Failed to check MQTT broker CA: %d", err);
		return err;
	}

	if (exists) {
		comparison = modem_key_mgmt_cmp(CONFIG_MQTT_HELPER_SEC_TAG,
						MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN,
						broker_ca_certificate,
						sizeof(broker_ca_certificate) - 1);
		if (comparison == 0) {
			LOG_INF("MQTT broker CA already provisioned");
			return 0;
		}

		err = modem_key_mgmt_delete(CONFIG_MQTT_HELPER_SEC_TAG,
					    MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN);
		if (err != 0) {
			LOG_ERR("Failed to replace MQTT broker CA: %d", err);
			return err;
		}
	}

	err = modem_key_mgmt_write(CONFIG_MQTT_HELPER_SEC_TAG,
				   MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN,
				   broker_ca_certificate,
				   sizeof(broker_ca_certificate) - 1);
	if (err != 0) {
		LOG_ERR("Failed to provision MQTT broker CA: %d", err);
		return err;
	}

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
		LOG_ERR("Failed to obtain IMEI via AT+CGSN: %d", err);
		return err;
	}

	imei_buf[IMEI_LEN] = '\0';
	len = snprintf(buffer, buffer_len, "nrf-%.*s", IMEI_LEN, imei_buf);
	if (len < 0 || (size_t)len >= buffer_len) {
		LOG_ERR("Generated MQTT client ID is too long");
		return -EMSGSIZE;
	}

	LOG_INF("MQTT client ID: %s", buffer);
	return 0;
}

static void on_mqtt_connack(enum mqtt_conn_return_code return_code, bool session_present)
{
	ARG_UNUSED(session_present);

	connack_result = return_code;
	if (return_code == MQTT_CONNECTION_ACCEPTED) {
		LOG_INF("MQTT connected to %s:%d", CONFIG_MQTT_BROKER_HOSTNAME,
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
		LOG_INF("MQTT disconnected");
	} else {
		LOG_WRN("MQTT disconnected with result: %d", result);
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
		LOG_INF("MQTT PUBACK received (ID: %u)", message_id);
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
		LOG_ERR("mqtt_helper_init failed: %d", err);
		return err;
	}

	return client_id_get(client_id_buf, sizeof(client_id_buf));
}

int tracker_mqtt_connect(k_timeout_t timeout, struct k_sem *abort_sem)
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

	LOG_INF("Connecting to MQTT broker (%s:%d)...", CONFIG_MQTT_BROKER_HOSTNAME,
		CONFIG_MQTT_BROKER_PORT);
	k_sem_reset(&mqtt_conn_sem);
	connack_result = MQTT_UNACCEPTABLE_PROTOCOL_VERSION;

	err = mqtt_helper_connect(&conn_params);
	if (err != 0) {
		LOG_ERR("mqtt_helper_connect failed: %d", err);
		return err;
	}
	atomic_set(&connect_attempted, 1);

	err = wait_sem_or_abort(&mqtt_conn_sem, abort_sem, timeout);
	if (err != 0) {
		if (err == -ECANCELED) {
			LOG_WRN("MQTT connect cancelled by MODE button");
		} else {
			LOG_ERR("MQTT CONNACK timed out");
		}
		return err;
	}
	if (connack_result != MQTT_CONNECTION_ACCEPTED) {
		return -ECONNREFUSED;
	}

	return 0;
}

int tracker_mqtt_publish_telemetry(const struct tracker_gnss_fix *gnss,
				   const struct tracker_cell_report *lte,
				   k_timeout_t timeout,
				   struct k_sem *abort_sem)
{
	struct mqtt_publish_param param = { 0 };
	size_t payload_len = 0;
	int err;

	if (gnss == NULL && lte == NULL) {
		return -EINVAL;
	}

	err = format_unified_telemetry_json(gnss, lte, payload_buf,
					    sizeof(payload_buf), &payload_len);
	if (err != 0) {
		LOG_ERR("Unified telemetry JSON formatting failed: %d", err);
		return err;
	}

	param.message.payload.data = (uint8_t *)payload_buf;
	param.message.payload.len = payload_len;
	param.message.topic.qos = (enum mqtt_qos)CONFIG_MQTT_PUB_QOS;
	param.message.topic.topic.utf8 = CONFIG_MQTT_PUB_TOPIC;
	param.message.topic.topic.size = strlen(CONFIG_MQTT_PUB_TOPIC);
	pending_publish_id = mqtt_helper_msg_id_get();
	param.message_id = pending_publish_id;

	LOG_INF("Publishing unified payload to '%s' (QoS %d, %zu bytes): %s",
		CONFIG_MQTT_PUB_TOPIC, CONFIG_MQTT_PUB_QOS, payload_len, payload_buf);
	if (CONFIG_MQTT_PUB_QOS == MQTT_QOS_1_AT_LEAST_ONCE) {
		k_sem_reset(&mqtt_puback_sem);
		puback_result = 0;
	}

	err = mqtt_helper_publish(&param);
	if (err != 0) {
		LOG_ERR("mqtt_helper_publish failed: %d", err);
		return err;
	}

	if (CONFIG_MQTT_PUB_QOS == MQTT_QOS_0_AT_MOST_ONCE) {
		return 0;
	}

	err = wait_sem_or_abort(&mqtt_puback_sem, abort_sem, timeout);
	if (err != 0) {
		if (err == -ECANCELED) {
			LOG_WRN("MQTT PUBACK wait cancelled by MODE button");
		} else {
			LOG_ERR("MQTT PUBACK timed out");
		}
		return err;
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
		LOG_ERR("mqtt_helper_disconnect failed: %d", err);
		return err;
	}

	err = k_sem_take(&mqtt_disconnected_sem, timeout);
	if (err != 0) {
		LOG_WRN("MQTT disconnect timed out");
		return -ETIMEDOUT;
	}

	return 0;
}
