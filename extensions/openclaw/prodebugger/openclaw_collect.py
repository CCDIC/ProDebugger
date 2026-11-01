"""Capture actual OpenClaw runs against generated, isolated file-read tasks.

This is a controlled runtime experiment, never a production trace dataset.
Requires an installed OpenClaw CLI, its diagnostics-otel plugin, and Node.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .otel_receiver import make_server
from .otel_import import load_export, spans_from_export
from .record_bridge import write_json


def scenarios():
    return [
        {"id": "case-01", "kind": "read", "files": {"report.txt": "Revenue: 42\n"},
         "target": "report.txt", "expected_text": "Revenue: 42\n"},
        {"id": "case-02", "kind": "read", "files": {"inventory.txt": "Units: 17\n"},
         "target": "inventory.txt", "expected_text": "Units: 17\n"},
        {"id": "case-03", "kind": "recovery", "files": {"backup.txt": "Revenue: 42\n"},
         "first": "current.txt", "target": "backup.txt", "expected_text": "Revenue: 42\n"},
        {"id": "case-04", "kind": "recovery", "files": {"mirror.txt": "Units: 17\n"},
         "first": "latest.txt", "target": "mirror.txt", "expected_text": "Units: 17\n"},
        {"id": "case-05", "kind": "unavailable", "files": {},
         "target": "missing.txt", "expected_text": "Revenue: 77\n"},
        {"id": "case-06", "kind": "unavailable", "files": {},
         "target": "absent.txt", "expected_text": "Units: 99\n"},
    ]


def make_config(workspace, plugin, endpoint, model, base_url, key_env):
    alias = "lab/" + model
    return {
        "agents": {"ownership": "explicit", "entries": {"probe": {}}, "defaults": {
            "workspace": str(workspace), "skipBootstrap": True, "contextInjection": "never",
            "skills": [], "model": {"primary": alias}, "thinkingDefault": "off",
            "timeoutSeconds": 120, "heartbeat": {"every": "0m"}, "sandbox": {"mode": "off"},
            "models": {alias: {"agentRuntime": {"id": "openclaw"},
                "params": {"maxTokens": 1200, "extraBody": {"thinking": {"type": "disabled"}}}}}}},
        "models": {"mode": "replace", "providers": {"lab": {
            "baseUrl": base_url, "apiKey": "${" + key_env + "}", "api": "openai-completions",
            "agentRuntime": {"id": "openclaw"}, "timeoutSeconds": 60,
            "models": [{"id": model, "name": "Controlled file-read experiment", "reasoning": False,
                "input": ["text"], "contextWindow": 64000, "maxTokens": 1200,
                "compat": {"supportsDeveloperRole": False, "supportsStore": False,
                           "maxTokensField": "max_tokens"}}]}}},
        "tools": {"profile": "full", "allow": ["read"], "fs": {"workspaceOnly": True},
                  "codeMode": {"enabled": False}, "toolSearch": {"enabled": False}},
        "plugins": {"allow": ["diagnostics-otel"], "load": {"paths": [str(plugin)]},
                    "entries": {"diagnostics-otel": {"enabled": True}}, "slots": {"memory": "none"}},
        "diagnostics": {"enabled": True, "otel": {"enabled": True, "traces": True,
            "metrics": False, "logs": False, "endpoint": endpoint,
            "tracesEndpoint": endpoint.rstrip("/") + "/v1/traces", "protocol": "http/protobuf",
            "serviceName": "prodebugger-openclaw-lab", "captureContent": True, "sampleRate": 1}},
        "cron": {"enabled": False}, "gateway": {"mode": "local"},
    }


def stop_process(process):
    # Only the process tree created by this runner is terminated on wall timeout.
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=15)


def run_case(args, root, scenario, state):
    folder = root / scenario["id"]
    workspace = folder / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    fixtures = []
    for name, content in scenario["files"].items():
        data = content.encode("utf-8")
        (workspace / name).write_bytes(data)
        fixtures.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    target = str(workspace / scenario["target"])
    if scenario["kind"] == "recovery":
        first = str(workspace / scenario["first"])
        prompt = f"Read {first} using the read tool. If it is unavailable, read {target} instead."
    else:
        prompt = f"Read {target} using the read tool."
    (folder / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    # Acceptance scope is written before execution; this is not a label inferred
    # from whatever the model happens to output. A separate review can later
    # bind these predicates to the runtime's actual argument/result field names.
    write_json(folder / "acceptance.json", {
        "scope": "obtain the declared file content through a read result; final answer excluded",
        "expected_tool": "read", "expected_path": target, "expected_text": scenario["expected_text"],
        "scenario": scenario["kind"], "fixtures": fixtures,
        "planned_diagnostic_status": "uncertain" if scenario["kind"] == "unavailable" else "no_failure",
        "not_an_agent_error_label": scenario["kind"] == "unavailable",
    })
    # Keep the original task even if launching, capture parsing, or cleanup fails.
    # Completion remains false until the native transcript and capture are audited.
    write_json(folder / "task.json", {"schema": "openclaw-task/v1", "task": prompt,
        "capture": {"complete": False, "reason": "Pending comparison of native session transcript and all captured spans."}})
    config_path = folder / "openclaw.json"
    session_id = str(uuid.uuid4())
    command = [str(args.node), str(args.cli), "agent", "--local", "--agent", "probe",
               "--session-id", session_id, "--model", "lab/" + args.model, "--message-file",
               str(folder / "prompt.txt"), "--thinking", "off", "--timeout", "120", "--json"]
    env = os.environ.copy()
    env.update(OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(config_path),
               OTEL_BSP_SCHEDULE_DELAY="1000")
    evidence = {"schema": "openclaw-runtime-capture/v1", "data_origin": "controlled",
        "command": command, "session_id": session_id, "started_at": datetime.now(timezone.utc).isoformat(),
        "wall_timeout_seconds": args.wall_timeout, "agent_timeout_seconds": 120,
        "provider_timeout_seconds": 60, "hard_model_call_cap": None,
        "fixtures": fixtures, "scenario": scenario["kind"], "status": "started"}
    write_json(folder / "execution.json", evidence)
    started = time.monotonic()
    server = worker = None
    try:
        server = make_server(folder / "capture", port=0)
        config = make_config(workspace, args.plugin, f"http://127.0.0.1:{server.server_address[1]}",
                             args.model, args.base_url, args.api_key_env)
        write_json(config_path, config)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        with (folder / "stdout.json").open("wb") as stdout, (folder / "stderr.txt").open("wb") as stderr:
            process = subprocess.Popen(command, cwd=workspace, env=env, stdout=stdout, stderr=stderr,
                                       start_new_session=os.name != "nt")
            try:
                code = process.wait(timeout=args.wall_timeout)
                evidence.update(status="exited", exit_code=code)
            except subprocess.TimeoutExpired:
                evidence.update(status="wall_timeout")
                stop_process(process)
                evidence.update(exit_code=process.returncode)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        if evidence["status"] == "wall_timeout":
            evidence["cleanup_error_type"] = type(exc).__name__
        else:
            evidence.update(status="launch_error", error_type=type(exc).__name__)
    finally:
        if server is not None:
            if worker is not None and worker.is_alive():
                server.shutdown()
            server.server_close()
        if worker is not None:
            worker.join(timeout=10)
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        captured = folder / "capture" / "traces.jsonl"
        if captured.exists():
            try:
                spans = spans_from_export(load_export(captured))
                evidence["span_count"] = len(spans)
                evidence["trace_ids"] = sorted({s["trace_id"] for s in spans})
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
                # Keep raw files and the original process status for diagnosis.
                evidence.update(span_count=None, trace_ids=[], capture_audit_error_type=type(exc).__name__)
        else:
            evidence.update(span_count=0, trace_ids=[])
        write_json(folder / "execution.json", evidence)
    print(scenario["id"] + ": " + evidence["status"] + ", spans=" + str(evidence["span_count"]), flush=True)
    return {"id": scenario["id"], "export": scenario["id"] + "/capture/traces.jsonl",
            "task_file": scenario["id"] + "/task.json", "expected_status": None,
            "expected_root_steps": [], "expected_module": None,
            "execution_status": evidence["status"], "exit_code": evidence.get("exit_code"),
            "span_count": evidence["span_count"],
            "capture_audit_error_type": evidence.get("capture_audit_error_type")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("node", "cli", "plugin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--wall-timeout", type=int, default=600)
    parser.add_argument("--cases", nargs="+", choices=[s["id"] for s in scenarios()])
    args = parser.parse_args(argv)
    if not os.environ.get(args.api_key_env):
        parser.error("credential environment variable is not set: " + args.api_key_env)
    if args.wall_timeout < 120:
        parser.error("wall-timeout must be at least 120 seconds")
    for field in ("node", "cli", "plugin", "output_dir"):
        setattr(args, field, getattr(args, field).resolve())
    if not args.node.is_file() or not args.cli.is_file() or not args.plugin.is_dir():
        parser.error("node/cli/plugin must identify existing installed runtime files")
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=False)
    state = args.state_dir.resolve() if args.state_dir else root / "state"
    state.mkdir(parents=True, exist_ok=True)
    plan = [s for s in scenarios() if not args.cases or s["id"] in args.cases]
    write_json(root / "plan.json", {"schema": "openclaw-controlled-plan/v1", "scenarios": plan,
        "data_origin": "actual OpenClaw execution on generated controlled fixtures, not production history",
        "created_before_execution": True})
    manifest = {"schema": "openclaw-evaluation/v1", "data_origin": "controlled", "status": "started", "cases": []}
    write_json(root / "manifest.json", manifest)
    failed = False
    for scenario in plan:
        try:
            result = run_case(args, root, scenario, state)
        except Exception as exc:
            result = {"id": scenario["id"], "execution_status": "case_error", "error_type": type(exc).__name__,
                      "export": scenario["id"] + "/capture/traces.jsonl",
                      "task_file": scenario["id"] + "/task.json", "expected_status": None,
                      "expected_root_steps": [], "expected_module": None}
        manifest["cases"].append(result)
        failed |= (result["execution_status"] != "exited" or result.get("exit_code") != 0
                   or not result.get("span_count") or result.get("capture_audit_error_type") is not None)
        write_json(root / "manifest.json", manifest)
    manifest["status"] = "completed_with_errors" if failed else "completed"
    write_json(root / "manifest.json", manifest)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
