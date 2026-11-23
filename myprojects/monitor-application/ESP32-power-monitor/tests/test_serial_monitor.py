import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'host'))
spec = importlib.util.spec_from_file_location('serial_monitor', APP / 'host' / 'serial_monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
FRAME = {'uptime_ms': 1000, 'channel0': {'battery_v': 4.1, 'load_v': 4.0,
         'current_ma': 25.0, 'power_w': 0.1}, 'channel1': None, 'error1': 'math overflow'}


class MonitorTests(unittest.TestCase):
    def test_delete_requires_confirmation_and_local_origin_and_reports_failure(self):
        store, writer = Mock(), Mock()
        state = monitor.MonitorState('/dev/test', '/tmp/test.jsonl', store, writer)
        server = monitor.create_server(state, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/api/data'
        try:
            for headers, code in [({}, 400),
                                  ({'Origin': 'https://example.com', 'X-Confirm-Clear': 'delete-all'}, 403)]:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url, method='DELETE', headers=headers))
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
            writer.clear_all.assert_not_called()
            headers = {'Origin': 'http://127.0.0.1:3100', 'X-Confirm-Clear': 'delete-all'}
            with urlopen(Request(url, method='DELETE', headers=headers)) as response:
                self.assertEqual(response.status, 204)
            writer.clear_all.assert_called_once()
            writer.clear_all.side_effect = OSError('database offline')
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(url, method='DELETE', headers=headers))
            self.assertEqual(caught.exception.code, 503)
            caught.exception.close()
            with urlopen(Request(url, method='OPTIONS', headers=headers)) as response:
                self.assertIn('DELETE', response.headers['Access-Control-Allow-Methods'])
                self.assertIn('X-Confirm-Clear', response.headers['Access-Control-Allow-Headers'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_absolute_history_and_annotation_api(self):
        store = Mock()
        store.history_between.return_value = []
        store.annotations_between.return_value = [{"text": "GPS finding"}]
        store.add_annotation.return_value = {"id": "test", "text": "LTE init"}
        state = monitor.MonitorState('/dev/test', '/tmp/test.jsonl', store)
        server = monitor.create_server(state, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def post(data, origin='http://localhost:3100'):
            return urlopen(Request(base + '/api/annotations', json.dumps(data).encode(),
                           headers={'Content-Type': 'application/json', 'Origin': origin}))
        try:
            with urlopen(base + '/api/history?start_ms=1000&end_ms=2000') as response:
                self.assertEqual(json.load(response)['annotations'], [{"text": "GPS finding"}])
            store.history_between.assert_called_once_with(1000, 2000)
            store.annotations_between.assert_called_once_with(1000, 2000)
            for query in ['start_ms=1000', 'start_ms=2000&end_ms=1000',
                          'start_ms=0&end_ms=86400001', 'start_ms=bad&end_ms=2000']:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(base + '/api/history?' + query)
                self.assertEqual(caught.exception.code, 400)
                caught.exception.close()
            valid = {'start_ms': 1000, 'end_ms': 2000, 'text': ' LTE init '}
            with post(valid) as response:
                self.assertEqual(response.status, 201)
            store.add_annotation.assert_called_once_with(1000, 2000, 'LTE init')
            for data in [{**valid, 'text': ''}, {**valid, 'text': 'x'*201},
                         {**valid, 'text': 'line\nlabel'}, {**valid, 'start_ms': True},
                         {**valid, 'end_ms': 1000}, []]:
                with self.assertRaises(HTTPError) as caught:
                    post(data)
                self.assertEqual(caught.exception.code, 400)
                caught.exception.close()
            with self.assertRaises(HTTPError) as caught:
                post(valid, 'https://example.com')
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()
            store.add_annotation.side_effect = OSError('database offline')
            with self.assertRaises(HTTPError) as caught:
                post(valid)
            self.assertEqual(caught.exception.code, 503)
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_delete_label_api_is_confirmed_local_and_reports_failures(self):
        store = Mock()
        server = monitor.create_server(monitor.MonitorState('/dev/test', '/tmp/test.jsonl', store), 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        label_id = 'a'*32
        url = f'http://127.0.0.1:{server.server_port}/api/annotations/{label_id}'
        headers = {'Origin': 'http://localhost:3100', 'X-Confirm-Label': 'delete'}
        try:
            for supplied, status in [({}, 400), ({**headers, 'Origin': 'https://example.com'}, 403)]:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url, method='DELETE', headers=supplied))
                self.assertEqual(caught.exception.code, status)
                caught.exception.close()
            store.delete_annotation.assert_not_called()
            with urlopen(Request(url, method='DELETE', headers=headers)) as response:
                self.assertEqual(response.status, 204)
            store.delete_annotation.assert_called_once_with(label_id)
            store.clear_all.assert_not_called()
            for failure, status in [(ValueError('Invalid ID'), 400), (KeyError('Missing'), 404), (OSError('Offline'), 503)]:
                store.delete_annotation.side_effect = failure
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url, method='DELETE', headers=headers))
                self.assertEqual(caught.exception.code, status)
                caught.exception.close()
            with urlopen(Request(url, method='OPTIONS', headers=headers)) as response:
                self.assertIn('X-Confirm-Label', response.headers['Access-Control-Allow-Headers'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

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

    def test_http_serves_api_snapshot_with_wildcard_cors(self):
        state = monitor.MonitorState('/dev/test', '/tmp/test.jsonl')
        server = monitor.create_server(state, 0)
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
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
            request = Request(base + '/api/state', headers={'Origin': 'http://localhost:3100'})
            with urlopen(request) as response:
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
            with self.assertRaises(HTTPError) as missing_page:
                urlopen(base + '/')
            missing_page.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
