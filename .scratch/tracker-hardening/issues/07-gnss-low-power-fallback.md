# 07: Fall back to low-power mode when no GNSS fix is found

**What to build:** When GNSS search times out without ever producing a valid fix, the tracker's main loop currently has no path out of waiting: it blocks forever on the PVT queue instead of transitioning to a GNSS-safe low-power state and retrying later. After this ticket, a failed search cycle ends the same way a successful one does after upload — by returning control to a bounded wait, then re-entering low power and retrying on schedule — so the device never gets stuck awake.

**Blocked by:** 03 (touches the same fix-waiting logic)

**Status:** ready-for-agent

- [ ] If GNSS fails to produce a valid fix within the configured search timeout, the tracker enters its low-power state instead of blocking indefinitely
- [ ] After the low-power interval elapses, the tracker resumes searching for a fix on the normal schedule
- [ ] This path is exercised without requiring an actual multi-hour hardware timeout (e.g. via a reduced timeout config or forced test injection) and observed to recover
