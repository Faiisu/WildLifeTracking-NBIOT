import ast
import contextlib
import copy
import io
import json
from pathlib import Path
import types
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "main.py"


class Meter:
    def __init__(self, error=None):
        self.error = error

    def read(self):
        if self.error:
            raise OSError(self.error)
        return 4.1, 4.0, 0.025, 0.1


class SerialOutputTests(unittest.TestCase):
    def run_frame(self, meter0, meter1):
        tree = ast.parse(SOURCE.read_text())
        json_default = next(node.value.value for node in tree.body
                            if isinstance(node, ast.Assign)
                            and any(isinstance(target, ast.Name)
                                    and target.id == "SERIAL_OUTPUT_JSON"
                                    for target in node.targets))
        read_interval_ms = next(node.value.value for node in tree.body
                                if isinstance(node, ast.Assign)
                                and any(isinstance(target, ast.Name)
                                        and target.id == "READ_INTERVAL_MS"
                                        for target in node.targets))
        main = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "main")
        loop = copy.deepcopy(next(node for node in main.body if isinstance(node, ast.While)))
        loop.body.append(ast.Break())
        module = ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[]))
        clock = types.SimpleNamespace(ticks_ms=lambda: 1234, ticks_diff=lambda a, b: a-b,
                                      sleep_ms=lambda milliseconds: None)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(module, str(SOURCE), "exec"), {
                "meter0": meter0, "meter1": meter1,
                "time": clock, "ujson": json, "SERIAL_OUTPUT_JSON": json_default,
                "READ_INTERVAL_MS": read_interval_ms,
            })
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0])

    def test_sampling_interval_is_250ms_for_four_hz(self):
        tree = ast.parse(SOURCE.read_text())
        interval_ms = next(node.value.value for node in tree.body
                           if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name)
                                   and target.id == "READ_INTERVAL_MS"
                                   for target in node.targets))
        self.assertEqual(interval_ms, 250)

    def test_default_emits_one_json_frame_with_both_channels(self):
        frame = self.run_frame(Meter(), Meter())
        self.assertEqual(frame["uptime_ms"], 1234)
        for channel in ("channel0", "channel1"):
            self.assertEqual(frame[channel], {
                "battery_v": 4.1, "load_v": 4.0,
                "current_ma": 25.0, "power_w": 0.1,
            })

    def test_faulted_channel_is_null_and_healthy_channel_continues(self):
        frame = self.run_frame(Meter(), Meter("INA239 math overflow"))
        self.assertIsNotNone(frame["channel0"])
        self.assertIsNone(frame["channel1"])
        self.assertEqual(frame["error1"], "INA239 math overflow")

    def test_disabled_channels_still_emit_valid_json(self):
        frame = self.run_frame(None, None)
        self.assertIsNone(frame["channel0"])
        self.assertIsNone(frame["channel1"])


if __name__ == "__main__":
    unittest.main()
