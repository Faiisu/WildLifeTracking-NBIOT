---
name: nordicsemi-uart-monitor
description: >-
  Read, capture, and interact with a Nordic development kit's UART/VCOM console.
  Use when the user asks to read or tail application logs, capture boot output
  after a reset, open a serial terminal or COM/ttyACM port, check what the
  firmware is printing, confirm the board booted, run Zephyr shell commands,
  send AT commands to an nRF91 modem, or investigate missing serial output.
metadata:
  title: "Nordic UART Monitor (VCOM logs and shell)"
  version: "1.0.3"
  tags: "uart, serial, vcom, logs, shell"
---

# Nordic UART Monitor

This skill reads and drives the **UART/VCOM console** of a Nordic development
kit — application logs, the boot banner, a Zephyr shell, or an nRF91 modem AT
interface.

The backend is a single-file Python program, `scripts/uart_server.py`, located
in the folder next to this skill. It holds the serial port open in a background
reader, buffers decoded lines in a **sequence-numbered ring**, and exposes a
**local HTTP server**, which is the primary interface you should use. Keep
`uart_server.py` and this `SKILL.md` together when sharing the skill.

Why the server rather than a plain `read` loop: a serial port can only be held
by one process, output is a continuous stream with no natural end, and a shell
prompt arrives with no trailing newline. The server solves all three — you poll
with a cursor instead of blocking, and one process owns the port.

## How to respond to a UART request

Decide which outcome the user wants before doing anything.

1. **Capture and interpret logs (default).** The user wants to know what the
   firmware printed — a boot log, an error, whether something ran. Start the
   server, capture, and return a concise **textual summary** of what the logs
   show. Do not dump hundreds of raw lines back at the user.
2. **Interact with a device CLI.** The user wants to run commands against a
   Zephyr shell or send AT commands. Use `POST /send` with an `expect` pattern
   so each command returns its own response (see
   [Shell interaction](references/recipes.md#shell-interaction)).
3. **Diagnose "no output".** The user says nothing appears on the serial port.
   This is the single most common UART problem and it is almost never the
   capture tool — see [When nothing appears](#when-nothing-appears).
4. **Just watch.** The user wants to follow logs live themselves. The server
   echoes captured lines to its own terminal by default, so the running `serve`
   window already shows live output; alternatively hand them the polling command,
   or start with `--log-file` and let them tail the file.

## Prerequisites

- A Nordic DK connected over USB, enumerating at least one VCOM serial port.
- `uv` installed (the script uses PEP 723 inline metadata and pulls `pyserial`
  automatically; no virtualenv setup needed).
- `nrfutil` on `PATH` for device correlation and `POST /reset`. Everything else
  works without it.
- The user must run the server locally — the serial port is local hardware. The
  server binds to `127.0.0.1` by default and refuses a non-loopback bind unless
  `--allow-non-loopback` explicitly acknowledges the exposure.

## Choosing the port — do not guess

A DK usually exposes **two or more** VCOM ports and only one carries the
application log. Picking the wrong one looks exactly like "the firmware prints
nothing".

```bash
uv run scripts/uart_server.py list
```

This returns every OS serial port plus, when `nrfutil` is available, a
`devices` array mapping each kit's serial number to its ports **in enumeration
order** — index `0` is "Serial Port 0", index `1` is "Serial Port 1".

Defaults by kit:

| DK | Serial Port 0 | Serial Port 1 |
|---|---|---|
| nRF52840 DK | Application logs (`uart0`) | Unused by default |
| nRF5340 DK | Application core logs (`uart0`, VCOM0) | Network core / secondary (`uart1`) |
| nRF54L* DK | Secondary (`uart0`) | **Application logs (`uart1`)** |

> [!IMPORTANT]
> On the **nRF54L series the application log is on the second port**, not the
> first. If an nRF54L DK looks silent, try the other port before anything else.

These are defaults only. An application can reassign the `zephyr,console`
chosen node in a devicetree overlay, so the runtime mapping may differ. When
the mapping matters and is not obvious, resolve it with
`nordicsemi_search_sources` or check the project's overlay rather than guessing.

**If a PPK2 sits in the board's power path, no VCOM enumerates at all** until
the PPK2 is running with DUT power enabled — the board is simply unpowered.
That looks like "board not connected". Use the `ppk2` skill first.

## Primary interface: the HTTP server

Use the path to the `uart_server.py` copy that ships with this skill
(`scripts/uart_server.py` relative to this file).

### 1. Check for an already-running server

Always probe first — a previous session may have left one running and holding
the port:

```bash
uv run scripts/uart_server.py status --port-num 8100
```

If it prints "No UART server running", start one. If it prints status JSON, a
server is up — but **do not blindly reuse it**: compare its `port` and `baud`
against what you need. Reuse only on a match; otherwise pick another
`--port-num`, or `stop` it with the user's approval.

`status` loads the process token from a private server-state file. A record is
accepted only when an authenticated `/status` probe identifies a UART server.
The HTTP server can be alive while `"open": false`, so serial-port openness is
not used as the liveness test. No PID is stored or checked.

### 2. Start the server

Run `serve` in a **dedicated terminal** and leave that terminal open. It
intentionally runs in the foreground so the user can observe readiness, serial
open failures, and incoming diagnostics. Do not append `&` and do not pipe it
through `grep`.

```bash
uv run scripts/uart_server.py serve --port <uart_port> --baud 115200 --port-num 8100
```

After the dedicated terminal prints
`UART HTTP server ready on <host>:<port>`, use a second terminal to confirm that
the serial port opened and obtain the token:

```bash
uv run scripts/uart_server.py status --port-num 8100   # -> {"open": true, ...}
TOKEN="$(uv run scripts/uart_server.py token --port-num 8100)"
```

PowerShell uses the same two-terminal flow. In the dedicated server terminal:

```powershell
uv run scripts\uart_server.py serve --port <uart_port> --baud 115200 --port-num 8100
```

Then, in a second PowerShell terminal:

```powershell
uv run scripts\uart_server.py status --port-num 8100
$UartToken = (& uv run scripts\uart_server.py token --port-num 8100).Trim()
```

The readiness message means the **HTTP server** is up, not that the serial port
opened: if
the port is busy the server stays up and reports the failure in `/status`, so
check `open` and `error` rather than relying only on readiness.

Useful `serve` options:

- `--baud` — line rate (default `115200`, the Nordic DK default).
- `--no-dtr` / `--no-rts` — see
  [DTR and RTS](references/recipes.md#dtr-and-rts).
- `--buffer-lines` — ring capacity in lines (default `5000`).
- `--echo` / `--no-echo` — print every captured line to stdout in the server
  terminal as it arrives (default: on). This is the live view; the HTTP ring and
  `--log-file` are unaffected. Echo starts only after the readiness line, so the
  first captured lines still reach the ring but may not appear live — poll
  `GET /logs?since_seq=0` if you need the earliest output.
- `--log-file <path>` — also append every captured line to a file. Use this for
  long captures: grep the file instead of polling for tens of thousands of lines.
- `--no-open` — start the server without taking the port (attach later with
  `POST /open`).
- `--no-strip-ansi` — keep the VT100 escapes the Zephyr shell emits. They are
  stripped by default because they are noise in a log and break `expect` patterns.
- `--max-response-chars` — spill threshold for oversized responses (0 disables).
- `--allow-non-loopback` — acknowledge a network-reachable authenticated API.
  Without this option, every non-loopback bind is refused.

### 3. Control and poll over HTTP (default `http://127.0.0.1:8100`)

| Method and path | Purpose |
|---|---|
| `GET /status` | Port, baud, `open`, dtr, rts, `next_seq`, `lines_buffered`, `dropped`, `error` |
| `GET /devices` | Serial ports plus the `nrfutil` device/VCOM correlation |
| `GET /logs?since_seq=<n>` | Lines from a cursor — the normal way to poll |
| `GET /logs?window_s=<s>` | Lines from the last N seconds |
| `GET /logs?max_lines=<n>&grep=<regex>` | Cap the reply and filter it (combine with either selector) |
| `GET /wait?pattern=<regex>&timeout_s=<s>` | Block until a line matches (boot banner, prompt) |
| `POST /send` | Body: `text`, `newline`, `expect`, `timeout_s` -> write, then capture the reply |
| `POST /reset` | Body: `serial_number`, `reset_kind`, `clear_buffer` -> clear the buffer, then `nrfutil device reset` |
| `POST /open` / `POST /close` | Take or release the serial port without stopping the server |
| `POST /clear` | Drop buffered lines and restart the cursor at 0 |
| `POST /dtr` / `POST /rts` | Body: `{"on": true\|false}` -> set a control line |
| `POST /shutdown` | Gracefully stop the server (releases the port) |

Every effective `GET` and `POST` requires `X-UART-Token`. Missing credentials
receive `401`; incorrect credentials receive `403`. Obtain the token through
the local CLI state command; there is deliberately no HTTP token endpoint:

```bash
TOKEN="$(uv run scripts/uart_server.py token --port-num 8100)"
curl -sS -H "X-UART-Token: $TOKEN" \
  "http://127.0.0.1:8100/logs?since_seq=0&max_lines=100"

curl -sS -H "X-UART-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text": "help", "expect": "uart:~", "timeout_s": 3}' \
  http://127.0.0.1:8100/send
```

PowerShell:

```powershell
$UartToken = (& uv run scripts\uart_server.py token --port-num 8100).Trim()
$UartHeaders = @{ "X-UART-Token" = $UartToken }

Invoke-RestMethod -Method Get `
  -Uri "http://127.0.0.1:8100/logs?since_seq=0&max_lines=100" `
  -Headers $UartHeaders

$SendBody = @{
  text = "help"
  expect = "uart:~"
  timeout_s = 3
} | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8100/send" `
  -Headers $UartHeaders `
  -ContentType "application/json" `
  -Body $SendBody
```

> [!IMPORTANT]
> **Every request must send `X-UART-Token`, and every `POST` must also send
> `Content-Type: application/json`**, including empty-body operations such as
> `/clear` and `/close`. Requests without the JSON content type receive `415`.
> The server intentionally provides no permissive CORS or `OPTIONS` support
> because UART has no browser application.

### Authentication state and recovery

Each `serve` process generates a new random token. It atomically writes
`server-<port>.json` with `host`, `port`, `started_at`, and `token` in a private
per-user runtime directory. POSIX systems enforce current-user ownership and
modes `0700`/`0600`. Windows stores the record below `%LOCALAPPDATA%` and relies
on the access-control list inherited from the user profile. Existing state must
always be a regular file. Server liveness and stale-state detection use the
authenticated `/status` probe, not a process identifier. The token protects the
HTTP API from callers such as browser pages that cannot read local user files;
processes running as the same OS user are trusted. Never put the token in a URL,
log it, or include it in an HTTP error.

Use the CLI instead of opening the state file directly:

```bash
uv run scripts/uart_server.py token --port-num 8100
uv run scripts/uart_server.py token --port-num 8100 --json  # Sensitive: includes the token
uv run scripts/uart_server.py status --port-num 8100
uv run scripts/uart_server.py stop --port-num 8100
```

`status` and `stop` auto-load verified state. For a deliberately remote or
manually managed server, provide credentials explicitly:

```bash
uv run scripts/uart_server.py status --host <host> --port-num 8100 --token-file <private-file>
uv run scripts/uart_server.py stop --host <host> --port-num 8100 --token '<token>'
```

Prefer `--token-file` where command arguments may be inspected. Do not combine
`--token` and `--token-file`.

If the CLI reports stale state, first confirm that no UART server owns that
port. A new `serve` removes a stale record only after validating that the
record is safe under the platform model above. Do not weaken POSIX permissions
or delete another user's state. If a state file was replaced while a server was
running, shutdown cleanup compares the process-scoped token and leaves the
replacement untouched.

### Response conventions

- **Cursor.** Every reply carries `next_seq`. Pass it back as `since_seq` on the
  next call to get exactly the lines you have not seen. Start from `0` for
  everything currently buffered.
- **`dropped`.** The ring evicts oldest-first. A non-zero `dropped` means lines
  were lost before you read them — poll more often or raise `--buffer-lines`.
  Never report a gap as "nothing was logged" when `dropped` is non-zero.
- **`buffer_reset`.** Your cursor predates a `/clear` or `/reset`. Restart from
  the `next_seq` in the same reply.
- **`generation`.** Incremented by every `/clear` and `/reset`. Sequence numbers
  restart at 0 each time, so a cursor is only meaningful within one generation.
  If `generation` differs from the one you last saw, your cursor is stale —
  restart from `next_seq`. Check this rather than relying on `buffer_reset`
  alone, which cannot detect a clear the sequence has already grown past.
- **`partial: true`.** The line was flushed because the port went quiet, not
  because a newline arrived — typically a shell prompt. This is what makes
  `expect` able to match a prompt at all.
- **`truncated`.** More lines matched than `max_lines`; you received the newest.
  Narrow with `grep` or page with `since_seq`.
- **Spilled responses.** A reply larger than `--max-response-chars` is written
  to a file and replaced with `{"spilled": true, "path": ..., "preview": ...}`.
  Read the file only if you truly need it all; usually a narrower query is better.
- **Status codes.** 200 ok; 400 malformed request; 401 token missing; 403 token
  incorrect; 409 wrong state (port not open, or busy); 413 request body too
  large; 415 missing `Content-Type: application/json` on a `POST`; 504 an
  `expect`/`wait` timed out — the lines captured so far are still in the body,
  so read them rather than retrying blindly.

## Common UART operations

For boot-log capture, incremental log following, shell interaction, wait
conditions, and DTR/RTS control, read [UART recipes](references/recipes.md).

## One-shot commands (no server)

For a single quick capture where no interaction is needed:

```bash
uv run scripts/uart_server.py read --port <uart_port> --baud 115200 --duration 10
uv run scripts/uart_server.py write --port <uart_port> --message "help"
```

These take the port for their own duration, so they **fail while a server holds
it**. If you get an "already open"/"access denied" error, a server is probably
running — probe with `status --port-num 8100`. Anything needing more than one
step should use the server.

## When nothing appears

Work through this before concluding the firmware is broken:

1. **Wrong VCOM port.** Try the other port. On nRF54L kits the application log
   is on the *second* one.
2. **DTR not asserted.** Confirm `/status` reports `dtr: true`.
3. **Board is unpowered.** If a PPK2 is in the power path, use the
   `ppk2` skill —
   an unpowered board enumerates nothing.
4. **Wrong baud.** 115200 is the Nordic default, but an application can change it.
5. **The board has not printed since you attached.** Reset it and capture
   ([Capture the boot log](references/recipes.md#capture-the-boot-log)).
6. **Logging is not built in.** The image may have no console backend, or route
   logs to RTT instead.

Still nothing, or a different failure (permission denied, port busy, garbled
characters)? **Read `references/troubleshooting.md`**, which covers those cases
in detail. Do not guess past this point.

## When to use RTT instead

SEGGER RTT carries logs over the debug probe with no UART pins and no VCOM
routing. Prefer it when the UART is occupied by application data, the board has
no usable VCOM, or VCOM routing is the thing that is broken. If a J-Link
session is already available, the `gdb` skill's `GET /rtt`
captures it directly.
Firmware side: `CONFIG_USE_SEGGER_RTT=y` with `CONFIG_LOG_BACKEND_RTT=y`.

## Cleanup

> [!WARNING]
> **The server holds an exclusive lock on the serial port.** While it runs, no
> other tool — a serial terminal, another capture, `nrfutil device rtt` on the
> same port — can open that VCOM.

**After completing the requested capture or interaction, you MUST ask the user
whether they want to stop the server.** Do NOT stop it, kill the process, or
call `POST /shutdown` on your own — the user decides when to release the port.

Present the question together with the exact command:

> The UART server is still running on port 8100, which keeps the serial port
> locked. Do you want to stop it?
>
> If yes, run:
> ```bash
> uv run scripts/uart_server.py stop --port-num 8100
> ```
> (If you started the server on a different port, replace `8100` with that port.)
