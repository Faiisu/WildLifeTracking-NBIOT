---
title: Cellular Asset Tracker — Documentation Hub
tags: [hub, index, senior-project, nrf9160]
category: hub
updated: 2026-11-25
---

# Cellular Asset Tracker — Documentation Hub

Central Obsidian vault for the **nRF9160 Cellular Asset Tracker** Senior Project (nRF Connect SDK v3.3.0, SparkFun Thing Plus nRF9160 / Circuit Dojo Feather nRF9160).

## 6 Core Sections

| # | Section | Hub Note | Scope |
| :---: | :--- | :--- | :--- |
| **1** | **Hardware Spec** | [[hardware/README\|Hardware Spec]] | SparkFun Thing Plus nRF9160, `nRF9160` SiP, `TPS63031` power-latch (`P0.31`/`JMP2`), `LIS2DH12TR`, `W25Q32JV` DPD, `PCF85063A`, solar prototype, and mass budget. |
| **2** | **Firmware & Software** | [[firmware-and-software/README\|Firmware & Software]] | All 7 nRF9160 firmware apps and 2 host monitoring applications under `myprojects/`. |
| **3** | **ADRs** | [[adr/README\|ADRs]] | Architectural Decision Records (`ADR 0001` – `ADR 0006`) aligned with checked-in code. |
| **4** | **Experiments** | [[experiments/README\|Experiments]] | Controlled experiment plans and bench power results (`0001` – `0006`, including LTE vs GNSS & Sleep power). |
| **5** | **Lessons** | [[lessons/README\|Lessons]] | Nordic DevAcademy Cellular IoT Fundamentals notes (`l2` – `l8`) paired with `v3.3.3_practices/`. |
| **6** | **API Contract** | [[api-contract/README\|API Contract]] | [[api-contract/mqtt-payload-schema\|Unified MQTT JSON Payload Schema (v1)]], TLS/QoS broker contract, `gps_monitoring` HTTP/SSE API, and Cell Geolocation rules. |

## Supporting References

- [[AI_CODING_GUIDE|AI Coding Guide]] — Library catalog, initialization order, and build/verification rules
- `../CONTEXT.md` — Domain model & glossary (at repo root)
- `../README.md` — Repository root overview
- `agents/` — Issue tracker, triage labels, and domain doc conventions
