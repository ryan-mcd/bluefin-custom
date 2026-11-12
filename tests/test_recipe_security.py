"""Exercise just recipes with hostile arguments and stubbed host commands."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUST = os.environ.get('JUST_BIN') or shutil.which('just')


@unittest.skipUnless(JUST, 'just is not installed')
class RecipeSecurityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for command, body in {
            'bluefin-agent-user': 'exit 0',
            'ramalama': 'exit 0',
            'bluefin-ai-profile': 'exit 1',
            'distrobox': '''if [[ "$1" == list ]]; then
  echo bluefin-agent-ubuntu
else
  printf '%s\\n' "$@" > "$RECIPE_ARGUMENT_LOG"
fi''',
        }.items():
            path = self.bin / command
            path.write_text('#!/usr/bin/env bash\n' + body + '\n')
            path.chmod(0o755)
        self.env = {**os.environ, 'HOME': str(self.base),
                    'PATH': str(self.bin) + os.pathsep + os.environ.get('PATH', ''),
                    'RECIPE_ARGUMENT_LOG': str(self.base / 'args')}
        self.marker = self.base / 'injected'
        self.payload = '$(touch ' + str(self.marker) + ')'

    def recipe(self, name, *args):
        return subprocess.run([JUST, '--justfile', str(ROOT / 'build_files/ai-agents.just'), name, *args],
                              env=self.env, text=True, capture_output=True, timeout=10)

    def test_metacharacters_are_not_executed_before_validation(self):
        for name, args in [('ai-model-select', [self.payload]),
                           ('hermes-workspace-create', [self.payload]),
                           ('ramalama-service-disable', [self.payload]),
                           ('hermes-local-configure', [self.payload]),
                           ('ai-profile-set', [self.payload])]:
            with self.subTest(recipe=name):
                result = self.recipe(name, *args)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.marker.exists())

    def test_service_and_workspace_names_reject_traversal(self):
        for name in ('hermes-workspace-create', 'ramalama-service-disable'):
            with self.subTest(recipe=name):
                result = self.recipe(name, '../outside')
                self.assertNotEqual(result.returncode, 0)
        result = self.recipe('hermes-workspace-create', 'safe-workspace')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.base / 'src' / 'safe-workspace').is_dir())

    def test_ramalama_service_rejects_invalid_inputs_before_generating_quadlet(self):
        for args in (['model;id', '8080', 'bluefin-ramalama', '64000'],
                     ['llama3.2', '08080', 'bluefin-ramalama', '64000'],
                     ['llama3.2', '65536', 'bluefin-ramalama', '64000'],
                     ['llama3.2', '8080', 'bluefin-ramalama', '064000'],
                     ['llama3.2', '8080', 'bluefin-ramalama', '999999999']):
            with self.subTest(args=args):
                result = self.recipe('ramalama-service-enable', *args)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((self.base / '.config/containers/systemd').exists())

    def test_root_build_options_are_not_shell_source(self):
        podman = self.bin / 'podman'
        podman.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$RECIPE_ARGUMENT_LOG\"\n")
        podman.chmod(0o755)
        env = {**self.env, 'BLUEFIN_AGENT_USER': self.payload}
        result = subprocess.run([JUST, '--justfile', str(ROOT / 'Justfile'), 'build'],
                                env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('BLUEFIN_AGENT_USER=' + self.payload, (self.base / 'args').read_text())
        self.assertFalse(self.marker.exists())

    def test_vm_rejects_shell_metacharacters(self):
        result = subprocess.run([JUST, '--justfile', str(ROOT / 'Justfile'), 'spawn-vm', self.payload],
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_nested_container_receives_values_as_arguments(self):
        for recipe, args in [('agent-container-openclaw-install', [self.payload, 'false']),
                             ('agent-container-npm-install-trusted', [self.payload, 'latest'])]:
            with self.subTest(recipe=recipe):
                result = self.recipe(recipe, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = (self.base / 'args').read_text().splitlines()
                self.assertIn(self.payload, lines)
                self.assertNotIn(self.payload, next(line for line in lines if 'source /etc/profile.d/' in line))
                self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
