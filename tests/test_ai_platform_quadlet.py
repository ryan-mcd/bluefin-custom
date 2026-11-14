"""Use Podman's real parser where installed; no containers or GPU required."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ai_platform import ASSETS, configuration, platform

GENERATOR = next((p for p in ('/usr/libexec/podman/quadlet',
                              '/usr/lib/systemd/system-generators/podman-system-generator')
                  if Path(p).is_file()), None)


@unittest.skipUnless(GENERATOR, 'Podman Quadlet generator is not installed')
class QuadletTest(unittest.TestCase):
    def test_control_plane_and_engine_quadlets_generate_services(self):
        config = configuration()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            quadlets = base / 'quadlets'
            quadlets.mkdir()
            for name in ('bifrost', 'webui', 'edge'):
                text = (ASSETS / (name + '.container.in')).read_text()
                (quadlets / ('bluefin-platform-' + name + '.container')).write_text(
                    text.replace('@@IMAGE@@', config['images'][name]))
            for name, profile in config['profiles'].items():
                (quadlets / ('bluefin-runtime-' + name + '.container')).write_text(
                    platform.runtime_quadlet(name, profile))
            result = subprocess.run([GENERATOR, '--user', '--dryrun'], text=True, capture_output=True,
                                    env={**os.environ, 'QUADLET_UNIT_DIRS': str(quadlets)}, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('bluefin-platform-bifrost', 'bluefin-platform-webui', 'bluefin-platform-edge',
                         'bluefin-runtime-small', 'bluefin-runtime-large'):
                self.assertIn(name + '.service', result.stdout, result.stderr)
            self.assertNotIn('unsupported', result.stderr.lower())
            self.assertNotIn('error', result.stderr.lower())


if __name__ == '__main__':
    unittest.main()
