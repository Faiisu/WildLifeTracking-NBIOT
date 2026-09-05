# 04: Guarantee location delivery with consistent QoS 1

**What to build:** The tracker's MQTT publish QoS setting is inconsistent across `Kconfig` (default 1), `config/mqtt.conf` (set to 0), and the README (which describes waiting for PUBACK). In practice the tracker fires-and-forgets today and never learns whether a location actually reached the broker. After this ticket, the tracker publishes at QoS 1 end-to-end, waits for and checks PUBACK, and all three sources of truth (Kconfig default, tracked config, documentation) agree.

**Blocked by:** None — 02 is resolved.

**Status:** ready-for-human (implemented and build-verified; runtime confirmation on a real broker is optional/nice-to-have)

- [x] `Kconfig`, `config/mqtt.conf`, and the README all specify the same QoS value (1) for location publishes — `config/mqtt.conf` was the odd one out (`=0`, left over from matching a manual `mosquitto_pub` test command); flipped to `=1` and updated its comment
- [x] A publish that the broker never acknowledges is surfaced as a distinguishable failure in logs/status, not silently treated as success — already implemented in `tracker_mqtt_publish_location()` (`MQTT PUBLISH ACK TIMEOUT` / `MQTT PUBLISH ACK ERROR` vs `MQTT PUBLISH ACKNOWLEDGED`, plus `LOG_ERR("Location delivery failed: %d", err)` in `main.c`); this path was previously unreachable in practice because the tracked config forced QoS 0
- [x] The shared publish-acknowledgement state (`connack_result`, `puback_result`, `pending_publish_id`) is documented in `tracker_mqtt.c`: it's safe without atomics because each value is fully written before its paired `k_sem_give` and only read after the matching `k_sem_take`, which is the actual synchronization point — added a comment spelling this out so it isn't mistaken for an unguarded race on a future edit
- [x] Build verified: clean incremental `west build -b sparkfun_thing_plus/nrf9160/ns` against NCS v3.3.0, zero errors, zero new warnings (FLASH 16.86%, RAM 28.36%)
- [ ] Optional: confirm end-to-end against a real or simulated broker that a publish still completes within timeout and an unacknowledged publish is correctly reported (needs a live/mock MQTT broker, not available in this environment)

## Comments

No behavior change to the actual publish/disconnect logic was needed —
the QoS-1 code path (wait for PUBACK, distinguish timeout vs rejection)
was already correctly implemented, it just never ran because the tracked
config overrode the Kconfig default down to QoS 0. Fixed the config
mismatch and documented the cross-thread invariant that was already
correct but unstated.
