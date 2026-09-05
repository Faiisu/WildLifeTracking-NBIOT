# 01: Rotate leaked MQTT credentials and remove secret from ESP32 monitor

**What to build:** The ESP32 power-monitor firmware connects to the KU IoT MQTT broker without a hardcoded, committed password. `myprojects/monitor-application/ESP32-power-monitor/config.py` currently contains a real broker hostname, username, and password in plaintext, tracked in git. The device should keep connecting and publishing exactly as before, but the actual secret must live in a file that is never committed, with a `config.example.py` template showing the shape without real values.

**Blocked by:** None (can start immediately)

**Status:** resolved (rotation waived — see below)

- [x] ~~Rotate the MQTT password at `iot.cpe.ku.ac.th`~~ — **waived by user decision on 2026-09-05.** The password was NOT rotated. The user accepted the risk of the old value (`b6510503492` / `***REDACTED-ROTATED-CREDENTIAL***`) remaining valid at the broker, rather than blocking on it.
- [x] `config.py` is excluded from git tracking going forward (`git rm --cached`, added to a new `.gitignore` in the folder, `.env` also covered)
- [x] `config.example.py` template exists documenting the required fields with placeholder values
- [x] ~~The ESP32 monitor still connects and publishes successfully with the new credentials~~ — doesn't apply: `main.py` never imports `config.py`. There is no Wi-Fi/MQTT connection code in this firmware; it only reads sensors and writes to OLED/serial. Noted in the new README.
- [x] README added documenting the local config-file setup

## Comments

Implemented the code/repo side (secret detracked, example template, README)
in commit `aa60920`. The credential itself was never rotated at the broker —
the user explicitly chose to accept that risk rather than wait on it, so this
ticket is closed with the old password still technically live at
`iot.cpe.ku.ac.th`. Ticket 02 (git history purge) proceeds on that basis: it
will remove the credential from reachable history, but the broker-side value
itself stays whatever it was before this ticket unless rotated later.
