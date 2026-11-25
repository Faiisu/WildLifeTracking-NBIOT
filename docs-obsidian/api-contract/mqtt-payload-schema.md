# Unified Tracker MQTT Payload Schema (v1)

This document defines the unified JSON payload standard shared by **LTE Cell-ID** and **GNSS** positioning reports so downstream MQTT subscribers, databases, and map frontends can parse all messages with a single schema.

## Top-Level Structure

| Field | Type | Description |
| :--- | :--- | :--- |
| `device_id` | `string` | Tracker MQTT client ID (e.g., `"WLTK01"` or `"nrf-<IMEI>"`). |
| `uptime_ms` | `integer` | Device monotonic uptime in milliseconds when the reading was captured. |
| `source` | `string` | Which positioning subsystems are populated: `"lte"`, `"gnss"`, or `"hybrid"`. |
| `gnss` | `object \| null` | GNSS PVT fix object, or `null` when only LTE data is reported. |
| `lte` | `object \| null` | LTE serving and neighbor cell object, or `null` when only GNSS data is reported. |

## `gnss` Object (or `null`)

| Field | Type | Units / Notes |
| :--- | :--- | :--- |
| `lat` | `number` | Latitude in decimal degrees (`%.6f`). |
| `lon` | `number` | Longitude in decimal degrees (`%.6f`). |
| `alt` | `number` | Altitude above sea level in meters (`%.1f`). |
| `acc` | `number` | Estimated horizontal accuracy radius in meters (`%.1f`). |
| `time` | `string` | UTC fix timestamp in ISO-8601 format (`YYYY-MM-DDTHH:MM:SSZ`). |

## `lte` Object (or `null`)

| Field | Type | Units / Notes |
| :--- | :--- | :--- |
| `mcc` | `integer` | Mobile Country Code (e.g., `520` for Thailand). |
| `mnc` | `integer` | Mobile Network Code (e.g., `3` for AIS). |
| `tac` | `integer \| null` | Tracking Area Code (`null` if unavailable). |
| `cid` | `integer` | Serving E-UTRAN Cell ID. |
| `earfcn` | `integer` | E-UTRA Absolute Radio Frequency Channel Number. |
| `pci` | `integer` | Physical Cell ID. |
| `rsrp` | `integer \| null` | Reference Signal Received Power in dBm (`null` if invalid). |
| `rsrq` | `integer \| null` | Reference Signal Received Quality index (`null` if invalid). |
| `ncells` | `array` | Neighbor cell list (`earfcn`, `pci`, `rsrp`, `rsrq`); empty `[]` if none. |

## Examples

### 1. LTE Only (`gnss` is `null`)

```json
{
  "device_id": "WLTK01",
  "uptime_ms": 12637,
  "source": "lte",
  "gnss": null,
  "lte": {
    "mcc": 520,
    "mnc": 3,
    "tac": 950,
    "cid": 159260272,
    "earfcn": 3600,
    "pci": 242,
    "rsrp": -80,
    "rsrq": 12,
    "ncells": [
      { "earfcn": 3600, "pci": 243, "rsrp": -92, "rsrq": 10 }
    ]
  }
}
```

### 2. GNSS Only (`lte` is `null`)

```json
{
  "device_id": "WLTK01",
  "uptime_ms": 48210,
  "source": "gnss",
  "gnss": {
    "lat": 13.756331,
    "lon": 100.501765,
    "alt": 12.5,
    "acc": 3.2,
    "time": "2026-11-25T13:30:00Z"
  },
  "lte": null
}
```

### 3. Hybrid (Both `gnss` and `lte` populated)

```json
{
  "device_id": "WLTK01",
  "uptime_ms": 51400,
  "source": "hybrid",
  "gnss": {
    "lat": 13.756331,
    "lon": 100.501765,
    "alt": 12.5,
    "acc": 3.2,
    "time": "2026-11-25T13:30:00Z"
  },
  "lte": {
    "mcc": 520,
    "mnc": 3,
    "tac": 950,
    "cid": 159260272,
    "earfcn": 3600,
    "pci": 242,
    "rsrp": -78,
    "rsrq": 11,
    "ncells": []
  }
}
```

## Backend Resolution Rule

1. If `gnss != null`, plot `gnss.lat`, `gnss.lon`, and `gnss.acc` directly.
2. Otherwise, if `lte != null`, resolve `lte` (`mcc`, `mnc`, `tac`, `cid`, `rsrp`, `ncells`) via a Cell Geolocation API (e.g., Google Geolocation, UnwiredLabs, OpenCelliD, or nRF Cloud) to obtain `lat`, `lon`, and `accuracy`.

## Implementations

- **Firmware (GNSS / LTE / Hybrid publishers):**
  - [`myprojects/gps_mqtt_tracker/src/tracker_mqtt.c`](../../myprojects/gps_mqtt_tracker/src/tracker_mqtt.c) (`source: "gnss"`, `lte: null`)
  - [`myprojects/gps_mqtt_stateControl/src/tracker_mqtt.c`](../../myprojects/gps_mqtt_stateControl/src/tracker_mqtt.c) (`source: "gnss"`, `lte: null`, or `gnss: null` when no fix)
  - [`myprojects/lte_mqtt_tracker/src/tracker_mqtt.c`](../../myprojects/lte_mqtt_tracker/src/tracker_mqtt.c) (`source: "lte"`, `gnss: null`)
  - [`myprojects/sleep_mode_psm/src/tracker_mqtt.c`](../../myprojects/sleep_mode_psm/src/tracker_mqtt.c) (`source: "lte"`, `gnss: null`)
  - [`myprojects/sleep_mode_sys_off/src/tracker_mqtt.c`](../../myprojects/sleep_mode_sys_off/src/tracker_mqtt.c) (`source: "lte"`, `gnss: null`)
  - [`myprojects/lte_vs_gnss_compare/src/tracker_mqtt.c`](../../myprojects/lte_vs_gnss_compare/src/tracker_mqtt.c) (`source: "lte"`, `"gnss"`, or `"hybrid"`)
- **Software (MQTT subscriber & map UI):**
  - [`myprojects/monitor-application/gps_monitoring/server.js`](../../myprojects/monitor-application/gps_monitoring/server.js) & [`cell-geolocation.js`](../../myprojects/monitor-application/gps_monitoring/cell-geolocation.js) (supports unified `gnss`/`lte`/`hybrid` + backward-compatible legacy flat payloads)
