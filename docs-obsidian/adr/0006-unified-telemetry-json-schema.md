# ADR 0006: Unified Telemetry JSON Schema for LTE and GNSS

## Status
Accepted (Implemented)

## Context
The project evaluates and combines two positioning methods on the nRF9160: **GNSS** (direct `lat`/`lon`, higher energy per cold fix) and **LTE Cell-ID** (tower metadata resolved server-side, much lower acquisition energy). Previously, `gps_mqtt_tracker` published flat GNSS objects (`{"lat":...}`) while `lte_mqtt_tracker` published flat cell objects (`{"mcc":...}`), requiring separate schema assumptions across firmware apps and the `gps_monitoring` backend.

## Decision
Adopt a single unified JSON envelope ([API Contract — MQTT Payload Schema](../api-contract/mqtt-payload-schema.md)) across all location-publishing firmware and monitor software:

```json
{"device_id":"WLTK01","uptime_ms":12637,"source":"lte|gnss|hybrid","gnss":{...}|null,"lte":{...}|null}
```

- When only LTE cell data is available, `gnss` is explicitly `null` (`"source": "lte"`).
- When only GNSS data is available, `lte` is explicitly `null` (`"source": "gnss"`).
- When both are present, both objects are populated (`"source": "hybrid"`).
- `gps_monitoring` uses `gnss` directly whenever `gnss != null` (zero Cell Geolocation API calls), falls back to Cell Geolocation when `gnss == null && lte != null`, and retains backward compatibility with legacy flat payloads.

## Consequences
- **Single contract:** All 6 firmware apps (`gps_mqtt_tracker`, `gps_mqtt_stateControl`, `lte_mqtt_tracker`, `sleep_mode_psm`, `sleep_mode_sys_off`, `lte_vs_gnss_compare`) and `gps_monitoring` speak one consistent JSON format.
- **Small payload overhead:** Adds ~55 bytes for top-level metadata and the `null` field; well within the 1024-byte `snprintf` buffer.
- **Hybrid-ready:** Future hybrid firmware can populate both `gnss` and `lte` without breaking the backend or database schema.

## References
- [Unified MQTT JSON Payload Schema](../api-contract/mqtt-payload-schema.md)
- [Experiment 0006: LTE Cell-ID vs GNSS & Sleep Power](../experiments/0006-lte-vs-gnss-and-sleep-power/README.md)
