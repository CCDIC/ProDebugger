"""Collector orchestration checks: subprocess is mocked; no model is called."""
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from prodebugger import openclaw_collect as collect
from prodebugger.otel_receiver import make_server
from prodebugger.openclaw_evaluate import load_manifest


try:
    import opentelemetry.proto
    HAS_PROTO = True
except ImportError:
    HAS_PROTO = False


def options(root):
    return Namespace(node=root / "node.exe", cli=root / "openclaw.mjs", plugin=root / "plugin",
                     model="offline-mock", base_url="http://127.0.0.1:1/unused",
                     api_key_env="OPENCLAW_TEST_KEY", wall_timeout=120)


@unittest.skipUnless(HAS_PROTO, "optional requirements-otel.txt not installed")
class OpenClawCollectChecks(unittest.TestCase):
    def run_case(self, folder, process, *, stop=None):
        servers = []

        def real_server(*args, **kwargs):
            server = make_server(*args, **kwargs)
            servers.append(server)
            return server

        with patch.object(collect, "make_server", side_effect=real_server), \
             patch.object(collect.subprocess, "Popen", return_value=process) as launch, \
             patch.object(collect, "stop_process", side_effect=stop) as terminate, \
             redirect_stdout(io.StringIO()):
            result = collect.run_case(options(folder), folder, collect.scenarios()[0], folder / "state")
        for server in servers:
            self.assertEqual(server.fileno(), -1)
            with socket.socket() as probe:
                probe.settimeout(0.2)
                self.assertNotEqual(probe.connect_ex(server.server_address), 0)
        return result, launch, terminate

    def test_isolated_fixtures_and_environment_placeholder_survive_successful_mock_exit(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENCLAW_TEST_KEY": "test-secret-not-for-disk"}):
            root = Path(directory)
            result, launch, terminate = self.run_case(root, Mock(wait=Mock(return_value=0)))
            folder = root / "case-01"
            self.assertEqual((folder / "workspace/report.txt").read_bytes(), b"Revenue: 42\n")
            config = json.loads((folder / "openclaw.json").read_text())
            self.assertEqual(config["models"]["providers"]["lab"]["apiKey"], "${OPENCLAW_TEST_KEY}")
            self.assertEqual(config["tools"]["allow"], ["read"])
            self.assertTrue(config["tools"]["fs"]["workspaceOnly"])
            self.assertEqual(launch.call_args.kwargs["cwd"], folder / "workspace")
            self.assertEqual(launch.call_args.kwargs["env"]["OPENCLAW_CONFIG_PATH"], str(folder / "openclaw.json"))
            task = json.loads((folder / "task.json").read_text())
            self.assertFalse(task["capture"]["complete"])
            self.assertNotIn("contract", task)
            acceptance = json.loads((folder / "acceptance.json").read_text())
            self.assertEqual(acceptance["expected_path"], str(folder / "workspace/report.txt"))
            self.assertEqual(acceptance["expected_text"], "Revenue: 42\n")
            self.assertEqual(result["execution_status"], "exited")
            self.assertEqual(result["span_count"], 0)
            terminate.assert_not_called()
            for path in folder.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"test-secret-not-for-disk", path.read_bytes())

    def test_timeout_stops_created_process_closes_receiver_and_preserves_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = Mock(wait=Mock(side_effect=subprocess.TimeoutExpired(["mock"], 120)), returncode=-9)
            result, _, terminate = self.run_case(root, process)
            terminate.assert_called_once_with(process)
            self.assertEqual(result["execution_status"], "wall_timeout")
            self.assertTrue((root / "case-01/prompt.txt").exists())
            self.assertFalse(json.loads((root / "case-01/task.json").read_text())["capture"]["complete"])

    def test_capture_parse_error_keeps_raw_bytes_and_original_exit_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'{"resourceSpans": '

            def completed(**kwargs):
                (root / "case-01/capture/traces.jsonl").write_bytes(raw)
                return 0

            result, _, _ = self.run_case(root, Mock(wait=Mock(side_effect=completed)))
            self.assertEqual(result["execution_status"], "exited")
            self.assertEqual(result["capture_audit_error_type"], "JSONDecodeError")
            self.assertEqual((root / "case-01/capture/traces.jsonl").read_bytes(), raw)
            evidence = json.loads((root / "case-01/execution.json").read_text())
            self.assertIsNone(evidence["span_count"])
            self.assertTrue((root / "case-01/task.json").exists())

    def test_config_write_failure_closes_bound_receiver_before_thread_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_write = collect.write_json

            def write_except_config(path, value):
                if Path(path).name == "openclaw.json":
                    raise OSError("test configuration write failure")
                return real_write(path, value)

            with patch.object(collect, "write_json", side_effect=write_except_config):
                result, launch, _ = self.run_case(root, Mock())
            launch.assert_not_called()
            self.assertEqual(result["execution_status"], "launch_error")
            evidence = json.loads((root / "case-01/execution.json").read_text())
            self.assertEqual(evidence["error_type"], "OSError")
            self.assertTrue((root / "case-01/task.json").exists())

    def test_manifest_exists_before_case_and_retains_failure_without_exception_text(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENCLAW_TEST_KEY": "test-key"}):
            root = Path(directory)
            args = options(root)
            args.node.touch()
            args.cli.touch()
            args.plugin.mkdir()
            output = root / "capture-run"

            def fail_case(*unused):
                self.assertEqual(json.loads((output / "manifest.json").read_text())["status"], "started")
                raise RuntimeError("exception-containing-test-secret")

            with patch.object(collect, "run_case", side_effect=fail_case):
                code = collect.main(["--node", str(args.node), "--cli", str(args.cli), "--plugin", str(args.plugin),
                                     "--output-dir", str(output), "--api-key-env", "OPENCLAW_TEST_KEY", "--cases", "case-01"])
            manifest_text = (output / "manifest.json").read_text()
            manifest = json.loads(manifest_text)
            self.assertEqual(code, 1)
            self.assertEqual(manifest["status"], "completed_with_errors")
            self.assertEqual(manifest["cases"][0]["error_type"], "RuntimeError")
            self.assertNotIn("exception-containing-test-secret", manifest_text)
            self.assertEqual(load_manifest(output / "manifest.json")["cases"][0]["expected_status"], None)


if __name__ == "__main__":
    unittest.main()
