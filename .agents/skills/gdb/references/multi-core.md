# Multi-core GDB Debugging

Read this before connecting to a multi-core Nordic target.

## Multi-core targets

One server can hold **multiple core sessions at once**, keyed by a `core` name
(`app`, `net`, …). Connect the first core (via `--autostart`/`--core` or `POST
/connect`), then connect additional cores at runtime with a **distinct `core`
name and its own `gdb_server_port`** (each core is served by its own GDB server,
e.g. a second `west debugserver` for the network core with its own build dir).
Every inspection/execution endpoint then accepts a `core` selector (`?core=net`
on GET, `"core": "net"` in POST bodies); with a single session it's optional.
`GET /sessions` lists all connected cores. The critical thing is that a single
core may be **Arm** or **RISC-V**, and that choice sets the GDB binary and the
`/fault` decoder.

Concurrent two-core example (nRF5340 app + net — hardware-validated):

```bash
# App core (autostarted by the server)
uv run scripts/gdb_server.py serve --build-dir build_app --core app \
  --gdb-server-port 2331 --rtt-port 19021 --dev-id <serial> \
  --west-topdir <ncs> --autostart --attach --port-num 8090
# Add the network core to the same server (its own build dir + ports)
# POST /connect {"core": "net", "build_dir": "build_net", "gdb_server_port": 2341,
#                "rtt_port": 19022, "dev_id": "<serial>", "west_topdir": "<ncs>",
#                "attach": true}
# Then target a core per call:
#   GET  /backtrace?core=net
#   GET  /threads?core=app
#   POST /interrupt {"core": "net"}
```

Multi-core gotchas (learned on hardware):

- **Space the per-core GDB ports.** Each JLinkGDBServer also reserves
  `gdb_port+1` (SWO) and `gdb_port+2` (Terminal I/O). Two cores 1 apart collide —
  use e.g. app `2331` and net `2341`. Give each core a distinct `rtt_port` too
  (19021, 19022, …).
- **Pass `dev_id`** (the J-Link serial) on every core so both GDB servers bind
  the same physical probe.
- **The net/second core must be running to enumerate threads.** On the nRF5340
  the application core releases the network core
  (`nrf_reset_network_force_off(NRF_RESET, false)`, or a BLE/IPC init that does
  it); the net image must already be flashed when the app boots.
- **nRF53 ships access-protected** — flashing the net core the first time needs a
  `west flash --recover` (mass-erases both cores). Ask the user before recovering.
- The first attach on a multi-core SoC can exceed the normal MI timeout
  (CoreSight bring-up, post-recover); the session allows extra time for the
  initial `target-select`.

> nRF54L note: the app core and the FLPR share the probe and **cannot** be
> debugged simultaneously (see below) — connect them one at a time even though
> the registry would otherwise allow concurrent cores.

### Pick the arch per core (auto-detected)

Each core is either **Arm Cortex-M33** (`arm-zephyr-eabi-gdb`, Cortex-M `/fault`
via SCB CFSR/HFSR/BFAR/MMFAR) or a **Nordic VPR RISC-V** core
(`riscv64-zephyr-elf-gdb`, `/fault` via `mcause`/`mepc`/`mtval`).

You normally do **not** need to specify this: the server auto-detects the
architecture from the core's build artifact (the ELF `e_machine` field) and
selects the matching GDB automatically. This is deliberately device-table-free,
so **new Nordic Arm or RISC-V cores work with no code or config change**. Use
`--arch arm|riscv` only to override the detection. The device/board lists in
this document are **illustrative examples, not an exhaustive or authoritative
list** — resolve board targets and per-core details via
`nordicsemi_search_sources`.

### nRF5340 — homogeneous Arm

Two Cortex-M33 cores: application (`cpuapp`) and network (`cpunet`). Both use the
Arm GDB and can be debugged **concurrently** from one server — autostart `app`,
then `POST /connect` the `net` core with its own build dir + spaced ports (see
the concurrent example and gotchas above). Hardware-validated: both cores'
threads enumerate independently and per-core breakpoints work.

### nRF54H20 — heterogeneous (Arm + RISC-V)

Multiple cores of **both** architectures:

- `cpuapp` (Cortex-M33, application) and `cpurad` (Cortex-M33, radio) — Arm GDB.
- `cpuppr` (VPR RISC-V, Peripheral Processor) and `cpuflpr` (VPR RISC-V, Fast
  Lightweight Processor) — use `--arch riscv`.
- The Secure Domain and System Controller are Nordic-managed and not
  user-debuggable.

For early-boot/reset debugging, keep cores halted while attaching:

```bash
# Halt-friendly reset, then release a specific core, then attach
uv run scripts/gdb_server.py list
# POST /reset {"reset_kind": "RESET_VIA_SECDOM"}
# POST /go    {"core": "Network"}
# then connect a session with {"attach": true}
```

Use `RESET_VIA_SECDOM` for the nRF54H-series; for nRF52/nRF53 use the device
family default (resolve it rather than guessing).

### nRF54L15 — heterogeneous (Arm + RISC-V), exclusive sessions

An Arm Cortex-M33 application core (`cpuapp`) plus one Nordic VPR RISC-V core,
the FLPR (`cpuflpr`, runs from SRAM by default):

- **RISC-V FLPR:** point `--build-dir` at the FLPR build — the arch (and thus
  the RISC-V GDB) is auto-detected from its ELF; `--arch riscv` only if needed.
- **Attach to the running FLPR.** The app core bootstraps/starts it, so let the
  system run, then connect with `{"attach": true}` and select the FLPR domain.
  Ensure `CONFIG_DEBUG_OPTIMIZATIONS=y` in **both** images' `prj.conf`.
- **Sessions are mutually exclusive on nRF54L** — you cannot debug `cpuapp` and
  the FLPR at the same time. The app-core code must tolerate the FLPR halting.
