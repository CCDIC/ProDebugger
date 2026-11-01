"""Capture isolated OpenClaw tasks through one foreground localhost gateway.

The receiver retains every native OTLP request. Per-task trace assignment uses
an exact captured run input.value match; timestamps never establish identity.
No gateway service is installed and no message is delivered to a channel.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.request
import uuid

from .openclaw_collect import scenarios, make_config, stop_process
from .otel_receiver import make_server
from .otel_import import load_export, spans_from_export
from .record_bridge import write_json


# --import installs a parent-owned stdin control before the native CLI loads.
# Emitting SIGTERM inside Node invokes OpenClaw's ordinary graceful shutdown
# handler on Windows too; Python terminate() would bypass that handler.
SHUTDOWN_PRELOAD = r'''import readline from "node:readline";
let requested = false;
const input = readline.createInterface({input: process.stdin, terminal: false});
input.on("line", (line) => {
  if (line !== "prodebugger-shutdown" || requested) return;
  requested = true;
  const timer = setInterval(() => {
    if (process.listenerCount("SIGTERM") > 0) {
      clearInterval(timer);
      input.close();
      process.stdin.pause();
      process.emit("SIGTERM");
    }
  }, 100);
});
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def create_state_directory(root, requested=None):
    """Allow a short fresh state path without exposing it to the read tool."""
    state = Path(requested).resolve() if requested else (root / "state").resolve()
    workspace = (root / "workspace").resolve()
    if state == workspace or workspace in state.parents:
        raise ValueError("state directory must be outside the fixture workspace")
    # Refuse existing state, including the user's normal OpenClaw directory.
    state.mkdir(parents=True, exist_ok=False)
    return state


def reuse_installed_state(root, manifest_path, version):
    """Reuse this collector's finished installation, then recheck native trust.

    Only the existing isolated state is reused. All fixtures, session IDs,
    configuration, and capture evidence belong to the new output directory.
    No install record is copied or synthesized.
    """
    path = Path(manifest_path).resolve()
    payload = path.read_bytes()
    previous = load_export(path)
    if (previous.get("schema") != "openclaw-evaluation/v1"
            or previous.get("data_origin") != "controlled"
            or previous.get("runtime_version") != version
            or not previous.get("finished_at")
            or previous.get("plugin_trust_verified") is not True
            or not isinstance(previous.get("state_directory"), str)):
        raise ValueError("reuse requires a finished compatible collector installation manifest")
    state = Path(previous["state_directory"]).resolve()
    workspace = (root / "workspace").resolve()
    if not state.is_dir() or state == workspace or workspace in state.parents:
        raise ValueError("installed state must exist outside the new fixture workspace")
    return state, {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
                   "state_directory": str(state),
                   "verification": "fresh official metadata inspection required before model execution"}


def runtime_version(cli):
    try:
        package = json.loads((Path(cli).parent / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return package.get("version") if package.get("name") == "openclaw" else None


def collection_environment(args, root, state):
    """Keep npm/cache writes local and make the chosen Node available to children."""
    env = os.environ.copy()
    path_key = next((key for key in env if key.upper() == "PATH"), "PATH")
    env[path_key] = str(args.node.parent) + os.pathsep + env.get(path_key, "")
    env.update(OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(root / "openclaw.json"),
               OPENCLAW_GATEWAY_TOKEN=secrets.token_urlsafe(32), OTEL_BSP_SCHEDULE_DELAY="1000",
               OPENCLAW_NO_RESPAWN="1", NODE_DISABLE_COMPILE_CACHE="1",
               XDG_CACHE_HOME=str(state / "cache"), npm_config_cache=str(state / "npm-cache"),
               npm_config_update_notifier="false", npm_config_fund="false")
    env.pop("NODE_COMPILE_CACHE", None)
    return env


def run_setup_command(command, root, env, manifest, key, timeout):
    """Persist bounded CLI setup evidence before any model request is possible."""
    receipt = {"command": command, "status": "started", "started_at": now(),
               "timeout_seconds": timeout, "stdout": key + ".stdout.json",
               "stderr": key + ".stderr.txt", "exit_code": None}
    manifest[key] = receipt
    write_json(root / "manifest.json", manifest)
    process = None
    try:
        with (root / receipt["stdout"]).open("wb") as stdout, (root / receipt["stderr"]).open("wb") as stderr:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=stdout, stderr=stderr,
                                       start_new_session=os.name != "nt",
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            try:
                receipt["exit_code"] = process.wait(timeout=timeout)
                receipt["status"] = "exited"
            except subprocess.TimeoutExpired:
                receipt["status"] = "timeout"
                stop_process(process)
                receipt["exit_code"] = process.returncode
                raise
        if receipt["exit_code"] != 0:
            raise RuntimeError(key + " failed; see saved setup logs")
    except BaseException as exc:
        receipt["error_type"] = type(exc).__name__
        if receipt["status"] == "started":
            receipt["status"] = "failed"
        if process is not None and process.poll() is None:
            stop_process(process)
        raise
    finally:
        receipt["finished_at"] = now()
        write_json(root / "manifest.json", manifest)
    return receipt


def inspect_official_exporter(args, root, env, manifest, key, enabled):
    # Default inspect reads package/install metadata. --runtime would import the
    # plugin and repeat source capture while a management lease is held.
    version = manifest["runtime_version"]
    command = [str(args.node), "--stack-size=8192", str(args.cli), "plugins", "inspect",
               "diagnostics-otel", "--json"]
    receipt = run_setup_command(command, root, env, manifest, key, args.inspect_timeout)
    report = json.loads((root / receipt["stdout"]).read_text(encoding="utf-8-sig"))
    plugin = report.get("plugin", {}) if isinstance(report, dict) else {}
    install = report.get("install", {}) if isinstance(report, dict) else {}
    trusted = (plugin.get("id") == "diagnostics-otel"
               and plugin.get("trustedOfficialInstall") is True
               and plugin.get("trust", {}).get("reason") == "trusted-official"
               and plugin.get("version") == version
               and plugin.get("enabled") is enabled
               and install.get("source") == "npm")
    manifest[key + "_verified"] = trusted
    write_json(root / "manifest.json", manifest)
    if not trusted:
        raise RuntimeError("official diagnostics exporter trust/version/enablement was not verified; no model requests sent")


def install_official_exporter(args, root, env, manifest):
    manifest["plugin_trust_verified"] = False
    reused = manifest.get("reused_install_source") is not None
    manifest["plugin_install_strategy"] = ("existing_official_install_reverified_then_config_enable" if reused
                                            else "official_disabled_install_then_config_enable")
    write_json(root / "manifest.json", manifest)
    config_path = root / "openclaw.json"
    if load_export(config_path)["plugins"]["entries"]["diagnostics-otel"].get("enabled") is not False:
        raise RuntimeError("official exporter install requires explicit disabled configuration")
    version = manifest["runtime_version"]
    command = [str(args.node), "--stack-size=8192", str(args.cli), "plugins", "install",
               "npm:@openclaw/diagnostics-otel@" + version, "--pin", "--no-enable"]
    if reused:
        print("Rechecking the existing official exporter installation in isolated state", flush=True)
    else:
        print("Installing the matching official diagnostics exporter disabled in isolated state", flush=True)
        run_setup_command(command, root, env, manifest, "plugin_install", args.install_timeout)
    inspect_official_exporter(args, root, env, manifest, "plugin_inspect_disabled", False)
    # --no-enable preserves authored policy. Read the installer's saved config,
    # retaining its additions and literal environment references, then change
    # only this boolean. `plugins enable` also runs slot runtime inspection.
    before = config_path.read_bytes()
    config = load_export(config_path)
    entry = config["plugins"]["entries"]["diagnostics-otel"]
    if entry.get("enabled") is not False:
        raise RuntimeError("installer changed the disabled exporter configuration")
    entry["enabled"] = True
    write_json(config_path, config)
    manifest["plugin_config_enable"] = {
        "method": "saved_config_single_boolean_change", "path": "openclaw.json",
        "json_pointer": "/plugins/entries/diagnostics-otel/enabled", "before": False, "after": True,
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "after_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(), "changed_at": now()}
    write_json(root / "manifest.json", manifest)
    inspect_official_exporter(args, root, env, manifest, "plugin_inspect", True)
    manifest["plugin_trust_verified"] = True
    write_json(root / "manifest.json", manifest)


def prepare_cases(root, plan):
    workspace = root / "workspace"
    workspace.mkdir()
    cases = []
    for scenario in plan:
        case_id = scenario["id"]
        folder, files = root / "cases" / case_id, workspace / case_id
        folder.mkdir(parents=True)
        files.mkdir()
        fixtures = []
        for name, content in scenario["files"].items():
            data = content.encode("utf-8")
            (files / name).write_bytes(data)
            fixtures.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        target = str(files / scenario["target"])
        marker = str(uuid.uuid4())
        instruction = (f"Read {files / scenario['first']} using the read tool. If it is unavailable, read {target} instead."
                       if scenario["kind"] == "recovery" else f"Read {target} using the read tool.")
        prompt = f"Controlled capture task {case_id} ({marker}). {instruction}"
        (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
        write_json(folder / "acceptance.json", {
            "scope": "obtain the declared file content through a read result; final answer excluded",
            "expected_tool": "read", "expected_path": target, "expected_text": scenario["expected_text"],
            "scenario": scenario["kind"], "fixtures": fixtures,
            "not_an_agent_error_label": scenario["kind"] == "unavailable"})
        write_json(folder / "task.json", {"schema": "openclaw-task/v1", "task": prompt,
            "capture": {"complete": False, "reason": "Pending native transcript and capture audit; no task contract bound."}})
        session = str(uuid.uuid4())
        params = {"message": prompt, "agentId": "probe", "sessionId": session,
                  "sessionKey": "agent:probe:" + marker, "thinking": "off", "deliver": False,
                  "timeout": 120, "idempotencyKey": str(uuid.uuid4())}
        write_json(folder / "rpc-params.json", params)
        cases.append({"id": case_id, "task_file": f"cases/{case_id}/task.json",
                      "export": "capture/traces.jsonl", "trace_id": None,
                      "session_id": session, "execution_status": "pending",
                      "expected_status": None, "expected_root_steps": [], "expected_module": None})
    return cases


def rpc_command(args, method, params, timeout_ms, expect_final=False):
    command = [str(args.node), str(args.cli), "gateway", "call", method,
               "--port", str(args.port), "--expect-url", f"ws://127.0.0.1:{args.port}",
               "--params", json.dumps(params, ensure_ascii=False, separators=(",", ":")),
               "--timeout", str(timeout_ms), "--json"]
    if expect_final:
        command.append("--expect-final")
    return command


def wait_ready(process, port, timeout):
    started, last_report = time.monotonic(), -10.0
    while time.monotonic() - started < timeout:
        if process.poll() is not None:
            raise RuntimeError("gateway exited before readiness")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        elapsed = time.monotonic() - started
        if elapsed - last_report >= 10:
            print(f"Gateway startup: {int(elapsed)}s elapsed; waiting for localhost healthz", flush=True)
            last_report = elapsed
        time.sleep(2)
    raise TimeoutError("gateway readiness deadline exceeded")


class TelemetryUnavailable(RuntimeError):
    pass


def verify_exporter_startup(root):
    log = root / "gateway.stderr.txt"
    if log.exists() and "internal diagnostics capability unavailable" in log.read_text(encoding="utf-8", errors="replace"):
        raise TelemetryUnavailable("internal_diagnostics_capability_unavailable")


def wait_for_native_capture(root, timeout=5):
    """Allow the configured one-second batch exporter a bounded delivery grace."""
    path = root / "capture" / "traces.jsonl"
    deadline = time.monotonic() + timeout
    while True:
        if path.is_file() and path.stat().st_size > 0:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.2, remaining))


def shutdown_gateway(process, timeout=45):
    if process is None:
        return "not_started"
    if process.poll() is not None:
        return "already_exited"
    try:
        process.stdin.write(b"prodebugger-shutdown\n")
        process.stdin.flush()
        code = process.wait(timeout=timeout)
        return "graceful_signal" if code == 0 else "nonzero_exit"
    except (OSError, subprocess.TimeoutExpired):
        stop_process(process)
        return "forced_process_tree"


def audit_gateway_shutdown(root, method, exit_code):
    """Check native exit/log evidence without retaining arbitrary log text.

    A signal and process exit do not establish exporter cleanup. The same bytes
    are hashed and checked for known lifecycle failures; harmless warnings are
    allowed. Missing evidence remains unknown rather than implying a clean flush.
    """
    methods = {"graceful_signal", "nonzero_exit", "forced_process_tree", "already_exited", "not_started"}
    method = method if isinstance(method, str) and method in methods else None
    code = exit_code if type(exit_code) is int else None
    reasons, logs = set(), []
    if method != "graceful_signal":
        reasons.add("gateway_not_gracefully_stopped")
    if code is None:
        reasons.add("gateway_exit_code_unknown")
    elif code != 0:
        reasons.add("gateway_exit_nonzero")
    failures = (
        ("exporter_forced_retirement", r"\[(?:plugins/cleanup|plugins)\][^\r\n]*\b(?:Plugin\s+)?diagnostics-otel\b[^\r\n]*\bforced retirement\b"),
        ("exporter_cleanup_failed", r"\[(?:plugins/cleanup|plugins)\][^\r\n]*\b(?:Plugin\s+)?diagnostics-otel\b[^\r\n]*\bcleanup failed\b"),
        ("gateway_shutdown_failed", r"\[shutdown\]\s+failed\b"),
        ("gateway_shutdown_step_failed", r"\[gateway\]\s+shutdown step failed\b"),
        ("gateway_shutdown_incomplete", r"\[gateway\][^\r\n]*\bGateway shutdown did not complete cleanly\b"),
    )
    for name in ("gateway.stdout.txt", "gateway.stderr.txt"):
        try:
            data = (Path(root) / name).read_bytes()
        except OSError:
            logs.append({"path": name, "error": "missing_or_unreadable"})
            reasons.add("gateway_shutdown_log_unavailable")
            continue
        logs.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            reasons.add("gateway_shutdown_log_invalid_utf8")
            text = data.decode("utf-8-sig", errors="replace")
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        for reason, pattern in failures:
            if re.search(pattern, text, re.IGNORECASE):
                reasons.add(reason)
    return {"schema": "openclaw-gateway-shutdown/v1", "method": method, "exit_code": code,
            "logs": logs, "reasons": sorted(reasons), "clean": not reasons}


def assign_traces(root, cases, spans):
    """Bind native run prompts and filter original OTLP groups without flattening."""
    from .otel_import import loads

    capture_path = Path("capture") / "traces.jsonl"
    payload = (root / capture_path).read_bytes()
    documents = [loads(line) for line in payload.decode("utf-8-sig").splitlines() if line.strip()]
    captured_spans = spans_from_export(documents)
    identity = lambda rows: {(s["trace_id"], s["span_id"]): s["source_digest"] for s in rows}
    if identity(captured_spans) != identity(spans):
        raise ValueError("trace assignment spans differ from the original native capture")
    origin = {"path": capture_path.as_posix(), "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}

    def select(value, trace):
        """Keep raw resource/scope/document metadata and exact native trace IDs."""
        if isinstance(value, list):
            selected = [item for row in value if (item := select(row, trace)) is not None]
            return selected or None
        if not isinstance(value, dict):
            return None
        native_id = value.get("traceId", value.get("trace_id", value.get("traceID")))
        if isinstance(native_id, str):
            return value if native_id.lower() == trace else None
        # Only traversal containers are changed. All resource attributes, scope
        # names/versions, schema URLs, span values and their original keys stay.
        keys = ("resourceSpans", "resource_spans", "scopeSpans", "scope_spans",
                "instrumentationLibrarySpans", "spans", "data", "result")
        updates = {key: select(value[key], trace) for key in keys if key in value}
        if not any(item is not None for item in updates.values()):
            return None
        return {**value, **{key: item if item is not None else [] for key, item in updates.items()}}

    for case in cases:
        prompt = (root / "cases" / case["id"] / "prompt.txt").read_text(encoding="utf-8")
        matching = [span for span in captured_spans if span["name"] in
                    {"openclaw.run", "openclaw.harness.run", "openclaw.message.processed"}
                    and span["attributes"].get("input.value") == prompt]
        identities = sorted({span["trace_id"] for span in matching})
        case["trace_binding"] = {"method": "exact_native_run_input.value", "matching_trace_ids": identities,
                                 "matching_span_ids": sorted({span["span_id"] for span in matching}),
                                 "source_capture": dict(origin)}
        case["export"] = capture_path.as_posix()
        if len(identities) != 1:
            case["trace_id"] = None
            case["trace_binding"]["status"] = "missing" if not identities else "ambiguous"
            continue
        case["trace_id"] = identities[0]
        case["trace_binding"]["status"] = "matched_pending_completeness_audit"
    # Different task prompts can share one trace in an unusual runtime/export.
    # A per-trace sequential importer cannot separate those task boundaries.
    for case in cases:
        trace = case["trace_id"]
        if trace and sum(trace in other["trace_binding"]["matching_trace_ids"] for other in cases) > 1:
            case["trace_id"] = None
            case["trace_binding"]["status"] = "shared_trace_requires_task_boundary_review"
    # Shared or ambiguous bindings never produce an apparently isolated export.
    for case in cases:
        trace = case["trace_id"]
        if trace is None:
            continue
        derived = select(documents, trace)
        if not derived:
            raise ValueError("matched native trace is absent from the original capture")
        selected = spans_from_export(derived)
        expected = [s for s in captured_spans if s["trace_id"] == trace]
        if identity(selected) != identity(expected):
            raise ValueError("trace filtering changed the native span set or source contents")
        path = Path("cases") / case["id"] / "trace.json"
        write_json(root / path, derived)
        provenance_path = path.with_name("trace.provenance.json")
        provenance = {"schema": "openclaw-trace-selection/v1", "source_capture": dict(origin),
            "method": "exact_native_run_input.value_then_native_trace_id_filter",
            "trace_id": trace, "matching_span_ids": case["trace_binding"]["matching_span_ids"],
            "selected_unique_span_count": len(selected),
            "boundary": "all captured spans with this native trace ID; no time slicing or session stitching",
            "export": {"path": path.as_posix(), "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest()}}
        write_json(root / provenance_path, provenance)
        case["export"] = path.as_posix()
        case["trace_binding"]["provenance_file"] = provenance_path.as_posix()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("node", "cli", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--plugin", type=Path, help="Deprecated and ignored; exporter is installed through the official CLI")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--state-dir", type=Path, help="Fresh isolated state directory outside the fixture workspace; short paths reduce Windows filesystem overhead")
    parser.add_argument("--reuse-install-from", type=Path,
                        help="Finished collector manifest whose isolated official installation will be reverified; new sessions and capture are created")
    parser.add_argument("--port", type=int, default=18879)
    parser.add_argument("--startup-timeout", type=int, default=1800)
    parser.add_argument("--install-timeout", type=int, default=1800)
    parser.add_argument("--inspect-timeout", type=int, default=180)
    parser.add_argument("--wall-timeout", type=int, default=240)
    parser.add_argument("--cases", nargs="+", choices=[s["id"] for s in scenarios()])
    args = parser.parse_args(argv)
    if args.state_dir is not None and args.reuse_install_from is not None:
        parser.error("--state-dir and --reuse-install-from are mutually exclusive")
    if not os.environ.get(args.api_key_env):
        parser.error("credential environment variable is not set: " + args.api_key_env)
    if (not 1 <= args.port <= 65535 or min(args.startup_timeout, args.install_timeout, args.inspect_timeout) < 1
            or args.wall_timeout < 120):
        parser.error("valid port, positive startup timeout, and wall timeout >=120 are required")
    for field in ("node", "cli", "output_dir"):
        setattr(args, field, getattr(args, field).resolve())
    if not args.node.is_file() or not args.cli.is_file():
        parser.error("node/cli must identify installed runtime files")
    version = runtime_version(args.cli)
    if not isinstance(version, str) or not version:
        parser.error("cli must belong to an installed openclaw package with an exact version")
    # Never attach to an unrelated existing listener.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error("gateway port is already in use")
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=False)
    reused_install = None
    try:
        if args.reuse_install_from is not None:
            state, reused_install = reuse_installed_state(root, args.reuse_install_from, version)
        else:
            state = create_state_directory(root, args.state_dir)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    plan = [s for s in scenarios() if not args.cases or s["id"] in args.cases]
    cases = prepare_cases(root, plan)
    write_json(root / "plan.json", {"schema": "openclaw-controlled-plan/v1", "created_before_execution": True,
        "data_origin": "actual OpenClaw gateway execution on generated fixtures, not production history", "scenarios": plan})
    manifest = {"schema": "openclaw-evaluation/v1", "data_origin": "controlled", "status": "installing_exporter",
                "started_at": now(), "cases": cases, "capture": "capture/traces.jsonl",
                "runtime_version": version, "state_directory": str(state),
                "gateway_port": args.port, "hard_model_call_cap": None,
                "agent_timeout_seconds": 120, "provider_timeout_seconds": 60, "wall_timeout_seconds": args.wall_timeout}
    if reused_install is not None:
        manifest["reused_install_source"] = reused_install
    write_json(root / "manifest.json", manifest)
    receiver = worker = gateway = active_client = None
    gateway_stdout = gateway_stderr = None
    failed = False
    try:
        receiver = make_server(root / "capture", port=0)
        worker = threading.Thread(target=receiver.serve_forever, daemon=True)
        worker.start()
        config = make_config(root / "workspace", args.plugin, f"http://127.0.0.1:{receiver.server_address[1]}",
                             args.model, args.base_url, args.api_key_env)
        # Local load paths lack the official install provenance needed for the
        # exporter to receive OpenClaw's private diagnostics capability.
        config["plugins"].pop("load", None)
        config["plugins"]["entries"]["diagnostics-otel"]["enabled"] = False
        config["gateway"] = {"mode": "local", "port": args.port, "bind": "loopback",
                             "auth": {"mode": "token", "token": "${OPENCLAW_GATEWAY_TOKEN}"},
                             "tailscale": {"mode": "off"}, "controlUi": {"enabled": False}}
        write_json(root / "openclaw.json", config)
        preload = root / "shutdown-preload.mjs"
        preload.write_text(SHUTDOWN_PRELOAD, encoding="utf-8")
        env = collection_environment(args, root, state)
        install_official_exporter(args, root, env, manifest)
        manifest["status"] = "starting_gateway"
        command = [str(args.node), "--stack-size=8192", "--import", preload.as_uri(), str(args.cli), "gateway", "run",
                   "--port", str(args.port), "--bind", "loopback", "--auth", "token", "--tailscale", "off"]
        manifest["gateway_command"] = command
        write_json(root / "manifest.json", manifest)
        gateway_stdout, gateway_stderr = (root / "gateway.stdout.txt").open("wb"), (root / "gateway.stderr.txt").open("wb")
        gateway = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.PIPE,
                                   stdout=gateway_stdout, stderr=gateway_stderr,
                                   start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        wait_ready(gateway, args.port, args.startup_timeout)
        verify_exporter_startup(root)
        manifest["exporter_startup_check"] = {"checked_at": now(), "capability_refusal_found": False}
        manifest["status"] = "collecting"
        manifest["gateway_ready_at"] = now()
        write_json(root / "manifest.json", manifest)
        for case in cases:
            folder = root / "cases" / case["id"]
            params = load_export(folder / "rpc-params.json")
            case.update(execution_status="started", started_at=now())
            write_json(root / "manifest.json", manifest)
            command = rpc_command(args, "agent", params, args.wall_timeout * 1000 - 5000, True)
            with (folder / "stdout.json").open("wb") as stdout, (folder / "stderr.txt").open("wb") as stderr:
                client = subprocess.Popen(command, cwd=root, env=env, stdout=stdout, stderr=stderr,
                                          start_new_session=os.name != "nt")
                active_client = client
                try:
                    code = client.wait(timeout=args.wall_timeout)
                    case.update(execution_status="exited", exit_code=code)
                    failed |= code != 0
                except subprocess.TimeoutExpired:
                    stop_process(client)
                    case.update(execution_status="wall_timeout", exit_code=client.returncode)
                    failed = True
                active_client = None
            case["finished_at"] = now()
            write_json(root / "manifest.json", manifest)
            print(case["id"] + ": " + case["execution_status"] + "; native capture retained", flush=True)
            if case is cases[0] and case["execution_status"] == "exited":
                if not wait_for_native_capture(root):
                    raise TelemetryUnavailable("no_native_capture_after_first_case_and_5s_exporter_grace")
                manifest["first_case_telemetry_check"] = {"checked_at": now(), "nonempty_capture": True}
            if case["execution_status"] == "wall_timeout":
                break
    except (Exception, KeyboardInterrupt) as exc:
        failed = True
        manifest.update(error_type=type(exc).__name__, status="capture_error")
        if isinstance(exc, TelemetryUnavailable):
            manifest.update(telemetry_status="unavailable", telemetry_reason=str(exc))
    finally:
        if active_client is not None and active_client.poll() is None:
            try:
                stop_process(active_client)
            except (OSError, subprocess.SubprocessError) as exc:
                manifest["client_shutdown_error_type"] = type(exc).__name__
        try:
            manifest["gateway_shutdown"] = shutdown_gateway(gateway)
            failed |= manifest["gateway_shutdown"] != "graceful_signal"
        except (OSError, subprocess.SubprocessError) as exc:
            failed = True
            manifest["gateway_shutdown_error_type"] = type(exc).__name__
        code = getattr(gateway, "returncode", None) if gateway is not None else None
        manifest["gateway_exit_code"] = code if type(code) is int else None
        # Keep the receiver live while native shutdown attempts the SDK flush.
        if receiver is not None:
            receiver.shutdown()
            receiver.server_close()
        if worker is not None:
            worker.join(timeout=10)
        for handle in (gateway_stdout, gateway_stderr):
            if handle is not None:
                handle.close()
        manifest["gateway_shutdown_audit"] = audit_gateway_shutdown(
            root, manifest.get("gateway_shutdown"), manifest["gateway_exit_code"])
        failed |= not manifest["gateway_shutdown_audit"]["clean"]
        capture = root / "capture" / "traces.jsonl"
        manifest["span_count"] = 0
        if capture.exists():
            try:
                spans = spans_from_export(load_export(capture))
                manifest["span_count"] = len(spans)
                failed |= not spans
                assign_traces(root, cases, spans)
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
                failed = True
                manifest["capture_audit_error_type"] = type(exc).__name__
        else:
            failed = True
        failed |= any(case["execution_status"] != "exited" or case.get("exit_code") != 0 for case in cases)
        # Current native exporters omit run input.value. A successful capture
        # therefore awaits exact native-session/tool-ID binding in the separate
        # offline audit, without inventing task/trace identity here.
        pending = [case["id"] for case in cases if case["trace_id"] is None]
        manifest["binding_pending"] = bool(pending) and not failed
        manifest["binding_pending_case_ids"] = pending if not failed else []
        status = ("telemetry_unavailable" if manifest.get("telemetry_status") == "unavailable"
                  else "completed_with_errors" if failed else "captured_pending_audit")
        manifest.update(status=status, finished_at=now())
        write_json(root / "manifest.json", manifest)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
