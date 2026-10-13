from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'host'))
from influxdb_store import InfluxDBStore, InfluxDBWriter, decode_row, line_protocol


FRAME = {'uptime_ms': 1250, 'channel0': {'battery_v': 4.1, 'load_v': 4.0,
         'current_ma': 25.0, 'power_w': 0.1}, 'channel1': None,
         'error1': 'sensor "offline"'}


class InfluxStoreTests(unittest.TestCase):
    def test_clear_preserves_schema_and_targets_configured_database(self):
        store = InfluxDBStore('http://unused', 'test database')
        store._request = Mock(side_effect=[b'{"product_name":"InfluxDB 3 Core","version":"3.12.0"}', b'', b'', b''])
        store.clear_all()
        self.assertEqual(store._request.call_args_list[1].args[0],
                         '/api/v3/configure/database?db=test+database&hard_delete_at=now')
        self.assertEqual(store._request.call_args_list[1].kwargs, {'method': 'DELETE'})

    def test_clear_refuses_older_servers_before_deleting(self):
        store = InfluxDBStore('http://unused')
        store._request = Mock(return_value=b'{"product_name":"InfluxDB 3 Core","version":"3.11.0"}')
        with self.assertRaises(OSError):
            store.clear_all()
        self.assertEqual(store._request.call_count, 1)

    def test_writer_discards_old_batch_and_writes_new_readings_after_clear(self):
        store = Mock()
        writer = InfluxDBWriter(store, Mock(), batch_interval=0.01)
        try:
            with patch('influxdb_store.time.time_ns', return_value=100):
                writer.clear_all()
            writer._flush([(FRAME, 99), (FRAME, 101)])
            store.clear_all.assert_called_once()
            store.write_frames.assert_called_once_with([(FRAME, 101)])
        finally:
            writer.close()

    def test_recreation_failure_still_discards_predelete_frames(self):
        store = Mock()
        failure = OSError('Data was deleted, but recreation failed')
        failure.data_deleted = True
        store.clear_all.side_effect = failure
        writer = InfluxDBWriter(store, Mock(), batch_interval=0.01)
        try:
            with patch('influxdb_store.time.time_ns', return_value=100):
                with self.assertRaises(OSError):
                    writer.clear_all()
            writer._flush([(FRAME, 99), (FRAME, 101)])
            store.write_frames.assert_called_once_with([(FRAME, 101)])
        finally:
            writer.close()

    def test_failed_clear_does_not_discard_pending_readings(self):
        store = Mock()
        store.clear_all.side_effect = OSError('unavailable')
        writer = InfluxDBWriter(store, Mock(), batch_interval=0.01)
        try:
            with self.assertRaises(OSError):
                writer.clear_all()
            writer._flush([(FRAME, 99)])
            store.write_frames.assert_called_once_with([(FRAME, 99)])
        finally:
            writer.close()

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
