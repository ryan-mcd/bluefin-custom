"""Build inputs written to root-sourced configuration must be safe."""
import os
import py_compile
import shutil
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


class PlatformAssetInstallTest(unittest.TestCase):
    def test_python_compilation_output_does_not_break_asset_installation(self):
        script = BUILD.read_text().split('install -d /usr/share/bluefin-ai/platform', 1)[1]
        script = 'install -d /usr/share/bluefin-ai/platform' + script.split('for executable in ', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            assets = base / 'ctx/ai-platform'
            destination = base / 'installed'
            shutil.copytree(BUILD.parent / 'ai-platform', assets,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
            # Match CI's explicit py_compile output, rather than relying on import caching.
            cache = assets / '__pycache__'
            cache.mkdir()
            py_compile.compile(str(assets / 'bluefin-ai-platform'),
                               cfile=str(cache / 'bluefin-ai-platform.cpython-test.pyc'), doraise=True)
            (assets / 'stray.pyc').write_bytes(b'build artifact')
            (assets / 'stray.pyo').write_bytes(b'build artifact')
            script = script.replace('/ctx/ai-platform', str(assets))
            script = script.replace('/usr/share/bluefin-ai/platform', str(destination))
            script = script.replace('/usr/lib/systemd/user', str(base / 'systemd/user'))
            result = subprocess.run(['bash', '-e', '-u', '-o', 'pipefail', '-c', script],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            expected = {p.name for p in assets.iterdir()
                        if p.is_file() and p.suffix not in ('.pyc', '.pyo')}
            self.assertEqual({p.name for p in destination.iterdir()}, expected)
            for name in expected:
                self.assertEqual((destination / name).read_bytes(), (assets / name).read_bytes())
                self.assertEqual((destination / name).stat().st_mode & 0o777, 0o644)


if __name__ == '__main__':
    unittest.main()
