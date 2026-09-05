# 07: Fall back to low-power mode when no GNSS fix is found

**What to build:** When GNSS search times out without ever producing a valid fix, the tracker's main loop currently has no path out of waiting: it blocks forever on the PVT queue instead of transitioning to a GNSS-safe low-power state and retrying later. After this ticket, a failed search cycle ends the same way a successful one does after upload — by returning control to a bounded wait, then re-entering low power and retrying on schedule — so the device never gets stuck awake.

**Blocked by:** 03 (touches the same fix-waiting logic)

**Status:** ready-for-human (implemented and build-verified; runtime recovery on real hardware is unverified)

- [x] If GNSS fails to produce a valid fix within the configured search timeout, the tracker enters its low-power state instead of blocking indefinitely — `tracker_gnss_wait_for_fix()` now bounds the *overall* search (via `sys_timepoint_calc`/`sys_timepoint_timeout`, re-derived each loop iteration) to `CONFIG_TRACKER_PERIODIC_TIMEOUT` seconds and returns `-ETIMEDOUT` on expiry; `main.c`'s existing dispatch (`state = (err == 0) ? CONNECT_LTE : ENTER_LOW_POWER`) already routed this correctly — it just never fired before, since the callee never timed out
- [x] After the low-power interval elapses, the tracker resumes searching for a fix on the normal schedule — falls out of the existing `ENTER_LOW_POWER -> WAIT_FOR_GNSS` loop in `main.c`, unchanged
- [x] `CONFIG_TRACKER_PERIODIC_TIMEOUT=0` ("infinite search", per its own Kconfig help text) is preserved: the new code special-cases 0 to `K_FOREVER` rather than passing `K_SECONDS(0)` (which would mean "don't wait at all", the opposite of infinite)
- [x] Build-verified: clean build against NCS v3.3.0 (sparkfun_thing_plus/nrf9160/ns), 320/320, zero errors, zero new warnings (FLASH 16.87%, +84 B for the timeout logic); also compiled successfully with a reduced `CONFIG_TRACKER_PERIODIC_TIMEOUT=5` overlay to confirm the config plumbing
- [ ] **Unverified on hardware:** actually observing the fallback fire and recover (GNSS search times out -> LOW_POWER -> resumes searching) needs a real board with no sky view, or a modem simulator -- neither is available in this environment. Static review of the logic (deadline computed once, re-derived per iteration so a steady trickle of non-fix PVT frames can't reset it) is as far as this could be verified here.

## Comments

Chose to reuse the existing `CONFIG_TRACKER_PERIODIC_TIMEOUT` (already used
to bound the modem's own internal per-attempt search via
`nrf_modem_gnss_fix_retry_set()`) rather than adding a new Kconfig symbol,
since the semantics ("how long to search before giving up") are identical at
both layers and a second knob would just invite them drifting apart.

Used `sys_timepoint_calc()`/`sys_timepoint_timeout()` instead of a naive
per-iteration `K_SECONDS(CONFIG_TRACKER_PERIODIC_TIMEOUT)` on each
`k_msgq_get()` call: the naive version would never actually fire, since a
steady trickle of non-fix PVT frames arriving during an active search resets
each individual wait before it expires. The timepoint pattern bounds the
*overall* search instead.
