[Back to application README](../README.md)

# Measuring energy by state

## Scope and evidence status

This is a **planned procedure**, not a measurement report. Its purpose is to
measure the energy and duration of each operation and the current during the
manual waits between operations. Record results in
[experiment 0004](../../../docs-obsidian/experiments/0004-state-control-power/README.md).
No per-state current, energy, savings percentage, or battery-life result has been
established by the build, flash, or host tests.

Read the [state machine](state-machine.md) before measuring. Separate operation
energy from waiting energy; waiting for the next button is not free and is not
necessarily a modem sleep state.

## Equipment and measurement boundary

1. Use SparkFun Thing Plus nRF9160 with the LTE/GNSS antennas and SIM required by
   the test. Record the firmware commit, dirty diff, NCS v3.3.0, board revision,
   and build target `sparkfun_thing_plus/nrf9160/ns`.
2. Put the current sensor in series with the tracker supply. Draw the actual
   wiring and identify the voltage measurement plane. State whether the result
   covers the whole board or a particular rail. Power the ESP32 monitor separately;
   its own consumption must not accidentally become tracker consumption.
3. Record supply voltage, shunt value, sensor configuration, calibration/zero
   checks, resolution, measurement uncertainty, and wiring losses. Use one intended
   supply path; document USB, battery, SWD, ground, and any back-power paths.
4. Preserve raw power samples and raw RTT output. Record channel and units.
   Prefer a sufficiently fast profiler for short LTE/TLS bursts. Use the existing
   [ESP32 + INA239 monitor](../../monitor-application/ESP32-power-monitor/README.md)
   for exploratory longer windows, subject to the limits below.

### Existing monitor limits

The ESP32 loop targets 250 ms (nominal 4 Hz). Host `received_at` is UTC serial
receipt time, not the instant of ADC conversion. Browser polling is not the sample
rate. Compute the actual interval distribution from saved samples; do not assume
that all samples are equally spaced or that the two channels are simultaneous.

Firmware serializes V, mA, and W. `power_w` is rounded to three decimal places
(0.001 W display increments), so low-power samples can round to zero. Current is
also rounded to three decimal places in mA; these are formatting increments, not
claims of sensor accuracy. Changing the graph to micro-units does not increase
resolution. A zero/missing reading is not proof of zero consumption.

At 4 Hz, short operations may contain too few samples to resolve energy or LTE
peaks. Report only sampled maxima, not true peak current. Mark such windows
unresolved, aggregate repeated cycles with stated assumptions, or collect a new
run with a suitable higher-rate instrument. Do not claim sub-millisecond boundary
accuracy or pure PSM current from this setup alone.

## Firmware profiles

| Profile | Configuration and use | Interpretation |
| --- | --- | --- |
| Eight-state baseline | Current defaults: RTT/GPS diagnostics, heartbeat except LOW_POWER | Instrumented GPS/FIX/LTE/PSM/MQTT/LOW_POWER cycle |
| Reduced-observer candidate | Planned separate overlay: `CONFIG_STARTUP_LED=n`, `CONFIG_ENABLE_LEDS=n`, `CONFIG_TRACKER_GNSS_DIAGNOSTICS=n`; retain transition logs | Quantify observer overhead in the same eight-state cycle |
| Deep-sleep candidate | Not implemented: modem shutdown or System OFF with verified wake/reinitialization | Separate future experiment |

Do not mix the earlier five-state/no-MQTT or ten-state/backlog firmware with
this baseline: active modules and workload differ. Record the effective build `.config` and dirty diff
for each run. Record TLS, QoS, payload bytes and redacted broker/config identity. Credentials
and certificates remain private deployment assets, not measurement evidence.

## Define measurement windows

RTT device uptime is the software operation clock. Logs are deferred/asynchronous,
not physical RF edges. Each `ENTER: X` starts work for the current state; `HOLD: X`
follows successful entry. A failure marker ends an unsuccessful attempt, and the
next MODE retries that same state. Keep repeats and failure windows distinct.

| Window | Start | End | Interpretation |
| --- | --- | --- | --- |
| GPS acquisition | `ENTER: GPS` | Session completion marker before GNSS stop | LTE-off preparation, continuous GNSS and diagnostics |
| GPS stop/transition | Session completion marker | `ENTER: FIX` after the ending press | GNSS stop plus any timeout-to-MODE wait; split the wait separately |
| FIX display | `ENTER: FIX` | `HOLD: FIX` | Local JSON/log formatting; no packet transmission |
| FIX hold | `HOLD: FIX` plus settling | Next `ENTER: LTE_NO_PSM` | GNSS/LTE off, heartbeat active |
| LTE without PSM entry | `ENTER: LTE_NO_PSM` | `HOLD: LTE_NO_PSM` or failure marker | LTE activation and registration with PSM request disabled |
| LTE without PSM hold | `HOLD: LTE_NO_PSM` plus settling | `ENTER: LTE_PSM` | Registered LTE with no PSM request |
| LTE with PSM entry | `ENTER: LTE_PSM` | `HOLD: LTE_PSM` or failure marker | LTE deactivation, fresh registration, PSM sleep wait |
| LTE with PSM hold | `HOLD: LTE_PSM` plus settling | `ENTER: MQTT_CONNECT` | Confirmed sleep, refused request, or later wake; classify each |
| MQTT connect entry | `ENTER: MQTT_CONNECT` | `HOLD: MQTT_CONNECT` or failure marker | Old-attempt cleanup, LTE readiness, DNS/TCP/TLS and CONNACK |
| MQTT connected hold | `HOLD: MQTT_CONNECT` plus settling | `ENTER: MQTT_SENDING` | Open socket, keepalive and modem activity |
| MQTT send entry | `ENTER: MQTT_SENDING` | `HOLD: MQTT_SENDING` or failure marker | One current report plus PUBACK; retry includes reconnection |
| MQTT sent hold | `HOLD: MQTT_SENDING` plus settling | `ENTER: MQTT_DISCONNECT` | Socket retained, post-transmission tail and helper activity |
| MQTT disconnect entry | `ENTER: MQTT_DISCONNECT` | `HOLD: MQTT_DISCONNECT` or failure marker | Disconnect and callback wait |
| MQTT disconnected hold | `HOLD: MQTT_DISCONNECT` plus settling | `ENTER: LOW_POWER` | Socket closed; registered LTE retained |
| LOW_POWER entry | `ENTER: LOW_POWER` | `LOW_POWER:` | Stop pending GNSS, LTE off, PSM request disabled and LED cancellation |
| LOW_POWER hold | `LOW_POWER:` plus settling | Next `ENTER: GPS` | Radio-off System ON wait with LED off |

The default monitor may not resolve FIX display or short transitions. Record them
as unresolved instead of assigning invented energy. A planned hold protocol is
5 s settling plus 30 s observation, subject to instrument/network suitability;
record actual durations. For GPS, use a predetermined manual duration (planned
example: 60 s), or classify the separate 180 s timeout condition.

`LTE connected - [PSM enabled]` indicates a grant. `PSM sleeping` confirms entry;
`PSM awake` identifies a subsequent exit. LTE_PSM timeout/refusal does not switch
to radio-off fallback; that state keeps LTE until MODE starts MQTT_CONNECT. Label
confirmed sleep, unconfirmed request, and awake portions separately. Never report
all LTE_PSM hold energy as uninterrupted PSM by name alone.

FIX and LOW_POWER both have stopped GNSS and deactivated LTE, but differ in LED
work and entry history. A matched comparison may characterize this difference,
subject to supply/observer/history controls; it is not a PSM-versus-System OFF
comparison. MQTT connect/send/disconnect windows belong to this eight-state profile.
Confirmed PSM does not imply continued sleep during socket traffic.

## Clock alignment

Record the RTT uptime epoch and the monitor time base separately. Capture at least
two observable synchronization anchors, their host timestamps, and the uncertainty
of observing/receiving them. A button press may help alignment, but it is not a
calibrated GPIO timing marker. Record offset/drift and convert window boundaries
onto the measurement clock before integration. Host receipt latency, log delivery,
manual timing, and sample interval contribute to boundary uncertainty.

If synchronization is insufficient for a short operation, report that operation's
energy as unresolved. Do not splice device uptime and UTC samples without alignment.
No automatic state-to-power synchronization is implemented by this application.

## Collection procedure

1. Copy the [run record](../../../docs-obsidian/experiments/0004-state-control-power/runs/RUN_TEMPLATE.md)
   to `run-001.md`. Fill in equipment, firmware, wiring, sampling, clock alignment,
   test order and chosen durations. Archive raw power and RTT logs with checksums.
2. Start power logging and RTT capture before reset. GPS starts automatically after
   initialization; record startup separately and identify `ENTER: GPS`.
3. Press MODE after the predetermined GPS duration to stop and display FIX. Record
   actual duration and valid/null outcome. If acquisition times out at 180 s, record
   the stopped-GPS wait and press MODE to advance; timeout alone does not advance.
4. Observe FIX for the chosen hold window. Press MODE to enter LTE_NO_PSM. Record
   entry energy, registration mode/outcome, then the registered no-PSM hold.
5. Press MODE to enter LTE_PSM. Record fresh registration energy and grant/sleep
   outcome. Observe the hold and separate `PSM awake` intervals. If entry is
   unconfirmed, preserve the failure classification while LTE remains active.
6. Press MODE for MQTT_CONNECT, then again for MQTT_SENDING, then MQTT_DISCONNECT.
   Record each operation and hold, the exact payload size, QoS, CONNACK/PUBACK,
   and external broker receipt. Send failures retry the same report on MODE and
   include reconnection energy; classify first attempts and retries separately.
7. Press MODE for LOW_POWER. Verify LED off and record its entry and hold. Press
   MODE again to start GPS immediately and begin the next cycle.
8. Repeat. A planned minimum is five attempted cycles per configuration/condition;
   increase repetitions when uncertainty/variation prevents interpretation. Keep
   first-boot/cold and warm acquisition separate. Keep errors and MODE retries with
   their energy and reasons rather than excluding them silently.

Record antennas, sky view, motion, temperature, carrier/network mode, home/roaming
and signal conditions. A null FIX is later transmitted as null JSON; it differs in payload length from
a valid fix. Control or stratify payload size in comparisons. All eight normal
transitions require MODE; busy registration/setup presses do not
queue. Do not press to skip a failed state: MODE retries it.

## Analysis and units

For irregular timestamps and validated power at the chosen boundary:

```text
P_mW = V * I_mA
E_mJ = sum((P_mW[i] + P_mW[i+1]) / 2 * (t_s[i+1] - t_s[i]))
E_mWh = E_mJ / 3600
I_avg_mA = integral(I_mA dt) / duration_s
P_avg_mW = E_mJ / duration_s
```

Use voltage and current from the same measurement plane. For the INA239 board-side
boundary, use the validated `load_v` and `current_ma`; `battery_v` includes a
different plane relative to the shunt. If deriving power rather than using the
rounded `power_w`, record the reason, calibration, rounding and uncertainty.
Do not treat missing/null/error samples as zero. Define a gap threshold from the
observed cadence; reject affected short windows or state the missing-energy bound.

Report duration, sample count, coverage, energy, average current/power, sampled
maximum, success/attempt counts, and variation (mean, standard deviation, range).
Keep instrument uncertainty separate from repeat-run variation. Keep operation and
hold windows disjoint when summing total cycle energy; a PSM hold nested in a cycle
must not also be added twice. Any baseline subtraction is a separate derived metric.

For a comparison, report absolute values first. Percentage change uses
`100 * (candidate - baseline) / baseline` and requires a nonzero, resolved baseline.
Do not infer battery life from isolated LOW_POWER current; real duty-cycle energy,
network behavior, failed attempts, supply conversion, and usable battery capacity
are separate inputs.

## See also

- [State machine and code map](state-machine.md)
- [Experiment plan and results status](../../../docs-obsidian/experiments/0004-state-control-power/README.md)
- [Existing hardware guidance](../../../docs-obsidian/hardware/power_measurement_checklist.md)
- [ESP32 monitor setup](../../monitor-application/ESP32-power-monitor/README.md)
