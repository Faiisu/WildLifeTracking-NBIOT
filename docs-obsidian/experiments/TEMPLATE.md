[Back to project README](../../README.md) | [Experiment index](README.md)

# Experiment NNNN: Descriptive title

Copy the record below into `<NNNN>-<slug>/README.md`, replace the prompts, and
adjust relative links for the new location. Leave unmeasured results explicitly
marked as not measured. Remove this authoring paragraph from the copied record.

## Question and status

- Status: Planned / In progress / Completed / Inconclusive
- Evidence type: Measured / Simulated / External reference / Illustrative
- Owner and dates: Record the owner, dates, and timezone.
- Question: State the behavior or trade-off being evaluated.
- Decision supported: Identify the proposed choice or link an existing ADR.

## Alternatives

| Alternative | Changed variable | Expected benefit | Trade-off |
| --- | --- | --- | --- |
| A: baseline | Define the baseline | Describe the benefit | Describe the cost |
| B: candidate | Define the difference | Describe the benefit | Describe the cost |

## Decision criteria

Define acceptable cost, required functional benefit, evidence sufficiency, and
conditions requiring another test before measuring. Label provisional criteria
and resolve them before making an acceptance decision.

## Method and controls

Record hardware, firmware commit and dirty changes, SDK version, effective
configuration and overlays (with secrets redacted), equipment and calibration,
sample rate, voltage, channels, network mode/carrier, signal conditions, workload,
test order, repeat count, and handling of failures or missing samples.

Define start/end events for each metric. State whether timestamps describe API
calls, physical transmission, or host receipt, and how clocks are aligned.
Separate measurement boundaries that overlap rather than adding them twice.

## Runs and evidence

Create `runs/run-<NNN>.md` for each collection session. Record:

- Alternative, conditions, dates/timezone, firmware/configuration, and deviations.
- Raw-data path, durable archive location, SHA-256 checksum, and channel/units.
- Time range, clock alignment, missing samples, failures, and excluded cycles
  with reasons. Keep excluded evidence available.
- Analysis command, script revision, and generated summary/figure paths.

## Analysis

State units and formulas, integration method, sample-gap treatment, and uncertainty.
For a percentage change, identify the exact metric used as the denominator:

```text
change_percent = 100 * (candidate - baseline) / baseline
```

This formula requires a nonzero baseline. Report absolute values as well as the
percentage, with successful/failed counts and repeat-run variability.

## Results

| Alternative | Metric and units | Successful / attempted cycles | Mean | Variation | Evidence |
| --- | --- | --- | --- | --- | --- |
| A | Define metric | Not measured | Not measured | Not measured | None |
| B | Define metric | Not measured | Not measured | Not measured | None |

## Conclusion and limitations

State what the evidence supports, whether the criteria were met, and what remains
unknown. Describe the tested conditions and limits on generalizing the result.
An inconclusive comparison is a valid outcome.

## Decision and follow-up

Link the relevant ADR and implementation issues, or state that the decision is
pending. Define conditions that would trigger revisiting the comparison.

## See also

- [Experiment index](README.md)
- [Hardware measurement checklist](../hardware/power_measurement_checklist.md)
