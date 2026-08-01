# ADR 0002: Motion-Triggered GPS Acquisition using LIS2DH12 Accelerometer

## Status
Proposed

## Context
Periodic GPS tracking consumes significant battery power when the device is stationary. To optimize battery life for long-term usage without charging, we must avoid unnecessary GPS scans. The SparkFun Thing Plus nRF9160 board has an on-board ST LIS2DH12 accelerometer connected via I2C.

## Decision
We will configure the LIS2DH12 accelerometer in low-power trigger mode (using Zephyr's sensor trigger subsystem). The sensor will be configured to detect motion (using threshold/slope detection) and fire an interrupt.
When motion is detected:
1. The sensor interrupt handler will signal the main application thread (using a semaphore `motion_detected_sem`).
2. The main thread will wake up, initiate a GPS scan, and attempt to acquire a fix.
3. If no motion is detected, the device remains in deep sleep.

## Consequences
- **Power Efficiency**: Avoids GPS scans when the tracker is stationary.
- **Immediate Response**: Wakes up and tracks immediately when movement begins.
- **Hardware Interrupts**: Relies on Zephyr's sensor subsystem and interrupts, allowing the main CPU to remain in sleep state.
