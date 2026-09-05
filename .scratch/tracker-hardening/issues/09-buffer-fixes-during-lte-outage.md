# 09: Buffer un-sent GPS fixes across LTE outages and flush backlog on reconnect

**What to build:** Today, if `tracker_modem_prepare_upload()`, `tracker_mqtt_connect()`, or the publish step fails, the just-acquired GPS fix is discarded outright in `main.c` and the tracker moves straight to low power. Instead, a fix that can't be uploaded should be retained in an in-memory backlog. The tracker keeps acquiring new fixes on its normal schedule, adding each one to the backlog whenever it still can't upload. The next time LTE + MQTT connect succeeds, every buffered fix is published in chronological order (oldest to newest), each keeping its original acquisition timestamp, before the tracker resumes normal single-fix operation.

## Backlog retention policy (decided during ticket grooming)

The backlog is a fixed-capacity, RAM-only structure (lost on reboot/power loss — no flash persistence in this ticket) storing only the fields needed to publish (lat/lon/alt/acc/timestamp), not the full `nrf_modem_gnss_pvt_data_frame`. It is split into two zones:

- **Active zone**: holds newly-acquired fixes at full resolution. When the active zone alone fills up, thin it by decimation (drop every second entry) rather than dropping the oldest outright, so surviving fixes stay spread across the whole outage instead of clustering at the recent end. The single oldest entry displaced by that decimation becomes a new **anchor**.
- **Anchor zone**: grows by one permanent entry per active-zone overflow event, preserving at least one representative fix from each "generation" of the outage for good. Anchors are never further decimated.

This means: the closer to the present, the denser the retained fixes; the further back, the sparser — but nothing is fully erased from the middle of a normal-length outage.

**Hard-full behavior**: if the anchor zone eventually grows to consume the entire buffer (no room left for any active/new fixes — only happens under an extremely long outage), evict the single oldest anchor to make room for new incoming fixes. This sacrifices the very first breadcrumb of the outage in exchange for never stopping recent position capture.

**Blocked by:** 04 (needs QoS 1 PUBACK confirmation to safely evict a buffered fix from the backlog only after the broker has actually acknowledged receipt — evicting on an unacknowledged publish risks silently losing a position permanently)

**Status:** ready-for-agent

- [ ] A fix that fails to upload at any stage (LTE connect timeout, MQTT connect timeout, or publish failure) is added to the backlog instead of being discarded
- [ ] The backlog is RAM-only; a reboot/power loss during an outage is accepted to lose it (no persistence requirement in this ticket)
- [ ] The backlog stores only lat/lon/alt/acc/timestamp per entry, not the full GNSS PVT frame
- [ ] Once LTE + MQTT connect succeeds, every backlog entry is published in oldest-to-newest order, and is only removed from the backlog after its PUBACK is received
- [ ] The backlog is split into an active zone (full-resolution recent fixes, decimated by half on overflow) and an anchor zone (one permanently-retained fix per active-zone overflow event)
- [ ] When the anchor zone has consumed the entire buffer and a new fix still can't fit, the oldest anchor is evicted to make room
- [ ] Total backlog capacity is a Kconfig tunable sized to comfortably fit the existing heap budget (`CONFIG_HEAP_MEM_POOL_SIZE=8192`)
- [ ] Normal (non-outage) operation — a single fix acquired and published within the same cycle — is unaffected

## Comments

Design was worked out interactively during grooming (see conversation): naive
"decimate everything on every overflow" was rejected because it only
survives ~log2(N) overflow rounds before the oldest generation shrinks to
zero; the active/anchor split lets a size-N buffer survive on the order of N
overflow rounds while still keeping recent data at full resolution, at the
cost of eventually sacrificing the very oldest anchor once the buffer is
completely full of anchors.
