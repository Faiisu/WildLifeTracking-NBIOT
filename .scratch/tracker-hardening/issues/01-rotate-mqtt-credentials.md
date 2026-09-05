# 01: Rotate leaked MQTT credentials and remove secret from ESP32 monitor

**What to build:** The ESP32 power-monitor firmware connects to the KU IoT MQTT broker without a hardcoded, committed password. `myprojects/monitor-application/ESP32-power-monitor/config.py` currently contains a real broker hostname, username, and password in plaintext, tracked in git. The device should keep connecting and publishing exactly as before, but the actual secret must live in a file that is never committed, with a `config.example.py` template showing the shape without real values.

**Blocked by:** None (can start immediately)

**Status:** ready-for-human (one step needs the account owner)

- [ ] **Needs you:** rotate the MQTT password at `iot.cpe.ku.ac.th` for user `b6510503492` (old value is dead) — I don't have access to that account
- [x] `config.py` is excluded from git tracking going forward (`git rm --cached`, added to a new `.gitignore` in the folder)
- [x] `config.example.py` template exists documenting the required fields with placeholder values
- [ ] ~~The ESP32 monitor still connects and publishes successfully with the new credentials~~ — doesn't apply: `main.py` never imports `config.py`. There is no Wi-Fi/MQTT connection code in this firmware; it only reads sensors and writes to OLED/serial. Noted in the new README so this isn't rediscovered as a mystery later.
- [x] README added documenting the local config-file setup

## Comments

Implemented the code/repo side (secret detracked, example template, README).
The credential itself is still live at the broker until you rotate it there —
please change the password for `b6510503492` on the KU IoT MQTT broker, then
update your local (gitignored) `config.py` with the new value. Once that's
done this ticket can move to `resolved`.
