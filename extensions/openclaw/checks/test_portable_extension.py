"""Standalone entry point/configuration regressions; no model or capture calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

EXTENSION = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("openclaw_entry", EXTENSION / "run.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class PortableExtensionChecks(unittest.TestCase):
    def test_explicit_config_and_env_only(self):
        from prodebugger.diagnosis_jobs import model_configuration
        with patch.dict(os.environ, {}, clear=True):
            cfg, view = model_configuration()
            self.assertFalse(view["configured"])
            self.assertIsNone(view["configuration_source"])
            self.assertIsNone(cfg["env_file"])
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "config.json"
                path.write_text(json.dumps({"model": "explicit-model", "env_file": "keys.env"}))
                (Path(folder) / "keys.env").write_text("OPENAI_API_KEY=test-placeholder\n")
                cfg, view = model_configuration(path)
                self.assertTrue(view["configured"])
                self.assertTrue(view["credential_available"])
                self.assertEqual(cfg["env_file"], str(Path(folder) / "keys.env"))
                self.assertNotIn("test-placeholder", json.dumps(view))
                with patch.dict(os.environ, {"PRODEBUGGER_MODEL": "env-model"}):
                    self.assertEqual(model_configuration(path)[0]["model"], "env-model")

    def test_imports_resolve_inside_extension_from_repo_root(self):
        script = ("import run; run.bootstrap(); import prodebugger, record; "
                  "from pathlib import Path; "
                  "assert Path(prodebugger.__file__).resolve().is_relative_to(run.EXTENSION); "
                  "assert Path(record.__file__).resolve().is_relative_to(run.EXTENSION)")
        result = subprocess.run([sys.executable, "-B", "-c",
            "import sys; sys.path.insert(0, " + repr(str(EXTENSION)) + "); " + script],
            cwd=EXTENSION.parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_http_startup_does_not_start_workers_or_capture(self):
        from prodebugger.safety_workbench import Handler, SafetyHTTPServer, SafetyStore
        with tempfile.TemporaryDirectory() as folder:
            store = SafetyStore(folder)
            server = SafetyHTTPServer(("127.0.0.1", 0), Handler)
            server.store = store
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}"
                self.assertEqual(urlopen(url + "/studio.html").status, 200)
                state = json.load(urlopen(url + "/api/safety/state"))
                self.assertEqual(state["cases"], [])
                self.assertIsNone(store._diagnoses)
                self.assertIsNone(store._risk_jobs)
                self.assertEqual(store.jobs, {})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_seed_import_is_idempotent_without_model_or_capture(self):
        from checks.test_openclaw_profile import source
        from prodebugger.safety_workbench import SafetyStore, default_policy
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            example = root / "example"
            example.mkdir()
            (example / "trajectory.json").write_text(json.dumps(source()))
            (example / "policy.json").write_text(json.dumps(default_policy("/workspace")))
            store = SafetyStore(root / "data")
            self.assertTrue(entry.seed_example(store, example)["imported"])
            self.assertFalse(entry.seed_example(store, example)["imported"])
            self.assertEqual(len(store.index["cases"]), 1)
            self.assertIsNone(store._diagnoses)
            self.assertIsNone(store._risk_jobs)


if __name__ == "__main__":
    unittest.main()
