"""Explicit estimate overrides survive registration while measured guards remain."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_ai_huggingface import release, source_fixture
from test_ai_platform import platform, runtime
import bluefin_ai_huggingface as hf
from bluefin_ai_models import ModelManager


def large_source():
    source = source_fixture()
    source['artifacts'][0]['bytes'] = 80 * hf.GIB
    source['metadata'].update({'test.block_count': 80, 'test.embedding_length': 4096,
                               'test.attention.head_count': 32, 'test.attention.head_count_kv': 8})
    return source


class ForceMemoryTests(unittest.TestCase):
    def test_override_retains_estimate_and_validation_requires_explicit_boolean(self):
        source = large_source(); config = release()
        with self.assertRaisesRegex(ValueError, r'112 GiB.*--force'):
            hf.make_profile(source, config['runtime_images'], runtime='strata')
        name, profile = hf.make_profile(source, config['runtime_images'], runtime='strata', force=True)
        self.assertEqual(profile['resident_gib'], 112)
        self.assertIs(profile['force_memory'], True)
        self.assertEqual(profile['environment']['CONTEXT'], '65536')
        config['profiles'][name] = profile
        platform.validate(config)
        for value in (False, 'true', 1, None):
            candidate = copy.deepcopy(config); candidate['profiles'][name]['force_memory'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                platform.validate(candidate)

    def test_forced_variant_has_distinct_identity_but_shares_weights_and_runtime_cache(self):
        source = large_source(); images = release()['runtime_images']
        normal, ordinary = hf.make_profile(source, images, context=32768)
        forced, profile = hf.make_profile(source, images, context=32768, force=True)
        self.assertNotEqual(normal, forced)
        self.assertNotIn('force_memory', ordinary)
        self.assertEqual(profile['cache_volume'], ordinary['cache_volume'])
        self.assertEqual(profile['artifacts'], ordinary['artifacts'])

    def test_force_does_not_bypass_context_or_weight_floor_or_invent_missing_geometry(self):
        source = large_source(); images = release()['runtime_images']
        for kwargs, message in [({'context': 262144}, 'exceeds source'),
                                ({'memory_gib': 81}, 'cannot hold'),
                                ({'memory_gib': 0}, 'positive integer')]:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, message):
                hf.make_profile(source, images, force=True, **kwargs)
        source['metadata'] = {}
        with self.assertRaisesRegex(ValueError, 'supply --memory-gib'):
            hf.make_profile(source, images, force=True)
        self.assertEqual(hf.make_profile(source, images, force=True, memory_gib=100)[1]['resident_gib'], 100)

    def test_cli_fetch_persists_force_and_boot_merge_preserves_it(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); base = home/'config'; base.mkdir()
            config = release(); (base/'controller.json').write_text(json.dumps(config))
            argv = ['bluefin-ai-platform', 'models', 'fetch', 'owner/model', '--file', 'weights.gguf',
                    '--runtime', 'strata', '--context', '64k', '--force']
            output = io.StringIO()
            with patch.object(platform, 'BASE', base), patch.object(Path, 'home', return_value=home), patch.object(platform, 'run'):
                with patch.object(platform.sys, 'argv', argv), patch.object(platform, 'resolve_source', return_value=large_source()):
                    with patch.object(platform, 'prepare_models') as prepare, patch.object(ModelManager, 'control') as control:
                        with redirect_stdout(output): platform.main()
            profiles = hf.registry_profiles(base); self.assertEqual(len(profiles), 1)
            name, profile = next(iter(profiles.items()))
            self.assertTrue(profile['force_memory']); self.assertEqual(profile['resident_gib'], 112)
            self.assertTrue(platform.validate(hf.merge_registry(config, base))['profiles'][name]['force_memory'])
            self.assertEqual(json.loads((base/'installed-models.json').read_text())['profiles'], [name])
            prepare.assert_called_once_with([name]); control.assert_called_once_with('reload')
            self.assertIn('Warning: --force', output.getvalue())
            self.assertFalse((base/'selection.json').exists())

    def test_existing_forced_id_variants_retain_override_without_resolving_a_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); base = home/'config'; base.mkdir(); config = release()
            (base/'controller.json').write_text(json.dumps(config))
            name, profile = hf.make_profile(large_source(), config['runtime_images'], runtime='strata', force=True)
            (base/'model-registry.json').write_text(json.dumps(dict(version=1, profiles={name: profile})))
            argv = ['bluefin-ai-platform', 'models', 'fetch', name, '--context', '32k']
            with patch.object(platform, 'BASE', base), patch.object(Path, 'home', return_value=home), patch.object(platform, 'run'):
                with patch.object(platform.sys, 'argv', argv), patch.object(platform, 'resolve_source') as resolve:
                    with patch.object(platform, 'prepare_models'), patch.object(ModelManager, 'control'), redirect_stdout(io.StringIO()):
                        platform.main()
            resolve.assert_not_called()
            variants = hf.registry_profiles(base); self.assertEqual(len(variants), 2)
            self.assertTrue(all(p['force_memory'] for p in variants.values()))
            self.assertEqual({p['context_tokens'] for p in variants.values()}, {32768, 65536})

    def test_cli_plan_force_is_read_only_and_use_pin_can_force_installed_profiles(self):
        for action in ('plan', 'use', 'pin'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                home = Path(directory); base = home/'config'; base.mkdir(); config = release()
                (base/'controller.json').write_text(json.dumps(config))
                name, profile = hf.make_profile(large_source(), config['runtime_images'], runtime='strata', context=32768)
                (base/'model-registry.json').write_text(json.dumps(dict(version=1, profiles={name: profile})))
                argv = ['bluefin-ai-platform', 'models', action, name, '--force']
                output = io.StringIO()
                with patch.object(platform, 'BASE', base), patch.object(Path, 'home', return_value=home), patch.object(platform, 'run'):
                    with patch.object(platform.sys, 'argv', argv), patch.object(platform, 'prepare_models') as prepare:
                        with patch.object(ModelManager, 'control', return_value={}) as control, redirect_stdout(output):
                            platform.main()
                self.assertIn('Warning: --force', output.getvalue())
                profiles = hf.registry_profiles(base)
                if action == 'plan':
                    self.assertEqual(profiles, {name: profile}); prepare.assert_not_called(); control.assert_not_called()
                else:
                    self.assertEqual(len(profiles), 2)
                    forced = next(n for n, p in profiles.items() if p.get('force_memory'))
                    prepare.assert_called_once_with([forced])
                    self.assertEqual(control.call_args_list[-1].args, ('select' if action == 'use' else 'select-pin', forced))

    def controller(self, force=True, memory=120):
        config = release()
        name, profile = hf.make_profile(large_source(), config['runtime_images'], runtime='strata', force=True)
        if not force: profile.pop('force_memory')
        config['profiles'] = {name: profile}; config['aliases']['general'] = name
        events = []
        return runtime.Controller(config, manage=lambda *a: events.append(a), health=lambda p: True,
                                  memory=lambda: memory*hf.GIB, preflight=lambda p: None), events

    def test_activation_overrides_projection_but_retains_weight_and_reserve_floor(self):
        normal, events = self.controller(force=False)
        with self.assertRaises(runtime.Rejected) as error:
            with normal.lease('general'): pass
        self.assertEqual(error.exception.code, 507)
        self.assertFalse(any(e[0] == 'start' for e in events))
        forced, events = self.controller()
        with forced.lease('general'): self.assertEqual(forced.inflight, 1)
        self.assertTrue(any(e[0] == 'start' for e in events))
        low, events = self.controller(memory=97)
        with self.assertRaisesRegex(runtime.Rejected, 'OS reserve'):
            with low.lease('general'): pass
        self.assertFalse(any(e[0] == 'start' for e in events))

    def test_force_still_enforces_measured_reserve_after_loading(self):
        controller, events = self.controller()
        controller.memory = unittest.mock.Mock(side_effect=[120*hf.GIB, 15*hf.GIB])
        with self.assertRaisesRegex(runtime.Rejected, 'Host memory reserve reached'):
            with controller.lease('general'): pass
        self.assertTrue(any(e[0] == 'start' for e in events))
        self.assertEqual(controller.inflight, 0)

    def test_force_does_not_bypass_artifact_preflight(self):
        controller, events = self.controller(); controller.preflight = runtime.runtime_preflight
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, 'home', return_value=Path(directory)):
            with self.assertRaisesRegex(runtime.Rejected, 'preparation is pending'):
                with controller.lease('general'): pass
        self.assertFalse(any(e[0] == 'start' for e in events))


if __name__ == '__main__': unittest.main()
