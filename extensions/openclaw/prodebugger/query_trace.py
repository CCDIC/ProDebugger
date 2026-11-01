"""One evidence-driven query per TRACE task, preserving the SUBMIT contract."""
import json

from .record_bridge import digest, plain
from .evidence_tools import ToolError, integer
from .query_dsl import execute as execute_query
from .evidence_quality import annotated_fact, evidence_quality
from .claim_parser import MENTAL, fragment, source_text, cards, payload as claim_payload
from .session_sources import is_session, message_event, feedback_text

MODE = "evidence-query-step/v2-elements"


class TraceCases:
    """Legacy window field names are transport metadata, never retrieval bounds."""
    def __init__(self, index):
        self.index, self.version, self.windows = index, 0, []

    def configure(self, candidates):
        if not isinstance(candidates, list) or not candidates:
            raise ToolError("invalid_arguments", "candidates must be nonempty")
        cases, ids = [], set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {"candidate_id", "issue_step", "issue_fids", "goal_links", "hypothesis"}:
                raise ToolError("invalid_arguments", "candidate needs candidate_id,issue_step,issue_fids,goal_links,hypothesis")
            cid = candidate["candidate_id"]
            if not isinstance(cid, str) or not cid.strip() or cid in ids:
                raise ToolError("invalid_arguments", "candidate_id must be unique")
            step = integer(candidate["issue_step"], "issue_step", 1, self.index.length)
            refs = candidate["issue_fids"]
            if not isinstance(refs, list) or not refs:
                raise ToolError("invalid_arguments", "provide at least one original issue Fact")
            if any(self.index.fact(fid).t != step for fid in refs):
                raise ToolError("invalid_arguments", "issue_fids must belong to issue_step")
            if not isinstance(candidate["goal_links"], list) or any(g not in self.index.requirements for g in candidate["goal_links"]):
                raise ToolError("invalid_arguments", "invalid goal_links")
            if not isinstance(candidate["hypothesis"], str) or not candidate["hypothesis"].strip():
                raise ToolError("invalid_arguments", "hypothesis required")
            # Point-valued anchor metadata keeps direct_submit.packet unchanged.
            # A result can select any retrieved step, including outside this point.
            cases.append({**candidate, "lo": step, "hi": step})
            ids.add(cid)
        if digest(cases) != digest(self.windows):
            self.windows, self.version = cases, self.version + 1


def query_context(tools):
    """Share executable relation names and original bounds with manager and workers."""
    return {"task_type": tools.bundle.source["task_type"],
            "step_range": [1, tools.index.length], "default_scope": "full_trajectory_including_steps_before_anchor",
            "relation_names": sorted(tools.bundle.record.relations.declared()),
            "observed_action_relations": sorted({f.rel for f in tools.bundle.record.F if f.slot == "act"}),
            "source_modules": ["observation", "memory", "reflection", "plan", "action"] + (["message"] if is_session(tools.bundle.source) else []),
            "message_instruction": "For recorded sessions use SEARCH_TEXT(term,'message') or SOURCE(message_fid,term) for exact public assistant/user utterances. A message is not internal memory; assess module=message only for assistant utterances with existing mind-side Facts.",
            "bounds_instruction": "Use these original step bounds. The anchor is a lead, not a lower bound. Omit optional bounds for a full-range element search; task-level Facts may have step 0."}


def visible_claim_fragments(tools, case):
    from .trace_manager import assignment
    source, t = tools.bundle.source, case["issue_step"]
    result = [fragment(source, t, module) for module in sorted(MENTAL)
              if source_text(source, t, module)]
    for e in assignment(tools, case["candidate_id"]).get("manager_assignment", {}).get("evidence", []):
        if e["module"] in MENTAL:
            original = source_text(source, e["step"], e["module"])
            if original.count(e["quote"]) == 1:
                start = original.index(e["quote"])
                result.append(fragment(source, e["step"], e["module"], start, start + len(e["quote"])))
    return result


def packet(tools, case):
    from .trace_manager import assignment
    step = tools.bundle.source["steps"][case["issue_step"] - 1]
    facts = [annotated_fact(tools.bundle.record, tools.index.fact(fid)) for fid in case["issue_fids"]]
    entities = sorted({str(e) for fid in case["issue_fids"] for e in tools.index.fact(fid).args})
    return {"schema": MODE, "task": tools.bundle.source["task"], **assignment(tools, case["candidate_id"]),
            "candidate": {k: case[k] for k in ("candidate_id", "issue_step", "issue_fids", "goal_links", "hypothesis")},
            "facts": facts, "entities": entities, "requirement_ids": sorted(tools.index.requirements),
            "agent_statements": claim_payload(cards(tools.bundle.source, visible_claim_fragments(tools, case))),
            "query_context": query_context(tools),
            "source": {"step": step["t"], "observation": step["env_text"], "modules": {**step["parts"],
                       **({"message": message_event(tools.bundle.source, step["t"]).get("text", "")}
                          if message_event(tools.bundle.source, step["t"]) else {})},
                       "admissible": step["admissible"],
                       "next_observation": feedback_text(tools.bundle.source, step["t"])},
            "rule_evidence": [e for e in tools.index.evidence.values() if set(e["refs"]) & set(case["issue_fids"])],
            "parse_audits": [a for a in getattr(tools.bundle.record, "parse_audits", []) if a["step"] in {0, step["t"]}],
            "evidence_quality": evidence_quality(tools.bundle.record, step["t"]),
            "instruction": "The anchor and assigned excerpts are the initial evidence, not full-history coverage. "
                           "Investigate the manager question and rivals with one element SQL query, then assess at most one step. "
                           "The anchor is not a retrieval boundary; source claims and parsing metadata are not verified facts."}


def query(tools, candidate_id, sql, *, query_executor=None):
    if candidate_id not in {c["candidate_id"] for c in tools.windows.windows}:
        raise ToolError("invalid_candidate", "unknown TRACE candidate")
    if candidate_id != getattr(tools, "current_trace_case", candidate_id):
        raise ToolError("invalid_candidate", "query must belong to the active TRACE task")
    if candidate_id in tools.trace_queries:
        raise ToolError("query_already_used", "each TRACE task has exactly one query opportunity")
    # Both successful and invalid SQL executions consume the opportunity.
    result = (query_executor or execute_query)(tools, sql)
    if "error" not in result:
        case = next(c for c in tools.windows.windows if c["candidate_id"] == candidate_id)
        # One logical batch for all visible unresolved clauses, including the
        # anchor/assigned evidence. The parser can recover a truncated response
        # using only missing units; it cannot issue another SQL or expand sources.
        result["agent_statements"] = tools.claim_parser.enrich(
            visible_claim_fragments(tools, case) + result.get("claim_fragments", []))
    result.update(candidate_id=candidate_id)
    tools.trace_queries[candidate_id] = result
    return result


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def commit(tools, answer, call_id):
    from .window_trace import normalize_candidate, MODE as OUTPUT_SCHEMA
    if not isinstance(answer, dict) or set(answer) != {"window_id", "candidates", "uncertainty"}:
        raise ToolError("invalid_trace_result", "preserve assess fields: window_id,candidates,uncertainty")
    cid = answer["window_id"]
    case = next((c for c in tools.windows.windows if c["candidate_id"] == cid), None)
    if case is None or cid not in tools.trace_queries:
        raise ToolError("invalid_trace_result", "TRACE must use its query before assessing")
    if cid != getattr(tools, "current_trace_case", cid):
        raise ToolError("invalid_trace_result", "assessment must belong to active task")
    if not isinstance(answer["candidates"], list) or not isinstance(answer["uncertainty"], str):
        raise ToolError("invalid_trace_result", "invalid candidates/uncertainty")
    if not answer["candidates"] and not answer["uncertainty"].strip():
        raise ToolError("invalid_trace_result", "empty result requires uncertainty")
    normalized = [normalize_candidate(tools, a, {"lo": 1, "hi": tools.index.length}) for a in answer["candidates"]]
    if len({c["step"] for c in normalized}) > 1:
        raise ToolError("invalid_trace_result", "return at most ONE original step; same-step module alternatives are allowed")
    if len({digest(c) for c in normalized}) != len(normalized):
        raise ToolError("invalid_trace_result", "duplicate candidates")
    inputs = packet(tools, case)
    assigned_quotes = [e["quote"] for e in inputs.get("manager_assignment", {}).get("evidence", [])]
    visible = list(_strings(inputs["source"]["modules"])) + assigned_quotes + list(_strings(tools.trace_queries[cid].get("rows", [])))
    for candidate in normalized:
        if not any(candidate["output_quote"] in s for s in visible):
            raise ToolError("invalid_trace_result", "quote was not present in ROUTE source or returned query rows")
    event = {"schema": OUTPUT_SCHEMA, "window_id": cid, "window": case,
             "packet_digest": digest({"evidence": packet(tools, case), "query": tools.trace_queries[cid]}),
             "response_turn": call_id, "after_tool_calls": tools.call_count,
             "conclusion": answer, "candidates": normalized}
    previous = tools.window_conclusions.get(cid)
    if previous and digest(previous["conclusion"]) != digest(answer):
        raise ToolError("invalid_trace_result", "TRACE result already committed")
    tools.window_conclusions[cid] = previous or event
    return previous or event


def coverage(tools):
    cases = tools.windows.windows
    retrieved = {t for q in tools.trace_queries.values() for t in q.get("returned_steps", [])}
    selected = sorted(retrieved | {c["issue_step"] for c in cases})
    assessed = sorted({c["step"] for e in tools.window_conclusions.values() for c in e["candidates"]})
    done = [c["candidate_id"] for c in cases if c["candidate_id"] in tools.window_conclusions]
    missing = [c["candidate_id"] for c in cases if c["candidate_id"] not in done]
    return {"windows": [{**c, "missing_steps": [c["issue_step"]] if c["candidate_id"] in missing else []} for c in cases],
            "window_version": tools.windows.version, "selected_steps": selected, "assessed_steps": assessed,
            "missing_steps": [c["issue_step"] for c in cases if c["candidate_id"] in missing],
            "unassessed_steps": sorted(set(range(1, tools.index.length + 1)) - set(assessed)),
            "trace_complete": bool(cases) and not missing, "completed_windows": done, "missing_windows": missing,
            "assessment_unit": "single_step_per_route_candidate"}
