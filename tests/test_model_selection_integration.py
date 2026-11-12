"""Exercise the real recipe and helpers with simulated systemd and HTTP."""
import json
import os
import signal
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUST = os.environ.get('JUST_BIN') or shutil.which('just')


@unittest.skipUnless(JUST, 'just is not installed')
class ModelSelectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / 'bin'
        self.state = self.home / 'units'
        self.bin.mkdir()
        self.state.mkdir()
        for name in ('bluefin-ai-profile', 'bluefin-model-switch', 'bluefin-strata-prepare',
                     'bluefin-strata-proxy', 'bluefin-strata-run', 'bluefin-strata-service-enable'):
            shutil.copy(ROOT / 'build_files' / name, self.bin / name)
            (self.bin / name).chmod(0o755)
        self.stub('bluefin-agent-user', 'exit 0')
        self.stub('bluefin-strata-check', 'printf "%s\\n" qwen3.8-flash-next-iq3_xxs')
        self.stub('curl', '''printf '%s\\n' '{"data":[{"id":"qwen3.8-flash-next-iq3_xxs"}]}' ''')
        self.stub('ujust', 'exec "$MOCK_JUST" --justfile "$MOCK_JUSTFILE" "$@"')
        self.stub('bluefin-ramalama-quadlet-fix', 'exit 0')
        self.stub('ramalama', '''
for argument in "$@"; do
  case "$argument" in
    --generate=quadlet:*)
      directory="${argument#--generate=quadlet:}"
      mkdir -p "$directory"
      printf '%s\\n' 'simulated quadlet' > "$directory/bluefin-ramalama.container"
      ;;
  esac
done
''')
        self.stub('systemctl', '''
[[ "$1" == --user ]] && shift
action="$1"; shift
case "$action" in
  is-active|is-enabled|is-failed)
    [[ "${1:-}" == --quiet ]] && shift
    [[ "$action" == is-active && -f "$MOCK_UNITS/$1" ]]
    ;;
  restart|start)
    if [[ "$action" == restart && "${FAIL_STRATA:-}" == 1 && "$1" == bluefin-strata.service ]]; then
      exit 1
    fi
    if [[ "$action" == restart && "${HOLD_STRATA:-}" == 1 && "$1" == bluefin-strata.service ]]; then
      touch "$MOCK_UNITS/restarting"
      sleep 30
    fi
    for unit in "$@"; do touch "$MOCK_UNITS/$unit"; done
    ;;
  stop)
    for unit in "$@"; do rm -f "$MOCK_UNITS/$unit"; done
    ;;
  disable|enable|daemon-reload|reset-failed) exit 0 ;;
  *) exit 1 ;;
esac
''')
        self.env = {**os.environ, 'HOME': str(self.home), 'MOCK_UNITS': str(self.state),
                    'MOCK_JUST': JUST, 'MOCK_JUSTFILE': str(ROOT / 'build_files/ai-agents.just'),
                    'PATH': str(self.bin) + os.pathsep + os.environ.get('PATH', '')}
        self.env.pop('FAIL_STRATA', None)
        self.env.pop('HOLD_STRATA', None)

    def stub(self, name, script):
        path = self.bin / name
        path.write_text('#!/usr/bin/env bash\nset -e\n' + script + '\n')
        path.chmod(0o755)

    def select(self, *, fail=False):
        env = {**self.env, 'FAIL_STRATA': '1'} if fail else self.env
        return subprocess.run([JUST, '--justfile', str(ROOT / 'build_files/ai-agents.just'),
                               'ai-model-select', 'IQ3_XXS', '64000', 'strata'],
                              text=True, capture_output=True, env=env, timeout=15)

    def test_selection_runs_recipe_and_persists_only_ready_backend(self):
        result = self.select()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = (self.home / '.config/bluefin-ai/deployment.conf').read_text()
        self.assertIn('MODEL_BACKEND=strata', config)
        self.assertIn('MODEL=IQ3_XXS', config)
        self.assertIn('PROFILE=model-only', config)
        runtime = json.loads((self.home / '.config/bluefin-ai/strata/runtime.json').read_text())
        self.assertEqual(runtime, {'model': 'IQ3_XXS', 'context': 64000,
                                   'local_port': 8080, 'upstream_port': 18080})
        self.assertTrue((self.state / 'bluefin-strata.service').exists())
        self.assertTrue((self.state / 'bluefin-strata-proxy.service').exists())
        self.assertFalse((self.state / 'bluefin-ramalama.service').exists())
        key = self.home / '.config/bluefin-ai/strata/backend-key'
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(key.read_text().strip(), result.stdout + result.stderr)

    def test_start_failure_restores_existing_ramalama(self):
        subprocess.run([str(self.bin / 'bluefin-ai-profile'), 'set', 'model-only', 'llama3.2'],
                       env=self.env, check=True, capture_output=True)
        quadlet = self.home / '.config/containers/systemd/bluefin-ramalama.container'
        quadlet.parent.mkdir(parents=True)
        quadlet.write_text('previous quadlet')
        (self.state / 'bluefin-ramalama.service').touch()
        result = self.select(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(quadlet.read_text(), 'previous quadlet')
        self.assertTrue((self.state / 'bluefin-ramalama.service').exists())
        self.assertIn('MODEL=llama3.2', (self.home / '.config/bluefin-ai/deployment.conf').read_text())
        self.assertFalse((self.state / 'bluefin-strata.service').exists())

    def test_switching_back_uses_existing_ramalama_recipe(self):
        result = self.select()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([JUST, '--justfile', str(ROOT / 'build_files/ai-agents.just'),
                                 'ai-model-select', 'llama3.2', '64000'],
                                text=True, capture_output=True, env=self.env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = (self.home / '.config/bluefin-ai/deployment.conf').read_text()
        self.assertIn('MODEL_BACKEND=ramalama', config)
        self.assertIn('MODEL=llama3.2', config)
        self.assertTrue((self.state / 'bluefin-ramalama.service').exists())
        self.assertFalse((self.state / 'bluefin-strata.service').exists())
        self.assertFalse((self.state / 'bluefin-strata-proxy.service').exists())
        self.assertTrue((self.home / '.config/containers/systemd/bluefin-ramalama.container').is_file())

    def test_interrupted_switch_attempts_to_restore_previous_service(self):
        subprocess.run([str(self.bin / 'bluefin-ai-profile'), 'set', 'model-only', 'llama3.2'],
                       env=self.env, check=True, capture_output=True)
        quadlet = self.home / '.config/containers/systemd/bluefin-ramalama.container'
        quadlet.parent.mkdir(parents=True)
        quadlet.write_text('previous quadlet')
        (self.state / 'bluefin-ramalama.service').touch()
        process = subprocess.Popen([JUST, '--justfile', str(ROOT / 'build_files/ai-agents.just'),
                                    'ai-model-select', 'IQ3_XXS', '64000', 'strata'],
                                   env={**self.env, 'HOLD_STRATA': '1'}, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.state / 'restarting').exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue((self.state / 'restarting').exists(), 'switch did not reach startup')
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=10)
            self.assertNotEqual(process.returncode, 0)
            self.assertTrue((self.state / 'bluefin-ramalama.service').exists())
            self.assertEqual(quadlet.read_text(), 'previous quadlet')
            self.assertIn('MODEL=llama3.2', (self.home / '.config/bluefin-ai/deployment.conf').read_text())
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()


if __name__ == '__main__':
    unittest.main()
