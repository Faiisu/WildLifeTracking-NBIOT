from pathlib import Path
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'host'))
from influxdb_store import InfluxDBStore, decode_row, line_protocol


FRAME = {'uptime_ms': 1250, 'channel0': {'battery_v': 4.1, 'load_v': 4.0,
         'current_ma': 25.0, 'power_w': 0.1}, 'channel1': None,
         'error1': 'sensor "offline"'}


class InfluxStoreTests(unittest.TestCase):
    def test_line_protocol_contains_host_timestamp_and_measurements(self):
        point = line_protocol(FRAME, 1_760_000_000_123_456_789)
        self.assertIn('measurements,device=esp32 ', point)
        self.assertIn('uptime_ms=1250i', point)
        self.assertIn('channel0_battery_v=4.1', point)
        self.assertIn('error1="sensor \\"offline\\""', point)
        self.assertTrue(point.endswith(' 1760000000123456789'))

    def test_decode_row_restores_received_time_and_channels(self):
        point = decode_row({'time': '2026-10-12T10:00:00Z', 'uptime_ms': 1250,
                            'channel0_load_v': 4.0, 'channel0_power_w': 0.1,
                            'error1': 'offline'})
        self.assertEqual(point['timestamp'], '2026-10-12T10:00:00.000000Z')
        self.assertEqual(point['received_at'], '2026-10-12T10:00:00.000000Z')
        self.assertEqual(point['channel0'], {'load_v': 4.0, 'power_w': 0.1})
        self.assertEqual(point['channel1'], None)
        self.assertEqual(point['error1'], 'offline')

    def test_influx_time_without_zone_is_normalized_as_utc(self):
        point = decode_row({'time': '2026-10-12T10:00:00.123456'})
        self.assertEqual(point['timestamp'], '2026-10-12T10:00:00.123456Z')

    def test_numeric_nanosecond_timestamp_keeps_database_precision(self):
        point = decode_row({'time': 1_791_799_200_123_456_789})
        self.assertEqual(point['timestamp'], '2026-10-12T10:00:00.123456789Z')

    def test_write_and_history_use_influx_v3_http_api(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                captured.append((self.path, body))
                if self.path == '/api/v3/query_sql':
                    response = b'{"time":"2026-10-12T10:00:00Z","uptime_ms":1}\n'
                else:
                    response = b''
                self.send_response(200)
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            store = InfluxDBStore(f'http://127.0.0.1:{server.server_port}')
            store.write_frames([(FRAME, 1_760_000_000_123_456_789)])
            rows = store.history(60)
            self.assertEqual(len(rows), 1)
            self.assertIn('/api/v3/write_lp?db=esp32_power_monitor&precision=nanosecond', captured[0][0])
            self.assertIn(b"INTERVAL '60 seconds'", captured[1][1])
            self.assertEqual(captured[1][0], '/api/v3/query_sql')
            with self.assertRaises(ValueError):
                store.history(42)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
