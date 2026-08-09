# SparkFun nRF9160 Thing Plus Blinky

This standalone Zephyr application follows the SparkFun nRF9160 Thing Plus
Blinky example, updated for the repository's pinned nRF Connect SDK v3.3.0.
It blinks the board's onboard blue LED once per second and enables MCUboot so
the build produces an image suitable for the board's USB serial DFU flow.

## Build

From the workspace root, using the NCS v3.3.0 toolchain environment:

```sh
west -z /Volumes/Mac_storage/opt/nordic/ncs/v3.3.0/zephyr build \
  --build-dir myprojects/sparkfun_blinky/build \
  myprojects/sparkfun_blinky \
  --pristine \
  --board sparkfun_thing_plus/nrf9160/ns
```

The DFU image is generated at:

```text
myprojects/sparkfun_blinky/build/zephyr/app_update.bin
```

## Upload with the onboard MCUboot serial bootloader

1. Hold **MODE**.
2. Press and release **RESET** while continuing to hold **MODE**.
3. Release **MODE** when the LED turns on.
4. Upload and reset the board:

```sh
newtmgr -c serial image upload myprojects/sparkfun_blinky/build/zephyr/app_update.bin
newtmgr -c serial reset
```

The onboard blue LED should change state every second. Serial log messages use
115200 baud, 8 data bits, no parity, and 1 stop bit.

To change the interval, set `CONFIG_BLINK_INTERVAL_MS` in `prj.conf` and rebuild.
