[Experiment plan](README.md) | [Experiment index](../README.md)

# Energy Consumption (MQTT)

Literature review topic: the effect of increasing JSON payload size on energy
consumption when publishing through MQTT.

Reviewed: 2026-10-13. Evidence type: **External reference**. These are published
measurements on other systems, not measurements of `gps_mqtt_tracker`. The local
experiment remains **Planned**.

## Answer supported by the literature

Increasing serialized JSON bytes can increase transmission energy, but no
universal percentage follows from payload size alone. The studies below show
small increases under some conditions and substantial increases under others.
Their measurement boundaries differ, so their percentages must not be averaged
or transferred directly to this tracker.

## 1. Direct MQTT over LTE-M and NB-IoT

Labdaoui, Nouvel, and Dutertre, *Comparative Analysis of LTE-M and NB-IoT Node
Battery Life in Different Coverage Environments*, GRETSI 2023.
[Conference paper, Table 1](https://gretsi.fr/data/colloque/pdf/2023_labdaoui1078.pdf).
No DOI was identified in the paper.

**External reference, measured:** SARA-R422S radio only, 3.8 V, Keithley
DMM6500, Mosquitto; Orange LTE-M and SFR NB-IoT. Table 1 transmission-phase
energy excludes other board components and separately reported attachment,
MQTT connection, disconnection, and sleep.
[Source: Sections 3–5 and Table 1](https://gretsi.fr/data/colloque/pdf/2023_labdaoui1078.pdf).

| Radio / condition | 1-byte payload, mJ | 1024-byte payload, mJ | Calculated increase relative to 1 byte |
| --- | ---: | ---: | ---: |
| LTE-M / outdoor | 2127 | 2139 | 0.56% |
| LTE-M / indoor | 2220 | 2270 | 2.25% |
| LTE-M / 30 dB added attenuation | 3631 | 5014 | 38.09% |
| NB-IoT / outdoor | 2105 | 2320 | 10.21% |
| NB-IoT / indoor | 2114 | 2710 | 28.19% |
| NB-IoT / 30 dB added attenuation | 4424 | 13597 | 207.35% |

Percentages are calculated: `100 * (E1024 - E1) / E1`. Sample counts,
standard deviations, and QoS/TLS settings are unreported. Payloads are not
identified as JSON; this 1024-fold comparison cannot predict N-to-2N JSON.
[Source: paper](https://gretsi.fr/data/colloque/pdf/2023_labdaoui1078.pdf).

## 2. Direct JSON-based MQTT over Wi-Fi

Mayo et al., *Energy Profiling of Secure MQTT over WiFi on ESP32-Based IoT
Devices*, IEEE ICC 2026. DOI:
[10.1109/ICC59461.2026.11587875](https://doi.org/10.1109/ICC59461.2026.11587875).
[Author-uploaded manuscript, Sections III-B/III-C and Table III](https://www.researchgate.net/publication/400694817_Energy_Profiling_of_Secure_MQTT_over_WiFi_on_ESP32-Based_IoT_Devices).

**External reference, measured:** ESP32-WROOM-32, MQTT 3.1.1/PubSubClient,
optional TLS 1.2, INA219 at 5 Hz on the 3.3 V supply. The baseline is 158-byte
sensor JSON; larger messages append plaintext padding. At a 600-second interval,
Table III gives:
[Source: manuscript](https://www.researchgate.net/publication/400694817_Energy_Profiling_of_Secure_MQTT_over_WiFi_on_ESP32-Based_IoT_Devices).

| Mode | 158 bytes, mean mA | 4096 bytes, mean mA | Calculated increase relative to 158 bytes |
| --- | ---: | ---: | ---: |
| Active, TLS | 76.46 | 78.28 | 2.38% |
| Deep Sleep, TLS | 18.83 | 19.38 | 2.92% |

These are window-average currents, not energy per PUBLISH. At equal voltage
and duration, relative energy and average-current increases are equal.
The authors also report approximately 89 to 93 mA
without TLS at a one-second interval. Sampling may miss short peaks; the
600-second case contains only three transmissions in 30 minutes, and no
statistical significance test was performed. This Wi-Fi result is not a cellular
estimate. The paper's “below 2%” claim concerns TLS overhead, not payload growth.
[Source: Sections III-C/IV and Table III](https://www.researchgate.net/publication/400694817_Energy_Profiling_of_Secure_MQTT_over_WiFi_on_ESP32-Based_IoT_Devices).

## 3. Indirect NB-IoT evidence explaining fixed overhead

Michelinakis et al., *Dissecting Energy Consumption of NB-IoT Devices
Empirically*, IEEE Internet of Things Journal, 8(2), 2021 (online 2020). DOI:
[10.1109/JIOT.2020.3013949](https://doi.org/10.1109/JIOT.2020.3013949).
[Author manuscript](https://arxiv.org/html/2004.07127).

**External reference, measured; UDP, not MQTT:** Otii Arc measurements isolate
u-blox SARA-N211 and Quectel BC95-G modules on two commercial operators.
With default settings, increasing 20-byte to 512-byte UDP packets adds only
tens of mJ while inactivity-timer energy is on the order of joules. With
Release Assistance Indication (RAI) removing that waiting phase, the authors
report size-related energy increases around 60%, never exceeding 100%, across
their compared cases. Thus, reducing fixed overhead can make payload cost more
visible. Neither comparison isolates JSON formatting or includes MQTT/TCP/TLS.
The reported distribution spans module, operator, and coverage combinations;
60% is not an N-to-2N slope or a tracker prediction.
[Source: Sections III-A and V-A/V-B](https://arxiv.org/html/2004.07127).

## Implication for the planned tracker experiment

**Inference and planned work:** A useful interpretation is
`E_upload = E_fixed + E_payload_dependent`, where the second term can change
with radio conditions and packet boundaries. This is a conceptual decomposition,
not a fitted model or measured equation for this project.

Measure both publish-through-PUBACK energy and total upload energy for the
same N-to-2N payloads, following the [existing experiment plan](README.md).
Keep message count, interval, QoS, TLS, registration state, and low-power
transition controlled; repeat under recorded radio conditions. Count actual
UTF-8 JSON bytes, including field names and punctuation. Measure new sensor
acquisition separately. Until those runs exist, no claim such as “doubling JSON
costs 1%” is supported for `gps_mqtt_tracker`.
