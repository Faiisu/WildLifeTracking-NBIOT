---
title: MQTT Connection Test Firmware
tags: [firmware, mqtt, tls, smoke-test, nrf9160]
category: firmware
app_path: myprojects/mqtt_connection_test
status: active
updated: 2026-11-25
---

# MQTT Connection Test (`myprojects/mqtt_connection_test/`)

Standalone LTE + TLS + MQTT connectivity smoke test (verifies broker credentials and CA provisioning independently of GNSS/Cell location).

## Flow

```text
modem init -> provision broker CA (sec_tag 24) -> LTE registration
-> TLS MQTT CONNECT (:8883) -> username/password auth
-> publish "mqtt_connection_test" (QoS 0, topic test/nrf91) -> disconnect
```

> [!note]
> Unlike the tracker apps, `config/logging.conf` enables USB-UART console (`CONFIG_LOG_BACKEND_UART=y`, `CONFIG_UART_CONSOLE=y`) so test output can be read without J-Link RTT. It sends a static string and does not use the telemetry JSON schema.

## Build & Local Setup

```bash
cp myprojects/mqtt_connection_test/config/mqtt-credentials.conf.example \
   myprojects/mqtt_connection_test/config/mqtt-credentials.conf
# Place broker root CA at myprojects/mqtt_connection_test/credentials/ca-cert.pem

west build -p always -b sparkfun_thing_plus/nrf9160/ns \
  myprojects/mqtt_connection_test --sysbuild
```

## Related Notes
- [[firmware-and-software/README|Firmware and Software Catalog]]
- [[api-contract/README|API Contract]]
