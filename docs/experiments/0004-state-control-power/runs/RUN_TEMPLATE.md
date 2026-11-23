[Back to experiment 0004](../README.md)

# Run form: tracker state power

**Template only; no measurements are recorded in this file.** Copy to
`run-001.md` and replace prompts with the actual conditions and evidence.

## Identity and scope

- Run ID, operator, collection date/time and timezone:
- Profile A/B/C and exact hypothesis:
- Firmware commit, dirty diff artifact, HEX path and SHA-256:
- SDK, board target/revision, redacted effective configuration artifact:
- Whole-board or rail boundary; voltage measurement plane:
- Wiring diagram; supply voltage/path; battery/USB/debug connections:
- Instrument, shunt, range, ADC/averaging configuration, calibration/zero check:
- Resolution, formatting precision, measurement uncertainty:
- Channel mapping and raw units:
- Planned and actual sampling cadence; missing-frame/error policy:
- Network mode, carrier, home/roaming, signal conditions:
- Eight-state profile, GPS manual-stop/timeout and valid/null FIX, session/hold schedule:
- TLS/QoS, exact payload bytes, redacted broker identity, delivery attempts/outcomes:
- Antenna placement, sky view, motion, temperature, cold/warm conditions:

## Clock alignment

| Anchor | RTT uptime (s) | Monitor/host UTC time | Alignment method and uncertainty |
| --- | --- | --- | --- |
| Start | Not recorded | Not recorded | Not recorded |
| End | Not recorded | Not recorded | Not recorded |

Record clock offset/drift, receipt latency, manual timing uncertainty, and the
mapping used to place operation boundaries on the power timeline.

## Raw evidence and archive

| Artifact | Source path | Durable archive | SHA-256 | Time range and units |
| --- | --- | --- | --- | --- |
| Power samples | Not collected | Not collected | Not computed | Not recorded |
| RTT log | Not collected | Not collected | Not computed | Not recorded |
| Effective config/diff | Not archived | Not archived | Not computed | Secrets removed |

Preserve original samples. Record sanitization of identifiers/coordinates and any
checksum distinction between private original and sanitized archive.

## Cycle and window ledger

| Cycle | State/window | Start marker/time | End marker/time | Outcome | Samples/coverage | Exclusions or deviations |
| --- | --- | --- | --- | --- | --- | --- |
| Not collected | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | None recorded |

Include every attempted cycle. Classify manual-stop/timeout, valid/null report,
registration failure/retry, MQTT connection/delivery/disconnect errors, PSM confirmation/refusal,
and unexpected wake-up. Keep excluded cycles and their reasons.

## Analysis and results

- Analysis command, script revision, input archives, and output paths:
- Integration method, voltage plane, gap handling, settling exclusions:
- Summary metrics with units, successful/attempted counts, and variation:
- Instrument and boundary uncertainty, separate from repeat-run variability:
- Energy: **Not measured** until this form is filled from raw evidence.
- Interpretation and unresolved limitations:

## See also

- [Power test procedure](../../../../myprojects/gps_mqtt_stateControl/docs/power-testing.md)
- [Experiment index](../../README.md)
