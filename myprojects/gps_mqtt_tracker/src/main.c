/*
 * Copyright (c) 2024 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#if IS_ENABLED(CONFIG_ENABLE_LEDS)
#include <dk_buttons_and_leds.h>
#endif
#include <modem/nrf_modem_lib.h>
#include <nrf_modem_at.h>
#include <modem/lte_lc.h>
#include <nrf_modem_gnss.h>
#include <net/mqtt_helper.h>

LOG_MODULE_REGISTER(GPS_MQTT_Tracker, LOG_LEVEL_INF);

#define IMEI_LEN 15
#define CGSN_RESPONSE_LENGTH (IMEI_LEN + 6 + 1)
#define CLIENT_ID_LEN (sizeof("nrf-") + IMEI_LEN)

#define MQTT_PAYLOAD_MAX_LEN 256
#define SUB_TOPIC_MSG_ID 9999

/* Semaphores for task synchronization */
static K_SEM_DEFINE(lte_connected, 0, 1);
static K_SEM_DEFINE(gnss_fix_sem, 0, 1);
static K_SEM_DEFINE(mqtt_conn_sem, 0, 1);

/* Global PVT data buffer */
static struct nrf_modem_gnss_pvt_data_frame current_pvt;
static char client_id_buf[CLIENT_ID_LEN];

/**
 * @brief Print formatted GNSS location fix data to serial console
 */
static void print_fix_data(const struct nrf_modem_gnss_pvt_data_frame *pvt)
{
	LOG_INF("========================================");
	LOG_INF("📍 GNSS Location Fix Acquired:");
	LOG_INF("   Latitude:  %.06f°", pvt->latitude);
	LOG_INF("   Longitude: %.06f°", pvt->longitude);
	LOG_INF("   Altitude:  %.01f m", (double)pvt->altitude);
	LOG_INF("   Accuracy:  %.01f m", (double)pvt->accuracy);
	LOG_INF("   Time (UTC): %04u-%02u-%02u %02u:%02u:%02u.%03u",
		pvt->datetime.year, pvt->datetime.month, pvt->datetime.day,
		pvt->datetime.hour, pvt->datetime.minute, pvt->datetime.seconds,
		pvt->datetime.ms);
	LOG_INF("========================================");
}

/**
 * @brief GNSS Event Handler
 */
static void gnss_event_handler(int event)
{
	int err;

	switch (event) {
	case NRF_MODEM_GNSS_EVT_PVT:
		LOG_INF("Searching for GNSS Satellites...");
#if IS_ENABLED(CONFIG_ENABLE_LEDS)
		dk_set_led_off(DK_LED1);
		dk_set_led_on(DK_LED2);
#endif
		break;

	case NRF_MODEM_GNSS_EVT_FIX:
		LOG_INF("GNSS fix event detected!");
		break;

	case NRF_MODEM_GNSS_EVT_PERIODIC_WAKEUP:
		LOG_INF("GNSS woke up for periodic fix cycle");
		break;

	case NRF_MODEM_GNSS_EVT_BLOCKED:
		LOG_INF("GNSS event blocked by LTE activity");
		break;

	case NRF_MODEM_GNSS_EVT_SLEEP_AFTER_FIX:
		LOG_INF("GNSS fix achieved! Reading PVT frame...");
#if IS_ENABLED(CONFIG_ENABLE_LEDS)
		dk_set_led_off(DK_LED2);
		dk_set_led_on(DK_LED3);
#endif

		err = nrf_modem_gnss_read(&current_pvt, sizeof(current_pvt), NRF_MODEM_GNSS_DATA_PVT);
		if (err == 0) {
			print_fix_data(&current_pvt);
			k_sem_give(&gnss_fix_sem);
		} else {
			LOG_ERR("Failed to read GNSS PVT data, error: %d", err);
		}
		break;

	case NRF_MODEM_GNSS_EVT_SLEEP_AFTER_TIMEOUT:
		LOG_WRN("GNSS fix timeout reached without valid fix.");
		break;

	default:
		break;
	}
}

/**
 * @brief Initialize and start the GNSS receiver
 */
static int gnss_init_and_start(void)
{
	int err;

#if defined(CONFIG_GNSS_HIGH_ACCURACY_TIMING_SOURCE)
	err = nrf_modem_gnss_timing_source_set(NRF_MODEM_GNSS_TIMING_SOURCE_TCXO);
	if (err != 0) {
		LOG_ERR("Failed to set TCXO timing source: %d", err);
		return err;
	}
#endif

#if defined(CONFIG_GNSS_LOW_ACCURACY)
	uint8_t use_case = NRF_MODEM_GNSS_USE_CASE_MULTIPLE_HOT_START | NRF_MODEM_GNSS_USE_CASE_LOW_ACCURACY;
	err = nrf_modem_gnss_use_case_set(use_case);
	if (err != 0) {
		LOG_ERR("Failed to set low accuracy use case: %d", err);
		return err;
	}
#endif

	err = nrf_modem_gnss_event_handler_set(gnss_event_handler);
	if (err != 0) {
		LOG_ERR("Failed to set GNSS event handler: %d", err);
		return err;
	}

	err = nrf_modem_gnss_fix_interval_set(CONFIG_TRACKER_PERIODIC_INTERVAL);
	if (err != 0) {
		LOG_ERR("Failed to set GNSS fix interval: %d", err);
		return err;
	}

	err = nrf_modem_gnss_fix_retry_set(CONFIG_TRACKER_PERIODIC_TIMEOUT);
	if (err != 0) {
		LOG_ERR("Failed to set GNSS fix retry timeout: %d", err);
		return err;
	}

	err = nrf_modem_gnss_start();
	if (err != 0) {
		LOG_ERR("Failed to start GNSS module: %d", err);
		return err;
	}

	err = nrf_modem_gnss_prio_mode_enable();
	if (err != 0) {
		LOG_ERR("Failed to enable GNSS priority mode: %d", err);
		return err;
	}

	LOG_INF("GNSS initialized and started successfully (Interval: %ds, Timeout: %ds)",
		CONFIG_TRACKER_PERIODIC_INTERVAL, CONFIG_TRACKER_PERIODIC_TIMEOUT);
	return 0;
}

/**
 * @brief Generate unique MQTT Client ID from modem IMEI or Kconfig
 */
static int client_id_get(char *buffer, size_t buffer_len)
{
	int len, err;
	char imei_buf[CGSN_RESPONSE_LENGTH];

	if (strlen(CONFIG_MQTT_CLIENT_ID) > 0) {
		len = snprintf(buffer, buffer_len, "%s", CONFIG_MQTT_CLIENT_ID);
		if (len < 0 || (size_t)len >= buffer_len) {
			LOG_ERR("Custom Client ID too long");
			return -EMSGSIZE;
		}
		return 0;
	}

	err = nrf_modem_at_cmd(imei_buf, sizeof(imei_buf), "AT+CGSN");
	if (err) {
		LOG_ERR("Failed to obtain IMEI via AT+CGSN, err: %d", err);
		return err;
	}

	imei_buf[IMEI_LEN] = '\0';
	len = snprintf(buffer, buffer_len, "nrf-%.*s", IMEI_LEN, imei_buf);
	if (len < 0 || (size_t)len >= buffer_len) {
		LOG_ERR("Formatted Client ID too long");
		return -EMSGSIZE;
	}

	LOG_INF("Generated MQTT Client ID: %s", buffer);
	return 0;
}

/**
 * @brief MQTT Subscription Helper
 */
static void subscribe_topics(void)
{
	int err;
	struct mqtt_topic sub_topic = {
		.topic = {
			.utf8 = CONFIG_MQTT_SUB_TOPIC,
			.size = strlen(CONFIG_MQTT_SUB_TOPIC)
		},
		.qos = MQTT_QOS_1_AT_LEAST_ONCE
	};

	struct mqtt_subscription_list sub_list = {
		.list = &sub_topic,
		.list_count = 1,
		.message_id = SUB_TOPIC_MSG_ID
	};

	LOG_INF("Subscribing to MQTT topic: %s", CONFIG_MQTT_SUB_TOPIC);
	err = mqtt_helper_subscribe(&sub_list);
	if (err) {
		LOG_ERR("Failed to subscribe to MQTT topic, err: %d", err);
	}
}

/**
 * @brief MQTT Callback: CONNACK
 */
static void on_mqtt_connack(enum mqtt_conn_return_code return_code, bool session_present)
{
	ARG_UNUSED(session_present);

	if (return_code == MQTT_CONNECTION_ACCEPTED) {
		LOG_INF("✅ Connected to MQTT Broker: %s:%d",
			CONFIG_MQTT_BROKER_HOSTNAME, CONFIG_MQTT_BROKER_PORT);
		k_sem_give(&mqtt_conn_sem);
		subscribe_topics();
	} else {
		LOG_ERR("Failed to connect to MQTT broker, return code: %d", return_code);
	}
}

/**
 * @brief MQTT Callback: SUBACK
 */
static void on_mqtt_suback(uint16_t message_id, int result)
{
	if (result != MQTT_SUBACK_FAILURE) {
		LOG_INF("✅ Subscribed successfully to topic (ID: %d, QoS: %d)", message_id, result);
	} else {
		LOG_ERR("❌ Subscription failed for topic ID: %d", message_id);
	}
}

/**
 * @brief MQTT Callback: Incoming Message Publish
 */
static void on_mqtt_publish(struct mqtt_helper_buf topic, struct mqtt_helper_buf payload)
{
	LOG_INF("📩 Received MQTT Message on Topic: %.*s | Payload: %.*s",
		topic.size, topic.ptr, payload.size, payload.ptr);
}

/**
 * @brief MQTT Callback: Disconnect
 */
static void on_mqtt_disconnect(int result)
{
	LOG_WRN("⚠️ MQTT Client disconnected: %d", result);
}

/**
 * @brief Publish GPS location payload over MQTT
 */
static int publish_gps_data(const struct nrf_modem_gnss_pvt_data_frame *pvt)
{
	int err, payload_len;
	char payload[MQTT_PAYLOAD_MAX_LEN];
	struct mqtt_publish_param param;

	payload_len = snprintf(payload, sizeof(payload),
		"{\"lat\":%.06f,\"lon\":%.06f,\"alt\":%.01f,\"acc\":%.01f,\"time\":\"%04u-%02u-%02uT%02u:%02u:%02uZ\"}",
		pvt->latitude, pvt->longitude, (double)pvt->altitude, (double)pvt->accuracy,
		pvt->datetime.year, pvt->datetime.month, pvt->datetime.day,
		pvt->datetime.hour, pvt->datetime.minute, pvt->datetime.seconds);

	if (payload_len < 0 || (size_t)payload_len >= sizeof(payload)) {
		LOG_ERR("Payload buffer overflow");
		return -EMSGSIZE;
	}

	memset(&param, 0, sizeof(param));
	param.message.payload.data = (uint8_t *)payload;
	param.message.payload.len = payload_len;
	param.message.topic.qos = MQTT_QOS_1_AT_LEAST_ONCE;
	param.message_id = mqtt_helper_msg_id_get();
	param.message.topic.topic.utf8 = CONFIG_MQTT_PUB_TOPIC;
	param.message.topic.topic.size = strlen(CONFIG_MQTT_PUB_TOPIC);
	param.dup_flag = 0;
	param.retain_flag = 0;

	LOG_INF("📤 Publishing GPS payload to topic '%s':", CONFIG_MQTT_PUB_TOPIC);
	LOG_INF("   %s", payload);

	err = mqtt_helper_publish(&param);
	if (err) {
		LOG_ERR("Failed to publish MQTT message, error: %d", err);
		return err;
	}

	LOG_INF("Successfully published GPS data via MQTT!");
	return 0;
}

/**
 * @brief LTE Link Control Handler
 */
static void lte_handler(const struct lte_lc_evt *const evt)
{
	switch (evt->type) {
	case LTE_LC_EVT_NW_REG_STATUS:
		if (evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ||
		    evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_ROAMING) {
			LOG_INF("📶 LTE Registered (%s)",
				evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ?
				"Home Network" : "Roaming");
#if IS_ENABLED(CONFIG_ENABLE_LEDS)
			dk_set_led_on(DK_LED1);
#endif
			k_sem_give(&lte_connected);
		}
		break;
	case LTE_LC_EVT_RRC_UPDATE:
		LOG_INF("RRC mode: %s", evt->rrc_mode == LTE_LC_RRC_MODE_CONNECTED ? "Connected" : "Idle");
		break;
	default:
		break;
	}
}

/**
 * @brief Configure nRF Modem and initialize LTE link connection
 */
static int modem_configure(void)
{
	int err;

	LOG_INF("Initializing nRF Modem Library...");
	err = nrf_modem_lib_init();
	if (err) {
		LOG_ERR("Failed to initialize nRF Modem Library: %d", err);
		return err;
	}

	LOG_INF("Connecting to cellular network...");
	err = lte_lc_connect_async(lte_handler);
	if (err) {
		LOG_ERR("lte_lc_connect_async failed: %d", err);
		return err;
	}

	/* Initially deactivate LTE so GNSS can get immediate antenna access */
	err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_DEACTIVATE_LTE);
	if (err) {
		LOG_ERR("Failed to set deactivate LTE mode: %d", err);
		return err;
	}

	return 0;
}

int main(void)
{
	int err;

	LOG_INF("===============================================");
	LOG_INF("🚀 GPS MQTT Asset Tracker Application Started");
	LOG_INF("   Version: %d.%d.%d", CONFIG_TRACKER_VERSION_MAJOR,
		CONFIG_TRACKER_VERSION_MINOR, CONFIG_TRACKER_VERSION_PATCH);
	LOG_INF("===============================================");

#if IS_ENABLED(CONFIG_ENABLE_LEDS)
	if (dk_leds_init() != 0) {
		LOG_ERR("Failed to initialize LED library");
	}
#endif

	err = modem_configure();
	if (err) {
		LOG_ERR("Modem configuration failed, aborting.");
		return 0;
	}

	/* Initialize MQTT helper library */
	struct mqtt_helper_cfg mqtt_cfg = {
		.cb = {
			.on_connack = on_mqtt_connack,
			.on_disconnect = on_mqtt_disconnect,
			.on_publish = on_mqtt_publish,
			.on_suback = on_mqtt_suback,
		},
	};

	err = mqtt_helper_init(&mqtt_cfg);
	if (err) {
		LOG_ERR("Failed to initialize MQTT helper: %d", err);
		return 0;
	}

	err = client_id_get(client_id_buf, sizeof(client_id_buf));
	if (err) {
		LOG_ERR("Failed to generate Client ID");
		return 0;
	}

	LOG_INF("Starting GNSS receiver...");
	err = gnss_init_and_start();
	if (err) {
		LOG_ERR("Failed to start GNSS");
		return 0;
	}

	while (1) {
		LOG_INF("Waiting for GNSS fix...");
		k_sem_take(&gnss_fix_sem, K_FOREVER);

		LOG_INF("Activating LTE to send GPS payload over MQTT...");
#if IS_ENABLED(CONFIG_ENABLE_LEDS)
		dk_set_led_on(DK_LED4);
#endif

		err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_NORMAL);
		if (err != 0) {
			LOG_ERR("Failed to activate LTE mode: %d", err);
			continue;
		}

		/* Wait for LTE network registration */
		if (k_sem_take(&lte_connected, K_SECONDS(60)) != 0) {
			LOG_ERR("LTE connection timed out!");
		} else {
			/* Connect to MQTT Broker */
			struct mqtt_helper_conn_params conn_params = {
				.hostname.ptr = CONFIG_MQTT_BROKER_HOSTNAME,
				.hostname.size = strlen(CONFIG_MQTT_BROKER_HOSTNAME),
				.device_id.ptr = client_id_buf,
				.device_id.size = strlen(client_id_buf),
			};

			LOG_INF("Connecting to MQTT Broker (%s:%d)...",
				CONFIG_MQTT_BROKER_HOSTNAME, CONFIG_MQTT_BROKER_PORT);
			err = mqtt_helper_connect(&conn_params);
			if (err) {
				LOG_ERR("MQTT connection attempt failed: %d", err);
			} else {
				/* Wait for MQTT CONNACK */
				if (k_sem_take(&mqtt_conn_sem, K_SECONDS(15)) == 0) {
					publish_gps_data(&current_pvt);
					k_sleep(K_SECONDS(2));
				} else {
					LOG_ERR("MQTT CONNACK timed out!");
				}
			}
		}

		/* Deactivate LTE to return antenna focus to GNSS for the next fix cycle */
#if IS_ENABLED(CONFIG_ENABLE_LEDS)
		dk_set_led_off(DK_LED4);
#endif
		LOG_INF("Deactivating LTE to return antenna control to GNSS...");
		err = lte_lc_func_mode_set(LTE_LC_FUNC_MODE_DEACTIVATE_LTE);
		if (err != 0) {
			LOG_ERR("Failed to deactivate LTE: %d", err);
		}
	}

	return 0;
}
