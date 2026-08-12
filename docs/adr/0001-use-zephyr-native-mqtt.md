# ADR 0001: Use Zephyr Native MQTT Library instead of nRF MQTT Helper

## Status
Superseded by the MQTT Helper API in the pinned NCS v3.3.0 release

## Context
The system needs to connect to a custom MQTT broker using client authentication
(username and password credentials). Earlier MQTT Helper versions were assumed
not to expose authentication fields, which motivated this proposal.

## Decision
The proposed decision was to use the standard Zephyr MQTT library
(`<zephyr/net/mqtt.h>`) directly instead of the Nordic `mqtt_helper` wrapper.

The repository is now pinned to NCS v3.3.0. Its
`struct mqtt_helper_conn_params` exposes `user_name` and `password`, and MQTT
Helper forwards both fields to the native Zephyr MQTT client. The tracker
therefore keeps MQTT Helper and supplies the optional credentials through those
fields without modifying the SDK.

The standard Zephyr MQTT library natively supports username and password authentication via the `struct mqtt_client` configuration, and maps directly to the BSD socket offload layer on the nRF9160 modem.

## Consequences
- **SDK Independence**: No SDK source modification is required.
- **Smaller application change**: Existing MQTT Helper callback, semaphore,
  keepalive, DNS, socket, and cleanup behavior remains intact.
- **Credential handling**: A real password belongs in the ignored local config
  overlay and must never be logged or committed.
