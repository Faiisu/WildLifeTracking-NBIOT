from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'host'))
from frontend_server import create_server


class FrontendServerTests(unittest.TestCase):
    def test_serves_page_with_configured_backend_and_keeps_api_separate(self):
        server = create_server(0, 'http://127.0.0.1:3210')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/') as response:
                page = response.read().decode()
                self.assertIn("const API_BASE = 'http://127.0.0.1:3210';", page)
                self.assertNotIn('__API_BASE__', page)
            with self.assertRaises(HTTPError) as missing_api:
                urlopen(f'http://127.0.0.1:{server.server_port}/api/state')
            missing_api.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
