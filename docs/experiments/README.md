[Back to project README](../../README.md)

# Experiments

Record questions, comparison methods, reproducible evidence, and analysis here.
Use [architectural decision records](../adr/) to capture the resulting design
choice and its trade-offs. An experiment may finish without a design decision.

## Index

| ID | Question | Status | Evidence | Decision |
| --- | --- | --- | --- | --- |
| [0001](0001-mqtt-payload-size/README.md) | How does doubling an MQTT payload affect energy? | Planned | No controlled measurements recorded | Pending |
| [0002](0002-gnss-continuous-baseline/README.md) | Does continuous GNSS fix where the tracker reports Using: 0? | In progress | Build passed; local flash and RTT acquisition pending | Pending |

## Structure

```text
docs/experiments/
├── README.md
├── TEMPLATE.md
└── <NNNN>-<slug>/
    ├── README.md          # Question, method, results, and conclusion
    ├── runs/              # Conditions and evidence for each run
    ├── data/              # Derived summaries with explicit units
    ├── figures/           # Plots generated from recorded evidence
    └── scripts/           # Reproducible analysis, when needed
```

Assign the next unused four-digit ID and a descriptive lowercase slug. Start
with one `README.md` using the [template](TEMPLATE.md). Create supporting
directories only when they contain real artifacts; the tree above is a layout
convention, not a requirement to create empty directories.

## Workflow

1. Define the question, alternatives, measurement boundaries, and decision
   criteria before collecting evidence.
2. Record controlled runs, including firmware revision, effective configuration,
   equipment, environment, and raw-data references.
3. Analyze repeat measurements, failed runs, variability, and limitations.
4. Mark the experiment `Completed` when the planned comparison has sufficient
   evidence, or `Inconclusive` when it cannot resolve the question. Use `Planned`
   and `In progress` for unfinished work.
5. Link the experiment from a relevant ADR when recording an architectural
   decision, and link back to the ADR from the experiment. A completed experiment
   alone does not mean that an alternative has been accepted.
6. Update the index when status or the linked decision changes.

Label evidence as **Measured**, **Simulated**, **External reference**, or
**Illustrative**. Planned values and illustrative percentages must stay separate
from results. Define the denominator for every reported percentage and distinguish
measurement uncertainty from repeat-run variability.

## Evidence storage

Commit reports, small derived CSV summaries, analysis scripts, and useful plots.
Keep credentials and private device identifiers out of these artifacts.

The power monitor's `data/` directory contains local runtime state. For raw logs,
record the source path, a durable archive location, SHA-256 checksum, channel,
units, and the exact time range used by the analysis in `runs/run-<NNN>.md`.
Preserve a stable copy before logs are rotated or overwritten. Small, sanitized
raw datasets may also be stored under the experiment's `data/` directory.
Do not treat an ignored local path alone as durable evidence.

Use [hardware guidance](../hardware/power_measurement_checklist.md) for shared
measurement procedures and [.scratch issue conventions](../agents/issue-tracker.md)
for implementation work. Record changed test conditions as a new run; preserve
earlier evidence. Link a later experiment or superseding ADR when revisiting a
conclusion.

## See also

- [Experiment template](TEMPLATE.md)
- [MQTT payload size experiment](0001-mqtt-payload-size/README.md)
- [ESP32 Power Monitor](../../myprojects/monitor-application/ESP32-power-monitor/README.md)
- [Domain documentation conventions](../agents/domain.md)
