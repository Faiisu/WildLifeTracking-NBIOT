import ast
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ina239', APP / 'ina239.py')
driver = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'machine': types.SimpleNamespace(SPI=object, Pin=object)}):
    spec.loader.exec_module(driver)

# Load the measurement wrapper without running the hardware application.
tree = ast.parse((APP / 'main.py').read_text())
wrapper = next(node for node in tree.body if isinstance(node, ast.ClassDef))
namespace = {}
exec(compile(ast.Module(body=[wrapper], type_ignores=[]), 'main.py', 'exec'), namespace)


class FakePin:
    def __init__(self):
        self.level = 1

    def value(self, level):
        self.level = level


class FakeSPI:
    def __init__(self):
        self.registers = {}
        self.frames = []
        self.fail = False

    def write(self, tx):
        if self.fail:
            raise OSError('SPI failure')
        self.registers[tx[0] >> 2] = int.from_bytes(tx[1:], 'big')

    def write_readinto(self, tx, rx):
        self.frames.append(bytes(tx))
        if self.fail:
            raise OSError('SPI failure')
        reg = tx[0] >> 2
        width = 3 if reg == 0x08 else 2
        data = self.registers.get(reg, 0).to_bytes(width, 'big')
        rx[1:] = data[:len(rx) - 1]


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.spi = FakeSPI()
        self.pin = FakePin()
        self.meter = driver.INA239(self.spi, self.pin, None, max_current_a=0.5)
        self.spi.registers[0x0B] = 1

    def test_power_reads_all_three_bytes(self):
        for raw in (0, 0x123456, 0xFFFFFF):
            self.spi.registers[0x08] = raw
            self.assertAlmostEqual(self.meter.read_power(), 0.2 * raw * self.meter.current_lsb)
            self.assertEqual(self.spi.frames[-1], b'\x21\x00\x00\x00')

    def test_voltage_and_signed_current_keep_16_bit_reads(self):
        self.spi.registers.update({0x05: 1600, 0x04: 0xFFFF, 0x07: 0xFFFF})
        self.assertEqual(self.meter.read_bus_voltage(), 5.0)
        self.assertEqual(self.meter.read_shunt_voltage(), -5e-6)
        self.assertEqual(self.meter.read_current(), -self.meter.current_lsb)
        self.assertTrue(all(len(frame) == 3 for frame in self.spi.frames))

    def test_status_rejects_invalid_measurements(self):
        for status, message in ((0, 'checksum'), (0x0201, 'overflow')):
            self.spi.registers[0x0B] = status
            with self.assertRaisesRegex(OSError, message):
                namespace['IwingPowerMeterINA239'](self.meter).read()
        self.spi.registers[0x0B] = 1
        self.assertEqual(self.meter.check_status(), 1)

    def test_wrapper_checks_status_after_measurement(self):
        original = self.meter.read_power
        def overflow_during_read():
            self.spi.registers[0x0B] = 0x0201
            return original()
        self.meter.read_power = overflow_during_read
        with self.assertRaisesRegex(OSError, 'overflow'):
            namespace['IwingPowerMeterINA239'](self.meter).read()

    def test_spi_failure_releases_chip_select(self):
        self.spi.fail = True
        for operation in (self.meter.read_power, lambda: self.meter._write_reg(0, 0)):
            with self.assertRaises(OSError):
                operation()
            self.assertEqual(self.pin.level, 1)


if __name__ == '__main__':
    unittest.main()
