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
