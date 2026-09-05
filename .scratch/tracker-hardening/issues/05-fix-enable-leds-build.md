# 05: Make the optional status-LED build option actually compile

**What to build:** Turning on `CONFIG_ENABLE_LEDS` in the GPS MQTT tracker is supposed to give visual LTE/GNSS/MQTT status indicators via the `dk_buttons_and_leds` library, but the build fails because `config/hardware.conf` disables `CONFIG_DK_LIBRARY` unconditionally. After this ticket, enabling the status-LED option produces a working build without the developer needing to hand-edit unrelated config files.

**Blocked by:** None — 02 is resolved.

**Status:** ready-for-human (implemented and build-verified both ways)

- [x] Building the tracker with `CONFIG_ENABLE_LEDS=y` succeeds out of the box — verified via `-DEXTRA_CONF_FILE` overlay, 322/322, zero errors, `CONFIG_DK_LIBRARY=y` pulled in automatically, FLASH 16.94% (+508 B vs. off)
- [x] Building the tracker with `CONFIG_ENABLE_LEDS=n` (default) is unaffected and still excludes the DK library — verified `# CONFIG_DK_LIBRARY is not set` in the resulting `.config`, FLASH/RAM identical to before this change (16.86%/28.36%)
- [x] The Kconfig dependency is now explicit: added `select DK_LIBRARY` to `config ENABLE_LEDS` in `Kconfig`, and removed the unconditional `CONFIG_DK_LIBRARY=n` from `config/hardware.conf` (it was silently fighting the selection for anyone who turned LEDs on)
- [ ] **Unverified on hardware:** only the build was checked (both configs compile and link correctly, correct symbols end up in `.config`). Whether `dk_leds_init()`/`dk_set_led_on()` actually drive the physical LEDs correctly on a Circuit Dojo/SparkFun board with `CONFIG_ENABLE_LEDS=y` has not been tested — needs a flash + visual check on real hardware.

## Comments

`DK_LIBRARY` itself has no `default` in its own Kconfig (nrf/lib/dk_buttons_and_leds/Kconfig),
so it's `n` unless something selects or sets it — safe to just delete the
hardcoded `=n` line rather than make it conditional (`.conf` fragments can't
express conditions anyway). No conflict/override warnings appeared in either
build. Build-verified against NCS v3.3.0, sparkfun_thing_plus/nrf9160/ns.
