# 01: Rotate leaked MQTT credentials and remove secret from ESP32 monitor

**What to build:** The ESP32 power-monitor firmware connects to the KU IoT MQTT broker without a hardcoded, committed password. `myprojects/monitor-application/ESP32-power-monitor/config.py` currently contains a real broker hostname, username, and password in plaintext, tracked in git. The device should keep connecting and publishing exactly as before, but the actual secret must live in a file that is never committed, with a `config.example.py` template showing the shape without real values.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The MQTT password in use at the broker has been rotated (old value is dead)
- [ ] `config.py` (or equivalent) is excluded from git tracking going forward
- [ ] A `config.example.py` (or similar) template exists documenting the required fields with placeholder values
- [ ] The ESP32 monitor still connects and publishes successfully with the new credentials
- [ ] README/setup docs for the monitor note how to create the local config file
