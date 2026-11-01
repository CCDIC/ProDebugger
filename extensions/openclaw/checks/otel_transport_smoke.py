"""Transport-only synthetic smoke using an installed official JS OTLP exporter.

This makes no model/agent calls. Its spans must never enter real-run evaluation.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading

from prodebugger.otel_receiver import TraceHandler, make_server


NODE_SCRIPT = r'''
const path = require("node:path");
const [plugin, endpoint, compression] = process.argv.slice(1);
const { OTLPTraceExporter } = require(path.join(plugin, "node_modules/@opentelemetry/exporter-trace-otlp-proto"));
const { BasicTracerProvider } = require(path.join(plugin, "node_modules/@opentelemetry/sdk-trace-base"));
(async () => {
  const exporter = new OTLPTraceExporter({url: endpoint, compression});
  const provider = new BasicTracerProvider();
  const span = provider.getTracer("prodebugger-transport-only-synthetic").startSpan("transport-only synthetic");
  span.setAttribute("data_origin", "transport_only_synthetic");
  span.setAttribute("not_an_agent_run", true);
  span.end();
  const result = await new Promise(resolve => exporter.export([span], resolve));
  console.log(JSON.stringify({export_code: result.code}));
  await exporter.shutdown();
  await provider.shutdown();
})().catch(error => { console.log(JSON.stringify({error_type: error.name})); process.exitCode = 1; });
'''


class LegacyFramingHandler(BaseHTTPRequestHandler):
    """Reproduce the former receiver's transfer-encoding rejection only."""
    def log_message(self, *args):
        pass

    def do_POST(self):
        status = 411 if self.headers.get("Transfer-Encoding") else 400
        self.server.statuses.append(status)
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True


class ObservedTraceHandler(TraceHandler):
    def _reply(self, status, *args, **kwargs):
        self.server.statuses.append(status)
        return super()._reply(status, *args, **kwargs)


def run(node, plugin, output):
    node, plugin, output = Path(node).resolve(), Path(plugin).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    package = json.loads((plugin / "package.json").read_text(encoding="utf-8"))
    if package.get("name") != "@openclaw/diagnostics-otel":
        raise ValueError("expected an installed official diagnostics-otel package directory")
    plan = {"schema": "otlp-transport-smoke/v1", "data_origin": "transport_only_synthetic",
            "not_an_agent_run": True, "plugin_version": package["version"], "plugin_path": str(plugin),
            "plugin_package_sha256": hashlib.sha256((plugin / "package.json").read_bytes()).hexdigest(),
            "note": "Baseline reproduces the old transfer-encoding rejection; capture contains synthetic exporter probes only."}
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    legacy = ThreadingHTTPServer(("127.0.0.1", 0), LegacyFramingHandler)
    current = make_server(output / "synthetic-capture", port=0)
    current.RequestHandlerClass = ObservedTraceHandler
    threads = []
    for server in (legacy, current):
        server.daemon_threads = True
        server.statuses = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("OTEL_")}
    env["NODE_DISABLE_COMPILE_CACHE"] = "1"
    cases = []
    try:
        for name, server, compression, expected_status, expected_export in (
                ("legacy_chunked", legacy, "none", 411, 1),
                ("fixed_chunked", current, "none", 200, 0),
                ("fixed_gzip_chunked", current, "gzip", 200, 0)):
            before = len(server.statuses)
            process = subprocess.run([str(node), "-e", NODE_SCRIPT, str(plugin),
                f"http://127.0.0.1:{server.server_address[1]}/v1/traces", compression],
                capture_output=True, env=env, timeout=40)
            (output / (name + ".stdout.json")).write_bytes(process.stdout)
            (output / (name + ".stderr.txt")).write_bytes(process.stderr)
            result = json.loads(process.stdout.decode("utf-8").strip())
            statuses = server.statuses[before:]
            cases.append({"id": name, "compression": compression, "http_statuses": statuses,
                "export_code": result.get("export_code"), "exit_code": process.returncode,
                "passed": process.returncode == 0 and statuses == [expected_status]
                          and result.get("export_code") == expected_export})
    finally:
        for server in (legacy, current):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(2)
    result = {**plan, "cases": cases, "passed": len(cases) == 3 and all(case["passed"] for case in cases)}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = run(args.node, args.plugin, args.output_dir)
    print(json.dumps({"passed": result["passed"], "cases": result["cases"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
