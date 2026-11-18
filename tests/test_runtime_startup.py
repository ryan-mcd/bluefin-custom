"""Exited engines must fail promptly, while live engines can finish preparing."""
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_ai_platform import configuration, runtime


class RuntimeStartupTests(unittest.TestCase):
    def probe(self, stdout, code=0):
        return patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(stdout=stdout, returncode=code))

    def test_failed_or_cleanly_exited_runtime_reports_state_without_journal_contents(self):
        for state, result, status in [('failed', 'exit-code', '1'), ('failed', 'oom-kill', '9'), ('inactive', 'success', '0')]:
            with self.subTest(state=state, result=result), self.probe(
                    f'LoadState=loaded\nActiveState={state}\nResult={result}\nExecMainStatus={status}\n') as run:
                message = runtime.runtime_failure('bluefin-runtime-small.service')
            self.assertIn(f'result={result}, status={status}', message)
            self.assertIn('journalctl --user -b -u bluefin-runtime-small.service', message)
            self.assertIn('--user', run.call_args.args[0])

    def test_active_or_activating_runtime_is_not_reported_as_failed(self):
        for state in ('active', 'activating', 'reloading', 'deactivating'):
            with self.subTest(state=state), self.probe('LoadState=loaded\nActiveState=' + state):
                self.assertIsNone(runtime.runtime_failure('bluefin-runtime-small.service'))

    def test_missing_unit_is_reported_even_when_show_returns_nonzero(self):
        with self.probe('LoadState=not-found\nActiveState=inactive\n', code=1):
            self.assertIn('unit is missing', runtime.runtime_failure('bluefin-runtime-small.service'))

    def test_query_errors_do_not_invent_an_engine_exit(self):
        with self.probe('', code=1): self.assertIsNone(runtime.runtime_failure('bluefin-runtime-small.service'))
        for exc in (OSError('unavailable'), subprocess.TimeoutExpired('systemctl', 5)):
            with self.subTest(exc=type(exc)), patch.object(runtime.subprocess, 'run', side_effect=exc):
                self.assertIsNone(runtime.runtime_failure('bluefin-runtime-small.service'))

    def controller(self, health, failure):
        self.events = []
        return runtime.Controller(configuration(), manage=lambda *a: self.events.append(a), health=health,
                                  memory=lambda: 120*runtime.GIB, preflight=lambda p: None, failure=failure)

    def test_exited_runtime_aborts_startup_without_sleeping_until_deadline(self):
        controller = self.controller(lambda p: False, lambda unit: 'Runtime exited before readiness (status=1)')
        with patch.object(runtime.time, 'sleep') as sleep:
            with self.assertRaisesRegex(runtime.Rejected, 'exited before readiness'):
                with controller.lease('general'): pass
        sleep.assert_not_called()
        self.assertIn(('stop', 'bluefin-runtime-small.service'), self.events)
        self.assertEqual(controller.inflight, 0); self.assertEqual(controller.reserved, 0)
        self.assertIn('small', controller.cooldown)

    def test_preparing_runtime_can_become_ready_and_reuse_does_not_probe_failure(self):
        health = unittest.mock.Mock(side_effect=[False, True, True])
        failure = unittest.mock.Mock(return_value=None)
        controller = self.controller(health, failure)
        with patch.object(runtime.time, 'sleep') as sleep:
            with controller.lease('general'): pass
            with controller.lease('general'): pass
        sleep.assert_called_once_with(1)
        failure.assert_called_once_with('bluefin-runtime-small.service')
        self.assertEqual(self.events.count(('start', 'bluefin-runtime-small.service')), 1)

    def test_failed_candidate_restores_previous_model(self):
        controller = self.controller(lambda p: p['backend_model'] == 'small', lambda unit: 'Runtime exited before readiness')
        with controller.lease('general'): pass
        with self.assertRaisesRegex(runtime.Rejected, 'exited before readiness'):
            with controller.lease('coding'): pass
        self.assertEqual(controller.current, 'small')
        self.assertLess(self.events.index(('stop', 'bluefin-runtime-large.service')),
                        len(self.events)-1)
        self.assertEqual(self.events[-1], ('start', 'bluefin-runtime-small.service'))


if __name__ == '__main__': unittest.main()
