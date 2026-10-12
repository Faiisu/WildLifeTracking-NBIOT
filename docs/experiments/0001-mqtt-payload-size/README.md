[Back to project README](../../../README.md) | [Experiment index](../README.md)

# Experiment 0001: MQTT Payload Size and Energy

## Question and status

- Status: **Planned**
- Evidence type: **No controlled measurements recorded**
- Question: How does increasing a JSON payload from N to 2N bytes affect publish
  energy and total upload energy in `gps_mqtt_tracker`?
- Proposed decision: Whether additional telemetry fields justify their energy cost.

The suggested 1% increase is an **illustrative example**, not a measured result,
acceptance threshold, or conclusion. This record does not establish that adding
fields is acceptable.

## Alternatives

| Alternative | Payload | Behavior |
| --- | --- | --- |
| A | N UTF-8 bytes | Baseline JSON sent as one MQTT PUBLISH |
| B | 2N UTF-8 bytes | Expanded JSON sent as one MQTT PUBLISH |

Count serialized payload bytes, including field names and punctuation. MQTT
headers and topic bytes are separate; keep the topic unchanged. Record the exact
sanitized JSON for each alternative and its encoded length before measurement.

Choose N before the test. Both payloads must fit the current 256-byte formatting
buffer in `tracker_mqtt.c`, including the terminating NUL, and the effective MQTT
buffer limits. Thus, for this plan, 2N must be at most 255 payload bytes. Larger
payloads require a separately recorded firmware/configuration change.

For the size-only comparison, use fixed valid JSON with controlled values and
additional fields or padding. Measure any new sensor acquisition separately.
Afterward, validate the actual proposed telemetry fields with realistic values.

## Decision criteria

The acceptable energy increase and required benefit are **not yet specified**.
Set them before measurement. A decision requires repeatable absolute energy
values, variability, delivery success rates, and evidence that the additional
fields provide the intended benefit. Report an inconclusive result if the
difference cannot be resolved against measurement uncertainty and variability.

## Method and controls

Use the same board, supply voltage, firmware revision, MQTT topic, QoS, TLS
configuration, broker, publish frequency, and number of fixes per upload for A
and B. Use one message per upload to isolate payload size from backlog length.
Preserve GPS-first sequencing and the normal connect/disconnect lifecycle.

Record the board target, SDK version, firmware commit and local changes,
effective configuration, instrument/calibration, sample rate, carrier, LTE-M or
NB-IoT mode, signal readings when available, and network conditions for each run.
Keep secrets out of the recorded configuration.

Use QoS 1 for this planned comparison and verify the effective setting before
each run. Alternate A and B, with a proposed minimum of 30 attempted cycles per
alternative; increase repetitions if variability prevents a useful comparison.
Report failed cycles separately and include their energy in an operational-cost
summary. Document warm-up cycles and exclusions rather than silently dropping
them.

Separate retained LTE registration from fresh attachment, and confirmed PSM from
LTE-deactivation fallback. A change in those conditions can outweigh the payload
size difference and must not be attributed to extra JSON fields.

## Measurement boundaries

| Metric | Start | End | Scope |
| --- | --- | --- | --- |
| MQTT connect energy | `MQTT CONNECTION` | `MQTT CONNECTED` | Connection work, including TCP/TLS where applicable |
| Publish energy | `MQTT PUBLISH` | `MQTT PUBLISH ACKNOWLEDGED` | Publish through matching successful PUBACK |
| Total upload energy | `LTE CONNECTION` or `LTE CONNECTION RETAINED` | Confirmed PSM entry or `MODEM LTE DEACTIVATED` | LTE preparation, MQTT session, upload, cleanup, and low-power transition |

These are application-level phase boundaries, not packet-level radio airtime.
Publish energy is included in total upload energy; do not add the two metrics.
Missing end markers make a cycle incomplete and require explicit classification.
Keep energy spent acquiring a GNSS fix outside the total upload metric.

Align status and telemetry timestamps before integration. Host-receipt timestamps
may include buffering delay; document the alignment method and its uncertainty.
Do not equate a short API call with completion of physical radio transmission.

## Data and analysis

For each collection session, create `runs/run-<NNN>.md` with the evidence metadata
specified in the [template](../TEMPLATE.md). Preserve raw telemetry and status
logs at a stable archive location, recording SHA-256 checksums and exact time
ranges. Local monitor logs alone are not a durable archive.

Create `data/summary.csv` after collecting evidence. Suggested columns:

```text
run_id,cycle_id,variant,payload_bytes,qos,registration_mode,low_power_mode,success,connect_energy_mj,publish_energy_mj,upload_energy_mj,upload_duration_s,notes
```

Integrate measured voltage and current using a stated method, such as trapezoidal
integration. With voltage in V, current in mA, and time in seconds:

```text
energy_mj = integral(voltage_v * current_ma, time_s)
increase_percent = 100 * (mean_energy_b - mean_energy_a) / mean_energy_a
```

Compute the percentage separately for publish energy and total upload energy.
Report mean, standard deviation, sample counts, and instrument uncertainty.
Treat missing samples explicitly; a zero power field with nonzero voltage and
current requires validation before integration. If voltage and current are
validated, derive power from them and document that choice.

The existing local files `telemetry-2026-08-12.ndjson` and
`status-2026-08-12.ndjson` under the power monitor's `data/` directory are possible
inputs for exploratory analysis. They do not establish a controlled N-versus-2N
comparison. The inspected status sequence includes `MQTT PUBLISH COMPLETE`
rather than a PUBACK marker and reports PSM not granted; it cannot directly
validate this planned QoS 1 comparison.

## Results

| Alternative | Payload bytes | Publish energy | Total upload energy | Cycles |
| --- | --- | --- | --- | --- |
| A | N, to be selected | Not measured | Not measured | Not collected |
| B | 2N, to be selected | Not measured | Not measured | Not collected |

## Conclusion and decision

Pending measurement. No conclusion about the energy cost of additional fields
has been made. Link a new or updated ADR when evidence supports a design choice;
record accepted trade-offs, applicability, and conditions for retesting there.

## See also

- [Experiment index](../README.md)
- [Experiment template](../TEMPLATE.md)
- [Architectural decision records](../../adr/)
- [Hardware measurement checklist](../../hardware/power_measurement_checklist.md)
- [ESP32 Power Monitor](../../../myprojects/monitor-application/ESP32-power-monitor/README.md)
- [Tracker architecture](../../../myprojects/gps_mqtt_tracker/CODE_ARCHITECTURE.md)
