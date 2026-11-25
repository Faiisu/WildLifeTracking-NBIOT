---
title: API Contract
tags: [api-contract, mqtt, json, rest, sse, cell-geolocation]
category: api-contract
updated: 2026-11-25
---

# API Contract

Interface contracts between the **nRF9160 tracker firmware**, the **MQTT broker**, the **[[firmware-and-software/gps-monitoring|gps_monitoring]] Node.js backend**, and external **Cell Geolocation APIs**.

## 1. MQTT Transport & Payload Contract

- **Full schema & examples:** [[api-contract/mqtt-payload-schema|Unified MQTT JSON Payload Schema (v1)]]
- **Protocol:** MQTT 3.1.1 over TLS 1.2 (`CONFIG_MQTT_LIB_TLS=y`, port `8883`, modem `sec_tag = 24`)
- **Authentication:** Username + password (git-ignored `config/mqtt-credentials.conf`) + server root CA (`credentials/ca-cert.pem`)
- **Client ID:** `CONFIG_MQTT_CLIENT_ID` (`"WLTK01"` default, or `"nrf-<IMEI>"` via `AT+CGSN` when empty)
- **Publish Topic:** `test/nrf91` (QoS `1` — backlog evicted only after `PUBACK`)

## 2. Monitor Backend HTTP & SSE API (`gps_monitoring/server.js`)

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `GET` | `/api/status` | Current MQTT state, redacted broker config, provider status, latest telemetry summary. |
| `GET` | `/api/locations` | Stored location history from SQLite (`deviceId`, `source`, `lat`, `lon`, `alt`, `acc`, `time`, cell metadata). |
| `PUT` | `/api/config` | Saves MQTT, PostgreSQL, and Cell Geolocation config to `data/mqtt-config.json`. |
| `POST` | `/api/test/mqtt` | Tests MQTT connectivity without replacing active session. |
| `POST` | `/api/test/postgres` | Tests optional PostgreSQL connection. |
| `POST` | `/api/test/cell-geo` | Tests configured Cell Geolocation provider (`cell-geolocation.js`). |
| `POST` | `/api/connect` | Connects live MQTT subscriber. |
| `POST` | `/api/disconnect` | Disconnects live MQTT subscriber. |
| `POST` | `/api/locations/clear` | Clears stored SQLite location history. |
| `GET` | `/api/events` | Server-Sent Events (SSE) stream pushing live fixes and status updates to Leaflet UI. |

## 3. Cell Geolocation Resolution Contract

- **`gnss != null`** (`source: "gnss"` or `"hybrid"`) → plot `gnss` directly (**0** Cell Geolocation API calls).
- **`gnss == null && lte != null`** (`source: "lte"`) → resolve `lte` (`mcc`, `mnc`, `tac`, `cid`, `rsrp`, `ncells`) via `cell-geolocation.js`.

---
Back to [[README|Documentation Hub]]
