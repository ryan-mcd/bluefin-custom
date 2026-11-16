"""Exercise the real proxy over HTTP, including SSE and control authorization."""
import http.client
import json
import threading
import unittest
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_ai_platform import configuration, runtime, platform
from bluefin_ai_models import ModelManager
from unittest.mock import patch
from types import SimpleNamespace


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
                                             memory=lambda: 120 * runtime.GIB, health=lambda p: True, preflight=lambda p: None)
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

    def test_platform_cli_selection_changes_general_without_client_reconfiguration(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);secret='a'*64
            (base/'admin-key').write_text(secret)
            self.server.admin_key=secret
            self.controller.selection_path=base/'selection.json'
            self.controller.profiles['large']['port']=self.backend.server_port
            manager=ModelManager(self.controller.config,base,base/'models',platform.run,platform.write,self.server.server_port)
            manager.control('select','large')
            self.backend.release.set()
            connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
            connection.request('POST','/v1/chat/completions',json.dumps({'model':'general','messages':[],'stream':True}),
                               {'Authorization':'Bearer inference-secret','Content-Type':'application/json'})
            response=connection.getresponse();response.read();connection.close()
            self.assertEqual(response.status,200)
            self.assertEqual(self.backend.payload['model'],'large')

    def test_discovered_strata_model_id_is_forwarded_and_survives_registry_reload(self):
        profile=self.controller.profiles['small']
        profile['discover_backend_model']=True
        response=SimpleNamespace(status=200,read=lambda limit:b'{"data":[{"id":"actual-source-model"}]}')
        with patch.object(runtime.http.client,'HTTPConnection') as connection:
            connection.return_value.getresponse.return_value=response
            self.assertTrue(runtime.ready(profile))
        fresh=json.loads(json.dumps(self.controller.config))
        fresh['profiles']['small'].pop('serving_model')
        self.controller.control('select','small')
        self.controller.refresh=lambda:fresh
        self.controller.control('reload')
        self.backend.release.set()
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request('POST','/v1/chat/completions',json.dumps({'model':'general','messages':[]}),
                           {'Authorization':'Bearer inference-secret','Content-Type':'application/json'})
        response=connection.getresponse();response.read();connection.close()
        self.assertEqual(response.status,200)
        self.assertEqual(self.backend.payload['model'],'actual-source-model')

    def test_named_picker_selection_switches_without_a_general_default(self):
        self.controller.default=None
        self.controller.profiles['large']['port']=self.backend.server_port
        self.backend.release.set()
        for name in ('small','large','small'):
            connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
            connection.request('POST','/v1/chat/completions',json.dumps({'model':name,'messages':[]}),
                               {'Authorization':'Bearer inference-secret','Content-Type':'application/json'})
            response=connection.getresponse();response.read();connection.close()
            self.assertEqual(response.status,200)
            self.assertEqual(self.backend.payload['model'],name)
            self.assertIsNone(self.controller.default)
        self.assertIn(('stop','bluefin-runtime-small.service'),self.events)
        self.assertIn(('start','bluefin-runtime-large.service'),self.events)

    def test_public_catalog_lists_only_prepared_named_models_with_source_labels(self):
        profile=self.controller.profiles['small']
        profile['context_tokens']=65536
        profile['source']=dict(repo='publisher/model',file='chosen-Q8.gguf',revision='a'*40)
        self.controller.profiles['large']['artifacts'][0]['name']='other.gguf'
        self.controller.catalog_ready=lambda p:p['backend_model']=='small'
        status,body=self.request('GET','/catalog/models')
        self.assertEqual(status,200)
        self.assertEqual([model['id'] for model in body['data']],['local/small'])
        label=body['data'][0]['name']
        for part in ('publisher/model','chosen-Q8.gguf','64K','llamacpp'):
            self.assertIn(part,label)
        self.assertFalse(self.events)
        self.controller.catalog_ready=lambda p:False
        self.assertEqual(self.request('GET','/catalog/models')[1]['data'],[])

    def test_catalog_does_not_expose_admin_control_or_accept_missing_credentials(self):
        status,_=self.request('GET','/catalog/models',key='wrong')
        self.assertEqual(status,401)
        status,_=self.request('GET','/catalog/models',key='admin-secret')
        self.assertEqual(status,401)
        self.assertFalse(self.events)

    def test_errors_do_not_activate_a_model(self):
        for body, expected in (('[]', 400), ('{"model":12}', 400), ('{"model":"unknown"}', 404)):
            status, _ = self.request('POST', '/v1/chat/completions', body)
            self.assertEqual(status, expected)
        self.assertFalse(self.events)


if __name__ == '__main__':
    unittest.main()
