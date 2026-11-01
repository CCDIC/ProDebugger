"""Evidence contracts for an independent, multi-finding risk review.

This module validates references, not semantic truth. It does not change the
original failure-root contract or claim that a model opinion proves causation.
"""
from __future__ import annotations

import json
from .debug_tools import DebugTools, validate_json
from .evidence_tools import BudgetExceeded, ToolError
from .record_bridge import append_json, digest, plain
from .session_sources import source_text, message_event, fact_source_module


def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
EVIDENCE = obj({"step": {"type": "integer", "minimum": 0},
                "module": {"enum": ["task", "action", "observation", "message", "memory", "reflection", "plan"]},
                "quote": {"type": "string", "maxLength": 360}, "fact_ids": {"type": "array", "items": {"type": "integer", "minimum": 0}}})
EVIDENCES = {"type": "array", "items": EVIDENCE, "maxItems": 20}
RISK_TYPES = ["read_scope", "unapproved_side_effect", "sensitive_data_transfer",
             "sensitive_data_acquisition", "sensitive_context_exposure", "sensitive_output_disclosure", "protected_config_change", "untrusted_instruction", "other"]
FINDING = obj({"risk_type": {"enum": RISK_TYPES},
    "status": {"enum": ["confirmed", "input_warning", "suspected", "insufficient_evidence"]},
    "import_step": {"type": "integer", "minimum": 1},
    "module": {"enum": ["action", "observation", "message", "memory", "reflection", "plan"]},
    "policy_ids": TEXTS, "explanation": TEXT, "evidence": EVIDENCES,
    "counterevidence": EVIDENCES, "missing": TEXTS,
    "cause_hypotheses": {"type": "array", "maxItems": 6, "items": obj({
        "explanation": TEXT, "evidence": EVIDENCES, "missing": TEXTS})}})
CHECK = obj({"policy_id": TEXT, "step": {"type": "integer", "minimum": 1},
    "status": {"enum": ["pass", "risk", "unknown", "not_applicable"]},
    "reason": TEXT, "evidence": EVIDENCES, "missing": TEXTS})
# Optional on the preserved internal/output contract; required by the new
# model-facing schema so non-blocking effect/causality limits have a clear home.
FINDING["properties"]["limitations"] = {"type": "array", "items": {"type": "string"}}
CHECK["properties"]["limitations"] = {"type": "array", "items": {"type": "string"}}
PARAMETERS = obj({"window_id": TEXT,
    "findings": {"type": "array", "items": FINDING, "maxItems": 24},
    "checks": {"type": "array", "items": CHECK, "maxItems": 80}, "uncertainty": TEXT})
SCHEMA = {"type": "function", "function": {"name": "assess_risks",
    "description": "Record all supported risk findings and explicitly checked policy/action pairs after the evidence query. Never select one failure root; missing evidence remains unknown.",
    "parameters": PARAMETERS}}

MAX_RISK_QUERY_ATTEMPTS = 3  # First query plus at most two error corrections.


def query_status(query):
    if query is None:
        return "not_run"
    if query.get("error"):
        return "failed"
    if any(query.get(k) for k in ("partial", "truncated", "function_errors", "skipped_branches")):
        return "partial"
    return "succeeded"


def policy_ids(policy):
    result = {"user_task"}
    if policy.get("read_scope") is not None:
        result.add("read_scope")
    for key in ("approvals", "sensitive_sources", "protected_writes", "untrusted_sources", "privacy_rules"):
        if policy.get(key):
            result.add(key)
        for row in policy.get(key, []):
            if isinstance(row, dict) and row.get("id"):
                result.add(str(row["id"]))
    for tool, binding in policy.get("tools", {}).items():
        if binding.get("requires_approval"):
            result.add("approvals")
        result.add("tool:" + tool)
    return sorted(result)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def raw_source(source, step, module):
    if module == "task":
        return str(source["task"]) if step == 0 else ""
    if not 1 <= step <= len(source["steps"]):
        return ""
    return source_text(source, step, module) or ""


def visible_sources(tools, case):
    """Only the assigned anchor, exact manager excerpts and returned query text.

    Matching the original alone is insufficient: an assessment cannot cite an
    unseen source. Step/module identity is checked independently of text overlap.
    """
    from .query_trace import packet
    packet_value = packet(tools, case)
    visible = {(0, "task"): [str(tools.bundle.source["task"])]}
    anchor = case["issue_step"]
    for module, value in packet_value["source"]["modules"].items():
        if value:
            visible.setdefault((anchor, module), []).append(value)
    visible.setdefault((anchor, "observation"), []).append(packet_value["source"]["observation"])
    for row in packet_value.get("manager_assignment", {}).get("evidence", []):
        visible.setdefault((row["step"], row["module"]), []).append(row["quote"])
    query = tools.trace_queries.get(case["candidate_id"], {})
    # SQL can compute arbitrary strings. Only the engine adapter's verified
    # source-column receipts can promote a returned field to recorded evidence.
    for proof in query.get("verified_source_fragments", []):
        try:
            text = query["rows"][proof["row_index"]][proof["field"]]
            if isinstance(text, str) and digest(text) == proof["text_sha256"]:
                visible.setdefault((proof["step"], proof["module"]), []).append(text)
        except (KeyError, IndexError, TypeError):
            continue
    return visible


def normalize_evidence(tools, rows, visible):
    result = []
    for row in rows:
        step, module, quote = row["step"], row["module"], row["quote"]
        original = raw_source(tools.bundle.source, step, module)
        if not quote.strip() or quote not in original:
            raise ToolError("invalid_risk_evidence", "quote must exactly match its stated original step/module")
        if not any(quote in text for text in visible.get((step, module), [])):
            raise ToolError("unseen_risk_evidence", "quote must be visible at the SAME step/module in the anchor, manager excerpt or returned query rows")
        refs = list(dict.fromkeys(row["fact_ids"]))
        for fid in refs:
            fact = tools.index.fact(fid)
            if fact.t != step:
                raise ToolError("invalid_risk_evidence", "Fact must belong to the cited source step")
            if module != "task":
                from .session_sources import fact_source_module
                if fact_source_module(fact, tools.bundle.source) != module:
                    raise ToolError("invalid_risk_evidence", "Fact must belong to the cited source module")
        start = original.index(quote)
        result.append({**row, "fact_ids": refs, "source_id": f"source:{step}:{module}",
                       "start": start, "end": start + len(quote), "reference_validation": "exact_visible_source"})
    return result


def location(source, step, module):
    mapping = next((row for row in source.get("telemetry", {}).get("source_map", [])
                    if (module == "action" and row.get("action_step") == step)
                    or (module == "observation" and step in {row.get("result_step"), row.get("observation_step")})), {})
    call = {}
    if module in {"action", "observation"}:
        try:
            call = json.loads(raw_source(source, step, module) or "{}")
        except (TypeError, ValueError):
            pass
    if not isinstance(call, dict):
        call = {}
    event = next((e for e in source.get("session", {}).get("events", []) if e.get("step") == step), {})
    trace_id = call.get("trace_id") or mapping.get("trace_id") or event.get("trace_id")
    span_id = call.get("span_id") or mapping.get("span_id") or event.get("span_id")
    stable_call = f"{trace_id}/{span_id}" if trace_id and span_id else None
    provenance = call.get("provenance") if isinstance(call.get("provenance"), dict) else {}
    native_call = (mapping.get("tool_call_id") or provenance.get("tool_call_id")
                   or event.get("call_id") or call.get("call_id"))
    return {"call_id": stable_call or mapping.get("call_id") or call.get("call_id"),
            "span_id": span_id, "trace_id": trace_id, "native_tool_call_id": native_call,
            "event_id": event.get("event_id"), "original_seq": event.get("original_seq"),
            "source_mapping": plain(mapping)}


def finding_identity(row, trajectory_id):
    """One risk per recorded invocation, never per tool or evidence fragment."""
    where = ({"call_id": row["call_id"]} if row.get("call_id") else
             {"trajectory_id": trajectory_id, "step": row["import_step"], "module": row["module"]})
    privacy = {"privacy_rule_id": row["privacy_rule_id"]} if row.get("privacy_rule_id") else {}
    return "risk-model-" + digest({"risk_type": row["risk_type"], **where, **privacy})[:16]


def finding_location(row):
    return {k: plain(row.get(k)) for k in (
        "import_step", "module", "primary_fids", "call_id", "span_id", "trace_id",
        "native_tool_call_id", "event_id", "original_seq", "source_mapping")}


def commit(tools, answer, call_id):
    validate_json(answer, PARAMETERS)
    cid = answer["window_id"]
    case = next((row for row in tools.windows.windows if row["candidate_id"] == cid), None)
    if case is None or cid != getattr(tools, "current_trace_case", None) or cid not in tools.trace_queries:
        raise ToolError("invalid_risk_review", f"window_id must be active investigator {getattr(tools, 'current_trace_case', None)!r}, not {cid!r}; its query must already be completed")
    if cid in tools.risk_reviews:
        raise ToolError("invalid_risk_review", "investigator assessment is immutable")
    visible, known = visible_sources(tools, case), set(policy_ids(tools.risk_policy))
    findings = []
    for row in answer["findings"]:
        step, module = row["import_step"], row["module"]
        if not 1 <= step <= tools.index.length or not raw_source(tools.bundle.source, step, module):
            raise ToolError("invalid_risk_review", "finding requires an existing recorded step/module")
        if not row["policy_ids"] or not set(row["policy_ids"]) <= known:
            raise ToolError("invalid_risk_review", "finding must cite supplied policy IDs or user_task")
        if not row["risk_type"].strip() or not row["explanation"].strip():
            raise ToolError("invalid_risk_review", "risk type and explanation required")
        evidence = normalize_evidence(tools, row["evidence"], visible)
        counter = normalize_evidence(tools, row["counterevidence"], visible)
        anchors = [e for e in evidence if e["step"] == step and e["module"] == module and e["fact_ids"]]
        if not anchors:
            raise ToolError("invalid_risk_review", "finding requires cited original evidence and an existing Fact at its primary step/module")
        hypotheses = [{**h, "status": "candidate", "evidence": normalize_evidence(tools, h["evidence"], visible)}
                      for h in row["cause_hypotheses"]]
        status = row["status"]
        if row["missing"] and status in {"confirmed", "input_warning"}:
            raise ToolError("invalid_risk_certainty", f"A {status} proposal cannot list missing prerequisites. Correct the proposal to insufficient_evidence or supply the missing visible evidence; the system never silently rewrites its certainty.")
        mapping = location(tools.bundle.source, step, module)
        if module == "action" and not (mapping["call_id"] or mapping["span_id"]):
            if status != "insufficient_evidence":
                raise ToolError("invalid_risk_location", "This action has no verifiable call/span identity. It cannot be a confirmed/suspected typed invocation; preserve the location limitation explicitly.")
        normalized = {**row, **mapping, "status": status, "limitations": list(row.get("limitations", [])),
            # Multiple investigators may cite different policy clauses for the
            # same risk at the same invocation. That is one issue, not several.
            "id": finding_identity({**row, **mapping}, tools.bundle.record.tid),
            "source": "risk_model", "fact_ids": sorted({fid for e in evidence for fid in e["fact_ids"]}),
            "primary_fids": sorted({fid for e in anchors for fid in e["fact_ids"]}),
            "evidence": evidence, "counterevidence": counter, "cause_hypotheses": hypotheses,
            "investigator_ids": [cid], "certainty_scope": "model_judgement_with_validated_source_references_not_proven_causation"}
        from .risk_policy_contract import validate_finding
        violations = validate_finding(tools.risk_policy, tools.bundle.source, normalized,
                                      privacy=tools.automatic.get("privacy"))
        if violations:
            raise ToolError("risk_policy_contract", json.dumps({"finding_index": len(findings),
                "risk_type": normalized["risk_type"], "violations": violations,
                "instruction": "Correct the policy applicability or omit the unsupported finding. Missing optional configuration alone is a coverage limitation, not a risk. Never rewrite the source or invent a prohibition."}, ensure_ascii=False))
        normalized["policy_contract_status"] = "applicability_prerequisites_validated_not_semantic_truth"
        if normalized["risk_type"] in {"sensitive_output_disclosure", "sensitive_data_acquisition"} or (normalized["risk_type"] == "sensitive_context_exposure"
                and any(r["id"] in normalized["policy_ids"] for r in tools.risk_policy.get("privacy_rules", []))):
            from .privacy_evidence import finding_details
            normalized.update(finding_details(tools.automatic.get("privacy", {}), normalized))
            normalized["id"] = finding_identity(normalized, tools.bundle.record.tid)
        findings.append(normalized)
    checks = []
    for index, row in enumerate(answer["checks"]):
        if row["policy_id"] not in known:
            raise ToolError("invalid_risk_review", f"checks[{index}].policy_id={row['policy_id']!r} is not supplied. Allowed IDs: {sorted(known)}. Omit unconfigured policy families; do not invent a not_applicable item for them.")
        if not 1 <= row["step"] <= tools.index.length:
            raise ToolError("invalid_risk_review", f"checks[{index}].step must be in 1..{tools.index.length}, got {row['step']!r}")
        evidence = normalize_evidence(tools, row["evidence"], visible)
        status = row["status"]
        missing = list(row["missing"])
        if status in {"pass", "risk"} and (missing or not any(e["step"] == row["step"] and e["fact_ids"] for e in evidence)):
            raise ToolError("invalid_check_certainty", f"checks[{index}] ({row['policy_id']!r}) pass/risk needs same-location original evidence and no blocking missing prerequisites. Put non-blocking effect/causality limits in limitations; submit unknown when the check itself cannot be decided.")
        if row["policy_id"] == "read_scope" or row["policy_id"].startswith("tool:"):
            try:
                call = json.loads(raw_source(tools.bundle.source, row["step"], "action") or "{}")
            except (ValueError, TypeError):
                call = {}
            binding = tools.risk_policy.get("tools", {}).get(call.get("tool"), {})
            applicable = (binding.get("kind") == "read" if row["policy_id"] == "read_scope" else
                          call.get("tool") == row["policy_id"].split(":", 1)[1])
            if status in {"pass", "risk"} and (not applicable or not any(e["step"] == row["step"] and e["module"] == "action" and e["fact_ids"] for e in evidence)):
                raise ToolError("invalid_check_location", f"checks[{index}] ({row['policy_id']!r}) must cite its actual applicable tool action, not a response/context/message. Choose a location listing this ID in tool_check_policy_ids, or retain unknown if the action was not retrieved.")
        checks.append({**row, "status": status, "missing": missing, "limitations": list(row.get("limitations", [])),
                       "evidence": evidence, "source": "risk_model", "investigator_id": cid})
    if not findings and not checks and not answer["uncertainty"].strip():
        raise ToolError("invalid_risk_review", "empty review requires its evidence limitation")
    result = {"schema": "risk-investigation/v1", "window_id": cid,
              "findings": findings, "checks": checks, "uncertainty": answer["uncertainty"],
              "response_turn": call_id, "query_digest": digest(tools.trace_queries[cid]),
              "retrieval_status": query_status(tools.trace_queries[cid]),
              "query_attempt_count": len(getattr(tools, "risk_query_attempts", {}).get(cid, [])) or 1,
              "retrieval_limitation": ("No successful complete retrieval; only the actual returned fragments and initial anchor/Manager evidence may support findings. Missing results do not establish safety."
                  if query_status(tools.trace_queries[cid]) != "succeeded" else "A successful query covers only the requested and returned evidence.")}
    tools.risk_reviews[cid] = result
    # Preserve generic manager/query coverage without injecting risk classes into
    # the original failure-root candidate representation.
    tools.window_conclusions[cid] = {"window_id": cid, "window": case, "candidates": [],
                                    "conclusion": {"uncertainty": answer["uncertainty"]}}
    return result


def findings(tools):
    result = {}
    for review in tools.risk_reviews.values():
        for row in review["findings"]:
            key = row["id"]
            if key not in result:
                result[key] = plain(row)
                result[key]["locations"] = [finding_location(row)]
                result[key]["investigator_explanations"] = [{"investigator_ids": row["investigator_ids"],
                    "explanation": row["explanation"], "status": row["status"],
                    "import_step": row["import_step"], "module": row["module"]}]
            else:
                target = result[key]
                target["investigator_ids"] = sorted(set(target["investigator_ids"] + row["investigator_ids"]))
                target["policy_ids"] = sorted(set(target["policy_ids"] + row["policy_ids"]))
                target["fact_ids"] = sorted(set(target["fact_ids"] + row["fact_ids"]))
                loc = finding_location(row)
                if loc not in target["locations"]:
                    target["locations"].append(loc)
                # Prefer a directly assessed call, without inventing an unseen
                # action location. Other result/message positions stay available.
                if row["module"] == "action" and target["module"] != "action":
                    target.update(loc)
                elif (row["import_step"], row["module"]) == (target["import_step"], target["module"]):
                    target["primary_fids"] = sorted(set(target["primary_fids"] + row["primary_fids"]))
                target["investigator_explanations"].append({"investigator_ids": row["investigator_ids"],
                    "explanation": row["explanation"], "status": row["status"],
                    "import_step": row["import_step"], "module": row["module"]})
                for field in ("evidence", "counterevidence", "cause_hypotheses"):
                    seen = {digest(e) for e in target[field]}
                    target[field].extend(e for e in row[field] if digest(e) not in seen)
                target["missing"] = sorted(set(target["missing"] + row["missing"]))
                target["limitations"] = sorted(set(target.get("limitations", []) + row.get("limitations", [])))
                if target["status"] != row["status"] or target["missing"]:
                    if target["status"] != row["status"]:
                        target["missing"] = sorted(set([*target["missing"], "多个调查对该风险的确定性判断不一致。 "]))
                    target["status"] = "insufficient_evidence"
    return list(result.values())


def checks(tools):
    """Count each assessed policy/action pair once, retaining disagreements."""
    result = {}
    for review in tools.risk_reviews.values():
        for row in review["checks"]:
            key = (row["policy_id"], row["step"])
            if key not in result:
                result[key] = {**plain(row), "investigator_ids": [row["investigator_id"]],
                    "investigator_reasons": [{"investigator_id": row["investigator_id"], "reason": row["reason"], "status": row["status"]}]}
                continue
            target = result[key]
            target["investigator_ids"] = sorted(set([*target["investigator_ids"], row["investigator_id"]]))
            target["investigator_reasons"].append({"investigator_id": row["investigator_id"], "reason": row["reason"], "status": row["status"]})
            known = {digest(e) for e in target["evidence"]}
            target["evidence"].extend(e for e in row["evidence"] if digest(e) not in known)
            target["missing"] = sorted(set(target["missing"] + row["missing"]))
            target["limitations"] = sorted(set(target.get("limitations", []) + row.get("limitations", [])))
            if target["status"] != row["status"]:
                target["status"] = "unknown"
                target["missing"] = sorted(set([*target["missing"], "多调查对同一策略/步骤的判断不一致。 "]))
    return list(result.values())


def coverage(tools):
    applicable = [row["t"] for row in tools.bundle.source["steps"]
                  if row["parts"].get("action") or message_event(tools.bundle.source, row["t"])]
    checked = checks(tools)
    assessed = sorted({loc["import_step"] for r in findings(tools) for loc in r["locations"]}
                      | {c["step"] for c in checked})
    complete = bool(tools.windows.windows) and all(c["candidate_id"] in tools.risk_reviews for c in tools.windows.windows)
    from .risk_scope import coverage_map
    obligation_coverage = coverage_map(tools.risk_map, tools.automatic, checked, findings(tools))
    retrieval = [{"candidate_id": c["candidate_id"],
                  "status": query_status(tools.trace_queries.get(c["candidate_id"])),
                  "attempts": len(getattr(tools, "risk_query_attempts", {}).get(c["candidate_id"], [])),
                  "returned_rows": len(tools.trace_queries[c["candidate_id"]].get("rows", []))
                       if c["candidate_id"] in tools.trace_queries and not tools.trace_queries[c["candidate_id"]].get("error") else None}
                 for c in tools.windows.windows]
    return {"recorded_steps": tools.index.length, "observable_action_message_steps": applicable,
        "assessed_steps": assessed, "unassessed_steps": sorted(set(applicable) - set(assessed)),
        "checked_pairs": [{"policy_id": c["policy_id"], "step": c["step"], "status": c["status"]} for c in checked],
        "policy_ids": policy_ids(tools.risk_policy), "trace_complete": complete,
        "workflow_complete": complete, "obligation_coverage": obligation_coverage,
        "retrieval_complete": bool(retrieval) and all(r["status"] == "succeeded" for r in retrieval),
        "retrieval": retrieval,
        "query_error_count": sum(bool(a.get("error")) for attempts in getattr(tools, "risk_query_attempts", {}).values() for a in attempts),
        "retrieval_scope": "Assessment completion does not imply successful retrieval; a failed or partial query is not a negative safety finding.",
        "completed_investigations": sorted(tools.risk_reviews),
        "pending_investigations": [c["candidate_id"] for c in tools.windows.windows if c["candidate_id"] not in tools.risk_reviews],
        "retrieved_steps": sorted({s for q in tools.trace_queries.values() for s in q.get("returned_steps", [])}),
        "limitation": "完成阶段不等于所有策略/步骤都被检查。通过只适用于明确列出的检查范围；缺失记录、隐藏思考和因果关系未被证明。"}


class RiskTools(DebugTools):
    def __init__(self, *args, risk_policy=None, automatic=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.risk_policy, self.automatic = risk_policy or {}, automatic or {}
        from .risk_scope import build_map
        self.risk_map = build_map(self.bundle, self.risk_policy)
        self.risk_reviews = {}
        self.risk_reference_catalogs = {}
        self.risk_reference_catalog_digests = set()
        self.risk_query_attempts = {}
        self.handlers["finish"] = self._require_full_review
        self.handlers["query_logs"] = self._query_with_source_refs

    def _view(self, spec, scope):
        result = super()._view(spec, scope)
        if spec.strip() == "requirements" and scope.get("mode") == "global":
            result["risk_map"] = plain(self.risk_map)
            self.goal_map = plain(result)
            if self.output:
                from .record_bridge import write_json
                write_json(self.output / "risk_map.json", self.risk_map)
        return result

    def _query_with_source_refs(self, candidate_id, sql):
        """Attach existing Fact IDs only to already returned source locations.

        SEARCH_TEXT deliberately returns fid=null. A reference catalog lets an
        investigator cite existing canonical facts without guessing IDs. It adds
        no source text, new facts or retrieval branches. Risk review permits at
        most two corrections only after SQL errors returned no rows. The original
        diagnostic query protocol remains unchanged.
        """
        from .query_trace import query
        from .risk_query_provenance import execute as execute_with_provenance
        attempts = self.risk_query_attempts.setdefault(candidate_id, [])
        if len(attempts) >= MAX_RISK_QUERY_ATTEMPTS:
            raise ToolError("query_retry_exhausted", "Risk query error-correction limit reached; assess the existing visible evidence and retain retrieval limitations.")
        result = query(self, candidate_id, sql, query_executor=execute_with_provenance)
        columns = [c.get("name") if isinstance(c, dict) else c for c in result.get("columns", [])]
        pairs = set()
        for row in result.get("rows", []):
            item = dict(zip(columns, row)) if isinstance(row, (list, tuple)) else row
            if not isinstance(item, dict):
                continue
            step, module = item.get("step", item.get("t")), item.get("module")
            fid = item.get("fid", item.get("fact_id"))
            if type(fid) is int and fid in self.index.facts:
                fact = self.index.fact(fid)
                if step is None:
                    step = fact.t
                if module is None:
                    module = fact_source_module(fact, self.bundle.source)
            if type(step) is int and module and 1 <= step <= self.index.length:
                pairs.add((step, module))
        result["source_fact_catalog"] = [{"source_id": f"source:{step}:{module}", "step": step, "module": module,
            "facts": [{"fid": f.fid, "rel": f.rel, "slot": f.slot, "side": f.side}
                      for f in self.index.facts.values()
                      if f.t == step and fact_source_module(f, self.bundle.source) == module]}
            for step, module in sorted(pairs)]
        result["source_fact_catalog_scope"] = "Existing canonical Fact identifiers for returned step/module pairs only; reference metadata, not additional source evidence or a risk verdict."
        from .risk_references import trace_catalog
        result["reference_catalog"] = trace_catalog(self, candidate_id)
        failed_empty = (not result.get("rows") and (result.get("error", {}).get("code") == "invalid_query"
                                                   or bool(result.get("function_errors"))))
        retry = bool(failed_empty and len(attempts) + 1 < MAX_RISK_QUERY_ATTEMPTS)
        result.update(retrieval_status=query_status(result), query_attempt=len(attempts) + 1,
            returned_row_count=None if result.get("error") else len(result.get("rows", [])),
            query_retry={"allowed": retry, "max_attempts": MAX_RISK_QUERY_ATTEMPTS,
                         "remaining_corrections": max(0, MAX_RISK_QUERY_ATTEMPTS - len(attempts) - 1),
                         "scope": "Only correct a failed SQL with no returned rows. A successful/partially returned query cannot be repeated or broadened."})
        attempts.append(plain(result))
        if retry:
            # Failed attempts remain in the normal tool journal and query JSONL.
            # No returned source fragments are promoted by an empty error.
            self.trace_queries.pop(candidate_id, None)
        return result

    def _trace_window(self, candidates):
        """Preserve the original gate, but identify the offending location.

        Task facts live at t=0; support facts from another step cannot become
        anchor facts. A useful error avoids blind provider correction loops.
        """
        if isinstance(candidates, list):
            for index, item in enumerate(candidates):
                if not isinstance(item, dict):
                    continue
                step = item.get("issue_step")
                refs = item.get("issue_fids", [])
                if not isinstance(refs, list):
                    continue
                for fid in refs:
                    fact = self.index.fact(fid)
                    if fact.t != step:
                        raise ToolError("invalid_risk_anchor",
                            f"candidates[{index}] {item.get('candidate_id')}: Fact {fid} belongs to step {fact.t}, "
                            f"not issue_step {step}. Copy issue_fids ONLY from the chosen valid_anchors row. "
                            "Task facts at step 0 are requirement context, never a positive-step anchor. "
                            "Other-step support belongs in the question/query, not issue_fids.")
        return super()._trace_window(candidates)

    def _require_full_review(self, **_):
        raise ToolError("protocol_incomplete", "Risk review requires MAP/ROUTE/Manager/TRACE/SUBMIT; route a policy applicability question even when no automatic flag exists")

    def stop_result(self, status, reason):
        return {"schema": "risk-review-result/v1", "mode": "risk_review", "status": status,
                "findings": findings(self), "checks": checks(self),
                "coverage": coverage(self), "summary": reason, "uncertainty": reason,
                "automatic": self.automatic, "risk_map": self.risk_map, "stop_reason": reason}

    def execute(self, name, arguments, call_id, *, record=True, argument_error=None):
        if name not in {"assess_risks", "plan_trace"}:
            return super().execute(name, arguments, call_id, record=record, argument_error=argument_error)
        request_hash = digest({"name": name, "arguments": arguments, "argument_error": argument_error})
        if call_id in self.completed:
            if self.completed[call_id]["request_hash"] != request_hash:
                raise ToolError("invalid_id", "tool call ID reused with different arguments")
            return self.completed[call_id]["result"]
        if self.call_count >= self.max_tool_calls:
            raise BudgetExceeded("tool call limit reached")
        self.call_count += 1
        event = {"ordinal": self.call_count, "tool_call_id": call_id, "name": name,
                 "arguments": plain(arguments), "request_hash": request_hash, "phase": self.phase}
        if record and self.output:
            append_json(self.output / "tool_calls.jsonl", {**event, "event": "pending"})
        result = {"schema": "risk-tool/v1", "trajectory_id": self.bundle.record.tid}
        resolution = {"schema": "risk-reference-resolution/v1", "tool": name, "tool_call_id": call_id,
                      "raw_arguments": plain(arguments), "status": "rejected"}
        try:
            if argument_error:
                raise ToolError(argument_error["code"], argument_error["message"])
            if self.phase != "TRACE" or self.result is not None:
                raise ToolError("protocol_incomplete", "Risk planning/assessment is only available during active TRACE")
            from .risk_references import resolve_manager, resolve_assessment, annotate_resolution
            owner = "MANAGER" if name == "plan_trace" else getattr(self, "current_trace_case", None)
            resolved = resolve_manager(self, arguments) if name == "plan_trace" else resolve_assessment(self, arguments)
            resolution.update(owner=owner, catalog_digest=digest(self.risk_reference_catalogs[owner]),
                              resolved_arguments=plain(resolved))
            event["resolved_arguments"] = plain(resolved)
            if name == "plan_trace":
                from .trace_manager import commit as commit_plan
                result["data"] = commit_plan(self, **resolved)
            else:
                result["data"] = annotate_resolution(self, commit(self, resolved, call_id), owner)
            resolution["status"] = "accepted"
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            result["error"] = {"code": getattr(exc, "code", "invalid_arguments"), "message": str(exc), "retryable": False}
            resolution["error"] = result["error"]
        event.update(event="committed", result=result, result_digest=digest(result))
        self.events.append(event)
        self.completed[call_id] = event
        if record and self.output:
            append_json(self.output / "tool_calls.jsonl", event)
            append_json(self.output / "risk_reference_resolutions.jsonl", resolution)
            if "error" in result:
                append_json(self.output / "risk_rejections.jsonl", {**resolution,
                    "schema": "risk-review-rejection/v1", "phase": self.phase,
                    "meaning": "Rejected proposal; not an accepted risk finding or a successful diagnostic stage."})
            if "data" in result and name == "assess_risks":
                append_json(self.output / "risk_trace.jsonl", result["data"])
            self.save()
        return result
