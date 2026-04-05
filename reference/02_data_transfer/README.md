# 02 — Data Transfer Protocols Reference

Samples for sending GPS coordinates and sensor data over NB-IoT.

---

## Protocol Comparison

| Protocol | Sample folder | Power cost | Complexity | Best use case |
|----------|--------------|-----------|------------|---------------|
| UDP | `udp/` | Lowest | Low | Raw GPS packets to custom server |
| HTTPS | `https_client/` | Medium | Medium | REST API to any web server |
| MQTT (nRF Cloud) | `nrf_cloud_mqtt/` | Medium | Medium | Real-time GPS dashboard on nRF Cloud |
| CoAP (nRF Cloud) | `nrf_cloud_coap/` | Low | Medium | Power-efficient nRF Cloud connection |
| Multi-service | `nrf_cloud_multi_service/` | Medium | High | Production-ready: GPS + alerts + FOTA |

---

## Samples in this folder

---

### `udp/`
**Source:** `nrf/samples/cellular/udp`

Lightest possible data transfer — raw UDP socket, no handshake, no TLS.

**Key features:**
- PSM + eDRX + RAI power saving built in
- Configurable packet size and send interval
- Best for: sending GPS coordinates to your own server (e.g., Node.js, Python)

**Core pattern from `src/main.c`:**
```c
// Create UDP socket
int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);

// Send GPS data as raw bytes or JSON string
char buf[64];
snprintf(buf, sizeof(buf), "{\"lat\":%.6f,\"lon\":%.6f}", lat, lon);
sendto(sock, buf, strlen(buf), 0, (struct sockaddr *)&addr, sizeof(addr));
```

**Key config in `prj.conf`:**
```
CONFIG_UDP_DATA_UPLOAD_SIZE_BYTES=100
CONFIG_UDP_DATA_UPLOAD_FREQUENCY_SECONDS=60
CONFIG_LTE_LC_PSM_MODULE=y
CONFIG_LTE_PSM_REQ=y
```

---

### `https_client/`
**Source:** `nrf/samples/net/https_client`

TLS-secured HTTP request — send GPS data to any REST API endpoint.

**Key features:**
- TLS 1.2 with certificate stored in modem security keys
- HEAD/GET/POST request support
- Best for: sending GPS to a web backend (Firebase, custom API, etc.)

**Core pattern:**
```c
// After LTE connect + TLS setup:
char request[256];
snprintf(request, sizeof(request),
    "POST /gps HTTP/1.1\r\n"
    "Host: yourserver.com\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: %d\r\n\r\n"
    "{\"lat\":%.6f,\"lon\":%.6f}",
    body_len, lat, lon);
send(sock, request, strlen(request), 0);
```

---

### `nrf_cloud_mqtt/`
**Source:** `nrf/samples/cellular/nrf_cloud_mqtt_cell_location`

Send location data to Nordic's nRF Cloud via MQTT — view on map in browser.

**Key features:**
- Persistent MQTT connection (real-time updates)
- nRF Cloud map dashboard shows device position
- Requires: nRF Cloud account + SIM with data plan

**Key config:**
```
CONFIG_NRF_CLOUD_MQTT=y
CONFIG_NRF_CLOUD_LOCATION=y
CONFIG_MQTT_KEEPALIVE=1200
```

**How it works:**
```
nRF9160 ──MQTT──> nRF Cloud broker ──> nRF Cloud dashboard (map)
```

---

### `nrf_cloud_coap/`
**Source:** `nrf/samples/cellular/nrf_cloud_coap_cell_location`

Same as MQTT version but uses CoAP — more power efficient (no persistent connection).

**Key features:**
- DTLS Connection ID (CID) reduces TLS handshake overhead
- Better for battery-powered devices than MQTT
- Uses `nrf_cloud_coap_location_send()` API

**Key config:**
```
CONFIG_NRF_CLOUD_COAP=y
CONFIG_NRF_CLOUD_LOCATION=y
```

---

### `nrf_cloud_multi_service/`
**Source:** `nrf/samples/cellular/nrf_cloud_multi_service`

All-in-one production reference: GPS location + custom messages + FOTA updates.

**Key features:**
- GNSS + cellular location with automatic fallback
- Periodic sensor data upload
- Alert messages (e.g., low battery warning)
- FOTA firmware update support
- Connection manager (`conn_mgr`) handles reconnect automatically

**This is the closest to a complete product — study this last.**

**Directory structure:**
```
src/
├── main.c               — application entry, event loop
├── cloud_connection.c   — MQTT connect/disconnect/reconnect
├── location_tracking.c  — GPS + fallback location logic
├── fota_support.c       — firmware update handling
└── led_control.c        — status LED patterns
```

---

## Recommended protocol for your senior project

```
Simple test server  →  UDP        (fastest to implement)
Web backend         →  HTTPS      (works with any server)
nRF Cloud dashboard →  CoAP       (most power-efficient with cloud map)
Full product        →  nrf_cloud_multi_service
```
