# 04: Guarantee location delivery with consistent QoS 1

**What to build:** The tracker's MQTT publish QoS setting is inconsistent across `Kconfig` (default 1), `config/mqtt.conf` (set to 0), and the README (which describes waiting for PUBACK). In practice the tracker fires-and-forgets today and never learns whether a location actually reached the broker. After this ticket, the tracker publishes at QoS 1 end-to-end, waits for and checks PUBACK, and all three sources of truth (Kconfig default, tracked config, documentation) agree.

**Blocked by:** None — 02 is resolved.

**Status:** ready-for-human (implemented and build-verified; runtime confirmation on a real broker is optional/nice-to-have)

- [x] `Kconfig`, `config/mqtt.conf`, and the README all specify the same QoS value (1) for location publishes — `config/mqtt.conf` was the odd one out (`=0`, left over from matching a manual `mosquitto_pub` test command); flipped to `=1` and updated its comment
- [x] A publish that the broker never acknowledges is surfaced as a distinguishable failure in logs/status, not silently treated as success — already implemented in `tracker_mqtt_publish_location()` (`MQTT PUBLISH ACK TIMEOUT` / `MQTT PUBLISH ACK ERROR` vs `MQTT PUBLISH ACKNOWLEDGED`, plus `LOG_ERR("Location delivery failed: %d", err)` in `main.c`); this path was previously unreachable in practice because the tracked config forced QoS 0
- [x] The shared publish-acknowledgement state is documented in `tracker_mqtt.c`. **Correction (caught by code review):** the first version of this comment incorrectly lumped `pending_publish_id` in with `connack_result`/`puback_result` as "written by the RX callback, read by the tracker thread" — it's actually the reverse for that one variable: written by the tracker thread before `mqtt_helper_publish()`, read by `on_mqtt_puback()` on the helper's RX thread, with no semaphore between that write and read at all. Its real safety argument is `mqtt_helper_publish()` calling `mqtt_publish()` synchronously in the tracker thread plus network causality (a PUBACK can't reference an ID that hasn't been sent yet). Rewrote the comment to describe each variable's actual direction correctly instead of overgeneralizing.
- [x] Build verified: clean incremental `west build -b sparkfun_thing_plus/nrf9160/ns` against NCS v3.3.0, zero errors, zero new warnings (FLASH 16.86%, RAM 28.36%)
- [ ] Optional: confirm end-to-end against a real or simulated broker that a publish still completes within timeout and an unacknowledged publish is correctly reported (needs a live/mock MQTT broker, not available in this environment)

## Comments

No behavior change to the actual publish/disconnect logic was needed —
the QoS-1 code path (wait for PUBACK, distinguish timeout vs rejection)
was already correctly implemented, it just never ran because the tracked
config overrode the Kconfig default down to QoS 0. Fixed the config
mismatch and documented the cross-thread invariant that was already
correct but unstated.
