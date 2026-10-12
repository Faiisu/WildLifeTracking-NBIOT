import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'host'))
spec = importlib.util.spec_from_file_location('serial_monitor', APP / 'host' / 'serial_monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
FRAME = {'uptime_ms': 1000, 'channel0': {'battery_v': 4.1, 'load_v': 4.0,
         'current_ma': 25.0, 'power_w': 0.1}, 'channel1': None, 'error1': 'math overflow'}


class MonitorTests(unittest.TestCase):
    def test_logs_only_measurement_json_and_preserves_errors(self):
        log, output, diagnostic = io.StringIO(), io.StringIO(), io.StringIO()
        self.assertFalse(monitor.forward_line('Local directory mounted', log, output, diagnostic))
        self.assertTrue(monitor.forward_line(json.dumps(FRAME), log, output, diagnostic))
        self.assertEqual(output.getvalue(), log.getvalue())
        record = json.loads(log.getvalue())
        self.assertEqual(record['error1'], 'math overflow')
        self.assertIn('received_at', record)
        self.assertIn('mounted', diagnostic.getvalue())

    def test_rejects_nonframes_and_nonfinite_values(self):
        for line in ('Traceback', 'null', '{}', '[]', '{"uptime_ms":true}',
                     json.dumps({**FRAME, 'uptime_ms': -1}),
                     json.dumps({**FRAME, 'channel0': {**FRAME['channel0'], 'power_w': float('nan')}})):
            self.assertIsNone(monitor.parse_frame(line))

    def test_bounds_raw_history_and_keeps_last_valid_frame(self):
        state = monitor.MonitorState('/dev/test', '/tmp/test.jsonl')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            state.ingest(json.dumps(FRAME), io.StringIO())
            for i in range(250):
                state.ingest('raw ' + str(i), io.StringIO())
        snapshot = state.snapshot()
        self.assertEqual(snapshot['count'], 1)
        self.assertEqual(snapshot['raw_count'], 251)
        self.assertEqual(len(snapshot['raw']), 200)
        self.assertEqual(snapshot['latest']['channel0'], FRAME['channel0'])
        state.finish(1)
        self.assertEqual(state.snapshot()['status'], 'error')

    def test_http_serves_api_snapshot_with_frontend_cors(self):
        state = monitor.MonitorState('/dev/test', '/tmp/test.jsonl')
        server = monitor.create_server(state, 0, 'http://127.0.0.1:3100')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            from urllib.request import Request
            request = Request(base + '/api/state', headers={'Origin': 'http://127.0.0.1:3100'})
            with urlopen(request) as response:
                payload = json.load(response)
                self.assertEqual(payload['port'], '/dev/test')
                self.assertEqual(payload['status'], 'connecting')
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'http://127.0.0.1:3100')
            with self.assertRaises(HTTPError) as missing_page:
                urlopen(base + '/')
            missing_page.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
