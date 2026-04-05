# Reference Samples — GPS + NB-IoT Senior Project

Copied from NCS v3.2.3. Each folder contains samples from the SDK
and a `README.md` explaining what to learn and how to apply it.

---

## Folder Index

| Folder | Topic | Samples inside | Priority |
|--------|-------|---------------|----------|
| [`01_gnss/`](01_gnss/README.md) | GPS / Location API | `location` | High |
| [`02_data_transfer/`](02_data_transfer/README.md) | Send data over NB-IoT | `udp`, `https_client`, `nrf_cloud_mqtt`, `nrf_cloud_coap`, `nrf_cloud_multi_service` | High |
| [`03_threading/`](03_threading/README.md) | Zephyr threads & sync | `synchronization`, `philosophers` | Medium |
| [`04_power_management/`](04_power_management/README.md) | PSM / eDRX / battery | `at_monitor`, `battery` | Medium |
| [`05_interrupts/`](05_interrupts/README.md) | GPIO interrupts + ISR → work queue | `gpio_button`, `isr_workqueue_dispatch` | Medium |

---

## Recommended learning order

```
Step 1  →  03_threading/synchronization      Understand k_sem between threads
Step 2  →  03_threading/philosophers         Understand k_mutex for shared GPS data
Step 3  →  04_power_management (README.md)   Add PSM config to prj.conf
Step 4  →  05_interrupts/gpio_button        GPIO interrupt + k_work deferred pattern
Step 5  →  01_gnss/location                  Upgrade to Location API with fallback
Step 5  →  02_data_transfer/udp              Send GPS over UDP to test server
Step 6  →  02_data_transfer/nrf_cloud_coap   Send to nRF Cloud map dashboard
Step 7  →  02_data_transfer/nrf_cloud_multi_service   Full production reference
```

---

## Project structure (for context)

```
senior-project/
├── CMakeLists.txt                          ← build config
├── Kconfig                                 ← GNSS mode options
├── prj.conf                                ← main config (NB-IoT + GPS)
├── overlay-pgps.conf                       ← optional: Predicted GPS
├── boards/
│   └── sparkfun_thing_plus_nrf9160_ns.conf ← board overrides
├── src/
│   ├── main.c                              ← your main application
│   ├── assistance.c / .h                   ← nRF Cloud A-GNSS
│   ├── assistance_minimal.c                ← factory almanac assistance
│   ├── factory_almanac_v2.h                ← pre-loaded almanac data
│   └── mcc_location_table.c / .h           ← cell-based rough location
└── reference/                              ← YOU ARE HERE
    ├── README.md                           ← this file
    ├── 01_gnss/
    ├── 02_data_transfer/
    ├── 03_threading/
    └── 04_power_management/
```

---

## Build command reminder

```bash
# From your senior-project root:
west build -b sparkfun_thing_plus_nrf9160_ns

# With Predicted GPS overlay:
west build -b sparkfun_thing_plus_nrf9160_ns -- \
  -DCONF_FILE="prj.conf overlay-pgps.conf"

# Flash:
west flash

# Monitor serial output (115200 baud):
west espressif monitor   # or use nRF Connect for Desktop → LTE Link Monitor
```
