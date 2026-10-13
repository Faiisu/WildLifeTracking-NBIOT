# GPS MQTT State Control

A separate NCS v3.3.0 application for running the tracker one state at a time.
It compiles the existing modem module from `../gps_mqtt_tracker/src`; keep both
application directories together. The GNSS session, backlog records, MQTT logging
variant, status filter, LED heartbeat, main loop, and configuration belong to this
application. The automatic tracker retains its original log format.

## Board and button

Use `sparkfun_thing_plus/nrf9160/ns` for the SparkFun Thing Plus nRF9160.
The MODE button is devicetree alias `sw0`, active low with pull-up on P0.12.
The RESET button reboots the device. CircuitDojo Feather nRF9160 is also declared
as a build target and uses the same button alias.

After automatic UI/modem/MQTT initialization, the PSM request is disabled until
the operator executes ENABLE_PSM. RTT logs show:

```text
READY: WAIT_FOR_GNSS; press the user button to execute
RUN: WAIT_FOR_GNSS
READY: CONNECT_LTE; press the user button to execute
```

Press and release MODE once for each state. The Zephyr GPIO keys driver debounces
the input. A press while READY starts that state. During a GPS session, one more
press ends acquisition and selects the latest valid fix, or a no-fix report.
Holding the button does not repeat. Presses during initialization and other
running states are discarded. A session-ending press never also starts LTE.

## State sequence

| State | One press executes | Next state |
| --- | --- | --- |
| WAIT_FOR_GNSS | Start continuous GNSS; press MODE again to finish, or wait up to 180 seconds; buffer the latest valid fix or a null-location report; stop GNSS | CONNECT_LTE after a completed session; ENTER_LOW_POWER on setup/read/stop errors |
| CONNECT_LTE | Activate LTE and wait up to 60 seconds for home/roaming registration | CONNECT_MQTT on success; ENTER_LOW_POWER on failure |
| CONNECT_MQTT | Connect to the broker and wait up to 15 seconds for accepted CONNACK | PUBLISH_BACKLOG on success; DISCONNECT_MQTT on failure |
| PUBLISH_BACKLOG | Send all retained fixes oldest first; wait up to 15 seconds per QoS 1 PUBACK | DISCONNECT_MQTT when empty or delivery fails |
| DISCONNECT_MQTT | Request disconnect and wait up to 10 seconds | ENABLE_PSM on success; retry DISCONNECT_MQTT on error |
| ENABLE_PSM | Deactivate LTE, enable the PSM request, re-register LTE (up to 60 seconds), then wait up to 60 seconds for a PSM sleep event | DISABLE_PSM; deactivate LTE as fallback if PSM entry is unconfirmed |
| DISABLE_PSM | Deactivate LTE to leave PSM and return radio control to GNSS; disable future PSM requests | WAIT_FOR_LOW_POWER on success; retry DISABLE_PSM on error |
| WAIT_FOR_LOW_POWER | Wait for a separate MODE press to enter LOW_POWER | LOW_POWER |
| LOW_POWER | Remain blocked with LTE off and GNSS stopped until MODE is pressed; that press returns to the GPS-ready state | WAIT_FOR_GNSS |
| ENTER_LOW_POWER | Retry GNSS stop if needed; wait for PSM up to 60 seconds, or deactivate LTE when PSM is unavailable | DISABLE_PSM on success; retry ENTER_LOW_POWER on cleanup failure |

Every transition waits for another press, including failure paths. The user must
press through disconnect, PSM enable, and PSM disable after an upload attempt.
GNSS/LTE failures use ENTER_LOW_POWER for cleanup, then DISABLE_PSM.
Initialization failures stop the application before READY.

GPS sessions run continuously until a second MODE press or the 180-second
application timeout (`config/tracker.conf`). A valid fix alone does not end the
session. Each valid PVT frame replaces the candidate fix; invalid frames do not
replace a previous valid candidate. At session end, the latest valid fix is
buffered. If no valid fix was observed, a no-fix report is buffered instead:

```json
{"lat":null,"lon":null,"alt":null,"acc":null,"time":null}
```

JSON uses `null`, not `none`. Unknown time is also null; no stale position or
invented zero coordinate is used. Both report types follow the normal LTE/MQTT
button sequence and remain buffered until delivery succeeds. GNSS is stopped
before any upload, including sessions ended without a fix. GPS diagnostics retain
their previous presentation. Button polling is bounded to about 100 ms even if
PVT frames stop arriving. GNSS uses the modem's continuous interval value 1;
`TRACKER_PERIODIC_INTERVAL` is not used by this manual session variant. The modem
retry setting does not bound continuous navigation; the application deadline does.
LTE is deactivated before acquisition to preserve GPS-first radio sequencing.

Backlog entries remain in RAM after failed delivery; they are removed only when
the configured MQTT delivery operation succeeds (PUBACK for the default QoS 1).
They are lost on reboot. If MQTT disconnects during a long pause before publishing,
the publish may fail; the entry stays buffered for a later manually stepped cycle.
Waiting at CONNECT_MQTT or PUBLISH_BACKLOG can leave LTE/MQTT active. This is a
manual inspection application; its logging and pauses are not a power benchmark.
The blue board LED (`led0`, D7/P0.03) pulses once while initializing or waiting
at READY, twice during GNSS acquisition, three times during LTE connection, and
four times during MQTT connection/upload. The pulse is 200 ms with a 1000 ms
pause after each group, configured in `config/hardware.conf`. Heartbeat pulses
continue while waiting at DISABLE_PSM; LED activity does not indicate whether the
modem is in PSM and adds LED/application activity during power measurements.

## Manual PSM control

The normal sequence is:

```text
WAIT_FOR_GNSS -> CONNECT_LTE -> CONNECT_MQTT -> PUBLISH_BACKLOG
-> DISCONNECT_MQTT -> ENABLE_PSM -> DISABLE_PSM -> WAIT_FOR_LOW_POWER --MODE--> LOW_POWER --MODE--> WAIT_FOR_GNSS
```

A PSM request does not force the network to grant PSM. ENABLE_PSM negotiates the
request with a new LTE registration after MQTT disconnect. It waits for an actual
modem PSM sleep event, including an event received before the wait starts.
`PSM sleeping` means that the application observed PSM entry.
`PSM entry not confirmed; deactivating LTE as fallback` means that entry was not confirmed within the
configured timeout; LTE is deactivated as fallback. The `LTE connected - [PSM enabled]` or `[PSM disabled]` line reports
the network PSM grant; `enabled` does not by itself confirm sleep entry. A timeout/fallback does not claim successful PSM.

When `READY: DISABLE_PSM` appears after confirmed entry, leave the board untouched
to observe PSM. The modem can wake autonomously for network procedures; waiting at
this state does not guarantee it stays asleep. Press MODE to deactivate LTE and
send `lte_lc_psm_req(false)`. LTE remains deactivated until a later CONNECT_LTE.
If enabling fails, DISABLE_PSM is still the next step so the request can be cleared.
Disabling failures keep DISABLE_PSM ready for another press.

After DISABLE_PSM succeeds, the application shows `READY: WAIT_FOR_LOW_POWER`
and waits for another MODE press. That press enters LOW_POWER and logs `LOW_POWER: LTE off, GNSS stopped; press MODE to return to GPS`. The main thread
blocks on the button semaphore; it does not restart GNSS or reconnect LTE by timer.
One MODE press exits LOW_POWER and shows `READY: WAIT_FOR_GNSS`; the next press
starts acquisition, preserving one state per press. The heartbeat LED remains
enabled in LOW_POWER. This is a radio-off application wait, not system-off or modem
library shutdown; the CPU can idle between interrupts, but LED/log activity remains.

## Readable logs

GNSS diagnostics and `STATUS: GNSS ...` lines keep their existing format.
Verbose shared modem/backlog information is filtered out; warnings and errors
remain visible. READY/RUN prompts remain so every manual step is identifiable.
Normal LTE and MQTT messages use the following text (the logging backend still
adds timestamps and module names). This illustrative packet is not a measured fix:

```text
LTE connecting...
LTE connected
LTE connected - [PSM disabled]
MQTT connecting...
MQTT connected
MQTT sending...
MQTT sent
{"lat":13.750000,"lon":100.500000,"alt":12.0,"acc":3.0,"time":"2026-10-13T08:09:10Z"}
MQTT disconnected
```

Each successfully delivered backlog entry prints its own exact transmitted JSON
payload after `MQTT sent`. For QoS 1, success is printed only after successful
PUBACK. Timeout, rejected acknowledgement, and send errors do not print a success
line or payload. QoS 0 labels success as lacking broker acknowledgement.

During ENABLE_PSM, a fresh LTE registration produces another `LTE connecting...`
line. When granted, the PSM update shows `LTE connected - [PSM enabled]`, followed
by `PSM sleeping` only on confirmed entry. DISABLE_PSM shows
`LTE disconnected - [PSM disabled]` because it deactivates LTE for the next GNSS
step. Network refusal remains visible as `[PSM disabled]` and a warning.

## Local MQTT setup

Defaults enable TLS on port 8883. Create ignored `config/mqtt-credentials.conf`
from `config/mqtt-credentials.conf.example` and supply your broker hostname,
username/password, and a distinct client ID when running multiple devices.
Put the broker root CA in ignored `credentials/ca-cert.pem`.
Local deployment files were reused from the original app when available during
creation; they are separate copies and later changes do not synchronize.

## Build and verification

From an initialized NCS v3.3.0 west workspace:

```sh
west build -p always -b sparkfun_thing_plus/nrf9160/ns \
  /path/to/workspace/myprojects/gps_mqtt_stateControl --sysbuild \
  -d /path/to/workspace/myprojects/gps_mqtt_stateControl/build-sparkfun
```

The output is `build-sparkfun/merged.hex`. Flashing is a separate hardware action.
Host checks exercise the actual state runner and button callback using mocks:

```sh
python3 myprojects/gps_mqtt_stateControl/tests/test_state_control.py
python3 myprojects/gps_mqtt_stateControl/tests/test_tracker_ui.py
python3 myprojects/gps_mqtt_stateControl/tests/test_gnss_session.py
```

On hardware, check one READY/RUN pair per press, no queued busy presses, an outdoor
valid fix, LTE registration, broker receipt/PUBACK, disconnect, and PSM or fallback.
Host mocks do not verify GPIO debounce, modem timing, RF sequencing, TLS, network
reachability, or power consumption.
