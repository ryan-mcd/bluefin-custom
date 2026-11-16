"""Bot updates must change the images actually used by deployment."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from test_ai_platform import ASSETS, configuration, platform


class ImageCatalogTests(unittest.TestCase):
    def test_catalog_has_pinned_images_for_all_components(self):
        catalog = platform.image_catalog(ASSETS / 'Dockerfile')
        self.assertEqual(set(catalog), platform.COMPONENTS)
        for image in catalog.values():
            platform.digest(image)
            self.assertIn('@sha256:', image)
            self.assertIn(':', image.split('@')[0].rsplit('/', 1)[1])

    def test_default_config_resolves_to_managed_images_without_mutation(self):
        config = json.loads((ASSETS / 'platform.json').read_text())
        original = copy.deepcopy(config)
        resolved = platform.resolve_images(config, ASSETS / 'Dockerfile')
        catalog = platform.image_catalog(ASSETS / 'Dockerfile')
        self.assertEqual(resolved['images'], {name: platform.podman_reference(catalog[name])
                                             for name in ('bifrost', 'webui', 'edge')})
        self.assertEqual(config, original)

    def test_bot_digest_update_changes_resolved_runtime(self):
        config = configuration()
        config['profiles']['small']['image'] = 'image:llamacpp'
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'Dockerfile'
            original = platform.image_catalog(ASSETS / 'Dockerfile')['llamacpp']
            updated = original.split('@')[0] + '@sha256:' + 'b' * 64
            manifest.write_text((ASSETS / 'Dockerfile').read_text().replace(original, updated))
            resolved = platform.resolve_images(config, manifest)
        self.assertEqual(resolved['profiles']['small']['image'], platform.podman_reference(updated))
        self.assertEqual(resolved['profiles']['large']['image'], config['profiles']['large']['image'])

    def test_source_runtime_choices_reference_managed_images_without_model_presets(self):
        config = json.loads((ASSETS / 'platform.json').read_text())
        resolved = platform.resolve_images(config, ASSETS / 'Dockerfile')
        examples = json.loads((ASSETS / 'profiles.example.json').read_text())
        self.assertEqual(set(resolved['runtime_images']), set(examples['runtime_choices']) - {'auto'})
        self.assertEqual(config['profiles'], {})
        for image in resolved['runtime_images'].values():
            platform.digest(image)

    def test_unknown_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown image catalog component'):
            platform.resolve_images({'images': {'webui': 'image:unknown'}}, ASSETS / 'Dockerfile')

    def test_runtime_references_drop_tags_but_preserve_registry_ports_and_digest(self):
        image = 'registry.example.test:5000/team/runtime:1.2.3@sha256:' + 'c' * 64
        expected = 'registry.example.test:5000/team/runtime@sha256:' + 'c' * 64
        resolved = platform.resolve_images({'images': {'webui': image}})
        self.assertEqual(resolved['images']['webui'], expected)
        self.assertEqual(platform.podman_reference(expected), expected)

    def test_unpinned_duplicate_or_executable_manifest_is_rejected(self):
        source = (ASSETS / 'Dockerfile').read_text()
        first = next(line for line in source.splitlines() if line.startswith('FROM '))
        for bad in (source + '\nRUN echo unexpected\n', source + '\n' + first + '\n',
                    source.replace(first, 'FROM nginx:latest AS bifrost')):
            with self.subTest(manifest=bad), tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / 'Dockerfile'
                manifest.write_text(bad)
                with self.assertRaises(ValueError):
                    platform.image_catalog(manifest)


if __name__ == '__main__':
    unittest.main()
