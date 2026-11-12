"""Dashboard backend validation without opening a listener."""
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'build_files' / 'bluefin-ai-dashboard'
loader = importlib.machinery.SourceFileLoader('strata_dashboard', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
dashboard = importlib.util.module_from_spec(spec)
loader.exec_module(dashboard)


class DashboardModelTests(unittest.TestCase):
    @patch.object(dashboard, 'run', return_value=(0, 'model-only'))
    def test_strata_choice_is_explicit_and_bounded(self, _run):
        self.assertEqual(dashboard.command_for({'action': 'model', 'model': 'IQ3_XXS',
                                                'context': '64000', 'backend': 'strata'}),
                         ['ujust', 'ai-model-select', 'IQ3_XXS', '64000', 'strata'])
        for model, backend in (('llama3.2', 'strata'), ('IQ3_XXS', 'strata;id'),
                               ('-foo', 'ramalama')):
            with self.subTest(model=model, backend=backend), self.assertRaises(ValueError):
                dashboard.command_for({'action': 'model', 'model': model,
                                       'context': '64000', 'backend': backend})


if __name__ == '__main__':
    unittest.main()
