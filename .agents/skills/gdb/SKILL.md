---
name: gdb
description: >-
  Debug an nRF Connect SDK (NCS) / Zephyr application on Nordic Semiconductor
  hardware with GDB over a J-Link probe. Use when the user asks to debug, set a
  breakpoint or watchpoint, step through code, inspect variables/registers/memory,
  attach a debugger to a running core, or analyze a crash, fault, hang, or
  assert. Drives the current NCS toolchain (`west` + `nrfutil` +
  `arm-zephyr-eabi-gdb`) through GDB's Machine Interface, NOT the legacy nRF5 SDK
  / `nrfjprog` / `arm-none-eabi-gdb` flow.
metadata:
  title: "NCS GDB Debugger"
  version: "1.0.2"
  tags: "gdb, debug, zephyr, ncs, jlink, troubleshooting"
---

# NCS GDB Debugger

This skill drives a GDB debug session against a Nordic Semiconductor target
using the **current nRF Connect SDK (NCS) / Zephyr toolchain**: `west` brings up
the J-Link GDB server, `arm-zephyr-eabi-gdb` (Zephyr SDK) is the client, and
`nrfutil device` performs device operations. It explicitly does **not** use the
legacy nRF5 SDK / `nrfjprog` / `arm-none-eabi-gdb` flow.

The backend is a single-file Python program, `scripts/gdb_server.py`, located in
the folder next to this skill. It drives GDB through its **Machine Interface**
(`--interpreter=mi2`) via `pygdbmi` (so responses are structured, not scraped
text) and exposes a **local HTTP server**, which is the primary interface you
should use. Keep `gdb_server.py` and this `SKILL.md` together when sharing the
skill.

## How to respond to a debug request

Before doing anything, decide which outcome the user wants. This choice
determines the entire flow.

1. **Scripted one-shot analysis (default).** The user wants to know *why*
   something happens (a crash, fault, hang, assert, or "what is the value of X
   here?"). Connect, set the relevant breakpoint(s) or run to the fault, collect
   state, and return a concise **textual analysis** (see
   [Textual analysis](#textual-analysis-default)). Do not hand an interactive
   prompt back to the user.
2. **Interactive live session.** The user wants to drive the debugger
   themselves ("let me step through", "stop at main and let me look around").
   Connect, set breakpoints, and relay control back and forth: run each
   execution/inspection step they ask for and report the resulting stop and
   state.
3. **Attach only.** The user wants to attach to an already-running target
   without resetting or reflashing it (equivalent to `west attach`). Connect
   with `attach: true` and do not flash or reset.

When in doubt, default to the scripted textual analysis and offer to drop into
an interactive session as a follow-up.

## Prerequisites

- A Nordic DK/target connected over its J-Link USB port (shows the `jlink`
  trait in `nrfutil device list`).
- An **NCS toolchain environment** providing `west` and `arm-zephyr-eabi-gdb`.
  On a typical NCS install these are **not** on the ambient shell `PATH` — they
  live inside the toolchain and are only visible through
  `nrfutil sdk-manager toolchain launch --ncs-version=<ver> -- <command>` (see
  [Running inside the NCS toolchain environment](#running-inside-the-ncs-toolchain-environment-important)).
  Verify with `nrfutil sdk-manager toolchain launch --ncs-version=<ver> -- \
  west --version`, **not** a bare `which west` — a bare check will usually
  report them "missing" even when the toolchain is installed. `nrfutil` itself
  is on the ambient `PATH`. If `nrfutil` or the toolchain is genuinely missing,
  stop and recommend running the NCS **setup** workflow first.
- `uv` installed (the script uses PEP 723 inline metadata and pulls `pygdbmi`
  automatically; no virtualenv setup needed).
- A **debug-friendly build**. `CONFIG_DEBUG_OPTIMIZATIONS=y` sets the `-Og`
  optimization level (it improves stepping and locals but is not what produces
  the symbol table), and `CONFIG_DEBUG_THREAD_INFO=y` enables thread-aware
  debugging. Without these, breakpoints and variable inspection *may behave
  unexpectedly* (a release / size-optimized image inlines and reorders code, so
  breakpoints, backtraces, and locals can be missing or misleading). Offer to
  rebuild with these options. Note that missing symbols or "no source" is
  usually a symbol/ELF-path mismatch (see the ELF-path note below), not the
  optimization level.

## Resolving targets — never guess

Never guess the board target, SoC, canonical board name, PCA number, serial
number, or reset kind. Resolve them with `nordicsemi_search_sources` (or ask the
user). This matters for multi-core devices and for the correct reset kind (see
below).

## Primary interface: the HTTP server

Always interact with GDB through the HTTP server. Use the path to the
`gdb_server.py` copy that ships with this skill (`scripts/gdb_server.py` relative
to this file).

### 1. Check for an already-running server

Before starting, always probe the server first — a previous session may have
left it running (and holding the probe):

```bash
uv run scripts/gdb_server.py status --port-num 8090
```

If it prints "No GDB server running", start it as described below. If it prints
status JSON, a server is up, but **do not blindly reuse it** — it may target a
different ELF, build, core, probe, or a coredump. The command securely loads the
process token from the private per-user state file; a detached server with
`"connected": false` is still live because liveness is determined by the
authenticated `/status` response, not by a PID check. Compare its `/status`
(`connected`, `mode`, `build_dir`, `elf`, `core`, `dev_id`) against what you
need; reuse only on a match. Otherwise pick another `--port-num`, or stop it
(`stop`) with the user's approval.

### 2. Start the server

Run `serve` in a **dedicated terminal** and leave that terminal open. It
intentionally runs in the foreground so the user can observe server readiness,
GDB/probe startup, and connection failures. Do not append `&` and do not pipe
the command through another process.

```bash
uv run scripts/gdb_server.py serve --build-dir build --autostart --port-num 8090
```

After the dedicated terminal reports that the HTTP server is ready, use a
second terminal to poll until the session is actually connected:

```bash
uv run scripts/gdb_server.py status --port-num 8090   # -> {"connected": true, ...}
```

On Windows PowerShell, use the same two-terminal flow. In the dedicated server
terminal:

```powershell
uv run scripts\gdb_server.py serve --build-dir build --autostart --port-num 8090
```

Then, in a second PowerShell terminal:

```powershell
uv run scripts\gdb_server.py status --port-num 8090
```

The readiness message means the HTTP server is bound, **not** that the GDB
session connected. With `--autostart`, a connection failure is caught and only
surfaced in `/status`, so check `connected` and `error`.

If `/status` shows `connected: false` with an `error`, autoconnect failed
(bad build dir, probe busy, SEGGER not on `PATH`, ...); fix that before issuing
session requests.

Useful `serve` options:

- `--build-dir` — the west/CMake build directory (default `build`). The ELF is
  auto-resolved: if `<build-dir>/domains.yaml` exists (a **sysbuild** build, the
  NCS default) the default image's `<build-dir>/<image>/zephyr/zephyr.elf` is
  used; otherwise it falls back to the flat `<build-dir>/zephyr/zephyr.elf`.
  Pass `--elf` to override.
- `--autostart` — connect a GDB session immediately (otherwise `POST /connect`).
- `--load` — flash the ELF to the target on connect.
- `--attach` — attach to a running core without reset/flash (`west attach`).
- `--gdb`, `--runner`, `--gdb-server-port` — override the GDB binary
  (`arm-zephyr-eabi-gdb`), west runner (`jlink`), or GDB server port (`2331`).
- `--gdbserver-exe` — override the J-Link GDB server executable. By default the
  headless build is auto-detected per platform (Windows `JLinkGDBServerCL`,
  Linux/macOS `JLinkGDBServerCLExe`), so you rarely need this.
- `--no-west` — do not launch `west debugserver`; assume a GDB server is already
  listening (e.g. the user started the J-Link GDB server manually).
- `--host` defaults to loopback. A non-loopback bind is refused unless the
  operator explicitly adds `--allow-non-loopback`; the server prints a warning
  because authenticated network access can still control the target.

Quick device check (no session):

```bash
uv run scripts/gdb_server.py list        # list attached J-Link devices
```

#### Running inside the NCS toolchain environment (important)

`west` and `arm-zephyr-eabi-gdb` are only on `PATH` **inside** the NCS toolchain
environment, and SEGGER's J-Link GDB server must also be reachable. On a typical
NCS install the server therefore has to be launched through
`nrfutil sdk-manager toolchain launch`. Use the invocation for your OS:

**Linux / macOS (bash):**

```bash
nrfutil sdk-manager toolchain launch --ncs-version=<ver> --chdir <ncs-topdir> -- \
  bash -c 'unset PYTHONHOME PYTHONPATH VIRTUAL_ENV; \
    cd <skill-dir> && uv run scripts/gdb_server.py serve \
    --build-dir <abs-build-dir> --west-topdir <ncs-topdir> --autostart --port-num 8090'
```

**Windows (PowerShell):**

```powershell
nrfutil sdk-manager toolchain launch --ncs-version=<ver> --chdir <ncs-topdir> -- `
  cmd /c "set PYTHONHOME=&& set PYTHONPATH=&& set VIRTUAL_ENV=&& cd /d <skill-dir> && uv run scripts\gdb_server.py serve ^
    --build-dir <abs-build-dir> --west-topdir <ncs-topdir> --autostart --port-num 8090"
```

Notes on the pieces the script does and does **not** handle for you:

- **Clear `PYTHONHOME` / `PYTHONPATH` in the launching shell first (required).**
  `nrfutil sdk-manager toolchain launch` exports these pointing at *its* bundled
  Python. They leak into the interpreter that `uv run` starts for
  `gdb_server.py` and corrupt that interpreter's own stdlib imports, so the
  script dies at startup — during `import argparse` -> `import re` — with
  `AssertionError: SRE module mismatch` on POSIX (or `ModuleNotFoundError: No
  module named 'west'`, and `Module use of pythonXY.dll conflicts...` on
  Windows), **before any of its own code runs**. The script therefore *cannot*
  fix this for itself; its automatic env-clean (see the next bullet) only
  applies to the child it spawns. This is why the commands above `unset` (bash)
  / `set VAR=` (cmd) `PYTHONHOME`, `PYTHONPATH`, and `VIRTUAL_ENV` before
  `uv run`. If you instead run `uv run` *outside* the toolchain launch (so these
  vars are never set), this step is unnecessary.
- **The `west` child env is cleaned automatically** — for the `west
  debugserver` subprocess it later spawns, the script strips `VIRTUAL_ENV`,
  `PYTHONPATH`, **and** `PYTHONHOME` (see `child_env_without_venv`) so `west`
  resolves the toolchain Python (avoids `ModuleNotFoundError: No module named
  'west'`). This covers only that child — not the interpreter running the
  script, which is why the launching-shell clear above is still required.
- **J-Link GDB server name is auto-detected** — the script picks the headless
  build present on your platform (Windows `JLinkGDBServerCL`, Linux/macOS
  `JLinkGDBServerCLExe`, falling back to the `JLinkGDBServer` symlink) instead
  of forcing one name, and never uses the GUI build (whose modal dialogs would
  block the session). Override with `--gdbserver-exe` only for a non-standard
  install.
- **Do not prepend generic system directories to `PATH`.** Only add the SEGGER
  JLink directory *if* the GDB server is not already resolvable (check with
  `command -v JLinkGDBServerCLExe` / `where JLinkGDBServerCL`). Prepending a
  broad directory such as `/usr/bin` can shadow the toolchain's own `python3.x`
  that `west`'s shebang resolves, re-introducing the `No module named 'west'`
  failure.
- **`--west-topdir <ncs-topdir>`** — Zephyr extension commands (`debugserver`)
  are workspace-bound, so the server runs `west debugserver` from the workspace
  topdir (e.g. `C:/ncs/v3.4.0`). Alternatively use `--no-west` and start
  `west debugserver` yourself in the toolchain env, then the skill connects to
  the existing GDB server.

### 3. Control and inspect

Read [debug operations](references/debug-operations.md) before sending HTTP
requests, inspecting crashes or threads, capturing RTT, analyzing core dumps, or
reading peripherals.

## Textual analysis (default)

When the user has not asked for an interactive session, collect state over the
HTTP server, interpret it, and reply with a short, human-readable summary. A
good analysis covers:

- **Where it stopped:** the stop reason (breakpoint hit, signal/fault, step
  complete) and the function/file:line, from `GET /stop-event` or the stop
  summary returned by execution endpoints.
- **Call stack:** a compact backtrace (innermost frames first) from
  `GET /backtrace`.
- **State:** the relevant locals (`GET /locals`) and fault registers
  (`GET /registers`: `pc`, `lr`, `sp`, `xpsr`, ...). `GET /analyze` bundles all
  of this into one structured response.

Keep it factual and concise, and separate observed evidence from the concluded
cause. State a root cause only after confirming it against the source and
control flow; otherwise present the suspicious value as a lead. For example:

> Stopped by a HardFault in `i2c_write` at `drivers/sensor.c:118`. Backtrace:
> `i2c_write` <- `sensor_sample_fetch` <- `main`. `pc=0x0000a3d2`,
> `lr=0xfffffff9` (exception return), `sp=0x20001f80`. Local `len` is 0 at the
> faulting line. Likely cause: a zero-length/near-null transfer; confirm by
> checking how `len` reaches `i2c_write` and the loop bound at `sensor.c:118`
> before asserting it as the root cause.

After delivering the analysis, offer an interactive session as an optional next
step.

## Multi-core targets

For nRF53, nRF54H, nRF54L, or any request involving more than one core, read
[multi-core debugging](references/multi-core.md) before connecting.

## Destructive operations — STOP AND ASK

`erase` and `recover` (and readback-protection changes) are destructive and can
wipe firmware or trigger a mass erase. **Always STOP AND ASK the user for
explicit confirmation before invoking them.** Authentication does not count as
approval and does not weaken either safety gate. These endpoints are gated
twice:

1. **Operator opt-in (out-of-band):** the server refuses `/erase` and `/recover`
   with `403` unless it was launched with `--allow-destructive`. A request flag
   is deliberately *not* enough on its own — an autonomous agent could set it, so
   the human operator must enable the capability at launch.
2. **Per-request confirmation:** when enabled, the body must still contain
   `{"confirm": true}` — only set that after the user has approved. Never send it
   on your own judgment.

## Cleanup

> [!WARNING]
> **The GDB session holds an exclusive lock on the J-Link probe.** While the
> server is running, no other tool (flashing, RTT, a second debugger) can use
> the probe.

**After completing the requested debugging task, you MUST ask the user whether
they want to stop the server.** Do NOT stop it, kill the process, or call
`POST /shutdown` on your own — the user decides when to release the probe.

Present the question together with the exact command:

> The GDB debug server is still running on port 8090, which keeps the J-Link
> probe locked. Do you want to stop it?
>
> If yes, run:
> ```bash
> uv run scripts/gdb_server.py stop --port-num 8090
> ```
> (If you started the server on a different port, replace `8090` with that port.)

## Troubleshooting

Common first checks:

- **`nrfutil device list` finds nothing / J-Link not visible:** confirm the DK
  is powered and connected via its J-Link USB port. If a PPK2 sits in the power
  path, the board is unpowered until the PPK2 is running — use the
  `ppk2` skill first.
- **No symbols, breakpoints "pending", odd backtraces:** the image was likely
  built without `CONFIG_DEBUG_OPTIMIZATIONS=y`. Rebuild for debugging.
- **`arm-zephyr-eabi-gdb` not found:** the NCS/Zephyr SDK environment is not
  activated — run through `nrfutil sdk-manager toolchain launch` (see
  [Prerequisites](#prerequisites)), not a bare shell.
- **`Address already in use` (HTTP port) or `Failed to open listener port
  <gdb-port>` on connect:** a previous attempt left the HTTP server or an
  orphaned J-Link GDB server holding the port. `--autostart`/`/connect` can fail
  *after* the server is already up, so first probe
  `uv run scripts/gdb_server.py status --port-num 8090` and reuse or
  `stop` it; if a stray J-Link GDB server lingers, find it
  (`pgrep -fl JLinkGDBServer` on POSIX, `tasklist | findstr JLinkGDBServer` on
  Windows) and kill it before retrying. Give a second core its own
  `--gdb-server-port`/`--rtt-port` (see [multi-core debugging](references/multi-core.md)).

If the debugger will not connect at all, the probe is busy/bricked, `west debug`
picks the wrong runner, GDB lacks Python support, or a core is out of sync —
**do not guess further here.** Read
[troubleshooting](references/troubleshooting.md), which covers those deeper
failure modes and where to find current Nordic guidance.
