# ADR 0001: Use Zephyr Native MQTT Library instead of nRF MQTT Helper

## Status
Proposed

## Context
The system needs to connect to a custom MQTT broker using client authentication (username and password credentials). 
The nRF Connect SDK provides a high-level `mqtt_helper` library, which wraps the underlying Zephyr MQTT API to simplify socket management and connections. However, standard versions of the `mqtt_helper` library do not expose username and password fields in `struct mqtt_helper_conn_params`, requiring developers to modify the SDK source code directly to support credential-based authentication.

## Decision
We will use the standard Zephyr MQTT Library (`<zephyr/net/mqtt.h>`) directly instead of the Nordic `mqtt_helper` wrapper. 

The standard Zephyr MQTT library natively supports username and password authentication via the `struct mqtt_client` configuration, and maps directly to the BSD socket offload layer on the nRF9160 modem.

## Consequences
- **SDK Independence**: We do not need to modify internal SDK helper files, ensuring clean code upgradeability.
- **Explicit Socket Management**: We will handle connection states and socket events (reconnect, polling, keepalive) explicitly in our application layer.
- **Portability**: The code remains fully compliant with the standard Zephyr RTOS networking stack.
