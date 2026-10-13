# Experiment 0002: Continuous GNSS baseline

## Question and status

- Status: In progress
- Evidence type: External reference; build/flash verification when recorded.
- Owner and date: Repository user and coding agent, 2026-10-13, Asia/Bangkok.
- Question: Does the continuous GNSS sample obtain a fix where the current tracker reports Using: 0?
- Decision supported: Diagnosis only; no architecture decision yet.

## Alternatives

| Alternative | Changed variable | Expected benefit | Trade-off |
| --- | --- | --- | --- |
| A: Current tracker | Periodic 300 s, low accuracy, PVT queue, LTE/MQTT | Application workload | User reports Using: 0; no raw run supplied |
| B: Temporary SDK sample baseline | Continuous, normal accuracy, latest PVT + semaphore, no LTE/MQTT; optional board heartbeat | Reproduce sample acquisition behavior | Multiple variables change; cannot identify one root cause |

## Decision criteria

A valid PVT fix and nonzero satellites used support successful acquisition in B.
Failure requires PVT flags, NMEA signal levels, and antenna/modem evidence.
Build and flash success alone are not a GNSS success criterion.

## Method and controls

Use SparkFun Thing Plus nRF9160, the same antenna, orientation, supply and location
as the reported working sample, and NCS v3.3.0. Actual antenna, supply, weather,
modem firmware version and prior GNSS cache are not yet recorded. Do not delete
modem GNSS data. Compare under the same conditions and record elapsed seconds
from GNSS start to first valid PVT, acquisition flags and NMEA. RTT channel 0 is
nonblocking; dropped output must be reported. No power measurement is planned. The user subsequently requested a board
heartbeat (1 second on/off); subsequent runs must record this additional GPIO
activity and must not be treated as the original LED-free baseline.

## Runs and evidence

Build passed; flash remains unconfirmed because target voltage/reset access failed.
Evidence is recorded in [run-001](runs/run-001.md). Hardware acquisition
output is pending the user's RTT observation. No controlled repeat runs exist.

## Analysis

Report first-fix elapsed time in seconds and valid-fix counts if raw output is
provided. Account for RTT losses and differences in modem cache. No percentage,
mean or variation can be calculated yet.

## Results

| Alternative | Metric | Successful / attempted cycles | Mean | Variation | Evidence |
| --- | --- | --- | --- | --- | --- |
| A | Valid GNSS fix | Not measured | Not measured | Not measured | User symptom only |
| B | Valid GNSS fix | Not measured | Not measured | Not measured | RTT result pending |

## Conclusion and limitations

No acquisition result is established. Several settings differ between A and B;
a successful B run would narrow the investigation but would not prove which
tracker setting caused the failure. The older working sample's SDK is unknown.

## Decision and follow-up

Pending user RTT evidence. If B fixes, isolate differences one at a time before
changing the primary tracker. No ADR or implementation ticket is required yet.

## See also

- [Experiment index](../README.md)
- [Temporary application](../../../myprojects/gps_mqtt_tracker_temp/README.md)
