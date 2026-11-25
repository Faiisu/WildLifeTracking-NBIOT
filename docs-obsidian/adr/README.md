---
title: Architectural Decision Records (ADRs)
tags: [adr, architecture, index]
category: adr
updated: 2026-11-25
---

# Architectural Decision Records (ADRs)

Key architectural choices, trade-offs, and implementation status for the Cellular Asset Tracker.

## Index

| ADR | Note | Status | Code Alignment |
| :--- | :--- | :--- | :--- |
| ADR 0001 | [[adr/0001-use-zephyr-native-mqtt\|Use Zephyr Native MQTT Library]] | **Superseded** | All checked-in firmware uses Nordic `mqtt_helper` (`CONFIG_MQTT_HELPER=y`) on NCS v3.3.0. |
| ADR 0002 | [[adr/0002-motion-triggered-gps\|Motion-Triggered GPS (LIS2DH12)]] | **Proposed** | Hardware spec & devicetree ready (`accel0`, `P0.29`); optional Kconfig in sleep prototypes (`default n`). |
| ADR 0003 | [[adr/0003-secure-mqtt-over-tls\|Secure MQTT Over TLS (MQTTS)]] | **Proposed (Implemented)** | Enabled by default across all tracker & sleep firmware (`CONFIG_MQTT_LIB_TLS=y`, port `8883`, `sec_tag = 24`). |
| ADR 0004 | [[adr/0004-network-priority-nbiot-ltem\|Network Priority (NB-IoT / LTE-M)]] | **Proposed** | Dual-mode `CONFIG_LTE_NETWORK_MODE_LTE_M_NBIOT=y` / `_GPS=y` enabled; carrier selection modem-managed. |
| ADR 0005 | [[adr/0005-adaptive-stride-backlog-retention\|Adaptive Stride Backlog Retention]] | **Accepted (Implemented)** | Implemented in [[firmware-and-software/gps-mqtt-tracker\|gps_mqtt_tracker]] & [[firmware-and-software/lte-mqtt-tracker\|lte_mqtt_tracker]] (40 entries in RAM, QoS 1 `PUBACK` eviction). |
| ADR 0006 | [[adr/0006-unified-telemetry-json-schema\|Unified Telemetry JSON Schema]] | **Accepted (Implemented)** | Implemented across all 6 location-publishing firmware apps and [[firmware-and-software/gps-monitoring\|gps_monitoring]] ([[api-contract/mqtt-payload-schema\|Schema]]). |

---
Back to [[README|Documentation Hub]]
