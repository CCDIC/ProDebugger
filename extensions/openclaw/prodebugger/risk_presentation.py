"""Map independent risk findings to precise existing nodes, without causal edges."""
from .record_bridge import digest
from types import SimpleNamespace
from .session_sources import source_text, fact_source_module


def _model_evidence_valid(evidence, facts, source):
    if not isinstance(evidence, dict):
        return False
    step, module = evidence.get("step"), evidence.get("module")
    quote = evidence.get("quote", evidence.get("text"))
    if step == 0 and module == "task":
        return isinstance(quote, str) and bool(quote) and quote in source.get("task", "") and evidence.get("fact_ids") == []
    if type(step) is not int or not 1 <= step <= len(source["steps"]) or not isinstance(module, str):
        return False
    try:
        original = source_text(source, step, module)
    except (IndexError, KeyError, TypeError):
        return False
    refs = evidence.get("fact_ids", [evidence.get("fid")])
    # Supporting SOURCE quotations may have no Fact ID. The primary finding
    # still needs a Fact at its own step/module, checked below.
    valid = (isinstance(original, str) and isinstance(quote, str) and bool(quote)
            and quote in original and isinstance(refs, list)
            and all(type(fid) is int and fid in facts and facts[fid]["t"] == step
                    and fact_source_module(SimpleNamespace(**facts[fid]), source) == module for fid in refs))
    return valid


def _model_check_evidence_valid(evidence, facts, source):
    """Check offsets when present, without changing legacy finding validation."""
    if not _model_evidence_valid(evidence, facts, source):
        return False
    if "start" not in evidence and "end" not in evidence:
        return True
    original = source.get("task", "") if evidence.get("step") == 0 else source_text(
        source, evidence["step"], evidence["module"])
    lo, hi = evidence.get("start"), evidence.get("end")
    quote = evidence.get("quote", evidence.get("text"))
    return (type(lo) is int and type(hi) is int and 0 <= lo < hi <= len(original)
            and original[lo:hi] == quote)


def _merge_model_checks(source, facts, calls, analysis, job, policy):
    """Validate saved model checks independently before adding view summaries.

    This does not replay the model or rewrite its result. Rejected checks remain
    in risk_review and the audit. A validated quotation is not semantic truth.
    """
    from .risk_trace import policy_ids
    from .assessment_presentation import policy_fields_unavailable
    known = set(policy_ids(policy or {}))
    expected_version = analysis.get("analysis_version")
    job_version = job.get("analysis_version") or (job.get("compilation") or {}).get("canonical_fingerprint")
    for ordinal, row in enumerate((job.get("result") or {}).get("checks", [])):
        reasons, cids = [], []
        if not isinstance(row, dict):
            analysis["model_check_audit"].append({"ordinal": ordinal, "accepted": False, "reasons": ["invalid_check_object"]})
            continue
        status, step = row.get("status"), row.get("step")
        if not expected_version or job_version != expected_version:
            reasons.append("matching_analysis_version_required")
        if status not in {"pass", "risk", "unknown", "not_applicable"}:
            reasons.append("unsupported_model_check_state")
        if row.get("policy_id") not in known:
            reasons.append("configured_policy_id_required")
        if type(step) is not int or not 1 <= step <= len(source["steps"]):
            reasons.append("recorded_step_required")
        evidence = row.get("evidence", [])
        evidence_valid = isinstance(evidence, list) and bool(evidence) and all(
            _model_check_evidence_valid(e, facts, source) for e in evidence)
        if evidence and not evidence_valid:
            reasons.append("exact_source_and_fact_references_required")
        primary = [f for f in facts.values() if f["t"] == step and f["rel"] in {"tool_call", "tool_result"}]
        for fact in primary:
            cids.extend(a["index"] for a in fact.get("args", []) if a.get("kind") == "call" and a.get("index") in calls)
        cids = list(dict.fromkeys(cids))
        if len(cids) > 1:
            reasons.append("unique_call_identity_required")
            cids = []
        if row.get("call_id") and row["call_id"] not in cids:
            reasons.append("matching_call_identity_required")
        if row.get("span_id") and (len(cids) != 1 or row["span_id"] != calls[cids[0]]["span_id"]):
            reasons.append("matching_span_identity_required")
        own_fids = {f["fid"] for f in primary}
        if not own_fids:
            own_fids = {f["fid"] for f in facts.values() if f["t"] == step and fact_source_module(SimpleNamespace(**f), source) == "message"}
        anchored = evidence_valid and any(e.get("step") == step and own_fids.intersection(e.get("fact_ids", [])) for e in evidence)
        if status in {"pass", "risk"} and (row.get("missing") or not anchored):
            reasons.append("same_location_evidence_without_blocking_missing_required")
        pid = row.get("policy_id", "")
        if pid == "read_scope" or pid.startswith("tool:"):
            call = calls[cids[0]] if len(cids) == 1 else None
            binding = (policy or {}).get("tools", {}).get(call["tool"], {}) if call else {}
            applicable = bool(call and call["import_step"] == step and (
                binding.get("kind") == "read" and isinstance((policy or {}).get("read_scope"), dict)
                if pid == "read_scope" else call["tool"] == pid.split(":", 1)[1] and binding))
            if status in {"pass", "risk"} and not applicable:
                reasons.append("applicable_tool_action_required")
        accepted = not reasons
        audit = {"ordinal": ordinal, "policy_id": pid, "step": step, "status": status,
            "accepted": accepted, "evidence_validated": evidence_valid, "reasons": reasons,
            "job_id": job.get("id"), "analysis_version": job_version}
        analysis["model_check_audit"].append(audit)
        normalized = {**row, "id": "model-check-" + digest([job.get("id"), ordinal, row])[:20],
            "source": "risk_model", "job_id": job.get("id"), "analysis_version": job_version,
            "source_sha256": job.get("source_sha256"), "policy_sha256": job.get("policy_sha256"),
            "state": {"pass": "satisfied", "risk": "violated"}.get(status, status or "unknown"),
            "original_status": status, "call_ids": cids if accepted else [], "import_steps": [step] if type(step) is int else [],
            "applied": accepted and anchored and status in {"pass", "risk"},
            "evidence_validated": evidence_valid and accepted, "presentation_validation": audit,
            "limitations": [*row.get("limitations", []), "模型检查仅在所引用的原文与配置范围内成立；不是整体安全或唯一因果证明。"]}
        if not accepted:
            normalized.update(display_status="unvalidated", applied=False, state="unknown")
        elif status in {"pass", "risk"} and policy_fields_unavailable(source, policy, cids, pid):
            # Preserve original_status. A missing configured field cannot become
            # a green applicability claim merely because the model wrote pass.
            normalized.update(display_status="policy_field_unavailable", applied=False)
        analysis["checks"].append(normalized)


def merge_risk_review(source, facts, calls, analysis, issue, review):
    from .trace_analysis import verified_evidence
    analysis["risk_review"] = review
    analysis["model_check_audit"] = []
    policy_digest = digest(review.get("policy") or {})
    automatic, job = review.get("automatic") or {}, review.get("model") or {}
    analysis["privacy"] = (job.get("privacy") or (job.get("result") or {}).get("privacy") or automatic.get("privacy"))
    if automatic and automatic.get("status") != "not_configured":
        valid_report = (automatic.get("source_sha256") == analysis["source_sha256"]
                        and automatic.get("policy_sha256") == policy_digest)
        if not valid_report:
            issue("uncertainty", "风险规则报告与输入或策略不匹配", "保留报告，未应用过期风险标记。",
                  state="unknown", origin="risk_integrity", missing=["matching_source_and_risk_policy"])
        else:
            queries = {q["id"]: q for q in automatic.get("queries", []) if "id" in q}
            for finding in automatic.get("findings", []):
                evidence = finding.get("evidence", [])
                valid = bool(evidence) and all(verified_evidence(e, facts, source, queries)
                    for e in evidence if e.get("fid") is not None)
                # Policy-only snippets cannot establish an observed action.
                valid = valid and any(type(e.get("fid")) is int for e in evidence)
                _append(finding, "risk_rule", valid, source, facts, calls, analysis, issue)
            for check in automatic.get("checks", []):
                state = check.get("status", check.get("state"))
                cid = check.get("call_id")
                analysis["checks"].append({**check, "source": "risk_rule",
                    "call_ids": [cid] if cid in calls else [],
                    "state": {"passed": "satisfied", "confirmed": "violated"}.get(state, state),
                    "applied": state in {"passed", "confirmed"}})
    if not job:
        return
    if (job.get("source_sha256") != analysis["source_sha256"]
            or job.get("policy_sha256", job.get("risk_policy_sha256")) != policy_digest):
        issue("uncertainty", "风险模型结果与当前输入或策略不匹配", "请按当前输入重新审查；未应用旧标记。",
              state="unknown", origin="risk_integrity", missing=["matching_risk_job"])
        return
    result = job.get("result") or {}
    _merge_model_checks(source, facts, calls, analysis, job, review.get("policy") or {})
    for finding in result.get("findings", []):
        evidence = finding.get("evidence", [])
        valid = bool(evidence) and all(_model_evidence_valid(e, facts, source) for e in evidence)
        valid = valid and any(e.get("step") == finding.get("import_step")
            and e.get("module") == finding.get("module") and e.get("fact_ids") for e in evidence)
        _append(finding, "risk_model", valid, source, facts, calls, analysis, issue)
    if job.get("status") in {"failed", "cancelled", "timed_out", "interrupted"}:
        issue("uncertainty", "完整风险审查未完成", job.get("error") or "自动规则结果仍保留。",
              state="unknown", origin="risk_model", key="risk-job-status",
              missing=[job.get("status")])


def _append(finding, origin, valid, source, facts, calls, analysis, issue):
    status = finding.get("status", "unknown")
    call = calls.get(finding.get("call_id"))
    step = finding.get("import_step", finding.get("step"))
    if call:
        # Evidence elsewhere can support this finding, but only this call is marked.
        primary = [call["fid"]]
        step = call["import_step"]
        if finding.get("span_id") not in (None, call["span_id"]):
            valid = False
    else:
        primary = [fid for fid in finding.get("primary_fids", []) if fid in facts]
        if finding.get("call_id"):
            valid = False
    refs = list(dict.fromkeys([*primary, *finding.get("fact_ids", [])]))
    if any(type(fid) is not int or fid not in facts for fid in refs):
        valid = False
    confirmed = status == "confirmed" and valid
    warning = status == "input_warning" and valid
    category = "safety" if confirmed or warning else "uncertainty"
    state = "violation_attempt" if confirmed else ("input_warning" if warning else "unknown")
    missing = list(finding.get("missing", []))
    if not valid:
        missing.append("exact_fact_and_source_evidence_not_verified")
    value = issue(category, finding.get("title") or ("模型风险判断" if confirmed else "风险调查线索"),
        finding.get("explanation") or "请核对策略、行为原文和证据限制。",
        key=origin + "-" + str(finding.get("id") or digest(finding)[:12]), state=state,
        fids=refs, primary_fids=primary, step=step, evidence=finding.get("evidence", []),
        missing=missing, origin=origin,
        certainty=("model_judgement_with_source" if origin == "risk_model" else "evidence_supported") if confirmed else "uncertain",
        rule_id=finding.get("rule_id"), risk_type=finding.get("risk_type"),
        policy_ids=finding.get("policy_ids", []), effect=finding.get("effect", "unknown"),
        counterevidence=finding.get("counterevidence", []),
        cause_hypotheses=finding.get("cause_hypotheses", []),
        risk_finding_id=finding.get("id"), risk_status=status,
        privacy_category=finding.get("privacy_category"), privacy_rule_id=finding.get("privacy_rule_id"),
        privacy_field_ids=finding.get("privacy_field_ids", []), privacy_flow_ids=finding.get("privacy_flow_ids", []),
        evidence_validated=bool(valid),
        limitations=[*finding.get("limitations", []), "风险结论与任务失败根因分别判断；有风险不要求工具报错。",
                     "证据链表示可观察的来源与行为联系，不等于已证明隐藏思考或唯一因果。"])
    if not call and finding.get("module") == "message" and source.get("session"):
        event = next((e for e in source["session"]["events"] if e["step"] == step and e["type"] == "session_message"), None)
        if event:
            value.update(call_id=None, call_ids=[], span_id=None,
                node_ids=[analysis["case_id"] + "::message:" + str(event.get("trace_id")) + "/" + event["event_id"]],
                affected_event_ids=[event["event_id"]], import_steps=[step], original_locations=[event.get("source", {})])
