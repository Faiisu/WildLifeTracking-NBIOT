# External Power Startup and Power-Latch Behavior (SparkFun Thing Plus nRF9160)

This note applies to `gps_mqtt_tracker` (and `gps_mqtt_stateControl`) built for
`sparkfun_thing_plus/nrf9160/ns` on nRF Connect SDK v3.3.0 and the SparkFun
Thing Plus nRF9160 (WRL-17354 / schematic BRD-0016).

## Confirmed Hardware Behavior

Observed on hardware:
- **Via USB-C**: The board boots automatically because VBUS triggers the
  on-board power-latch circuit.
- **Via external supply, LiPo battery, or JTAG/SWD debugger power**: The board
  does not auto-start on power application alone. **Press the `MODE` button
  (`sw0`, P0.12) once** to send a wake pulse to the buck/boost power latch;
  once the nRF9160 boots, Zephyr drives `P0.31` (`NRF_PS_EN`) high to latch
  the 3.3 V supply.

This is a hardware power-latch design characteristic of the SparkFun Thing Plus
nRF9160, not a firmware or chip defect.

> [!CAUTION]
> In the WRL-17354 schematic, **J4 is the LTE U.FL antenna connector** and
> **J5 is the LiPo battery connector**. Verify your PCB revision and silkscreen
> before connecting any power source.

## Why USB-C and `MODE` Start the Board

The board uses a TPS63031 buck/boost regulator (3.3 V) gated by a latch circuit
driven by USB VBUS / `MODE` wake and `NRF_PS_EN` (`P0.31`):

```text
USB VBUS or MODE wake pulse
          |
          v
TPS63031 enables 3.3 V rail
          |
          v
nRF9160 boots Zephyr
          |
          v
P0.31 / NRF_PS_EN driven High (regulator-always-on)
```

In NCS v3.3.0 (`sparkfun_thing_plus_nrf9160_common.dtsi`), `pwr-3v3` is defined
with `enable-gpios = <&gpio0 31 GPIO_ACTIVE_HIGH>` and `regulator-always-on`.
Zephyr latches `P0.31` after boot, but firmware cannot assert `P0.31` while the
CPU is unpowered during cold start.

## Evidence in Current Configuration

- Devicetree defines `P0.31` as the `pwr-3v3` fixed regulator enable pin.
- `CONFIG_STARTUP_LED=y` blinks the blue LED `D7` (`led0`) in groups of
  200 ms pulses with a 1000 ms pause between groups (2 blinks = GNSS finding,
  3 blinks = LTE finding, 4 blinks = LTE registered / MQTT upload; off during
  init and low-power preparation).
- `config/logging.conf` routes logs exclusively over SEGGER RTT and disables
  UART/serial console (`CONFIG_SERIAL=n`, `CONFIG_CONSOLE=n`), so the absence
  of USB-UART output does not mean the firmware stopped running.
- `main.c` never waits for USB VBUS or a serial terminal before starting modem,
  GNSS, LTE, or MQTT operations.

## Operating Without Modifying Hardware

1. Confirm power is connected to the correct LiPo/VBAT or 5 V input (never an
   U.FL RF connector).
2. Keep supply voltage within SparkFun's specified range (2.8–5.5 V) with
   correct polarity; SparkFun recommends at least a 300 mAh LiPo to handle LTE
   TX current peaks.
3. After applying external/battery/JTAG power, **press `MODE` once** to wake
   the 3.3 V rail.
4. Confirm `D7` begins its blink-group pattern (if `D7` stays solid in the
   bootloader, press `RESET` once without holding `MODE`).
5. Verify runtime behavior over SEGGER RTT or by observing MQTT messages at the
   broker.

## Optional `JMP2` Always-On Hardware Modification

If unattended auto-start on battery/external power without pressing `MODE` is
required, SparkFun's Hookup Guide documents changing `JMP2` from its default
pad configuration to `Always on` (center pad bridged to the right pad after
cutting the existing trace). This has **not** been tested or applied on project
hardware.

> [!WARNING]
> Disconnect USB, battery, programmer, and all power supplies before cutting or
> soldering `JMP2`, and verify your exact PCB revision schematic first.

Expected trade-offs of `Always on`:
- The board powers up immediately when supply voltage is applied.
- `P0.31` (`PS_EN`) can no longer shut down the 3.3 V buck/boost rail.
- Standby/sleep board current may differ and must be re-measured.

## Verification Status

- **Verified on hardware**: JTAG/debugger power alone does not latch 3.3 V;
  pressing `MODE` once wakes the regulator and boots the firmware normally.
- **Pending hardware measurement**: Oscilloscope capture of VBAT/5 V and 3V3
  rails during LTE TX bursts, peak current sag checks, and `JMP2` testing.

## References

- [SparkFun Thing Plus nRF9160 product page](https://www.sparkfun.com/sparkfun-thing-plus-nrf9160.html)
- [SparkFun nRF9160 Thing Plus Hookup Guide](https://learn.sparkfun.com/tutorials/nrf9160-thing-plus-hookup-guide/all)
- [SparkFun nRF9160 Thing Plus schematic](https://cdn.sparkfun.com/assets/5/7/c/a/c/nRF9160_Thing_Plus.pdf)
- NCS v3.3.0 board definition:
  `zephyr/boards/sparkfun/thing_plus/sparkfun_thing_plus_nrf9160_common.dtsi`
