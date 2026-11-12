"""Verify private settings preparation and failure before side effects."""
import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'build_files'
loader = importlib.machinery.SourceFileLoader('prepare', str(ROOT / 'bluefin-strata-prepare'))
spec = importlib.util.spec_from_loader(loader.name, loader)
prepare = importlib.util.module_from_spec(spec)
loader.exec_module(prepare)
runtime = prepare.load_helper('bluefin-strata-run')
proxy = prepare.load_helper('bluefin-strata-proxy')


class PrepareTests(unittest.TestCase):
    def test_private_key_is_persistent_and_settings_are_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'strata'
            settings = {'model': 'IQ3_XXS', 'context': 64000, 'local_port': 8080, 'upstream_port': 18080}
            prepare.prepare(base, settings, runtime, proxy)
            key = proxy.read_key(base / 'backend-key')
            self.assertEqual((base.stat().st_mode & 0o777), 0o700)
            self.assertEqual(((base / 'runtime.json').stat().st_mode & 0o777), 0o600)
            self.assertEqual(json.loads((base / 'runtime.json').read_text()), settings)
            prepare.prepare(base, {**settings, 'model': 'IQ2_XS'}, runtime, proxy)
            self.assertEqual(proxy.read_key(base / 'backend-key'), key)

    def test_invalid_model_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'strata'
            with self.assertRaises(ValueError):
                prepare.prepare(base, {'model': 'other', 'context': 64000, 'local_port': 8080,
                                       'upstream_port': 18080}, runtime, proxy)
            self.assertFalse(base.exists())


if __name__ == '__main__':
    unittest.main()
