"""Security checks for the local dashboard's HTTP and command boundaries."""
import http.client
import importlib.machinery
import importlib.util
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'build_files' / 'bluefin-ai-dashboard'
loader = importlib.machinery.SourceFileLoader('dashboard', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
dashboard = importlib.util.module_from_spec(spec)
loader.exec_module(dashboard)


class DashboardSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = dashboard.ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.capability = 'test-capability'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def headers(self):
        return {'Origin': f'http://127.0.0.1:{self.server.server_port}',
                'Content-Type': 'application/json', 'Authorization': 'Bearer test-capability'}

    def test_status_requires_capability(self):
        code, _ = self.request('GET', '/api/status')
        self.assertEqual(code, 401)
        with patch.object(dashboard, 'status', return_value={'ready': False}):
            code, body = self.request('GET', '/api/status', headers=self.headers())
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {'ready': False})

    def test_actions_require_capability_same_origin_and_valid_host(self):
        payload = json.dumps({'action': 'profile', 'profile': 'model-only'})
        headers = self.headers()
        for mutation, expected in (({'Authorization': 'Bearer wrong'}, 401),
                                   ({'Origin': 'http://attacker.example'}, 403),
                                   ({'Host': 'attacker.example'}, 403),
                                   ({'Content-Type': 'text/plain'}, 403)):
            with self.subTest(mutation=mutation), patch.object(dashboard, 'start_job') as start:
                code, _ = self.request('POST', '/api/action', payload, {**headers, **mutation})
                self.assertEqual(code, expected)
                start.assert_not_called()
        with patch.object(dashboard, 'start_job', return_value=True) as start:
            code, _ = self.request('POST', '/api/action', payload, headers)
            self.assertEqual(code, 202)
            start.assert_called_once_with(['ujust', 'ai-profile-set', 'model-only'], 'profile')

    def test_model_and_context_are_validated_before_execution(self):
        with patch.object(dashboard, 'run', return_value=(0, 'central-agent')):
            for model in ('$(id)', 'model;id', 'model";id', ''):
                with self.subTest(model=model), self.assertRaises(ValueError):
                    dashboard.command_for({'action': 'model', 'model': model, 'context': '64000'})
            with self.assertRaises(ValueError):
                dashboard.command_for({'action': 'model', 'model': 'llama3.2', 'context': '1024'})
            self.assertEqual(dashboard.command_for({'action': 'model', 'model': 'hf://repo/model:tag', 'context': '64000'}),
                             ['ujust', 'ai-model-select', 'hf://repo/model:tag', '64000'])

    def test_arbitrary_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            dashboard.command_for({'action': 'profile', 'profile': 'hybrid;id'})


if __name__ == '__main__':
    unittest.main()
