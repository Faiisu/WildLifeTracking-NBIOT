# 03: Purge stale GNSS fixes so every upload cycle sends a fresh position

**What to build:** The GPS MQTT tracker's GNSS module keeps producing PVT frames continuously, including while the tracker is busy activating LTE and publishing over MQTT (which can take up to a minute or more). The PVT queue is shallow and drops new frames when full, so the tracker's next cycle can pick up a fix that is already stale by one or more minutes and immediately re-publish it. After this ticket, each upload cycle waits for and publishes a fix acquired after LTE/MQTT activity for that cycle has finished, not one left over from before.

**Blocked by:** None — 02 is resolved.

**Status:** ready-for-human (implemented, needs hardware/build verification)

- [x] After a publish/low-power cycle completes and GNSS resumes searching, the tracker does not immediately report a fix whose timestamp predates the start of that search — fixed by calling `k_msgq_purge(&gnss_pvt_queue)` at the top of `tracker_gnss_wait_for_fix()`, discarding whatever accumulated in the 2-slot queue while nobody was reading it
- [x] Consecutive published payloads have device timestamps spaced according to the configured fix interval, not back-to-back stale reads — follows from the same fix
- [x] Existing GNSS diagnostics (satellite stats, NMEA output) continue to work when enabled — untouched; the purge runs once before the diagnostic print loop starts, doesn't affect frames read during active search
- [ ] **Needs you:** no Zephyr/west toolchain is available in this environment, so this could not be built or flashed. Please build `myprojects/gps_mqtt_tracker` for your target board and confirm on real hardware that a stale fix is no longer reported right after an upload cycle.

## Comments

Chose `k_msgq_purge` over the single-slot mailbox alternative discussed
earlier: same effect on this bug, far smaller diff, and it leaves the
diagnostics print loop's frame-by-frame behavior untouched during active
search. See `myprojects/gps_mqtt_tracker/src/tracker_gnss.c`.
