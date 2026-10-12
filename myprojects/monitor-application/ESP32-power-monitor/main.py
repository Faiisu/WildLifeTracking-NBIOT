from machine import Pin, SPI, I2C, SDCard
import time
import ujson
import os
import gc
from ina239 import INA239
from ssd1306 import SSD1306_I2C

###########
# I/O pins
###########
# SPI pins
PIN_SPI_SCK  = 18
PIN_SPI_MOSI = 19
PIN_SPI_MISO = 23
PIN_SPI_CS0  = 5
PIN_SPI_CS1  = 17

# Optional ALERT pin; set to None if not used
PIN_ALERT0 = 25
PIN_ALERT1 = 26

# I2C pins for OLED
PIN_I2C_SCL = 21
PIN_I2C_SDA = 22

#######################
# Electrical constants
#######################
RSHUNT_OHM_0 = 0.2
RSHUNT_OHM_1 = 0.2
MAX_CURRENT_A_0 = 0.5  # expected maximum current in A
MAX_CURRENT_A_1 = 0.5  # expected maximum current in A

# Serial output interval
READ_INTERVAL_MS = 1000

# True: one JSON object per serial line. False: original human-readable text.
SERIAL_OUTPUT_JSON = False

#########################################
class IwingPowerMeterINA239:

    def __init__(self, ina239):
        self.ina239 = ina239

    def read(self):
        self.ina239.check_status()
        v_shunt = self.ina239.read_shunt_voltage()
        v_load = self.ina239.read_bus_voltage()
        i_load = self.ina239.read_current()
        p_load = self.ina239.read_power()
        self.ina239.check_status()
        v_bat = v_shunt + v_load

        return v_bat, v_load, i_load, p_load

#########################################
def main():
    global meter0, meter1, spi  # for debugging

    spi = SPI(
        2,
        baudrate=1_000_000,  # ADS1220 is fine at 1MHz; you can increase later
        polarity=0, phase=1, # SPI mode 1
        sck=Pin(PIN_SPI_SCK),
        mosi=Pin(PIN_SPI_MOSI),
        miso=Pin(PIN_SPI_MISO),
    )

    cs0 = Pin(PIN_SPI_CS0, Pin.OUT, value=1)
    cs1 = Pin(PIN_SPI_CS1, Pin.OUT, value=1)
    alert0 = Pin(PIN_ALERT0, Pin.IN, Pin.PULL_UP) if PIN_ALERT0 is not None else None
    alert1 = Pin(PIN_ALERT1, Pin.IN, Pin.PULL_UP) if PIN_ALERT1 is not None else None

    i2c = I2C(1, sda=Pin(PIN_I2C_SDA), scl=Pin(PIN_I2C_SCL))
    oled = SSD1306_I2C(128, 64, i2c)

    #meter0 = None
    meter0 = IwingPowerMeterINA239(INA239(
        spi=spi,
        cs_pin=cs0,
        alert_pin=alert0,
        rshunt_ohm=RSHUNT_OHM_0,
        max_current_a=MAX_CURRENT_A_0,
        hires_mode=False,
    ))
    #meter1 = None
    meter1 = IwingPowerMeterINA239(INA239(
        spi=spi,
        cs_pin=cs1,
        alert_pin=alert1,
        rshunt_ohm=RSHUNT_OHM_1,
        max_current_a=MAX_CURRENT_A_1,
        hires_mode=False,
    ))

    while True:
        loop_started_ms = time.ticks_ms()
        payload = {
            "uptime_ms": loop_started_ms,
            "channel0": None,
            "channel1": None,
        }

        error0 = None
        if meter0 is not None:
            try:
                b0, v0, i0, p0 = meter0.read()
                payload["channel0"] = {
                    "battery_v": round(b0, 3),
                    "load_v": round(v0, 3),
                    "current_ma": round(i0 * 1000, 3),
                    "power_w": round(p0, 3),
                }
            except OSError as exc:
                error0 = str(exc)

        error1 = None
        if meter1 is not None:
            try:
                b1, v1, i1, p1 = meter1.read()
                payload["channel1"] = {
                    "battery_v": round(b1, 3),
                    "load_v": round(v1, 3),
                    "current_ma": round(i1 * 1000, 3),
                    "power_w": round(p1, 3),
                }
            except OSError as exc:
                error1 = str(exc)

        if error0:
            payload["error0"] = error0
        if error1:
            payload["error1"] = error1

        if SERIAL_OUTPUT_JSON:
            print(ujson.dumps(payload))
        else:
            if error0:
                print("Channel 0 error:", error0)
            elif meter0 is not None:
                print(f"B0 = {b0:.3f} V, V0 = {v0:.3f} V, I0 = {i0*1000:.3f} mA, P0 = {p0:.3f} W")
            if error1:
                print("Channel 1 error:", error1)
            elif meter1 is not None:
                print(f"B1 = {b1:.3f} V, V1 = {v1:.3f} V, I1 = {i1*1000:.3f} mA, P1 = {p1:.3f} W")
            print("------------------------")

        oled.fill(0)
        oled.hline(0, 30, 127, 1)
        if error0:
            oled.text("CH0 read error", 0, 0)
        elif meter0 is not None:
            oled.text(f"I0 = {i0*1000:.3f} mA", 0, 0)
            oled.text(f"V0 = {v0:.3f} V", 0, 10)
            oled.text(f"P0 = {p0*1000:.3f} mW", 0, 20)
        if error1:
            oled.text("CH1 read error", 0, 35)
        elif meter1 is not None:
            oled.text(f"I1 = {i1*1000:.3f} mA", 0, 35)
            oled.text(f"V1 = {v1:.3f} V", 0, 45)
            oled.text(f"P1 = {p1*1000:.3f} mW", 0, 55)
        oled.show()

        elapsed_ms = time.ticks_diff(time.ticks_ms(), loop_started_ms)
        remaining_ms = READ_INTERVAL_MS - elapsed_ms
        if remaining_ms > 0:
            time.sleep_ms(remaining_ms)

#########################################
main()
