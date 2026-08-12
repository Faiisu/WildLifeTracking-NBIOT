from machine import SPI, Pin
import time

class INA239:
    # Registers
    REG_CONFIG      = 0x00
    REG_ADC_CONFIG  = 0x01
    REG_SHUNT_CAL   = 0x02
    REG_SHUNT_VOLT  = 0x04
    REG_BUS_VOLT    = 0x05
    REG_CURRENT     = 0x07
    REG_POWER       = 0x08

    def __init__(self,
                 spi,
                 cs_pin,
                 alert_pin,
                 rshunt_ohm=0.2,
                 max_current_a=1,
                 hires_mode=False):
        self.spi = spi
        self.cs = cs_pin
        self.cs.value(1)
        self.alert = alert_pin
        self.hires_mode = hires_mode
        self.current_lsb = max_current_a/(2**15)
        self.rshunt = rshunt_ohm
        self.configure()


    def _write_reg(self, reg, value):
        # INA239: first byte = [ADDR5:ADDR0][0][R/W]
        # Write command => R/W = 0
        cmd = ((reg & 0x3F) << 2)

        tx = bytearray([
            cmd,
            (value >> 8) & 0xFF,
            value & 0xFF
        ])

        self.cs.value(0)
        self.spi.write(tx)
        self.cs.value(1)


    def _read_reg(self, reg):
        # INA239: first byte = [ADDR5:ADDR0][0][R/W]
        # Read command => R/W = 1
        cmd = ((reg & 0x3F) << 2) | 0x01

        tx = bytearray([cmd, 0x00, 0x00])
        rx = bytearray(3)

        self.cs.value(0)
        self.spi.write_readinto(tx, rx)
        self.cs.value(1)

        # rx[0] is junk during address phase
        return (rx[1] << 8) | rx[2]


    def configure(self):
        if self.hires_mode:
            adcrange = 1
        else:
            adcrange = 0
        self._write_reg(self.REG_CONFIG, 0x0000 | (adcrange << 4))

        ###############################################################
        # * continuous measurement for bus voltage, shunt voltage and
        #   temperature
        # * 1052 us conversion time for all measurements
        # * 64 samples average
        ###############################################################
        self._write_reg(self.REG_ADC_CONFIG, 0b1111_101_101_101_011)

        # INA239 datasheet, equation (1)
        if self.hires_mode:
            shunt_cal = int(819.2e6 * self.current_lsb * self.rshunt * 4)
        else:
            shunt_cal = int(819.2e6 * self.current_lsb * self.rshunt)

        if shunt_cal > 32767:
            raise ValueError("SHUNT_CAL cannot exceed 32767")
        if shunt_cal == 0:
            raise ValueError("SHUNT_CAL is zero")

        self._write_reg(self.REG_SHUNT_CAL, shunt_cal)


    def read_bus_voltage(self):
        raw = self._read_reg(self.REG_BUS_VOLT)
        return raw * 3.125e-3  # 3.125 mV per LSB


    def read_shunt_voltage(self):
        raw = self._read_reg(self.REG_SHUNT_VOLT)
        # signed conversion
        if raw & 0x8000:
            raw -= 65536
        return raw * 5e-6  # 5 µV per LSB


    def read_current(self):
        raw = self._read_reg(self.REG_CURRENT)
        if raw & 0x8000:
            raw -= 65536
        return raw * self.current_lsb


    def read_power(self):
        raw = self._read_reg(self.REG_POWER)
        return 0.2 * raw * self.current_lsb
