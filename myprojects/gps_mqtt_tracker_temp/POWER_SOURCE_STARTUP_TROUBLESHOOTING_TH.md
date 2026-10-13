# วิเคราะห์ปัญหาบอร์ดเริ่มทำงานเฉพาะเมื่อเสียบ USB-C

เอกสารนี้ใช้กับ `gps_mqtt_tracker` ที่ build สำหรับ
`sparkfun_thing_plus/nrf9160/ns` บน nRF Connect SDK v3.3.0 และบอร์ด SparkFun
Thing Plus nRF9160 (WRL-17354 / schematic BRD-0016)

## สรุปผลการวิเคราะห์ (ยืนยันผลการทดสอบจริงแล้ว)

ข้อสรุปจากการทดสอบบนฮาร์ดแวร์จริง:
- เมื่อเสียบ **USB-C**: บอร์ดจะเริ่มทำงานทันทีโดยอัตโนมัติ เนื่องจากมีสัญญาณ VBUS ไปปลุกวงจร power-latch
- เมื่อจ่ายไฟจาก **แหล่งจ่ายภายนอก / แบตเตอรี่** หรือ **จ่ายไฟผ่านพอร์ต JTAG / Debugger (SWD)**: บอร์ดจะไม่เริ่มทำงานอัตโนมัติ **ต้องกดปุ่ม MODE (Button) 1 ครั้งก่อนเสมอ** เพื่อสร้างสัญญาณ Wake Pulse ปลุกวงจร buck/boost ให้เริ่มจ่ายไฟ จากนั้น nRF9160 จะ boot และขับขา P0.31 (`NRF_PS_EN`) เป็น High เพื่อค้างไฟเลี้ยง 3.3V ให้ระบบทำงานต่อไปได้ตามปกติ

ผลการทดสอบนี้ยืนยันว่าเฟิร์มแวร์และฮาร์ดแวร์ทำงานได้สมบูรณ์ ไม่ได้มีความผิดปกติที่ตัวชิปหรือโค้ด แต่เป็นพฤติกรรมการออกแบบวงจร Power-latch ตามธรรมชาติของบอร์ด SparkFun Thing Plus nRF9160


> [!CAUTION]
> Schematic ทางการของ WRL-17354 ระบุ **J4 เป็นขั้ว U.FL ของเสา LTE** และ
> **J5 เป็นขั้วแบตเตอรี่ LiPo** ไม่ควรจ่ายไฟเข้า J4 หากบอร์ดที่ใช้อยู่พิมพ์
> `J4` ไว้ที่ขั้ว power จริง ให้หยุดและตรวจ part number, PCB revision และ
> schematic ของ revision นั้นก่อน เพราะอาจไม่ใช่ BRD-0016 ที่เอกสารนี้อ้างอิง

## เหตุใด USB-C และ MODE จึงทำให้บอร์ดเริ่มทำงาน

ภาคจ่ายไฟของบอร์ดใช้ TPS63031 buck/boost สร้าง 3.3 V และมีวงจร latch ที่รับ
สัญญาณจากเส้นทาง USB/WAKE และ `NRF_PS_EN` ลำดับการทำงานโดยสรุปคือ

```text
USB/สัญญาณ MODE ปลุกวงจร
          |
          v
TPS63031 เริ่มจ่าย 3.3 V
          |
          v
nRF9160 เริ่ม boot และ Zephyr เริ่มทำงาน
          |
          v
P0.31 / NRF_PS_EN เป็น High เพื่อค้างไฟเลี้ยง
```

ไฟล์ board ของ NCS v3.3.0 กำหนด regulator `pwr-3v3` เป็น
`regulator-always-on` และใช้ P0.31 แบบ active-high เป็นขา enable ดังนั้น
หลังจาก CPU เริ่ม boot แล้ว Zephyr จะสั่งค้างไฟให้โดยอัตโนมัติ แต่ firmware
ไม่สามารถสั่ง P0.31 ได้ในช่วงที่ CPU ยังไม่มีไฟ นี่คือเหตุผลที่แก้ cold start
ด้วยโค้ดอย่างเดียวไม่ได้

USB-C สามารถสร้างเงื่อนไขเริ่มจ่ายไฟให้วงจรได้อัตโนมัติ (ตรวจจับแรงดัน VBUS) ส่วนการจ่ายไฟจากฝั่ง VBAT/LiPo, แหล่งจ่ายภายนอก หรือ **การจ่ายไฟผ่านพอร์ต JTAG / Programmer / Debugger (SWD)** จะไม่มีสัญญาณ VBUS ไปกระตุ้นวงจร latch จึง **ต้องกดปุ่ม MODE หนึ่งครั้ง** เพื่อสร้าง wake pulse ให้วงจร buck/boost เริ่มทำงาน เมื่อ CPU มีไฟและ nRF9160 เริ่ม boot ระบบ Zephyr จะสั่งยกขา P0.31 (`NRF_PS_EN`) ค้างไฟเลี้ยง 3.3V ให้ทำงานต่อเนื่องได้เองโดยอัตโนมัติ

ข้อสรุปนี้ได้รับการยืนยันจากการทดสอบจริงแล้วว่า การต่อจ่ายไฟผ่านพอร์ต JTAG เพียงอย่างเดียว บอร์ดจะไม่เริ่มทำงานจนกว่าจะกดปุ่ม MODE


## หลักฐานจาก configuration ปัจจุบัน

- Devicetree ของ `sparkfun_thing_plus/nrf9160/ns` กำหนด P0.31 เป็น enable
  ของ fixed regulator 3.3 V และกำหนดให้ regulator เปิดตลอดหลัง boot
- Effective build configuration เปิด `CONFIG_REGULATOR=y` และ
  `CONFIG_REGULATOR_FIXED=y`
- `CONFIG_STARTUP_LED=y` ทำให้ LED สีน้ำเงิน D7 เปลี่ยนสถานะทุก 1 วินาที
  หรือครบหนึ่งรอบติด/ดับทุก 2 วินาที ใช้ยืนยันได้ว่า application เริ่มทำงานแล้ว
- Log ปัจจุบันใช้ RTT และปิด UART/serial console ดังนั้นการไม่มี serial log
  เมื่อถอด USB-C ไม่ได้แปลว่า application หยุดทำงาน ให้ใช้ heartbeat LED หรือ
  MQTT message เป็นตัวตรวจยืนยันแทน

ไม่มีเงื่อนไขใน `main.c` ที่ตรวจ USB, VBUS หรือรอ serial terminal ก่อนเริ่ม
modem, GNSS, LTE หรือ MQTT

## วิธีใช้งานโดยไม่แก้ฮาร์ดแวร์

1. ตรวจสอบก่อนว่าแหล่งจ่ายต่อเข้าขั้ว LiPo/VBAT หรือ 5V ที่ถูกต้อง ไม่ใช่
   U.FL antenna connector
2. ตั้งแหล่งจ่ายให้อยู่ในช่วง 2.8–5.5 V ตามข้อมูลของ SparkFun และตรวจ polarity ให้ถูกต้อง หากใช้ขั้ว JST ให้ใช้ single-cell LiPo ที่ตรง pinout
3. จ่ายไฟ (จากแบตเตอรี่ LiPo, แหล่งจ่ายภายนอก หรือสายจ่ายไฟของพอร์ต JTAG / Programmer) แล้ว **กดปุ่ม MODE หนึ่งครั้ง** เพื่อปลุกบอร์ดให้เริ่มทำงาน
4. ตรวจว่า LED D7 blink ทุก 1 วินาที หาก LED ค้างใน bootloader ให้กด RESET หนึ่งครั้งโดยไม่กด MODE ค้าง
5. ยืนยันการทำงานต่อด้วย MQTT publish ที่ broker ไม่จำเป็นต้องต่อ USB-C หรือใช้ RTT เพื่อให้ application ทำงาน


SparkFun แนะนำไม่ให้ใช้แบตเตอรี่ต่ำกว่า 300 mAh แหล่งจ่าย bench supply,
สายไฟ และ connector ต้องรองรับกระแสกระชากของ modem ด้วย ไม่ควรใช้ค่าเฉลี่ย
กระแสอย่างเดียวในการตั้ง current limit

## วิธีตั้งเป็น Always-on ด้วย JMP2

วิธีนี้เหมาะเมื่อระบบต้องเริ่มทันทีเมื่อมีไฟและไม่ต้องการกด MODE เป็นเพียง
คำแนะนำการดัดแปลงฮาร์ดแวร์ ยังไม่ได้ดำเนินการหรือทดสอบกับบอร์ดจริง

> [!WARNING]
> ถอด USB, battery, programmer และแหล่งจ่ายทั้งหมดก่อนตัด trace หรือบัดกรี
> ยืนยัน PCB revision และตำแหน่ง JMP2 จาก schematic/ภาพ board ของ revision
> จริงก่อนทุกครั้ง หากไม่แน่ใจให้ใช้วิธีกด MODE แทน

สำหรับ WRL-17354 ตาม SparkFun Hookup Guide:

1. ถ่ายรูปหรือทำเครื่องหมายตำแหน่ง bridge เดิมไว้ เพื่อให้ย้อนกลับได้
2. ตัด existing short ของ JMP2 และตรวจด้วยมัลติมิเตอร์ว่าเปิดวงจรแล้ว
3. เชื่อม **pad กลางกับ pad ขวา** ของ JMP2 เพื่อเลือก `Always on`
4. ตรวจว่าไม่มี solder bridge ไปยัง pad ที่สาม ก่อนต่อแหล่งจ่าย
5. จ่ายไฟที่ input ที่ถูกต้องและตรวจ 3.3 V ก่อนทดสอบ GNSS/LTE

ผลกระทบของโหมด Always-on:

- บอร์ดควรเริ่มทันทีเมื่อมีไฟ โดยไม่ต้องกด MODE
- `PS_EN` จะไม่ควบคุมการตัดไฟหลักแบบเดิม
- firmware ไม่สามารถปิด buck/boost เพื่อลดกระแสเหลือระดับ power-off ของบอร์ด
- กระแสขณะ idle/standby ของระบบจริงอาจสูงขึ้น จึงต้องวัดใหม่หลังดัดแปลง
- การย้อนกลับต้องแก้ solder bridge ของ JMP2 ให้ตรงกับค่าเดิม

## ขั้นตอนวัดเพื่อยืนยันก่อนดัดแปลง

ใช้มัลติมิเตอร์หรือ oscilloscope วัดอย่างน้อย VBAT/5V input และ 3V3 โดยใช้
GND เดียวกับบอร์ด บันทึกแรงดันต่ำสุดและ current limit ของแหล่งจ่ายในแต่ละกรณี

| การทดสอบ | ผลที่คาด / ผลจริง | ความหมาย |
| --- | --- | --- |
| USB-C อย่างเดียว | D7 เริ่ม blink ทันที | firmware boot ได้อัตโนมัติจากสัญญาณ VBUS |
| จ่ายไฟผ่าน JTAG อย่างเดียว (ไม่กด MODE) | บอร์ดไม่เริ่มทำงาน (3V3 ไม่ถูก latch) | **ยืนยันผลจริงแล้ว**: พอร์ต JTAG ไม่มีสัญญาณ VBUS ไปปลุกวงจร latch |
| จ่ายไฟผ่าน JTAG แล้วกดปุ่ม MODE 1 ครั้ง | D7 เริ่ม blink และ boot ปกติ | **ยืนยันผลจริงแล้ว**: การกดปุ่ม MODE สร้าง wake pulse ให้วงจร latch ทำงาน |
| External/VBAT อย่างเดียว ไม่กด MODE | 3V3 ไม่ถูก latch | ตรวจเส้นทาง wake/power-latch |
| External/VBAT แล้วกด MODE | D7 เริ่ม blink | ยืนยัน cold-start/power-latch |
| External ที่ขา 5V/VUSB | ควรตรวจเทียบคู่มือของ revision | ถ้าไม่ boot ให้ตรวจ wiring และ voltage sag |
| ช่วง GNSS search | 3V3 ต้องไม่ตกจน reset | ตรวจ source/cable/current limit |
| LTE registration/MQTT publish | ไม่มี reset และ publish สำเร็จ | ตรวจรับกระแสกระชากของ modem |

หาก 3V3 มีขึ้นชั่วคราวแล้วตกทันทีหลังปล่อย MODE ให้ตรวจว่า P0.31 ถูกขับเป็น
High, firmware image ตรงกับ board target และบอร์ดไม่ได้ค้างอยู่ใน bootloader
หาก 3V3 คงอยู่แต่ tracker ไม่ทำงาน ปัญหาจึงค่อยย้ายไปตรวจ firmware, reset
reason หรือ modem initialization

## ผลการทดสอบที่ยืนยันแล้วและขอบเขตที่เหลือ

### ผลที่ยืนยันแล้ว (Verified):
- **การจ่ายไฟผ่านพอร์ต JTAG / Debugger**: ยืนยันแล้วว่าบอร์ดจะไม่เริ่มทำงานเอง ต้องกดปุ่ม MODE (Button) 1 ครั้งเพื่อสร้าง wake pulse ให้วงจร buck/boost เริ่มจ่ายไฟ จากนั้นเฟิร์มแวร์จะค้างไฟเลี้ยงให้ทำงานต่อได้ตามปกติ ไม่ใช่ข้อผิดพลาดของบอร์ดหรือโค้ด

### ขอบเขตที่ยังไม่ได้ตรวจ (Pending Measurement):
- ยังไม่ได้วัดแรงดันจริงที่ input และ 3V3 ด้วย oscilloscope
- ยังไม่ได้วัด peak current หรือ voltage sag ระหว่าง LTE transmit
- ยังไม่ได้ดัดแปลง JMP2 หรือทดสอบ Always-on



## เอกสารอ้างอิง

- [SparkFun Thing Plus nRF9160 product page](https://www.sparkfun.com/sparkfun-thing-plus-nrf9160.html)
- [SparkFun nRF9160 Thing Plus Hookup Guide](https://learn.sparkfun.com/tutorials/nrf9160-thing-plus-hookup-guide/all)
- [SparkFun nRF9160 Thing Plus schematic](https://cdn.sparkfun.com/assets/5/7/c/a/c/nRF9160_Thing_Plus.pdf)
- NCS v3.3.0 board definition:
  `zephyr/boards/sparkfun/thing_plus/sparkfun_thing_plus_nrf9160_common.dtsi`

