"""Evidence-preserving presentation of existing rules, safety and diagnostics.

This is an analysis adapter, not a replacement rule/diagnosis engine. Original
evaluations are retained unchanged. Only OC-TASK-001 is an OpenClaw extension.
An issue belongs to a call, never to every invocation of its shared tool entity.
"""
from __future__ import annotations

from collections import Counter
import json
import ntpath
import posixpath

from .abstract_rules import export_rules
from .openclaw_contract import evaluate as task_outcome
from .record_bridge import build_bundle, digest, plain

SCHEMA = "prodebugger-trace-analysis/v1"
CATEGORIES = ("rule", "safety", "runtime", "model", "uncertainty")
LABELS = {"rule": "规则问题", "safety": "安全风险", "runtime": "工具运行异常",
          "model": "模型诊断", "uncertainty": "证据不足"}
RULE_NOTES = {
    "assertion.consistency/v1": "需要真实记录的结构化记忆断言及可比较观察；未采集记忆不算错误。",
    "assertion.grounding/v1": "需要结构化断言及对应观察；缺少记忆或来源不能反推编造。",
    "information.update/v1": "需要旧断言、更新观察及时间语义；OpenClaw 工具遥测通常不具备。",
    "decision.applicability/v1": "需要独立行动契约。OpenClaw profile 的 unsupported 保留未知，不判违反。",
    "execution.result/v1": "需要调用关联的反馈及预期效果模型；tool_error 不等于 effect=none 或决策错误。",
    "completion.consistency/v1": "需要明确完成断言及已建模目标；工具成功不等于任务完成。",
    "progress.efficiency/v1": "需要可比较的重复动作、结果、上下文及重试例外；缺少这些前提保留未知。",
}
REASONS = {
    "unsupported_contract_or_no_tag": "没有可用的独立行动契约，原规则无法判断这次决定是否允许。",
    "missing_expected_effect_model": "记录了工具返回，但没有该动作的完整预期效果模型。",
    "missing_trigger_fact": "没有记录本规则需要的断言；不表示 Agent 缺少该能力或发生错误。",
    "missing_completion_assertion": "没有明确的完成断言，本规则没有可检查对象。",
    "parse_incomplete": "相关原文解析尚不完整，违反候选已降级为无法判断。",
    "explicit_contract_mismatch": "行动与已有行动契约不一致。",
    "explicit_no_effect_not_responsibility_judgment": "已记录的反馈明确表示无效果；这仍不证明 Agent 应承担责任。",
    "missing_feedback": "缺少与本次调用关联的返回。",
}


def verified_evidence(item, facts, source, queries):
    """Verify an actual saved SOURCE row, its exact offsets and current Fact."""
    if not isinstance(item, dict) or type(item.get("fid")) is not int:
        return False
    fact = facts.get(item["fid"])
    step = item.get("step")
    if fact is None or fact["t"] != step or type(step) is not int or not 1 <= step <= len(source["steps"]):
        return False
    module = item.get("module")
    if module not in {"action", "observation", "message"} or fact["slot"] != ("act" if module == "action" else "obs"):
        return False
    original = source["steps"][step - 1]
    text = original["parts"].get(module) if module == "action" else original["env_text"]
    if module == "message":
        if not source.get("session"):
            return False
        from .session_sources import source_text
        text = source_text(source, step, "message")
    start, end = item.get("start"), item.get("end")
    if (not isinstance(text, str) or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(text) or text[start:end] != item.get("text")):
        return False
    result = queries.get(item.get("query_id"), {}).get("result", {})
    if any(result.get(k) for k in ("partial", "truncated", "error", "function_errors", "skipped_branches")):
        return False
    keys = ("source_id", "fid", "step", "module", "start", "end", "text")
    return any(all(row.get(key) == item.get(key) for key in keys) for row in result.get("rows", []))


def _loads(value):
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _fact_source(fact, source):
    """Exact canonical source link. Clearly distinguished from a SOURCE query."""
    t = fact.get("t")
    module = {"act": "action", "obs": "observation", "mem": "memory", "plan": "plan", "refl": "reflection"}.get(fact.get("slot"))
    if source.get("session") and fact.get("rel") in {"session_final_answer", "session_public_statement", "session_message_text", "session_explicit_plan", "session_task_text"}:
        event_index = next((i for i, e in enumerate(source["session"].get("events", [])) if e["step"] == t and e["type"] == "session_message"), None)
        if event_index is not None:
            event = source["session"]["events"][event_index]
            text, quote = event.get("text", ""), fact.get("span", "")
            if quote and quote in text:
                start = text.index(quote)
                return {"source": "canonical_session_message", "fid": fact["fid"], "step": t,
                        "module": "message", "start": start, "end": start + len(quote), "text": quote,
                        "source_ref": f"session/events/{event_index}/text", "native_source": event.get("source", {}),
                        "note": "已记录的原生消息与事实精确对应；公开表达不代表内部记忆。"}
    if type(t) is not int or not 1 <= t <= len(source["steps"]) or module is None:
        return None
    step = source["steps"][t - 1]
    text = step["env_text"] if module == "observation" else step["parts"].get(module)
    quote = fact.get("span")
    if not isinstance(text, str) or not isinstance(quote, str) or not quote or quote not in text:
        return None
    start = text.index(quote)
    return {"source": "canonical_fact_source", "fid": fact["fid"], "step": t, "module": module,
            "start": start, "end": start + len(quote), "text": quote,
            "source_ref": f"steps/{t-1}/" + ("env_text" if module == "observation" else "parts/" + module),
            "note": "原编译事实与原文精确片段；不是另造的 SOURCE 查询结果。"}


def _call_index(record, source, case_id):
    calls = {}
    for fact in record["facts"]:
        if fact["rel"] != "tool_call":
            continue
        payload = _loads(fact["value"])
        entity = next((arg for arg in fact["args"] if arg["kind"] == "call"), None)
        if entity is None:
            continue
        ident = entity["index"]
        mapping = [item for item in source.get("telemetry", {}).get("source_map", [])
                   if item.get("trace_id") == payload.get("trace_id") and item.get("span_id") == payload.get("span_id")]
        calls[ident] = {"call_id": ident, "node_id": case_id + "::call:" + ident,
                       "trace_id": payload.get("trace_id"), "span_id": payload.get("span_id"),
                       "tool": payload.get("tool"), "import_step": fact["t"], "fid": fact["fid"],
                       "original_locations": mapping,
                       "provenance": payload.get("provenance", {}), "payload": payload}
    return calls


def _related_calls(fids, facts, calls, *, step=None):
    linked = []
    for fid in fids:
        for entity in facts.get(fid, {}).get("args", []):
            if entity["kind"] == "call" and entity["index"] in calls:
                linked.append(entity["index"])
    # A shared tool is deliberately not expanded to all its invocations.
    if not linked and type(step) is int:
        linked = [key for key, call in calls.items() if call["import_step"] == step]
    return list(dict.fromkeys(linked))


def analyze_source(source, report, case, *, bundle=None, diagnosis=None, risk_review=None):
    bundle = bundle or build_bundle(source, online=False)
    record = bundle.snapshot()
    facts = {fact["fid"]: fact for fact in record["facts"]}
    calls = _call_index(record, source, case["id"])
    analysis = {"schema": SCHEMA, "case_id": case["id"], "source_sha256": digest(source),
                "analysis_version": bundle.fingerprint,
                "policy_sha256": digest(case["policy"]) if case.get("policy") else report.get("policy", {}).get("digest"),
                "rule_evaluations": plain(bundle.evaluations), "rules": export_rules(),
                "rule_applicability": [], "issues": [], "checks": [], "queries": [],
                "root_candidates": [], "calls": list(calls.values()), "diagnosis": diagnosis,
                "parse_audits": record.get("parse_audits", []),
                "task_outcome": task_outcome(source) if source.get("task_type") == "openclaw" else None,
                "notes": ["导入步骤是工具调用/返回转换后的编号，不等同于原始 Agent 执行步骤。",
                          "红色标明规则违反或有原文支持的模型错误判断；仍不是已证明的唯一根因。",
                          "绿色仅表示已执行且适用的检查在当前范围通过；不表示整个任务绝对安全。",
                          "所有原版规则结果保留。缺少记忆、反思、计划不会被新增为错误。"]}

    def issue(category, title, explanation, *, key=None, state="confirmed", fids=(), primary_fids=None,
              step=None, evidence=None, counter=(), missing=(), rule_id=None, origin=None, certainty=None, **extra):
        fids = list(dict.fromkeys(fid for fid in fids if type(fid) is int and fid in facts))
        linked = _related_calls(primary_fids if primary_fids is not None else fids, facts, calls, step=step)
        selected = [calls[key] for key in linked]
        ident = case["id"] + "::" + category + "::" + (key or digest([title, rule_id, fids, step])[:16])
        value = {"id": ident, "category": category, "category_label": LABELS[category], "state": state,
                 "title": title, "explanation": explanation, "source": origin or category,
                 "rule_id": rule_id, "certainty": certainty or ("evidence_supported" if state == "confirmed" else "uncertain"),
                 "call_id": linked[0] if len(linked) == 1 else None, "call_ids": linked,
                 "span_id": selected[0]["span_id"] if len(selected) == 1 else None,
                 "trace_id": selected[0]["trace_id"] if len(selected) == 1 else None,
                 "node_ids": [call["node_id"] for call in selected],
                 "import_steps": sorted({call["import_step"] for call in selected} or ({step} if type(step) is int else set())),
                 "fact_ids": fids, "evidence": evidence if evidence is not None else
                     [item for fid in fids if (item := _fact_source(facts[fid], source))],
                 "counterevidence": [item for fid in counter if fid in facts and (item := _fact_source(facts[fid], source))],
                 "missing": list(missing), "original_locations": [item for call in selected for item in call["original_locations"]],
                 **extra}
        analysis["issues"].append(value)
        return value

    # Preserve all seven original rules, including applicability/unknown states.
    for definition in analysis["rules"]:
        rows = [row for row in bundle.evaluations if row["rule"] == definition["id"]]
        analysis["rule_applicability"].append({"rule_id": definition["id"], "source": "reused_abstract_rules",
            "requirements": definition["evidence_requirements"], "states": dict(Counter(row["state"] for row in rows)),
            "explanation": RULE_NOTES.get(definition["id"], "按原版规则前提评估。")})
    for row in bundle.evaluations:
        detail = row.get("details", {})
        reason = detail.get("reason", "unknown")
        primary = detail.get("bindings", {}).get("fact")
        call_keys = _related_calls([primary] if type(primary) is int else [], facts, calls)
        analysis["checks"].append({"source": "reused_abstract_rules", "rule_id": row["rule"], "state": row["state"],
            "reason": reason, "call_ids": call_keys, "fact_ids": row.get("refs", []),
            "step": detail.get("event_step", row.get("t")),
            "applied": row["state"] in {"satisfied", "violated"}})
        if row["state"] == "violated":
            issue("rule", "原版规则发现问题", REASONS.get(reason, reason), fids=row.get("refs", []),
                  primary_fids=[primary] if type(primary) is int else [], step=detail.get("event_step"),
                  rule_id=row["rule"], origin="reused_abstract_rules")
        elif reason == "parse_incomplete":
            issue("uncertainty", "解析不完整，规则结论降级", REASONS[reason], state="unknown", fids=row.get("refs", []),
                  primary_fids=[primary] if type(primary) is int else [], step=detail.get("event_step"),
                  rule_id=row["rule"], origin="parse_audit", missing=detail.get("parse_gaps", []))

    source_matches = report.get("source", {}).get("sha256") == analysis["source_sha256"]
    queries = {row["id"]: row for row in report.get("queries", []) if "id" in row}
    if not source_matches:
        issue("uncertainty", "安全报告与当前输入不匹配", "没有应用过期报告的风险或通过状态。", state="unknown",
              origin="source_integrity", missing=["matching_safety_report"])
    else:
        for flag in report.get("flags", []):
            evidence = flag.get("evidence", [])
            ident = flag.get("trace_id", "") + "/" + flag.get("span_id", "")
            call = calls.get(ident)
            valid = bool(evidence) and call is not None and call["import_step"] == flag.get("step")
            valid = valid and all(verified_evidence(e, facts, source, queries) for e in evidence)
            valid = valid and any(e["fid"] == call["fid"] for e in evidence)
            if not valid:
                issue("uncertainty", "安全标记的证据无法核对", "保存的 SOURCE 片段与当前调用不一致，未判定违规。",
                      key="invalid-" + flag["id"], state="unknown", step=flag.get("step"),
                      origin="safety_evidence_validation", missing=["verified_SOURCE_rows"])
                continue
            extras = [e for e in flag.get("effect_evidence", []) if verified_evidence(e, facts, source, queries)]
            issue("safety", flag["title"], flag.get("recommendation", "已记录违反当前策略的调用尝试。"),
                  key=flag["id"], state="violation_attempt", fids=[e["fid"] for e in evidence + extras],
                  primary_fids=[call["fid"]], evidence=evidence + extras, rule_id=flag["rule_id"],
                  origin="safety_audit", certainty="evidence_supported", flag_id=flag["id"],
                  effect=flag.get("effect", "unknown"), policy=report.get("policy"),
                  limitations=["违规尝试不等同于产生了危险效果，也不自动等同于任务失败根因。"])
        for row in report.get("steps", []):
            ident = str(row.get("trace_id", "")) + "/" + str(row.get("span_id", ""))
            call = calls.get(ident)
            matched = call is not None and call["import_step"] == row.get("step") and call["tool"] == row.get("tool")
            analysis["checks"].append({"source": "safety_audit", "rule_id": report.get("policy", {}).get("id"),
                "state": row.get("status", "unknown") if matched else "unknown", "call_ids": [ident] if matched else [],
                "applied": matched and row.get("status") in {"safe", "unsafe"} and not row.get("gaps"),
                "step": row.get("step"), "tool": row.get("tool"), "kind": row.get("kind"),
                "reason": "；".join(row.get("gaps", [])) or "explicit_policy_check",
                "gaps": row.get("gaps", [])})
            if row.get("gaps"):
                issue("uncertainty", "本次调用的证据不足", "；".join(row["gaps"]), state="unknown",
                      step=row.get("step"), fids=[call["fid"]] if call else [], origin="safety_audit", missing=row["gaps"])
    coverage = source.get("telemetry", {}).get("coverage", {})
    complete = (coverage.get("declared_complete") is True and coverage.get("content_complete") is True
                and not coverage.get("gaps"))
    analysis["capture_complete"] = complete
    if not complete:
        issue("uncertainty", "采集完整性尚未确认", "没有告警也不能证明未记录的部分通过检查。", state="unknown",
              origin="capture_coverage", missing=coverage.get("gaps") or ["complete_capture_declaration"])
    for audit in record.get("parse_audits", []):
        if audit.get("status") == "unresolved":
            issue("uncertainty", "有原文尚未可靠解析", "未将解析缺口当成规则违反。", state="unknown", step=audit.get("step"),
                  origin="parse_audit", missing=audit.get("reasons", []) or [audit.get("source_id", "unresolved_parse")])

    # tool_error is an objective observation. It is never automatically a red decision.
    for fact in record["facts"]:
        if fact["rel"] == "tool_error":
            issue("runtime", "工具返回错误", "这次工具调用记录了错误状态；是否是错误决策，需另外核对任务、参数和上下文。",
                  fids=[fact["fid"]], state="observed", origin="captured_tool_result", certainty="observed",
                  limitations=["工具错误可能是预期结果或已恢复错误，不能单独证明 Agent 决策错误。"])

    if source.get("task_type") == "openclaw":
        _task_extension(source, record, calls, analysis, issue, complete, case.get("policy") or {}, bundle)
    if source.get("session"):
        from .session_checks import session_checks, session_findings
        checks = session_checks(bundle)
        analysis["session_checks"] = checks
        for finding in session_findings(bundle):
            finding = dict(finding)
            entity_ids = finding.pop("entity_ids", [])
            if not entity_ids:
                affected = set(finding.get("affected_event_ids", []))
                entity_ids = ["message:" + str(e.get("trace_id")) + "/" + e["event_id"]
                              for e in source["session"].get("events", []) if e["event_id"] in affected]
            finding["node_ids"] = [case["id"] + "::" + value for value in entity_ids if value in record["entities"]]
            finding["import_steps"] = sorted({e["step"] for e in source["session"].get("events", [])
                if e["event_id"] in finding.get("affected_event_ids", [])})
            issue(**finding)
    _model_findings(source, facts, calls, analysis, issue, diagnosis)
    if risk_review:
        from .risk_presentation import merge_risk_review
        merge_risk_review(source, facts, calls, analysis, issue, risk_review)
    categories = Counter(item["category"] for item in analysis["issues"])
    findings = [item for item in analysis["issues"] if item["category"] != "uncertainty"]
    analysis["summary"] = {"finding_count": len(findings), "issue_count": len(analysis["issues"]),
        "uncertainty_count": categories["uncertainty"], "by_category": {key: categories[key] for key in CATEGORIES},
        "affected_trajectory_count": int(bool(findings)), "affected_call_count": len({key for row in findings for key in row["call_ids"]}),
        "counting": "按唯一问题计数；一个问题引用多个实体或事实只计一次。"}
    from .assessment_presentation import add_check_summaries
    add_check_summaries(analysis, source, facts, calls, (risk_review or {}).get("policy"))
    return analysis


def _task_extension(source, record, calls, analysis, issue, complete, policy, bundle):
    """Narrow explicit read-target check; no NLP inference or benchmark labels."""
    rule_id = "OC-TASK-001"
    check = {"source": "openclaw_extension", "rule_id": rule_id, "state": "unknown", "applied": False,
             "call_ids": [], "reason": "requires_one_explicit_read_contract_and_one_call"}
    analysis["checks"].append(check)
    applicability = {"rule_id": rule_id, "source": "openclaw_extension", "requirements": ["complete_capture",
        "complete_single_read_contract", "required_path_in_task", "one_read_call", "exact_call_and_result_sources"],
        "explanation": "新增适配：只检查完整、单项且只有一次 read 调用的明确路径任务；多调用/备用路径任务不推断。"}
    analysis["rule_applicability"].append(applicability)
    contract = source.get("task_contract") or {}
    outcome = analysis["task_outcome"] or {}
    checks = outcome.get("checks", [])
    if contract.get("complete") is not True or len(checks) != 1 or len(calls) != 1:
        applicability["states"] = {"unknown": 1}
        return
    requirement = checks[0]
    predicates = requirement.get("argument_equals", {})
    call = next(iter(calls.values()))
    expected = predicates.get("/path") if set(predicates) == {"/path"} else None
    actual = call["payload"].get("arguments", {}).get("path") if isinstance(call["payload"].get("arguments"), dict) else None
    if (requirement.get("tool") not in {"read", "read_file"} or call["tool"] != requirement.get("tool")
            or not isinstance(expected, str) or not isinstance(actual, str) or expected not in source["task"]):
        applicability["states"] = {"unknown": 1}
        return
    check["call_ids"] = [call["call_id"]]
    facts = {fact["fid"]: fact for fact in record["facts"]}
    action = facts[call["fid"]]
    results = [fact for fact in record["facts"] if fact["rel"] == "tool_result"
               and any(arg["kind"] == "call" and arg["index"] == call["call_id"] for arg in fact["args"])]
    sources = [_fact_source(fact, source) for fact in [action, *results]]
    if not complete or len(results) != 1 or not all(sources) or any(a.get("status") == "unresolved" for a in record["parse_audits"]):
        check["reason"] = "incomplete_capture_or_source"
        applicability["states"] = {"unknown": 1}
        return
    from .otel_import import _truncated
    from .safety_audit import _Evidence, _locations
    result_payload = _loads(results[0]["value"])
    if "result" not in result_payload or _truncated(result_payload.get("result")) or _truncated(call["payload"].get("arguments")):
        check["reason"] = "missing_or_truncated_arguments_or_result"
        applicability["states"] = {"unknown": 1}
        return
    query_evidence = _Evidence(bundle, analysis)
    identity = (call["trace_id"], call["span_id"])
    target_proof = query_evidence.member(action["t"], "action", action["value"], _locations(action["value"]), ("arguments", "path"), identity)
    result_proof = query_evidence.member(results[0]["t"], "observation", results[0]["value"], _locations(results[0]["value"]), ("status",), identity)
    if not target_proof or not result_proof:
        check["reason"] = "SOURCE_query_unavailable"
        applicability["states"] = {"unknown": 1}
        return
    module = ntpath if ntpath.splitdrive(expected)[0] else posixpath
    cwd = policy.get("working_directory")
    if module.isabs(actual) != module.isabs(expected) and not isinstance(cwd, str):
        check["reason"] = "relative_path_requires_working_directory"
        applicability["states"] = {"unknown": 1}
        return
    def normalized(path):
        if not module.isabs(path) and isinstance(cwd, str):
            path = module.join(cwd, path)
        return module.normcase(module.normpath(path))
    same = normalized(actual) == normalized(expected)
    check.update(applied=True, state="satisfied" if same else "violated", reason="matching_task_target" if same else "explicit_task_target_mismatch")
    applicability["states"] = {check["state"]: 1}
    if same:
        return
    # A matching result elsewhere would defeat the premise; the narrow one-call
    # condition and original task-contract evaluator both guard this extension.
    if requirement.get("state") != "unmet":
        check.update(applied=False, state="unknown", reason="task_requirement_not_established_unmet")
        applicability["states"] = {"unknown": 1}
        return
    task_evidence = {"source": "explicit_task_contract", "source_ref": "task_contract/checks/0/argument_equals/~1path",
                     "text": expected, "task_text": source["task"], "check_id": requirement["id"]}
    issue("rule", "读取目标与明确任务不一致", "任务和完整契约要求读取 A，但唯一的 read 调用使用了不同目标 B；采集内未满足原要求。",
          rule_id=rule_id, origin="openclaw_extension", fids=[action["fid"], results[0]["fid"]],
          primary_fids=[action["fid"]], evidence=[task_evidence, target_proof, result_proof],
          expected_target=expected, observed_target=actual,
          limitations=["这是可观察的任务目标不匹配，不证明内部思考过程或唯一根因。",
                       "仅适用一次明确 read 调用；不评价多调用、允许备用来源或任意自然语言任务。"])


def _model_findings(source, facts, calls, analysis, issue, diagnosis):
    if not diagnosis:
        return
    if diagnosis.get("source_sha256") != analysis["source_sha256"]:
        issue("uncertainty", "模型诊断与当前输入不匹配", "未应用过期模型结果。", state="unknown", origin="diagnosis_integrity",
              missing=["matching_diagnosis_source"])
        return
    saved_policy = diagnosis.get("policy_sha256")
    if (saved_policy is not None or diagnosis.get("id")) and saved_policy != analysis.get("policy_sha256"):
        issue("uncertainty", "模型诊断使用的策略已过期或无法核对", "当前安全策略与诊断运行时策略不同，未应用旧模型错误标记；请按当前策略重新诊断。",
              state="unknown", origin="diagnosis_integrity", missing=["matching_diagnosis_policy"])
        return
    result = diagnosis.get("result") or {}
    selected_root = (result.get("root") or {}).get("root_id")
    seen = set()
    for event in diagnosis.get("assessments", []):
        for candidate_index, candidate in enumerate(event.get("candidates", []), 1):
            key = digest(candidate)[:16]
            if key in seen:
                continue
            seen.add(key)
            fid, step = candidate.get("primary_fid"), candidate.get("step")
            refs = [fid, *candidate.get("evidence_for", [])]
            refs_valid = all(type(value) is int and value in facts for value in refs)
            primary = facts.get(fid, {})
            module = candidate.get("module")
            from .session_sources import source_text, slots_for
            text = source_text(source, step, module) if type(step) is int and 1 <= step <= len(source["steps"]) else None
            quote = candidate.get("output_quote")
            matched = (refs_valid and primary.get("t") == step and isinstance(text, str)
                       and primary.get("slot") == slots_for(source).get(module)
                       and primary.get("side") == "mind"
                       and isinstance(quote, str) and bool(quote) and quote in text
                       and isinstance(primary.get("span"), str) and (quote in primary["span"] or primary["span"] in quote))
            if any(a.get("status") == "unresolved" and a.get("step", 0) <= (step or 0)
                   for a in analysis.get("parse_audits", [])):
                matched = False
            supported = matched and candidate.get("status") == "error"
            missing = [] if matched else ["valid_primary_fact_and_exact_source_quote"]
            if candidate.get("status") == "uncertain":
                missing.append(candidate.get("uncertainty") or "model_uncertainty")
            model_issue = issue("model", "模型判断的错误步骤" if supported else "模型调查结论尚不确定",
                candidate.get("reasoning") or candidate.get("proposition") or "保留模型原始调查结果。",
                key=key, state="confirmed" if supported else "unknown", fids=refs,
                primary_fids=[fid] if type(fid) is int else [], step=step,
                counter=candidate.get("evidence_against", []), missing=missing,
                origin="model_TRACE", certainty="model_judgement_with_source" if supported else "uncertain",
                fault_class=candidate.get("fault_class"), proposition=candidate.get("proposition"),
                output_quote=quote, uncertainty=candidate.get("uncertainty"),
                window_id=event.get("window_id"), is_root_cause=candidate.get("is_root_cause"),
                limitations=["这是模型判断与来源核对，不能解释为数学证明或已知的唯一根因。"])
            if module == "message" and source.get("session"):
                message_event = next((e for e in source["session"].get("events", []) if e["step"] == step), None)
                if message_event:
                    model_issue.update(call_id=None, call_ids=[], span_id=None,
                        node_ids=[analysis["case_id"] + "::message:" + str(message_event.get("trace_id")) + "/" + message_event["event_id"]],
                        affected_event_ids=[message_event["event_id"]], import_steps=[step],
                        original_locations=[message_event.get("source", {})])
            if candidate.get("is_root_cause") is not False:
                analysis["root_candidates"].append({"id": key, "issue_id": model_issue["id"],
                    "state": "candidate" if supported else "uncertain",
                    "selected": selected_root == f"trace-window:{event.get('window_id')}:{candidate_index}",
                    "call_ids": model_issue["call_ids"], "import_steps": model_issue["import_steps"],
                    "fact_ids": model_issue["fact_ids"], "explanation": model_issue["explanation"],
                    "proven": False, "uncertainty": candidate.get("uncertainty") or "候选尚不能证明为唯一根因。"})
    if result.get("status") == "uncertain" or diagnosis.get("status") in {"failed", "cancelled", "timed_out"}:
        issue("uncertainty", "完整诊断未能接受根因" if result.get("status") == "uncertain" else "完整诊断未完成",
              result.get("uncertainty") or result.get("reasoning") or diagnosis.get("error") or diagnosis.get("status"),
              key="diagnosis-status", state="unknown", origin="model_SUBMIT", missing=[result.get("stop_reason") or diagnosis.get("status")])


def annotate_nodes(nodes, analysis):
    """Conservative visual policy, with independent risk/error/runtime badges."""
    # Global capture limits are a banner, but a broken input/report identity
    # invalidates this saved audit's attribution everywhere.
    global_unknown = any(row["category"] == "uncertainty" and not row["node_ids"]
                         and row["source"] in {"source_integrity", "parse_audit"} for row in analysis["issues"])
    for node in nodes:
        issues = [row for row in analysis["issues"] if node["id"] in row["node_ids"]]
        checks = [row for row in analysis["checks"] if node.get("index") in row.get("call_ids", [])]
        node["issue_ids"] = [row["id"] for row in issues]
        node["checks"] = checks
        summary = analysis.get("check_summaries", {}).get(node.get("index"))
        if summary:
            node["check_summary"] = summary
            node["assessment"] = {"status": summary["overall"], "label": summary["label"],
                "label_en": summary["label_en"], "summary_id": summary["summary_id"],
                "has_unresolved": summary["has_unresolved"], "has_conflict": summary["has_conflict"],
                "scope_note": summary["scope_note"], "scope_note_en": summary["scope_note_en"]}
        node["badges"] = [{"category": row["category"], "label": "违规尝试" if row["state"] == "violation_attempt" else ("可疑输入" if row["state"] == "input_warning" else LABELS[row["category"]]),
                           "state": row["state"], "issue_id": row["id"]} for row in issues]
        node["role"] = "behavior" if node["kind"] in {"call", "message"} else "structure"
        node["flag_ids"] = [row["flag_id"] for row in issues if row.get("flag_id")]
        if node["kind"] not in {"call", "message"}:
            visual, reason = "neutral", "结构实体；同一工具的各次调用分别评估，不把某次问题扩散到工具全部调用。"
        elif any(row["category"] in {"rule", "model"} and row["state"] == "confirmed" for row in issues):
            visual, reason = "error", "有原文支持的规则问题或模型错误判断；见来源、证据及限制。"
        elif any(row["category"] == "safety" and row["state"] == "violation_attempt" for row in issues):
            visual, reason = "risk", "记录了安全违规尝试；是否产生效果、是否导致任务失败另行判断。"
        elif any(row["category"] == "safety" and row["state"] == "input_warning" for row in issues):
            visual, reason = "risk", "记录中含可疑外部输入；仅为调查线索，不代表 Agent 已执行其中指令。"
        elif global_unknown or any(row["category"] == "uncertainty" or row["state"] == "unknown" for row in issues):
            visual, reason = "unknown", "相关证据不足或模型仅怀疑，暂不能确定。"
        elif any(row["applied"] and row["state"] in {"safe", "satisfied"} for row in checks):
            visual, reason = "passed", "已执行且适用的检查在当前范围内通过；未覆盖的规则和任务语义见详情。"
        else:
            visual, reason = "neutral", "未执行适用检查或本规则不适用；没有告警不表示通过。"
        node["visual_state"], node["reason"] = visual, reason
        node["status"] = {"error": "unsafe", "risk": "unsafe", "passed": "safe", "unknown": "unknown", "neutral": "neutral"}[visual]
    return nodes
