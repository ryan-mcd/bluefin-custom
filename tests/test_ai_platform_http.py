"""Exercise the real proxy over HTTP, including SSE and control authorization."""
import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_ai_platform import configuration, runtime


class Backend(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.server.payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.wfile.write(b'data: {"token":"one"}\n\n')
        self.wfile.flush()
        self.server.release.wait(3)
        self.wfile.write(b'data: [DONE]\n\n')


class HTTPTest(unittest.TestCase):
    def setUp(self):
        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        self.backend.release = threading.Event()
        threading.Thread(target=self.backend.serve_forever, daemon=True).start()
        config = configuration()
        config['profiles']['small']['port'] = self.backend.server_port
        self.events = []
        self.controller = runtime.Controller(config, manage=lambda *a: self.events.append(a),
                                             memory=lambda: 120 * runtime.GIB, health=lambda p: True)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), runtime.Handler)
        self.server.controller = self.controller
        self.server.inference_key = 'inference-secret'
        self.server.admin_key = 'admin-secret'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.backend.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.backend.shutdown()
        self.backend.server_close()

    def request(self, method, path, body=None, key='inference-secret'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request(method, path, body, {'Authorization': 'Bearer ' + key,
                                              'Content-Type': 'application/json'})
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, json.loads(data)

    def test_inference_credentials_cannot_change_mode(self):
        status, _ = self.request('POST', '/control/mode', json.dumps({'action': 'pin', 'profile': 'large'}))
        self.assertEqual(status, 401)
        self.assertFalse(self.events)
        status, _ = self.request('POST', '/control/mode', json.dumps({'action': 'pin', 'profile': 'large'}),
                                 key='admin-secret')
        self.assertEqual(status, 200)
        self.assertEqual(self.controller.pin, 'large')

    def test_sse_forwards_before_completion_and_holds_lease(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        connection.request('POST', '/v1/chat/completions',
                           json.dumps({'model': 'general', 'messages': [], 'stream': True}),
                           {'Authorization': 'Bearer inference-secret', 'Content-Type': 'application/json'})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.readline(), b'data: {"token":"one"}\n')
        self.assertEqual(self.controller.inflight, 1)
        self.assertEqual(self.backend.payload['model'], 'small')
        self.backend.release.set()
        self.assertIn(b'data: [DONE]', response.read())
        connection.close()
        # HTTP EOF occurs after the backend closes and lease cleanup completes.
        with self.controller.condition:
            self.assertEqual(self.controller.inflight, 0)

    def test_errors_do_not_activate_a_model(self):
        for body, expected in (('[]', 400), ('{"model":12}', 400), ('{"model":"unknown"}', 404)):
            status, _ = self.request('POST', '/v1/chat/completions', body)
            self.assertEqual(status, expected)
        self.assertFalse(self.events)


if __name__ == '__main__':
    unittest.main()
