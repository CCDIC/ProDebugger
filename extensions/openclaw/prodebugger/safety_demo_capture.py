"""Capture real, isolated tool executions for the safety-policy demonstration.

This is custom instrumentation with the official OpenTelemetry SDK. It is not
an OpenClaw agent run and makes no model calls. Only new local fixtures and two
loopback HTTP sinks are used; the destructive command is blocked by a wrapper.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

from . import otel_import
from .otel_receiver import make_server


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dependencies():
    """Use explicit SDK configuration or this extension's optional npm install."""
    node = os.environ.get("PRODEBUGGER_NODE") or shutil.which("node")
    package = os.environ.get("PRODEBUGGER_OTEL_PACKAGE_JSON")
    root = Path(__file__).resolve().parents[1]
    if not package and (root / "node_modules/@opentelemetry/sdk-trace-base/package.json").is_file():
        package = str(root / "package.json")
    if not node or not package or not Path(package).is_file():
        raise RuntimeError("Optional local capture needs Node and this extension's npm install, or explicit PRODEBUGGER_NODE and PRODEBUGGER_OTEL_PACKAGE_JSON.")
    return str(node), str(Path(package).resolve())


def capture_demo(output_dir: Path) -> dict:
    """Create a fresh capture and imported sources, returning its manifest.

    Expected verdicts exist only in the acceptance manifest. They are never
    inserted into the tool spans or the imported sources used for analysis.
    """
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError("Refusing to overwrite an existing capture: " + str(output))
    node, sdk_package = _dependencies()
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "workspace"
    outside = output / "outside-fixture"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "public.txt").write_text("Public demonstration report: revenue 42.\n", encoding="utf-8")
    (workspace / ".env").write_text("DEMO_TOKEN=DEMO_ONLY_NOT_A_SECRET\n", encoding="utf-8")
    (outside / "private.txt").write_text("Outside the allowed demonstration root. Dummy content only.\n", encoding="utf-8")
    fixture_hashes = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in (workspace / "public.txt", workspace / ".env", outside / "private.txt")}
    receiver = make_server(output / "capture", port=0)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_thread.start()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("OTEL_")}
    env["NODE_DISABLE_COMPILE_CACHE"] = "1"
    runtime = Path(__file__).resolve().parent.parent / "runtime_helpers" / "safety_runtime.mjs"
    try:
        process = subprocess.run([node, str(runtime),
            f"http://127.0.0.1:{receiver.server_address[1]}/v1/traces", str(output), sdk_package],
            capture_output=True, env=env, timeout=90)
        (output / "runtime.stdout.txt").write_bytes(process.stdout)
        (output / "runtime.stderr.txt").write_bytes(process.stderr)
    finally:
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(3)
    if process.returncode:
        raise RuntimeError("Isolated tool runtime failed: " + process.stderr.decode("utf-8", errors="replace")[-4000:])
    receipt = json.loads((output / "runtime-receipt.json").read_text(encoding="utf-8"))
    capture_path = output / "capture" / "traces.jsonl"
    spans = otel_import.spans_from_export(otel_import.load_export(capture_path))
    by_identity = {(span["trace_id"], span["span_id"]): span for span in spans}
    expected_identities = {(case["trace_id"], case["span_id"]) for case in receipt["cases"]}
    if len(spans) != 8 or len(expected_identities) != 8 or set(by_identity) != expected_identities:
        raise AssertionError("Received identities do not match the eight executed tool calls")
    if receipt["export_code"] != 0 or receipt["sdk_shutdown_ack"] is not True:
        raise AssertionError("Exporter or SDK shutdown did not acknowledge completion")
    for case in receipt["cases"]:
        attrs = by_identity[(case["trace_id"], case["span_id"])]["attributes"]
        if json.loads(attrs["gen_ai.tool.call.arguments"]) != case["arguments"]:
            raise AssertionError("Received tool arguments differ from actual execution")
        if case["capture_output"]:
            if json.loads(attrs["gen_ai.tool.call.result"]) != case["result"]:
                raise AssertionError("Received result differs from actual execution")
        elif "gen_ai.tool.call.result" in attrs:
            raise AssertionError("Intentionally disabled tool output was unexpectedly present")
    policy = {
        "schema": "prodebugger-safety-policy/v1", "id": "local-safety-demo", "version": "1",
        "path_style": "windows" if os.name == "nt" else "posix",
        "working_directory": str(workspace), "allowed_roots": [str(workspace)],
        "forbidden_paths": [str(workspace / ".env")],
        "allowed_network_hosts": [{"host": "127.0.0.1", "port": receipt["allowed_sink_port"]}],
        "denied_tools": [],
        "denied_commands": [{"id": "recursive-delete", "executable": "rm",
                             "require_any_args": ["-rf", "-fr", "-r", "--recursive"]}],
        "tools": {"read_file": {"kind": "read", "path_arg": "path"},
                  "write_file": {"kind": "write", "path_arg": "path"},
                  "http_request": {"kind": "network", "url_arg": "url"},
                  "exec": {"kind": "command", "command_arg": "command"}},
    }
    policy_path = output / "policy.json"
    _write(policy_path, policy)
    cases = []
    for case in receipt["cases"]:
        task = {"schema": "openclaw-task/v1", "task": case["task"],
            "capture": {"complete": True, "reason":
                "Controlled tool runtime executed exactly one call in this trace; its identity, arguments and enabled result fields match the receiver, all eight expected spans arrived, and exporter/SDK shutdown acknowledged completion. Output capture disabled is separately recorded as a content gap."}}
        source, audit = otel_import.convert(spans, task, trace_id=case["trace_id"], trajectory_id=case["id"])
        source["telemetry"].update({"data_origin": "controlled_tool_runtime",
            "not_openclaw_agent_run": True, "model_calls": 0})
        imported = otel_import.save_import(source, audit, spans, output / "imports")
        _write(imported / "task.json", task)
        cases.append({"id": case["id"], "title": case["title"], "scenario": case["scenario"],
            "source_path": str(imported / "input.json"), "trace_id": case["trace_id"],
            "span_id": case["span_id"], "expected_verdict": case["expected_verdict"],
            "capture_content_complete": audit["coverage"]["content_complete"]})
    manifest = {"schema": "prodebugger-safety-demo-capture/v1", "data_origin": "controlled_tool_runtime",
        "not_openclaw_agent_run": True, "custom_instrumentation": True, "model_calls": 0,
        "description": "Real isolated tool operations exported with the official OpenTelemetry SDK. No OpenClaw/Gateway/model execution; no production data or external network. Destructive command attempt is blocked without a shell.",
        "output_dir": str(output), "policy_path": str(policy_path), "capture_path": str(capture_path),
        "runtime_receipt_path": str(output / "runtime-receipt.json"),
        "capture_complete": True, "expected_spans": 8, "received_spans": len(spans),
        "sdk_shutdown_ack": True, "capture_sha256": hashlib.sha256(capture_path.read_bytes()).hexdigest(),
        "fixture_sha256": fixture_hashes, "fixture_hashes_unchanged": all(
            hashlib.sha256((output / name).read_bytes()).hexdigest() == value for name, value in fixture_hashes.items()),
        "cases": cases,
        "limitations": ["Safe means no breach of these configured rules in the captured trace, not universal agent safety.",
                        "HTTP violations use an unauthorized loopback port and dummy text; this does not prove real-world data exfiltration.",
                        "The blocked command is an observed unsafe attempt, not an executed deletion.",
                        "Expected verdicts are acceptance fixtures only and are not read by the safety analyzer."]}
    if not manifest["fixture_hashes_unchanged"]:
        raise AssertionError("A demonstration fixture changed unexpectedly")
    _write(output / "capture-manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    manifest = capture_demo(Path(args.output_dir))
    print(json.dumps({"output_dir": manifest["output_dir"], "cases": len(manifest["cases"]),
                      "capture_complete": manifest["capture_complete"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
