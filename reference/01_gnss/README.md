# 01 — GNSS / GPS Reference

Samples that directly improve GPS acquisition, accuracy, and reliability.

---

## Samples in this folder

### `location/`
**Source:** `nrf/samples/cellular/location`

High-level Location API that wraps raw GNSS with automatic fallback logic.

| Feature | Detail |
|---------|--------|
| Methods | GNSS → Cellular → Wi-Fi (in priority order) |
| Fallback | If GNSS times out, falls back to cellular location automatically |
| Assistance | Supports P-GPS overlay (`overlay-pgps.conf`) |
| API | `location_request()` instead of raw `nrf_modem_gnss_*` calls |

**Key files:**
- `src/main.c` — calls `location_init()` + `location_request()`, handles `LOCATION_EVT_LOCATION` event
- `prj.conf` — enables `CONFIG_LOCATION`, `CONFIG_LTE_LINK_CONTROL`
- `overlay-pgps.conf` — add to build for Predicted GPS assistance

**How to apply to your project:**

Replace raw `nrf_modem_gnss_*` calls in your `main.c` with the Location library:

```c
#include <modem/location.h>

// 1. Init
location_init(location_event_handler);

// 2. Request a fix
struct location_config config;
location_config_defaults_set(&config, 1, methods);
location_request(&config);

// 3. Handle result in callback
void location_event_handler(const struct location_event_data *event_data) {
    if (event_data->id == LOCATION_EVT_LOCATION) {
        double lat = event_data->location.latitude;
        double lon = event_data->location.longitude;
    }
}
```

**Build command:**
```bash
west build -b sparkfun_thing_plus_nrf9160_ns -- \
  -DCONF_FILE="prj.conf overlay-pgps.conf"
```

---

## Relevant NCS libraries (no copy needed — in SDK)

| Library | Path in NCS | Purpose |
|---------|-------------|---------|
| `lib/location` | `nrf/lib/location/` | Location API implementation |
| `nrf_modem_gnss.h` | `nrfxlib/nrf_modem/include/` | Raw GNSS modem API |
| `lte_link_control` | `nrf/lib/lte_link_control/` | LTE connection for A-GNSS download |
