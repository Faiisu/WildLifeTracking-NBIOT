# UART Recipes

Read the primary `SKILL.md` first and start or reuse the UART server before
following these operation-specific recipes.

## Recipes

### Capture the boot log

The classic race — reset the board, then start capturing, and the boot banner is
already gone. `POST /reset` closes it by clearing the buffer *before* issuing the
reset, so the boot log always starts at sequence 0:

```bash
# Server already running and holding the port
curl -sS -H "X-UART-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"serial_number": "<segger_serial>", "clear_buffer": true}' \
  http://127.0.0.1:8100/reset

# Wait for the banner instead of sleeping a guessed number of seconds.
# Pass since_seq=0 — /wait scans from the *current* cursor by default, and a
# fast board has usually already printed the banner by the time this call lands.
curl -sS -H "X-UART-Token: $TOKEN" \
  "http://127.0.0.1:8100/wait?pattern=Booting&since_seq=0&timeout_s=10"

curl -sS -H "X-UART-Token: $TOKEN" \
  "http://127.0.0.1:8100/logs?since_seq=0&max_lines=200"
```

Match the banner loosely. Current NCS prints two lines — `*** Booting nRF
Connect SDK v3.2.0-... ***` followed by `*** Using Zephyr OS v4.2.99-... ***` —
so `Booting Zephyr` matches nothing on a modern image.

If `nrfutil` is unavailable, `POST /clear` then reset the board by hand (button
or `nrfutil device reset` in another shell) — the ordering guarantee is the same.

### Follow logs incrementally

```bash
curl -sS -H "X-UART-Token: $TOKEN" "http://127.0.0.1:8100/logs?since_seq=0"   # -> next_seq: 42
curl -sS -H "X-UART-Token: $TOKEN" "http://127.0.0.1:8100/logs?since_seq=42"  # -> only what is new
```

Filter noisy output server-side rather than pulling everything:

```bash
curl -sS -H "X-UART-Token: $TOKEN" \
  "http://127.0.0.1:8100/logs?since_seq=0&grep=%3Cerr%3E%7C%3Cwrn%3E"
```

### Shell interaction

`POST /send` takes the cursor *before* writing, so no output can slip through
between sending and listening. With `expect`, one call is one request/response:

```bash
curl -sS -H "X-UART-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text": "kernel version", "expect": "uart:~", "timeout_s": 3}' \
  http://127.0.0.1:8100/send
```

The reply contains `matched`, `matched_line`, and every line captured after the
write. Notes:

- The Zephyr shell prompt is `uart:~$ `, and it arrives as a `partial` line.
  Prefer the plain substring `uart:~` as the `expect` pattern. Anchoring on the
  `$` means escaping it for the regex *and* for JSON *and* for the shell, and a
  `\$` that loses one layer produces `Malformed JSON body: Invalid \escape`.
  If you do need the anchor, put the body in a file and use `curl -d @body.json`.
- Without `expect`, `/send` writes and then captures for `timeout_s` before
  returning. Prefer `expect`: it returns as soon as the device answers.
- If a prompt is visible and you need a quick probe, `help` is a good first command.
- For an nRF91 modem AT interface, the same call works with
  `{"text": "AT+CFUN?", "expect": "OK|ERROR"}`.
- `newline` defaults to `\r\n`; pass `""` for a device that wants none.

### Wait for something specific

```bash
curl -sS -H "X-UART-Token: $TOKEN" \
  "http://127.0.0.1:8100/wait?pattern=ready&timeout_s=30"
```

Returns as soon as a matching line arrives (200) or on timeout (504, with the
lines seen). Use this instead of sleeping and hoping.

`/wait` watches for *new* lines: it scans from the current cursor unless you
pass `since_seq`. If the line you want may already have been captured, pass the
cursor it would have arrived at (`since_seq=0` after a `/reset`), otherwise the
call times out even though the line is sitting in the buffer.

## DTR and RTS

Nordic DK UART lines are **tri-stated until the connected terminal asserts
DTR**. The server asserts DTR and RTS on open by default, which is what makes
output appear at all.

Assert or drop a line at runtime:

```bash
curl -sS -H "X-UART-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"on": false}' http://127.0.0.1:8100/dtr
```

Start without asserting DTR when the target resets on a DTR pulse and you need
to observe it undisturbed:

```bash
uv run scripts/uart_server.py serve --port <uart_port> --no-dtr --port-num 8100
```

Run this foreground command in the dedicated server terminal described above.

If the port opens cleanly but produces nothing, DTR is one of the first things
to check — see [When nothing appears](../SKILL.md#when-nothing-appears).
