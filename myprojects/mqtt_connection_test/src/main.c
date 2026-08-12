/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <errno.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <modem/lte_lc.h>
#include <modem/modem_key_mgmt.h>
#include <modem/nrf_modem_lib.h>
#include <net/mqtt_helper.h>

LOG_MODULE_REGISTER(mqtt_connection_test, LOG_LEVEL_INF);

static K_SEM_DEFINE(lte_connected, 0, 1);
static K_SEM_DEFINE(mqtt_connected, 0, 1);
static K_SEM_DEFINE(mqtt_puback, 0, 1);
static K_SEM_DEFINE(mqtt_disconnected, 0, 1);

static const unsigned char ca_certificate[] = {
#if __has_include("ca-cert.pem")
#include "ca-cert.pem"
#else
""
#endif
};

static uint16_t pending_publish_id;

static void lte_handler(const struct lte_lc_evt *const evt)
{
	if (evt->type != LTE_LC_EVT_NW_REG_STATUS) {
		return;
	}

	if (evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ||
	    evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_ROAMING) {
		LOG_INF("LTE registered (%s)", evt->nw_reg_status ==
			LTE_LC_NW_REG_REGISTERED_HOME ? "home" : "roaming");
		k_sem_give(&lte_connected);
	}
}

static void mqtt_connack(enum mqtt_conn_return_code result, bool session_present)
{
	ARG_UNUSED(session_present);
	if (result == MQTT_CONNECTION_ACCEPTED) {
		LOG_INF("MQTT CONNACK accepted");
	} else {
		LOG_ERR("MQTT CONNACK rejected: %d", result);
	}
	k_sem_give(&mqtt_connected);
}

static void on_mqtt_puback(uint16_t message_id, int result)
{
	if (message_id != pending_publish_id) {
		LOG_WRN("Unexpected PUBACK ID: %u", message_id);
		return;
	}

	LOG_INF("MQTT PUBACK result: %d", result);
	k_sem_give(&mqtt_puback);
}

static void on_mqtt_disconnected(int result)
{
	LOG_INF("MQTT disconnected: %d", result);
	k_sem_give(&mqtt_disconnected);
}

static int ca_provision(void)
{
	bool exists;
	int err;
	int mismatch;

	if (sizeof(ca_certificate) <= 1) {
		LOG_ERR("Missing credentials/ca-cert.pem");
		return -ENOENT;
	}

	err = modem_key_mgmt_exists(CONFIG_MQTT_HELPER_SEC_TAG,
		MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN, &exists);
	if (err != 0) {
		return err;
	}

	if (exists) {
		mismatch = modem_key_mgmt_cmp(CONFIG_MQTT_HELPER_SEC_TAG,
			MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN, ca_certificate,
			sizeof(ca_certificate) - 1);
		if (mismatch == 0) {
			LOG_INF("Broker CA already provisioned in sec tag %d",
				CONFIG_MQTT_HELPER_SEC_TAG);
			return 0;
		}

		err = modem_key_mgmt_delete(CONFIG_MQTT_HELPER_SEC_TAG,
			MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN);
		if (err != 0) {
			return err;
		}
	}

	err = modem_key_mgmt_write(CONFIG_MQTT_HELPER_SEC_TAG,
		MODEM_KEY_MGMT_CRED_TYPE_CA_CHAIN, ca_certificate,
		sizeof(ca_certificate) - 1);
	if (err == 0) {
	LOG_INF("Broker CA provisioned in sec tag %d", CONFIG_MQTT_HELPER_SEC_TAG);
	}

	return err;
}

int main(void)
{
	struct mqtt_helper_cfg mqtt_cfg = {
		.cb = {
			.on_connack = mqtt_connack,
			.on_puback = on_mqtt_puback,
			.on_disconnect = on_mqtt_disconnected,
		},
	};
	struct mqtt_helper_conn_params connection = {
		.hostname.ptr = CONFIG_MQTT_TEST_BROKER_HOSTNAME,
		.hostname.size = strlen(CONFIG_MQTT_TEST_BROKER_HOSTNAME),
		.device_id.ptr = CONFIG_MQTT_TEST_CLIENT_ID,
		.device_id.size = strlen(CONFIG_MQTT_TEST_CLIENT_ID),
		.user_name.ptr = CONFIG_MQTT_TEST_USERNAME,
		.user_name.size = strlen(CONFIG_MQTT_TEST_USERNAME),
		.password.ptr = CONFIG_MQTT_TEST_PASSWORD,
		.password.size = strlen(CONFIG_MQTT_TEST_PASSWORD),
	};
	struct mqtt_publish_param publish = {0};
	int err;

	if (connection.hostname.size == 0 || connection.user_name.size == 0 ||
	    connection.password.size == 0) {
		LOG_ERR("Set host, username, and password in config/mqtt-credentials.conf");
		return 0;
	}

	err = nrf_modem_lib_init();
	if (err != 0) {
		LOG_ERR("Modem initialization failed: %d", err);
		return 0;
	}

	err = ca_provision();
	if (err != 0) {
		LOG_ERR("CA provisioning failed: %d", err);
		return 0;
	}

	err = lte_lc_connect_async(lte_handler);
	if (err != 0) {
		LOG_ERR("LTE connection start failed: %d", err);
		return 0;
	}
	if (k_sem_take(&lte_connected, K_SECONDS(120)) != 0) {
		LOG_ERR("LTE registration timed out");
		return 0;
	}

	err = mqtt_helper_init(&mqtt_cfg);
	if (err != 0) {
		LOG_ERR("MQTT helper initialization failed: %d", err);
		return 0;
	}

	LOG_INF("Connecting to %s:%d as %s", CONFIG_MQTT_TEST_BROKER_HOSTNAME,
		CONFIG_MQTT_TEST_BROKER_PORT, CONFIG_MQTT_TEST_CLIENT_ID);
	err = mqtt_helper_connect(&connection);
	if (err != 0) {
		LOG_ERR("MQTT connect start failed: %d", err);
		return 0;
	}
	if (k_sem_take(&mqtt_connected, K_SECONDS(30)) != 0) {
		LOG_ERR("MQTT CONNACK timed out");
		return 0;
	}

	publish.message.payload.data = (uint8_t *)CONFIG_MQTT_TEST_PAYLOAD;
	publish.message.payload.len = strlen(CONFIG_MQTT_TEST_PAYLOAD);
	publish.message.topic.topic.utf8 = CONFIG_MQTT_TEST_PUB_TOPIC;
	publish.message.topic.topic.size = strlen(CONFIG_MQTT_TEST_PUB_TOPIC);
	publish.message.topic.qos = (enum mqtt_qos)CONFIG_MQTT_TEST_PUB_QOS;
	pending_publish_id = mqtt_helper_msg_id_get();
	publish.message_id = pending_publish_id;
	LOG_INF("Publishing with message ID %u", publish.message_id);
	if (CONFIG_MQTT_TEST_PUB_QOS == MQTT_QOS_1_AT_LEAST_ONCE) {
		k_sem_reset(&mqtt_puback);
	}

	err = mqtt_helper_publish(&publish);
	if (err != 0) {
		LOG_ERR("MQTT publish failed: %d", err);
		return 0;
	}
	LOG_INF("MQTT publish submitted (QoS %d)", CONFIG_MQTT_TEST_PUB_QOS);

	if (CONFIG_MQTT_TEST_PUB_QOS == MQTT_QOS_1_AT_LEAST_ONCE &&
	    k_sem_take(&mqtt_puback, K_SECONDS(30)) != 0) {
		LOG_ERR("MQTT PUBACK timed out");
		return 0;
	}

	k_sem_reset(&mqtt_disconnected);
	err = mqtt_helper_disconnect();
	if (err != 0) {
		LOG_ERR("MQTT disconnect failed: %d", err);
		return 0;
	}
	if (k_sem_take(&mqtt_disconnected, K_SECONDS(10)) != 0) {
		LOG_WRN("MQTT disconnect timed out");
	}

	LOG_INF("MQTT connection test passed");
	return 0;
}
