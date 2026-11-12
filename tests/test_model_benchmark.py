"""Test streaming timing and token-usage reporting without a model server."""
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[1] / 'build_files' / 'bluefin-model-benchmark'
loader = importlib.machinery.SourceFileLoader('benchmark', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
benchmark = importlib.util.module_from_spec(spec)
loader.exec_module(benchmark)


class Stream:
    def __init__(self, lines):
        self.lines = lines

    def getheader(self, name):
        return 'text/event-stream' if name == 'Content-Type' else None

    def __iter__(self):
        return iter(self.lines)


class BenchmarkTests(unittest.TestCase):
    def test_stream_uses_reported_tokens_and_output_timing(self):
        stream = Stream([b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
                         b'data: {"choices":[{"delta":{"content":"First"}}]}\n',
                         b'data: {"choices":[{"delta":{"content":" more"}}]}\n',
                         b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20}}\n',
                         b'data: [DONE]\n'])
        clock = iter((1.0, 3.0, 7.0, 8.0)).__next__
        with patch.object(benchmark, 'request', return_value=(Mock(), stream)):
            result = benchmark.measure(8080, 'actual-id', 128, clock=clock)
        self.assertEqual(result['time_to_first_output_s'], 2.0)
        self.assertEqual(result['total_s'], 7.0)
        self.assertEqual(result['approx_output_tokens_per_s'], 5.0)
        self.assertEqual(result['prompt_tokens'], 10)

    def test_missing_usage_is_not_invented_and_incomplete_stream_fails(self):
        output = b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n'
        with patch.object(benchmark, 'request', return_value=(Mock(), Stream([output, b'data: [DONE]\n']))):
            result = benchmark.measure(8080, 'id', 128, clock=iter((1.0, 2.0, 3.0)).__next__)
        self.assertIsNone(result['approx_output_tokens_per_s'])
        with patch.object(benchmark, 'request', return_value=(Mock(), Stream([output]))):
            with self.assertRaises(ValueError):
                benchmark.measure(8080, 'id', 128, clock=iter((1.0, 2.0, 3.0)).__next__)


if __name__ == '__main__':
    unittest.main()
