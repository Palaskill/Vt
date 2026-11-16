"""The visual assets must remain available from the local, offline server."""
import http.client
import threading
import unittest

import server as app


class DesignAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.host = '127.0.0.1:' + str(cls.http.server_port)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def test_local_assets_and_content_security_policy(self):
        connection = http.client.HTTPConnection(self.host, timeout=10)
        try:
            connection.request('GET', '/')
            response = connection.getresponse()
            page = response.read()
            self.assertEqual(response.status, 200)
            self.assertIn("script-src 'self'", response.getheader('Content-Security-Policy'))
            for name in ('design.css', 'design.js', 'gsap.min.js', 'lenis.min.js', 'react-spotlight.js'):
                with self.subTest(asset=name):
                    self.assertIn(('/' + name).encode(), page)
                    connection.request('GET', '/' + name)
                    asset = connection.getresponse()
                    self.assertEqual(asset.status, 200)
                    self.assertGreater(len(asset.read()), 100)
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
