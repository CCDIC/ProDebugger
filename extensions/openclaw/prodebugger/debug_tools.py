"""LLM tool schemas, scoped dispatch, event accounting, and deterministic replay."""
from __future__ import annotations

from pathlib import Path

from .record_bridge import append_json, digest, plain, write_json
from .evidence_tools import BudgetExceeded, EvidenceIndex, ToolError, integer
from .window_trace import SCHEMA as WINDOW_SCHEMA, coverage, schema_for
from .query_trace import TraceCases, MODE as TRACE_MODE, commit as commit_window, query as query_logs
from .direct_submit import POLICY, policy_for, outcome_context, commit as commit_direct, packet as submit_packet

import operators as OPS
from record import Entity
from session import Session

GLOBAL = {"mode": "global"}
SCOPE = {"type": "object", "properties": {"mode": {"enum": ["local", "global"]},
          "step": {"type": "integer", "minimum": 1}}, "required": ["mode"], "additionalProperties": False}
INT = {"type": "integer", "minimum": 0}
TEXT = {"type": "string"}
FIDS = {"type": "array", "items": INT}
ROWS = {"type": "array", "items": {"type": "object"}}


def schema(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required),
                           "additionalProperties": False}}}


from .trace_manager import SCHEMA as MANAGER_SCHEMA, commit as commit_manager

SCHEMAS = [
    MANAGER_SCHEMA,
    schema("view", "Read requirements, entity, scan, episodes, local_audits, changes or packet. Local scope hides next agent outputs.",
           {"spec": TEXT, "scope": SCOPE}, ["spec", "scope"]),
    schema("view_evidence", "Read native rule candidates and their original facts. No candidates does not prove no failure.",
           {"scope": SCOPE, "eid": INT, "kind": TEXT, "rule": TEXT, "status": TEXT,
            "steps": {"type": "array", "items": INT, "minItems": 2, "maxItems": 2},
            "requirement": TEXT, "include_refs": {"type": "boolean"}, "cursor": INT,
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ["scope"]),
    *[schema(name, "Retrieve a bounded typed provenance graph; links do not prove causation.",
             {key: INT, "scope": SCOPE, "direction": {"enum": ["backward", "forward", "goal"]},
              "edge_types": {"type": "array", "items": {"enum": ["basis", "situation", "purpose", "version", "context"]}},
              "depth": {"type": "integer", "minimum": 0, "maximum": 8},
              "max_nodes": {"type": "integer", "minimum": 1, "maximum": 500},
              "steps": {"type": "array", "items": INT, "minItems": 2, "maxItems": 2}}, [key, "scope"])
      for name, key in [("trace_evidence", "eid"), ("trace_fact", "fid")]],
    schema("route_cases", "Declare evidence-backed anchors for the TRACE manager to combine or allocate to investigators.",
           {"candidates": ROWS}, ["candidates"]),
    schema("query_logs", "One element-scoped SQL query over the full trajectory, including steps BEFORE the anchor. Prefer literal term/module lists and UNION ALL BY NAME; omit NULL placeholders and return only needed fields. Use query_context step bounds and relation names; omit optional bounds for full-range element search. Full steps/modules/fact dumps and RAW_STEP are forbidden. Track entities/states/actions or search specific source claims. Invalid SQL consumes the opportunity.",
           {"candidate_id": TEXT, "sql": {**TEXT, "description": "One SQL query with concise English -- comments inside this string: begin with -- Purpose:, then explain each retrieval branch and meaningful filters/bounds. Use newline separators, not # comments. Comments describe the inquiry, not assumed findings. Use single-quoted literal arguments and specific search terms, never generic 'search' for plan/reflection. No aggregate row/byte/source/function budgets; no full-step access."}}, ["candidate_id", "sql"]),
    WINDOW_SCHEMA,
    *[schema(name, "Inspect one original Fact with explicit visibility scope.",
             {"fid": INT, "scope": SCOPE}, ["fid", "scope"])
      for name in ("check", "span", "back", "up")],
    schema("state", "Read the state at a step.", {"step": INT, "scope": SCOPE}, ["step", "scope"]),
    schema("versions", "Read relation assignments of entities named kind:index.",
           {"relation": TEXT, "entities": {"type": "array", "items": TEXT}, "scope": SCOPE},
           ["relation", "entities", "scope"]),
    schema("neighbours", "Read nearby original Facts within scope.",
           {"fid": INT, "span": {"type": "integer", "minimum": 0, "maximum": 8}, "scope": SCOPE}, ["fid", "scope"]),
    *[schema(name, "Update candidate shortlist.", {"fids": FIDS}, ["fids"]) for name in ("hold", "drop")],
    schema("contrast", "Record a comparison of two or more original Facts.", {"fids": FIDS}, ["fids"]),
    schema("reroute", "Return to ROUTE to revise evidence cases.", {"reason": TEXT}, ["reason"]),
    schema("finish", "Finish without a diagnosed root. no_failure requires all modelled and unmodelled goals resolved; otherwise uncertain.",
           {"status": {"enum": ["uncertain", "no_failure"]}, "reason": TEXT}, ["status", "reason"]),
]
SCHEMA_BY_NAME = {s["function"]["name"]: s for s in SCHEMAS}
READ_TOOLS = {"view", "view_evidence", "trace_evidence", "trace_fact", "check", "span", "back", "up",
              "state", "versions", "neighbours", "hold", "drop", "contrast", "finish"}
PHASE_TOOLS = {"MAP": {"view", "finish"}, "ROUTE": READ_TOOLS | {"route_cases", "reroute"},
               "TRACE": {"plan_trace", "query_logs", "assess"}, "SUBMIT": set()}


def validate_json(value, contract, path="arguments"):
    kind = contract.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    if isinstance(kind, list):
        if not any(type(value) is types.get(option) for option in kind):
            raise ToolError("invalid_arguments", f"{path} must be one of {kind}")
        kind = next(option for option in kind if type(value) is types.get(option))
    if kind in types and type(value) is not types[kind]:
        raise ToolError("invalid_arguments", f"{path} must be {kind}")
    if "enum" in contract and value not in contract["enum"]:
        raise ToolError("invalid_arguments", f"{path} has an invalid enum value")
    if kind == "integer":
        if value < contract.get("minimum", value) or value > contract.get("maximum", value):
            raise ToolError("invalid_arguments", f"{path} exceeds numeric bounds")
    if kind == "array":
        if not contract.get("minItems", 0) <= len(value) <= contract.get("maxItems", len(value)):
            raise ToolError("invalid_arguments", f"{path} has invalid length")
        for i, item in enumerate(value):
            validate_json(item, contract.get("items", {}), f"{path}[{i}]")
    if kind == "object":
        props = contract.get("properties", {})
        missing = sorted(set(contract.get("required", [])) - set(value))
        extra = sorted(set(value) - set(props)) if contract.get("additionalProperties") is False else []
        errors = (["missing required fields: " + ", ".join(missing)] if missing else [])
        if extra:
            errors.append("unexpected fields: " + ", ".join(extra))
        if errors:
            raise ToolError("invalid_arguments", f"{path} has " + "; ".join(errors))
        for key, item in value.items():
            if key in props:
                validate_json(item, props[key], f"{path}.{key}")


class DebugTools:
    def __init__(self, bundle, output: Path | None = None, max_tool_calls=240, W=5,
                 *, trace_top_p=5, trace_top_k=2):
        self.bundle, self.output = bundle, output
        self.index = EvidenceIndex(bundle)
        self.session = Session(bundle.record)
        self.windows = TraceCases(self.index)
        self.trace_protocol = TRACE_MODE
        self.trace_queries = {}
        self.route_cases = []
        self.manager_plan = None
        from .claim_parser import ClaimParser
        self.claim_parser = ClaimParser(bundle.source)
        self.max_tool_calls, self.default_W = max_tool_calls, W
        self.trace_top_p = integer(trace_top_p, "trace_top_p", 1)
        self.trace_top_k = integer(trace_top_k, "trace_top_k", 1)
        self.call_count = 0
        self.events, self.completed = [], {}
        self.window_conclusions = {}
        self.direct_completed = {}
        self.phase, self.result, self.goal_map = "MAP", None, None
        self.route_history = []
        self.handlers = {"view": self._view, "view_evidence": self.index.view,
                         "trace_evidence": self._trace_evidence, "trace_fact": self._trace_fact,
                         "route_cases": self._trace_window, "assess": commit_window,
                         "plan_trace": lambda **kw: commit_manager(self, **kw),
                         "query_logs": lambda **kw: query_logs(self, **kw),
                         "reroute": self._reroute, "finish": self._finish}
        for op in ("check", "span", "back", "up", "state", "versions", "neighbours", "hold", "drop", "contrast"):
            self.handlers[op] = lambda _op=op, **kw: self._operator(_op, **kw)

    def _view(self, spec, scope):
        scope = self.index.scope(scope)
        source = spec.split("|", 1)[0].strip()
        if source.split(":", 1)[0] not in {"requirements", "entity", "changes", "scan", "episodes", "local_audits", "packet"}:
            raise ToolError("invalid_arguments", "view source is not in the experiment whitelist")
        requested_spec = spec
        if source == "local_audits":
            if "steps:" not in spec:
                step = scope.get("step", 1)
                hi = step if scope["mode"] == "local" else min(step + 4, self.index.length)
                spec += f" | steps:{step}:{hi}"
            if "detail:" not in spec:
                spec += " | detail:full"
        out = OPS.view(self.index.scoped_record(scope), spec)
        for key in ("candidate_family_count", "candidate_families_query", "candidate_detail_count", "candidate_details_query"):
            out.pop(key, None)
        if source == "local_audits":
            # Full raw module text is already in transition.module_text. Core's
            # per-module input/output/feedback prose repeats this shared material.
            out.pop("query_answer", None)
            for audit in out.get("audits", []):
                audit.pop("step_summary", None)
                for module in audit.get("modules", {}).values():
                    module.pop("review_context", None)
                    for fact in module.get("outputs", []):
                        fact.pop("support", None)  # basis refs remain; inspect via trace_fact.
            out["projection"] = ("llm-local-audit/v1; raw module text and owned Facts complete"
                                 if "detail:full" in spec else "summary audit; read detail:full for complete source")
        else:
            out.pop("query_answer", None)  # A second prose rendering of the same structured view.
        truncated = []
        def bounded(value, path):
            if isinstance(value, list):
                # Raw audit packets, module-owned Facts, and scalar refs stay complete.
                preserve = path == "audits" or ".modules." in path or all(not isinstance(x, (dict, list)) for x in value)
                if not preserve and len(value) > 8:
                    truncated.append({"path": path, "total": len(value), "returned": 8})
                    value = value[:8]
                return [bounded(v, f"{path}[{i}]") for i, v in enumerate(value)]
            if isinstance(value, dict):
                return {k: bounded(v, f"{path}.{k}" if path else k) for k, v in value.items()}
            return value
        out = bounded(out, "")
        if truncated:
            out["truncation"] = {"truncated": True, "sections": truncated,
                                 "instruction": "Use view with steps:lo:hi and section:<field>, or trace_fact/span for omitted source material."}
        out["requested_query"] = requested_spec
        if self.bundle.source["task_type"] == "openclaw":
            out["task_outcome"] = outcome_context(self)
            out["capture_coverage"] = self.bundle.source.get("telemetry", {}).get("coverage", {})
        out["parse_audit"] = {"unresolved": [{"step": a["step"], "kind": a["kind"], "reasons": a["reasons"]}
            for a in getattr(self.bundle.record, "parse_audits", []) if a["status"] == "unresolved"],
            "instruction": "Parse gaps are unknowns, not evidence of agent failure; inspect original text."}
        if source == "requirements" and scope["mode"] == "global" and "|" not in spec:
            self.goal_map = plain(out)
            if self.phase == "MAP":
                self.phase = "ROUTE"
        return out

    def _trace_evidence(self, eid, **kwargs):
        self.index.view(eid=eid, scope=kwargs["scope"])
        return self.index.trace(self.index.evidence[eid]["refs"], **kwargs)

    def _trace_fact(self, fid, **kwargs):
        return self.index.trace([fid], **kwargs)

    def _trace_window(self, candidates):
        if self.goal_map is None:
            raise ToolError("protocol_incomplete", "MAP must read requirements first")
        if isinstance(candidates, list) and len(candidates) > self.trace_top_p:
            raise ToolError("invalid_arguments", f"route_cases permits at most trace_top_p={self.trace_top_p} ranked anchors; "
                            "compare the evidence and resubmit the strongest anchors, not a chronological prefix")
        version = self.windows.version
        self.windows.configure(candidates)
        if self.windows.version != version:
            self.window_conclusions.clear()
            self.trace_queries.clear()
            self.route_cases = plain(self.windows.windows)
            self.manager_plan = None
            self.result = None
            self.route_history.append(plain({"version": self.windows.version, "windows": self.windows.windows}))
        self.phase = "TRACE"
        return {"canonical_fingerprint": self.index.fingerprint,
                "window_version": self.windows.version, "coverage": coverage(self)}

    def _operator(self, op, scope=None, **kwargs):
        if op in {"hold", "drop", "contrast"}:
            fids = kwargs["fids"]
            for fid in fids:
                self.index.fact(fid)
            if op == "contrast" and len(fids) < 2:
                raise ToolError("invalid_arguments", "contrast needs at least two Facts")
            return self.session.call(op, *fids) if op == "contrast" else getattr(self.session, op)(*fids)
        scope = self.index.scope(scope)
        record = self.index.scoped_record(scope)
        if op == "state":
            step = integer(kwargs["step"], "step", 0, self.index.length)
            if scope["mode"] == "local" and step > scope["step"]:
                raise ToolError("out_of_scope", "state exceeds local step")
            return OPS.state(record, step)
        if op == "versions":
            entities = tuple(Entity(*value.split(":", 1)) for value in kwargs["entities"])
            return OPS.versions(record, kwargs["relation"], entities)
        fid = kwargs["fid"]
        self.index.fact(fid, scope)
        args = [fid]
        if op == "neighbours":
            args.append(integer(kwargs.get("span", 1), "span", 0, 8))
        return OPS.ACTION_SPACE[op](record, *args)

    def _reroute(self, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ToolError("invalid_arguments", "re-route needs a reason")
        self.phase, self.result = "ROUTE", None
        return {"phase": "ROUTE", "reason": reason, "previous_windows": self.windows.windows}

    def _finish(self, status, reason):
        if status not in {"uncertain", "no_failure"} or not isinstance(reason, str) or not reason.strip():
            raise ToolError("invalid_arguments", "invalid finish status or reason")
        if status == "no_failure":
            if self.bundle.source["task_type"] == "openclaw":
                if outcome_context(self)["status"] != "success":
                    raise ToolError("protocol_incomplete", "no_failure requires a verified complete tool-result task contract")
            else:
                requirements = self._view("requirements", GLOBAL).get("requirements", [])
                if (not requirements or any(row.get("status", {}).get("state") != "satisfied" for row in requirements)
                        or any(a["status"] == "unresolved" for a in getattr(self.bundle.record, "parse_audits", []))):
                    raise ToolError("protocol_incomplete", "all requirements must be observed satisfied")
        self.result = self.stop_result(status, reason)
        return self.result

    def stop_result(self, status, reason):
        return {"schema": "debugger-result/v1", "policy": policy_for(self.bundle.source), "status": status,
                "accepted": False, "root": None, "supporting_evidence": [], "counterevidence": [],
                "causal_chain": [], "repair": None, "alternatives": submit_packet(self)["candidates"],
                "coverage": coverage(self), "stop_reason": reason}

    def execute(self, name, arguments, call_id, *, record=True, argument_error=None):
        request = {"name": name, "arguments": arguments}
        if argument_error is not None:
            request["argument_error"] = plain(argument_error)
        request_hash = digest(request)
        if call_id in self.completed:
            if self.completed[call_id]["request_hash"] != request_hash:
                raise ToolError("invalid_id", "tool_call_id reused with different arguments")
            return self.completed[call_id]["result"]
        if self.call_count >= self.max_tool_calls:
            raise BudgetExceeded("tool call limit reached")
        self.call_count += 1
        event = {"ordinal": self.call_count, "tool_call_id": call_id, "name": name,
                 "arguments": plain(arguments), "request_hash": request_hash, "phase": self.phase}
        if argument_error is not None:
            event["argument_error"] = plain(argument_error)
        if record and self.output:
            append_json(self.output / "tool_calls.jsonl", {**event, "event": "pending"})
        result = {"schema": "debugger-tool/v1", "run_id": self.output.parent.name if self.output else None,
                  "trajectory_id": self.bundle.record.tid,
                  "scope": arguments.get("scope", GLOBAL) if isinstance(arguments, dict) else GLOBAL}
        try:
            if argument_error is not None:
                raise ToolError(argument_error["code"], argument_error["message"])
            if name not in self.handlers or not isinstance(arguments, dict):
                raise ToolError("invalid_arguments", "unknown tool or non-object arguments")
            parameters = (schema_for(self.bundle.source) if name == "assess" else SCHEMA_BY_NAME[name])["function"]["parameters"]
            validate_json(arguments, parameters)
            if name not in PHASE_TOOLS[self.phase]:
                raise ToolError("protocol_incomplete", f"{name} is unavailable in {self.phase}")
            if self.result is not None:
                raise ToolError("protocol_incomplete", "run is finished")
            if name == "assess":
                result["data"] = plain(commit_window(self, arguments, call_id))
            else:
                result["data"] = plain(self.handlers[name](**arguments))
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            result["error"] = {"code": getattr(exc, "code", "invalid_arguments"),
                               "message": str(exc), "retryable": False}
            if argument_error is not None:
                result["error"].update(plain(argument_error))
        event.update(event="committed", result=result, result_digest=digest(result))
        self.events.append(event)
        self.completed[call_id] = event
        if record and self.output:
            append_json(self.output / "tool_calls.jsonl", event)
            if name == "assess" and "data" in result:
                append_json(self.output / "window_trace.jsonl", result["data"])
            if name == "query_logs" and "data" in result:
                append_json(self.output / "trace_queries.jsonl", result["data"])
            self.save()
        return result

    def restore(self, events, direct_events=()):
        for event in events:
            if event.get("event") != "committed":
                continue
            output = self.execute(event["name"], event["arguments"], event["tool_call_id"], record=False,
                                  argument_error=event.get("argument_error"))
            if digest(output) != event["result_digest"]:
                raise ToolError("source_mismatch", "event replay output differs from checkpoint source")
        if (self.window_conclusions and coverage(self)["trace_complete"]
                and (any(e["candidates"] for e in self.window_conclusions.values()) or self.bundle.source["task_type"] == "openclaw")):
            self.phase = "SUBMIT"
        for event in direct_events:
            if event["after_tool_calls"] != self.call_count:
                raise ToolError("source_mismatch", "direct answer has no matching tool journal position")
            commit_direct(self, event["raw_answer"], event["response_turn"], record=False, expected=event)

    def save(self):
        if not self.output:
            return
        for name, data in {"goal_map": self.goal_map, "route_candidates": self.route_history,
                           "manager_plan": self.manager_plan,
                           "trace_windows": coverage(self),
                           "trace_graph": [e["result"]["data"] for e in self.events
                                           if e["name"] in {"trace_evidence", "trace_fact"} and "data" in e["result"]]}.items():
            write_json(self.output / f"{name}.json", data)
