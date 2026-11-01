"""Verify that the opt-in parsing view exposes the existing IR without edits."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from checks.test_safety_audit import capture, policy
from prodebugger import parsed_view
from prodebugger.record_bridge import build_bundle, digest, read_json, write_json
from prodebugger.safety_audit import audit_source
from prodebugger.safety_workbench import Handler, SafetyHTTPServer, SafetyStore


CASE = {"id": "visible-case", "title": "Controlled read", "origin": "user_import",
        "origin_label": "用户导入", "created_at": "2026-10-22T00:00:00Z"}


def file_hashes(folder):
    return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*")) if path.is_file()}


class ParsedViewChecks(unittest.TestCase):
    def test_record_is_exact_original_snapshot_and_counts_match(self):
        source = capture(args={"path": "/workspace/.env"})
        report = audit_source(source, policy())
        original = copy.deepcopy((source, report, CASE))
        expected = build_bundle(source, online=False).snapshot()
        with patch.object(parsed_view, "build_bundle", wraps=build_bundle) as compiler:
            view = parsed_view.parsed_source(source, report, CASE)
        compiler.assert_called_once_with(source, online=False)
        self.assertEqual(view["record"], expected)
        self.assertEqual(view["source_sha256"], digest(source))
        self.assertEqual(view["snapshot_sha256"], digest(expected))
        self.assertFalse(view["compiler"]["online"])
        self.assertEqual(view["compiler"]["entry"], "build_bundle")
        self.assertEqual(view["counts"], {"entities": len(expected["entities"]),
                         "relations": len(expected["relations"]), "facts": len(expected["facts"]),
                         "goals": len(expected["goals"]), "steps": len(source["steps"])})
        self.assertEqual((source, report, CASE), original)

    def test_missing_tool_result_body_and_mental_modules_stay_missing(self):
        source = capture(response={"status": "error", "error": {"message": "ENOENT"}})
        envelope = json.loads(source["steps"][1]["env_text"])
        del envelope["result"]
        source["steps"][1]["env_text"] = json.dumps(envelope)
        report = audit_source(source, policy())
        view = parsed_view.parsed_source(source, report, CASE)
        observed = next(f for f in view["record"]["facts"] if f["rel"] == "tool_result")
        self.assertNotIn("result", json.loads(observed["value"]))
        original_observation = view["record"]["source_steps"]["2"]["observation"]
        self.assertEqual(original_observation, source["steps"][1]["env_text"])
        self.assertNotIn("result", json.loads(original_observation))
        for step in view["record"]["source_steps"].values():
            for key in ("memory", "reflection", "plan"):
                self.assertIsNone(step["modules"][key])

    def test_fact_entity_and_relation_references_are_original_and_resolvable(self):
        source = capture()
        view = parsed_view.parsed_source(source, audit_source(source, policy()), CASE)
        record = view["record"]
        fids = {f["fid"] for f in record["facts"]}
        for fact in record["facts"]:
            self.assertIn(fact["rel"], record["relations"])
            for entity in fact["args"] + ([fact["purpose"]] if fact["purpose"] else []):
                key = entity["kind"] + ":" + entity["index"]
                self.assertEqual(record["entities"][key], entity)
            self.assertTrue(set(fact["basis"]) <= fids)
            if fact["situation"] is not None:
                self.assertIn(fact["situation"], fids)
        for step in source["steps"]:
            self.assertEqual(record["source_steps"][str(step["t"])]["observation"], step["env_text"])

    def test_safety_flags_are_separate_links_not_added_parse_facts(self):
        source = capture(args={"path": "/workspace/.env"})
        report = audit_source(source, policy())
        empty = {"source": report["source"], "flags": []}
        without = parsed_view.parsed_source(source, empty, CASE)
        with_flags = parsed_view.parsed_source(source, report, CASE)
        self.assertEqual(without["record"], with_flags["record"])
        self.assertEqual(without["snapshot_sha256"], with_flags["snapshot_sha256"])
        self.assertFalse(without["safety_links"])
        link = with_flags["safety_links"][0]
        flag = report["flags"][0]
        self.assertEqual(link["fids"], sorted({e["fid"] for e in flag["evidence"] + flag["effect_evidence"]}))
        self.assertEqual(link["flag_id"], flag["id"])
        self.assertFalse(link["part_of_original_parse"])
        self.assertEqual(link["source"], "saved_safety_report")

    def test_stale_report_is_not_attached_to_current_parse(self):
        source = capture()
        report = audit_source(source, policy())
        report["source"]["sha256"] = "different-source"
        view = parsed_view.parsed_source(source, report, CASE)
        self.assertFalse(view["safety_links"])
        self.assertTrue(view["warnings"])
        self.assertEqual(view["record"], build_bundle(source, online=False).snapshot())

    def fixture(self, folder):
        source = capture(args={"path": "/workspace/.env"})
        report = audit_source(source, policy())
        case = {**CASE, "policy": policy(), "source_path": "saved/input.json",
                "report_path": "saved/report.json", "history": ["saved/report.json"]}
        write_json(folder / "saved/input.json", source)
        write_json(folder / "saved/report.json", report)
        write_json(folder / "index.json", {"schema": "safety-workbench-index/v1", "cases": [case],
                                          "active_run": None, "policy": policy()})
        return SafetyStore(folder), source

    def test_store_read_leaves_all_input_index_and_report_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, source = self.fixture(root)
            before = file_hashes(root)
            state_before = store.state()
            with patch.object(store, "start_demo", side_effect=AssertionError("must not capture")), \
                 patch.object(store, "reaudit", side_effect=AssertionError("must not reaudit")):
                view = store.parsed(CASE["id"])
                self.assertEqual(store.parsed(CASE["id"]), view)
            self.assertEqual(view["record"], build_bundle(source, online=False).snapshot())
            self.assertEqual(file_hashes(root), before)
            self.assertEqual(store.state(), state_before)
            self.assertNotIn("record", state_before["cases"][0])

    def test_http_success_unknown_case_404_and_no_client_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, source = self.fixture(root)
            server = SafetyHTTPServer(("127.0.0.1", 0), Handler)
            server.store = store
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:" + str(server.server_address[1])
            before = file_hashes(root)
            try:
                with urlopen(base + "/api/safety/parsed/visible-case", timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    view = json.load(response)
                self.assertEqual(view["record"], build_bundle(source, online=False).snapshot())
                for suffix in ("does-not-exist", "../../saved/input.json", "%2E%2E%2Fsaved%2Finput.json", "prefix/visible-case", ""):
                    with self.subTest(suffix=suffix), self.assertRaises(HTTPError) as failure:
                        urlopen(base + "/api/safety/parsed/" + suffix, timeout=10)
                    self.assertEqual(failure.exception.code, 404)
                # A query parameter cannot substitute a different saved path.
                with urlopen(base + "/api/safety/parsed/visible-case?source_path=../../secret.json", timeout=10) as response:
                    self.assertEqual(json.load(response)["source_sha256"], digest(source))
                self.assertEqual(file_hashes(root), before)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_registered_outside_source_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, _ = self.fixture(root)
            store.index["cases"][0]["source_path"] = "../outside.json"
            with self.assertRaises(ValueError):
                store.parsed(CASE["id"])


if __name__ == "__main__":
    unittest.main()
