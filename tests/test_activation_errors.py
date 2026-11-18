"""Activation errors remain useful after retries enter the cooldown window."""
import copy
import time
import unittest

from test_ai_platform import configuration, runtime


class ActivationErrorTests(unittest.TestCase):
    def controller(self, memory=120):
        self.events = []
        return runtime.Controller(configuration(), manage=lambda *a: self.events.append(a), health=lambda p: True,
                                  memory=lambda: memory*runtime.GIB, preflight=lambda p: None)

    def test_cooldown_keeps_original_memory_error_and_retry_delay_without_starting_again(self):
        controller = self.controller(memory=20)
        with self.assertRaises(runtime.Rejected) as original:
            with controller.lease('general'): pass
        self.assertEqual(original.exception.code, 507)
        with self.assertRaises(runtime.Rejected) as retry:
            with controller.lease('general'): pass
        self.assertEqual(retry.exception.code, 503)
        self.assertIn(str(original.exception), str(retry.exception)); self.assertIn('retry in ', str(retry.exception))
        self.assertFalse(any(e[0] == 'start' for e in self.events))
        failure = controller.status()['activation_failures']['small']
        self.assertEqual(failure['code'], 507); self.assertEqual(failure['message'], str(original.exception))
        self.assertGreater(failure['retry_after_seconds'], 0); self.assertLessEqual(failure['retry_after_seconds'], 60)

    def test_successful_retry_clears_retained_failure(self):
        controller = self.controller(memory=20)
        with self.assertRaises(runtime.Rejected):
            with controller.lease('general'): pass
        controller.cooldown['small'] = time.monotonic()-1; controller.memory = lambda: 120*runtime.GIB
        with controller.lease('general'): pass
        self.assertNotIn('small', controller.cooldown)
        self.assertEqual(controller.status()['activation_failures'], {})

    def test_failed_candidate_and_failed_restore_keep_separate_causes(self):
        controller = self.controller()
        with controller.lease('general'): pass
        def fail(*args):
            if args[0] == 'start': raise runtime.Rejected(503, 'Engine failed: ' + args[1])
        controller.manage = fail
        with self.assertRaises(runtime.Rejected): controller.control('pin', 'large')
        failures = controller.status()['activation_failures']
        self.assertEqual(set(failures), {'small', 'large'})
        self.assertIn('bluefin-runtime-small.service', failures['small']['message'])
        self.assertIn('bluefin-runtime-large.service', failures['large']['message'])

    def test_generic_exception_contents_are_not_copied_and_control_messages_are_bounded(self):
        controller = self.controller()
        controller.remember_activation_failure('large', OSError('private-exception-content'))
        message = controller.status()['activation_failures']['large']['message']
        self.assertNotIn('private-exception-content', message); self.assertIn('OSError', message)
        controller.remember_activation_failure('small', runtime.Rejected(503, 'engine\nfailed\r' + 'x'*2000))
        message = controller.status()['activation_failures']['small']['message']
        self.assertNotIn('\n', message); self.assertNotIn('\r', message); self.assertLessEqual(len(message), 1024)

    def test_registry_reload_discards_failures_for_removed_profiles(self):
        controller = self.controller()
        controller.remember_activation_failure('large', runtime.Rejected(503, 'failed'))
        cfg = copy.deepcopy(controller.config); del cfg['profiles']['large']; del cfg['aliases']['coding']
        controller.refresh = lambda: cfg; controller.control('reload')
        self.assertEqual(controller.cooldown, {}); self.assertEqual(controller.status()['activation_failures'], {})


if __name__ == '__main__': unittest.main()
