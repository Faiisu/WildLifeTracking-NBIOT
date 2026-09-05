# 06: Move tracker broker endpoint and credentials out of tracked config

**What to build:** `config/mqtt.conf` for the GPS MQTT tracker commits a real HiveMQ Cloud cluster hostname, and the local `config/mqtt-credentials.conf` overlay uses a weak placeholder password (`admin`/`00000000`) against a broker reachable from the public internet. After this ticket, both the broker hostname/port and the credentials are deployment-specific values kept out of tracked files, with a real rotated password in place and an example file documenting the required overlay for a fresh checkout.

**Blocked by:** 01

**Status:** ready-for-human (implemented and build-verified; password rotation explicitly waived by user — see below)

- [x] The production HiveMQ Cloud hostname is no longer present in a tracked config file — moved to `config/mqtt-credentials.conf` (gitignored); verified with `git grep` across the whole repo that the hostname string appears nowhere in git-tracked content
- [x] ~~`config/mqtt-credentials.conf` (or successor) uses a rotated, non-trivial password~~ — **waived by user decision.** This is a demo/coursework checkout, not a production deployment; the `admin`/`00000000` placeholder stays as-is. The local file's own comment now says to rotate before pointing at a broker that matters.
- [x] `config/mqtt-credentials.conf.example` documents every field: hostname (required), port (optional override, commented out, since a sane TLS default is now tracked), username/password
- [x] README explains the local-overlay setup step and no longer names a specific hostname/service as "the configured" broker

## Comments

Scope note beyond the literal ticket text: also moved `CONFIG_MQTT_BROKER_PORT`/
`CONFIG_MQTT_HELPER_PORT` handling, since testing the "no local overlay"
fallback path surfaced a real inconsistency — removing the hostname override
alone left `CONFIG_MQTT_BROKER_PORT` falling back to its Kconfig default of
`1883` while `CONFIG_MQTT_HELPER_PORT` still defaulted to `8883` (TLS), a
mismatch the old tracked config's "keep both port settings equal" comment had
existed specifically to prevent. Fixed by keeping the generic, non-identifying
`8883` TLS port default tracked in `config/mqtt.conf` (restoring consistency
in the no-overlay case) and leaving only the actually-identifying hostname to
be supplied locally. Build-verified three ways: with the real local overlay,
with it removed (falls back cleanly to `test.mosquitto.org:8883`, ports
consistent), and the normal dev state restored afterward. All against NCS
v3.3.0, sparkfun_thing_plus/nrf9160/ns, zero errors, identical FLASH/RAM to
before this ticket.
