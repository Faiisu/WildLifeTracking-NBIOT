# 06: Move tracker broker endpoint and credentials out of tracked config

**What to build:** `config/mqtt.conf` for the GPS MQTT tracker commits a real HiveMQ Cloud cluster hostname, and the local `config/mqtt-credentials.conf` overlay uses a weak placeholder password (`admin`/`00000000`) against a broker reachable from the public internet. After this ticket, both the broker hostname/port and the credentials are deployment-specific values kept out of tracked files, with a real rotated password in place and an example file documenting the required overlay for a fresh checkout.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] The production HiveMQ Cloud hostname is no longer present in a tracked config file
- [ ] `config/mqtt-credentials.conf` (or successor) uses a rotated, non-trivial password rather than `admin`/`00000000`
- [ ] `config/mqtt-credentials.conf.example` (or equivalent) documents every field a new deployment needs to set
- [ ] README explains how to configure a fresh checkout to point at a broker without editing tracked files
