"""Check flash orchestration without accessing a physical probe."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class FlashScriptTest(unittest.TestCase):
    def test_build_then_flash_with_firmware_range_erase_and_slow_swd(self):
        script = Path(__file__).resolve().parents[3] / "flash_gps_mqtt_tracker_temp.sh"
        with tempfile.TemporaryDirectory(prefix="gnss-flash-test-") as directory:
            root = Path(directory)
            ncs = root / "ncs"
            (ncs / ".west").mkdir(parents=True)
            (ncs / ".west/config").touch()
            toolchain = root / "toolchain"
            (toolchain / "bin").mkdir(parents=True)
            west = toolchain / "bin/west"
            west.write_text('''#!/usr/bin/env python3
import sys
from pathlib import Path
args = sys.argv[1:]
assert args[:3] == ["build", "-p", "always"]
assert args[args.index("-b") + 1] == "sparkfun_thing_plus/nrf9160/ns"
assert "--sysbuild" in args
assert any(arg.endswith("/myprojects/gps_mqtt_tracker_temp") for arg in args)
build = Path(args[args.index("-d") + 1])
build.mkdir(parents=True)
(build / "merged.hex").write_text(":00000001FF\\n")
''')
            west.chmod(0o755)
            nrfutil = root / "nrfutil"
            nrfutil.write_text('''#!/usr/bin/env python3
import sys
from pathlib import Path
args = sys.argv[1:]
assert args[:2] == ["device", "program"]
assert Path(args[args.index("--firmware") + 1]).is_file()
assert args[args.index("--swd-clock-frequency") + 1] == "100"
assert args[args.index("--options") + 1] == "chip_erase_mode=ERASE_RANGES_TOUCHED_BY_FIRMWARE,reset=RESET_SYSTEM,verify=VERIFY_READ"
assert args[args.index("--family") + 1] == "nrf91"
''')
            nrfutil.chmod(0o755)
            dll = root / "jlink.dylib"
            dll.touch()
            environment = os.environ.copy()
            environment.update(
                NCS_ROOT=str(ncs), TOOLCHAIN_BUNDLE=str(toolchain),
                NRFUTIL=str(nrfutil), NRFUTIL_HOME=str(root / "nrfutil-home"),
                JLINK_DLL=str(dll), JLINK_SERIAL_NUMBER="test-probe",
                BUILD_DIR=str(root / "build"),
                BOARD="sparkfun_thing_plus/nrf9160/ns", SWD_CLOCK_FREQUENCY="100",
            )
            result = subprocess.run(
                ["bash", str(script)], env=environment,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Flash and verification completed", result.stdout)


if __name__ == "__main__":
    unittest.main()
