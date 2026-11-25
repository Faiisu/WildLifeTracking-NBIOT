# GDB Debug Operations

Read the primary `SKILL.md` first, start or reuse the server, then use this
reference for operation-specific requests.

## Control and inspect over HTTP (default `http://127.0.0.1:8090`)

Every effective `GET` and `POST` requires the process-scoped token in the
`X-GDB-Token` header. Missing credentials return `401`; incorrect credentials
return `403`. The token is never accepted in a URL and there is no HTTP token
endpoint. Retrieve it locally from the private server record:

```bash
GDB_TOKEN="$(uv run scripts/gdb_server.py token --port-num 8090)"
```

PowerShell:

```powershell
$GdbToken = (& uv run scripts\gdb_server.py token --port-num 8090).Trim()
```

The record is stored in a private per-user runtime directory and contains the
host, port, start time, and token. It does not contain or check a PID.
`token --json` prints that full record. POSIX systems enforce current-user
ownership and modes `0700`/`0600`. Windows stores the record below
`%LOCALAPPDATA%` and relies on the access-control list inherited from the user
profile. Liveness and stale-state detection use an authenticated `/status`
probe. The token protects the HTTP API from callers such as browser pages that
cannot read local user files; processes running as the same OS user are trusted.
Treat both outputs as secrets: do not log them, paste them into errors, or put
the token in a query string. The `status` and `stop` CLI commands load the state
automatically. For a remote or separately managed server, they also accept
`--token <token>` or `--token-file <path>`.

The endpoint notation below assumes this header on every request.

| Method & path | Purpose |
|---------------|---------|
| `GET /status[?core=<name>]` | Session state: connected, attached, core, build_dir, elf, rtt_port, last stop summary. With >1 core, also lists `cores` |
| `GET /sessions` | All connected cores and their per-core status (multi-core) |
| `GET /devices` | `nrfutil device list --traits jlink` |
| `POST /connect` | Body: `build_dir`, `elf`, `gdb` (must be a Zephyr SDK GDB binary), `runner`, `gdb_server_port`, `arch` (`arm`/`riscv`), `svd`, `rtt_port`, `core`, `dev_id`, `load`, `attach`, `use_west`, `west_topdir` -> start GDB, connect, load symbols |
| `POST /disconnect` | Body: optional `core` -> detach GDB and stop that core's GDB server subprocess |
| `POST /run` / `/continue` / `/interrupt` | Start / resume / halt execution |
| `POST /step` / `/next` / `/stepi` / `/finish` | Step into / over / one instruction / until return |
| `POST /until` | Body: `location` (`file:line` or function) -> run until reached |
| `POST /monitor` | Body: `command` (e.g. `reset halt`) -> send a J-Link `monitor` command |
| `POST /breakpoints` | Body: `location`, optional `condition`, `temporary` -> insert breakpoint |
| `GET /breakpoints` | List breakpoints/watchpoints |
| `POST /breakpoints/delete` \| `/enable` \| `/disable` | Body: `number` |
| `POST /watchpoints` | Body: `expression`, `mode` (`write`/`read`/`access`) |
| `GET /backtrace` | Current call stack |
| `POST /frame` | Body: `level` -> select stack frame |
| `GET /locals` | Locals/args of the current frame |
| `POST /evaluate` | Body: `expression` -> evaluate a C expression |
| `POST /variable` | Body: `expression` (e.g. `my_var=0x1`) -> assign a variable/memory |
| `GET /registers` | Named CPU register values |
| `GET /memory?addr=<expr>&len=<n>` | Read raw memory bytes (`len` capped at 64 KiB) |
| `GET /rtt[?seconds=<s>&channel=<n>]` | Capture SEGGER RTT (channel 0) from the J-Link. Core must be **running** |
| `GET /stack-usage` | Per-thread stack high-water usage (proactive overflow check) |
| `GET /stop-event` | Why execution last stopped (reason + frame) |
| `GET /analyze` | Halt -> backtrace + locals + fault registers -> structured summary |
| `GET /fault` | Decode the target's fault status + backtrace. Arm: SCB CFSR/HFSR/BFAR/MMFAR. RISC-V: mcause/mepc/mtval |
| `GET /crash-report` | One-shot triage: halt -> arch-aware fault decode -> recover faulting PC/LR -> summary paragraph |
| `GET /peripheral?peripheral=<name>[&register=<reg>]` | Read peripheral registers and decode them into named SVD fields (needs `--svd`) |
| `GET /threads[?backtrace=true]` | List RTOS threads (name, state, prio, frame); `backtrace=true` adds each thread's call stack |
| `POST /reset` | Body: `reset_kind`, `serial_number` -> `nrfutil device reset` |
| `POST /halt` / `POST /go` | `nrfutil device halt` / `go` (`go` accepts `core`) |
| `POST /erase` / `POST /recover` | Destructive; need server `--allow-destructive` **and** `{"confirm": true}` (see below) |
| `POST /shutdown` | Gracefully stop the server (releases the probe) |

The interface is HTTP with authenticated JSON bodies. Every POST must use
`Content-Type: application/json`. For example with `curl`:

```bash
# GET a read endpoint
curl -sS -H "X-GDB-Token: ${GDB_TOKEN}" http://127.0.0.1:8090/status
curl -sS -H "X-GDB-Token: ${GDB_TOKEN}" \
  "http://127.0.0.1:8090/memory?addr=0x20000000&len=32"

# POST with a JSON body
curl -sS -H "X-GDB-Token: ${GDB_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"location": "main.c:42", "temporary": true}' \
  http://127.0.0.1:8090/breakpoints

# Check the HTTP status too: 2xx = ok; 400 bad request; 409 wrong state
# (e.g. core running); 401/403 = authentication failed; 415 = wrong POST
# content type; 503/504 = GDB/probe unavailable or timed out.
curl -sS -H "X-GDB-Token: ${GDB_TOKEN}" \
  -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/backtrace
```

PowerShell:

```powershell
$GdbHeaders = @{ "X-GDB-Token" = $GdbToken }

Invoke-RestMethod -Method Get `
  -Uri "http://127.0.0.1:8090/memory?addr=0x20000000&len=32" `
  -Headers $GdbHeaders

$BreakpointBody = @{
  location = "main.c:42"
  temporary = $true
} | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8090/breakpoints" `
  -Headers $GdbHeaders `
  -ContentType "application/json" `
  -Body $BreakpointBody
```

### Response conventions

- **Guided loop.** Many responses include a `suggested_next_actions` list — the
  recommended next step(s). Execution results suggest what to inspect. If a
  breakpoint did not fire, treat it as **not observed during this run**, not
  proof the path is dead: it may have been set too late, optimized out, still
  pending (unresolved), on the wrong core/image, or blocked by exhausted
  hardware comparators. Verify with `GET /breakpoints` (installed? `addr`
  resolved? hit count?) and confirm the core/image before concluding a path is
  unreachable. Reads that fail because the core is running return a hint to
  `POST /interrupt` first (memory/registers/backtrace/threads require a halted
  core).
- **Token-aware output.** Large responses (big `/memory` reads,
  `/threads?backtrace=true`, full `/peripheral` dumps) are automatically
  **spilled to a file** to protect context: you get an envelope
  `{"spilled": true, "path": ..., "bytes": ..., "sha256": ..., "preview": ...}`.
  Read the file only if you actually need the full payload; otherwise narrow the
  query (e.g. a single register, a smaller window). Tune or disable with
  `--max-response-chars` (0 disables).
- **Core selection (multi-core).** When more than one core is connected, target
  a specific one with `?core=<name>` on GET requests or `"core": "<name>"` in
  POST bodies. With a single session the selector is optional. See
  [multi-core debugging](multi-core.md).

Typical scripted flow:

1. `POST /connect` (skip if launched with `--autostart`).
2. `POST /breakpoints {"location": "main.c:42"}` (or the fault handler).
3. `POST /continue` and read the returned stop summary.
4. `GET /backtrace`, `GET /locals`, `GET /registers` — or just `GET /analyze`.
5. Deliver a textual analysis; ask the user before stopping the server.

### Catching early / one-shot functions (e.g. `main`)

By the time GDB attaches, early code such as `main` has usually already run, so a
breakpoint set afterwards never fires and the CPU may be parked in the idle
thread (in tickless builds, with nothing executing, no breakpoint anywhere can
hit). To catch an early function you must halt at the reset vector *before* it
runs: set the breakpoint, then `POST /monitor {"command": "reset halt"}`, then
`POST /continue`.

Be aware that reset behavior is target- and probe-specific: on some devices
(observed on the nRF54L15 DK) `monitor reset halt` does **not** hold the core at
the reset vector, so `main` still cannot be caught this way. In that case break
on a function that runs repeatedly (a loop body, a periodic handler, an ISR) to
confirm control, or resolve the correct reset approach for the SoC via
`nordicsemi_search_sources`.

### Crash / HardFault triage

To root-cause a fault (HardFault, BusFault, MemManage, UsageFault on Arm; a
machine trap on the RISC-V FLPR):

1. Catch the fault. The simplest portable way on Zephyr (Arm) is to break on the
   fault handler: `POST /breakpoints {"location":"z_arm_fault"}` (and/or
   `k_sys_fatal_error_handler`), then `POST /continue`.
2. **`GET /crash-report`** does the whole triage in one call: it halts, decodes
   the fault (arch-aware), recovers the faulting PC/LR, resolves them to symbols,
   and returns a ready-to-read summary paragraph. Use this first.
3. For finer control, `GET /fault` returns just the decoded fault (Arm:
   `CFSR`/`HFSR`/`BFAR`/`MMFAR` named flags + fault address; RISC-V:
   `mcause`/`mepc`/`mtval`), and `GET /backtrace` + `POST /frame` + `GET /locals`
   reach the faulting source line.
4. Report the decoded fault + the source location + the likely cause and fix.

GDB often cannot auto-unwind through the Cortex-M exception (the backtrace shows
`<signal handler called>` then `??`). **Prefer `GET /crash-report`**: it already
does the arch-aware exception-frame recovery (it reads `EXC_RETURN` to pick the
right stack and frame layout, extracts the stacked PC/LR, and resolves them to
symbols). Do not hand-scan the stack unless `/crash-report` cannot recover the
frame.

If you must do it manually, note that the recovery depends on `EXC_RETURN` (in
`LR` on exception entry): it selects the stack (PSP for a Zephyr thread, MSP for
ISR context) and whether an extended FP frame is present, and the basic stacked
set is `{r0,r1,r2,r3,r12,LR,PC,xPSR}` with 4 bytes of alignment padding when
`xPSR` bit 9 is set. Decode `EXC_RETURN` and the stack selection first rather
than scanning for a value that merely looks like a code address, then resolve
the stacked PC/LR with `POST /evaluate {"expression":"(void(*)())<pc>"}`.

Combine the decoded fault (address + type) with the resolved PC/LR to report the
exact function, line, and offending access.

### Hangs & deadlocks (RTOS thread inspection)

To debug a stuck system ("which thread is blocked, and where?"), use
`GET /threads?backtrace=true`. It lists every Zephyr thread with its name,
state, priority, and current frame, plus a per-thread backtrace.

- **Requirements:** build with `CONFIG_DEBUG_THREAD_INFO=y` (add
  `CONFIG_THREAD_NAME=y` for names). Thread awareness comes from GDB via the
  J-Link runner's `RTOSPlugin_Zephyr`, which `west debugserver` loads
  automatically — no manual setup and no stack reconstruction needed.
- **Reading it:** a thread's backtrace ending in a kernel wait names the cause,
  e.g. a `PENDING` thread whose stack is `… -> z_pend_curr -> z_impl_k_sem_take`
  is blocked taking a semaphore; `… -> z_impl_k_sleep` is sleeping. To find a
  deadlock, look for two threads each pending on a resource the other holds.
- Attach to the running system (`{"attach": true}`) and `POST /interrupt` to
  capture steady-state thread positions rather than halting at boot.

### Stack overflow (crash + proactive)

Stack overflow is a distinct, common bug class handled two ways:

- **At the crash:** `GET /crash-report` decodes the Armv8-M `STKOF` UsageFault
  bit, sets `stack_overflow: true`, names the overflowing thread, and suggests
  which stack size to raise (`CONFIG_MAIN_STACK_SIZE`, the `K_THREAD_STACK` size,
  or `CONFIG_ISR_STACK_SIZE`). Requires the MPU/stack-guard fault to fire
  (`CONFIG_HW_STACK_PROTECTION=y`, on by default for most Nordic boards).
- **Before it crashes:** `GET /stack-usage` walks the kernel thread list and
  reports each thread's stack high-water mark (`used`/`free`/`used_pct`), and
  flags threads ≥90% used. Build with `CONFIG_THREAD_MONITOR=y` (thread list),
  `CONFIG_THREAD_STACK_INFO=y` (bounds), and `CONFIG_INIT_STACKS=y` (the fill
  pattern used to measure usage). Threads without the fill pattern report
  `used: null` (`fill_seen: false`).

### RTT capture (SEGGER Real-Time Transfer)

`GET /rtt?seconds=<s>` captures the target's RTT channel-0 output (console/log)
straight from the J-Link — no UART pins, no VCOM routing. This is the preferred
way to read firmware logs and to capture a coredump emitted over the RTT log
backend, and it sidesteps the VCOM-routing fragility of UART capture.

- **The core must be running:** RTT is polled while the CPU executes and only
  after the app initializes the `_SEGGER_RTT` control block. On a halted or
  freshly-reset target you get 0 bytes — `POST /go` (or `/continue`) then retry.
  (The response's `suggested_next_actions` says exactly this when empty.)
- **Firmware side:** enable RTT output, e.g. `CONFIG_USE_SEGGER_RTT=y` with
  `CONFIG_RTT_CONSOLE=y`, or the RTT log backend `CONFIG_LOG_BACKEND_RTT=y`.
- The J-Link RTT telnet port defaults to 19021; override with `--rtt-port`
  (serve) or `{"rtt_port": ...}` on `/connect` if you changed it.

### Post-mortem: analyzing a core dump (no live probe)

To debug a **field crash with no attached device**, use Zephyr's core-dump flow.
The skill drives GDB against `coredump_gdbserver.py` instead of a J-Link target,
so backtrace/registers/locals work on the captured snapshot.

Firmware side (once): enable `CONFIG_DEBUG_COREDUMP=y` and a backend (e.g.
`CONFIG_DEBUG_COREDUMP_BACKEND_LOGGING=y`, or the flash-partition backend). On a
fatal error the dump is emitted to that backend.

To analyze a captured dump:

1. Retrieve the dump and convert it to binary — e.g. for the logging backend,
   `zephyr/scripts/coredump/coredump_serial_log_parser.py dump.log dump.bin`.
2. Start the server in coredump mode:

```bash
uv run scripts/gdb_server.py serve --build-dir <build> --west-topdir <ncs-topdir> \
  --coredump dump.bin --autostart --port-num 8090
```

The server launches `coredump_gdbserver.py <elf> dump.bin` (fixed port 1234) and
connects GDB (arch auto-detected from the ELF). The target is already halted at
the crash — no reset/flash. Then `GET /backtrace`, `GET /registers`, `GET
/locals`, and `GET /threads` (if the dump includes thread data) work as usual.
`GET /status` reports `mode: "coredump"`.

Note: fault status registers live in the SCB peripheral region, which a RAM-only
dump does not contain — so `/fault` may be empty on a coredump; rely on the
backtrace and CPU registers (which the dump does capture) instead.

### Peripheral inspection (SVD)

To debug peripheral misconfiguration (a UART not transmitting, a GPIO stuck, a
TWIM misprogrammed), read the peripheral's registers **by name** rather than
decoding raw addresses by hand. `GET /peripheral?peripheral=UARTE0` reads and
decodes every register of the peripheral into named bit-fields (with enumerated
value names where the SVD provides them); add `&register=ENABLE` for a single
register.

This needs a **CMSIS-SVD file for the connected SoC**, supplied via `--svd`
(serve) or `{"svd": ...}` on `/connect` — it is deliberately *not* hardcoded per
device. Nordic SVD files ship inside the NCS install (the MDK folder,
`modules/hal/nordic/nrfx/mdk/*.svd`); resolve the exact file for the target SoC
via `nordicsemi_search_sources` rather than guessing. Because decoding is driven
entirely by the SVD, new SoCs work with no code change — just point at their SVD.
