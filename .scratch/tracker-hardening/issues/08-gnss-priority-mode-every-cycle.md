# 08: Re-arm GNSS priority mode on every fix cycle, not just the first

**What to build:** `nrf_modem_gnss_prio_mode_enable()` is currently called once during `tracker_gnss_start()`. The modem's priority mode is a one-shot grant that expires after the first fix, so subsequent periodic fix cycles run without the priority boost the first cycle got, likely taking longer or being more susceptible to LTE blocking. After this ticket, every fix cycle gets the same priority treatment as the first (or, if a one-time boost is intentional, that decision is captured in an ADR instead of being silent).

**Blocked by:** 03 (touches the same GNSS start/wait sequence)

**Status:** ready-for-agent

- [ ] Priority mode is re-enabled before each periodic fix search, not just the very first one
- [ ] Time-to-fix for a second/third fix cycle is not measurably worse than the first cycle under the same conditions
- [ ] If the team decides a one-time boost is actually correct, that decision and its rationale is recorded in `docs/adr/`
