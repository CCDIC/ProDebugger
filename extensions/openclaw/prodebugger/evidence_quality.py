"""Extraction limits: missing knowledge never establishes an agent fault."""
from .record_bridge import plain

RAW_MODULE_RELATIONS = {"recalled_summary", "recalled_nothing", "memory_unparsed", "assessed", "planned"}


def fact_quality(record, fact):
    if fact.side == "task":
        kind, status = "task_contract", "not_world_evidence"
    elif fact.side == "mind" and fact.rel in {"session_message_text", "session_public_statement", "session_final_answer", "session_explicit_plan", "message_content", "message_role"}:
        kind, status = "recorded_public_statement", "verbatim_or_explicit_extraction_not_verified_truth"
    elif fact.slot == "obs" and fact.side == "env":
        audit = next((a for a in getattr(record, "parse_audits", [])
                      if a["kind"] == "observation" and a["step"] == fact.t), {})
        kind, status = "observation_extraction", audit.get("status", "not_audited")
    elif fact.slot == "chk":
        kind, status = "derived_check", "inference_not_observation"
    elif fact.rel in {"memory_unparsed", "recalled_nothing"}:
        kind, status = "parser_metadata", "no_typed_memory_claim_not_empty_memory"
    elif fact.rel in RAW_MODULE_RELATIONS:
        kind, status = "agent_text", "verbatim_not_verified_truth"
    else:
        kind, status = "agent_claim_extraction", "selective_heuristic_not_audited"
    scope = getattr(record, 'claim_scopes', {}).get(fact.fid, {})
    return {"source_kind": kind, "parse_status": status,
            'assertion_time': scope.get('event_step'), 'claim_scope': scope.get('temporal'),
            'claim_polarity': scope.get('polarity')}


def evidence_quality(record, through):
    gaps = [{"source_id": a["source_id"], "step": a["step"], "kind": a["kind"],
             "status": a["status"], "reasons": [str(r)[:180] for r in a.get("reasons", [])[:4]],
             "unresolved": [{"quote": g["quote"][:180], "reason": g["reason"][:180]}
                            for g in a.get("unresolved", [])[:3]]}
            for a in getattr(record, "parse_audits", []) if a["step"] <= through and a["status"] == "unresolved"]
    return {"through_step": through, "absence_means": "unknown_not_false", "parse_gaps": gaps,
            "unmodeled_goals": [str(g.entity()) for g in record.goals if g.status_mode != "tracked"],
            "observation_coverage": "Template/backoff admission is extraction coverage, not complete world observability or semantic truth.",
            "agent_module_coverage": "Memory/plan/action extraction is selective and heuristic. A missing typed claim or unmentioned constraint is not an omission by the agent.",
            "derived_checks": "env/chk facts are computed from attempted actions and parsed state, not independent environment feedback.",
            "false_positive_check": "Compare original source with the extracted proposition; test ambiguity, history, negation and legitimate exploration/retry. Missing facts, unmet checks or limited retrieval alone cannot support error."}


def annotated_fact(record, fact):
    return {**plain(fact), **fact_quality(record, fact)}
