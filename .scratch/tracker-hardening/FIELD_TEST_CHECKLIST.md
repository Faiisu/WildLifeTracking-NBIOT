# Field/Hardware Test Checklist — Tracker Hardening

Consolidates every item across tickets 01–09 that could not be verified in
this environment (no physical board, no live carrier, no live broker) and
still needs real hardware/field data before it can be marked done or before
ticket 08 can be decided. Pulled from `.scratch/tracker-hardening/issues/*.md`
on 2026-09-05.

## 1. Outstanding design decision — ticket 08 (GNSS priority mode)

Blocked on field data before any code is written. See
[08-gnss-priority-mode-every-cycle.md](issues/08-gnss-priority-mode-every-cycle.md)
for full rationale (Nordic's own guidance says priority mode should be
reactive, not unconditional every-cycle, because it trades away LTE's TAU
radio time).

- [ ] **Baseline contention.** Log `NRF_MODEM_GNSS_PVT_FLAG_NOT_ENOUGH_WINDOW_TIME`
      occurrences across many real fix cycles (`CONFIG_TRACKER_GNSS_DIAGNOSTICS=y`).
      If it rarely/never fires, this ticket may be unnecessary.
- [ ] **Actual granted periodic TAU interval.** Read `LTE_LC_EVT_PSM_UPDATE`'s
      `evt->psm_cfg.tau` in the field — the requested `CONFIG_TRACKER_PSM_PERIODIC_TAU_SECONDS`
      (300s) may not be what the carrier actually grants.
- [ ] **Mobility-triggered TAU frequency under real movement.** Correlate
      `LTE_LC_EVT_CELL_UPDATE` (cell/TAC changes) against real movement
      speed/range to see how often Tracking Areas are crossed relative to
      the fix interval — this number can't be tuned away by Kconfig.
- [ ] **Time-to-fix, cycle 1 vs. cycle 2+, same real conditions.** Multiple
      fixes from a fixed location (controls for sky-view variance) —
      quantifies whether losing priority mode after fix 1 actually costs
      meaningful time.
- [ ] **Power cost: one TAU vs. one full re-attach, measured.** Use
      `myprojects/monitor-application/power-monitor/` tooling for real
      mA/mJ numbers, not qualitative reasoning.
- [ ] **Multi-day energy budget: PSM+TAU vs. full-deactivate-every-cycle.**
      Same power-monitor tooling, both strategies over a representative
      deployment window including realistic movement.
- [ ] **Does enabling GNSS priority visibly disrupt an in-flight TAU?** If a
      reactive-priority prototype is built, test against conditions that
      would need a TAU around the same time; check modem/network logs for
      retries or delays.
- [ ] **Sanity-check the "consecutive frames" threshold** for triggering
      reactive priority mode, using real flag-noise patterns from item 1.

## 2. Runtime verification still open (implemented, build-verified, not hardware-verified)

- [ ] **Ticket 03 — stale GNSS fix purge.** Confirm on a live board+broker
      that a stale fix is never reported right after an upload cycle.
- [ ] **Ticket 04 — MQTT QoS 1.** Confirm end-to-end against a real/simulated
      broker that a publish completes within timeout and an unacknowledged
      publish is correctly reported.
- [ ] **Ticket 07 — GNSS timeout fallback.** Observe the fallback actually
      firing and recovering (search times out → `LOW_POWER` → resumes
      searching) on a real board with no sky view, or a modem simulator.
      Only static review has happened so far.
- [ ] **Ticket 09 — backlog buffering (adaptive stride).** Confirm against a
      real broker that a multi-fix backlog, built up during a real outage,
      drains correctly oldest-to-newest on reconnect. Also worth checking in
      the field: does adaptive stride's even-spacing-over-time trade-off
      (no extra recent-fix density — see
      [ADR 0005](../../docs/adr/0005-adaptive-stride-backlog-retention.md))
      actually matter for real animal movement patterns, or is uniform
      coverage sufficient.

## 3. Known accepted risk (no field test — a standing decision, not a gap)

- [ ] **Ticket 01/06 — MQTT credentials not rotated.** The leaked ESP32
      broker password (`iot.cpe.ku.ac.th`) and the demo HiveMQ credentials
      (`admin`/`00000000`) were explicitly left un-rotated per user decision
      (waived for a demo project, not production). Revisit before any real
      deployment beyond a demo.

## How to use this list

Run section 1 and 2 together during the same field outing where possible —
most need the tracker physically moving with `CONFIG_TRACKER_GNSS_DIAGNOSTICS=y`
and a live broker connection. Section 1's results feed directly into
deciding ticket 08 (reactive priority mode vs. TAU-period fix vs. leave
one-shot + ADR); section 2 just closes out the "optional" checkboxes already
sitting in tickets 03/04/07/09.
