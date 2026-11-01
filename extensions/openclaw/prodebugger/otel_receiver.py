"""Local-only OTLP/HTTP trace capture; no forwarding or credentials are stored.

Install requirements-otel.txt, then run:
    python -B -m prodebugger.otel_receiver --output-dir artifacts/capture --port 4318
Set OpenClaw's traces endpoint to http://127.0.0.1:4318/v1/traces.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import threading


DEFAULT_MAX_BYTES = 16 * 1024 * 1024
MAX_CHUNK_LINE_BYTES = 8192
MAX_CHUNK_FRAMING_BYTES = 64 * 1024
HTTP_TOKEN = rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
CHUNK_LINE = re.compile(rb"([0-9A-Fa-f]+)(?:;" + HTTP_TOKEN +
                        rb"(?:=(?:" + HTTP_TOKEN + rb'|"(?:[\t !#-\[\]-~]|\\[\t -~])*"))?)*')
TRAILER_LINE = re.compile(HTTP_TOKEN + rb":[\t \x21-\x7e\x80-\xff]*")


class RequestBodyError(ValueError):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def decode_trace_request(payload):
    """Convert protobuf to standard OTLP JSON, including hexadecimal IDs.

    MessageToDict follows generic protobuf JSON and base64-encodes bytes;
    OTLP's JSON mapping instead requires hexadecimal trace/span IDs. Other
    bytes attributes keep their normal base64 representation.
    """
    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    except ImportError as exc:
        raise RuntimeError("Install requirements-otel.txt to decode OTLP protobuf") from exc
    request = ExportTraceServiceRequest()
    request.ParseFromString(payload)
    result = MessageToDict(request)

    def normalize(value):
        if isinstance(value, list):
            for item in value:
                normalize(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {"traceId", "spanId", "parentSpanId"} and isinstance(item, str):
                    value[key] = base64.b64decode(item, validate=True).hex()
                else:
                    normalize(item)

    normalize(result)
    return result


class TraceCapture:
    def __init__(self, output_dir, max_bytes=DEFAULT_MAX_BYTES):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.rejections = {}
        self.sequence = max((int(path.stem.split("-")[-1])
                             for path in self.output_dir.glob("request-*.protobuf")
                             if path.stem.split("-")[-1].isdigit()), default=0)

    def reject(self, status, reason):
        """Record only fixed status/reason counters, never request-derived data."""
        with self.lock:
            key = (status, reason)
            self.rejections[key] = self.rejections.get(key, 0) + 1
            summary = {"schema": "otlp-rejection-counts/v1", "counts": [
                {"status": code, "reason": label, "count": count}
                for (code, label), count in sorted(self.rejections.items())]}
            temporary = self.output_dir / "rejections.json.tmp"
            temporary.write_text(json.dumps(summary, separators=(",", ":")) + "\n", encoding="utf-8")
            temporary.replace(self.output_dir / "rejections.json")

    def save(self, payload, decoded, *, wire_size, compressed):
        encoded = json.dumps(decoded, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        spans = [span for resource in decoded.get("resourceSpans", [])
                 for scope in resource.get("scopeSpans", []) for span in scope.get("spans", [])]
        with self.lock:
            self.sequence += 1
            name = f"request-{self.sequence:06d}"
            # Exclusive creation prevents an existing capture being overwritten.
            with (self.output_dir / (name + ".protobuf")).open("xb") as handle:
                handle.write(payload)
            with (self.output_dir / (name + ".json")).open("x", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
            with (self.output_dir / "traces.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
            manifest = {"sequence": self.sequence, "received_at": datetime.now(timezone.utc).isoformat(),
                        "protobuf_file": name + ".protobuf", "json_file": name + ".json",
                        "wire_bytes": wire_size, "protobuf_bytes": len(payload), "gzip": compressed,
                        "span_count": len(spans), "trace_ids": sorted({s.get("traceId", "") for s in spans})}
            with (self.output_dir / "manifest.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest, ensure_ascii=False, allow_nan=False) + "\n")
        return manifest


class TraceHandler(BaseHTTPRequestHandler):
    server_version = "ProDebuggerOTLP/1"

    def log_message(self, format, *args):
        # BaseHTTPRequestHandler logs URL/header-derived data. Capture records
        # deliberately contain telemetry bodies only, never request headers.
        pass

    def _reply(self, status, payload=b"", content_type="application/x-protobuf"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, b'{"status":"ready"}', "application/json")
        else:
            self._reply(404)

    def _reject(self, status, reason):
        try:
            self.server.capture.reject(status, reason)
        except OSError:
            pass
        self._reply(status, reason.encode("ascii"), "text/plain")

    def _read_exact(self, length):
        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestBodyError(400, "incomplete_body")
        return data

    def _read_chunked(self, maximum):
        body, framing = bytearray(), 0

        def line():
            nonlocal framing
            value = self.rfile.readline(MAX_CHUNK_LINE_BYTES + 1)
            framing += len(value)
            if len(value) > MAX_CHUNK_LINE_BYTES or framing > MAX_CHUNK_FRAMING_BYTES:
                raise RequestBodyError(413, "chunk_framing_too_large")
            if not value.endswith(b"\r\n"):
                raise RequestBodyError(400, "invalid_chunk_framing")
            return value[:-2]

        while True:
            match = CHUNK_LINE.fullmatch(line())
            if not match:
                raise RequestBodyError(400, "invalid_chunk_size")
            length = int(match[1], 16)
            if length > maximum - len(body):
                raise RequestBodyError(413, "trace_request_too_large")
            if length == 0:
                while True:
                    trailer = line()
                    if not trailer:
                        return bytes(body)
                    if (not TRAILER_LINE.fullmatch(trailer)
                            or trailer.split(b":", 1)[0].lower() in {
                                b"content-length", b"transfer-encoding", b"content-encoding", b"host"}):
                        raise RequestBodyError(400, "invalid_chunk_trailer")
            body.extend(self._read_exact(length))
            if self._read_exact(2) != b"\r\n":
                raise RequestBodyError(400, "invalid_chunk_framing")
            framing += 2
            if framing > MAX_CHUNK_FRAMING_BYTES:
                raise RequestBodyError(413, "chunk_framing_too_large")

    def _read_body(self, maximum):
        lengths = self.headers.get_all("Content-Length", [])
        transfers = self.headers.get_all("Transfer-Encoding", [])
        if len(lengths) > 1 or (lengths and transfers):
            raise RequestBodyError(400, "ambiguous_body_framing")
        if transfers:
            if len(transfers) != 1 or transfers[0].strip().lower() != "chunked":
                raise RequestBodyError(400, "unsupported_transfer_encoding")
            return self._read_chunked(maximum)
        if not lengths:
            raise RequestBodyError(411, "content_length_or_chunked_required")
        value = lengths[0].strip()
        if not re.fullmatch(r"[0-9]+", value):
            raise RequestBodyError(400, "invalid_content_length")
        length = int(value)
        if length > maximum:
            raise RequestBodyError(413, "trace_request_too_large")
        return self._read_exact(length)

    def do_POST(self):
        if self.path != "/v1/traces":
            self._reply(404)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"application/x-protobuf", "application/protobuf"}:
            self._reject(415, "otlp_protobuf_required")
            return
        encoding = self.headers.get("Content-Encoding", "identity").strip().lower()
        if encoding not in {"identity", "gzip"}:
            self._reject(415, "unsupported_content_encoding")
            return
        capture = self.server.capture
        self.connection.settimeout(10)
        try:
            wire = self._read_body(capture.max_bytes)
            if encoding == "gzip":
                with gzip.GzipFile(fileobj=io.BytesIO(wire)) as handle:
                    payload = handle.read(capture.max_bytes + 1)
            else:
                payload = wire
            if len(payload) > capture.max_bytes:
                raise RequestBodyError(413, "decompressed_trace_request_too_large")
            decoded = decode_trace_request(payload)
        except RequestBodyError as exc:
            self._reject(exc.status, exc.reason)
            return
        except Exception:
            self._reject(400, "invalid_otlp_trace_request")
            return
        try:
            capture.save(payload, decoded, wire_size=len(wire), compressed=encoding == "gzip")
        except OSError:
            self._reject(500, "unable_to_persist_trace_request")
            return
        # An empty ExportTraceServiceResponse is a valid success protobuf.
        self._reply(200)


def make_server(output_dir, port=4318, max_bytes=DEFAULT_MAX_BYTES):
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    # Fail before accepting traffic if the optional decoder is unavailable.
    decode_trace_request(b"")
    server = ThreadingHTTPServer(("127.0.0.1", port), TraceHandler)
    server.daemon_threads = True
    server.capture = TraceCapture(output_dir, max_bytes)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv)
    try:
        server = make_server(args.output_dir, args.port, args.max_bytes)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({"endpoint": f"http://127.0.0.1:{server.server_address[1]}/v1/traces",
                      "output_dir": str(args.output_dir), "max_bytes": args.max_bytes}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
