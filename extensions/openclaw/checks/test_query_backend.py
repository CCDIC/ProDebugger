import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prodebugger.record_bridge import build_bundle, plain, write_json, compiler, PROFILES
from prodebugger.parse_audit import audit, _validate_response, prepare
from prodebugger.query_dsl import execute
from prodebugger.query_trace import packet, query, commit
from prodebugger.window_trace import FIELDS, commit as old_commit
from prodebugger.debug_tools import DebugTools
from prodebugger.direct_submit import packet as submit_packet
from prodebugger.debugger_agent import DebuggerAgent
from prodebugger.llm_client import ReplayClient, json_lines
from prodebugger.run_experiment import DEFAULTS


def source():
    env = ["You pick up the apple 1 from the countertop 1.", "Nothing happens.", "You are carrying: an apple 1."]
    agents = [f"<memory>I hold apple 1.</memory><reflection>The apple is held.</reflection>"
              f"<plan>Inspect countertop 1 at decision {i}.</plan><action>look</action>" for i in range(1, 4)]
    return {"trajectory_id": "fixture", "task_type": "alfworld", "task": "put a apple on diningtable.",
            "steps": plain(compiler.segment(env, agents, [["look"]] * 3))}


def case_for(tools):
    fid = next(f.fid for f in tools.index.facts.values() if f.t == 3 and f.slot == "plan")
    return {"candidate_id": "case1", "issue_step": 3, "issue_fids": [fid], "goal_links": [], "hypothesis": "Check prior decision."}


def answer():
    return {"window_id": "case1", "candidates": [{"status": "uncertain", "is_root_cause": None, "step": 1, "module": "plan", "fault_class": None,
            "output_quote": "Inspect countertop 1 at decision 1.", "proposition": "The inspection may be unnecessary.",
            "reasoning": "Available evidence does not establish its purpose.", "immediate_effect": "No effect recorded.",
            "uncertainty": "A supported diagnostic purpose remains possible.", "requirement_links": []}], "uncertainty": "Limited evidence."}


def submit_answer():
    return {"status": "diagnosed", "selected_candidate_id": "trace-window:case1:1",
            "fault_class": "inefficient_plan", "output_quote": "Inspect countertop 1 at decision 1.",
            "reasoning": "This is the most plausible supplied cause, though the inspection's purpose is unresolved.",
            "comparisons": [{"candidate_id": "trace-window:case1:1", "reasoning": "Selected with limited source support."}],
            "repair": "Use prior observations to choose the next inspection.", "uncertainty": "A diagnostic purpose remains possible."}


def manager_answer(agent_ids=("case1",)):
    return {"assignments": [{"agent_id": aid, "anchor_case_id": "case1", "route_case_ids": ["case1"],
        "question": "Did an earlier inspection lack an information-gathering purpose?",
        "search_targets": ["Earlier plan inspections and independent observations"],
        "evidence": [{"source_id": "source:3:plan", "quote": "Inspect countertop 1 at decision 3."}],
        "criteria": {"supported": "Original plan and feedback establish unproductive repetition.",
            "refuted": "A changed state or diagnostic purpose explains the inspection.",
            "unknown": "The available excerpts do not establish the purpose; keep it unknown."}}
        for aid in agent_ids],
        "decision_rules": [{"rule_id": "r1", "conditions": [{"agent_id": aid, "outcome": "supported",
            "finding": "Original evidence establishes an unproductive inspection."} for aid in agent_ids],
            "conclusion": "Compare the evidenced source mistake with later propagation.",
            "caveat": "An earlier timestamp alone does not establish dependence."}],
        "uncertainty": "Earlier memory and reflection require targeted retrieval."}


def call(cid, name, args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class BackendChecks(unittest.TestCase):
    def setUp(self):
        self.bundle = build_bundle(source())
        self.tools = DebugTools(self.bundle)
        self.case = case_for(self.tools)
        self.tools.execute("view", {"spec": "requirements", "scope": {"mode": "global"}}, "map")
        self.tools.execute("route_cases", {"candidates": [self.case]}, "route")

    def test_strict_template_and_semantic_triggers(self):
        profile = PROFILES["alfworld"]
        for text in ["You put it on the table.", "Nothing happens. It disappeared.", "No apples remain.",
                     "There is only one left.", "The price is lower than 10.", "It is still closed."]:
            self.assertTrue(any(r.startswith("semantic_trigger") for r in audit("observation", text, [{}], profile)["reasons"]))
        self.assertFalse(audit("observation", source()["steps"][0]["env_text"], [{}], profile)["reasons"])
        self.assertIn("no_complete_trusted_template", audit("observation", source()["steps"][0]["env_text"] + " Extra meaning.", [{}], profile)["reasons"])

    def test_task_and_observation_residual_coverage(self):
        profile = PROFILES["alfworld"]
        text = "put a apple on diningtable, without cleaning it."
        goals = plain(profile.parse_goal(text))
        _, _, gaps = _validate_response({"facts": [], "goals": goals, "unresolved": []}, "task", text, profile, [])
        self.assertTrue(gaps)
        fact = {"rel": "effect", "args": [["agent", "self"]], "value": "none", "value_type": "string", "span": "Nothing happens."}
        _, _, gaps = _validate_response({"facts": [fact], "goals": [], "unresolved": []}, "observation", "Nothing happens. The apple disappeared.", profile, [])
        self.assertTrue(gaps)
        fact["span"] = "Invented statement."
        with self.assertRaises(ValueError):
            _validate_response({"facts": [fact], "goals": [], "unresolved": []}, "observation", "Nothing happens.", profile, [])

    def test_incomplete_is_not_rule_violation(self):
        data = source(); data["steps"][1]["env_text"] = "Nothing happens. It disappeared."
        bundle = build_bundle(data)
        self.assertTrue(any(a["status"] == "unresolved" for a in bundle.record.parse_audits))
        self.assertFalse(any(e["state"] == "violated" and e.get("t", 0) >= 2 for e in bundle.evaluations))

    def test_backoff_sees_only_prior_sources_and_cache_reuses(self):
        captured = []
        class FakeClient:
            def __init__(self, *args):
                pass
            def snapshot(self):
                return {"model_calls": len(captured)}
            def complete(self, history, schemas, phase):
                payload = json.loads(history[1]["content"])
                captured.append(payload)
                fact = {"rel": "effect", "args": [["agent", "self"]], "value": "none", "value_type": "string", "span": payload["text"]}
                return {"role": "assistant", "content": json.dumps({"facts": [fact], "goals": [], "unresolved": []})}
        with tempfile.TemporaryDirectory() as directory, patch("prodebugger.llm_client.LLMClient", FakeClient):
            data = source(); data["steps"][1]["env_text"] = "The action had no effect."
            parsed = prepare(data, Path(directory), {}, online=True)
            self.assertEqual(parsed["units"]["observation:2"]["status"], "backoff")
            self.assertEqual(captured[0]["prior_observations_and_actions"], [source()["steps"][0]["env_text"], "look"])
            calls = len(captured)
            self.assertEqual(prepare(data, Path(directory), {}, online=True), parsed)
            self.assertEqual(len(captured), calls)

    def test_complete_alfworld_scopes_and_antecedents(self):
        from prodebugger.template_parser import parse, NeedsBackoff
        profile = PROFILES["alfworld"]
        result = parse("observation", "You open the cabinet 1. The cabinet 1 is open. In it, you see nothing.", profile)
        empty = [f for f in result["facts"] if f["rel"] == "visible_empty"]
        self.assertEqual(empty[0]["args"], [("cabinet", "1")])
        self.assertTrue(empty[0]["value"])
        result = parse("observation", "Nothing happens.", profile)
        self.assertEqual([f["rel"] for f in result["facts"]], ["effect"])
        for text in ["In it, you see nothing.", "You arrive at cabinet 1. The cabinet 2 is open. In it, you see nothing.",
                     "On the table 1, you see a apple 1, and something else.", "Nothing happens. You move the apple 1 to the desk 1."]:
            with self.assertRaises(NeedsBackoff):
                parse("observation", text, profile)
        result = parse("task", "find two creditcard and put them in sofa.", profile)
        self.assertEqual(result["goals"][0]["count"], 2)
        self.assertEqual(result["goals"][0]["constraints"][0]["expected"], "creditcard")
        self.assertEqual(parse("task", "put a glass in cabinet.", profile)["goals"][0]["constraints"][0]["expected"], "glass")

    def test_listing_titles_quantities_and_option_boundaries(self):
        from prodebugger.template_parser import parse, NeedsBackoff
        profile = PROFILES["webshop"]
        title = "A very long product title " * 6
        observation = "'Back to Search' [SEP] 'Page 1 (Total results: 50)' [SEP] 'Next >' [SEP] 'B000000000' [SEP] '" + title + "' [SEP] '$10.00 to $20.00'"
        result = parse("observation", observation, profile)
        self.assertTrue(any(f["value"] == title for f in result["facts"]))
        self.assertTrue(any(f["rel"] == "result_total" and f["value"] == 50 for f in result["facts"]))
        with self.assertRaises(NeedsBackoff):
            parse("observation", observation + " [SEP] 'extra claim'", profile)
        result = parse("task", "Find me men's shirts with long sleeve with color: red, and fit type: women, and price lower than 50.00 dollars", profile)
        self.assertEqual([g["name"] for g in result["goals"]], ["description", "color", "fit_type", "price"])
        self.assertEqual(result["goals"][0]["status_mode"], "unmodeled")
        self.assertEqual(result["goals"][-1]["constraints"][0]["expected"], 50.0)
        self.assertEqual(result["goals"][-1]["constraints"][0]["operator"], "lt")

    def test_composable_queries_and_functions(self):
        queries = ["WITH x AS (SELECT * FROM TRACK_ENTITY('portable:apple_1')) SELECT step,COUNT(*) AS n FROM x WHERE side='mind' OR side='env' GROUP BY step HAVING COUNT(*)>0",
                   "SELECT * FROM TRACK_STATE('agent:self','holding')",
                   "SELECT * FROM TRACE_FACT(0,'backward',3)", "SELECT * FROM SEARCH_TEXT('decision 1','plan')",
                   "SELECT * FROM STATE_AT('agent:self','holding',2)", "SELECT * FROM ABOUT('portable:apple_1')",
                   "SELECT * FROM VERSIONS(0)", "SELECT * FROM GOAL_STATUS('req:apple_in_diningtable',2)",
                   "SELECT * FROM COVERAGE(2)", "SELECT * FROM FIND_ENTITY('apple')"]
        for sql in queries:
            with self.subTest(sql=sql):
                result = execute(self.tools, sql)
                self.assertNotIn("error", result)
                self.assertGreater(result["returned_rows"], 0)

    def test_read_only_without_result_row_budget(self):
        for sql in ["DELETE FROM facts", "SELECT * FROM facts; SELECT * FROM steps", "SELECT * FROM sqlite_master",
                    "SELECT load_extension('/tmp/x')", "SELECT * FROM TRACK_ENTITY('product:invented')",
                    "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x",
                    "SELECT unknown_column FROM steps", "SELECT * FROM nope.facts", "SELECT * FROM steps,steps"]:
            self.assertIn("error", execute(self.tools, sql), sql)
        result = execute(self.tools, "SELECT a.step FROM TRACK_ENTITY('portable:apple_1') a CROSS JOIN TRACK_ENTITY('portable:apple_1') b CROSS JOIN TRACK_ENTITY('portable:apple_1') c")
        self.assertGreater(result["returned_rows"], 120)
        self.assertFalse(result["truncated"])
        self.assertIsNone(result["limits"]["rows"])

    def test_json_attribute_filters_and_joins_preserve_sqlite_scalar_semantics(self):
        result = execute(self.tools, """
            WITH held AS (
                SELECT step,args,span FROM TRACK_STATE('agent:self','holding')
                WHERE json_extract(args,'$[1]')='portable:apple_1'
            ), apples AS (SELECT entity_id FROM FIND_ENTITY('apple'))
            SELECT h.step,h.span,json_extract(h.args,'$[1]') AS entity,
                   json_extract(h.args,'$[99]') AS missing,
                   json_extract('[false,null,3]','$[0]') AS boolean_value,
                   json_extract('[false,null,3]','$[1]') AS null_value,
                   json_extract('[false,null,3]','$[2]') AS number_value,
                   json_extract(h.args,'$[0]','$[1]') AS pair
            FROM held h JOIN apples a ON json_extract(h.args,'$[1]')=a.entity_id
            ORDER BY step""")
        self.assertNotIn("error", result)
        self.assertTrue(result["rows"])
        row = result["rows"][0]
        self.assertEqual(row["entity"], "portable:apple_1")
        self.assertIsNone(row["missing"])
        self.assertEqual(row["boolean_value"], 0)
        self.assertIsNone(row["null_value"])
        self.assertEqual(row["number_value"], 3)
        self.assertEqual(json.loads(row["pair"]), ["agent:self", "portable:apple_1"])
        # A true type assignment must not satisfy a cleanliness predicate.
        clean = execute(self.tools, "SELECT * FROM TRACK_STATE('portable:apple_1','has') WHERE json_extract(args,'$[1]')='attribute:clean'")
        self.assertNotIn("error", clean)
        self.assertEqual(clean["rows"], [])

    def test_one_query_even_when_invalid(self):
        self.assertIn("error", query(self.tools, "case1", "DROP TABLE facts"))
        with self.assertRaises(ValueError):
            query(self.tools, "case1", "SELECT * FROM steps")

    def test_no_window_context_and_root_outside_anchor(self):
        inputs = packet(self.tools, self.tools.windows.windows[0])
        for forbidden in ("prior_context", "window_steps", "evaluation_steps"):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual(inputs["source"]["step"], 3)
        query(self.tools, "case1", "SELECT * FROM SEARCH_TEXT('decision 1','plan')")
        event = commit(self.tools, answer(), "assess")
        self.assertEqual(event["candidates"][0]["step"], 1)
        self.assertEqual(set(event["conclusion"]["candidates"][0]), set(FIELDS))
        payload = submit_packet(self.tools)
        self.assertEqual(payload["schema"], "direct-submit-input/v2")
        from prodebugger.window_trace import coverage
        self.assertEqual(coverage(self.tools)['assessment_unit'], 'single_step_per_route_candidate')
        self.assertEqual(coverage(self.tools)['assessed_steps'], [1])
        self.assertEqual(set(payload["candidates"][0]), {"candidate_id", "window", "step", "module", "assessment",
                                                        "local_context_ref", "related_evidence", "evidence_selection"})
        self.tools.window_conclusions.clear()
        self.tools.windows.windows[0]["lo"] = 1
        legacy = old_commit(self.tools, answer(), "old")
        self.assertEqual(set(event), set(legacy))
        self.assertEqual(event["candidates"], legacy["candidates"])

    def test_unretrieved_quote_and_multistep_rejected(self):
        query(self.tools, "case1", "SELECT step FROM TRACK_ENTITY('portable:apple_1')")
        with self.assertRaises(ValueError):
            commit(self.tools, answer(), "assess")
        proposal = answer()
        proposal["candidates"].append({**proposal["candidates"][0], "step": 3, "output_quote": "Inspect countertop 1 at decision 3."})
        with self.assertRaises(ValueError):
            commit(self.tools, proposal, "assess")

    def test_late_anchor_retrieves_early_modules_actions_and_state(self):
        data = source()
        memory = "At the first decision I picked up apple 1."
        agents = [f"<memory>{memory if i == 2 else 'Continue the task.'}</memory>"
                  f"<reflection>{'The initial pickup succeeded.' if i == 3 else 'Progress is unresolved.'}</reflection>"
                  f"<plan>Inspect countertop 1 at decision {i}.</plan><action>look</action>" for i in range(1, 31)]
        data["steps"] = plain(compiler.segment([source()["steps"][0]["env_text"]] + ["Nothing happens."] * 29,
                                               agents, [["look"]] * 30))
        tools = DebugTools(build_bundle(data))
        late_fid = next(f.fid for f in tools.bundle.record.F if f.t == 30 and f.slot == "plan")
        early_fid = next(f.fid for f in tools.bundle.record.F if f.t == 2 and f.slot == "mem")
        case = {**self.case, "issue_step": 30, "issue_fids": [late_fid]}
        tools.execute("view", {"spec": "requirements", "scope": {"mode": "global"}}, "map")
        tools.execute("route_cases", {"candidates": [case]}, "route")
        inputs = packet(tools, tools.windows.windows[0])
        self.assertEqual(inputs["query_context"]["step_range"], [1, 30])
        self.assertIn("look", inputs["query_context"]["observed_action_relations"])
        self.assertNotIn("examine", inputs["query_context"]["relation_names"])
        self.assertEqual((tools.windows.windows[0]["lo"], tools.windows.windows[0]["hi"]), (30, 30))
        result = query(tools, "case1", f"""
            SELECT step,module,text,source_kind FROM SEARCH_TEXT('first decision','memory')
            UNION ALL SELECT step,module,text,source_kind FROM SEARCH_TEXT('initial pickup','reflection',1,3)
            UNION ALL SELECT step,module,text,source_kind FROM SEARCH_TEXT('decision 4.','plan')
            UNION ALL SELECT step,'action',span,source_kind FROM TRACK_ACTION('look',1,4)
            UNION ALL SELECT step,'state',span,source_kind FROM TRACK_STATE('agent:self','holding')
            UNION ALL SELECT step,module,text,source_kind FROM SOURCE({early_fid},'first decision')
            UNION ALL SELECT step,'state_at',span,source_kind FROM STATE_AT('agent:self','holding',4)
            ORDER BY step,module""")
        self.assertNotIn("error", result)
        self.assertFalse(result["truncated"])
        actual = {(r["module"], r["step"]) for r in result["rows"]}
        self.assertTrue({("memory", 2), ("reflection", 3), ("plan", 4), ("action", 1), ("state", 1), ("state_at", 1)} <= actual)
        proposal = answer()
        proposal["candidates"][0].update(step=2, module="memory", output_quote=memory)
        event = commit(tools, proposal, "assess")
        self.assertEqual(set(event["conclusion"]["candidates"][0]), set(FIELDS))
        payload = submit_packet(tools)
        self.assertEqual(payload["schema"], "direct-submit-input/v2")
        self.assertEqual(payload["candidates"][0]["step"], 2)
        self.assertEqual(payload["candidates"][0]["module"], "memory")

    def test_full_agent_replay_and_resume_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            final = submit_answer()
            messages = [call("map", "view", {"spec": "requirements", "scope": {"mode": "global"}}),
                        call("route", "route_cases", {"candidates": [self.case]}),
                        call("manage", "plan_trace", manager_answer()),
                        call("query", "query_logs", {"candidate_id": "case1", "sql": "SELECT * FROM SEARCH_TEXT('decision 1','plan')"}),
                        call("assess", "assess", answer()), {"role": "assistant", "content": json.dumps(final)}]
            write_json(out / "replay.json", messages)
            client = ReplayClient(out / "replay.json", out)
            agent = DebuggerAgent(self.bundle, client, out, DEFAULTS)
            original_dispatch = agent._dispatch
            def interrupt_after_query(item):
                original_dispatch(item)
                if item["function"]["name"] == "query_logs":
                    raise KeyboardInterrupt()
            agent._dispatch = interrupt_after_query
            interrupted = agent.run()
            self.assertEqual(interrupted["status"], "uncertain")
            self.assertEqual(client.cursor, 4)
            client = ReplayClient(out / "replay.json", out)
            agent = DebuggerAgent(self.bundle, client, out, DEFAULTS, resume=True)
            result = agent.run()
            self.assertEqual(result["status"], "diagnosed")
            self.assertEqual(result["root"]["source_trace_status"], "uncertain")
            self.assertEqual(result["stop_reason"], "direct TRACE-candidate comparison completed")
            self.assertEqual(client.cursor, 6)
            requests = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "requests").glob("*.json"))]
            self.assertEqual([r["phase"] for r in requests], ["MAP", "ROUTE", "MANAGER", "TRACE", "TRACE", "SUBMIT"])
            self.assertEqual(requests[-1]["tools"], [])
            self.assertEqual(len(requests[4]["messages"]), 4)
            restored = DebuggerAgent(self.bundle, ReplayClient(out / "replay.json", out), out, DEFAULTS, resume=True)
            rerun = restored.run()
            self.assertEqual(rerun["status"], result["status"])
            self.assertEqual(restored.client.cursor, 6)
            self.assertEqual(len(json_lines(out / "trace_queries.jsonl")), 1)

    def test_full_log_access_rejected_even_indirectly(self):
        forbidden = ["SELECT * FROM steps", "SELECT action FROM steps WHERE step=1",
                     "SELECT * FROM modules WHERE module='memory'", "SELECT * FROM facts",
                     "SELECT * FROM RAW_STEP(1)", "WITH x AS (SELECT * FROM steps) SELECT * FROM x",
                     "SELECT s.* FROM TRACK_ENTITY('portable:apple_1') e JOIN steps s ON e.step=s.step",
                     "SELECT * FROM TRACK_ENTITY('agent:self')", "SELECT * FROM SEARCH_TEXT('%','plan')",
                     "SELECT * FROM SEARCH_TEXT('the','plan')", "SELECT * FROM SEARCH_TEXT('apple','all')",
                     "SELECT step AS fid FROM TRACK_ENTITY('portable:apple_1')"]
        for sql in forbidden:
            self.assertIn('error', execute(self.tools, sql), sql)

    def test_raw_wrapper_cannot_leak_and_source_offsets_are_exact(self):
        fid = self.case['issue_fids'][0]
        result = execute(self.tools, f"SELECT * FROM TRACE_FACT({fid},'backward',1)")
        wrapper = next(r for r in result['rows'] if r['fid'] == fid)
        self.assertIsNone(wrapper['span'])
        self.assertIsNone(wrapper['value'])
        result = execute(self.tools, f"SELECT * FROM SOURCE({fid},'countertop')")
        row = result['rows'][0]
        self.assertEqual(row['text'], source()['steps'][2]['parts']['plan'][row['start']:row['end']])
        self.assertLessEqual(len(row['text']), 360)

    def test_all_sources_materialize_before_explicit_sql_limit(self):
        data = source()
        long_plan = 'padding ' * 80 + 'countertop' + ' continuation' * 70
        data['steps'] = plain(compiler.segment(['Nothing happens.'] * 20,
            [f'<plan>{long_plan}</plan><action>look</action>'] * 20, [['look']] * 20))
        tools = DebugTools(build_bundle(data))
        result = execute(tools, "SELECT * FROM SEARCH_TEXT('countertop','plan') LIMIT 2")
        self.assertEqual(result['returned_rows'], 2)
        self.assertEqual(result['coverage'], 'partial_SQL_LIMIT')
        self.assertFalse(result['truncated'])
        self.assertEqual(result['function_scopes'][0]['limits']['matched_sources'], 20)
        self.assertEqual(result['function_scopes'][0]['limits']['returned_sources'], 20)
        self.assertIsNone(result['function_scopes'][0]['limits']['max_sources'])
        self.assertTrue(all(len(r['text']) <= 360 for r in result['rows']))

    def test_missing_clean_state_never_becomes_negative(self):
        from record import Entity
        from process_notes import goal_state
        from intention_validation import validate_intention
        data = source(); data['task'] = 'put a clean apple on diningtable.'
        data['steps'] = plain(compiler.segment(['You pick up the apple 1 from the countertop 1.'],
            ['<plan>I should move apple 1 to diningtable 1.</plan><action>move apple 1 to diningtable 1</action>'],
            [['move apple 1 to diningtable 1']]))
        bundle = build_bundle(data); r = bundle.record
        check = next(f for f in r.F if f.slot == 'chk' and f.rel == 'satisfied' and f.t == 1)
        self.assertIsNone(check.value)
        self.assertEqual(goal_state(r, r.goals[0], 1)['state'], 'unknown')
        from views import entity_view
        self.assertEqual(entity_view(r, 'req', r.goals[0].entity().index)['status']['state'], 'unknown')
        self.assertFalse(any(f.slot == 'chk' and f.rel == 'in' for f in r.F))
        self.assertNotEqual(validate_intention(r, 1)['constraint_bindings'][0]['relation'], 'substitutes')
        action = PROFILES['alfworld'].parse_action('move apple 1 to diningtable 1', ['move apple 1 to diningtable 1'])
        args = (Entity('portable','apple_1'), Entity('attribute','clean'), Entity('value','true'))
        r.add('has', args, False, 'obs', 'env', 1, value_type='boolean')
        self.assertIs(PROFILES['alfworld'].evaluate_discharges(action, r.goals, r, 1)[0]['value'], False)
        r.add('has', args, True, 'obs', 'env', 1, value_type='boolean')
        self.assertIs(PROFILES['alfworld'].evaluate_discharges(action, r.goals, r, 1)[0]['value'], True)

    def test_parse_gaps_and_heuristic_provenance_are_always_visible(self):
        data = source(); data['steps'][0]['env_text'] = 'It might be elsewhere.'
        tools = DebugTools(build_bundle(data))
        result = execute(tools, "SELECT * FROM TRACK_ACTION('look')")
        self.assertTrue(result['evidence_quality']['parse_gaps'])
        self.assertEqual(result['evidence_quality']['absence_means'], 'unknown_not_false')
        self.assertTrue(all(r['parse_status'] == 'selective_heuristic_not_audited' for r in result['rows']))
        self.assertIn('evidence_quality', execute(tools, 'SELECT * FROM steps'))

    def test_complete_templates_do_not_enable_closed_world_negation(self):
        from criterion import kappa
        from record import Entity
        r = self.bundle.record
        self.assertTrue(all(a['status'] == 'template' for a in r.parse_audits))
        self.assertFalse(r.relations['holding'].is_observed())
        fid = r.add('holding', (Entity('agent','other'), Entity('portable','pear_1')),
                    True, 'mem', 'mind', 1, span='I might be carrying a pear.', value_type='boolean')
        self.assertEqual(kappa(r, r.fact(fid)).value, 'unk')


if __name__ == "__main__":
    unittest.main()
