"""Safety workbench persistence and localhost API boundary checks."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from checks.test_otel_import import exported, normalized, task_spec, tool
from prodebugger.otel_import import convert
from prodebugger.record_bridge import read_json
from prodebugger.safety_workbench import Handler, SafetyHTTPServer, SafetyStore, default_policy


class SafetyWorkbenchChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SafetyStore(Path(self.temp.name) / "data")
        self.policy = default_policy("/workspace")
        self.source, _ = convert(normalized(exported(tool(arguments={"path": "/workspace/public.txt"}))), task_spec())

    def tearDown(self):
        self.temp.cleanup()

    def test_policy_change_creates_new_report_and_preserves_the_source_and_old_report(self):
        case = self.store.add_source(self.source, self.policy, title="read")
        self.assertEqual(case["status"], "safe")
        stored = self.store._find(case["id"])
        source_file = self.store._resolve(stored["source_path"])
        initial_report = self.store._resolve(stored["report_path"])
        source_bytes, report_bytes = source_file.read_bytes(), initial_report.read_bytes()
        policy = copy.deepcopy(self.policy)
        policy["forbidden_paths"] = ["/workspace/public.txt"]
        changed = self.store.reaudit(case["id"], policy)
        self.assertEqual(changed["report"]["status"], "unsafe")
        self.assertNotEqual(case["report"]["policy"]["digest"], changed["report"]["policy"]["digest"])
        self.assertEqual(source_file.read_bytes(), source_bytes)
        self.assertEqual(initial_report.read_bytes(), report_bytes)
        self.assertEqual(len(stored["history"]), 2)
        restored = self.store.reaudit(case["id"], self.policy)
        self.assertEqual(restored["report"]["status"], "safe")
        self.assertEqual(len(stored["history"]), 3)

    def test_restart_loads_current_policy_and_report_without_serving_raw_source(self):
        case = self.store.add_source(self.source, self.policy, title="read")
        reopened = SafetyStore(self.store.folder)
        state = reopened.state()
        self.assertEqual(state["summary"]["total"], 1)
        self.assertEqual(state["cases"][0]["id"], case["id"])
        self.assertNotIn("source_path", state["cases"][0])
        self.assertNotIn("source", state["cases"][0])

    def test_invalid_policy_does_not_create_a_saved_case(self):
        with self.assertRaises(ValueError):
            self.store.add_source(self.source, {"schema": "unknown"}, title="bad")
        self.assertEqual(self.store.state()["summary"]["total"], 0)

    def test_only_completed_active_demo_batch_is_visible_while_old_files_remain(self):
        old = self.store.add_source(self.source, self.policy, title="old", origin="controlled_tool_runtime", case_id="batch-old-case")
        self.store.index["active_run"] = "batch-old"
        new = self.store.add_source(self.source, self.policy, title="new", origin="controlled_tool_runtime", case_id="batch-new-case")
        self.assertEqual([c["id"] for c in self.store.state()["cases"]], [old["id"]])
        self.store.index["active_run"] = "batch-new"
        state = self.store.state()
        self.assertEqual([c["id"] for c in state["cases"]], [new["id"]])
        self.assertEqual(state["archived_cases"], 1)
        self.assertTrue(self.store._resolve(self.store._find(old["id"])["report_path"]).is_file())

    def test_http_rejects_cross_origin_and_static_path_escape(self):
        server = SafetyHTTPServer(("127.0.0.1", 0), Handler)
        server.store = self.store
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            body = json.dumps({"source": self.source, "policy": self.policy, "title": "api"}).encode()
            request = Request(base + "/api/safety/import", data=body,
                              headers={"Content-Type": "application/json", "Origin": "https://unrelated.invalid"})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=10)
            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(self.store.state()["summary"]["total"], 0)
            request = Request(base + "/api/safety/import", data=body,
                              headers={"Content-Type": "application/json", "Origin": base})
            with urlopen(request, timeout=10) as response:
                self.assertEqual(response.status, 201)
                result = json.load(response)
            self.assertEqual(result["report"]["status"], "safe")
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + "/%2e%2e/%2e%2e/prodebugger/safety_workbench.py", timeout=10)
            self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__ == "__main__":
    unittest.main()
