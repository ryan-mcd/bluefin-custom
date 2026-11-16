"""Default boot must start the UI without waiting for GPU/model preparation."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_ai_platform import ASSETS, configuration, platform, runtime


class StartupDefaultsTests(unittest.TestCase):
    def test_default_boot_starts_without_a_model_download_or_implicit_selection(self):
        config = platform.resolve_images(json.loads((ASSETS / 'platform.json').read_text()), ASSETS / 'Dockerfile')
        platform.validate(config)
        self.assertTrue(config['enabled'])
        self.assertIsNone(config['aliases']['general'])
        self.assertEqual(config['profiles'], {})
        self.assertEqual(config['prepare_on_boot'], [])
        self.assertNotIn('webui_aliases',config)

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
            self.assertEqual(artifact.call_count, 1)
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
        with patch.object(runtime.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0), failure]):
            with self.assertRaisesRegex(runtime.Rejected, 'WebUI remains available') as error:
                runtime.runtime_preflight(profile)
        self.assertEqual(error.exception.code, 503)

    def test_existing_model_waits_for_its_checksum_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            models = home / '.local/share/bluefin-ai/models'
            models.mkdir(parents=True)
            (models / 'test.gguf').write_bytes(b'test')
            profile = configuration()['profiles']['small']
            with patch.object(Path, 'home', return_value=home), patch.object(runtime.subprocess, 'run') as run:
                with self.assertRaisesRegex(runtime.Rejected, 'preparation is pending'):
                    runtime.runtime_preflight(profile)
            run.assert_not_called()

    def test_verified_model_can_run_while_other_profiles_are_still_preparing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            models = home / '.local/share/bluefin-ai/models'
            models.mkdir(parents=True)
            path = models / 'test.gguf'
            path.write_bytes(b'test')
            artifact = dict(name='test.gguf', bytes=4, sha256=hashlib.sha256(b'test').hexdigest())
            platform.model_artifact(models, artifact)
            profile = dict(configuration()['profiles']['small'], artifacts=[artifact])
            with patch.object(Path, 'home', return_value=home):
                with patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                    runtime.runtime_preflight(profile)
                    path.chmod(0o600)  # ctime-only changes such as SELinux relabeling keep the receipt valid.
                    runtime.runtime_preflight(profile)
                    self.assertFalse(any('systemctl' in call.args[0] for call in run.call_args_list))
                # A same-length change cannot reuse the old verification receipt.
                path.write_bytes(b'evil')
                with self.assertRaisesRegex(runtime.Rejected, 'preparation is pending'):
                    runtime.runtime_preflight(profile)

    def test_catalog_availability_requires_verified_weights_and_a_cached_image(self):
        profile=configuration()['profiles']['small']
        with tempfile.TemporaryDirectory() as directory,patch.object(Path,'home',return_value=Path(directory)):
            with patch.object(runtime.subprocess,'run') as run:
                self.assertFalse(runtime.runtime_cached(profile));run.assert_not_called()
            with patch.object(runtime,'artifact_ready',return_value=True):
                with patch.object(runtime.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
                    self.assertFalse(runtime.runtime_cached(profile))
                with patch.object(runtime.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
                    self.assertTrue(runtime.runtime_cached(profile))

    def test_status_uses_the_agent_account_even_from_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'platform.json').write_text('{"enabled":true}')
            with patch.object(platform, 'ASSETS', base), patch.object(platform, 'run', return_value=SimpleNamespace(stdout='claudex\n')):
                with patch.object(platform.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1001, pw_dir='/var/home/claudex')):
                    with patch.object(platform.os, 'geteuid', return_value=0), patch.object(platform.subprocess, 'run') as run:
                        platform.show_status()
            calls = [c.args[0] for c in run.call_args_list]
            self.assertTrue(any(c[:4] == ['runuser', '-u', 'claudex', '--'] and 'podman' in c for c in calls))
            self.assertTrue(any('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus' in c for c in calls))


if __name__ == '__main__':
    unittest.main()
