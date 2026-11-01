"""Read-only check coverage for views; never replaces an engine verdict.

Call and return events share one summary. Counts describe unique saved checks,
not display cards, findings, or an estimate of whole-task safety.
"""
from collections import Counter
import json

from .record_bridge import digest

LABELS = {
    "passed": ("该项检查通过", "Check passed"),
    "risk": ("检查发现问题", "Issue found"),
    "missing_evidence": ("检查证据不足", "Insufficient check evidence"),
    "policy_field_unavailable": ("策略指定字段不可用", "Configured field unavailable"),
    "unsupported": ("工具检查尚未支持", "Tool check unsupported"),
    "unconfigured": ("尚未配置检查", "Check not configured"),
    "prerequisite_unavailable": ("规则前提未提供", "Rule prerequisites unavailable"),
    "not_applicable": ("本项不适用", "Check not applicable"),
    "not_evaluated": ("已记录，未单独评估", "Recorded, not individually assessed"),
    "unvalidated": ("模型检查未通过引用校验", "Model check not validated"),
    "conflict": ("检查结论存在分歧", "Check conclusions disagree"),
}
OVERALL = {
    "risk": ("存在问题，查看来源", "Issue found; inspect its source"),
    "mixed": ("部分检查通过，仍有检查限制", "Some checks passed; limitations remain"),
    "passed": ("已执行的适用检查通过", "Executed applicable checks passed"),
    "unsupported": LABELS["unsupported"],
    "not_evaluated": LABELS["not_evaluated"],
    "unknown": ("尚不能完成相关检查", "Related checks remain unresolved"),
}
PREREQUISITES = {
    "missing_trigger_fact", "missing_completion_assertion", "missing_expected_effect_model",
    "unsupported_contract_or_no_tag", "missing_previous_action",
    "requires_one_explicit_read_contract_and_one_call", "requires_explicit_contract",
}
UNRESOLVED = {"missing_evidence", "policy_field_unavailable", "unsupported", "unconfigured", "unvalidated", "conflict"}


def _json(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def policy_fields_unavailable(source, policy, call_ids, policy_id=None, refs=()):
    """Missing configured members, not missing whole responses or inferred PII."""
    from .risk_evidence import pointer
    from .risk_review import _match
    selected = []
    for i, spec in enumerate((policy or {}).get("sensitive_sources", [])):
        if policy_id not in (None, "sensitive_sources", spec.get("id")) and f"/sensitive_sources/{i}" not in refs:
            continue
        for step in source.get("steps", []):
            action = _json(step.get("parts", {}).get("action"))
            ident = str(action.get("trace_id")) + "/" + str(action.get("span_id"))
            if ident not in call_ids or action.get("tool") != spec.get("tool") or not _match(action.get("arguments"), spec.get("argument_equals", {})):
                continue
            outputs = [_json(s.get("env_text")) for s in source.get("steps", [])]
            outputs = [o for o in outputs if o.get("type") == "tool_result"
                       and all(o.get(k) == action.get(k) for k in ("trace_id", "span_id", "tool"))]
            if len(outputs) != 1 or outputs[0].get("result") is None:
                continue
            try:
                pointer(outputs[0]["result"], spec["result_pointer"])
            except (KeyError, IndexError, TypeError, ValueError):
                selected.append({"policy_id": spec["id"], "result_pointer": spec["result_pointer"],
                                 "call_id": ident, "response_recorded": True})
    return selected


def classify(row, source, policy):
    """Add display meaning while retaining state/status/reason from the engine."""
    if row.get("display_status") in LABELS:
        return row["display_status"]
    state = row.get("state", row.get("status", "unknown"))
    reason = row.get("reason", "")
    # Historic capture reports also keep structured gap objects. Classify only
    # their string codes; retain the original objects for provenance/details.
    missing_values = []
    for key in ("missing", "gaps"):
        value = row.get(key)
        missing_values.extend(value if isinstance(value, (list, tuple)) else [value] if value else [])
    missing = {value for value in missing_values if isinstance(value, str)}
    if state in {"violated", "unsafe", "confirmed", "risk"}:
        return "risk"
    if state in {"safe", "satisfied", "passed", "pass"} and row.get("applied"):
        return "passed"
    if state == "not_applicable" or reason == "non_equivalent_action":
        return "not_applicable"
    if reason in PREREQUISITES:
        return "prerequisite_unavailable"
    if {"unsupported_tool", "supported_tool_binding", "unsupported_target_semantics"} & missing:
        return "unsupported" if (policy or {}).get("tools") else "unconfigured"
    if "complete_distinct_sensitive_value" in missing and policy_fields_unavailable(
            source, policy, row.get("call_ids", []), row.get("policy_id"), row.get("policy_refs", [])):
        return "policy_field_unavailable"
    if reason in {"not_configured", "unconfigured"}:
        return "unconfigured"
    if state == "unknown" or missing_values:
        return "missing_evidence"
    return "not_evaluated"


def _dimension(row, policy):
    if row.get("policy_id"):
        return row["policy_id"]
    for ref in row.get("policy_refs", []):
        if ref.startswith("/sensitive_sources/"):
            try:
                return policy["sensitive_sources"][int(ref.split("/")[2])]["id"]
            except (KeyError, IndexError, ValueError, TypeError):
                pass
    return row.get("risk_type") or row.get("rule_id") or "unidentified_check"


def add_check_summaries(analysis, source, facts, calls, policy=None):
    policy = policy or {}
    items = []
    for raw in analysis["checks"]:
        row = dict(raw)
        row["display_status"] = classify(row, source, policy)
        row["label"], row["label_en"] = LABELS[row["display_status"]]
        row["state"] = raw.get("state", raw.get("status", "unknown"))
        row["policy_id"] = _dimension(row, policy)
        row["id"] = raw.get("id") or "display-check-" + digest(raw)[:20]
        row["import_steps"] = sorted(set(row.get("import_steps", [])) | {
            calls[c]["import_step"] for c in row.get("call_ids", []) if c in calls})
        if type(row.get("step")) is int:
            row["import_steps"] = sorted(set(row["import_steps"]) | {row["step"]})
        row["policy_fields"] = policy_fields_unavailable(source, policy, row.get("call_ids", []),
            raw.get("policy_id"), row.get("policy_refs", [])) if row["display_status"] == "policy_field_unavailable" else []
        if row["policy_fields"]:
            row["display_explanation"] = "返回原文已记录，但策略指定的字段不存在；该字段检查未完成，不表示整段工具返回缺失。"
            row["display_explanation_en"] = "The response is recorded, but a configured member is absent. This check is incomplete; the whole response is not missing."
        row["analysis_version"] = raw.get("analysis_version", analysis.get("analysis_version"))
        row["current_analysis_version"] = analysis.get("analysis_version")
        items.append(row)
    # Retain complete raw checks too. Added fields describe presentation only.
    analysis["check_items"] = list({row["id"]: row for row in items}.values())
    for finding in analysis["issues"]:
        if finding["category"] != "uncertainty":
            continue
        proxy = {**finding, "state": "unknown", "source": finding["source"]}
        status = classify(proxy, source, policy)
        finding["display_status"] = status
        finding["display_label"], finding["display_label_en"] = LABELS[status]
        if status == "policy_field_unavailable":
            finding["display_explanation"] = "工具返回已记录；策略指定字段不可用，该字段检查尚未完成。"

    def summary(key, call_id, steps, rows):
        rows = list({r["id"]: r for r in rows}.values())
        counts = {name: 0 for name in LABELS}
        counts.update(Counter(row["display_status"] for row in rows))
        dimensions = {}
        for row in rows:
            dimensions.setdefault(row["policy_id"], []).append(row)
        disagreements = [{"policy_id": dim, "item_ids": [r["id"] for r in rs]}
            for dim, rs in dimensions.items()
            if {r["display_status"] for r in rs} >= {"passed", "risk"}]
        counts["conflict"] = len(disagreements)
        issues = [i for i in analysis["issues"] if call_id in i.get("call_ids", [])] if call_id else [
            i for i in analysis["issues"] if not i.get("call_ids") and set(steps) & set(i.get("import_steps", []))]
        has_risk = bool(counts["risk"] or any(i["category"] in {"rule", "model", "safety"}
            and i["state"] in {"confirmed", "violation_attempt", "input_warning"} for i in issues))
        unresolved = any(counts[n] for n in UNRESOLVED)
        passed = bool(counts["passed"])
        overall = ("risk" if has_risk else "mixed" if passed and unresolved else "passed" if passed else
                   "unsupported" if counts["unsupported"] and not any(counts[n] for n in UNRESOLVED - {"unsupported"}) else
                   "unknown" if unresolved else "not_evaluated")
        label, label_en = OVERALL[overall]
        return {"summary_id": key, "call_id": call_id, "shared_steps": sorted(steps),
            "analysis_version": analysis.get("analysis_version"), "items": rows, "counts": counts,
            "has_passed": passed, "has_unresolved": unresolved, "has_conflict": bool(disagreements),
            "has_limitations": unresolved or bool(counts["prerequisite_unavailable"]),
            "disagreements": disagreements, "finding_ids": [i["id"] for i in issues if i["category"] != "uncertainty"],
            "overall": overall, "label": label, "label_en": label_en,
            "scope_note": "仅表示列出的已执行检查；通过不能消除其他检查的未知或证明全局安全。",
            "scope_note_en": "Limited to the listed checks. A pass does not erase another check's uncertainty or certify whole-task safety."}

    call_summaries, event_summaries = {}, {}
    for cid, call in calls.items():
        result_steps = {f["t"] for f in facts.values() if f["rel"] == "tool_result" and any(
            a.get("kind") == "call" and a.get("index") == cid for a in f.get("args", []))}
        steps = {call["import_step"], *result_steps}
        value = summary("call:" + cid, cid, steps, [r for r in analysis["check_items"] if cid in r.get("call_ids", [])])
        call_summaries[cid] = value
        for step in steps:
            event_summaries[str(step)] = value
    for event in source.get("session", {}).get("events", []):
        step = event["step"]
        if str(step) not in event_summaries:
            rows = [r for r in analysis["check_items"] if not r.get("call_ids") and step in r["import_steps"]]
            event_summaries[str(step)] = summary("event:" + str(step), None, {step}, rows)
    analysis["check_summaries"] = call_summaries
    analysis["event_assessments"] = event_summaries
    analysis["assessment_summary"] = {"counts": dict(Counter(r["display_status"] for r in analysis["check_items"])),
        "unique_check_count": len(analysis["check_items"]), "assessed_call_count": sum(bool(s["items"]) for s in call_summaries.values()),
        "affected_call_count": sum(s["overall"] == "risk" for s in call_summaries.values()),
        "counting": "按唯一检查记录计数；调用卡和返回卡共享摘要，不重复累计。",
        "counting_en": "Counts unique check records. Call and return cards share a summary and are not counted twice."}
