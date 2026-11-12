"""Configuration migration and Strata compatibility checks."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROFILE = Path(__file__).resolve().parents[1] / 'build_files' / 'bluefin-ai-profile'


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = {**os.environ, 'HOME': self.temp.name}

    def run_profile(self, *args):
        return subprocess.run(['bash', str(PROFILE), *args], env=self.env,
                              text=True, capture_output=True)

    def test_old_config_defaults_to_ramalama(self):
        directory = Path(self.temp.name) / '.config/bluefin-ai'
        directory.mkdir(parents=True)
        (directory / 'deployment.conf').write_text('PROFILE=model-only\nMODEL=llama3.2\n')
        result = self.run_profile('get', 'MODEL_BACKEND')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ramalama')

    def test_rejects_unknown_backend_and_incompatible_strata_model(self):
        for model, backend in (('llama3.2', 'strata'), ('IQ3_XXS', 'other')):
            with self.subTest(model=model, backend=backend):
                result = self.run_profile('set', 'model-only', model, '8080', '64000',
                                          '8643', 'lan', 'off', '', '', '', backend)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((Path(self.temp.name) / '.config/bluefin-ai/deployment.conf').exists())

    def test_supported_strata_quant_is_persisted(self):
        result = self.run_profile('set', 'model-only', 'IQ3_XXS', '8080', '64000',
                                  '8643', 'lan', 'off', '', '', '', 'strata')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_profile('get', 'MODEL_BACKEND').stdout.strip(), 'strata')

    def test_apply_rejects_stale_strata_runtime_before_systemctl(self):
        result = self.run_profile('set', 'model-only', 'IQ3_XXS', '8080', '64000',
                                  '8643', 'lan', 'off', '', '', '', 'strata')
        self.assertEqual(result.returncode, 0, result.stderr)
        binaries = Path(self.temp.name) / 'bin'
        binaries.mkdir()
        for name, content in (('bluefin-agent-user', 'exit 0'),
                              ('systemctl', 'touch "$HOME/systemctl-called"')):
            path = binaries / name
            path.write_text('#!/usr/bin/env bash\n' + content + '\n')
            path.chmod(0o755)
        self.env['PATH'] = str(binaries) + os.pathsep + self.env['PATH']
        result = self.run_profile('apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not match', result.stderr)
        self.assertFalse((Path(self.temp.name) / 'systemctl-called').exists())


if __name__ == '__main__':
    unittest.main()
