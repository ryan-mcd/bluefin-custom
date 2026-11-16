"""Boot updates must apply the catalog, preserve data, and prove readiness."""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_ai_platform import ASSETS, configuration, platform, runtime


class BootProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'home'
        self.assets = Path(self.temporary.name) / 'assets'
        shutil.copytree(ASSETS, self.assets)
        self.base = self.home / '.config/bluefin-ai/platform'
        self.config = configuration()
        self.events = []

        def manager(*args, **_kwargs):
            self.events.append(args)
            return SimpleNamespace(stdout='')

        self.run = self.enterContext(patch.object(platform, 'run', side_effect=manager))
        self.active = self.enterContext(patch.object(platform, 'active', return_value=False))
        self.images = self.enterContext(patch.object(platform, 'ensure_images'))
        self.health = self.enterContext(patch.object(platform, 'verify_platform'))
        self.artifacts = self.enterContext(patch.object(platform, 'model_artifact'))
        self.enterContext(patch.object(platform, 'ASSETS', self.assets))
        self.enterContext(patch.object(platform, 'BASE', self.base))
        self.enterContext(patch.object(Path, 'home', return_value=self.home))

    def test_boot_starts_and_records_release_only_after_readiness(self):
        def ready(_config):
            self.assertIn(('systemctl', '--user', 'start', 'bluefin-ai-platform.target'), self.events)
            self.assertFalse((self.base / 'release').exists())

        self.health.side_effect = ready
        platform.provision(self.config)
        self.assertTrue((self.base / 'release').exists())
        self.images.assert_called_once_with(self.config, runtimes=False)

    def test_webui_publishes_configured_aliases_and_uses_the_authenticated_ingress(self):
        platform.provision(self.config)
        env = dict(line.split('=', 1) for line in (self.base / 'webui.env').read_text().splitlines())
        connection = json.loads(env['OPENAI_API_CONFIGS'])['0']
        self.assertTrue(connection['enable'])
        self.assertEqual(connection['model_ids'], [])
        nginx = (self.base / 'nginx.conf').read_text()
        self.assertNotIn('@@INFERENCE_KEY@@', nginx)
        self.assertIn('proxy_pass http://127.0.0.1:18100/catalog/models', nginx)
        self.assertEqual(env['OPENAI_API_KEYS'], (self.base / 'client-key').read_text().strip())
        quadlet = (self.home / '.config/containers/systemd/bluefin-platform-webui.container').read_text()
        self.assertIn('OPENAI_API_BASE_URLS=http://127.0.0.1:8643/v1', quadlet)

    def test_fresh_units_are_started_without_resetting_unloaded_units(self):
        def manager(*args, **_kwargs):
            self.events.append(args)
            if args[:3] == ('systemctl', '--user', 'reset-failed'):
                raise RuntimeError('Unit not loaded')
            return SimpleNamespace(stdout='')

        self.run.side_effect = manager
        platform.provision(self.config)
        self.assertIn(('systemctl', '--user', 'start', 'bluefin-ai-platform.target'), self.events)
        self.assertTrue((self.base / 'release').exists())

    def test_retry_resets_only_failed_managed_units_including_model_preparation(self):
        def manager(*args, **_kwargs):
            self.events.append(args)
            if args[:3] == ('systemctl', '--user', 'list-units'):
                return SimpleNamespace(stdout='bluefin-ai-models.service loaded failed failed Models\n'
                                       'unrelated.service loaded failed failed Other\n')
            return SimpleNamespace(stdout='')

        self.run.side_effect = manager
        platform.provision(self.config)
        resets = [event for event in self.events if event[:3] == ('systemctl', '--user', 'reset-failed')]
        self.assertEqual(resets, [('systemctl', '--user', 'reset-failed', 'bluefin-ai-models.service')])

    def test_unchanged_release_repairs_quadlets_and_preserves_credentials_on_next_boot(self):
        platform.provision(self.config)
        keys = {p.name: p.read_bytes() for p in self.base.glob('*key')}
        secret = (self.base / 'webui-secret').read_bytes()
        artifact_checks = self.artifacts.call_count
        quadlet = self.home / '.config/containers/systemd/bluefin-platform-webui.container'
        quadlet.write_text('stale generated definition')
        platform.provision(self.config)
        self.assertIn(self.config['images']['webui'], quadlet.read_text())
        self.assertEqual(self.artifacts.call_count, artifact_checks)
        self.assertEqual(self.images.call_count, 2)
        self.assertEqual(self.health.call_count, 2)
        for name, value in keys.items():
            self.assertEqual((self.base / name).read_bytes(), value)
        self.assertEqual((self.base / 'webui-secret').read_bytes(), secret)

    def test_new_image_is_rendered_on_next_boot_without_changing_webui_storage(self):
        platform.provision(self.config)
        before = (self.base / 'release').read_bytes()
        password = (self.base / 'webui-admin-password').read_bytes()
        updated = copy.deepcopy(self.config)
        updated['images']['webui'] = 'registry.example.test/webui@sha256:' + 'd' * 64
        platform.provision(updated)
        quadlet = self.home / '.config/containers/systemd/bluefin-platform-webui.container'
        self.assertIn(updated['images']['webui'], quadlet.read_text())
        self.assertIn('Volume=bluefin-platform-webui:/app/backend/data', quadlet.read_text())
        self.assertEqual((self.base / 'webui-admin-password').read_bytes(), password)
        self.assertNotEqual((self.base / 'release').read_bytes(), before)

    def test_pull_failure_leaves_previous_files_and_release_intact(self):
        platform.provision(self.config)
        before = {p.name: p.read_bytes() for p in self.base.iterdir() if p.is_file()}
        updated = copy.deepcopy(self.config)
        updated['images']['webui'] = 'registry.example.test/webui@sha256:' + 'd' * 64
        self.images.side_effect = RuntimeError('registry offline')
        with self.assertRaisesRegex(RuntimeError, 'registry offline'):
            platform.provision(updated)
        for name, value in before.items():
            self.assertEqual((self.base / name).read_bytes(), value)

    def test_failed_health_stops_candidate_and_does_not_record_success(self):
        self.health.side_effect = RuntimeError('Open WebUI health check failed')
        with self.assertRaisesRegex(RuntimeError, 'Open WebUI'):
            platform.provision(self.config)
        self.assertFalse((self.base / 'release').exists())
        self.assertEqual(self.events[-1], ('systemctl', '--user', 'stop', 'bluefin-ai-platform.target'))

    def test_manual_rerun_does_not_interrupt_an_active_release(self):
        platform.provision(self.config)
        self.events.clear()
        self.active.side_effect = lambda unit: unit == 'bluefin-ai-platform.target'
        platform.provision(self.config)
        self.assertFalse(any(event[:3] == ('systemctl', '--user', 'stop') for event in self.events))
        updated = copy.deepcopy(self.config)
        updated['images']['webui'] = 'registry.example.test/webui@sha256:' + 'd' * 64
        with self.assertRaisesRegex(ValueError, 'Stop bluefin-ai-platform.target'):
            platform.provision(updated)
        self.assertFalse(any(event[:3] == ('systemctl', '--user', 'stop') for event in self.events))

    def test_orphaned_service_is_not_replaced_while_running(self):
        self.active.side_effect = lambda unit: unit == 'bluefin-platform-webui.service'
        with self.assertRaisesRegex(ValueError, 'outside its target'):
            platform.provision(self.config)
        self.images.assert_not_called()


class CommandFailureTests(unittest.TestCase):
    def test_start_failure_exposes_systemd_stderr_but_not_inspection_stdout(self):
        failure = subprocess.CalledProcessError(
            1, ['systemctl', '--user', 'start', 'bluefin-ai-platform.target'],
            output='private container data', stderr='Job failed: dependency missing')
        with patch.object(platform.subprocess, 'run', side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, 'dependency missing') as error:
                platform.run('systemctl', '--user', 'start', 'bluefin-ai-platform.target')
        self.assertIn('exit 1', str(error.exception))
        self.assertNotIn('private container data', str(error.exception))

    def test_timeout_identifies_the_operation(self):
        with patch.object(platform.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['podman', 'pull'], 30)):
            with self.assertRaisesRegex(RuntimeError, 'timed out after 30s: podman pull'):
                platform.run('podman', 'pull', 'example.test/image', timeout=30)


class RuntimeResetTests(unittest.TestCase):
    def test_fresh_runtime_is_started_without_resetting_unloaded_unit(self):
        def manager(args, **_kwargs):
            if args[2] == 'reset-failed':
                raise subprocess.CalledProcessError(1, args, stderr='Unit not loaded')
            return SimpleNamespace(stdout='')

        with patch.object(runtime.subprocess, 'run', side_effect=manager) as run:
            runtime.systemctl('reset-failed', 'bluefin-runtime-small.service')
            runtime.systemctl('start', 'bluefin-runtime-small.service')
        self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'start', 'bluefin-runtime-small.service'])

    def test_failed_runtime_is_reset_and_query_failure_is_not_ignored(self):
        failed = SimpleNamespace(stdout='bluefin-runtime-small.service loaded failed failed Runtime\n')
        with patch.object(runtime.subprocess, 'run', return_value=failed) as run:
            runtime.systemctl('reset-failed', 'bluefin-runtime-small.service')
        self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'reset-failed', 'bluefin-runtime-small.service'])
        with patch.object(runtime.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['systemctl'])):
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.systemctl('reset-failed', 'bluefin-runtime-small.service')


class ImageCacheTests(unittest.TestCase):
    def test_cached_images_boot_without_registry_access(self):
        config = configuration()
        with patch.object(platform.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
            with patch.object(platform, 'run') as run:
                platform.ensure_images(config)
        run.assert_not_called()

    def test_missing_image_is_pulled_once_and_storage_errors_are_not_hidden(self):
        config = configuration()  # all entries share one image
        with patch.object(platform.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with patch.object(platform, 'run') as run:
                platform.ensure_images(config)
        run.assert_called_once_with('podman', 'pull', config['images']['webui'])
        error = SimpleNamespace(returncode=125, stderr='storage unavailable')
        with patch.object(platform.subprocess, 'run', return_value=error):
            with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
                platform.ensure_images(config)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.image_id = 'sha256:' + 'a' * 64
        self.containers = [dict(Name='bluefin-platform-' + name, Image=self.image_id,
                                State={'Running': True}) for name in ('bifrost', 'webui', 'edge')]

    def metadata(self, *args, **_kwargs):
        payload = [{'Id': self.image_id}] if args[1:3] == ('image', 'inspect') else self.containers
        return SimpleNamespace(stdout=json.dumps(payload))

    @staticmethod
    def probe(port, _path, credential=None):
        if port == 18100:
            return 200, b'{"state":"idle"}'
        if port in (3000, 3001):
            return 200, b'{"status":true}'
        if port == 8643 and not credential:
            return 401, b''
        return 200, b'{"data":[{"id":"local/general"},{"id":"local/coding"}]}'

    def test_nonempty_but_wrong_model_catalog_does_not_pass_readiness(self):
        def probe(port, path, credential=None):
            if port == 18101:
                return 200, b'{"data":[{"id":"unconfigured/model"}]}'
            return self.probe(port, path, credential)

        with patch.object(platform, 'run', side_effect=self.metadata), patch.object(platform, 'probe', side_effect=probe):
            with patch.object(platform, 'key', return_value='test-key'):
                with self.assertRaisesRegex(RuntimeError, 'missing configured aliases'):
                    platform.verify_platform(configuration(), timeout=0)

    def test_readiness_checks_actual_image_ids_and_private_auth(self):
        with patch.object(platform, 'run', side_effect=self.metadata):
            with patch.object(platform, 'key', side_effect=lambda name: name + '-secret'):
                with patch.object(platform, 'probe', side_effect=self.probe) as probe:
                    platform.verify_platform(configuration(), timeout=0)
        probe.assert_any_call(18100, '/control/status', 'admin-key-secret')
        probe.assert_any_call(8643, '/v1/models', 'client-key-secret')

    def test_old_running_image_is_not_treated_as_updated(self):
        self.containers[1]['Image'] = 'sha256:' + 'b' * 64
        with patch.object(platform, 'run', side_effect=self.metadata), patch.object(platform, 'key', return_value='key'):
            with self.assertRaisesRegex(RuntimeError, 'not running its configured image'):
                platform.verify_platform(configuration(), timeout=0)

    def test_invalid_cached_metadata_has_an_actionable_error(self):
        with patch.object(platform, 'run', return_value=SimpleNamespace(stdout='[]')):
            with self.assertRaisesRegex(RuntimeError, 'Invalid cached image metadata for bifrost'):
                platform.verify_platform(configuration(), timeout=0)

    def test_unhealthy_webui_and_missing_gateway_auth_are_rejected(self):
        for failed_port, status, body in ((3000, 503, b'{}'), (8643, 200, b'{"data":[1]}')):
            def probe(port, path, credential=None):
                if port == failed_port:
                    return status, body
                return self.probe(port, path, credential)

            with self.subTest(port=failed_port), patch.object(platform, 'run', side_effect=self.metadata):
                with patch.object(platform, 'key', return_value='key'), patch.object(platform, 'probe', side_effect=probe):
                    with self.assertRaisesRegex(RuntimeError, 'Platform readiness failed'):
                        platform.verify_platform(configuration(), timeout=0)


if __name__ == '__main__':
    unittest.main()
