# ADR 0004: Cellular Network Priority (NB-IoT first, then LTE-M fallback)

## Status
Proposed

## Context
The tracker will operate in low-power cellular network environments. The user prioritized NB-IoT first (narrowband, lower power, better coverage) and LTE-M second.

## Decision
We will configure the modem's system settings to prioritize NB-IoT and fallback to LTE-M:
1. In `myprojects/gps_mqtt_tracker/config/modem.conf`, we set
   `CONFIG_LTE_NETWORK_MODE_LTE_M_NBIOT_GPS=y`.
2. At runtime, we will programmatically configure the modem's search order using the nRF Link Control API or AT commands, ensuring that NB-IoT is searched first, and LTE-M is used as a fallback if NB-IoT registration is unavailable.
3. The modem will be configured with PSM (Power Saving Mode) and eDRX requests to maximize battery life once registered.

## Consequences
- **Low Power Consumption**: Prioritizing NB-IoT aligns with long-term battery goals.
- **Coverage Redundancy**: Falls back to LTE-M if NB-IoT is not supported in the local cellular cell.
