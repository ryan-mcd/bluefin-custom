"""Exercise dashboard HTTP boundaries without opening a listener."""
import importlib.machinery
import importlib.util
import io
import json
import signal
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[1] / 'build_files' / 'bluefin-ai-dashboard'
loader = importlib.machinery.SourceFileLoader('dashboard_inmemory', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
dashboard = importlib.util.module_from_spec(spec)
loader.exec_module(dashboard)


class Request:
    def __init__(self, raw):
        self.input = io.BytesIO(raw)
        self.output = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        return self.input if 'r' in mode else self.output

    def sendall(self, data):
        self.output.write(data)


class DashboardBoundaryTests(unittest.TestCase):
    def test_timeout_terminates_the_recipe_process_group(self):
        process = Mock(pid=1234)
        process.communicate.side_effect = [subprocess.TimeoutExpired(['ujust'], 2), ('', '')]
        with patch.object(dashboard.subprocess, 'Popen', return_value=process) as start, \
             patch.object(dashboard.os, 'killpg') as kill:
            code, message = dashboard.run(['ujust', 'ai-model-select'], timeout=2)
        self.assertEqual(code, 1)
        self.assertIn('timed out', message)
        self.assertTrue(start.call_args.kwargs['start_new_session'])
        kill.assert_called_once_with(1234, signal.SIGTERM)

    def request(self, headers):
        body = json.dumps({'action': 'model', 'model': 'IQ3_XXS', 'context': '64000',
                           'backend': 'strata'}).encode()
        request = Request(b'POST /api/action HTTP/1.1\r\n' + headers +
                          b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        server = SimpleNamespace(server_port=8765, capability='private-token')
        with patch.object(dashboard, 'run', return_value=(0, 'model-only')), \
             patch.object(dashboard, 'start_job', return_value=True) as start:
            dashboard.Handler(request, ('127.0.0.1', 1234), server)
        return request.output.getvalue(), start

    def test_strata_action_requires_capability_host_and_origin(self):
        good = (b'Host: 127.0.0.1:8765\r\nOrigin: http://127.0.0.1:8765\r\n'
                b'Content-Type: application/json\r\nAuthorization: Bearer private-token\r\n')
        for headers, status in ((good.replace(b'private-token', b'wrong'), b'401'),
                                (good.replace(b'Origin: http://127.0.0.1:8765', b'Origin: http://evil'), b'403'),
                                (good.replace(b'Host: 127.0.0.1:8765', b'Host: evil'), b'403')):
            with self.subTest(status=status):
                output, start = self.request(headers)
                self.assertIn(status, output.split(b'\r\n', 1)[0])
                start.assert_not_called()
        output, start = self.request(good)
        self.assertIn(b'202', output.split(b'\r\n', 1)[0])
        start.assert_called_once_with(['ujust', 'ai-model-select', 'IQ3_XXS', '64000', 'strata'], 'model')


if __name__ == '__main__':
    unittest.main()
