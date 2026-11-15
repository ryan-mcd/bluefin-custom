"""Default boot must start the UI without waiting for GPU/model preparation."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_ai_platform import ASSETS, configuration, platform, runtime


class StartupDefaultsTests(unittest.TestCase):
    def test_default_is_enabled_with_complete_immutable_starter_model(self):
        config = platform.resolve_images(json.loads((ASSETS / 'platform.json').read_text()), ASSETS / 'Dockerfile')
        platform.validate(config)
        self.assertTrue(config['enabled'])
        self.assertEqual(config['aliases'], {'general': 'starter'})
        artifact = config['profiles']['starter']['artifacts'][0]
        self.assertIn('/23749fefcc72300e3a2ad315e1317431b06b590a/', artifact['url'])
        self.assertEqual(artifact['bytes'], 639446688)
        self.assertEqual(artifact['sha256'], '9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031')

    def test_control_plane_image_pull_does_not_pull_optional_engines(self):
        config = configuration()
        config['profiles']['small']['image'] = 'registry.example.test/engine@sha256:' + 'e' * 64
        with patch.object(platform.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with patch.object(platform, 'run') as run:
                platform.ensure_images(config, runtimes=False)
        run.assert_called_once_with('podman', 'pull', config['images']['webui'])

    def test_prepare_models_is_separate_and_has_no_ttm_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'controller.json').write_text(json.dumps(configuration()))
            with patch.object(platform, 'BASE', base), patch.object(platform, 'run') as run:
                with patch.object(platform, 'model_artifact') as artifact, patch.object(platform, 'ensure_images') as images:
                    platform.prepare_models()
            run.assert_called_once_with('bluefin-agent-user', 'require')
            self.assertEqual(artifact.call_count, 2)
            self.assertEqual(images.call_args.args[0]['images'], {})

    def test_missing_model_refuses_inference_without_stopping_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Path, 'home', return_value=Path(directory)):
                with patch.object(runtime.subprocess, 'run') as run:
                    with self.assertRaisesRegex(runtime.Rejected, 'preparation is pending'):
                        runtime.runtime_preflight(configuration()['profiles']['small'])
            run.assert_not_called()

    def test_gpu_failure_is_an_inference_error(self):
        profile = dict(configuration()['profiles']['small'], artifacts=[])
        failure = SimpleNamespace(returncode=1, stderr='reboot required')
        with patch.object(runtime.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0, stdout='exited\n'), failure]):
            with self.assertRaisesRegex(runtime.Rejected, 'WebUI remains available') as error:
                runtime.runtime_preflight(profile)
        self.assertEqual(error.exception.code, 503)

    def test_existing_model_waits_for_checksum_and_image_preparation(self):
        profile = dict(configuration()['profiles']['small'], artifacts=[])
        with patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='running\n')) as run:
            with self.assertRaisesRegex(runtime.Rejected, 'preparation is pending'):
                runtime.runtime_preflight(profile)
        self.assertEqual(run.call_count, 1)

    def test_status_uses_the_agent_account_even_from_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'platform.json').write_text('{"enabled":true}')
            with patch.object(platform, 'ASSETS', base), patch.object(platform, 'run', return_value=SimpleNamespace(stdout='claudex\n')):
                with patch.object(platform.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1001)):
                    with patch.object(platform.os, 'geteuid', return_value=0), patch.object(platform.subprocess, 'run') as run:
                        platform.show_status()
            calls = [c.args[0] for c in run.call_args_list]
            self.assertTrue(any(c[:4] == ['runuser', '-u', 'claudex', '--'] and 'podman' in c for c in calls))
            self.assertTrue(any('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus' in c for c in calls))


if __name__ == '__main__':
    unittest.main()
