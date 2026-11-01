"""No-model checks for the single-gateway controlled capture workflow."""
from argparse import Namespace
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from prodebugger import openclaw_collect_gateway as gateway
from checks.test_otel_import import normalized, exported, span, tool, TRACE, OTHER_TRACE


def save_capture(root, documents):
    path = root / "capture" / "traces.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = documents if isinstance(documents, list) else [documents]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class GatewayCollectionChecks(unittest.TestCase):
    def test_exporter_capability_refusal_blocks_model_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gateway.stderr.txt").write_text("[plugins] diagnostics-otel: internal diagnostics capability unavailable\n")
            with self.assertRaisesRegex(gateway.TelemetryUnavailable, "internal_diagnostics"):
                gateway.verify_exporter_startup(root)
            (root / "gateway.stderr.txt").write_text("Gateway ready\n")
            gateway.verify_exporter_startup(root)

    def test_capture_grace_requires_nonempty_native_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(gateway.wait_for_native_capture(root, timeout=0))
            capture = root / "capture/traces.jsonl"
            capture.parent.mkdir()
            capture.touch()
            self.assertFalse(gateway.wait_for_native_capture(root, timeout=0))
            capture.write_text('{"resourceSpans":[]}\n')
            self.assertTrue(gateway.wait_for_native_capture(root, timeout=0))

    def test_missing_telemetry_stops_after_first_case(self):
        try:
            import opentelemetry.proto
        except ImportError:
            self.skipTest("optional requirements-otel.txt not installed")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GATEWAY_TEST_PROVIDER_KEY": "offline-secret"}):
            base = Path(directory)
            node, cli = base / "node", base / "openclaw.mjs"
            node.touch()
            cli.touch()
            (base / "package.json").write_text(json.dumps({"name": "openclaw", "version": "2026.10.5"}))
            with gateway.socket.socket() as port_probe:
                port_probe.bind(("127.0.0.1", 0))
                port = port_probe.getsockname()[1]
            process = Mock(wait=Mock(return_value=0), poll=Mock(return_value=None))
            with patch.object(gateway.subprocess, "Popen", return_value=process) as launch, \
                 patch.object(gateway, "install_official_exporter"), patch.object(gateway, "wait_ready"), \
                 patch.object(gateway, "wait_for_native_capture", return_value=False), redirect_stdout(io.StringIO()):
                status = gateway.main(["--node", str(node), "--cli", str(cli), "--output-dir", str(base / "run"),
                                       "--api-key-env", "GATEWAY_TEST_PROVIDER_KEY", "--port", str(port),
                                       "--cases", "case-01", "case-02"])
            self.assertEqual(status, 1)
            self.assertEqual(launch.call_count, 2)  # Gateway plus exactly one agent RPC.
            manifest = json.loads((base / "run/manifest.json").read_text())
            self.assertEqual(manifest["status"], "telemetry_unavailable")
            self.assertEqual(manifest["cases"][0]["execution_status"], "exited")
            self.assertEqual(manifest["cases"][1]["execution_status"], "pending")

    def test_native_capture_without_run_prompt_waits_for_binding_but_empty_capture_fails(self):
        try:
            import opentelemetry.proto
        except ImportError:
            self.skipTest("optional requirements-otel.txt not installed")
        for payload in (exported(), {"resourceSpans": []}, None):
            with self.subTest(empty=payload != exported()), tempfile.TemporaryDirectory() as directory, \
                 patch.dict(os.environ, {"GATEWAY_TEST_PROVIDER_KEY": "offline-secret"}):
                base = Path(directory)
                node, cli = base / "node", base / "openclaw.mjs"
                node.touch()
                cli.touch()
                (base / "package.json").write_text(json.dumps({"name": "openclaw", "version": "2026.10.5"}))
                with gateway.socket.socket() as port_probe:
                    port_probe.bind(("127.0.0.1", 0))
                    port = port_probe.getsockname()[1]
                def ready(*args):
                    if payload is not None:
                        save_capture(base / "run", payload)
                    else:
                        (base / "run/capture/traces.jsonl").touch()
                process = Mock(wait=Mock(return_value=0), poll=Mock(return_value=None), returncode=0)
                with patch.object(gateway.subprocess, "Popen", return_value=process), \
                     patch.object(gateway, "install_official_exporter"), patch.object(gateway, "wait_ready", side_effect=ready), \
                     patch.object(gateway, "wait_for_native_capture", return_value=True), redirect_stdout(io.StringIO()):
                    status = gateway.main(["--node", str(node), "--cli", str(cli), "--output-dir", str(base / "run"),
                                           "--api-key-env", "GATEWAY_TEST_PROVIDER_KEY", "--port", str(port), "--cases", "case-01"])
                manifest = gateway.load_export(base / "run/manifest.json")
                self.assertIsNone(manifest["cases"][0]["trace_id"])
                self.assertEqual(manifest["gateway_shutdown"], "graceful_signal")
                self.assertEqual(manifest["gateway_exit_code"], 0)
                self.assertTrue(manifest["gateway_shutdown_audit"]["clean"])
                task = gateway.load_export(base / "run/cases/case-01/task.json")
                self.assertFalse(task["capture"]["complete"])
                if payload == exported():
                    self.assertEqual(status, 0)
                    self.assertEqual(manifest["status"], "captured_pending_audit")
                    self.assertGreater(manifest["span_count"], 0)
                    self.assertTrue(manifest["binding_pending"])
                    self.assertEqual(manifest["binding_pending_case_ids"], ["case-01"])
                    self.assertEqual(manifest["cases"][0]["trace_binding"]["status"], "missing")
                else:
                    self.assertEqual(status, 1)
                    self.assertEqual(manifest["status"], "completed_with_errors")
                    self.assertEqual(manifest["span_count"], 0)
                    self.assertFalse(manifest["binding_pending"])
                    self.assertEqual(manifest["capture_audit_error_type"], "ValueError")

    def test_official_install_is_pinned_and_trust_inspection_never_imports_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = Namespace(node=root / "node", cli=root / "openclaw.mjs", install_timeout=90, inspect_timeout=30)
            manifest = {"runtime_version": "2026.10.5"}
            config = {"plugins": {"entries": {"diagnostics-otel": {"enabled": False}}},
                      "models": {"apiKey": "${GATEWAY_TEST_PROVIDER_KEY}"},
                      "gateway": {"auth": {"token": "${OPENCLAW_GATEWAY_TOKEN}"}}}
            gateway.write_json(root / "openclaw.json", config)
            report = {"plugin": {"id": "diagnostics-otel", "version": "2026.10.5", "enabled": True,
                                 "trustedOfficialInstall": True, "trust": {"reason": "trusted-official"}},
                      "install": {"source": "npm"}}

            def setup(command, output, env, state, key, timeout):
                saved = gateway.load_export(output / "openclaw.json")
                if key == "plugin_install":
                    self.assertIs(saved["plugins"]["entries"]["diagnostics-otel"]["enabled"], False)
                    saved["installer_added_field"] = {"preserved": True}
                    gateway.write_json(output / "openclaw.json", saved)
                report["plugin"]["enabled"] = saved["plugins"]["entries"]["diagnostics-otel"]["enabled"]
                path = key + ".stdout.json"
                (output / path).write_text(json.dumps(report))
                return {"stdout": path}

            with patch.object(gateway, "run_setup_command", side_effect=setup) as run, redirect_stdout(io.StringIO()):
                gateway.install_official_exporter(args, root, {}, manifest)
                install, disabled_inspect, enabled_inspect = [call.args[0] for call in run.call_args_list]
                self.assertIn("npm:@openclaw/diagnostics-otel@2026.10.5", install)
                self.assertIn("--pin", install)
                self.assertIn("--no-enable", install)
                self.assertNotIn("--force", install)
                self.assertNotIn("--runtime", disabled_inspect)
                self.assertEqual(disabled_inspect, enabled_inspect)
                self.assertTrue(manifest["plugin_inspect_disabled_verified"])
                self.assertTrue(manifest["plugin_inspect_verified"])
                self.assertTrue(manifest["plugin_trust_verified"])
                config["plugins"]["entries"]["diagnostics-otel"]["enabled"] = True
                config["installer_added_field"] = {"preserved": True}
                self.assertEqual(gateway.load_export(root / "openclaw.json"), config)

    def test_each_metadata_gate_rejects_bad_provenance_or_enablement_before_runtime(self):
        for stage, mismatch in [("plugin_inspect_disabled", "trust"), ("plugin_inspect_disabled", "enabled"),
                                ("plugin_inspect", "trust"), ("plugin_inspect", "enabled")]:
            with self.subTest(stage=stage, mismatch=mismatch), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                args = Namespace(node=root / "node", cli=root / "openclaw.mjs", install_timeout=90, inspect_timeout=30)
                manifest = {"runtime_version": "2026.10.5"}
                gateway.write_json(root / "openclaw.json", {"plugins": {"entries": {"diagnostics-otel": {"enabled": False}}}})
                def setup(command, output, env, state, key, timeout):
                    report = {"plugin": {"id": "diagnostics-otel", "version": "2026.10.5", "enabled": key == "plugin_inspect",
                        "trustedOfficialInstall": True, "trust": {"reason": "trusted-official"}}, "install": {"source": "npm"}}
                    if key == stage:
                        report["plugin"]["trustedOfficialInstall" if mismatch == "trust" else "enabled"] = (
                            False if mismatch == "trust" else not report["plugin"]["enabled"])
                    path = key + ".stdout.json"
                    (output / path).write_text(json.dumps(report))
                    return {"stdout": path}
                with patch.object(gateway, "run_setup_command", side_effect=setup) as run, redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, "trust/version/enablement"):
                        gateway.install_official_exporter(args, root, {}, manifest)
                self.assertFalse(manifest["plugin_trust_verified"])
                self.assertFalse(manifest[stage + "_verified"])
                self.assertEqual(run.call_count, 2 if stage == "plugin_inspect_disabled" else 3)
                self.assertEqual("plugin_config_enable" in manifest, stage == "plugin_inspect")

    def test_enabled_install_config_is_rejected_before_any_setup_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gateway.write_json(root / "openclaw.json", {"plugins": {"entries": {"diagnostics-otel": {"enabled": True}}}})
            with patch.object(gateway, "run_setup_command") as run:
                with self.assertRaisesRegex(RuntimeError, "explicit disabled"):
                    gateway.install_official_exporter(Namespace(), root, {}, {})
            run.assert_not_called()

    def test_install_failure_stops_before_gateway_or_model_and_preserves_logs(self):
        try:
            import opentelemetry.proto
        except ImportError:
            self.skipTest("optional requirements-otel.txt not installed")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GATEWAY_TEST_PROVIDER_KEY": "offline-secret"}):
            base = Path(directory)
            node, cli = base / "node", base / "openclaw.mjs"
            node.touch()
            cli.touch()
            (base / "package.json").write_text(json.dumps({"name": "openclaw", "version": "2026.10.5"}))
            with gateway.socket.socket() as port_probe:
                port_probe.bind(("127.0.0.1", 0))
                port = port_probe.getsockname()[1]
            process = Mock(wait=Mock(return_value=7), poll=Mock(return_value=7))
            with patch.object(gateway.subprocess, "Popen", return_value=process) as launch, redirect_stdout(io.StringIO()):
                result = gateway.main(["--node", str(node), "--cli", str(cli), "--output-dir", str(base / "run"),
                                       "--api-key-env", "GATEWAY_TEST_PROVIDER_KEY", "--port", str(port), "--cases", "case-01"])
            self.assertEqual(result, 1)
            self.assertEqual(launch.call_count, 1)
            manifest = json.loads((base / "run/manifest.json").read_text())
            config = json.loads((base / "run/openclaw.json").read_text())
            self.assertNotIn("load", config["plugins"])
            self.assertIs(config["plugins"]["entries"]["diagnostics-otel"]["enabled"], False)
            self.assertEqual(manifest["plugin_install"]["exit_code"], 7)
            self.assertEqual(manifest["gateway_shutdown"], "not_started")
            self.assertEqual(manifest["cases"][0]["execution_status"], "pending")
            self.assertTrue((base / "run" / manifest["plugin_install"]["stderr"]).is_file())
            child_env = launch.call_args.kwargs["env"]
            path_key = next(key for key in child_env if key.upper() == "PATH")
            self.assertEqual(child_env[path_key].split(os.pathsep)[0], str(base))
            self.assertEqual(Path(child_env["npm_config_cache"]), base / "run/state/npm-cache")

    def test_setup_timeout_is_recorded_and_child_tree_is_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            root, manifest = Path(directory), {}
            process = Mock(wait=Mock(side_effect=subprocess.TimeoutExpired("install", 5)),
                           poll=Mock(return_value=1), returncode=1)
            with patch.object(gateway.subprocess, "Popen", return_value=process), \
                 patch.object(gateway, "stop_process") as stop:
                with self.assertRaises(subprocess.TimeoutExpired):
                    gateway.run_setup_command(["node", "install"], root, {}, manifest, "plugin_install", 5)
            stop.assert_called_once_with(process)
            receipt = json.loads((root / "manifest.json").read_text())["plugin_install"]
            self.assertEqual(receipt["status"], "timeout")
            self.assertEqual(receipt["exit_code"], 1)

    def test_short_state_directory_is_fresh_and_outside_fixture_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "run"
            root.mkdir()
            state = gateway.create_state_directory(root, base / ".oc99")
            self.assertEqual(state, (base / ".oc99").resolve())
            self.assertTrue(state.is_dir())
            with self.assertRaises(FileExistsError):
                gateway.create_state_directory(root, state)
            for forbidden in (root / "workspace", root / "workspace/cache"):
                with self.subTest(path=forbidden), self.assertRaisesRegex(ValueError, "outside"):
                    gateway.create_state_directory(root, forbidden)
            self.assertFalse((root / "workspace").exists())

    def test_installed_runtime_version_is_recorded_without_running_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            cli = folder / "openclaw.mjs"
            for version in ("2026.9.9", "2026.10.5"):
                (folder / "package.json").write_text(json.dumps({"name": "openclaw", "version": version}))
                self.assertEqual(gateway.runtime_version(cli), version)

    def test_fixture_workspace_excludes_prompts_credentials_and_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = gateway.prepare_cases(root, gateway.scenarios()[:2])
            paths = {path.relative_to(root / "workspace").as_posix()
                     for path in (root / "workspace").rglob("*") if path.is_file()}
            self.assertEqual(paths, {"case-01/report.txt", "case-02/inventory.txt"})
            self.assertNotEqual(cases[0]["session_id"], cases[1]["session_id"])
            for case in cases:
                folder = root / "cases" / case["id"]
                task = json.loads((folder / "task.json").read_text())
                params = json.loads((folder / "rpc-params.json").read_text())
                self.assertEqual(params["message"], task["task"])
                self.assertFalse(params["deliver"])
                self.assertFalse(task["capture"]["complete"])
                self.assertNotIn("contract", task)
                self.assertIsNone(case["trace_id"])

    def test_binding_requires_exact_native_prompt_and_rejects_ambiguous_traces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = gateway.prepare_cases(root, gateway.scenarios()[:1])
            prompt = (root / "cases/case-01/prompt.txt").read_text()
            raw = exported()
            raw["spans"][0]["attributes"]["input.value"] = prompt
            spans = normalized(raw)
            save_capture(root, raw)
            gateway.assign_traces(root, cases, spans)
            self.assertEqual(cases[0]["trace_id"], TRACE)
            self.assertTrue((root / cases[0]["export"]).exists())
            # A model prompt/context is not an authorized run boundary.
            raw["spans"][0]["name"] = "openclaw.model.call"
            save_capture(root, raw)
            gateway.assign_traces(root, cases, normalized(raw))
            self.assertIsNone(cases[0]["trace_id"])
            raw["spans"][0]["name"] = "openclaw.run"
            second = exported(trace=OTHER_TRACE)
            second["spans"][0]["attributes"]["input.value"] = prompt
            save_capture(root, [raw, second])
            gateway.assign_traces(root, cases, normalized([raw, second]))
            self.assertIsNone(cases[0]["trace_id"])
            self.assertEqual(cases[0]["trace_binding"]["status"], "ambiguous")

    def test_rpc_has_explicit_gateway_identity_and_no_local_fallback(self):
        args = Namespace(node=Path("node"), cli=Path("openclaw.mjs"), port=18879)
        command = gateway.rpc_command(args, "agent", {"message": "Read file.", "deliver": False}, 235000, True)
        self.assertEqual(command[2:5], ["gateway", "call", "agent"])
        self.assertIn("--expect-url", command)
        self.assertIn("--expect-final", command)
        self.assertNotIn("--local", command)
        self.assertNotIn("--token", command)
        self.assertEqual(json.loads(command[command.index("--params") + 1])["message"], "Read file.")

    def test_two_tasks_on_one_trace_are_not_silently_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = gateway.prepare_cases(root, gateway.scenarios()[:2])
            rows = []
            for number, case in enumerate(cases, 1):
                prompt = (root / "cases" / case["id"] / "prompt.txt").read_text()
                rows.append(span(sid=f"{number:016x}", attributes={"input.value": prompt}))
            save_capture(root, {"spans": rows})
            gateway.assign_traces(root, cases, normalized({"spans": rows}))
            self.assertTrue(all(case["trace_id"] is None for case in cases))
            self.assertTrue(all(case["trace_binding"]["status"] == "shared_trace_requires_task_boundary_review" for case in cases))

    def test_shutdown_uses_node_stdin_signal_before_force_kill(self):
        process = Mock(poll=Mock(return_value=None), wait=Mock(return_value=0))
        with patch.object(gateway, "stop_process") as stop:
            self.assertEqual(gateway.shutdown_gateway(process), "graceful_signal")
            process.stdin.write.assert_called_once_with(b"prodebugger-shutdown\n")
            stop.assert_not_called()
        process = Mock(poll=Mock(return_value=None), wait=Mock(side_effect=subprocess.TimeoutExpired("gateway", 45)))
        with patch.object(gateway, "stop_process") as stop:
            self.assertEqual(gateway.shutdown_gateway(process), "forced_process_tree")
            stop.assert_called_once_with(process)

        process = Mock(poll=Mock(return_value=None), wait=Mock(return_value=17))
        with patch.object(gateway, "stop_process") as stop:
            self.assertEqual(gateway.shutdown_gateway(process), "nonzero_exit")
            stop.assert_not_called()

    def test_shutdown_audit_checks_logs_without_treating_warnings_as_failures(self):
        cases = [
            ("", None),
            ("[warn] optional component unavailable; continuing\n", None),
            ("[plugins/cleanup] Plugin diagnostics-otel forced retirement after 5000ms: "
             "1 still-running call(s); PRIVATE_SHUTDOWN_DETAIL\n", "exporter_forced_retirement"),
            ("[shutdown] failed in 22527ms: plugin/diagnostics-otel\n", "gateway_shutdown_failed"),
            ("[gateway] shutdown step failed (gateway close): PRIVATE_SHUTDOWN_DETAIL\n", "gateway_shutdown_step_failed"),
        ]
        for stderr, failure in cases:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "gateway.stdout.txt").write_bytes(b"[gateway] shutting down\n")
                (root / "gateway.stderr.txt").write_text(stderr, encoding="utf-8")
                result = gateway.audit_gateway_shutdown(root, "graceful_signal", 0)
                self.assertEqual(result["clean"], failure is None)
                self.assertEqual(result["reasons"], [] if failure is None else [failure])
                self.assertEqual(result["logs"][1]["sha256"], hashlib.sha256(
                    (root / "gateway.stderr.txt").read_bytes()).hexdigest())
                self.assertNotIn("PRIVATE_SHUTDOWN_DETAIL", json.dumps(result))

    def test_shutdown_audit_requires_gateway_exit_zero_and_both_raw_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("gateway.stdout.txt", "gateway.stderr.txt"):
                (root / name).write_bytes(b"")
            for code in (17, -9, None, False, "0"):
                with self.subTest(code=code):
                    result = gateway.audit_gateway_shutdown(root, "graceful_signal", code)
                    self.assertFalse(result["clean"])
                    reason = "gateway_exit_nonzero" if type(code) is int else "gateway_exit_code_unknown"
                    self.assertIn(reason, result["reasons"])
            self.assertFalse(gateway.audit_gateway_shutdown(root, "forced_process_tree", 0)["clean"])
            (root / "gateway.stderr.txt").unlink()
            result = gateway.audit_gateway_shutdown(root, "graceful_signal", 0)
            self.assertFalse(result["clean"])
            self.assertIn("gateway_shutdown_log_unavailable", result["reasons"])

    def test_cleanup_failure_after_exit_zero_prevents_successful_collection(self):
        try:
            import opentelemetry.proto
        except ImportError:
            self.skipTest("optional requirements-otel.txt not installed")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GATEWAY_TEST_PROVIDER_KEY": "offline-secret"}):
            base = Path(directory)
            node, cli = base / "node", base / "openclaw.mjs"
            node.touch()
            cli.touch()
            (base / "package.json").write_text(json.dumps({"name": "openclaw", "version": "2026.10.5"}))
            with gateway.socket.socket() as port_probe:
                port_probe.bind(("127.0.0.1", 0))
                port = port_probe.getsockname()[1]
            def ready(*args):
                save_capture(base / "run", exported())
                (base / "run/gateway.stderr.txt").write_text(
                    "[plugins/cleanup] Plugin diagnostics-otel forced retirement after 5000ms: cleanup remains pending.\n",
                    encoding="utf-8")
            process = Mock(wait=Mock(return_value=0), poll=Mock(return_value=None), returncode=0)
            with patch.object(gateway.subprocess, "Popen", return_value=process), \
                 patch.object(gateway, "install_official_exporter"), patch.object(gateway, "wait_ready", side_effect=ready), \
                 patch.object(gateway, "wait_for_native_capture", return_value=True), redirect_stdout(io.StringIO()):
                status = gateway.main(["--node", str(node), "--cli", str(cli), "--output-dir", str(base / "run"),
                    "--api-key-env", "GATEWAY_TEST_PROVIDER_KEY", "--port", str(port), "--cases", "case-01"])
            manifest = gateway.load_export(base / "run/manifest.json")
            self.assertEqual(status, 1)
            self.assertEqual(manifest["gateway_shutdown"], "graceful_signal")
            self.assertEqual(manifest["gateway_exit_code"], 0)
            self.assertFalse(manifest["gateway_shutdown_audit"]["clean"])
            self.assertIn("exporter_forced_retirement", manifest["gateway_shutdown_audit"]["reasons"])
            self.assertEqual(manifest["status"], "completed_with_errors")
            self.assertGreater(manifest["span_count"], 0)

    def test_startup_failure_preserves_pending_cases_and_never_writes_gateway_token(self):
        try:
            import opentelemetry.proto
        except ImportError:
            self.skipTest("optional requirements-otel.txt not installed")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GATEWAY_TEST_PROVIDER_KEY": "offline-provider-secret"}):
            root = Path(directory)
            node, cli, plugin = root / "node", root / "cli.mjs", root / "plugin"
            node.touch()
            cli.touch()
            plugin.mkdir()
            (root / "package.json").write_text(json.dumps({"name": "openclaw", "version": "2026.10.5"}))
            with gateway.socket.socket() as port_probe:
                port_probe.bind(("127.0.0.1", 0))
                port = port_probe.getsockname()[1]
            process = Mock(poll=Mock(return_value=None), wait=Mock(return_value=0))
            with patch.object(gateway.subprocess, "Popen", return_value=process) as launch, \
                 patch.object(gateway, "install_official_exporter"), \
                 patch.object(gateway, "wait_ready", side_effect=TimeoutError("no server")), \
                 redirect_stdout(io.StringIO()):
                status = gateway.main(["--node", str(node), "--cli", str(cli), "--plugin", str(plugin),
                                       "--output-dir", str(root / "run"), "--api-key-env", "GATEWAY_TEST_PROVIDER_KEY",
                                       "--port", str(port),
                                       "--cases", "case-01"])
            token = launch.call_args.kwargs["env"]["OPENCLAW_GATEWAY_TOKEN"]
            self.assertEqual(status, 1)
            manifest = json.loads((root / "run/manifest.json").read_text())
            self.assertEqual(manifest["error_type"], "TimeoutError")
            self.assertEqual(manifest["cases"][0]["execution_status"], "pending")
            for path in (root / "run").rglob("*"):
                if path.is_file():
                    self.assertNotIn(token.encode(), path.read_bytes())
                    self.assertNotIn(b"offline-provider-secret", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
