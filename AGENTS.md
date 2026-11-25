# Repository Instructions for AI Coding Agents

Read [`docs-obsidian/AI_CODING_GUIDE.md`](docs-obsidian/AI_CODING_GUIDE.md) before changing code.
It is the repository source of truth for dependencies, initialization order,
Kconfig ownership, build commands, and hardware-only verification boundaries.

## Required workflow

1. Before adding any file or code, inspect the existing folder structure and
   choose the correct location for the change. Create or adjust directories
   first when needed, keep related files together, and follow the application's
   existing layout and build/runtime requirements.
2. Inspect existing functions and features before adding code: check whether the
   task can be implemented inside an existing function first; if a new function
   would replace an old function or feature, evaluate adapting the old one
   first, and remove any superseded or unused functions, declarations, and build
   references.
3. Work within one Zephyr application directory at a time. Read that app's
   `CMakeLists.txt`, `Kconfig`, `prj.conf`, `sample.yaml`, and `src/main.c`.
4. Exercise folders may contain intentional gaps. Compare with the matching
   `_sol` folder.
5. Target nRF Connect SDK v3.3.0, pinned in `cell-fund.yml`.
6. Preserve modem initialization and asynchronous event/semaphore ordering.
7. Preserve GPS-first radio sequencing in tracker code.
8. Define new tunables in application `Kconfig`; select defaults in `prj.conf`.
9. Keep socket and MQTT buffers length-aware, check all return values, and clean
   up sockets/DNS allocations on errors.
10. Never commit real certificates, passwords, PSKs, or private keys.
11. Build the changed app with an allowed slash-qualified board target and
   report which hardware/network behavior remains untested.

Do not assume cJSON or an accelerometer library exists. The current MQTT tracker
uses `snprintf` for JSON, and no LIS2DH12 implementation is checked in.

## Language

Write all new or revised repository documentation and program text in English
only, including comments, docstrings, identifiers, log messages, and UI text.
Apply this rule to the content being changed; translating existing files is a
separate task. Conversation replies may use the user's preferred language.

## Experiments and decisions

When planning or recording a comparison of behavior, performance, power, or
design alternatives, read [`docs-obsidian/experiments/README.md`](docs-obsidian/experiments/README.md)
and use its template. Keep plans, reproducible evidence, and analysis in
`docs-obsidian/experiments/<NNNN>-<slug>/`. Record architectural decisions in `docs-obsidian/adr/`
with links to the supporting experiments. Keep implementation tickets in
`.scratch/` following the existing issue-tracker workflow.

Label planned, measured, simulated, and illustrative information explicitly.
Base measured conclusions on recorded runs and state the measurement scope,
units, variation, and limitations. A hypothetical percentage is not a result.

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/<feature-slug>/`. See
`docs-obsidian/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label string equal to its name. See
`docs-obsidian/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs-obsidian/adr/` at the repo root. See
`docs-obsidian/agents/domain.md`.
