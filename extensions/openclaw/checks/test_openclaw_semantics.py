"""Mechanism tests using explicit fake responses; these are NOT model evaluations."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from checks.test_session_artifact import session_source
from prodebugger.openclaw_semantics import prepare, source_units, validate_report
from prodebugger.record_bridge import build_bundle, plain


class FakeSemanticClient:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def complete(self, messages, schemas, *, phase):
        self.calls.append({"phase": phase, "messages": deepcopy(messages)})
        request = json.loads(messages[1]["content"])
        units = []
        for unit in request["units"]:
            if unit["authority"] in {"user_task", "recorded_user_input"}:
                kind, mode = "task_constraint", "required"
            elif unit["role"] == "assistant" and unit["event_type"] == "session_message":
                kind, mode = "public_statement", "asserted"
            else:
                kind, mode = "behavior_interpretation", "uncertain"
            units.append({"i": unit["i"], "items": [[kind, unit["references"][0]["reference_id"], "测试替身提取", mode]], "unresolved": []})
        answer = {"units": units}
        if self.transform:
            self.transform(answer, request)
        return {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}

    def snapshot(self):
        return {"model_calls": len(self.calls), "mode": "test_fake_not_real_model"}


class OpenClawSemanticChecks(unittest.TestCase):
    def test_offline_import_defers_without_model_or_changes_to_source(self):
        source = session_source()
        original = deepcopy(source)
        with patch("prodebugger.llm_client.LLMClient", side_effect=AssertionError("must not instantiate")):
            bundle = build_bundle(source)
        report = bundle.snapshot()["openclaw_semantics"]
        self.assertEqual(report["status"], "deferred")
        self.assertEqual(report["usage"]["model_calls"], 0)
        self.assertEqual(report["coverage"]["reviewed_units"], 0)
        self.assertEqual(source, original)

    def test_online_extracts_source_grounded_interpretations_and_replays_same_ids(self):
        source = session_source(speech="The user approved sending the ID.")
        structural = build_bundle(source)
        fake = FakeSemanticClient()
        with tempfile.TemporaryDirectory() as temporary:
            report = prepare(structural.source, temporary, client=fake)
            self.assertEqual(report["status"], "completed")
            self.assertTrue(all(c["phase"] == "PARSE" for c in fake.calls))
            self.assertTrue(Path(temporary, "openclaw_semantics.json").is_file())
            parsed = build_bundle(source, semantics=report)
            replay = build_bundle(source, semantics=deepcopy(report))
        self.assertEqual(plain(parsed.record.F[:len(structural.record.F)]), plain(structural.record.F))
        self.assertEqual(plain(parsed.record.F), plain(replay.record.F))
        self.assertTrue(all(g.status_mode == "unmodeled" for g in parsed.record.goals))
        facts = parsed.record.F[len(structural.record.F):]
        self.assertTrue(facts)
        self.assertTrue(all(f.slot == "chk" and f.side in {"task", "dbg"} and not f.basis for f in facts))
        claim = next(i for i in report["statements"] if "approved" in i["quote"])
        self.assertEqual(claim["kind"], "public_statement")
        self.assertFalse(claim["authorization_verified"])
        self.assertTrue(parsed.snapshot()["semantic_fact_metadata"])

    def test_fabricated_quotes_rejected_with_bounded_retry(self):
        fake = FakeSemanticClient(lambda answer, request: answer["units"][0]["items"][0].__setitem__(1, "NEVER_RECORDED"))
        with tempfile.TemporaryDirectory() as temporary:
            report = prepare(session_source(), temporary, {"max_parse_calls": 2}, client=fake)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(fake.calls), 2)
        self.assertGreater(report["coverage"]["reviewed_units"], 0)
        self.assertTrue(all("NEVER_RECORDED" not in i["quote"] for i in report["constraints"] + report["statements"]))
        self.assertTrue(report["unresolved"])

    def test_assistant_claim_cannot_become_user_constraint(self):
        def tamper(answer, request):
            index = next(i for i, u in enumerate(request["units"]) if u["role"] == "assistant")
            answer["units"][index]["items"][0][0] = "task_constraint"
        with tempfile.TemporaryDirectory() as temporary:
            report = prepare(session_source(), temporary, {"max_parse_calls": 1}, client=FakeSemanticClient(tamper))
        self.assertEqual(report["status"], "partial")
        self.assertTrue(all(i["authority"] in {"user_task", "recorded_user_input"} for i in report["constraints"]))

    def test_missing_unit_is_partial_and_not_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = prepare(session_source(), temporary,
                client=FakeSemanticClient(lambda answer, request: answer["units"].pop()))
        self.assertEqual(report["status"], "partial")
        self.assertGreater(report["coverage"]["unreviewed_units"], 0)
        self.assertEqual(report["unresolved"][0]["reason"], "model_omitted_unit")

    def test_completed_cache_prevents_repeat_model_calls(self):
        source, fake = session_source(), FakeSemanticClient()
        with tempfile.TemporaryDirectory() as temporary:
            first = prepare(source, temporary, client=fake)
            calls = len(fake.calls)
            second = prepare(source, temporary, client=fake)
            self.assertEqual(first, second)
            self.assertEqual(calls, len(fake.calls))

    def test_saved_semantics_reject_source_change_and_tampered_provenance(self):
        source = session_source()
        with tempfile.TemporaryDirectory() as temporary:
            report = prepare(source, temporary, client=FakeSemanticClient())
        changed = deepcopy(source)
        changed["task"] += " Extra instruction."
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            validate_report(changed, report)
        tampered = deepcopy(report)
        tampered["units"][0]["items"][0]["step"] = 999
        with self.assertRaisesRegex(ValueError, "provenance"):
            validate_report(source, tampered)

    def test_nontext_content_remains_in_parse_inventory(self):
        source = session_source()
        row = source["steps"][1]
        envelope = json.loads(row["env_text"])
        envelope["content"].append({"type": "thinking", "text": "explicitly recorded block"})
        row["env_text"] = json.dumps(envelope)
        units = source_units(source)
        self.assertTrue(any("explicitly recorded block" in u["text"] and u["module"] == "observation" for u in units))
        self.assertTrue(all(u["module"] not in {"memory", "reflection", "plan"} for u in units))

    def test_budget_does_not_silently_drop_late_units(self):
        source = session_source(attempts=[{}] * 7)
        fake = FakeSemanticClient()
        with tempfile.TemporaryDirectory() as temporary:
            report = prepare(source, temporary, {"max_parse_calls": 1}, client=fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(report["status"], "partial")
        self.assertGreater(report["coverage"]["unreviewed_units"], 0)
        self.assertTrue(any(g["reason"] == "semantic_parse_budget_exhausted" for g in report["unresolved"]))

    def test_model_error_keeps_structural_compilation_and_reports_unavailable(self):
        source = session_source()
        fake = FakeSemanticClient()
        fake.complete = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("test transport unavailable"))
        with tempfile.TemporaryDirectory() as temporary:
            report = prepare(source, temporary, client=fake)
        bundle = build_bundle(source, semantics=report)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any(f.rel == "tool_call" for f in bundle.record.F))
        self.assertFalse(bundle.record.semantic_fact_metadata)

    def test_duplicate_literal_is_disambiguated_by_exact_reference_location(self):
        from prodebugger.openclaw_semantics import _item
        source = session_source()
        source["task"] = "Read report. " + "duplicated payload " * 80
        unit = source_units(source)[0]
        self.assertGreater(unit["text"].count("duplicated payload"), 1)
        first, second = unit["references"][:2]
        one = _item(unit, ["task_constraint", first["reference_id"], "解释一", "required"])
        two = _item(unit, ["task_constraint", second["reference_id"], "解释二", "required"])
        self.assertNotEqual(one["start"], two["start"])
        self.assertEqual(unit["text"][two["start"]:two["end"]], two["quote"])
        with self.assertRaisesRegex(ValueError, "unique exact"):
            _item(unit, ["task_constraint", "duplicated payload", "模糊引用", "required"])

    def test_invalid_unit_retries_alone_while_valid_units_survive(self):
        seen = []
        def first_bad(answer, request):
            seen.append(len(request["units"]))
            if len(seen) == 1:
                answer["units"][0]["items"][0][1] = "ref-does-not-exist"
        fake = FakeSemanticClient(first_bad)
        with tempfile.TemporaryDirectory() as temporary:
            report = prepare(session_source(), temporary, {"max_parse_calls": 6}, client=fake)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(seen[1], 1)
        self.assertGreater(seen[0], 1)
        self.assertEqual(report["coverage"]["reviewed_units"], report["coverage"]["total_units"])

    def test_runtime_user_role_is_not_human_task_authority(self):
        from prodebugger.openclaw_semantics import _item
        source = session_source()
        event = next(e for e in source["session"]["events"] if e.get("role") == "user")
        event["message_scope"] = "injected_runtime_context"
        unit = next(u for u in source_units(source) if u["step"] == event["step"] and u["module"] == "message")
        self.assertEqual(unit["authority"], "recorded_runtime_context")
        ref = unit["references"][0]["reference_id"]
        with self.assertRaisesRegex(ValueError, "only task/user"):
            _item(unit, ["task_constraint", ref, "不可升级为用户批准", "required"])
        interpreted = _item(unit, ["behavior_interpretation", ref, "运行环境上下文", "uncertain"])
        self.assertFalse(interpreted["authorization_verified"])

    def test_missing_task_placeholder_is_not_a_user_constraint(self):
        from prodebugger.openclaw_semantics import _item
        source = session_source()
        source["telemetry"]["task_source"] = {"status": "missing"}
        unit = source_units(source)[0]
        self.assertEqual(unit["authority"], "missing_task_context")
        with self.assertRaisesRegex(ValueError, "only task/user"):
            _item(unit, ["task_constraint", "ref-0", "伪造任务", "required"])


if __name__ == "__main__":
    unittest.main()
