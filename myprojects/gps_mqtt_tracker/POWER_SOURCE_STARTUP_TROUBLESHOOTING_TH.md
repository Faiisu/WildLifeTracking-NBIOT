# วิเคราะห์ปัญหาบอร์ดเริ่มทำงานเฉพาะเมื่อเสียบ USB-C

เอกสารนี้ใช้กับ `gps_mqtt_tracker` ที่ build สำหรับ
`sparkfun_thing_plus/nrf9160/ns` บน nRF Connect SDK v3.3.0 และบอร์ด SparkFun
Thing Plus nRF9160 (WRL-17354 / schematic BRD-0016)

## สรุปผลการวิเคราะห์

อาการที่พบคือโปรแกรมเริ่มทำงานเมื่อเสียบ USB-C แต่ไม่เริ่มเมื่อจ่ายไฟจาก
แหล่งจ่ายภายนอก จนกว่าจะกดปุ่ม MODE เมื่อกด MODE แล้ว tracker เริ่มทำงานได้
ตามปกติ

ผลนี้ชี้ว่า firmware, nRF9160 และภาคจ่ายไฟ 3.3 V สามารถทำงานได้ สาเหตุหลัก
อยู่ที่เส้นทางเริ่มต้นของวงจร power-latch หรือจุดที่นำแหล่งจ่ายภายนอกเข้าบอร์ด
ไม่ใช่เพราะโปรแกรมต้องใช้ USB-C

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

USB-C สามารถสร้างเงื่อนไขเริ่มจ่ายไฟให้วงจรได้ ส่วนการจ่ายจากฝั่ง VBAT/LiPo
อาจต้องกด MODE เพื่อสร้าง wake pulse ก่อน ซึ่งตรงกับผลทดสอบที่กด MODE แล้ว
tracker เริ่มทำงาน

อย่างไรก็ตาม คู่มือ SparkFun ระบุว่าสามารถจ่ายไฟภายนอกผ่านขา 5V ได้ หาก
แหล่งจ่ายถูกต่อเข้าขา 5V/VUSB ของบอร์ดรุ่นนี้แล้วบอร์ดยังไม่เริ่มเอง ให้ตรวจ
สาย, GND ร่วม, polarity, current limit, แรงดันตก และ PCB revision ก่อนสรุปว่า
เป็นพฤติกรรมปกติของ power-latch

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
2. ตั้งแหล่งจ่ายให้อยู่ในช่วง 2.8–5.5 V ตามข้อมูลของ SparkFun และตรวจ
   polarity ให้ถูกต้อง หากใช้ขั้ว JST ให้ใช้ single-cell LiPo ที่ตรง pinout
3. จ่ายไฟ แล้วกด MODE หนึ่งครั้งเพื่อปลุกบอร์ด
4. ตรวจว่า LED D7 blink ทุก 1 วินาที หาก LED ค้างใน bootloader ให้กด RESET
   หนึ่งครั้งโดยไม่กด MODE ค้าง
5. ยืนยันการทำงานต่อด้วย MQTT publish ที่ broker ไม่จำเป็นต้องต่อ USB-C หรือ
  ใช้ RTT เพื่อให้ application ทำงาน

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

| การทดสอบ | ผลที่คาด | ความหมาย |
| --- | --- | --- |
| USB-C อย่างเดียว | D7 เริ่ม blink | firmware boot ได้ |
| External/VBAT อย่างเดียว ไม่กด MODE | 3V3 อาจไม่ถูก latch | ตรวจเส้นทาง wake/power-latch |
| External/VBAT แล้วกด MODE | D7 เริ่ม blink | ยืนยัน cold-start/power-latch |
| External ที่ขา 5V/VUSB | ควรตรวจเทียบคู่มือของ revision | ถ้าไม่ boot ให้ตรวจ wiring และ voltage sag |
| ช่วง GNSS search | 3V3 ต้องไม่ตกจน reset | ตรวจ source/cable/current limit |
| LTE registration/MQTT publish | ไม่มี reset และ publish สำเร็จ | ตรวจรับกระแสกระชากของ modem |

หาก 3V3 มีขึ้นชั่วคราวแล้วตกทันทีหลังปล่อย MODE ให้ตรวจว่า P0.31 ถูกขับเป็น
High, firmware image ตรงกับ board target และบอร์ดไม่ได้ค้างอยู่ใน bootloader
หาก 3V3 คงอยู่แต่ tracker ไม่ทำงาน ปัญหาจึงค่อยย้ายไปตรวจ firmware, reset
reason หรือ modem initialization

## ขอบเขตที่ยังไม่ได้ตรวจบนฮาร์ดแวร์

- ยังไม่ได้วัดแรงดันจริงที่ input และ 3V3
- ยังไม่ได้วัด peak current หรือ voltage sag ระหว่าง LTE transmit
- ยังไม่ได้ยืนยัน PCB revision และ connector ที่ผู้ใช้เรียกว่า J4
- ยังไม่ได้ดัดแปลง JMP2 หรือทดสอบ Always-on

## เอกสารอ้างอิง

- [SparkFun Thing Plus nRF9160 product page](https://www.sparkfun.com/sparkfun-thing-plus-nrf9160.html)
- [SparkFun nRF9160 Thing Plus Hookup Guide](https://learn.sparkfun.com/tutorials/nrf9160-thing-plus-hookup-guide/all)
- [SparkFun nRF9160 Thing Plus schematic](https://cdn.sparkfun.com/assets/5/7/c/a/c/nRF9160_Thing_Plus.pdf)
- NCS v3.3.0 board definition:
  `zephyr/boards/sparkfun/thing_plus/sparkfun_thing_plus_nrf9160_common.dtsi`

