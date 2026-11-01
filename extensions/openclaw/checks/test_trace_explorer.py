"""Counterexamples for graph provenance/coloring, sampling, and live stubs."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from checks.test_openclaw_contract import source as several_calls
from checks import test_parsed_view as parsed_checks
from checks.test_parsed_view import CASE, file_hashes
from checks.test_safety_audit import capture, policy
from prodebugger.record_bridge import build_bundle, write_json
from prodebugger.safety_audit import audit_source
from prodebugger.safety_workbench import Handler, SafetyHTTPServer
from prodebugger.trace_explorer import graph_source, integrations, sample_cases


def graph(source=None):
    source = source or capture()
    return graph_source(source, audit_source(source, policy()), {**CASE, "policy": policy()})


def node(graph, entity_id):
    return next(n for n in graph["nodes"] if n["entity_id"] == entity_id)


class TraceExplorerChecks(unittest.TestCase):
    def test_nodes_are_exact_record_entities_and_record_unmodified(self):
        data = capture()
        result = graph(data)
        self.assertEqual(result["record"], build_bundle(data, online=False).snapshot())
        self.assertEqual({n["entity_id"] for n in result["nodes"]}, set(result["record"]["entities"]))
        for n in result["nodes"]:
            self.assertEqual(n["id"], CASE["id"] + "::" + n["entity_id"])
            self.assertEqual({"kind": n["kind"], "index": n["index"]}, result["record"]["entities"][n["entity_id"]])

    def test_edges_are_only_actual_fact_argument_links(self):
        result = graph()
        facts = {f["fid"]: f for f in result["record"]["facts"]}
        expected = sum(max(0, len(f["args"]) - 1) for f in facts.values())
        self.assertEqual(len(result["edges"]), expected)
        for edge in result["edges"]:
            fact = facts[edge["fid"]]
            self.assertEqual(edge["relation"], fact["rel"])
            self.assertEqual(edge["step"], fact["t"])
            self.assertEqual(edge["source_arg"], 0)
            for end, index in (("source", edge["source_arg"]), ("target", edge["target_arg"])):
                arg = fact["args"][index]
                self.assertEqual(edge[end], CASE["id"] + "::" + arg["kind"] + ":" + arg["index"])
        for fact in facts.values():
            if len(fact["args"]) == 1:
                key = fact["args"][0]["kind"] + ":" + fact["args"][0]["index"]
                self.assertIn(fact["fid"], node(result, key)["fids"])
                self.assertFalse(any(edge["fid"] == fact["fid"] for edge in result["edges"]))

    def test_safe_behavior_does_not_color_structural_entities(self):
        result = graph()
        self.assertEqual(node(result, "call:trace-1/span-1")["status"], "safe")
        self.assertEqual(node(result, "tool:read_file")["status"], "neutral")
        for n in result["nodes"]:
            if n["kind"] not in {"call", "tool"}:
                self.assertEqual(n["status"], "neutral")
        self.assertEqual(node(result, "agent:self")["status"], "neutral")

    def test_unsafe_is_local_and_mixed_tool_preserves_all_action_steps(self):
        data = several_calls([
            {"action": {"arguments": {"path": "/workspace/notes.txt"}}},
            {"action": {"arguments": {"path": "/workspace/.env"}}},
        ])
        result = graph(data)
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(node(result, "call:trace-1/span-1")["status"], "safe")
        self.assertEqual(node(result, "call:trace-1/span-2")["status"], "unsafe")
        tool = node(result, "tool:read_file")
        self.assertEqual(tool["status"], "neutral")
        self.assertEqual(tool["action_steps"], [1, 2])
        self.assertIn("分别评估", tool["reason"])
        red = {n["id"] for n in result["nodes"] if n["status"] == "unsafe"}
        linked = {nid for warning in result["warnings"] if warning["type"] == "policy_violation" for nid in warning["node_ids"]}
        self.assertEqual(red, linked)

    def test_unknown_related_call_prevents_tool_from_appearing_safe(self):
        data = several_calls([
            {"action": {"arguments": {"path": "/workspace/notes.txt"}}},
            {"action": {"arguments": {"path": None}}},
        ])
        result = graph(data)
        self.assertEqual(node(result, "call:trace-1/span-1")["status"], "safe")
        self.assertEqual(node(result, "call:trace-1/span-2")["status"], "unknown")
        self.assertEqual(node(result, "tool:read_file")["status"], "neutral")

    def test_global_capture_limit_does_not_override_completed_local_checks(self):
        result = graph(capture(complete=False))
        self.assertTrue(all(n["status"] == "safe" for n in result["nodes"] if n["role"] == "behavior"))
        self.assertTrue(any(w["type"] == "coverage_gap" for w in result["warnings"]))
        result = graph(capture(args={"path": "/workspace/.env"}, complete=False))
        self.assertEqual(node(result, "call:trace-1/span-1")["status"], "unsafe")

    def test_stale_report_cannot_supply_flags_or_colors(self):
        data = capture(args={"path": "/workspace/.env"})
        report = audit_source(data, policy())
        report["source"]["sha256"] = "stale"
        result = graph_source(data, report, CASE)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["report_matches_source"])
        self.assertTrue(all(n["status"] == "unknown" and not n["flag_ids"] for n in result["nodes"] if n["role"] == "behavior"))
        self.assertTrue(any(w["type"] == "source_mismatch" for w in result["warnings"]))

    def test_tampered_query_proof_cannot_turn_nodes_red(self):
        data = capture(args={"path": "/workspace/.env"})
        for mutation in (lambda r: r["flags"][0]["evidence"][0].update(text="invented"),
                         lambda r: r["queries"][0]["result"].update(partial=True),
                         lambda r: r["flags"][0]["evidence"][0].update(fid=9999)):
            report = audit_source(data, policy())
            mutation(report)
            result = graph_source(data, report, CASE)
            self.assertFalse(any(n["status"] == "unsafe" for n in result["nodes"]))
            self.assertTrue(any(w["type"] == "evidence_unavailable" for w in result["warnings"]))

    def test_one_invalid_flag_does_not_hide_another_confirmed_violation(self):
        data = several_calls([
            {"action": {"arguments": {"path": "/workspace/.env"}}},
            {"action": {"arguments": {"path": "/outside/private.txt"}}},
        ])
        report = audit_source(data, policy())
        report["flags"][1]["evidence"][0]["text"] = "invalid"
        result = graph_source(data, report, CASE)
        self.assertEqual(result["status"], "unsafe")
        self.assertEqual(node(result, "call:trace-1/span-1")["status"], "unsafe")
        self.assertEqual(node(result, "call:trace-1/span-2")["status"], "unknown")
        self.assertTrue(any(w["type"] == "evidence_unavailable" for w in result["warnings"]))

    def test_case_qualified_ids_do_not_merge_equal_entities(self):
        data = capture()
        report = audit_source(data, policy())
        first = graph_source(data, report, CASE)
        second = graph_source(data, report, {**CASE, "id": "another-case"})
        self.assertFalse({n["id"] for n in first["nodes"]} & {n["id"] for n in second["nodes"]})

    def pool(self):
        return [{"id": f"case-{i:02d}", "title": "public read" if i % 2 else "restricted write",
                 "origin": "historical_openclaw" if i % 3 else "user_import",
                 "status": ("safe", "unsafe", "unknown")[i % 3]} for i in range(18)]

    def test_sample_seed_is_reproducible_uniform_without_replacement(self):
        first = sample_cases(self.pool(), {"count": 10, "seed": "fixed"})
        second = sample_cases(list(reversed(self.pool())), {"count": 10, "seed": "fixed"})
        self.assertEqual(first, second)
        self.assertEqual(len(set(first["case_ids"])), 10)
        self.assertEqual(first["eligible_count"], 18)
        self.assertEqual(first["method"], "uniform_without_replacement")
        self.assertEqual(sample_cases(self.pool(), {"count": 24})["selected_count"], 18)
        self.assertTrue(sample_cases(self.pool(), {"count": 24})["pool_exhausted"])

    def test_sampling_filters_and_empty_pool_are_explicit(self):
        request = {"count": 24, "seed": "fixed", "origins": ["historical_openclaw"], "statuses": ["unsafe"], "query": "PUBLIC"}
        result = sample_cases(self.pool(), request)
        expected = {c["id"] for c in self.pool() if c["origin"] == "historical_openclaw" and c["status"] == "unsafe" and "public" in c["title"]}
        self.assertEqual(set(result["case_ids"]), expected)
        self.assertEqual(result["filters"]["query_fields"], ["case_id", "title"])
        self.assertEqual(sample_cases(self.pool(), {"count": 1, "origins": []})["selected_count"], 0)
        self.assertEqual(sample_cases([], {"count": 1})["eligible_count"], 0)

    def test_invalid_sample_fields_types_and_enums_are_rejected(self):
        for request in ({}, {"count": True}, {"count": 0}, {"count": 25}, {"count": 1, "seed": "x" * 65},
                        {"count": 1, "seed": 1}, {"count": 1, "origins": ["random"]},
                        {"count": 1, "statuses": ["all"]}, {"count": 1, "query": []},
                        {"count": 1, "path": "../input.json"}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                sample_cases(self.pool(), request)

    def test_http_graph_sample_and_stubs_are_readonly_and_archives_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, _ = parsed_checks.ParsedViewChecks().fixture(root)
            case = store.index["cases"][0]
            for case_id, run in (("old-archive", "old"), ("active-visible", "active")):
                store.index["cases"].append({**case, "id": case_id, "origin": "controlled_tool_runtime"})
            store.index["active_run"] = "active"
            store._save()
            before = file_hashes(root)
            server = SafetyHTTPServer(("127.0.0.1", 0), Handler)
            server.store = store
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:" + str(server.server_address[1])
            def post(path, value):
                return urlopen(Request(base + path, data=json.dumps(value).encode(), headers={"Content-Type": "application/json"}), timeout=10)
            try:
                with urlopen(base + "/api/safety/graph/visible-case", timeout=10) as response:
                    self.assertEqual(json.load(response)["schema"], "prodebugger-trace-graph/v1")
                with self.assertRaises(HTTPError) as missing:
                    urlopen(base + "/api/safety/graph/not-found", timeout=10)
                self.assertEqual(missing.exception.code, 404)
                with post("/api/safety/sample", {"count": 24, "seed": "same"}) as response:
                    sample = json.load(response)
                    self.assertEqual(set(sample["case_ids"]), {"visible-case", "active-visible"})
                with post("/api/safety/sample", {"count": 24, "seed": "same"}) as response:
                    self.assertEqual(json.load(response), sample)
                with self.assertRaises(HTTPError) as invalid:
                    post("/api/safety/sample", {"count": 100})
                self.assertEqual(invalid.exception.code, 400)
                with urlopen(base + "/api/safety/integrations", timeout=10) as response:
                    settings = json.load(response)
                self.assertEqual(settings, integrations())
                self.assertTrue(all(not c["implemented"] and c["status"] == "not_connected" for c in settings["connections"]))
                with self.assertRaises(HTTPError) as not_implemented:
                    post("/api/safety/live/events", {"secret": "not-stored"})
                self.assertEqual(not_implemented.exception.code, 501)
                stub = json.load(not_implemented.exception)
                self.assertFalse(stub["accepted"])
                self.assertFalse(stub["stored"])
                self.assertEqual(file_hashes(root), before)
                self.assertFalse(store.jobs)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
