"""Switch validation and rollback with mocked host services."""
import importlib.machinery
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'build_files' / 'bluefin-model-switch'
loader = importlib.machinery.SourceFileLoader('model_switch', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
switcher = importlib.util.module_from_spec(spec)
loader.exec_module(switcher)


class SwitchTests(unittest.TestCase):
    def test_gateway_check_requires_auth_and_uses_backend_model_id(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'gateway.env'
            key.write_text('MODEL_GATEWAY_API_KEY=' + 'a' * 64 + '\n')
            requests = []

            class Response:
                def __init__(self, authorized, is_local):
                    self.status = 200 if authorized or is_local else 401

                def getheader(self, _name):
                    return '1'

                def read(self, _limit):
                    return b'{"data":[{"id":"qwen3.8-flash-next-iq3_xxs"}]}' if self.status == 200 else b''

            class Connection:
                def __init__(self, *args, **kwargs):
                    self.authorized = False
                    self.is_local = args[1] == 8080

                def request(self, method, path, headers=None):
                    headers = headers or {}
                    requests.append((method, path, headers))
                    self.authorized = bool(headers)

                def getresponse(self):
                    return Response(self.authorized, self.is_local)

                def close(self):
                    pass

            with patch.object(switcher.http.client, 'HTTPConnection', Connection):
                switcher.verify_gateway('8643', 'off', key, '8080', 'strata')
            self.assertEqual(requests[0], ('GET', '/v1/models', {}))
            self.assertEqual(requests[1][2]['Authorization'], 'Bearer ' + 'a' * 64)

    def test_invalid_choices_fail_before_runtime_changes(self):
        for model, context, backend in [('other', '64000', 'strata'),
                                        ('IQ3_XXS', '100', 'strata'),
                                        ('IQ3_XXS', '64000', 'shell;id')]:
            with self.subTest(model=model), self.assertRaises(ValueError):
                switcher.validate(model, context, backend, 'hybrid', '8080', '8643')

    def test_failed_strata_start_restores_old_files_and_service(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            quadlet = home / '.config/containers/systemd/bluefin-ramalama.container'
            quadlet.parent.mkdir(parents=True)
            quadlet.write_text('old quadlet')
            runtime = home / '.config/bluefin-ai/strata/runtime.json'
            runtime.parent.mkdir(parents=True)
            runtime.write_text('old runtime')
            states = {'bluefin-ramalama.service': True, 'bluefin-strata.service': False,
                      'bluefin-strata-proxy.service': False}
            calls = []

            def systemctl(*args, **kwargs):
                calls.append(args)
                if args[0] in ('stop', 'start'):
                    states[args[1]] = args[0] == 'start'
                return SimpleNamespace(returncode=1 if args[0] == 'is-enabled' else 0)

            def run(args, **kwargs):
                if args[0] == 'bluefin-strata-service-enable':
                    runtime.write_text('new runtime')
                    raise subprocess.CalledProcessError(1, args)
                return SimpleNamespace(returncode=0)

            values = {'PROFILE': 'model-only', 'MODEL_PORT': '8080', 'MODEL_GATEWAY_PORT': '8643'}
            with patch.object(switcher.Path, 'home', return_value=home), \
                 patch.object(switcher, 'get', side_effect=values.get), \
                 patch.object(switcher, 'active', side_effect=states.get), \
                 patch.object(switcher, 'systemctl', side_effect=systemctl), \
                 patch.object(switcher, 'run', side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    switcher.switch('IQ3_XXS', '64000', 'strata')
            self.assertEqual(quadlet.read_text(), 'old quadlet')
            self.assertEqual(runtime.read_text(), 'old runtime')
            self.assertTrue(states['bluefin-ramalama.service'])
            self.assertIn(('start', 'bluefin-ramalama.service'), calls)

    def test_success_commits_only_after_backend_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            quadlet = home / '.config/containers/systemd/bluefin-ramalama.container'
            quadlet.parent.mkdir(parents=True)
            quadlet.write_text('old')
            calls = []

            def run(args, **kwargs):
                calls.append(tuple(args))
                return SimpleNamespace(returncode=0)

            def systemctl(*args, **kwargs):
                return SimpleNamespace(returncode=1 if args[0] == 'is-enabled' else 0)

            values = {'PROFILE': 'model-only', 'MODEL_PORT': '8080', 'MODEL_GATEWAY_PORT': '8643'}
            with patch.object(switcher.Path, 'home', return_value=home), \
                 patch.object(switcher, 'get', side_effect=values.get), \
                 patch.object(switcher, 'active', return_value=False), \
                 patch.object(switcher, 'systemctl', side_effect=systemctl), \
                 patch.object(switcher, 'run', side_effect=run):
                switcher.switch('IQ3_XXS', '64000', 'strata')
            self.assertEqual(calls[0][0], 'bluefin-strata-service-enable')
            self.assertEqual(calls[1][0:2], ('bluefin-ai-profile', 'set'))
            self.assertFalse(quadlet.exists())

    def test_gateway_is_reconfigured_and_checked_before_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            gateway = home / '.config/bluefin-ai/model-gateway/nginx.conf'
            gateway.parent.mkdir(parents=True)
            gateway.write_text('old gateway')
            calls = []
            values = {'PROFILE': 'remote-agents', 'MODEL_PORT': '8080',
                      'MODEL_GATEWAY_PORT': '8643', 'MODEL_GATEWAY_TLS': 'off'}

            def run(args, **kwargs):
                calls.append(tuple(args))
                if args == ['ujust', 'ai-profile-model-gateway-configure']:
                    gateway.write_text('new gateway')
                return SimpleNamespace(returncode=0)

            def systemctl(*args, **kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=1 if args[0] == 'is-enabled' else 0)

            with patch.object(switcher.Path, 'home', return_value=home), \
                 patch.object(switcher, 'get', side_effect=values.get), \
                 patch.object(switcher, 'active', side_effect=lambda unit: unit == 'bluefin-model-gateway.service'), \
                 patch.object(switcher, 'systemctl', side_effect=systemctl), \
                 patch.object(switcher, 'run', side_effect=run), \
                 patch.object(switcher, 'verify_gateway', side_effect=lambda *args: calls.append(('verify',))):
                switcher.switch('IQ3_XXS', '64000', 'strata')
            self.assertLess(calls.index(('ujust', 'ai-profile-model-gateway-configure')), calls.index(('verify',)))
            commit = next(i for i, call in enumerate(calls) if call[:2] == ('bluefin-ai-profile', 'set'))
            self.assertLess(calls.index(('verify',)), commit)
            self.assertEqual(gateway.read_text(), 'new gateway')

    def test_failed_gateway_check_restores_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            gateway = home / '.config/bluefin-ai/model-gateway/nginx.conf'
            gateway.parent.mkdir(parents=True)
            gateway.write_text('old gateway')
            calls = []
            values = {'PROFILE': 'hybrid', 'MODEL_PORT': '8080',
                      'MODEL_GATEWAY_PORT': '8643', 'MODEL_GATEWAY_TLS': 'off'}

            def run(args, **kwargs):
                calls.append(tuple(args))
                if args == ['ujust', 'ai-profile-model-gateway-configure']:
                    gateway.write_text('new gateway')
                return SimpleNamespace(returncode=0)

            def systemctl(*args, **kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=1 if args[0] == 'is-enabled' else 0)

            with patch.object(switcher.Path, 'home', return_value=home), \
                 patch.object(switcher, 'get', side_effect=values.get), \
                 patch.object(switcher, 'active', side_effect=lambda unit: unit == 'bluefin-model-gateway.service'), \
                 patch.object(switcher, 'systemctl', side_effect=systemctl), \
                 patch.object(switcher, 'run', side_effect=run), \
                 patch.object(switcher, 'verify_gateway', side_effect=RuntimeError('not ready')):
                with self.assertRaises(RuntimeError):
                    switcher.switch('IQ3_XXS', '64000', 'strata')
            self.assertEqual(gateway.read_text(), 'old gateway')
            self.assertEqual(calls.count(('restart', 'bluefin-model-gateway.service')), 2)
            self.assertFalse(any(call[:2] == ('bluefin-ai-profile', 'set') for call in calls))


if __name__ == '__main__':
    unittest.main()
