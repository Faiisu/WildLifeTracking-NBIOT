# ADR 0003: Secure MQTT Over TLS (MQTTS)

## Status
Proposed

## Context
The tracker must send GPS coordinates securely to a custom MQTT broker using client authentication. Plaintext MQTT (port 1883) would expose credentials (username and password) and location data. The user specified using TLS for encrypted communication.

## Decision
We will establish MQTT connections over TLS (port 8883) using Zephyr's native socket layer offloaded to the nRF9160 modem.
1. The broker's root CA certificate will be provisioned to a secure storage tag on the modem (`CONFIG_MQTT_TLS_SEC_TAG`) before the connection is opened.
2. The MQTT client will configure a TLS socket with peer verification enabled (`TLS_PEER_VERIFY_REQUIRED`).
3. Sockets will use offloaded TLS, utilizing the hardware cryptography features of the nRF9160 modem.

## Consequences
- **Encryption**: Credentials and tracking coordinates are fully encrypted in transit.
- **Modem Offloading**: Cryptography operations are offloaded to the modem core, saving main application memory and processor cycles.
- **Certificate Handling**: The application must provision the certificate to the modem once at startup or if it changes.
