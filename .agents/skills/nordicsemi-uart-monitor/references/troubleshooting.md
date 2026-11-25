# UART / VCOM Troubleshooting

Read this after the basic checks in `SKILL.md`
([When nothing appears](../SKILL.md#when-nothing-appears)) failed to explain the
problem. Each section starts with the symptom as the user would describe it.

## Contents

- [Port is busy or access is denied](#port-is-busy-or-access-is-denied)
- [Permission denied on Linux](#permission-denied-on-linux)
- [Port opens but stays completely silent](#port-opens-but-stays-completely-silent)
- [Output is garbled or full of replacement characters](#output-is-garbled-or-full-of-replacement-characters)
- [Output stops part-way through a session](#output-stops-part-way-through-a-session)
- [The boot log is always missed](#the-boot-log-is-always-missed)
- [The shell never responds to a sent command](#the-shell-never-responds-to-a-sent-command)
- [Lines are missing from the middle of a capture](#lines-are-missing-from-the-middle-of-a-capture)
- [nRF91 specifics](#nrf91-specifics)
- [When none of the above fits](#when-none-of-the-above-fits)

## Port is busy or access is denied

Symptoms: `could not open port`, `Access is denied` (Windows), `Device or
resource busy` (Linux), `Resource temporarily unavailable`.

A serial port admits exactly one owner. In order of likelihood:

1. **Our own server already holds it.** Probe before assuming anything else:
   `uv run scripts/uart_server.py status --port-num 8100`. If a server is up,
   use it rather than opening the port again.
2. **A serial terminal is open** — nRF Connect Serial Terminal, PuTTY, `screen`,
   `minicom`, the VS Code serial monitor, Arduino IDE. Ask the user to close it.
3. **An orphaned server from an earlier attempt.** Find it and stop it:
   - POSIX: `pgrep -fl uart_server.py`
   - Windows: `Get-CimInstance Win32_Process -Filter "Name like '%python%'" | Select ProcessId, CommandLine`
   Prefer `uart_server.py stop --port-num <port>` over killing the process, so
   the port is closed cleanly.
4. **`nrfutil device rtt` or a J-Link session** is using the same interface.

On Linux a stale lock in `/var/lock/LCK..ttyACM0` can outlive a crashed
process; remove it only after confirming no process holds the port.

## Permission denied on Linux

Symptom: `Permission denied: '/dev/ttyACM0'`.

The user is not in the group that owns the device node. Check and fix:

```bash
ls -l /dev/ttyACM0          # -> crw-rw---- root dialout
groups                      # is 'dialout' listed?
sudo usermod -aG dialout $USER
```

The group change takes effect on the next login — a new shell is not enough.
Do not recommend running the capture with `sudo`: it works once and leaves
root-owned artifacts behind.

On some distributions the group is `uucp` or `plugdev` instead. Read the actual
group from `ls -l` rather than assuming.

## Port opens but stays completely silent

The port opens, `/status` reports `open: true`, and `next_seq` never advances.

1. **DTR is not asserted.** Nordic DK UART lines are tri-stated until the
   terminal asserts DTR. Confirm `/status` shows `dtr: true`; assert it with
   `POST /dtr {"on": true}`.
2. **Wrong VCOM.** Try the kit's other port. On nRF54L kits the application log
   is on the **second** port.
3. **The console was rerouted in the build.** Check the project's devicetree
   overlay for a `zephyr,console` reassignment, and `prj.conf` for
   `CONFIG_UART_CONSOLE`. An image built with `CONFIG_LOG_BACKEND_RTT=y` and no
   UART backend prints nothing over UART by design — capture RTT instead.
4. **VCOM is not routed on the Board Controller.** Nordic DKs route VCOM lines
   through the interface MCU, and that routing is configurable. If VCOM0 is
   disabled or routed away from the application UART, the port enumerates and
   opens but carries nothing. Use the **`board-controller` skill**
   to read and fix the routing.
5. **The board is unpowered.** If a PPK2 is in the power path, use the
   `ppk2` skill — an unpowered board still shows a VCOM
   only if the interface MCU has its own supply, which is exactly what makes
   this confusing.
6. **The firmware genuinely prints nothing** — it crashed before logging
   initialised, or it is idle. Reset and capture the boot log; if even the boot
   banner is absent, debug the image with the `gdb` skill
   rather than the UART.

## Output is garbled or full of replacement characters

Symptoms: `����`, random punctuation, occasional readable words.

Almost always a **baud mismatch**. 115200 is the Nordic default, but an
application can select anything. Confirm the rate the firmware configures
(devicetree `current-speed` on the console UART) and pass it with `--baud`.

If partially readable text appears at the correct baud, suspect:

- A **USB hub or long cable** corrupting frames — connect directly.
- **Two writers on one line**, e.g. two cores logging to the same UART.
- **Binary data on the console** — modem traces or a protocol stream rather than
  text. Capture it raw to a file with `--log-file` instead of reading it as lines.

If the text is readable but littered with bracket sequences like `[0m`, that is
ANSI colour from the Zephyr shell. The server strips these by default; you are
seeing them only if the server was started with `--no-strip-ansi`.

## Output stops part-way through a session

Capture works, then `next_seq` stops advancing.

- Check `/status` for an `error` field — a `UART read failed` message means the
  device went away (unplugged, reset into the bootloader, or a USB dropout). The
  reader stops on a read error rather than spinning. Re-open with `POST /open`.
- **The board reset and its VCOM re-enumerated.** On some kits a reset drops and
  re-creates the USB device, invalidating the open handle. Re-open the port; if
  the path changed, run `uart_server.py list` again.
- **USB power management** suspended the port. Avoid hubs and docking stations;
  on Linux check for `usbcore.autosuspend` interference.

## The boot log is always missed

Use `POST /reset`, which clears the buffer *before* issuing the reset so the
boot log starts at sequence 0. Attaching after a manual reset always races.

If `nrfutil` is unavailable, `POST /clear` first, then reset the board, then
read from `since_seq=0` — the same ordering guarantee applies.

If the very first lines are still missing, the application logs before the
console is ready. `CONFIG_LOG_MODE_DEFERRED=y` can also delay early messages
until the log thread runs.

## The shell never responds to a sent command

`POST /send` reports `matched: false` and no lines came back.

1. **There is no shell in the image.** `CONFIG_SHELL=y` and a shell backend
   (`CONFIG_SHELL_BACKEND_SERIAL=y`) must be enabled. Without them the device
   ignores input entirely.
2. **The `expect` pattern does not match.** The prompt is `uart:~$ ` — escape
   the `$` (`uart:~\$`). Send without `expect` first and read the raw lines to
   see exactly what comes back.
3. **Wrong line ending.** The default is `\r\n`. Some CLIs want only `\r`; pass
   `"newline": "\r"`.
4. **Input goes to a different UART than the log output.** The shell backend may
   be bound to another instance. Check the devicetree.
5. **The device echoes but does not execute** — the shell is in a paused or
   raw-input state. Send an empty line to re-draw the prompt.

## Lines are missing from the middle of a capture

Check `dropped` in the response. Non-zero means the ring evicted lines before
you read them. Either poll more often, or start the server with a larger
`--buffer-lines`, or add `--log-file` so the full stream lands on disk
regardless of polling cadence.

Never describe a gap as "the firmware logged nothing" when `dropped` is non-zero.

## nRF91 specifics

- The nRF91 DK exposes **several** VCOM ports; the AT command interface and the
  application log are usually not the same one. Enumerate with
  `uart_server.py list` and probe each with `AT` (expect `OK`).
- **Modem traces are binary**, not text. Capture them with `--log-file` and
  process them with Nordic's trace tooling; reading them as lines produces noise.
- An AT interface generally wants `\r` as the line ending, not `\r\n`.

## When none of the above fits

Do not guess further. Query `nordicsemi_search_sources` (MCP tool) with the
exact symptom and the board name — it retrieves current DevZone Q&A and
official documentation, which cover kit- and revision-specific behaviour that
cannot be enumerated here.

Related skills, when the evidence points away from the UART itself:

- `board-controller` — VCOM routing, flow control, and rail
  voltages on the DK.
- `ppk2` — the board is unpowered because a PPK2 sits in
  its power path.
- `gdb` — the image crashes before logging starts, or you
  want RTT instead.
