"""Test the local adapter without opening sockets or requiring a GPU."""
import argparse
import io
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'build_files' / 'bluefin-strata-proxy'
loader = importlib.machinery.SourceFileLoader('strata_proxy', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
proxy = importlib.util.module_from_spec(spec)
loader.exec_module(proxy)


class Response:
    status = 200

    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def getheaders(self):
        return [('Content-Type', 'text/event-stream'), ('Transfer-Encoding', 'chunked'),
                ('Set-Cookie', 'secret=do-not-forward'), ('Connection', 'keep-alive')]

    def read1(self, _size):
        return next(self.chunks, b'')


class Request:
    def __init__(self, raw):
        self.input = io.BytesIO(raw)
        self.output = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        return self.input if 'r' in mode else self.output

    def sendall(self, data):
        self.output.write(data)


class AdapterTests(unittest.TestCase):
    def test_key_requires_private_regular_file_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            key.write_text('a' * 64 + '\n')
            key.chmod(0o600)
            self.assertEqual(proxy.read_key(key), 'a' * 64)
            key.chmod(0o644)
            with self.assertRaises(ValueError):
                proxy.read_key(key)
            key.chmod(0o600)
            alias = Path(directory) / 'alias'
            alias.symlink_to(key)
            with self.assertRaises(OSError):
                proxy.read_key(alias)

    def test_stream_and_tool_payload_pass_through_with_private_auth(self):
        body = b'{"model":"actual-id","stream":true,"tools":[{"type":"function"}]}'
        chunks = [b'data: {"choices":[{"delta":{"tool_calls":[',
                  b'{"index":0}]}}]}\n\n', b'data: [DONE]\n\n']
        request = Request(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost:8080\r\n'
                          b'Authorization: Bearer client-key\r\nContent-Type: application/json\r\n'
                          + b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        server = SimpleNamespace(upstream_port=18080, backend_key='a' * 64, server_port=8080)
        with patch.object(proxy.http.client, 'HTTPConnection') as connection:
            connection.return_value.getresponse.return_value = Response(chunks)
            proxy.Handler(request, ('127.0.0.1', 1234), server)
        kwargs = connection.return_value.request.call_args.kwargs
        self.assertEqual(kwargs['body'], body)
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer ' + 'a' * 64)
        result = request.output.getvalue()
        self.assertIn(b'200 OK', result)
        self.assertIn(b'X-Bluefin-Strata-Adapter: 1', result)
        self.assertNotIn(b'Transfer-Encoding:', result)
        self.assertNotIn(b'Set-Cookie:', result)
        self.assertTrue(result.endswith(b''.join(chunks)))

    def test_rejects_ambiguous_framing_without_upstream_request(self):
        request = Request(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost:8080\r\n'
                          b'Transfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n')
        server = SimpleNamespace(upstream_port=18080, backend_key='a' * 64, server_port=8080)
        with patch.object(proxy.http.client, 'HTTPConnection') as connection:
            proxy.Handler(request, ('127.0.0.1', 1234), server)
        connection.assert_not_called()
        self.assertIn(b'400', request.output.getvalue())

    def test_chunked_request_is_decoded_and_forwarded_with_a_length(self):
        request = Request(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost:8080\r\n'
                          b'Transfer-Encoding: chunked\r\nContent-Type: application/json\r\n\r\n'
                          b'4\r\n{"a"\r\n3\r\n:1}\r\n0\r\n\r\n')
        server = SimpleNamespace(upstream_port=18080, backend_key='a' * 64, server_port=8080)
        with patch.object(proxy.http.client, 'HTTPConnection') as connection:
            connection.return_value.getresponse.return_value = Response([b'ok'])
            proxy.Handler(request, ('127.0.0.1', 1234), server)
        kwargs = connection.return_value.request.call_args.kwargs
        self.assertEqual(kwargs['body'], b'{"a":1}')
        self.assertEqual(kwargs['headers']['Content-Length'], '7')
        self.assertNotIn('Transfer-Encoding', kwargs['headers'])

    def test_chunked_request_rejects_excess_size_and_trailers(self):
        for body, status in ((b'1000001\r\n', b'413'),
                             (b'0\r\nX-Ignored: value\r\n\r\n', b'400'),
                             (b'5\r\nabc\r\n', b'400')):
            with self.subTest(body=body), patch.object(proxy.http.client, 'HTTPConnection') as connection:
                request = Request(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost:8080\r\n'
                                  b'Transfer-Encoding: chunked\r\n\r\n' + body)
                server = SimpleNamespace(upstream_port=18080, backend_key='a' * 64, server_port=8080)
                proxy.Handler(request, ('127.0.0.1', 1234), server)
                connection.assert_not_called()
                self.assertIn(status, request.output.getvalue().split(b'\r\n', 1)[0])

    def test_rejects_cross_origin_and_dns_rebinding(self):
        server = SimpleNamespace(upstream_port=18080, backend_key='a' * 64, server_port=8080)
        for headers in (b'Host: evil.example:8080\r\n',
                        b'Host: localhost:8080\r\nOrigin: https://evil.example\r\n'):
            with self.subTest(headers=headers), patch.object(proxy.http.client, 'HTTPConnection') as connection:
                request = Request(b'GET /v1/models HTTP/1.1\r\n' + headers + b'\r\n')
                proxy.Handler(request, ('127.0.0.1', 1234), server)
                connection.assert_not_called()
                self.assertIn(b'403', request.output.getvalue())

    def test_ports_are_canonical_and_bounded(self):
        self.assertEqual(proxy.port('8080'), 8080)
        for value in ('0', '08080', '65536', '1;id'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                proxy.port(value)


if __name__ == '__main__':
    unittest.main()
