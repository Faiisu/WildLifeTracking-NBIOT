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
tracker setting caused the failure. The user supplied the NCS v1.4.1 GPS sample
as the working reference on 2026-10-13; the actual flashed revision, board and
modem firmware are still unconfirmed.

### External reference comparison (2026-10-13)

Sources: [NCS v1.4.1 GPS main.c](https://github.com/nrfconnect/sdk-nrf/blob/v1.4.1/samples/nrf9160/gps/src/main.c)
and [prj.conf](https://github.com/nrfconnect/sdk-nrf/blob/v1.4.1/samples/nrf9160/gps/prj.conf).
This is static inspection, not measured acquisition evidence. The user reports
logs are available but GNSS does not obtain a fix; the tested application is
not yet identified.

| Property | Working external reference | Primary tracker | Temporary baseline |
| --- | --- | --- | --- |
| GNSS schedule | Continuous: interval 1, retry 0 | Periodic: interval 300 s, retry 300 s | Continuous: interval 1, retry 0 |
| Assistance | SUPL disabled by default | No assistance implemented | Assistance disabled |
| LTE before first fix | GPS activated with CFUN 31; no default LTE connection | LTE deactivated, then GNSS activated | GNSS activated; no LTE connection |
| GNSS API | BSD library GNSS socket | nrf_modem_gnss callbacks | nrf_modem_gnss callbacks |
| Antenna setup | DK/Thingy91-specific MAGPIO and COEX0 | Board-specific COEX0 and GPIO13 | Board-specific COEX0 and GPIO13 |
| Output | printk/printf console | RTT logging | RTT logging and stdout |

The temporary baseline already reproduces the reference's continuous,
unassisted acquisition mode using the pinned NCS v3.3.0 APIs. This does not
prove hardware or modem equivalence. Do not copy DK/Thingy91 MAGPIO settings
onto SparkFun/Circuit Dojo hardware without validating its antenna circuit.
Needed evidence: tested application/startup banner, board model, modem firmware
version, elapsed search time, PVT flags and GSV/GSA output from the failing run.

## Decision and follow-up

Pending user RTT evidence. If B fixes, isolate differences one at a time before
changing the primary tracker. No ADR or implementation ticket is required yet.

## See also

- [Experiment index](../README.md)
- [Temporary application](../../../myprojects/gps_mqtt_tracker_temp/README.md)
