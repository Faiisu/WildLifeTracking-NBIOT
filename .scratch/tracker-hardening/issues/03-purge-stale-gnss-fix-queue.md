# 03: Purge stale GNSS fixes so every upload cycle sends a fresh position

**What to build:** The GPS MQTT tracker's GNSS module keeps producing PVT frames continuously, including while the tracker is busy activating LTE and publishing over MQTT (which can take up to a minute or more). The PVT queue is shallow and drops new frames when full, so the tracker's next cycle can pick up a fix that is already stale by one or more minutes and immediately re-publish it. After this ticket, each upload cycle waits for and publishes a fix acquired after LTE/MQTT activity for that cycle has finished, not one left over from before.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] After a publish/low-power cycle completes and GNSS resumes searching, the tracker does not immediately report a fix whose timestamp predates the start of that search
- [ ] Consecutive published payloads have device timestamps spaced according to the configured fix interval, not back-to-back stale reads
- [ ] Existing GNSS diagnostics (satellite stats, NMEA output) continue to work when enabled
