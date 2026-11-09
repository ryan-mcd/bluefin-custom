"""Build inputs written to root-sourced configuration must be safe."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1] / 'build_files' / 'build.sh'


class BuildInputSecurityTest(unittest.TestCase):
    def test_linger_rejects_shell_and_configuration_injection(self):
        script = BUILD.read_text().split('### Install packages', 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'executed'
            for value in ('true', 'false', 'YES', '0', '1'):
                result = subprocess.run(['bash', '-c', script], env={**os.environ, 'BLUEFIN_AGENT_ENABLE_LINGER': value}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for value in ('$(touch ' + str(marker) + ')', 'true"\nEVIL=1', 'maybe'):
                result = subprocess.run(['bash', '-c', script], env={**os.environ, 'BLUEFIN_AGENT_ENABLE_LINGER': value}, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
