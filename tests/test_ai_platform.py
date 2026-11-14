"""Lifecycle failures, admission, release validation and boot-argument safety."""
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'build_files/ai-platform'


def load(name):
    loader = importlib.machinery.SourceFileLoader(name, str(ASSETS / name))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


runtime = load('bluefin-runtime-controller')
platform = load('bluefin-ai-platform')
memory = load('bluefin-ai-memory')


def configuration():
    config = json.loads((ASSETS / 'platform.json').read_text())
    config['enabled'] = True
    image = 'registry.example.test/test@sha256:' + 'a' * 64
    config['images'] = dict.fromkeys(('bifrost', 'webui', 'edge'), image)
    config['profiles'] = {
        name: dict(engine='llamacpp', image=image, backend_model=name, resident_gib=8,
                   accelerator='vulkan', requires_key=False, port=18110 + n,
                   command=['--model', '/models/test.gguf', '--port', '8080'],
                   artifacts=[dict(name='test.gguf', sha256='b' * 64, bytes=4)])
        for n, name in enumerate(('small', 'large'))}
    config['aliases'] = dict(general='small', coding='large')
    return config


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.controller = runtime.Controller(configuration(), manage=lambda *a: self.events.append(a),
                                             memory=lambda: 120 * runtime.GIB, health=lambda p: True)

    def test_automatic_activation_and_alias_translation(self):
        with self.controller.lease('general') as p:
            self.assertEqual(p['backend_model'], 'small')
            self.assertEqual(self.controller.inflight, 1)
        self.assertEqual(self.controller.inflight, 0)
        self.assertEqual(self.controller.current, 'small')

    def test_switch_waits_for_stream_lease(self):
        lease = self.controller.lease('general')
        lease.__enter__()
        errors = []

        def switch():
            try:
                self.controller.control('pin', 'large')
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=switch)
        worker.start()
        deadline = time.monotonic() + 2
        while not self.controller.switching and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertEqual(self.controller.state, 'draining')
        self.assertNotIn(('stop', 'bluefin-runtime-small.service'), self.events)
        with self.assertRaises(runtime.Rejected) as rejected:
            with self.controller.lease('general'):
                pass
        self.assertEqual(rejected.exception.code, 503)
        with self.assertRaises(runtime.Rejected):
            self.controller.control('auto')
        lease.__exit__(None, None, None)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.assertEqual(self.controller.current, 'large')
        self.assertLess(self.events.index(('stop', 'bluefin-runtime-small.service')),
                        self.events.index(('start', 'bluefin-runtime-large.service')))

    def test_drain_timeout_retains_old_engine(self):
        self.controller.config['drain_seconds'] = .01
        with self.controller.lease('general'):
            with self.assertRaises(runtime.Rejected) as rejected:
                self.controller.control('pin', 'large')
            self.assertEqual(rejected.exception.code, 409)
        self.assertEqual(self.controller.current, 'small')
        self.assertNotIn(('stop', 'bluefin-runtime-small.service'), self.events)

    def test_start_failure_restores_previous_engine_and_cools_down(self):
        with self.controller.lease('general'):
            pass

        def manager(*args):
            self.events.append(args)
            if args == ('start', 'bluefin-runtime-large.service'):
                raise OSError('candidate failure')

        self.controller.manage = manager
        with self.assertRaises(OSError):
            self.controller.control('pin', 'large')
        self.assertEqual(self.controller.current, 'small')
        self.assertIn(('stop', 'bluefin-runtime-large.service'), self.events)
        self.assertGreater(self.controller.cooldown['large'], time.monotonic())

    def test_cleanup_failure_prevents_second_runtime(self):
        with self.controller.lease('general'):
            pass

        def manager(*args):
            self.events.append(args)
            if args[0] == 'stop':
                raise OSError('could not stop')

        self.controller.manage = manager
        with self.assertRaises(OSError):
            self.controller.control('pin', 'large')
        self.assertTrue(self.controller.fault)
        with self.assertRaises(runtime.Rejected):
            with self.controller.lease('general'):
                pass
        self.assertNotIn(('start', 'bluefin-runtime-large.service'), self.events)

    def test_failed_restore_is_cleaned_up_before_new_admission(self):
        with self.controller.lease('general'):
            pass

        def manager(*args):
            self.events.append(args)
            if args[0] == 'start':
                raise OSError('candidate and restore failure')

        self.controller.manage = manager
        with self.assertRaises(OSError):
            self.controller.control('pin', 'large')
        self.assertIsNone(self.controller.current)
        self.assertEqual(self.events[-1], ('stop', 'bluefin-runtime-small.service'))
        self.assertIn('small', self.controller.cooldown)

    def test_memory_failure_cannot_start_runtime(self):
        self.controller.memory = lambda: 20 * runtime.GIB
        with self.assertRaises(runtime.Rejected) as rejected:
            self.controller.control('pin', 'large')
        self.assertEqual(rejected.exception.code, 507)
        self.assertNotIn(('start', 'bluefin-runtime-large.service'), self.events)

    def test_pin_then_auto(self):
        self.controller.control('pin', 'small')
        with self.assertRaises(runtime.Rejected) as rejected:
            with self.controller.lease('coding'):
                pass
        self.assertEqual(rejected.exception.code, 409)
        self.controller.control('auto')
        with self.controller.lease('coding'):
            pass
        self.assertEqual(self.controller.current, 'large')

    def test_admission_limit_and_disconnect_cleanup(self):
        self.controller.config['max_inflight'] = 1
        with self.controller.lease('general'):
            with self.assertRaises(runtime.Rejected) as rejected:
                with self.controller.lease('general'):
                    pass
            self.assertEqual(rejected.exception.code, 429)
        with self.assertRaises(BrokenPipeError):
            with self.controller.lease('general'):
                raise BrokenPipeError()
        self.assertEqual(self.controller.inflight, 0)

    def test_reconcile_stops_all_managed_units(self):
        self.controller.reconcile()
        self.assertEqual(self.events, [('stop', 'bluefin-runtime-small.service'),
                                       ('stop', 'bluefin-runtime-large.service')])


class ReleaseTest(unittest.TestCase):
    def test_shipped_release_is_disabled(self):
        self.assertFalse(platform.validate(json.loads((ASSETS / 'platform.json').read_text()))['enabled'])

    def test_enabled_release_requires_pins_models_and_safe_strings(self):
        config = configuration()
        self.assertIs(platform.validate(config), config)
        for update in ('mutable_image', 'bad_alias', 'port_collision', 'bad_path', 'specifier'):
            candidate = copy.deepcopy(config)
            if update == 'mutable_image':
                candidate['images']['bifrost'] = 'docker.io/maximhq/bifrost:latest'
            elif update == 'bad_alias':
                candidate['aliases']['general'] = 'missing'
            elif update == 'port_collision':
                candidate['profiles']['large']['port'] = 18110
            elif update == 'bad_path':
                candidate['profiles']['small']['artifacts'][0]['name'] = '../secret'
            else:
                candidate['profiles']['small']['command'].append('%h')
            with self.subTest(update=update), self.assertRaises(ValueError):
                platform.validate(candidate)

    def test_runtime_quadlets_have_no_autostart_or_restart(self):
        profile = configuration()['profiles']['small']
        text = platform.runtime_quadlet('small', profile)
        self.assertNotIn('[Install]', text)
        self.assertIn('Restart=no', text)
        self.assertIn('PublishPort=127.0.0.1:18110:8080', text)
        self.assertNotIn('PartOf=', text)

    def test_disabled_provision_performs_no_host_actions(self):
        with patch.object(platform, 'run') as run:
            platform.provision({'enabled': False})
        run.assert_not_called()

    def test_preseeded_artifacts_are_verified_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory)
            (models / 'test.gguf').write_bytes(b'test')
            artifact = dict(name='test.gguf', bytes=4, sha256=hashlib.sha256(b'test').hexdigest())
            platform.model_artifact(models, artifact)
            artifact['sha256'] = 'a' * 64
            with self.assertRaises(ValueError):
                platform.model_artifact(models, artifact)
            self.assertEqual((models / 'test.gguf').read_bytes(), b'test')


class MemoryTest(unittest.TestCase):
    def test_exact_110_gib_and_replacement(self):
        changes, target = memory.plan(['quiet', 'ttm.pages_limit=123', 'foo=bar'], 'ttm', 4096)
        self.assertEqual(target, 'ttm.pages_limit=28835840')
        self.assertEqual(changes, ['--delete=ttm.pages_limit=123', '--append=' + target])
        self.assertEqual(28835840 * 4096, 110 * 1024 ** 3)

    def test_idempotence_and_module_migration(self):
        target = 'ttm.pages_limit=28835840'
        self.assertEqual(memory.plan([target], 'ttm', 4096)[0], [])
        self.assertEqual(memory.plan(['amdttm.pages_limit=123', target], 'ttm', 4096)[0],
                         ['--delete=amdttm.pages_limit=123', '--delete=' + target, '--append=' + target])

    def test_refuses_unknown_page_size_or_conflicting_tuning(self):
        for args, size in (([], 65536), (['amdgpu.gttsize=65536'], 4096),
                           (['ttm.page_pool_size=28835840'], 4096)):
            with self.subTest(args=args, size=size), self.assertRaises(ValueError):
                memory.plan(args, 'ttm', size)


if __name__ == '__main__':
    unittest.main()
