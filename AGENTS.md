# Repository Instructions for AI Coding Agents

Read [`docs/AI_CODING_GUIDE.md`](docs/AI_CODING_GUIDE.md) before changing code.
It is the repository source of truth for dependencies, initialization order,
Kconfig ownership, build commands, and hardware-only verification boundaries.

## Required workflow

1. Work within one Zephyr application directory at a time. Read that app's
   `CMakeLists.txt`, `Kconfig`, `prj.conf`, `sample.yaml`, and `src/main.c`.
2. Exercise folders may contain intentional gaps. Compare with the matching
   `_sol` folder; prefer `l4_MQTT/` over the duplicate `l4/` tree.
3. Target nRF Connect SDK v3.3.0, pinned in `cell-fund.yml`.
4. Preserve modem initialization and asynchronous event/semaphore ordering.
5. Preserve GPS-first radio sequencing in tracker code.
6. Define new tunables in application `Kconfig`; select defaults in `prj.conf`.
7. Keep socket and MQTT buffers length-aware, check all return values, and clean
   up sockets/DNS allocations on errors.
8. Never commit real certificates, passwords, PSKs, or private keys.
9. Build the changed app with an allowed slash-qualified board target and report
   which hardware/network behavior remains untested.

Do not assume cJSON or an accelerometer library exists. The current MQTT tracker
uses `snprintf` for JSON, and no LIS2DH12 implementation is checked in.

