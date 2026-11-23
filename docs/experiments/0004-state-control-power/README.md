[Back to project README](../../../README.md) | [Experiment index](../README.md)

# Experiment 0004: Energy by tracker state

## Question and status

- Status: **Planned**. No controlled power runs are recorded here.
- Evidence type: planned protocol based on checked-in code; no measured or simulated
  energy results. Example durations are protocol choices, not observations.
- Owner and dates: operator to be recorded per run; plan created 2026-10-13,
  Asia/Bangkok (UTC+07:00). Raw monitor host timestamps use UTC.
- Question: what energy does each entry operation consume, and what current is
  observed in GPS, FIX, LTE_NO_PSM, LTE_PSM, MQTT_CONNECT, MQTT_SENDING,
  MQTT_DISCONNECT and LOW_POWER under matched conditions?
- Decision supported: establish a state energy budget and decide whether deeper
  sleep is worth its reinitialization and wake-up costs. No deeper mode is selected
  by this experiment yet.

## Alternatives

| Alternative | Changed variable | Expected benefit (unmeasured) | Trade-off |
| --- | --- | --- | --- |
| A: eight-state baseline | Current eight-state firmware; LED off in LOW_POWER | Separate GPS/FIX/LTE-policy/MQTT/LOW_POWER costs | Logging and LEDs contribute outside LOW_POWER |
| B: reduced observer | Planned overlay disabling all tracker LEDs and GNSS diagnostics; retain transition RTT | Measure observer overhead under the same eight-state workload | Reduced diagnostics |
| Historical reference | `db5c12d` ten-state MQTT firmware; heartbeat in LOW_POWER | Existing code reference only | Different build/workload; not a matched A/B baseline |

System OFF and modem-library shutdown are follow-up alternatives, not implemented
by A. The eight-state build sends one current-session report over TLS/MQTT; it does
not drain a backlog of previous cycles. Separate valid/null FIX,
network mode, PSM granted/refused/awake, cold/warm GNSS and failed attempts.

## Decision criteria

**Planned criteria:** collect at least five attempted cycles per matched condition,
retain all failures, and include raw evidence plus clock-alignment and uncertainty
records. Use longer/more runs or a faster instrument when short windows or small
currents cannot be resolved. A fixed percentage-saving target is not specified.
Do not claim a saving when it is unresolved against instrument uncertainty,
rounding, sample coverage, boundary uncertainty, and repeat-run variability.

Functional acceptance for A requires a dark LOW_POWER LED, no recurring LED work
in that mode, MODE wake-up, heartbeat recovery outside LOW_POWER, and the requested
GPS -> FIX -> LTE_NO_PSM -> LTE_PSM -> MQTT_CONNECT -> MQTT_SENDING ->
MQTT_DISCONNECT -> LOW_POWER -> GPS order. Host tests and a build can support software checks but do not
prove electrical savings or real button/radio behavior.

## Method and controls

Follow the [power test procedure](../../../myprojects/gps_mqtt_stateControl/docs/power-testing.md)
and [state-machine reference](../../../myprojects/gps_mqtt_stateControl/docs/state-machine.md).
Use the same board, voltage plane, supply, instrument configuration/calibration,
SIM, antenna placement, network conditions, and manual hold schedule for paired
alternatives. Capture actual GNSS session duration and valid/null FIX outcome;
FIX JSON is displayed and later transmitted in MQTT_SENDING. Record exact payload
bytes, QoS, TLS configuration, broker identity (redacted), and delivery outcome.
Compare one report per successful cycle; a retry includes MQTT reconnection.

Use NCS v3.3.0 and `sparkfun_thing_plus/nrf9160/ns`. Record the commit, dirty diff,
firmware SHA-256, redacted effective `.config`, board revision, shunt, voltage,
sample cadence, channels, calibration, home/roaming and LTE-M/NB-IoT selection,
sky view, temperature, motion, test order, repeats, missing samples, and failures.
A planned matched schedule is a 60 s GPS session and 5 s settling plus 30 s hold
windows where appropriate. Record actual durations; adapt and version this schedule
before collection if network or instrument conditions require it.

Separate `ENTER`-to-`HOLD` operation windows from holds; split stopped-GPS waiting
after its 180 s timeout from active acquisition. GPS starts at boot and after
LOW_POWER wake; only MODE advances to FIX. LTE_PSM entry includes fresh registration
and the sleep wait, so it is not steady PSM energy. PSM refusal/timeout retains LTE
until MQTT_CONNECT; socket traffic may wake the modem even after confirmed PSM. Sleep/wake events are logged, but confirmed entry does
not prove uninterrupted sleep throughout a hold. LOW_POWER disables LTE/request
and LED work; MODE restarts GPS directly. FIX and LOW_POWER holds can assess a
heartbeat/history difference with appropriate controls.

The ESP32 monitor targets 4 Hz and serializes power rounded to 0.001 W. Use its raw
voltage/current with documented measurement-plane and rounding limits when suitable.
Do not claim true burst peaks or resolved sub-mW sleep power from graph scaling.
Use a faster/more suitable instrument or report an unresolved result if needed.

Align RTT device uptime and monitor host UTC receipt time with recorded anchors
and uncertainty. Neither RTT markers nor serial receipt timestamps are physical RF
edges. No automatic synchronized state marker is implemented.

## Runs and evidence

There are **no measured runs** yet. [RUN_TEMPLATE.md](runs/RUN_TEMPLATE.md) is a form,
not evidence. Copy it to `runs/run-001.md` when a real collection starts. Record
raw power and RTT paths, durable archives, SHA-256 checksums, channel/units, exact
selected time ranges, clock alignment, missing frames, failures, exclusions, and
the analysis command/revision. Preserve logs before local rotation; an ignored
monitor path alone is not durable evidence. Sanitize private identifiers and
locations in committed artifacts without changing the measurement units/timing.

Create derived `data/summary.csv` only after collecting evidence. Suggested columns:

```text
run_id,cycle_id,profile,window_type,state,outcome,fix_valid,stop_reason,payload_bytes,qos,delivery_attempt,registration_mode,psm_outcome,start_utc,end_utc,duration_s,sample_count,coverage_fraction,energy_mj,average_current_ma,average_power_mw,sampled_max_current_ma,uncertainty_notes
```

`window_type` distinguishes operation, hold and stopped-GPS timeout wait. `outcome`
distinguishes valid/null FIX, registration failure/retry, PSM confirmation/refusal/
wake and radio-off LOW_POWER. The `fix_valid` and `stop_reason` columns describe the GPS outcome; payload bytes/QoS and attempt count describe the later upload. There is no backlog
loop in this profile.

## Analysis

Use validated voltage/current or power at a stated boundary. Apply trapezoidal
integration to actual sample timestamps:

```text
power_mw = voltage_v * current_ma
energy_mj = sum((power_mw[i] + power_mw[i+1]) / 2 * delta_time_s)
energy_mwh = energy_mj / 3600
change_percent = 100 * (candidate - baseline) / baseline
```

Define gap handling and reject unresolved windows rather than filling gaps with
zeros. Report absolute duration, energy and current, sample coverage, successful /
attempted counts, mean, standard deviation, and range. Report instrument and
boundary uncertainty separately. A percentage requires a resolved nonzero baseline.
Do not double-count holds when totaling cycle energy. Report energy per completed
cycle and valid GPS fix separately; a null FIX is not a valid GPS fix, and no
measurement can imply a successful delivery without PUBACK/broker evidence. Report
energy per delivered report separately from energy per valid GPS fix.

## Results

| Alternative | Metric and units | Successful / attempted cycles | Mean | Variation | Evidence |
| --- | --- | --- | --- | --- | --- |
| A | Eight-state entry energy (mJ), duration (s) | Not measured | Not measured | Not measured | None |
| B | Eight-state entry energy (mJ), duration (s) | Not measured | Not measured | Not measured | None |
| A | LOW_POWER hold current (mA), power (mW) | Not measured | Not measured | Not measured | None |
| B | LOW_POWER hold current (mA), power (mW) | Not measured | Not measured | Not measured | None |
| A versus B | Observer overhead across matched windows | Not measured | Not measured | Not measured | None |

## Conclusion and limitations

Pending measurement. LED cancellation is implemented in A; no electrical saving
has been quantified. Whole-board results include supply conversion and observers.
Manual timing, retained radio activity, network negotiation, sampling and
rounding can dominate apparent differences. Build/flash success does not establish
power accuracy, network policy, or battery life. LOW_POWER is a System ON wait,
not System OFF, modem shutdown, or a guaranteed modem-only sleep measurement.

## Decision and follow-up

No energy-based architectural decision is recorded. After sufficient evidence,
link an ADR in `docs/adr/` if selecting modem shutdown, System OFF, or another
architectural sleep policy. Revisit measurements after changes in firmware,
logging, LED behavior, board/supply, network policy, state sequence, or instrument.

## See also

- [Application README](../../../myprojects/gps_mqtt_stateControl/README.md)
- [Power test procedure](../../../myprojects/gps_mqtt_stateControl/docs/power-testing.md)
- [Run form](runs/RUN_TEMPLATE.md)
- [Experiment template](../TEMPLATE.md)
- [Existing hardware guidance](../../hardware/power_measurement_checklist.md)
- [ESP32 monitor](../../../myprojects/monitor-application/ESP32-power-monitor/README.md)
