# GPS + NB-IoT Tracker — Senior Project

SparkFun Thing Plus nRF9160 — NCS v3.2.3

---

## What this project does

Reads GPS coordinates from the nRF9160's built-in GNSS receiver and outputs them over serial.
The modem runs in NB-IoT + GPS mode with PSM power saving enabled.
The codebase is ready to extend with data transmission (UDP / CoAP / MQTT) once GPS is working.

---

## Hardware

| Item | Detail |
|------|--------|
| Board | SparkFun Thing Plus nRF9160 |
| Board target (west) | `sparkfun_thing_plus_nrf9160_ns` |
| SiP | nRF9160 (ARM Cortex-M33 + LTE-M/NB-IoT modem + GPS) |
| SDK | nRF Connect SDK (NCS) v3.2.3 |
| Toolchain | NCS Toolchain v3.2.3 |

> The `_ns` suffix is required — it enables TrustZone non-secure mode needed by the modem library.

---

## Project structure

```
senior-project/
├── CMakeLists.txt                            build config
├── Kconfig                                   GNSS mode / assistance options
├── prj.conf                                  main config (NB-IoT + GPS + PSM)
├── overlay-pgps.conf                         optional: Predicted GPS overlay
├── boards/
│   └── sparkfun_thing_plus_nrf9160_ns.conf   board-specific overrides (auto-applied)
├── src/
│   ├── main.c                                application entry + GNSS event loop
│   ├── assistance.c / .h                     nRF Cloud A-GNSS handler
│   ├── assistance_minimal.c                  factory almanac + LTE time injection
│   ├── factory_almanac_v2.h                  pre-loaded almanac data for nRF9160
│   └── mcc_location_table.c / .h            rough location from cell network code
├── reference/                                learning resources (see reference/README.md)
│   ├── 01_gnss/
│   ├── 02_data_transfer/
│   ├── 03_threading/
│   ├── 04_power_management/
│   └── 05_interrupts/
└── README.md                                 this file
```

---

## Quick start

### 1. Build

```bash
# From this directory (senior-project/):
west build -b sparkfun_thing_plus_nrf9160_ns

# With Predicted GPS for faster first fix:
west build -b sparkfun_thing_plus_nrf9160_ns -- \
  -DCONF_FILE="prj.conf overlay-pgps.conf"
```

### 2. Flash

```bash
west flash
```

### 3. Monitor serial output

```bash
# 115200 baud, 8N1
# Option A — nRF Connect for Desktop → LTE Link Monitor
# Option B — any serial terminal (e.g. PuTTY, minicom, screen)
screen /dev/ttyUSBx 115200
```

Expected output once running:
```
Acquiring satellites...
Tracking:  3 Using:  0  Unhealthy:  0
Latitude:       13.756331
Longitude:     100.501765
Altitude:       10.8 m
Speed:           0.2 m/s
Heading:         0.0 deg
Date:          2026-04-05
Time (UTC):    08:12:44.000
```

---

## Configuration guide (`prj.conf`)

### GNSS assistance mode — choose one

| Option | `prj.conf` key | Cold start TTFF | Requirements |
|--------|----------------|-----------------|--------------|
| No assistance (default) | `CONFIG_GNSS_SAMPLE_ASSISTANCE_NONE=y` | 60–120 s | None |
| Factory almanac | `CONFIG_GNSS_SAMPLE_ASSISTANCE_MINIMAL=y` | 15–45 s | None |
| nRF Cloud A-GNSS | `CONFIG_GNSS_SAMPLE_ASSISTANCE_NRF_CLOUD=y` | 5–15 s | nRF Cloud account + SIM |
| Predicted GPS | add `overlay-pgps.conf` | < 10 s | nRF Cloud account + SIM |

### GNSS operation mode — choose one

| Mode | `prj.conf` key | Description |
|------|----------------|-------------|
| Continuous (default) | `CONFIG_GNSS_SAMPLE_MODE_CONTINUOUS=y` | Always tracking |
| Periodic | `CONFIG_GNSS_SAMPLE_MODE_PERIODIC=y` | Fix every N seconds |
| TTFF test | `CONFIG_GNSS_SAMPLE_MODE_TTFF_TEST=y` | Measure time-to-first-fix |

Periodic interval (seconds, used when periodic mode is on):
```ini
CONFIG_GNSS_SAMPLE_PERIODIC_INTERVAL=120
CONFIG_GNSS_SAMPLE_PERIODIC_TIMEOUT=120
```

### Network mode

```ini
CONFIG_LTE_NETWORK_MODE_NBIOT_GPS=y      # NB-IoT + GPS (default)
# CONFIG_LTE_NETWORK_MODE_LTE_M_GPS=y   # LTE-M + GPS (faster, higher power)
```

### PSM timers

```ini
CONFIG_LTE_PSM_REQ_RPTAU="00101000"   # Sleep interval: 8 hours
CONFIG_LTE_PSM_REQ_RAT="00000011"     # Active time after wake: 6 seconds
```

See [`reference/04_power_management/README.md`](reference/04_power_management/README.md)
for the full timer encoding table.

---

## How main.c works

### Thread and data flow

```
nRF9160 modem (GNSS hardware)
        │
        │  NRF_MODEM_GNSS_EVT_PVT  →  k_sem_give(&pvt_data_sem)
        │  NRF_MODEM_GNSS_EVT_NMEA →  k_msgq_put(&nmea_queue, ...)
        │  NRF_MODEM_GNSS_EVT_AGNSS_REQ → k_work_submit() → fetch assistance
        ▼
  gnss_event_handler()    ← ISR/callback — short, fast, never blocks
        │
        ▼
  main() — k_poll() loop  ← wakes when pvt_data_sem or nmea_queue has data
        │
        ├─ pvt_data_sem signaled  →  read last_pvt, print lat/lon/alt/speed
        └─ nmea_queue has data    →  print raw NMEA string, free memory
```

### Key Zephyr primitives used

| Primitive | Variable | Purpose |
|-----------|----------|---------|
| `K_SEM_DEFINE` | `pvt_data_sem` | Signal main loop when PVT data is ready |
| `K_SEM_DEFINE` | `time_sem` | Signal when LTE time sync is complete |
| `K_MSGQ_DEFINE` | `nmea_queue` | Pass NMEA string pointers from ISR to main |
| `k_work` | `agnss_data_get_work` | Defer A-GNSS fetch to thread context |
| `k_poll` | `events[2]` | Wait on both semaphore and queue at once |

### PVT data structure

The GNSS fix data is in `last_pvt` (`struct nrf_modem_gnss_pvt_data_frame`):

```c
last_pvt.latitude      // double  — decimal degrees
last_pvt.longitude     // double  — decimal degrees
last_pvt.altitude      // float   — meters above ellipsoid
last_pvt.speed         // float   — m/s
last_pvt.heading       // float   — degrees
last_pvt.sv_count      // number of satellites used
last_pvt.flags         // NRF_MODEM_GNSS_PVT_FLAG_FIX_VALID — check this first
```

---

## Extending the project

### Add data transmission (UDP — simplest)

```c
// After getting a fix in main loop, open a UDP socket:
int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);

char buf[64];
snprintf(buf, sizeof(buf),
    "{\"lat\":%.6f,\"lon\":%.6f}",
    last_pvt.latitude, last_pvt.longitude);

sendto(sock, buf, strlen(buf), 0,
    (struct sockaddr *)&server_addr, sizeof(server_addr));

close(sock);
```

Add to `prj.conf`:
```ini
CONFIG_NETWORKING=y
CONFIG_NET_SOCKETS=y
CONFIG_NET_SOCKETS_OFFLOAD=y
CONFIG_NET_NATIVE=n
```

See [`reference/02_data_transfer/README.md`](reference/02_data_transfer/README.md)
for UDP, HTTPS, MQTT, and CoAP patterns.

### Add a button to trigger a fix on demand

```c
#include <zephyr/drivers/gpio.h>

static const struct gpio_dt_spec btn = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
static struct gpio_callback btn_cb;
static struct k_work fix_work;

static void fix_handler(struct k_work *w)
{
    nrf_modem_gnss_start();   // or location_request() if using Location API
}

static void btn_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    k_work_submit(&fix_work);   // never do real work inside ISR
}

// In main():
k_work_init(&fix_work, fix_handler);
gpio_pin_configure_dt(&btn, GPIO_INPUT);
gpio_pin_interrupt_configure_dt(&btn, GPIO_INT_EDGE_TO_ACTIVE);
gpio_init_callback(&btn_cb, btn_isr, BIT(btn.pin));
gpio_add_callback(btn.port, &btn_cb);
```

See [`reference/05_interrupts/README.md`](reference/05_interrupts/README.md) for full pattern.

---

## Assistance files explained

| File | Purpose | Active when |
|------|---------|-------------|
| `assistance.c/.h` | Fetches A-GNSS ephemeris data from nRF Cloud | `CONFIG_GNSS_SAMPLE_ASSISTANCE_NRF_CLOUD=y` |
| `assistance_minimal.c` | Injects factory almanac + LTE time + MCC position | `CONFIG_GNSS_SAMPLE_ASSISTANCE_MINIMAL=y` |
| `factory_almanac_v2.h` | Pre-compiled almanac binary for nRF9160 | (included by assistance_minimal.c) |
| `mcc_location_table.c/.h` | Maps mobile country code → rough GPS coordinates | (included by assistance_minimal.c) |

---

## Reference materials

See [`reference/README.md`](reference/README.md) for the full index.

| Topic | Folder |
|-------|--------|
| GPS / Location API | [`reference/01_gnss/`](reference/01_gnss/README.md) |
| Data transfer (UDP, HTTPS, MQTT, CoAP) | [`reference/02_data_transfer/`](reference/02_data_transfer/README.md) |
| Zephyr threads & synchronization | [`reference/03_threading/`](reference/03_threading/README.md) |
| PSM / eDRX / battery | [`reference/04_power_management/`](reference/04_power_management/README.md) |
| GPIO interrupts + ISR → work queue | [`reference/05_interrupts/`](reference/05_interrupts/README.md) |

---

## Common issues

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Build error: `CONFIG_REGULATOR` not set | Board file not applied | Confirm target is `sparkfun_thing_plus_nrf9160_ns` (with `_ns`) |
| No GNSS output | Modem not initialized | Check LTE Link Monitor for `+CEREG` registration |
| `pvt_data_sem` never fires | Antenna not connected or indoors | Go outside with clear sky view |
| `k_malloc` fails for NMEA | Heap too small | Increase `CONFIG_HEAP_MEM_POOL_SIZE` in `prj.conf` |
| Float values print as `%f` | picolibc float disabled | Ensure `CONFIG_PICOLIBC_IO_FLOAT=y` in `prj.conf` |
