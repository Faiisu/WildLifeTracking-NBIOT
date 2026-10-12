# Domain Model and Glossary — Cellular Asset Tracker

This document defines the key domain concepts and ubiquitous language used throughout the nRF9160-based low-power GPS/MQTT tracker system.

## Domain Concepts

### 1. Tracker
The primary firmware application running on the nRF9160. It manages the execution flow, state transitions, and coordination between cellular connectivity, GNSS positioning, and cloud communication.

### 2. GNSS (Global Navigation Satellite System)
The positioning subsystem inside the nRF9160 modem core. It receives signals from GPS/GLONASS satellites to compute the device's geographical position (latitude, longitude, altitude, and accuracy).

### 3. Cellular Link
The cellular network connection established over LTE-M or NB-IoT. It is managed dynamically to balance data transfer with power consumption.

### 4. MQTT Publisher
The application component responsible for connecting to the remote MQTT broker, authenticating with credentials (username and password), and publishing structured coordinate payloads.

### 5. GPS-First Operation
An operating pattern that maximizes battery life by disabling the LTE transceiver (`LTE_LC_FUNC_MODE_DEACTIVATE_LTE`) during GPS scanning. This prevents RF interference from blocking weak satellite signals and saves power.

### 6. Power Saving (PSM / eDRX)
Low-power configuration modes requested from the cellular network:
- **PSM (Power Saving Mode)**: Allows the device to go into a deep sleep state while remaining registered to the network, avoiding expensive reconnection handshakes.
- **eDRX (extended Discontinuous Reception)**: Extends the paging cycles to reduce receiver activity during active sessions.

### 7. Accelerometer (LIS2DH12)
The on-board 3-axis motion sensor. See [hardware specification](docs/hardware/lis2dh12.md) and [ADR 0002](docs/adr/0002-motion-triggered-gps.md) for integration design.

### 8. Motion Trigger Event
A GPIO interrupt fired by the LIS2DH12 when movement exceeds a configured threshold, signaling the tracker to acquire a new GPS fix. See [ADR 0002](docs/adr/0002-motion-triggered-gps.md).

### 9. MQTT Payload (JSON)
The structured JSON data package published to the MQTT broker. See [CODE_ARCHITECTURE.md](myprojects/gps_mqtt_tracker/CODE_ARCHITECTURE.md) for the current payload schema.

