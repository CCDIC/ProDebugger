"""Ranked root-step candidates independently retained per bounded ROUTE window."""
from .benchmark_scope import ALLOWED, allowed, is_openclaw, scope, validate_cause
from .evidence_tools import ToolError
from .record_bridge import digest
from local_judgement import MODULES, SLOTS
from .session_sources import source_text, owned_facts, slots_for

MODE = "independent-window-candidates/v3-root-role"
FIELDS = ("status", "is_root_cause", "step", "module", "fault_class", "output_quote",
          "proposition", "reasoning", "immediate_effect", "uncertainty", "requirement_links")
SCHEMA = {"type": "function", "function": {"name": "assess",
    "description": "Assess one original step with same-step module alternatives. Distinguish whether a fault exists (status) from whether it originates the investigated evidence (is_root_cause: true/false/null). A confident non-root fault still returns its step. Empty list requires uncertainty.",
    "parameters": {"type": "object", "additionalProperties": False,
        "required": ["window_id", "candidates", "uncertainty"], "properties": {
        "window_id": {"type": "string"}, "uncertainty": {"type": "string"},
        "candidates": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": list(FIELDS),
        "properties": {
            **{k: {"type": "string"} for k in FIELDS if k not in
               {"status", "is_root_cause", "step", "module", "fault_class", "requirement_links"}},
            "status": {"enum": ["error", "uncertain"]},
            "is_root_cause": {"type": ["boolean", "null"], "description": "true: supported origin candidate for this ROUTE evidence chain; false: this fault is not its root; null: causal role unresolved. Explain using reasoning and uncertainty."},
            "step": {"type": "integer", "minimum": 1},
            "module": {"enum": list(ALLOWED)},
            "fault_class": {"type": ["string", "null"]},
            "requirement_links": {"type": "array", "items": {"type": "string"}}}}}}}}}


def schema_for(task_type=None):
    if not is_openclaw(task_type):
        return SCHEMA
    from copy import deepcopy
    result = deepcopy(SCHEMA)
    candidate = result["function"]["parameters"]["properties"]["candidates"]["items"]
    candidate["properties"]["module"]["enum"] = list(allowed(task_type))
    return result


def packet(tools, window):
    source = tools.bundle.source["steps"]
    def raw(item):
        return {"step": item["t"], "env_text": item["env_text"],
                "module_text": {m: item["parts"].get(m) for m in MODULES},
                "admissible": item["admissible"],
                "next_env_text": source[item["t"]]["env_text"] if item["t"] < len(source) else None}
    lo, hi = window["lo"], window["hi"]
    selected = {k: window[k] for k in ("candidate_id", "lo", "hi")}
    return {"schema": "raw-window-input/v1", "evaluation_mode": MODE,
            "benchmark_scope": scope(tools.bundle.source), "task": tools.bundle.source["task"],
            "candidate_limits": {"top_p": tools.trace_top_p, "top_k": tools.trace_top_k,
                                 "top_k_unit": "distinct_root_steps_per_window"},
            "requirement_ids": sorted(tools.index.requirements), "window_version": tools.windows.version,
            "window": selected, "windows": [selected], "evaluation_steps": list(range(lo, hi + 1)),
            "prior_context": [raw(s) for s in source if s["t"] < lo],
            "window_steps": [raw(source[t - 1]) for t in range(lo, hi + 1)],
            "evidences": [e for e in tools.index.evidence.values() if lo <= e["t"] <= hi],
            "next_environment": {"step": hi + 1, "env_text": source[hi]["env_text"]} if hi < len(source) else None}


def coverage(tools):
    from .query_trace import MODE as QUERY_MODE
    if getattr(tools, "trace_protocol", None) == QUERY_MODE:
        from .query_trace import coverage as query_coverage
        return query_coverage(tools)
    windows = tools.windows.windows
    selected = sorted({t for w in windows for t in range(w["lo"], w["hi"] + 1)})
    completed = [w for w in tools.windows.windows if w["candidate_id"] in tools.window_conclusions]
    done = sorted({t for w in completed for t in range(w["lo"], w["hi"] + 1)})
    return {"windows": [{**w, "missing_steps": [] if w in completed else list(range(w["lo"], w["hi"] + 1))}
                        for w in windows], "window_version": tools.windows.version,
            "selected_steps": selected, "assessed_steps": done,
            "missing_steps": sorted(set(selected) - set(done)),
            "unassessed_steps": sorted(set(range(1, tools.index.length + 1)) - set(done)),
            "trace_complete": bool(windows) and len(completed) == len(windows),
            "completed_windows": [w["candidate_id"] for w in completed],
            "missing_windows": [w["candidate_id"] for w in windows if w not in completed],
            "assessment_unit": "window_candidate_list"}


def validate_quote(tools, step, module, quote):
    text = source_text(tools.bundle.source, step, module)
    if not isinstance(quote, str) or not quote.strip() or quote not in (text or ""):
        raise ToolError("invalid_window_root", f"Quote at step {step} module {module} is not an exact source substring: {quote!r}")


def normalize_candidate(tools, answer, window):
    if not isinstance(answer, dict) or set(answer) != set(FIELDS):
        raise ToolError("invalid_window_root", "candidate requires exactly the root fields")
    for key in ("proposition", "reasoning", "immediate_effect", "uncertainty", "output_quote"):
        if not isinstance(answer[key], str):
            raise ToolError("invalid_window_root", f"{key} must be a string")
    if not answer["reasoning"].strip() or not answer["proposition"].strip():
        raise ToolError("invalid_window_root", "reasoning and proposition are required")
    if answer["is_root_cause"] is not None and type(answer["is_root_cause"]) is not bool:
        raise ToolError("invalid_window_root", "is_root_cause must be true, false or null")
    if answer["is_root_cause"] is None and not answer["uncertainty"].strip():
        raise ToolError("invalid_window_root", "unresolved causal role requires uncertainty")
    links = answer["requirement_links"]
    if not isinstance(links, list) or any(not isinstance(k, str) or k not in tools.index.requirements for k in links):
        raise ToolError("invalid_window_root", "invalid requirement_links")
    t, module = answer["step"], answer["module"]
    permitted = allowed(tools.bundle.source)
    if type(t) is not int or not window["lo"] <= t <= window["hi"] or module not in permitted or module not in slots_for(tools.bundle.source):
        raise ToolError("invalid_window_root", "candidate must identify an in-window step/module")
    if answer["fault_class"] is not None and answer["fault_class"] not in permitted[module]:
        raise ToolError("invalid_window_root", "fault_class is outside benchmark scope")
    if answer["status"] == "error":
        if answer["fault_class"] not in permitted[module]:
            raise ToolError("invalid_window_root", "fault_class is outside benchmark scope")
        if not answer["proposition"].strip() or not answer["immediate_effect"].strip():
            raise ToolError("invalid_window_root", "error requires proposition and immediate_effect")
        validate_cause(module, answer, tools.bundle.source)
    elif answer["status"] == "uncertain":
        if not answer["uncertainty"].strip():
            raise ToolError("invalid_window_root", "uncertain candidate requires explicit uncertainty")
    else:
        raise ToolError("invalid_window_root", "status must be error or uncertain")
    validate_quote(tools, t, module, answer["output_quote"])
    owned = owned_facts(tools.index.facts.values(), tools.bundle.source, t, module)
    if not owned:
        raise ToolError("invalid_window_root", "candidate has no existing module Fact")
    matching = [f for f in owned if f.span and (f.span in answer["output_quote"] or answer["output_quote"] in f.span)]
    fid = (max(matching, key=lambda f: len(f.span)) if matching else owned[0]).fid
    return {**answer, "primary_fid": fid, "evidence_for": [fid], "evidence_against": []}


def commit(tools, answer, call_id):
    if not isinstance(answer, dict) or set(answer) != {"window_id", "candidates", "uncertainty"}:
        raise ToolError("invalid_window_root", "assess requires window_id, candidates and uncertainty")
    window = next((w for w in tools.windows.windows if w["candidate_id"] == answer["window_id"]), None)
    if window is None or answer["window_id"] != getattr(tools, "current_trace_window", answer["window_id"]):
        raise ToolError("invalid_window_root", "window_id must match the requested window")
    if not isinstance(answer["candidates"], list) or not isinstance(answer["uncertainty"], str):
        raise ToolError("invalid_window_root", "candidates must be a list and uncertainty a string")
    if not answer["candidates"] and not answer["uncertainty"].strip():
        raise ToolError("invalid_window_root", "empty candidates requires an evidence-gap explanation")
    normalized, seen = [], set()
    for index, candidate in enumerate(answer["candidates"], 1):
        try:
            item = normalize_candidate(tools, candidate, window)
            key = digest(candidate)
            if key in seen:
                raise ToolError("invalid_window_root", "identical duplicate candidate")
            seen.add(key)
            normalized.append(item)
        except (ValueError, TypeError, KeyError) as exc:
            raise ToolError("invalid_window_root", f"candidate[{index}]: {exc}") from exc
    root_steps = {candidate["step"] for candidate in normalized}
    if len(root_steps) > tools.trace_top_k:
        raise ToolError("invalid_window_root", f"assess permits at most trace_top_k={tools.trace_top_k} distinct root steps; "
                        "rank by source evidence and task contribution, then resubmit the strongest steps "
                        "with their material module alternatives; do not take a chronological prefix")
    event = {"schema": MODE, "window_id": answer["window_id"], "window": window,
             "packet_digest": digest(packet(tools, window)), "response_turn": call_id,
             "after_tool_calls": tools.call_count, "conclusion": answer, "candidates": normalized}
    old = tools.window_conclusions.get(answer["window_id"])
    if old and digest(old["conclusion"]) != digest(answer):
        raise ToolError("invalid_window_root", "window already has a different committed conclusion")
    tools.window_conclusions[answer["window_id"]] = old or event
    return old or event
