from machine import Pin, SPI
import time

# Electrical constants (edit to your circuit)
VREF = 2.048              # ADR3420 output (V)


# ----------------------------
# ADS1220 minimal driver
# ----------------------------

class ADS1220:
    # Commands (ADS1220)
    CMD_RESET   = 0x06
    CMD_START   = 0x08
    CMD_POWERDOWN = 0x02
    CMD_RDATA   = 0x10
    CMD_RREG    = 0x20  # 0010 rrrr + (nregs-1)
    CMD_WREG    = 0x40  # 0100 rrrr + (nregs-1)

    def __init__(self,
                 spi: SPI,
                 cs: Pin,
                 drdy: Pin | None = None):
        self.spi = spi
        self.cs = cs
        self.cs.init(Pin.OUT, value=1)
        self.drdy = drdy
        if self.drdy is not None:
            self.drdy.init(Pin.IN, Pin.PULL_UP)

    def _xfer(self, data: bytes) -> bytes:
        self.cs.value(0)
        out = bytearray(len(data))
        self.spi.write_readinto(data, out)
        self.cs.value(1)
        return bytes(out)

    def _write(self, data: bytes) -> None:
        self.cs.value(0)
        self.spi.write(data)
        self.cs.value(1)

    def reset(self):
        self._write(bytes([self.CMD_RESET]))
        time.sleep_ms(5)

    def start(self):
        self._write(bytes([self.CMD_START]))

    def write_regs(self, start_reg: int, values: bytes):
        # start_reg 0..3, up to 4 regs
        n = len(values)
        cmd = self.CMD_WREG | ((start_reg & 0x03) << 2) | ((n-1) & 0x03)
        self._write(bytes([cmd]) + values)

    def read_regs(self, start_reg: int, n: int) -> bytes:
        cmd = 0x20 | ((start_reg & 0x03) << 2) | ((n-1) & 0x03)
        resp = self._xfer(bytes([cmd]) + bytes([0x00] * n))
        return resp[1:]  # skip command echo bytes

    def wait_drdy(self, timeout_ms: int = 200):
        if self.drdy is None:
            # Fallback: wait a bit (depends on data rate)
            time.sleep_ms(timeout_ms)
            return True
        t0 = time.ticks_ms()
        while self.drdy.value() == 1:
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                return False
        return True

    def read_data24(self) -> int:
        # Read 24-bit conversion result, signed two's complement
        #self._write(bytes([self.CMD_RDATA]))
        # ADS1220 outputs 3 bytes (MSB first)
        self.cs.value(0)
        raw = bytearray(3)
        self.spi.readinto(raw, 0x00)
        self.cs.value(1)
        #print(raw)

        val = (raw[0] << 16) | (raw[1] << 8) | raw[2]
        # Sign extend 24-bit
        if val & 0x800000:
            val -= 0x1000000
        return val

    def code_to_volts(self, code: int) -> float:
        # ADS1220 is 24-bit with sign bit; full-scale is +/- (2^23 - 1)
        # Use 2^23 for scaling (common approach)
        return (code / (1 << 23)) * VREF

    def configure_single_ended(self, ain_pos: int, data_rate_sps: int = 20):
        """
        Configure for single-ended measurement:
          - MUX: AINp = ain_pos, AINn = AVSS (GND)
          - PGA bypass (gain=1)
          - External reference (REFP/REFN pins)
          - Continuous conversion
          - Data rate set
        """

        # --- Register 0: MUX + PGA + PGA_BYPASS ---
        # MUX bits: AINp/AINn selection
        # We'll use the common mapping:
        #   AINp = AIN0..AIN3, AINn = AVSS
        # Check your ADS1220 mux mapping if you use other combos.
        #
        # Many ADS1220 drivers use:
        #   mux = 0b1000 for AIN0-AVSS, 0b1001 for AIN1-AVSS, etc.
        # We'll implement that pattern:
        if ain_pos not in (0, 1, 2, 3):
            raise ValueError("ain_pos must be 0..3")

        mux = 0x08 + ain_pos  # AINx vs AVSS (common convention)
        pga_gain = 0x00        # gain = 1
        pga_bypass = 0x01      # bypass PGA

        reg0 = (mux << 4) | (pga_gain << 1) | (pga_bypass & 0x01)

        # --- Register 1: DR, mode, etc. ---
        # We'll choose:
        #   - Normal mode
        #   - Continuous conversion
        #   - Data rate = data_rate_sps
        #
        # Data rate code depends on ADS1220. A common set:
        #   20 SPS -> 0x00, 45 -> 0x01, 90 -> 0x02, 175 -> 0x03, 330 -> 0x04, 600 -> 0x05, 1000 -> 0x06
        dr_map = {20: 0x00, 45: 0x01, 90: 0x02, 175: 0x03, 330: 0x04, 600: 0x05, 1000: 0x06}
        dr = dr_map.get(data_rate_sps, 0x00)

        # Bits are device-specific; this is a commonly-used layout:
        #   reg1 = (dr << 5) | (mode << 3) | (conv_mode << 2) | (temp << 1) | (burnout)
        # We'll set:
        #   mode=0 (normal), conv_mode=1 (continuous), temp=0, burnout=0
        reg1 = (dr << 5) | (0 << 3) | (1 << 2) | (0 << 1) | 0

        # --- Register 2: VREF selection, FIR, etc. ---
        # Choose external REFP/REFN, and leave other options default.
        # Common: VREF = external => bits '10' somewhere; we’ll use a typical value.
        # We'll use:
        #   - VREF = external (REFP/REFN)
        #   - Low-side switch open, etc.
        reg2 = 0b01_00_0_000

        # --- Register 3: IDAC, DRDY mode, etc. ---
        reg3 = 0b000_000_0_0

        self.write_regs(0, bytes([reg0, reg1, reg2, reg3]))
        #print(hex(reg0), hex(reg1), hex(reg2), hex(reg3))
        self.start()

    def probe(self, strict_id: bool = False):
        """
        Returns (ok: bool, info: dict)

        ok=True means SPI comms look valid and registers are readable.
        If strict_id=True, we also try to validate REG3 'ID' bits (best-effort).
        """

        # Try to reset so registers go to defaults (or at least a known state)
        #try:
        #    self.reset()
        #except Exception as e:
        #    return (False, {"reason": "spi_write_failed", "error": repr(e)})

        # Read all 4 config registers
        try:
            regs = self.read_regs(0, 4)
        except Exception as e:
            return (False, {"reason": "rreg_failed", "error": repr(e)})

        r0, r1, r2, r3 = regs[0], regs[1], regs[2], regs[3]

        # Basic sanity: if MISO is floating you often see 0xFF or 0x00 everywhere
        if regs == b"\x00\x00\x00\x00":
            return (False, {"reason": "all_zero_regs", "regs": list(regs)})
        if regs == b"\xFF\xFF\xFF\xFF":
            return (False, {"reason": "all_ff_regs", "regs": list(regs)})

        info = {"regs": [r0, r1, r2, r3]}

        # Best-effort ID check:
        # On ADS1220, upper bits of REG3 are commonly used for ID / revision in TI families.
        # Libraries sometimes interpret bits 7..5 (or 7..4) as ID.
        # We'll extract both patterns and just report them.
        id_3bits = (r3 >> 5) & 0x07
        id_4bits = (r3 >> 4) & 0x0F
        info["reg3_id_3bits"] = id_3bits
        info["reg3_id_4bits"] = id_4bits

        if strict_id:
            # If you know the expected ID value for your ADS1220 revision,
            # set it here. Since board/libs differ, we don't hard-fail by default.
            # Example placeholder: expected_3bits = 0b000  (EDIT if you know)
            expected_3bits = 0  # <-- set this if you have confirmed expected value
            if id_3bits != expected_3bits:
                #return (False, {"reason": "id_mismatch", **info})
                return (False, {"reason": "id_mismatch"})

        return (True, info)


    def read_channel_volts(self,
                           ch: int,
                           sps: int = 20,
                           discard_first: bool = True,
                           avg: int = 4) -> float:
        self.configure_single_ended(ch, data_rate_sps=sps)

        # Discard first sample after mux change (recommended)
        if discard_first:
            if not self.wait_drdy(200):
                raise RuntimeError("DRDY timeout (discard)")
            _ = self.read_data24()

        acc = 0.0
        for _ in range(avg):
            if not self.wait_drdy(200):
                raise RuntimeError("DRDY timeout")
            code = self.read_data24()
            acc += self.code_to_volts(code)
        return acc / avg
