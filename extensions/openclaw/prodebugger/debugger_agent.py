"""Four isolated phase conversations sharing the original debugger state machine."""
from __future__ import annotations

import json
from pathlib import Path

from .debug_tools import DebugTools, PHASE_TOOLS, SCHEMAS
from .evidence_tools import BudgetExceeded, ToolError
from .llm_client import PHASES, json_lines
from .record_bridge import append_json, digest, plain, read_json, write_json
from .window_trace import SCHEMA as WINDOW_SCHEMA, schema_for
from .query_trace import MODE, packet as trace_packet, coverage as window_coverage
from .query_dsl import documentation
from .route_context import compact as compact_route_context, progress as route_progress
from .evidence_quality import evidence_quality
from .benchmark_scope import scope as benchmark_scope
from .trace_manager import SCHEMA as MANAGER_SCHEMA, packet as manager_packet
from .direct_submit import POLICY, policy_for, outcome_context, commit as commit_direct, packet as submit_packet, parse as parse_submit
from local_judgement import SLOTS

PROMPTS = {p: Path(__file__).parent / "prompts" / f"{p.lower()}.md" for p in PHASES}


class DebuggerAgent:
    def _prompt(self, phase):
        path = Path(__file__).parent / "prompts" / f"{phase.lower()}.md"
        if self.bundle.source["task_type"] == "openclaw":
            specific = path.with_name("openclaw_" + path.name)
            if specific.is_file():
                path = specific
        return path.read_text(encoding="utf-8")

    def __init__(self, bundle, client, output, config, *, resume=False):
        self.bundle, self.client, self.output, self.config = bundle, client, Path(output), config
        self.tools = DebugTools(bundle, self.output, config["max_tool_calls"], config["window_size"],
                                trace_top_p=config.get("trace_top_p", 5), trace_top_k=config.get("trace_top_k", 2))
        from .claim_parser import ClaimParser
        from .llm_client import LLMClient
        self.tools.claim_parser = ClaimParser(bundle.source, self.output, config=config,
                                              online=isinstance(client, LLMClient))
        self.phase_messages = {p: [] for p in PHASES}
        self.active_phase = None
        self.turns, self.text_only_turns = 0, 0
        self.trace_failures = {}
        self.trace_histories = {}
        self.manager_history = []
        self.manager_failures = 0
        self.consumed_model_state = client.snapshot()
        checkpoint = self.output / "checkpoint.json"
        if resume:
            state = read_json(checkpoint)
            if state.get("schema") != "debugger-checkpoint/v6":
                raise ToolError("source_mismatch", "managed TRACE checkpoints require v6; start a new run")
            if state["canonical_fingerprint"] != bundle.fingerprint:
                raise ToolError("source_mismatch", "input/compiler/rules/prompt changed; use a new run")
            self.tools.restore(json_lines(self.output / "tool_calls.jsonl"),
                               json_lines(self.output / "direct_submit.jsonl"))
            self.phase_messages, self.turns = state["phase_messages"], state["turns"]
            self.active_phase = state["active_phase"]
            self.text_only_turns = state.get("text_only_turns", 0)
            self.trace_failures = state.get("trace_failures", {})
            self.trace_histories = state.get("trace_histories", {})
            self.manager_history = state.get("manager_history", [])
            self.manager_failures = state.get("manager_failures", 0)
            self.consumed_model_state = state["model_state"]
            client.restore(state["model_state"])
            if client.recovered_message and client.recovered_phase not in {"TRACE", "MANAGER"}:
                if client.recovered_phase != self.active_phase:
                    raise ToolError("source_mismatch", "durable model response phase differs from checkpoint")
                self._assistant(client.recovered_message)
        else:
            self._activate("MAP")
            self.checkpoint()

    @property
    def messages(self):
        return self.phase_messages[self.active_phase]

    def _handoff(self, phase):
        if phase == "SUBMIT":
            return {"schema": "debugger-handoff/v1", "from": self.active_phase, "to": phase,
                    "policy": policy_for(self.bundle.source), "instruction": "Compare supplied TRACE suspects directly; no tools. "
                                                         "The runner supplies original context and benchmark scope."}
        if phase == "TRACE":
            return {"schema": "debugger-handoff/v1", "from": self.active_phase, "to": phase,
                    "versions": {"window_version": self.tools.windows.version}, "evaluation_mode": MODE,
                    "candidate_limits": {"top_p": self.tools.trace_top_p, "top_k": 1},
                    "instruction": "The manager assigns investigations and conditional interpretation rules from all ROUTE evidence. "
                                   "Each investigator gets anchor evidence, allocated excerpts, one SQL query, then one step in assess."}
        state = {"schema": "debugger-handoff/v1", "from": self.active_phase, "to": phase,
                 "versions": {"window_version": self.tools.windows.version}, "goal_map": self.tools.goal_map,
                 "coverage": window_coverage(self.tools),
                 "candidate_limits": {"top_p": self.tools.trace_top_p, "top_k": 1,
                                      "top_k_unit": "distinct_root_steps_per_case"},
                 "route_history": self.tools.route_history,
                 "instruction": "Current shared state supersedes old versions. Hypotheses and prior judgements are leads, not local evidence. Re-read scoped raw material when needed."}
        # Carry transition tool outputs without forwarding the previous model's messages or reasoning.
        outputs = []
        if self.active_phase:
            for m in reversed(self.messages):
                if m["role"] == "assistant":
                    break
                if m["role"] == "tool":
                    event = self.tools.completed.get(m["tool_call_id"], {})
                    if event.get("name") in {"route_cases", "reroute", "assess"}:
                        outputs.append({"tool": event["name"], "result": json.loads(m["content"])})
        state["transition_results"] = list(reversed(outputs))
        return plain(state)

    def _log_message(self, message):
        append_json(self.output / "messages.jsonl", {"phase": self.active_phase,
                    "message": plain(message), "turn": self.turns})

    def _route_timeline(self):
        """Full original action/feedback timeline with explicit existing Fact locations."""
        from .session_sources import is_session, message_event, owned_facts, slots_for, feedback_text
        source = self.bundle.source["steps"]
        facts = list(self.tools.index.facts.values())
        seen, rows = {}, []
        for item in source:
            t, action = item["t"], item["parts"].get("action")
            feedback = feedback_text(self.bundle.source, t)
            rows.append({"step": t, "env_text": item["env_text"], "action": action,
                "next_env_text": feedback,
                "module_fids": {m: [f.fid for f in owned_facts(facts, self.bundle.source, t, m)]
                                for m in slots_for(self.bundle.source)},
                **({"recorded_message": {"text": message_event(self.bundle.source, t).get("text", ""),
                                         "role": message_event(self.bundle.source, t).get("role"),
                                         "original_seq": message_event(self.bundle.source, t).get("original_seq")}}
                    if message_event(self.bundle.source, t) else {}),
                "statement_discovery": {m: {"topics": sorted({topic for c in self.bundle.record.agent_statements
                    if c["step"] == t and c["module"] == m for topic in c["topics"]}),
                    "unresolved_clauses": sum(c["coverage"] == "unresolved" for c in self.bundle.record.agent_statements
                                              if c["step"] == t and c["module"] == m)}
                    for m in ("memory", "reflection", "plan")},
                "recorded_goal_observations": [{"fid": f.fid, "relation": f.rel,
                    "arguments": plain(f.args), "value": plain(f.value), "quote": f.span}
                    for f in facts if f.t == t and f.side == "env"
                    and any(str(a) in self.tools.index.requirements for a in f.args)],
                "signals": {"previous_exact_action_steps": list(seen.get(action, [])),
                    "explicit_no_effect_feedback": feedback.strip().lower() == "nothing happens."
                                                   if feedback is not None else None}})
            if action:
                seen.setdefault(action, []).append(t)
        return {"schema": "route-timeline/v1", "task": self.bundle.source["task"],
            "requirement_ids": sorted(self.tools.index.requirements), "steps": rows,
            "complete": True, "step_count": len(rows),
            "evidence_quality": evidence_quality(self.bundle.record, len(rows)),
            "instruction": ("For recorded OpenClaw sessions step is an imported event index, not an original Agent reasoning step. original_seq preserves native position. Tool response identity comes from call IDs; adjacency is not causality. message is public recorded text, not a memory module. "
                            if is_session(self.bundle.source) else "") +
                "step equals the current analysis input step and Fact.t. module_fids belong to that row's step. "
                "scope.step only controls visibility, not a returned Fact's location. "
                "Feedback is original post-action text; repeat/no-effect signals are leads, not faults. "
                "Empty recorded_goal_observations means no such compiled observation, not proof of no progress. "
                "statement_discovery topics are lexical leads, not asserted propositions or fault signals. "
                "Unresolved clause counts measure parser coverage, never agent omissions. "
                "Use tools to read full modules at candidate steps before declaring causal anchors."}

    def _activate(self, phase):
        if phase == self.active_phase:
            return
        previous = self.active_phase
        handoff = self._handoff(phase)
        self.active_phase = phase
        if not self.messages:
            metadata = {"trajectory_id": self.bundle.record.tid,
                        "task": self.bundle.record.task, "steps": len(self.bundle.source["steps"]),
                        "submission_policy": policy_for(self.bundle.source),
                        "candidate_limits": {"top_p": self.tools.trace_top_p, "top_k": 1,
                                             "top_k_unit": "distinct_root_steps_per_case"}}
            self.messages.extend([
                {"role": "system", "content": self._prompt(phase)},
                {"role": "user", "content": json.dumps(metadata, ensure_ascii=False)}])
            for m in self.messages:
                self._log_message(m)
        message = {"role": "user", "content": json.dumps(handoff, ensure_ascii=False)}
        self.messages.append(message)
        self._log_message(message)
        if phase == "ROUTE":
            message = {"role": "user", "content": json.dumps(self._route_timeline(), ensure_ascii=False)}
            self.messages.append(message)
            self._log_message(message)
            if self.bundle.source["task_type"] == "openclaw":
                message = {"role": "user", "content": json.dumps({"schema": "openclaw-task-context/v1",
                    "task_outcome": outcome_context(self.tools), "benchmark_scope": benchmark_scope(self.bundle.source),
                    "capture_coverage": self.bundle.source.get("telemetry", {}).get("coverage", {}),
                    "domain_briefing": self.bundle.record.domain_briefing}, ensure_ascii=False)}
                self.messages.append(message)
                self._log_message(message)
        append_json(self.output / "phase_events.jsonl", {"from": previous, "to": phase,
                    "turn": self.turns, "handoff": handoff})
        self.text_only_turns = 0

    def checkpoint(self):
        write_json(self.output / "checkpoint.json", {"schema": "debugger-checkpoint/v6",
                   "canonical_fingerprint": self.bundle.fingerprint, "phase": self.tools.phase,
                   "active_phase": self.active_phase,
                   "turns": self.turns, "text_only_turns": self.text_only_turns,
                   "trace_failures": self.trace_failures,
                   "trace_histories": self.trace_histories,
                   "manager_history": self.manager_history, "manager_failures": self.manager_failures,
                   "tool_calls": self.tools.call_count, "model_state": self.consumed_model_state,
                   "phase_messages": self.phase_messages})

    def _assistant(self, message):
        if message.get("role") != "assistant":
            raise ToolError("model_error", "model returned a non-assistant message")
        self.messages.append(plain(message))
        self.consumed_model_state = self.client.snapshot()
        self.turns += 1
        self.text_only_turns = 0 if message.get("tool_calls") else self.text_only_turns + 1
        self._log_message(message)
        self.checkpoint()  # Preserve outstanding calls before executing any mutation.

    def _pending_calls(self):
        assistant_pos = next((i for i in range(len(self.messages) - 1, -1, -1)
                              if self.messages[i]["role"] == "assistant"), None)
        if assistant_pos is None:
            return []
        calls = self.messages[assistant_pos].get("tool_calls", []) or []
        completed = {m.get("tool_call_id") for m in self.messages[assistant_pos + 1:] if m["role"] == "tool"}
        return [c for c in calls if c.get("id") not in completed]

    def _dispatch(self, call):
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            raise ToolError("model_error", "tool call is missing its ID")
        function = call.get("function", {})
        raw_arguments = function.get("arguments", "{}")
        argument_error = None
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            arguments = raw_arguments
            start, end = max(0, exc.pos - 80), min(len(raw_arguments), exc.pos + 80)
            argument_error = {"code": "invalid_json",
                "message": f"arguments is invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno} (character {exc.pos}).",
                "json_error": {"message": exc.msg, "line": exc.lineno, "column": exc.colno, "position": exc.pos},
                "context": {"start": start, "end": end, "text": raw_arguments[start:end]},
                "instruction": "Resend the entire arguments as one valid JSON object. Fix the reported JSON syntax; "
                    "do not append another object or paraphrase exact source quotations. The requested tool was not executed."}
        except TypeError:
            arguments = raw_arguments
            argument_error = {"code": "invalid_json",
                "message": f"arguments must be a JSON string, received {type(raw_arguments).__name__}.",
                "instruction": "Serialize the complete arguments object as valid JSON text. The requested tool was not executed."}
        result = self.tools.execute(function.get("name", ""), arguments, call_id, argument_error=argument_error)
        self.messages.append({"role": "tool", "tool_call_id": call_id,
                              "content": json.dumps(result, ensure_ascii=False)})
        self._log_message(self.messages[-1])
        self.checkpoint()

    def _context(self):
        """Preserve a whole fresh tool batch, with evidence-bearing older summaries."""
        messages = compact_route_context(self.messages, self.tools.completed)
        for message in messages:
            if message["role"] == "user":
                value = json.loads(message["content"])
                if value.get("schema") == "debugger-handoff/v1" and value.get("transition_results"):
                    value["transition_results"] = [{"tool": row["tool"],
                        "original_digest": digest(row["result"]), "truncated": True,
                        "instruction": "Re-read scoped views for raw material."}
                        for row in value["transition_results"]]
                    message["content"] = json.dumps(value, ensure_ascii=False)
        return messages

    def _manage_trace(self):
        """One isolated manager conversation; committed allocation survives restart."""
        if self.tools.manager_plan is not None:
            return
        if not self.manager_history:
            self.manager_history = [
                {"role": "system", "content": self._prompt("MANAGER")},
                {"role": "user", "content": json.dumps(manager_packet(self.tools), ensure_ascii=False)}]
            self._log_message(self.manager_history[-1])
            self.checkpoint()
        history = self.manager_history
        while self.tools.manager_plan is None and self.manager_failures < 3:
            if history[-1]["role"] != "assistant":
                if self.turns >= self.config["max_turns"]:
                    raise BudgetExceeded("assistant turn limit reached")
                if self.client.recovered_message and self.client.recovered_phase == "MANAGER":
                    message = self.client.recovered_message
                    self.client.recovered_message = None
                else:
                    message = self.client.complete(plain(history), [MANAGER_SCHEMA], phase="MANAGER")
                history.append(plain(message))
                self._assistant(message)
            calls = history[-1].get("tool_calls") or []
            result = None
            if len(calls) == 1 and calls[0].get("function", {}).get("name") == "plan_trace":
                self._dispatch(calls[0])
                result = json.loads(self.messages[-1]["content"])
                history.append(plain(self.messages[-1]))
            if result is None:
                for call in calls:
                    history.append({"role": "tool", "tool_call_id": call["id"],
                                    "content": json.dumps({"error": "Use exactly one plan_trace call"})})
            if result is not None and "error" not in result:
                self.checkpoint()
                return
            self.manager_failures += 1
            correction_instruction = (
                "Correct the allocation using only supplied sources. Include supported/refuted/unknown "
                "criteria and conditional rules; do not claim findings.")
            if self.bundle.source["task_type"] == "openclaw":
                correction_instruction += (
                    " Resend exactly one plan_trace call. Its arguments must be one JSON object with "
                    "only the top-level keys assignments, decision_rules, uncertainty; never add an "
                    "arguments/function/parameters wrapper. Close the top-level object only once after "
                    "the uncertainty value. Keep the corrected plan short: one assignment and one "
                    "decision rule unless independent questions need more. Use one or two short exact "
                    "quotes per assignment. For each quote, copy text from the source_inventory entry "
                    "whose source_id you specify; a quote from another source is invalid even for the "
                    "same call. Recheck the reported failing field; do not infer or rewrite source text.")
            correction = {"role": "user", "content": json.dumps({
                "error": result.get("error") if result else "Use exactly one plan_trace call",
                "instruction": correction_instruction})}
            history.append(correction)
            self._log_message(correction)
            self.checkpoint()
        if self.tools.manager_plan is None:
            raise ToolError("invalid_manager_plan", "manager plan validation failed three times")

    def _trace(self):
        """One durable query/assessment conversation per manager assignment."""
        self._manage_trace()
        query_schema = next(s for s in SCHEMAS if s["function"]["name"] == "query_logs")
        for case in self.tools.windows.windows:
            cid = case["candidate_id"]
            if cid in self.tools.window_conclusions:
                continue
            if cid not in self.trace_histories:
                inputs = {**trace_packet(self.tools, case), "benchmark_scope": benchmark_scope(self.bundle.source),
                          "candidate_limits": {"top_k": 1}, "query_language": documentation()}
                if self.bundle.source["task_type"] == "openclaw":
                    inputs["task_outcome"] = outcome_context(self.tools)
                self.trace_histories[cid] = [
                    {"role": "system", "content": self._prompt("TRACE")},
                    {"role": "user", "content": json.dumps(inputs, ensure_ascii=False)}]
                self._log_message(self.trace_histories[cid][-1])
                self.checkpoint()
            history = self.trace_histories[cid]
            self.tools.current_trace_case = cid
            try:
                while cid not in self.tools.window_conclusions:
                    name = "assess" if cid in self.tools.trace_queries else "query_logs"
                    failure_key = cid + ":" + name
                    if self.trace_failures.get(failure_key, 0) >= 3:
                        break
                    # Persisted assistant calls may need dispatch after a restart.
                    if history[-1]["role"] != "assistant":
                        if self.turns >= self.config["max_turns"]:
                            raise BudgetExceeded("assistant turn limit reached")
                        if self.client.recovered_message and self.client.recovered_phase == "TRACE":
                            message = self.client.recovered_message
                            self.client.recovered_message = None
                        else:
                            message = self.client.complete(plain(history),
                                [query_schema if name == "query_logs" else schema_for(self.bundle.source)], phase="TRACE")
                        history.append(plain(message))
                        self._assistant(message)  # checkpoints history before mutation
                    message = history[-1]
                    calls = message.get("tool_calls") or []
                    result = None
                    if len(calls) == 1:
                        call = calls[0]
                        # A committed query can precede the history checkpoint.
                        prior = self.tools.completed.get(call.get("id"))
                        actual = call.get("function", {}).get("name")
                        if actual == name or prior is not None:
                            self._dispatch(call)
                            result = json.loads(self.messages[-1]["content"])
                            history.append(plain(self.messages[-1]))
                    if result is None:
                        for call in calls:
                            history.append({"role": "tool", "tool_call_id": call["id"],
                                "content": json.dumps({"error": "Use exactly one " + name + " call"})})
                    if result is not None and "error" not in result:
                        # A SQL validation error is query data and still consumes
                        # the single query opportunity. No SQL repair is offered.
                        self.checkpoint()
                        continue
                    self.trace_failures[failure_key] = self.trace_failures.get(failure_key, 0) + 1
                    correction = {"role": "user", "content": json.dumps({
                        "error": result.get("error") if result else "Use exactly one " + name + " call",
                        "instruction": (
                            "Call query_logs exactly once with the active candidate ID and one SQL string. "
                            "The query has not been committed; correct the tool-call arguments using query_language."
                            if cid not in self.tools.trace_queries else
                            "Call assess with at most one step and exact module quotes from existing evidence. "
                            "Use empty candidates with uncertainty if insufficient. Retrieval is closed.")})}
                    history.append(correction)
                    self._log_message(correction)
                    self.checkpoint()
            finally:
                del self.tools.current_trace_case
        if window_coverage(self.tools)["trace_complete"]:
            if any(e["candidates"] for e in self.tools.window_conclusions.values()) or self.bundle.source["task_type"] == "openclaw":
                self.tools.phase = "SUBMIT"
            else:
                self.tools.result = self.tools.stop_result("no_candidate", "TRACE completed with no candidates; SUBMIT skipped")
        else:
            self.tools.result = self.tools.stop_result("uncertain", "TRACE response validation failed three times")
        self.checkpoint()

    def _direct_submit(self):
        if not submit_packet(self.tools)["candidates"] and self.bundle.source["task_type"] != "openclaw":
            self.tools.result = self.tools.stop_result("no_candidate", "TRACE completed with no candidates; SUBMIT skipped")
            self.checkpoint()
            return
        if self.messages[-1]["role"] == "assistant":
            message = self.messages[-1]
            try:
                if message.get("tool_calls"):
                    raise ToolError("invalid_direct_submit", "SUBMIT has no tools; return plain JSON")
                commit_direct(self.tools, parse_submit(message.get("content")), self.turns)
            except (ValueError, TypeError, KeyError) as exc:
                self.tools.result = self.tools.stop_result("error", f"direct SUBMIT response rejected: {exc}")
                self.tools.result.update(policy=policy_for(self.bundle.source), uncertainty=str(exc))
            self.checkpoint()
            return
        if self.turns >= self.config["max_turns"]:
            raise BudgetExceeded("assistant turn limit reached")
        self.messages.append({"role": "user", "content": json.dumps(submit_packet(self.tools), ensure_ascii=False)})
        self._log_message(self.messages[-1])
        self.checkpoint()
        self._assistant(self.client.complete(plain(self.messages), [], phase="SUBMIT"))

    def run(self):
        try:
            while self.tools.result is None:
                if self.tools.phase == "TRACE":
                    self._activate("TRACE")
                    self._trace()
                    continue
                if self.tools.phase == "SUBMIT":
                    self._activate("SUBMIT")
                    self._direct_submit()
                    continue
                pending = self._pending_calls()
                if pending:
                    if (self.tools.phase == "ROUTE"
                            and route_progress(self.messages, self.tools.completed)["consecutive_rounds_without_new_evidence"] >= 3
                            and any(c.get("function", {}).get("name") not in {"route_cases", "finish"} for c in pending)):
                        self.tools.result = self.tools.stop_result("uncertain", "ROUTE requested more retrieval after a decision-only turn")
                        break
                    for call in pending:
                        if self.tools.result is not None:
                            break
                        self._dispatch(call)
                    continue
                self._activate(self.tools.phase)
                if self.turns >= self.config["max_turns"]:
                    raise BudgetExceeded("assistant turn limit reached")
                progress = route_progress(self.messages, self.tools.completed) if self.tools.phase == "ROUTE" else {}
                stalled = progress.get("consecutive_rounds_without_new_evidence", 0)
                if stalled >= 4:
                    self.tools.result = self.tools.stop_result("uncertain", "ROUTE made no valid decision after a decision-only turn")
                    break
                decision_only = stalled >= 3
                self.messages.append({"role": "user", "content": json.dumps({
                    "phase": self.tools.phase, "coverage": window_coverage(self.tools),
                    "remaining_tool_calls": self.tools.max_tool_calls - self.tools.call_count,
                    "retrieval_progress": progress,
                    "instruction": ("Repeated retrieval added no evidence. Reuse the retained results and call route_cases for supported leads, or finish uncertain. No more retrieval this turn."
                                    if decision_only else "Read the original task and requirements before routing."
                                    if self.tools.phase == "MAP" else "Use the evidence to route plausible causes for TRACE, or finish uncertain if unsupported. Do not repeat an unchanged retrieval.")})})
                self._log_message(self.messages[-1])
                self.checkpoint()
                allowed = {"route_cases", "finish"} if decision_only else PHASE_TOOLS[self.tools.phase]
                schemas = [s for s in SCHEMAS if s["function"]["name"] in allowed]
                message = self.client.complete(self._context(), schemas, phase=self.active_phase)
                self._assistant(message)
                if not message.get("tool_calls"):
                    if self.text_only_turns >= 3:
                        self.tools.result = self.tools.stop_result("uncertain", "model repeatedly returned text without a tool decision")
        except BudgetExceeded as exc:
            self.tools.result = self.tools.stop_result("budget_exhausted", str(exc))
        except ToolError as exc:
            self.tools.result = self.tools.stop_result("error", f"{exc.code}: {exc}")
        except KeyboardInterrupt:
            self.tools.result = self.tools.stop_result("uncertain", "interrupted; resume from checkpoint")
        result = self.tools.result
        result.update(run_id=self.output.parent.name, trajectory_id=self.bundle.record.tid,
                      canonical_fingerprint=self.bundle.fingerprint, phase=self.tools.phase,
                      route_history=self.tools.route_history, usage={**self.client.snapshot(),
                      "tool_calls": self.tools.call_count, "assistant_turns": self.turns})
        result["evaluation_mode"] = MODE
        if self.tools.claim_parser.folder:
            usage_path = self.tools.claim_parser.folder / "usage_summary.json"
            if usage_path.exists():
                result["usage"]["claim_parsing"] = read_json(usage_path)
        result["policy"] = policy_for(self.bundle.source)
        if self.bundle.source["task_type"] == "openclaw":
            result["task_outcome"] = outcome_context(self.tools)
            result["telemetry"] = plain(self.bundle.source.get("telemetry", {}))
            if result.get("root"):
                result["root"]["source_spans"] = [row for row in result["telemetry"].get("source_map", [])
                    if row["action_step"] == result["root"]["step"]]
        self.tools.save()
        self.checkpoint()
        write_json(self.output / "result.json", result)
        write_json(self.output / "usage.json", result["usage"])
        return result
