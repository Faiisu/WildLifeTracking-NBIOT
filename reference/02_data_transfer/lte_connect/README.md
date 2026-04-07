# lte_connect — LTE-M / NB-IoT Connect Reference

**Source:** `nrf/samples/cellular/pdn`

Shows how to connect to the LTE network (LTE-M or NB-IoT) using the `lte_lc` library,
and how to create, configure, and activate PDP (Packet Data Protocol) contexts.

---

## What to learn from this sample

- How to initialize the modem with `nrf_modem_lib_init()`
- Two connect styles: **blocking** (`lte_lc_connect`) and **async** (`lte_lc_connect_async`)
- How to register an LTE event handler for network registration, PSM, eDRX, PDN events
- How to select **LTE-M vs NB-IoT** mode via Kconfig or runtime API
- How to create a secondary PDP context (separate APN)

---

## Minimal connect pattern (blocking)

```c
#include <modem/lte_lc.h>
#include <modem/nrf_modem_lib.h>

void lte_handler(const struct lte_lc_evt *const evt)
{
    /* Handle LTE_LC_EVT_NW_REG_STATUS, LTE_LC_EVT_PSM_UPDATE, etc. */
}

int main(void)
{
    nrf_modem_lib_init();

    /* Register handler BEFORE lte_lc_connect() to catch PDN activation */
    lte_lc_register_handler(lte_handler);

    lte_lc_connect();   /* blocks until registered on network */

    /* Now connected — open sockets, send data */

    lte_lc_power_off();
    return 0;
}
```

## Async connect pattern (non-blocking, from udp sample)

```c
K_SEM_DEFINE(lte_connected, 0, 1);

static void lte_handler(const struct lte_lc_evt *const evt)
{
    if (evt->type == LTE_LC_EVT_NW_REG_STATUS &&
        (evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_HOME ||
         evt->nw_reg_status == LTE_LC_NW_REG_REGISTERED_ROAMING)) {
        k_sem_give(&lte_connected);
    }
}

int main(void)
{
    nrf_modem_lib_init();
    lte_lc_connect_async(lte_handler);  /* returns immediately */
    k_sem_take(&lte_connected, K_FOREVER);
    /* connected */
}
```

---

## Selecting LTE-M vs NB-IoT (prj.conf)

```
# LTE-M only (default for nRF9160/nRF9161)
CONFIG_LTE_NETWORK_MODE_LTE_M=y

# NB-IoT only
CONFIG_LTE_NETWORK_MODE_NBIOT=y

# NB-IoT + GNSS  ← recommended for this project
CONFIG_LTE_NETWORK_MODE_NBIOT_GPS=y

# LTE-M + GNSS
CONFIG_LTE_NETWORK_MODE_LTE_M_GPS=y

# Dual-mode (modem picks best available)
CONFIG_LTE_NETWORK_MODE_LTE_M_NBIOT=y
```

## Selecting mode at runtime

```c
#include <modem/lte_lc.h>

/* Call before lte_lc_connect() */
lte_lc_system_mode_set(LTE_LC_SYSTEM_MODE_NBIOT_GPS,
                       LTE_LC_SYSTEM_MODE_PREFER_NBIOT);
```

---

## PDN context (this sample's main feature)

A secondary PDP context lets you connect to a different APN (e.g., private IoT APN):

```c
uint8_t cid;

lte_lc_pdn_ctx_create(&cid);                                  /* allocate context */
lte_lc_pdn_ctx_configure(cid, "iot.apn", LTE_LC_PDN_FAM_IPV4V6, NULL);
lte_lc_pdn_activate(cid, &esm, NULL);                         /* activate */

/* Use lte_lc_pdn_id_get(cid) to bind a socket to this PDN */
```

For most projects (single APN), the **default PDP context (cid=0)** is activated
automatically by `lte_lc_connect()` — no extra PDN code needed.

---

## Key events in lte_handler

| Event | Meaning |
|-------|---------|
| `LTE_LC_EVT_NW_REG_STATUS` | Registered / lost connection |
| `LTE_LC_EVT_PSM_UPDATE` | Network granted PSM timers (TAU / Active time) |
| `LTE_LC_EVT_EDRX_UPDATE` | Network granted eDRX window |
| `LTE_LC_EVT_RRC_UPDATE` | Radio switched between Connected / Idle |
| `LTE_LC_EVT_CELL_UPDATE` | Moved to a different cell tower |
| `LTE_LC_EVT_PDN` | PDP context activated / deactivated |

---

## Disconnect / shutdown sequence

```c
lte_lc_offline();        /* CFUN=4 — flight mode, modem stays powered */
lte_lc_power_off();      /* CFUN=0 — full modem power off */
nrf_modem_lib_shutdown();
```

---

## Required Kconfig (prj.conf)

```
CONFIG_NRF_MODEM_LIB=y
CONFIG_LTE_LINK_CONTROL=y
CONFIG_NETWORKING=y
CONFIG_NET_NATIVE=n
CONFIG_NET_SOCKETS=y
CONFIG_NET_SOCKETS_OFFLOAD=y
```

For PDN management (secondary contexts):
```
CONFIG_LTE_LC_PDN_MODULE=y
CONFIG_LTE_LC_PDN_ESM_STRERROR=y   /* human-readable PDN error strings */
```

---

## Relation to other reference samples

- **This sample** — how to get connected; foundation for everything below
- [`../udp/`](../udp/) — sends data after connecting (uses `lte_lc_connect_async`)
- [`../https_client/`](../https_client/) — TLS socket after connecting
- [`../nrf_cloud_coap/`](../nrf_cloud_coap/) — CoAP to nRF Cloud after connecting
