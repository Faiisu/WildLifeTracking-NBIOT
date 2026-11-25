[Back to project README](../../../README.md) | [Experiment index](../README.md)

# Experiment 0005: GNSS module acquisition time and energy comparison

## Question and status

- Status: **Planned**. No candidate modules or controlled measurements have been recorded.
- Evidence type: **Planned**; no measured, simulated, or external-reference result is claimed.
- Owner and dates: Owner to be assigned. Plan created 2026-10-16, Asia/Bangkok (UTC+07:00).
- Question: For each candidate GNSS module, how long does it take to obtain a valid location fix, how much energy does acquisition use, and what accuracy and fix success rate are achieved under matched conditions?
- Decision supported: Select a GNSS module that meets the project's location quality and response-time requirements with the best supported energy cost. No module is selected by this plan.

## Alternatives

| Alternative | Changed variable | Expected benefit | Trade-off |
| --- | --- | --- | --- |
| A: candidate module | GNSS module model and its required host integration | Baseline acquisition time, energy, accuracy, and reliability | Availability and integration requirements are not yet known |
| B: each additional candidate | GNSS module model; hold test conditions and fix criteria constant | Identify the time-energy-quality trade-off across available modules | Results may not generalize to other antennas, firmware, assistance services, or environments |

Candidate model list: **TBD**. Record exact module part number, hardware revision, firmware version, antenna, and assistance configuration before testing.

## Decision criteria

Resolve these provisional criteria before collecting comparison runs:

- Minimum acceptable horizontal accuracy and fix validity criteria.
- Maximum acceptable time to first valid fix (TTFF).
- Required fix success rate for each intended environment.
- Whether the decision optimizes energy per successful fix, meets a battery budget, or uses a weighted score. Record the rationale and any hard constraints; do not choose weights after seeing results.
- Minimum repeat count and acceptable uncertainty based on the instrument and observed variation.

Compare energy per successful fix alongside TTFF, accuracy, and success rate. A module that is faster but materially less accurate, or that frequently fails, must not be called the best value based on acquisition energy alone. If no candidate meets the hard constraints, report the comparison as inconclusive or identify the unmet requirement.

## Method and controls

### To do before measurement

- [ ] List candidate module models and confirm that each is available for testing.
- [ ] Record each module's part number, hardware revision, firmware, host board/interface, antenna and antenna placement, and required supply conditions.
- [ ] Define the project requirements for horizontal accuracy, fix validity, maximum TTFF, target success rate, and intended operating environments.
- [ ] Decide which start conditions to compare: cold start, warm start, and/or hot start. Define how ephemeris, almanac, backup power, and assistance data will be cleared or preserved consistently.
- [ ] Select and document one measurement boundary. Prefer module supply energy for module-level comparison; if measuring the whole board, report that separately and do not label it module-only energy.
- [ ] Select a current/energy instrument, verify calibration and zero offset, document the shunt and voltage plane, and use a sample rate sufficient for acquisition current changes.
- [ ] Define a consistent acquisition start event and end event. Record elapsed time from GNSS start command to the first PVT fix that passes the agreed validity and accuracy criteria. Record failures and timeouts as attempted cycles.
- [ ] Choose controlled locations and sky conditions, including the environments relevant to deployment. Keep location, antenna orientation, mounting, supply voltage, temperature, and test duration consistent across candidates.
- [ ] Fix GNSS settings and services where possible: constellation/bands, update interval, dynamic model, accuracy mode, assistance data, and host-side processing. Document unavoidable differences.
- [ ] Plan randomized or alternating test order to reduce time-of-day and changing-satellite-geometry bias. Set the repeat count before collecting data.
- [ ] Create a run record for each session, preserve raw instrument and GNSS logs, and record checksums and durable archive locations.

### Measurement protocol

Run the same predeclared sequence for every candidate and start condition. Capture raw voltage and current at the selected boundary, GNSS start and valid-fix markers, fix validity, reported horizontal accuracy, satellite/quality indicators available from each module, and any timeout or error. Align device and instrument clocks with recorded anchors and uncertainty. Distinguish API-call timestamps from physical measurement timestamps.

Do not combine setup energy or host-board consumption with module acquisition energy unless that whole-system metric is explicitly reported as a separate result. Keep failed attempts in the dataset; apply the same timeout rule to every candidate. Record environmental deviations, missing samples, and excluded cycles with reasons while retaining the original evidence.

## Runs and evidence

No runs have been collected. Create `runs/run-<NNN>.md` for each measurement session, recording:

- Candidate identity, start condition, test location/environment, date/timezone, firmware/configuration, and deviations.
- Instrument model, calibration status, measurement boundary, voltage plane, sample rate, and channel units.
- Raw-data path, durable archive location, SHA-256 checksum, selected time range, clock alignment, missing samples, failures, and exclusions.
- Analysis command/script revision and derived summary or figure paths.

Create supporting `runs/`, `data/`, `figures/`, or `scripts/` folders when they contain actual artifacts.

## Analysis

For each attempt, calculate acquisition duration and integrate power over the same start-to-end window using actual sample timestamps. For sampled voltage and current:

```text
power_w = voltage_v * current_a
energy_j = sum((power_w[i] + power_w[i+1]) / 2 * delta_time_s)
success_rate_percent = 100 * successful_valid_fixes / attempted_cycles
energy_per_successful_fix_j = total_acquisition_energy_j / successful_valid_fixes
```

Define sample-gap handling before analysis; reject unresolved windows rather than filling gaps with zeros. If no valid fix is obtained, report the failed attempt's duration and energy to timeout, and report energy per successful fix as unavailable when the candidate has zero successful fixes. Report absolute values, successful/attempted counts, mean and variation, instrument uncertainty, and environment/start-condition strata. Report accuracy with its units and method. Do not pool cold and warm starts or different environments into one result without also showing the separate groups.

## Results

| Alternative | Metric and units | Successful / attempted cycles | Mean | Variation | Evidence |
| --- | --- | --- | --- | --- | --- |
| Each candidate, by start condition and environment | TTFF (s) | Not measured | Not measured | Not measured | None |
| Each candidate, by start condition and environment | Acquisition energy (J) | Not measured | Not measured | Not measured | None |
| Each candidate, by start condition and environment | Energy per valid fix (J/fix) | Not measured | Not measured | Not measured | None |
| Each candidate, by start condition and environment | Horizontal accuracy (m) and success rate (%) | Not measured | Not measured | Not measured | None |

## Conclusion and limitations

Pending candidate selection and controlled measurement. There is no evidence yet that any module is faster, more energy-efficient, more accurate, or more reliable than another. Results will apply only to the tested hardware, antenna, firmware, assistance configuration, supply, location, and environmental conditions.

## Decision and follow-up

No architecture decision or implementation issue is linked yet. After measurements, document the chosen module and trade-offs in an ADR if the result changes the system architecture or module selection. Revisit the comparison if the candidate set, antenna, firmware, GNSS settings, assistance service, supply, or deployment environment changes materially.

## See also

- [Experiment index](../README.md)
- [Experiment template](../TEMPLATE.md)
- [GNSS continuous baseline](../0002-gnss-continuous-baseline/README.md)
