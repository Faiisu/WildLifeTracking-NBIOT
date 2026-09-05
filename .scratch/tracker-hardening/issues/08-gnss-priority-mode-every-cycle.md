# 08: Re-arm GNSS priority mode on every fix cycle, not just the first

**What to build:** `nrf_modem_gnss_prio_mode_enable()` is currently called once during `tracker_gnss_start()`. The modem's priority mode is a one-shot grant that expires after the first fix, so subsequent periodic fix cycles run without the priority boost the first cycle got, likely taking longer or being more susceptible to LTE blocking. After this ticket, every fix cycle gets the same priority treatment as the first (or, if a one-time boost is intentional, that decision is captured in an ADR instead of being silent).

**Blocked by:** 03 (touches the same GNSS start/wait sequence). Also
practically blocked on the field/hardware testing below before a design
decision can be made — see Comments.

**Status:** needs-info (design decision on hold pending field data; not
ready-for-agent yet)

- [ ] Priority mode is re-enabled before each periodic fix search, not just the very first one
- [ ] Time-to-fix for a second/third fix cycle is not measurably worse than the first cycle under the same conditions
- [ ] If the team decides a one-time boost is actually correct, that decision and its rationale is recorded in `docs/adr/`

## Comments

### Why this is on hold

Grooming surfaced that the naive fix (call `nrf_modem_gnss_prio_mode_enable()`
every cycle, unconditionally) contradicts Nordic's own documented guidance
for this API: priority mode should only be enabled reactively, after
`NRF_MODEM_GNSS_PVT_FLAG_NOT_ENOUGH_WINDOW_TIME` has been observed in
*several consecutive* PVT frames -- not blindly every cycle -- because
enabling it trades away radio time from LTE's own background housekeeping
(Tracking Area Updates), which happens both on a periodic timer
(`CONFIG_TRACKER_PSM_PERIODIC_TAU_SECONDS`, currently tracked at 300s,
suspiciously equal to `CONFIG_TRACKER_PERIODIC_INTERVAL`) and, independent of
any timer, whenever the device physically moves into a new cellular
Tracking Area -- a real concern for a tracker mounted on something that
moves. Deciding between "reactive priority mode," "just fix the TAU period
mismatch," "both," or "leave one-shot + write an ADR" needs real signal to
resolve, not more armchair reasoning. This is the field/hardware test plan to
run before revisiting this ticket.

### Test plan -- field/hardware topics to gather before deciding

1. **Baseline: does contention even happen here?** Log `NRF_MODEM_GNSS_PVT_FLAG_NOT_ENOUGH_WINDOW_TIME` occurrences (already read in `print_gnss_flags()`, currently just printed under `CONFIG_TRACKER_GNSS_DIAGNOSTICS`) across many real fix cycles in the field. If it rarely or never fires, the whole ticket may be unnecessary.
2. **Actual granted periodic TAU interval.** The tracker requests `CONFIG_TRACKER_PSM_PERIODIC_TAU_SECONDS`, but the network can grant a different value (`LTE_LC_EVT_PSM_UPDATE` already logs `evt->psm_cfg.tau`). Confirm what your actual carrier grants in the field -- the 300s tracked value may not even be what's really in effect.
3. **Mobility-triggered TAU frequency under real movement.** Correlate `LTE_LC_EVT_CELL_UPDATE` events (cell/TAC changes) against real GPS traces from actual animal/asset movement speed and range, to see how often Tracking-Area boundaries are actually crossed relative to the GNSS fix interval. This is the number that can't be tuned away by a Kconfig change.
4. **Time-to-fix, first vs. subsequent cycles, same real conditions.** Multiple real fix attempts from a fixed location (to control for sky-view variance) comparing cycle 1 (has priority) against cycle 2+ (doesn't) -- quantifies whether priority mode's absence actually costs meaningful time-to-fix here, or if it's negligible in practice.
5. **Power cost: one TAU vs. one full re-attach, measured.** Use the existing `myprojects/monitor-application/power-monitor/` tooling to get real mA/mJ numbers instead of the qualitative reasoning from grooming (TAU is signaling-only under an existing security context; full re-attach re-does authentication/security/bearer setup -- lighter in principle, but "how much lighter" needs measurement on this actual hardware/carrier).
6. **Overall multi-day energy budget: PSM+TAU vs. full-deactivate-every-cycle.** Same power-monitor tooling, run both strategies across a representative real deployment window (including realistic movement), to see which actually wins net -- the current PSM choice was made for good reasons, but hasn't been validated against a mobile deployment's actual TAU frequency.
7. **Does enabling GNSS priority visibly disrupt an in-flight TAU?** If reactive priority mode is prototyped, deliberately test it against conditions that would need a TAU around the same time, and check modem/network logs for retries, delays, or any sign the "may interfere with LTE operations" warning has a visible real cost here.
8. **Sanity-check the "consecutive frames" threshold.** Before hardcoding how many consecutive `NOT_ENOUGH_WINDOW_TIME` frames should trigger reactive priority, look at real flag noise patterns from #1 to see what threshold actually distinguishes "genuinely blocked" from "one noisy frame."
