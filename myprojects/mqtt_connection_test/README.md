# MQTT connection test

This standalone nRF91 application verifies the end-to-end TLS MQTT path:

```text
modem init -> provision broker CA -> LTE registration -> TLS MQTT CONNECT
-> username/password authentication -> publish -> disconnect
```

It uses only a broker CA chain for TLS server verification. Client
authentication uses username and password; no client certificate or private key
is used.

## Local setup

Create the ignored local credentials overlay from the template:

```bash
cd myprojects/mqtt_connection_test
cp config/mqtt-credentials.conf.example config/mqtt-credentials.conf
```

`config/mqtt.conf` sets the TLS helper options, default port (`8883`), topic
(`test/nrf91`), payload (`mqtt_connection_test`), QoS (`0`), and security tag
(`24`), while leaving `CONFIG_MQTT_TEST_BROKER_HOSTNAME`,
`CONFIG_MQTT_TEST_USERNAME`, and `CONFIG_MQTT_TEST_PASSWORD` empty. Because
`src/main.c` exits early if any of those three settings is empty, set your
broker hostname, username, and password in `config/mqtt-credentials.conf`.

Place the broker root CA PEM at `credentials/ca-cert.pem`. Both
`config/mqtt-credentials.conf` and `credentials/ca-cert.pem` are ignored by Git.

## Expected output

The successful sequence logs:

```text
Broker CA provisioned in sec tag 24
LTE registered (home|roaming)
MQTT CONNACK accepted
MQTT publish submitted (QoS 0)
MQTT connection test passed
```

Build with:

```bash
west build -p always -b sparkfun_thing_plus/nrf9160/ns \
  myprojects/mqtt_connection_test --sysbuild
```

`config/logging.conf` enables the UART serial console (`CONFIG_LOG_BACKEND_UART=y`,
`CONFIG_UART_CONSOLE=y`) so test output can be captured over USB-UART without an
RTT viewer.
