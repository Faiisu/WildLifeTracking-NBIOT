# LTE and MQTT Publish Test

This application isolates LTE registration and MQTT publishing from
`gps_mqtt_tracker`. It contains no GNSS, CoAP, LED, button, or MQTT subscription
logic.

It connects to `test.mosquitto.org:1883` and publishes the following payload to
`nrf91/test/data` every 30 seconds:

```json
{"message":"hello from nRF91"}
```

Configure the broker, topic, client ID, payload, and interval in `Kconfig` or
with `west build -t menuconfig`. This is a plaintext MQTT connectivity test; do
not use it for confidential data.

## Build and flash

From the repository root in an initialized nRF Connect SDK v3.3.0 workspace:

```bash
west build -p always -b sparkfun_thing_plus/nrf9160/ns \
  myprojects/LTE_n_MQTT --sysbuild
west flash
```

For an nRF9160 DK, replace the board argument with
`nrf9160dk/nrf9160/ns`.

Monitor the published data from a computer with Mosquitto clients installed:

```bash
mosquitto_sub -h test.mosquitto.org -p 1883 -t nrf91/test/data -v
```
