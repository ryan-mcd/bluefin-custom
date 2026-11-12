"""Check the Strata container invocation without running Podman."""
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'build_files' / 'bluefin-strata-run'
loader = importlib.machinery.SourceFileLoader('strata_run', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
runtime = importlib.util.module_from_spec(spec)
loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def test_command_uses_narrow_devices_and_does_not_include_key(self):
        args = runtime.command({'model': 'IQ3_XXS', 'context': 64000, 'upstream_port': 18080, 'local_port': 8080},
                               Path('/home/agent/.config/bluefin-ai/strata/backend-key'),
                               Path('/dev/dri/renderD128'))
        self.assertIn(runtime.IMAGE, args)
        self.assertIn('127.0.0.1:18080:8080', args)
        self.assertIn('/dev/dri/renderD128', args)
        self.assertNotIn('--privileged', args)
        self.assertNotIn('--network=host', args)
        self.assertNotIn('/home/agent:/data', args)
        self.assertIn('API_KEY_FILE=/run/bluefin/backend-key', args)

    def test_invalid_settings_are_rejected(self):
        good = {'model': 'IQ3_XXS', 'context': 64000, 'upstream_port': 18080, 'local_port': 8080}
        for changes in ({'model': 'other'}, {'model': 'IQ3_XXS;id'},
                        {'context': True}, {'context': 0}, {'upstream_port': 65536}, {'local_port': 18080}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                runtime.validate({**good, **changes})
        with self.assertRaises(ValueError):
            runtime.command(good, Path('/home/agent/key'), Path('/dev/dri/card0'))


if __name__ == '__main__':
    unittest.main()
