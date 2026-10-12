# ESP32 Power Monitor

MicroPython firmware for an ESP32 reading two INA239 current/power channels
over SPI and showing them on an SSD1306 OLED, with readings mirrored to the
serial console.

## Setup

1. Copy `config.example.py` to `config.py`.
2. Fill in `config.py` with your local Wi-Fi and MQTT broker credentials.
   `config.py` is gitignored and must never be committed.
3. Flash `boot.py`, `config.py`, `main.py`, and the driver modules
   (`ina239.py`, `ads1220.py`, `ssd1306.py`) to the device.

## Note on `config.py`

`main.py` currently does not import `config.py` — it only reads the two
INA239 channels and writes to the OLED and serial console; there is no
Wi-Fi/MQTT connection code in this firmware yet. `config.py` exists as the
credential file for that future integration. Keep credentials there instead
of hardcoding them in `main.py` once Wi-Fi/MQTT support is added.

## Measurement diagnostics

The driver reads the INA239 POWER register as a 24-bit unsigned value.
Each measurement checks DIAG_ALRT before and after reading the values; a math
overflow or trim-memory checksum error rejects the reading. The affected
channel displays a read error on the OLED and an error message on serial,
while the other channel continues. In JSON mode, the failed channel is `null`
and `error0` or `error1` contains the reason.

Run the driver regression tests on a computer with Python 3:

```bash
python3 -m unittest discover -s myprojects/monitor-application/ESP32-power-monitor/tests -v
```

These tests use simulated SPI responses; electrical accuracy and operation on
the ESP32 still require a hardware test.

## Run temporarily from the computer

With MicroPython already installed on the ESP32, run:

```bash
./myprojects/monitor-application/ESP32-power-monitor/run_monitor.sh
```

The default serial port is `/dev/cu.usbserial-0001`. Pass another port as the
first argument if needed. The script uses `mpremote` from PATH or the dedicated
`~/.local/share/esp32-power-monitor-tools` environment; `MPREMOTE_BIN` can
override the executable. It mounts this local application directory and runs
it on the ESP32 without copying files to the device. The original running
program is interrupted. Keep the USB connection and terminal open. Press
Ctrl+C to stop, then press the board Reset button to run its original program.
Close other serial monitors before launching.
