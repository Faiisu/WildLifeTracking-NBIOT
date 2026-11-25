# Experiment 0003: Working legacy GNSS versus MQTT tracker

## Question and status

- Status: In progress.
- Evidence type: Static source/build configuration inspection; user-reported hardware result.
- Date: 2026-10-13, Asia/Bangkok.
- Question: What differs between the GPS-only legacy application that the user reports obtains a fix and the primary MQTT tracker?
- Decision supported: Diagnosis only; no architecture decision.

## Alternatives

- A: `myprojects/legacy_gps_3a9c2e0`, snapshot with MQTT disabled locally.
- B: `myprojects/gps_mqtt_tracker`, main at `4983271`.

## Decision criteria

Identify differences without assuming that a build or source match proves runtime equivalence. A cause requires recorded failing and successful hardware runs.

## Method and controls

Compare local source files and generated application `.config` files under each application's `build/` directory. Compare only non-secret settings. Neither generated configuration is confirmed as the exact firmware flashed in the reported run. No new hardware run was performed.

## Runs and evidence

The user reports that A obtains a GPS fix. Raw logs, first-fix time, repeat count, modem firmware, antenna, supply and exact flashed firmware hash were not supplied. This is a user report, not an agent measurement.

Static inspection found byte-identical `src/tracker_gnss.c`, `src/tracker_modem.c`, `src/circuitdojo_gnss.c`, `config/tracker.conf`, `config/modem.conf`, `config/logging.conf`, `config/system.conf`, and SparkFun board configuration.

Both generated configurations select `sparkfun_thing_plus/nrf9160/ns`, interval/retry 300 seconds, low accuracy, GNSS diagnostics, GPIO13 setup, the same COEX0 routing and RTT logging. A has no MQTT helper/library; B enables MQTT helper and TLS. LED timing differs: A toggles every 1000 ms; B uses 200 ms pulses with 1000 ms group pauses.

## Analysis

A starts GNSS immediately after modem initialization and waits for fixes repeatedly. B calls `tracker_mqtt_init()` before `tracker_gnss_start()`; any MQTT initialization failure returns before GNSS starts. B has local CA and credential files present, so missing CA cannot be asserted from file presence alone. Their contents were not inspected for this comparison.

After a valid fix B activates LTE, publishes the backlog, disconnects MQTT and waits for PSM or deactivates LTE. A never initiates uploads. Post-upload behavior cannot explain a missing first fix before any LTE upload occurred.

## Results

GNSS success: user-reported for A; not independently verified. No acquisition timing, counts, variation or power measurements are available. The GNSS implementation and checked settings match; startup dependencies, application flow, MQTT/TLS and LED behavior differ.

## Conclusion and limitations

Do not replace the GNSS module merely to match A: it already matches byte-for-byte. No root cause is established. Existing build artifacts may differ from the firmware used on the board.

## Decision and follow-up

Capture B's startup through `MQTT READY`, `GNSS START`, `Waiting for GNSS fix`, PVT status and any errors. Record whether failure is before the first fix or after the first upload. Use the same board, antenna, supply, location and modem firmware as A. No ADR is created until evidence supports a decision.
