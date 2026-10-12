# LIS2DH12TR 3-Axis Accelerometer — Hardware Specification & Integration Guide

เอกสารข้อมูลจำเพาะทางเทคนิคและแนวทางการผสานการทำงานของเซนเซอร์วัดความเร่ง 3 แกน **STMicroelectronics LIS2DH12TR** สำหรับโปรเจกต์ Cellular Asset Tracker เพื่อใช้ในการพัฒนาฟีเจอร์ Motion-Triggered Wakeup ตาม [ADR 0002](../adr/0002-motion-triggered-gps.md)

---

## 1. ภาพรวม (Overview)

**LIS2DH12 (LIS2DH12TR)** ผลิตโดย **STMicroelectronics** เป็นชิปวัดความเร่งเชิงเส้น 3 แกนแบบดิจิทัล (Digital Output 3-Axis Linear Accelerometer) ในตระกูล "femto" โดดเด่นด้วยการใช้พลังงานที่ต่ำมากในระดับไมโครแอมป์ (Ultra-low-power) จึงเหมาะสมอย่างยิ่งสำหรับอุปกรณ์ติดตามทรัพย์สินหรือสัตว์ป่าที่ทำงานด้วยแบตเตอรี่ระยะยาว

---

## 2. คุณสมบัติทางเทคนิคที่สำคัญ (Key Specifications)

| คุณสมบัติ | รายละเอียด |
| :--- | :--- |
| **แกนตรวจวัด (Axes)** | 3 แกน (X, Y, Z) |
| **ช่วงการวัด (Full-Scale)** | เลือกปรับได้ **±2g, ±4g, ±8g, ±16g** |
| **แรงดันไฟฟ้าใช้งาน (Supply Voltage)** | 1.71 V ถึง 3.6 V |
| **แรงดันไฟฟ้าส่วน I/O (IO Supply)** | รองรับ 1.8 V แยกอิสระ |
| **อัตราการกินกระแส (Current Consumption)** | ต่ำสุดถึง **2 µA** ในโหมด Low-power |
| **อัตราการส่งข้อมูล (ODR)** | ตั้งแต่ 1 Hz ถึง 5.3 kHz |
| **ความละเอียด (Resolution)** | 8-bit (Low-power), 10-bit (Normal), 12-bit (High-resolution) |
| **การเชื่อมต่อสื่อสาร (Interface)** | รองรับทั้ง **I2C** และ **SPI** (3-wire / 4-wire) |
| **ขนาดตัวถัง (Package)** | LGA-12 ขนาด 2.0 × 2.0 × 1.0 มม. (ระยะพิตช์ 0.5 มม.) |
| **ช่วงอุณหภูมิทำงาน** | -40 °C ถึง +85 °C |

---

## 3. ฟังก์ชันเด่นภายในชิป (Embedded Features)

* **ขา Interrupt อิสระ 2 ช่อง (INT1 / INT2):** สามารถโปรแกรมเงื่อนไขการแจ้งเตือนได้หลากหลาย
* **การตรวจจับการเคลื่อนไหวและการตก (Motion & Free-fall Detection):** ส่งสัญญาณเตือนเมื่อเกิดการเคลื่อนที่หรือเครื่องตก
* **การตรวจจับการเคาะ (Click / Double-click Recognition):** ตรวจจับการแตะครั้งเดียวหรือแตะสองครั้งบนอุปกรณ์
* **การตรวจจับทิศทาง (6D/4D Orientation):** ใช้สำหรับการหมุนหน้าจอแนวตั้ง/แนวนอน
* **ฟังก์ชัน Sleep-to-wake และ Return-to-sleep:** ช่วยประหยัดพลังงานโดยเปิดการทำงานเฉพาะเมื่อตรวจพบการขยับ
* **บัฟเฟอร์ FIFO ในตัว (32 ระดับ):** ช่วยลดภาระการประมวลผลของตัวควบคุมหลัก (MCU)
* **เซนเซอร์วัดอุณหภูมิในตัว (Embedded Temperature Sensor):** ใช้ตรวจสอบอุณหภูมิเบื้องต้นได้

---

## 4. การนำไปประยุกต์ใช้งานในระบบ Tracker (Applications & Integration Blueprint)

### เป้าหมายในโปรเจกต์
ใช้สำหรับเป็น **Motion-Triggered Wakeup Interrupt** เพื่อปลุก nRF9160 ให้เริ่มกระบวนการหาพิกัดดาวเทียม GNSS เฉพาะเมื่อตรวจพบการเคลื่อนที่ ป้องกันการสิ้นเปลืองพลังงานแบตเตอรี่ในขณะที่อุปกรณ์อยู่นิ่ง (ตามข้อกำหนดใน [ADR 0002](../adr/0002-motion-triggered-gps.md))

รายละเอียดสถาปัตยกรรมการทำงานร่วมกับ Zephyr RTOS, ลำดับการ Interrupt, และ Kconfig dependencies ดูที่ [ADR 0002: Motion-Triggered GPS Acquisition](../adr/0002-motion-triggered-gps.md)

โค้ดตัวอย่างการอ่านค่าเซนเซอร์ผ่าน Zephyr Sensor API: [`samples/accelerometer/`](../../samples/accelerometer/)

---

## 5. เอกสารอ้างอิงที่เกี่ยวข้อง (Related Documentation)
* [ADR 0002: Motion-Triggered GPS Acquisition using LIS2DH12 Accelerometer](../adr/0002-motion-triggered-gps.md)
* [Domain Model & Glossary (LIS2DH12 Accelerometer)](../../CONTEXT.md)
* [AI Coding Guide](../../docs/AI_CODING_GUIDE.md)
