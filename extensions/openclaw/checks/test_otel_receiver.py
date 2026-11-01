"""Offline HTTP receiver checks with genuine OTLP protobuf encoding."""
import gzip
import http.client
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from prodebugger.otel_import import load_export, spans_from_export, convert
from prodebugger.otel_receiver import make_server
from checks.test_otel_import import task_spec


try:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
except ImportError:
    ExportTraceServiceRequest = None


def protobuf():
    request = ExportTraceServiceRequest()
    scope = request.resource_spans.add().scope_spans.add()
    root = scope.spans.add()
    root.trace_id = bytes.fromhex("ab" * 16)
    root.span_id = bytes.fromhex("01" * 8)
    root.name = "openclaw.run"
    root.start_time_unix_nano, root.end_time_unix_nano = 1000, 2000
    tool = scope.spans.add()
    tool.trace_id = root.trace_id
    tool.span_id = bytes.fromhex("02" * 8)
    tool.parent_span_id = root.span_id
    tool.name = "openclaw.tool.execution"
    tool.start_time_unix_nano, tool.end_time_unix_nano = 1200, 1500
    for key, value in {"gen_ai.tool.name": "read_file",
                       "gen_ai.tool.call.arguments": '{"path":"notes.txt"}',
                       "gen_ai.tool.call.result": '{"text":"expected contents"}'}.items():
        attr = tool.attributes.add()
        attr.key, attr.value.string_value = key, value
    extra = tool.attributes.add()
    extra.key, extra.value.bytes_value = "opaque", b"\x00\x01"
    return request.SerializeToString()


@unittest.skipUnless(ExportTraceServiceRequest, "optional requirements-otel.txt not installed")
class OtelReceiverChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.server = make_server(self.folder, port=0, max_bytes=2048)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.directory.cleanup()

    def post(self, body, *, chunked=False, **headers):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        connection.request("POST", "/v1/traces", body=body,
                           headers={"Content-Type": "application/x-protobuf", **headers}, encode_chunked=chunked)
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, data

    def raw_post(self, body, extra_headers=b"Transfer-Encoding: chunked\r\n"):
        with socket.create_connection(self.server.server_address, timeout=3) as connection:
            connection.sendall(b"POST /v1/traces HTTP/1.1\r\nHost: localhost\r\n"
                               b"Content-Type: application/x-protobuf\r\n" + extra_headers + b"\r\n" + body)
            connection.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(connection)
            response.begin()
            return response.status, response.read()

    def test_gzip_protobuf_is_saved_and_decoded_to_importable_otlp_without_headers(self):
        body = protobuf()
        status, reply = self.post(gzip.compress(body), **{
            "Content-Encoding": "gzip", "Authorization": "Bearer receiver-test-secret",
            "X-API-Key": "receiver-test-api-secret"})
        self.assertEqual((status, reply), (200, b""))
        self.assertEqual((self.folder / "request-000001.protobuf").read_bytes(), body)
        spans = spans_from_export(load_export(self.folder / "traces.jsonl"))
        self.assertEqual(spans[1]["trace_id"], "ab" * 16)
        self.assertEqual(spans[1]["span_id"], "02" * 8)
        self.assertEqual(spans[1]["parent_span_id"], "01" * 8)
        self.assertEqual(spans[1]["attributes"]["opaque"], "AAE=")
        source, audit = convert(spans, task_spec())
        self.assertTrue(audit["ready"])
        self.assertEqual(source["telemetry"]["trace_id"], "ab" * 16)
        for path in self.folder.iterdir():
            self.assertNotIn(b"receiver-test-secret", path.read_bytes())
            self.assertNotIn(b"receiver-test-api-secret", path.read_bytes())
        manifest = json.loads((self.folder / "manifest.jsonl").read_text())
        self.assertTrue(manifest["gzip"])
        self.assertEqual(manifest["span_count"], 2)

    def test_invalid_and_oversized_bodies_never_create_capture_files(self):
        self.assertEqual(self.post(b"invalid-protobuf")[0], 400)
        self.assertEqual(self.post(b"x" * 2049)[0], 413)
        self.assertEqual(self.post(gzip.compress(b"x" * 2049), **{"Content-Encoding": "gzip"})[0], 413)
        self.assertEqual(self.post(b"not gzip", **{"Content-Encoding": "gzip"})[0], 400)
        self.assertEqual(self.post(b"{}", **{"Content-Type": "application/json"})[0], 415)
        self.assertEqual([p.name for p in self.folder.iterdir()], ["rejections.json"])
        counts = json.loads((self.folder / "rejections.json").read_text())["counts"]
        self.assertEqual(sum(row["count"] for row in counts), 5)
        self.assertTrue(all(set(row) == {"status", "reason", "count"} for row in counts))

    def test_actual_chunked_and_gzip_streams_preserve_protobuf_and_hide_headers_and_trailers(self):
        body = protobuf()
        for compressed in (False, True):
            wire = gzip.compress(body) if compressed else body
            headers = {"Content-Encoding": "gzip"} if compressed else {}
            self.assertEqual(self.post(iter([wire[:11], wire[11:]]), chunked=True, **headers), (200, b""))
        framed = (f'{len(body):x};transport="fixture"\r\n'.encode() + body +
                  b"\r\n0\r\nX-Private-Trailer: hidden-trailer-secret\r\n\r\n")
        self.assertEqual(self.raw_post(framed, b"Transfer-Encoding: chunked\r\nAuthorization: hidden-header-secret\r\n"), (200, b""))
        for index in range(1, 4):
            self.assertEqual((self.folder / f"request-{index:06d}.protobuf").read_bytes(), body)
        for path in self.folder.iterdir():
            self.assertNotIn(b"hidden-trailer-secret", path.read_bytes())
            self.assertNotIn(b"hidden-header-secret", path.read_bytes())
        self.assertEqual(len(spans_from_export(load_export(self.folder / "traces.jsonl"))), 2)

    def test_chunked_limits_apply_to_accumulated_and_decompressed_payload(self):
        body = protobuf()
        self.server.capture.max_bytes = len(body)
        self.assertEqual(self.post(iter([body[:7], body[7:]]), chunked=True)[0], 200)
        self.assertEqual(self.post(iter([body, b"x"]), chunked=True)[0], 413)
        self.assertEqual(self.post(iter([gzip.compress(b"x" * (len(body) + 1))]), chunked=True,
                                   **{"Content-Encoding": "gzip"})[0], 413)
        self.assertEqual(len(list(self.folder.glob("request-*.protobuf"))), 1)

    def test_malformed_truncated_or_ambiguous_chunked_requests_are_rejected(self):
        cases = [
            (b"g\r\nx\r\n0\r\n\r\n", b"Transfer-Encoding: chunked\r\n"),
            (b"1\r\nx!\n0\r\n\r\n", b"Transfer-Encoding: chunked\r\n"),
            (b"5\r\nabc", b"Transfer-Encoding: chunked\r\n"),
            (b"1\r\nx\r\n", b"Transfer-Encoding: chunked\r\n"),
            (b"0\r\nMissing-Colon\r\n\r\n", b"Transfer-Encoding: chunked\r\n"),
            (b"0\r\nContent-Length: 5\r\n\r\n", b"Transfer-Encoding: chunked\r\n"),
            (b"0\r\n\r\n", b"Transfer-Encoding: chunked\r\nContent-Length: 0\r\n"),
            (b"", b"Content-Length: 0\r\nContent-Length: 0\r\n"),
            (b"0\r\n\r\n", b"Transfer-Encoding: gzip, chunked\r\n"),
        ]
        for body, headers in cases:
            with self.subTest(body=body, headers=headers):
                self.assertEqual(self.raw_post(body, headers)[0], 400)
        self.assertEqual(list(self.folder.glob("request-*")), [])
        self.assertFalse((self.folder / "traces.jsonl").exists())

    def test_chunk_framing_and_trailers_have_independent_bounds(self):
        self.assertEqual(self.raw_post(b"1;x=" + b"a" * 8192 + b"\r\nx\r\n0\r\n\r\n")[0], 413)
        trailers = b"0\r\n" + (b"X-Padding: " + b"a" * 4000 + b"\r\n") * 17 + b"\r\n"
        self.assertEqual(self.raw_post(trailers)[0], 413)
        self.assertEqual(list(self.folder.glob("request-*")), [])

    def test_requests_have_distinct_files_and_retry_spans_are_deduplicated_on_import(self):
        self.assertEqual(self.post(protobuf())[0], 200)
        self.assertEqual(self.post(protobuf())[0], 200)
        self.assertTrue((self.folder / "request-000001.protobuf").exists())
        self.assertTrue((self.folder / "request-000002.protobuf").exists())
        self.assertEqual(len(spans_from_export(load_export(self.folder / "traces.jsonl"))), 2)
        self.assertEqual(len((self.folder / "manifest.jsonl").read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
