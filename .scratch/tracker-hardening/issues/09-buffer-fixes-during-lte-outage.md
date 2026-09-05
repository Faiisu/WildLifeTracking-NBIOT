# 09: Buffer un-sent GPS fixes across LTE outages and flush backlog on reconnect

**What to build:** Today, if `tracker_modem_prepare_upload()`, `tracker_mqtt_connect()`, or the publish step fails, the just-acquired GPS fix is discarded outright in `main.c` and the tracker moves straight to low power. Instead, a fix that can't be uploaded should be retained in an in-memory backlog. The tracker keeps acquiring new fixes on its normal schedule, adding each one to the backlog whenever it still can't upload. The next time LTE + MQTT connect succeeds, every buffered fix is published in chronological order (oldest to newest), each keeping its original acquisition timestamp, before the tracker resumes normal single-fix operation.

## Backlog retention policy — final decision: adaptive stride sampling

See **[ADR 0005](../../../docs/adr/0005-adaptive-stride-backlog-retention.md)**
for the full comparison and rationale. Summary: the backlog is a
fixed-capacity, RAM-only structure (lost on reboot/power loss — no flash
persistence in this ticket) storing only the fields needed to publish
(lat/lon/alt/acc/timestamp), not the full `nrf_modem_gnss_pvt_data_frame`.

Overflow policy is **adaptive stride sampling**: a single global stride `S`
(starts at 1) determines which fixes get stored at all (`fix_number % S == 0`);
when the buffer fills, `S` doubles and the existing buffer is refiltered to
the new stride before the qualifying fix is appended. This keeps the
retained set evenly spaced across the *entire* outage seen so far — verified
by simulation to scale reach with elapsed time (a 30-entry buffer covered
~93% of a 1,000,000-fix outage, uniformly spaced) — rather than converging
to a small fixed recent-only window the way a flat decimation cascade does
(see ADR 0005 for why that alternative was rejected).

~~Superseded design (kept for history — see ADR 0005 "Alternatives
Considered" §1): an active/anchor zone split, where the active zone
decimated by half on overflow and displaced entries became permanent
"anchors." Simulation showed anchors are not actually permanent in practice
— they get evicted again almost immediately once the buffer reaches steady
state, capping total reach at roughly `2 x capacity` regardless of outage
length. Rejected in favor of adaptive stride.~~

**Blocked by:** 04 (needs QoS 1 PUBACK confirmation to safely evict a buffered fix from the backlog only after the broker has actually acknowledged receipt — evicting on an unacknowledged publish risks silently losing a position permanently)

**Status:** ready-for-human (implemented and build-verified; runtime confirmation on a real broker/outage is optional/nice-to-have)

- [x] A fix that fails to upload at any stage (LTE connect timeout, MQTT connect timeout, or publish failure) is added to the backlog instead of being discarded — `main.c`'s `TRACKER_STATE_WAIT_FOR_GNSS` now calls `tracker_backlog_add()` immediately after every valid fix, before any upload is attempted, so a later failure at any stage simply leaves it buffered
- [x] The backlog is RAM-only; a reboot/power loss during an outage is accepted to lose it (no persistence requirement in this ticket) — static array in `.bss`, no flash writes
- [x] The backlog stores only lat/lon/alt/acc/timestamp per entry, not the full GNSS PVT frame — `struct tracker_location_fix` in `tracker_backlog.h`; `tracker_mqtt_publish_location()` was refactored to take this type instead of the raw PVT frame
- [x] Once LTE + MQTT connect succeeds, every backlog entry is published in oldest-to-newest order, and is only removed from the backlog after its PUBACK is received — new `TRACKER_STATE_PUBLISH_BACKLOG` state drains via `tracker_backlog_peek_oldest()`/`remove_oldest()`, stopping (keeping the rest buffered) on the first publish failure
- [x] Overflow handling uses adaptive stride sampling (single global stride, doubled and refiltered on overflow) per ADR 0005 — not the superseded active/anchor design
- [x] Total backlog capacity is a Kconfig tunable (`CONFIG_TRACKER_BACKLOG_CAPACITY`, default 40), static array not heap — measured +1936 B RAM at default capacity
- [x] Normal (non-outage) operation is unaffected — a fix always seq==1 at stride 1 the moment a drained backlog receives its first new entry, so it's stored and drained in the same cycle exactly as before this ticket
- [x] Build verified: clean `west build` against NCS v3.3.0 (sparkfun_thing_plus/nrf9160/ns), 321/321, zero errors, zero new warnings (FLASH 16.98%, RAM 29.61%)
- [ ] Optional: on-device confirmation against a real broker that a multi-fix backlog actually drains correctly on reconnect (needs a live outage scenario, not available in this environment)

## Comments

Design evolved through three simulated/verified candidates before writing
firmware code — see ADR 0005 for the full comparison, numbers, and links to
the interactive visualizations built while deciding:
1. Flat active/anchor cascade — rejected, converges to a fixed ~2x-capacity
   recent-only window no matter how long the outage runs.
2. Logarithmic stride buckets — rejected as the sole scheme, gives real
   whole-outage reach but with recency-biased, geometrically uneven gaps.
3. **Adaptive stride sampling — chosen.** Evenly spaced across the whole
   outage, reach scales with elapsed time. Trade-off: no extra density for
   the most recent fixes specifically; a raw recent window can be added
   later as a separate, additive change if that turns out to matter.
