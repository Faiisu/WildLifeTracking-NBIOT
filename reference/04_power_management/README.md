# 04 — Power Management Reference

Critical for battery-powered GPS trackers on NB-IoT.
The nRF9160 can go from ~6mA active to ~2.5μA in PSM sleep.

---

## Power modes overview

```
Active (LTE connected)    ~6–10 mA
eDRX (periodic wake)      ~1–3 mA average
PSM (deep sleep)          ~2.5 μA
Power off                 ~0.1 μA
```

---

## Samples in this folder

---

### `at_monitor/`
**Source:** `nrf/samples/cellular/at_monitor`

Monitor modem state changes (PSM active/inactive, signal quality) via AT notifications.

**Key concepts:**
- `AT_MONITOR()` macro — register handler for unsolicited AT responses
- Monitor `+CEREG` (network registration) and `+CESQ` (signal quality)
- Monitor `%XPSMR` (PSM mode entered/exited)
- Non-blocking — runs in interrupt context

**Pattern from `src/main.c`:**
```c
#include <modem/at_monitor.h>

// Register handler for PSM status notifications
AT_MONITOR(psm_monitor, "%XPSMR", psm_handler);

static void psm_handler(const char *response)
{
    // Called when modem enters or exits PSM sleep
    // response = "%XPSMR: 1"  → entered PSM
    // response = "%XPSMR: 0"  → exited PSM (wake up)
    LOG_INF("PSM status: %s", response);
}
```

**Apply to your project:**
Use to log exactly when the modem sleeps/wakes — helps verify PSM is actually working.

---

### `battery/`
**Source:** `nrf/samples/cellular/battery`

Monitor battery voltage and react to low-battery events.

**Key concepts:**
- `modem_battery_low_level_handler_set()` — callback when battery drops below threshold
- `modem_battery_voltage_get()` — read current voltage in mV
- Graceful shutdown before battery dies (save last GPS fix to flash)

**Pattern:**
```c
#include <modem/modem_battery.h>

static void battery_low_handler(int battery_voltage)
{
    LOG_WRN("Low battery: %d mV — saving last fix and sleeping", battery_voltage);
    // Save last GPS fix to non-volatile storage
    // Then enter ultra-low-power mode
}

int main(void) {
    modem_battery_low_level_handler_set(battery_low_handler);
    // ...
}
```

---

## PSM + eDRX configuration (add to your `prj.conf`)

```ini
# --- Power Saving Mode (PSM) ---
# Modem enters deep sleep (~2.5 μA) between transmissions
CONFIG_LTE_LC_PSM_MODULE=y
CONFIG_LTE_PSM_REQ=y
CONFIG_LTE_PSM_REQ_RPTAU="00101000"   # Wake-up interval: 8 hours (T3412)
CONFIG_LTE_PSM_REQ_RAT="00000011"     # Active time after wake: 6 seconds (T3324)

# --- eDRX (Extended Discontinuous Reception) ---
# Less aggressive than PSM — modem wakes periodically to check for downlink
CONFIG_LTE_LC_EDRX_MODULE=y
CONFIG_LTE_EDRX_REQ=y
CONFIG_LTE_EDRX_REQ_VALUE_LTE_M="0101"    # ~81 seconds eDRX cycle for LTE-M
CONFIG_LTE_EDRX_REQ_VALUE_NBIOT="1001"    # ~163 seconds eDRX cycle for NB-IoT
```

---

## PSM timer value encoding (T3412 / T3324)

The RPTAU and RAT values are 8-bit encoded strings. Common values:

### T3412 (RPTAU) — how long modem sleeps
| Value | Duration |
|-------|---------|
| `"00000001"` | 2 minutes |
| `"00100001"` | 1 hour |
| `"00101000"` | 8 hours ← default in your prj.conf |
| `"01000001"` | 24 hours |

### T3324 (RAT) — how long modem stays active after waking
| Value | Duration |
|-------|---------|
| `"00000001"` | 2 seconds |
| `"00000011"` | 6 seconds ← default in your prj.conf |
| `"00001010"` | 20 seconds |
| `"00111110"` | 62 seconds |

> **Tip:** For GPS tracking, set RAT long enough to get a GPS fix + send data, then let PSM sleep.
> A typical TTFF (time-to-first-fix) without assistance is 60–120 seconds cold start,
> 5–15 seconds warm start. Use `overlay-pgps.conf` to reduce this dramatically.

---

## RAI — Release Assistance Indication

Tell the network to release the RRC connection immediately after your last packet.
Reduces active time without waiting for the inactivity timer (~10 seconds saved).

```c
// Set socket option before the last send()
int rai_val = SO_RAI_LAST;
setsockopt(sock, SOL_SOCKET, SO_RAI, &rai_val, sizeof(rai_val));
sendto(sock, buf, len, 0, ...);   // network releases connection immediately after this
```

Enable in `prj.conf`:
```ini
CONFIG_LTE_RAI_MODULE=y
```
