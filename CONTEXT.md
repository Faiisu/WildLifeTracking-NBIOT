# Domain Model and Glossary — Cellular Asset Tracker

This document defines the key domain concepts used across the nRF9160 GPS/MQTT tracker codebase and distinguishes implemented capabilities from planned/proposed features.

## Implemented Concepts (`myprojects/gps_mqtt_tracker/`)

### 1. Tracker
The primary Zephyr firmware application running on the nRF9160 application core (`src/main.c`). It runs a six-state loop (`WAIT_FOR_GNSS` -> `CONNECT_LTE` -> `CONNECT_MQTT` -> `PUBLISH_BACKLOG` -> `DISCONNECT_MQTT` -> `ENTER_LOW_POWER`) coordinating GNSS, cellular connectivity, backlog retention, and MQTT publishing.

### 2. GNSS (Global Navigation Satellite System)
The positioning receiver inside the nRF9160 modem core (`src/tracker_gnss.c`). It runs periodically (`CONFIG_TRACKER_PERIODIC_INTERVAL`, `CONFIG_TRACKER_PERIODIC_TIMEOUT`), queues PVT frames (`gnss_pvt_queue`), purges stale frames before each wait, and accepts only frames with `NRF_MODEM_GNSS_PVT_FLAG_FIX_VALID`.

### 3. Cellular Link & Power Saving (PSM)
The LTE-M / NB-IoT connection managed via `lte_lc` (`src/tracker_modem.c`). The firmware enables `CONFIG_LTE_LC_PSM_MODULE=y` and disables eDRX (`CONFIG_LTE_LC_EDRX_MODULE=n`). After MQTT disconnect, it waits for `LTE_LC_MODEM_SLEEP_PSM`; if PSM is refused, unavailable, or times out, it falls back to `LTE_LC_FUNC_MODE_DEACTIVATE_LTE`.

### 4. GPS-First Operation
An operating pattern that prevents LTE activity from blocking weak GNSS signals on shared-radio hardware by keeping LTE deactivated during GNSS acquisition and activating `LTE_LC_FUNC_MODE_NORMAL` only when fixes are ready to upload.

### 5. Outage Backlog (Adaptive Stride Sampling)
A fixed-capacity RAM buffer (`CONFIG_TRACKER_BACKLOG_CAPACITY`, default `40`, `src/tracker_backlog.c`) that stores `struct tracker_location_fix` entries before upload is attempted. When full, the sampling stride doubles and existing entries are refiltered so retained points stay evenly spaced across the outage. Entries are removed only after QoS 1 `PUBACK` (see [ADR 0005](docs-obsidian/adr/0005-adaptive-stride-backlog-retention.md)).

### 6. MQTT Publisher (TLS + QoS 1)
The uplink-only MQTT client (`src/tracker_mqtt.c`) using `mqtt_helper` over modem-offloaded sockets (`CONFIG_NET_SOCKETS_OFFLOAD=y`). It provisions `credentials/ca-cert.pem` into modem security tag `24`, authenticates with optional username/password, sets client ID from `CONFIG_MQTT_CLIENT_ID` (`"WLTK01"` by default, or `nrf-<IMEI>` when empty), and publishes JSON payloads:

```json
{"lat":13.756331,"lon":100.501765,"alt":12.5,"acc":3.2,"time":"2026-08-08T09:30:00Z"}
```

## Planned / Unimplemented Concepts

### 7. Accelerometer (LIS2DH / LIS2DH12) & Motion Trigger Events
Although the SparkFun Thing Plus nRF9160 and Circuit Dojo Feather board devicetrees include an on-board ST `lis2dh` at `&i2c1` (`0x18`, `irq-gpios` on `gpio0` pin `29`) and standalone examples exist under `samples/accelerometer/` and `samples/tracker/`, **no accelerometer driver or motion wake-up logic is currently integrated into `gps_mqtt_tracker` or `gps_mqtt_stateControl`**. See [ADR 0002](docs-obsidian/adr/0002-motion-triggered-gps.md) and [lis2dh12.md](docs-obsidian/hardware/lis2dh12.md).

### 8. eDRX (extended Discontinuous Reception)
Explicitly disabled in `config/modem.conf` (`CONFIG_LTE_LC_EDRX_MODULE=n`) in both `gps_mqtt_tracker` and `gps_mqtt_stateControl`.
