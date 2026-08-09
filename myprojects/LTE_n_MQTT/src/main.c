/*
 * LTE and MQTT publish-only test application.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include <modem/lte_lc.h>
#include <modem/nrf_modem_lib.h>
#include <net/mqtt_helper.h>
#include <nrf_modem_at.h>

LOG_MODULE_REGISTER(LTE_n_MQTT, LOG_LEVEL_INF);

#define IMEI_LEN 15
#define CGSN_RESPONSE_LENGTH (IMEI_LEN + 6 + 1)
#define CLIENT_ID_LEN (sizeof("nrf-") + IMEI_LEN)

static K_SEM_DEFINE(lte_connected, 0, 1);
static K_SEM_DEFINE(mqtt_connected, 0, 1);

static char client_id[CLIENT_ID_LEN];

static void lte_handler(const struct lte_lc_evt *const evt)
{
	switch (evt->type) {
	case LTE_LC_EVT_NW_REG_STATUS:
		if (evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ||
		    evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_ROAMING) {
			LOG_INF("LTE registered (%s)",
				evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ?
				"home" : "roaming");
			k_sem_give(&lte_connected);
		}
		break;

	case LTE_LC_EVT_RRC_UPDATE:
		LOG_INF("RRC mode: %s",
			evt->rrc_mode == LTE_LC_RRC_MODE_CONNECTED ?
			"connected" : "idle");
		break;

	default:
		break;
	}
}

static void on_mqtt_connack(enum mqtt_conn_return_code return_code,
			    bool session_present)
{
	ARG_UNUSED(session_present);

	if (return_code != MQTT_CONNECTION_ACCEPTED) {
		LOG_ERR("MQTT connection rejected: %d", return_code);
		return;
	}

	LOG_INF("MQTT connected to %s:%d",
		CONFIG_MQTT_BROKER_HOSTNAME, CONFIG_MQTT_HELPER_PORT);
	k_sem_give(&mqtt_connected);
}

static void on_mqtt_disconnect(int result)
{
	LOG_WRN("MQTT disconnected: %d", result);
}

static void on_mqtt_puback(uint16_t message_id, int result)
{
	if (result == 0) {
		LOG_INF("Broker acknowledged message ID %u", message_id);
	} else {
		LOG_ERR("PUBACK failed for message ID %u: %d", message_id, result);
	}
}

static int client_id_get(char *buffer, size_t buffer_size)
{
	char imei_response[CGSN_RESPONSE_LENGTH];
	int err;
	int len;

	if (buffer == NULL || buffer_size == 0) {
		return -EINVAL;
	}

	if (strlen(CONFIG_MQTT_CLIENT_ID) > 0) {
		len = snprintf(buffer, buffer_size, "%s", CONFIG_MQTT_CLIENT_ID);
		if (len < 0 || (size_t)len >= buffer_size) {
			return -EMSGSIZE;
		}
		return 0;
	}

	err = nrf_modem_at_cmd(imei_response, sizeof(imei_response), "AT+CGSN");
	if (err != 0) {
		LOG_ERR("AT+CGSN failed: %d", err);
		return err;
	}

	imei_response[IMEI_LEN] = '\0';
	len = snprintf(buffer, buffer_size, "nrf-%.*s", IMEI_LEN, imei_response);
	if (len < 0 || (size_t)len >= buffer_size) {
		return -EMSGSIZE;
	}

	return 0;
}

static int modem_connect(void)
{
	int err;

	LOG_INF("Initializing nRF Modem library");
	err = nrf_modem_lib_init();
	if (err != 0) {
		LOG_ERR("nrf_modem_lib_init failed: %d", err);
		return err;
	}

	LOG_INF("Connecting to LTE network");
	err = lte_lc_connect_async(lte_handler);
	if (err != 0) {
		LOG_ERR("lte_lc_connect_async failed: %d", err);
		return err;
	}

	err = k_sem_take(&lte_connected,
			 K_SECONDS(CONFIG_LTE_CONNECT_TIMEOUT_SECONDS));
	if (err != 0) {
		LOG_ERR("LTE registration timed out after %d seconds",
			CONFIG_LTE_CONNECT_TIMEOUT_SECONDS);
		return -ETIMEDOUT;
	}

	return 0;
}

static int mqtt_broker_connect(void)
{
	struct mqtt_helper_conn_params connection = {
		.hostname.ptr = CONFIG_MQTT_BROKER_HOSTNAME,
		.hostname.size = strlen(CONFIG_MQTT_BROKER_HOSTNAME),
		.device_id.ptr = client_id,
		.device_id.size = strlen(client_id),
	};
	int err;

	LOG_INF("Connecting to MQTT broker %s:%d as %s",
		CONFIG_MQTT_BROKER_HOSTNAME, CONFIG_MQTT_HELPER_PORT, client_id);
	err = mqtt_helper_connect(&connection);
	if (err != 0) {
		LOG_ERR("mqtt_helper_connect failed: %d", err);
		return err;
	}

	err = k_sem_take(&mqtt_connected,
			 K_SECONDS(CONFIG_MQTT_CONNECT_TIMEOUT_SECONDS));
	if (err != 0) {
		LOG_ERR("MQTT CONNACK timed out after %d seconds",
			CONFIG_MQTT_CONNECT_TIMEOUT_SECONDS);
		return -ETIMEDOUT;
	}

	return 0;
}

static int publish_test_payload(void)
{
	const char *payload = CONFIG_MQTT_TEST_PAYLOAD;
	struct mqtt_publish_param param = { 0 };
	int err;

	param.message.payload.data = (uint8_t *)payload;
	param.message.payload.len = strlen(payload);
	param.message.topic.qos = MQTT_QOS_1_AT_LEAST_ONCE;
	param.message.topic.topic.utf8 = CONFIG_MQTT_PUB_TOPIC;
	param.message.topic.topic.size = strlen(CONFIG_MQTT_PUB_TOPIC);
	param.message_id = mqtt_helper_msg_id_get();
	param.dup_flag = 0;
	param.retain_flag = 0;

	err = mqtt_helper_publish(&param);
	if (err != 0) {
		LOG_ERR("MQTT publish failed: %d", err);
		return err;
	}

	LOG_INF("Published to %s: %s", CONFIG_MQTT_PUB_TOPIC, payload);
	return 0;
}

int main(void)
{
	struct mqtt_helper_cfg mqtt_config = {
		.cb = {
			.on_connack = on_mqtt_connack,
			.on_disconnect = on_mqtt_disconnect,
			.on_puback = on_mqtt_puback,
		},
	};
	int err;

	LOG_INF("LTE + MQTT publish-only test started");

	err = modem_connect();
	if (err != 0) {
		return 0;
	}

	err = mqtt_helper_init(&mqtt_config);
	if (err != 0) {
		LOG_ERR("mqtt_helper_init failed: %d", err);
		return 0;
	}

	err = client_id_get(client_id, sizeof(client_id));
	if (err != 0) {
		LOG_ERR("Failed to create MQTT client ID: %d", err);
		return 0;
	}

	err = mqtt_broker_connect();
	if (err != 0) {
		return 0;
	}

	while (true) {
		(void)publish_test_payload();
		k_sleep(K_SECONDS(CONFIG_MQTT_PUBLISH_INTERVAL_SECONDS));
	}

	return 0;
}
