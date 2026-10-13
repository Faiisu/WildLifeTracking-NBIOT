# Continuous GNSS RTT baseline

This temporary application follows `samples/gps` using the GNSS sample from
NCS v3.3.0 (`nrf/samples/cellular/gnss`). `src/main.c` is copied from that sample;
the startup banner and diagnostic output are adapted for RTT. Board startup
also schedules a heartbeat LED. Kconfig reuses the pinned SDK sample.

GNSS runs continuously with interval 1, retry 0, normal accuracy, no duty cycling,
and no assistance. PVT uses the sample's latest-value storage and semaphore.
No LTE registration or MQTT upload is started. GPIO13 and COEX0 preserve the
SparkFun Thing Plus nRF9160 antenna/power setup. Existing tracker source/config
files remain in this copied folder but are not compiled or loaded.

Both logging and printf output use RTT up channel 0. Look for the startup banner,
`GPS status: SEARCHING` or `GPS status: FIX_VALID`, with `Tracking`, `Using`,
`Unhealthy` and PVT flags on each PVT update. Valid fixes also log latitude,
longitude and accuracy. Radio blocking/window flags have explicit status logs.
Detailed PVT and `$GPGSV`/`$GPGSA` output remain available. Screen-clearing ANSI
sequences are removed so the RTT viewer retains the status history. Output is nonblocking and can drop if the RTT viewer does not drain it.
Continuous GNSS remains active until reset or replacement firmware is flashed.

The blue `led0` heartbeat is on for 1 second and off for 1 second, independently
of GNSS fix status. RTT reports `Heartbeat LED enabled: 1000 ms on/off`.
`CONFIG_GNSS_HEARTBEAT_LED` disables/enables it and
`CONFIG_GNSS_HEARTBEAT_INTERVAL_MS` controls each phase. The heartbeat indicates
that the kernel workqueue is running; it does not confirm a GNSS fix.

```sh
west build -p always -b sparkfun_thing_plus/nrf9160/ns \
  myprojects/gps_mqtt_tracker_temp --sysbuild \
  -d myprojects/gps_mqtt_tracker_temp/build-gnss-baseline
```

[Comparison plan and verification](../../docs/experiments/0002-gnss-continuous-baseline/README.md).

## Flash locally

Close the existing VS Code/J-Link debug session, power the board, and run:

```sh
./flash_gps_mqtt_tracker_temp.sh
```

Run this from the repository root. The script builds this application, programs
only firmware memory ranges at SWD 100 kHz, verifies the write, and resets the
board. Reconnect the RTT viewer to up channel 0 after a successful flash.
The current build has passed; agent flash attempts were not confirmed because
of target voltage/reset access errors. See the experiment run for details.
