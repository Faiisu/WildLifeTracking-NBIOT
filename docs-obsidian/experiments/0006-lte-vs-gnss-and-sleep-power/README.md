[Back to Experiments Index](../README.md) | [Back to Documentation Hub](../../README.md)

# Experiment 0006: LTE Cell-ID vs GNSS Acquisition & Sleep Power

## Question and Status

- **Status:** **In progress** (preliminary user-reported bench figures recorded; controlled repeat runs with archived raw traces and standalone `release.conf` J-Link-disconnected sleep measurements pending).
- **Evidence type:** **Preliminary user-reported bench observations** (summary averages; raw CSV traces, supply voltage, instrument model, and repeat-run standard deviation not yet archived) + **derived arithmetic** ($E = P \times t$).
- **Date:** 2026-11-25, Asia/Bangkok (UTC+07:00).
- **Question:** How do LTE Cell-ID positioning, GNSS positioning, and sleep modes (`DEACTIVATE_LTE` + System ON Idle vs `sys_poweroff()`) compare in power, duration, and energy per fix on `sparkfun_thing_plus/nrf9160/ns`?
- **Decision supported:** Inform the final tracker positioning strategy (pure LTE vs pure GNSS vs Hybrid) and sleep policy for battery sizing. Supports [ADR 0006](../../adr/0006-unified-telemetry-json-schema.md).

## Alternatives & Firmware

| Alternative | Firmware | Behavior |
| :--- | :--- | :--- |
| **Stage 1: LTE Cell-ID** | `myprojects/sleep_mode_psm` / `myprojects/lte_vs_gnss_compare` | Open LTE → connect (≤ 30 s) → `lte_lc_neighbor_cell_measurement()` → MQTT/TLS publish → `DEACTIVATE_LTE` → Sleep |
| **Stage 2: GNSS (GPS-first)** | `myprojects/gps_mqtt_tracker` / `myprojects/lte_vs_gnss_compare` | LTE off → Open GNSS → PVT fix (≤ 300 s) → off GNSS → connect LTE → MQTT/TLS publish → `DEACTIVATE_LTE` → Sleep |
| **Sleep Mode 1 (System ON Idle + PSM/Fallback)** | `myprojects/sleep_mode_psm` | Requests PSM; falls back to `DEACTIVATE_LTE` + System ON Idle when carrier refuses PSM (`active_time = -1 s`). Wakes on 30 s timer or `MODE` button. |
| **Sleep Mode 2 (nRF9160 System OFF)** | `myprojects/sleep_mode_sys_off` | `lte_lc_power_off()` + `nrf_modem_lib_shutdown()` + `sys_poweroff()`. Wakes on `MODE` button (`sw0`, `P0.12`). |

## Observed Hardware / Network Behavior (from J-Link RTT Logs)

- **Carrier / SIM:** AIS Thailand (`MCC=520, MNC=3`, roaming registration, `TAC=950`, `CID=159260272`, `PCI=242`, `RSRP=-75` to `-81 dBm`).
- **LTE re-attach time:** ~2.5 – 3.5 s from `LTE_LC_FUNC_MODE_NORMAL` to `LTE_LC_NW_REG_REGISTERED_ROAMING`.
- **Cell measurement latency:** ~1.4 ms (`LTE_LC_EVT_NEIGHBOR_CELL_MEAS`).
- **MQTT/TLS publish (`test/nrf91`, HiveMQ Cloud `:8883`, QoS 1):** ~3.8 – 4.0 s total (TLS handshake + `CONNACK` + `PUBLISH` + `PUBACK` + disconnect).
- **PSM negotiation:** Network returned `TAU=3600 s, active_time=-1 s` (`Network did not grant PSM`), triggering the firmware's `LTE_LC_FUNC_MODE_DEACTIVATE_LTE` fallback.

## Preliminary Bench Results

> [!NOTE]
> Values below are **user-reported bench averages** (not yet backed by archived multi-cycle raw traces). Derived energy uses $E = P \times t$.

| Mode / Stage | Reported Avg Power | Reported Duration | Derived Energy | Notes & Limitations |
| :--- | :---: | :---: | :---: | :--- |
| **GNSS / GPS finding** | ~181 mW | ~5 min (300 s) | ~54,300 mJ (~15.08 mWh) | Cold/challenging acquisition baseline; outdoor Warm/Hot Start or A-GPS not yet measured |
| **LTE connect + Cell-ID location** | ~150 mW | ~5 s | ~750 mJ (~0.208 mWh) | Serving cell `MCC=520, MNC=3` (~2.5–3.5 s attach + ~1.4 ms cell meas) |
| **Sleep (`STATE_SLEEP`, System 1)** | ~23 mW | — | ~23 mJ/s (~6.39 µWh/s) | `DEACTIVATE_LTE` + System ON Idle (`W25Q32JV` DPD, `LIS2DH12` ODR=0); likely includes J-Link/RTT debug domain overhead if probe was connected |
| **System OFF (`sleep_mode_sys_off`)** | Pending | — | Pending | Firmware verified on hardware (`RESET_LOW_POWER_WAKE`); standalone current pending |

### Preliminary Comparison (Derived from Reported Averages)

- Under the observed bench conditions, a ~5 min GNSS search (~15.08 mWh) consumes roughly **72×** the energy of a ~5 s LTE Cell-ID fix (~0.208 mWh).
- **Trade-off:** LTE Cell-ID saves ~72× acquisition energy and works indoors, but provides tower-level accuracy (~100 m – 3+ km) via server-side geolocation, whereas GNSS provides 2 – 10 m accuracy offline without a tower database.

## Next Controlled Runs Needed to Mark `Completed`

1. Flash `myprojects/lte_vs_gnss_compare` built with `release.conf` (`CONFIG_LOG=n`, `CONFIG_USE_SEGGER_RTT=n`, `CONFIG_COMPARE_STATUS_LED=n`).
2. Disconnect J-Link/USB and supply fixed 3.7 V battery/bench power through a µA-capable profiler (e.g., PPK2).
3. Record ≥ 5 matched Stage 1 (LTE) and Stage 2 (GNSS, outdoor clear sky) cycles plus standalone `STATE_SLEEP` and `sleep_mode_sys_off` floor currents; archive raw traces and `data/summary.csv`.
