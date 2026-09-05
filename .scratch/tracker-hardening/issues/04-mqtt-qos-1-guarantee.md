# 04: Guarantee location delivery with consistent QoS 1

**What to build:** The tracker's MQTT publish QoS setting is inconsistent across `Kconfig` (default 1), `config/mqtt.conf` (set to 0), and the README (which describes waiting for PUBACK). In practice the tracker fires-and-forgets today and never learns whether a location actually reached the broker. After this ticket, the tracker publishes at QoS 1 end-to-end, waits for and checks PUBACK, and all three sources of truth (Kconfig default, tracked config, documentation) agree.

**Blocked by:** None — 02 is resolved.

**Status:** ready-for-agent

- [ ] `Kconfig`, `config/mqtt.conf`, and the README all specify the same QoS value (1) for location publishes
- [ ] A publish that the broker never acknowledges is surfaced as a distinguishable failure in logs/status, not silently treated as success
- [ ] The shared publish-acknowledgement state used across the MQTT callback and the publish call is documented (or made atomic) so the cross-thread invariant is explicit
- [ ] Existing publish/disconnect flow still completes within its configured timeouts on a real or simulated broker
