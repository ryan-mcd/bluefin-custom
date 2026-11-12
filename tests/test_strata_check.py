"""Readiness requires the adapter and an actual model list."""
import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'build_files' / 'bluefin-strata-check'
loader = importlib.machinery.SourceFileLoader('strata_check', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
check = importlib.util.module_from_spec(spec)
loader.exec_module(check)


class Response:
    status = 200

    def __init__(self, marker='1', data=None):
        self.marker = marker
        self.data = {'data': [{'id': 'qwen3.8-flash-next-iq3_xxs'}]} if data is None else data

    def getheader(self, name):
        return self.marker if name == 'X-Bluefin-Strata-Adapter' else None

    def read(self, _limit):
        return json.dumps(self.data).encode()


class ReadinessTests(unittest.TestCase):
    def test_actual_model_id_is_reported(self):
        with patch.object(check.http.client, 'HTTPConnection') as connection:
            connection.return_value.getresponse.return_value = Response()
            self.assertEqual(check.model_ids('8080'), ['qwen3.8-flash-next-iq3_xxs'])

    def test_other_listener_and_missing_ids_are_not_ready(self):
        for response in (Response(marker=None), Response(data={'data': []}),
                         Response(data={'data': [{'id': ''}]})):
            with self.subTest(response=response), patch.object(check.http.client, 'HTTPConnection') as connection:
                connection.return_value.getresponse.return_value = response
                with self.assertRaises(ValueError):
                    check.model_ids('8080')


if __name__ == '__main__':
    unittest.main()
