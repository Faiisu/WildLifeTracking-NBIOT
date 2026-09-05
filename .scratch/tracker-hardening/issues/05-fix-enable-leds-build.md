# 05: Make the optional status-LED build option actually compile

**What to build:** Turning on `CONFIG_ENABLE_LEDS` in the GPS MQTT tracker is supposed to give visual LTE/GNSS/MQTT status indicators via the `dk_buttons_and_leds` library, but the build fails because `config/hardware.conf` disables `CONFIG_DK_LIBRARY` unconditionally. After this ticket, enabling the status-LED option produces a working build without the developer needing to hand-edit unrelated config files.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Building the tracker with `CONFIG_ENABLE_LEDS=y` succeeds out of the box
- [ ] Building the tracker with `CONFIG_ENABLE_LEDS=n` (default) is unaffected and still excludes the DK library
- [ ] The Kconfig dependency between `ENABLE_LEDS` and the DK library is explicit (`select`/`depends on`), not left to an external config file to get right
