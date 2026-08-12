# MQTT connection test

This isolated nRF91 application verifies the complete HiveMQ Cloud path:

```text
modem init -> provision server CA -> LTE registration -> TLS MQTT CONNECT
-> username/password authentication -> publish -> disconnect
```

It uses only a broker CA for TLS verification. MQTT authentication uses a
username and password; no client certificate or private key is used.

## Local setup

Create the ignored local configuration file:

```bash
cp config/mqtt-credentials.conf.example config/mqtt-credentials.conf
```

Set the broker host, port, client ID, topic, payload, QoS, and security tag in
`config/mqtt.conf`; set only username and password in the ignored local file.
Add the broker root CA PEM as `credentials/ca-cert.pem`. Both local credential
assets are ignored by Git.

## Expected output

The successful sequence contains these messages:

```text
Broker CA provisioned in sec tag 24
LTE registered
MQTT CONNACK accepted
MQTT publish submitted
MQTT connection test passed
```

Build with:

```bash
west build -p always -b sparkfun_thing_plus/nrf9160/ns \
  myprojects/mqtt_connection_test --sysbuild
```

The test logs to the board UART so the complete connection result can be
captured without an RTT viewer.
