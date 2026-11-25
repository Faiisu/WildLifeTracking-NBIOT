---
title: Hardware Specification
tags: [hardware, index, sparkfun-nrf9160, power]
category: hardware
updated: 2026-11-25
---

# Hardware Specification

Hardware architecture, on-board ICs, power-latch behavior, deep-sleep hardware boundaries, and power/mass constraints for the **SparkFun Thing Plus nRF9160** (`sparkfun_thing_plus/nrf9160/ns`, WRL-17354 / BRD-0016; also compatible with `circuitdojo_feather/nrf9160/ns`).

## 1. Core Board & On-Board ICs (Verified from Devicetree)

| Component | Bus / Pin | Role in Project | Low-Power / Deep-Sleep Capability |
| :--- | :--- | :--- | :--- |
| **Nordic nRF9160 SiP** | App Core + LTE-M/NB-IoT + GNSS Modem | Primary MCU, cellular radio, GNSS receiver, offloaded TLS sockets | **System ON Idle**, **Modem PSM** (~2.7–4 µA SiP spec), **Radio Off** (`DEACTIVATE_LTE`/`DEACTIVATE_GNSS`), **System OFF** (`sys_poweroff()`). Shared radio requires GPS-first sequencing. |
| **TI TPS63031 + Power Latch** | `P0.31` (`NRF_PS_EN`), `MODE` (`sw0`, `P0.12`) | Generates 3.3 V rail; latched High by Zephyr (`regulator-always-on`) | On battery/JTAG boot, press `MODE` once. Driving `P0.31` Low cuts 3.3 V completely. Keeping 3.3 V alive in System OFF requires `JMP2 = Always on` (untested). |
| **ST LIS2DH12TR Accelerometer** | `&i2c1` (`0x18`), `INT1` on `P0.29` (`accel0`) | Motion detection / wake-up ([[adr/0002-motion-triggered-gps\|ADR 0002]]) | ~2 µA in 8-bit low-power mode; ODR=0 power-down (~0.5 µA). Requires `anym-on-int1` overlay for `P0.29` any-motion. |
| **Winbond W25Q32JV NOR Flash** | `&spi3` (`cs` `P0.07`, `wp` `P0.08`, `hold` `P0.10`) | 4 MB external storage (`has-dpd`) | Deep Power-Down (DPD) via `CONFIG_PM_DEVICE=y` + `CONFIG_SPI_NOR=y` + `PM_DEVICE_ACTION_SUSPEND`. |
| **NXP PCF85063A RTC** | `&i2c1` (`0x51`) | External low-power RTC | Sub-µA RTC; no `int-gpios` declared in board DTS. |
| **GNSS Antenna / COEX0** | `P0.13` (Low at boot), `AT%XCOEX0=1,1,1565,1586` | Enables GNSS LNA/antenna routing | Configured via `CONFIG_MODEM_ANTENNA=y` + `circuitdojo_gnss.c`. |
| **Microchip MCP73831 Charger** | USB-C `VBUS` → `J5` LiPo (`R12 = 2 kΩ`, ~500 mA ceiling) | On-board single-cell LiPo charger + PMOS (`Q3`) load sharing | Hardware-fixed charge current (`Icharge ≈ 1000 / R12_kΩ`). |

> [!caution]
> On WRL-17354, **`J4` is the LTE U.FL antenna connector** and **`J5` is the LiPo battery connector**. Verify silkscreen and polarity before applying power.

## 2. Deep-Sleep Hardware Boundaries

| Sleep Mode | Internal Timer? | RAM Preserved? | On-Board `LIS2DH12` Wake? | `MODE` Wake? | Hardware Mod? |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **System ON Idle + PSM / Radio Off** | Yes (`k_sleep`) | Yes | Yes (`P0.29`) | Yes (`P0.12`) | None |
| **nRF9160 System OFF (`sys_poweroff`)** | No | No | Only with `JMP2 = Always on` | Yes (`P0.12`) | `JMP2 = Always on` for 3.3 V retention |
| **Board Power-Latch OFF (`P0.31 = 0`)** | No | No | No (3.3 V off) | Yes | External VBAT wake circuit |

## 3. Hardware Notes

- [[hardware/power-source-startup-troubleshooting|External Power Startup & Power-Latch Troubleshooting]]
- [[hardware/lis2dh12|LIS2DH12TR Accelerometer Specification & Integration Guide]]
- [[hardware/solar-power-prototype/README|Solar Power Prototype (BOM, Wiring & Schematic)]] *(Proposed bench prototype)*
- [[hardware/solar-panel-shortlist|Solar Panel Shortlist & Constraints]]
- [[hardware/tracker-mass-constraint|Tracker Mass Constraint (≤ 240 g)]]
- `power-consumption.xlsx` — Power consumption workbook

---
Back to [[README|Documentation Hub]]
