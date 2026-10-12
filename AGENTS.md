# Repository Instructions for AI Coding Agents

Read [`docs/AI_CODING_GUIDE.md`](docs/AI_CODING_GUIDE.md) before changing code.
It is the repository source of truth for dependencies, initialization order,
Kconfig ownership, build commands, and hardware-only verification boundaries.

## Required workflow

1. Before adding any file or code, inspect the existing folder structure and
   choose the correct location for the change. Create or adjust directories
   first when needed, keep related files together, and follow the application's
   existing layout and build/runtime requirements.
2. Work within one Zephyr application directory at a time. Read that app's
   `CMakeLists.txt`, `Kconfig`, `prj.conf`, `sample.yaml`, and `src/main.c`.
3. Exercise folders may contain intentional gaps. Compare with the matching
   `_sol` folder.
4. Target nRF Connect SDK v3.3.0, pinned in `cell-fund.yml`.
5. Preserve modem initialization and asynchronous event/semaphore ordering.
6. Preserve GPS-first radio sequencing in tracker code.
7. Define new tunables in application `Kconfig`; select defaults in `prj.conf`.
8. Keep socket and MQTT buffers length-aware, check all return values, and clean
   up sockets/DNS allocations on errors.
9. Never commit real certificates, passwords, PSKs, or private keys.
10. Build the changed app with an allowed slash-qualified board target and report
   which hardware/network behavior remains untested.

Do not assume cJSON or an accelerometer library exists. The current MQTT tracker
uses `snprintf` for JSON, and no LIS2DH12 implementation is checked in.

## Language

Write all new or revised repository documentation and program text in English
only, including comments, docstrings, identifiers, log messages, and UI text.
Apply this rule to the content being changed; translating existing files is a
separate task. Conversation replies may use the user's preferred language.

## Experiments and decisions

When planning or recording a comparison of behavior, performance, power, or
design alternatives, read [`docs/experiments/README.md`](docs/experiments/README.md)
and use its template. Keep plans, reproducible evidence, and analysis in
`docs/experiments/<NNNN>-<slug>/`. Record architectural decisions in `docs/adr/`
with links to the supporting experiments. Keep implementation tickets in
`.scratch/` following the existing issue-tracker workflow.

Label planned, measured, simulated, and illustrative information explicitly.
Base measured conclusions on recorded runs and state the measurement scope,
units, variation, and limitations. A hypothetical percentage is not a result.

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/<feature-slug>/`. See
`docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label string equal to its name. See
`docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See
`docs/agents/domain.md`.
