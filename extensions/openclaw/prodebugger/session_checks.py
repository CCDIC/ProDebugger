"""Small, explicit checks over session facts; no generic semantic verdicts.

Checks can certify a particular quotation/contract predicate, never the entire
task, internal intention, or a unique root cause. Unknown is not a pass.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

from .session_artifact import _json, _result_error, _text


def _pointer(value, pointer):
    for part in pointer.lstrip("/").split("/") if pointer else []:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise KeyError(part)
    return value


def _equal(left, right):
    return type(left) is type(right) and left == right


def _predicate(value, predicates):
    try:
        return all(_equal(_pointer(value, pointer), expected) for pointer, expected in predicates.items())
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def _numeric_fields(text):
    """Only unambiguous, standalone quoted numeric fields, e.g. Revenue: 42.

    Free-form claims, qualifications and inferred summaries are unsupported.
    A duplicate field with conflicting values remains ambiguous.
    """
    result = {}
    for key, value in re.findall(r"(?m)^\s*([A-Za-z][A-Za-z _-]{0,50})\s*:\s*(-?\d+(?:\.\d+)?)\s*$", text):
        result.setdefault(key.strip().casefold(), set()).add(Decimal(value))
    return result


def session_checks(bundle):
    source, record = bundle.source, bundle.record
    session = source.get("session", {})
    if session.get("schema") not in {"openclaw-session/v1", "openclaw-session/v2", "openclaw-signoz-session/v1"}:
        return []
    events = {event["step"]: event for event in session.get("events", [])}
    meta = getattr(record, "session_fact_meta", {})
    calls = {str(f.args[0]): f for f in record.F if f.rel == "tool_call"}
    outputs = {str(f.args[0]): f for f in record.F if f.rel == "tool_result"}
    contracts = [f for f in record.F if f.rel == "session_contract"]
    contract = source.get("task_contract")
    checks = []

    def emit(rule, suffix, state, title, explanation, fids=(), call_ids=(), missing=(), comparison=((), ()), primary_fids=()):
        fids = list(dict.fromkeys(fids))
        event_ids = list(dict.fromkeys(ident for fid in fids for ident in meta.get(fid, {}).get("source_events", [])))
        evidence = []
        for fid in fids:
            fact = record.fact(fid)
            evidence.append({"fid": fid, "step": fact.t, "text": fact.span,
                             "source_events": meta.get(fid, {}).get("source_events", []),
                             "source": meta.get(fid, {}).get("source", {}),
                             "note": "规范事实与原始记录映射；不是隐藏思维或因果证明。"})
        check = {"id": rule + ":" + suffix, "rule_id": rule, "state": state,
                 "title": title, "explanation": explanation, "fids": fids, "event_ids": event_ids,
                 "primary_fids": list(primary_fids), "affected_fids": list(primary_fids), "checked_fids": fids,
                 "affected_event_ids": list(dict.fromkeys(ident for fid in primary_fids for ident in meta.get(fid, {}).get("source_events", []))),
                 "call_ids": list(call_ids), "evidence": evidence, "missing": list(missing),
                 "comparison_fids": [list(comparison[0]), list(comparison[1])],
                 "source": "openclaw_session_extension", "applied": state in {"passed", "violated"},
                 "scope": "仅核对明确引用和显式契约；不证明整个任务完成或唯一根因。"}
        checks.append(check)
        return check

    contract_valid = False
    if contract is not None:
        from .openclaw_contract import validate
        try:
            validate(contract)
            contract_valid = True
        except (ValueError, TypeError, KeyError):
            emit("OC-SESSION-CONTRACT-001", "invalid", "unknown", "任务契约无法解析",
                 "显式任务契约不符合可检查格式，没有将它解释成任务违反。",
                 [f.fid for f in contracts], missing=["有效的显式任务契约"])
    if contract_valid:
        # Reuse the existing explicit-contract evaluator. This view adds the
        # state-table pointers, not a second independently deciding rule engine.
        from .openclaw_contract import evaluate
        original_outcome = evaluate(source)
        originals = {item["id"]: item for item in original_outcome.get("checks", [])}
        for check in contract["checks"]:
            candidates = [f for f in calls.values() if (data := _json(f.value)).get("tool") == check["tool"]
                          and _predicate(data.get("arguments"), check["argument_equals"])]
            matched = [(f, outputs[str(f.args[0])]) for f in candidates if str(f.args[0]) in outputs]
            fids = [f.fid for f in contracts] + [f.fid for pair in matched for f in pair]
            state = {"satisfied": "passed", "unmet": "violated"}.get(originals.get(check["id"], {}).get("state"), "unknown")
            explanation = ("原有显式契约检查找到目标参数及对应返回满足此条件；只表示该项条件通过。" if state == "passed" else
                           "原有显式契约检查判定此结果条件未满足；不直接证明 Agent 决策错误或根因。" if state == "violated" else
                           "没有足够的匹配成功返回来核验此条件；缺失或工具失败不自动等于任务决策错误。")
            emit("OC-SESSION-CONTRACT-001", check["id"], state, "显式任务条件对照", explanation, fids,
                 [str(c.args[0]).removeprefix("call:") for c, r in matched],
                 [] if state != "unknown" else ["与声明参数匹配且内容完整的工具返回"],
                 ([f.fid for f in contracts], [r.fid for c, r in matched]))

    final_facts = [f for f in record.F if f.rel == "session_final_answer"]
    if not final_facts:
        emit("OC-SESSION-ANSWER-001", "missing", "unknown", "最终回答未能核验",
             "没有明确标为最终回答的原生消息，不能用最后一个工具返回替代用户答复。", missing=["带有结束标记的最终回答"])
    for final in final_facts:
        text = str(final.value)
        # Only evidence already available at the time of the answer is eligible.
        latest = {}
        for key, call in calls.items():
            if key not in outputs or outputs[key].t >= final.t:
                continue
            call_data = _json(call.value)
            # Only identical tool+arguments supersede one another. Different
            # ranges/files can legitimately return different numeric values.
            target = (call_data.get("tool"), json.dumps(call_data.get("arguments"), ensure_ascii=False, sort_keys=True))
            result = outputs[key]
            if target not in latest or result.t > latest[target][1].t:
                latest[target] = (call, result)
        available = [(call, result) for call, result in latest.values() if not _result_error(_json(result.value))]
        if contract_valid:
            expected_candidates = [(c, r) for c, r in available if any(
                _json(c.value).get("tool") == check["tool"]
                and _predicate(_json(c.value).get("arguments"), check["argument_equals"])
                for check in contract["checks"])]
        else:
            expected_candidates = available
        comparable = [(c, r, _text(_json(r.value).get("result"))) for c, r in expected_candidates]
        comparable = [(c, r, output) for c, r, output in comparable if output.strip()]
        final_fields = _numeric_fields(text)
        field_values = {}
        for call, result, output in comparable:
            for key, values in _numeric_fields(output).items():
                field_values.setdefault(key, set()).update(values)
        contradictions = {key for key, values in final_fields.items() if len(values) == 1 and
                          len(field_values.get(key, set())) == 1 and values != field_values[key]}
        qualified = bool(re.search(r"\b(?:example|hypothetical|illustrat\w*|incorrect|wrong|would|could|might)\b|"
                                   r"\bnot\s+(?:the|actual|correct|equal)\b|示例|举例|假设|并非|不应|不是实际", text, re.I))
        matching_keys = {key for key, values in final_fields.items() if len(values) == 1 and values == field_values.get(key)}
        exact = [(c, r) for c, r, output in comparable if output.strip() in text]
        fids = [final.fid] + [r.fid for c, r, output in comparable]
        call_ids = [str(c.args[0]).removeprefix("call:") for c, r, output in comparable]
        # A quote of 'Revenue: 99' against a unique recorded 'Revenue: 42'
        # is a bounded contradiction. General statements remain unvalidated.
        if contradictions and qualified:
            state, title = "unknown", "数值差异涉及限定语，尚不能判断"
            explanation = "回答有数值差异，但也出现举例、否定或假设用语；本规则不能确认它是否在断言实际结果。"
            missing = ["确认数值属于最终断言还是被否定／举例的内容"]
        elif contradictions:
            state, title = "violated", "最终回答中的明确数值与工具返回不一致"
            explanation = "最终回答中的独立数值字段 " + "、".join(sorted(contradictions)) + " 与相关工具返回冲突。仅定位这处公开回答，不推断内部原因。"
            missing = []
        elif exact or matching_keys:
            state, title = "passed", "最终回答的明确引用与返回一致"
            explanation = "回答中可直接核对的原文引用／数值与对应返回一致；其他说明、任务全部要求和根因尚未由此验证。"
            missing = []
        else:
            state, title = "unknown", "最终回答的语义仍需核验"
            explanation = "未找到本适配支持的明确原文引用或唯一数值字段对照；不能把未发现矛盾当成检查通过。"
            missing = ["支持明确比较的回答内容／对应工具输出", "通用自然语言语义核验"]
        emit("OC-SESSION-ANSWER-001", str(final.fid), state, title, explanation, fids,
             call_ids, missing, ([final.fid], [r.fid for c, r, output in comparable]), primary_fids=[final.fid])
    return checks


def session_findings(bundle):
    """Arguments compatible with trace_analysis.issue; never pin answer errors
    onto an otherwise correct tool call just because that call supplied evidence.
    """
    findings = []
    event_map = {event["event_id"]: event for event in bundle.source.get("session", {}).get("events", [])}
    for check in session_checks(bundle):
        # Existing trace_analysis already presents explicit task-contract
        # findings. Keep this table comparison without counting it twice.
        if check["rule_id"] == "OC-SESSION-CONTRACT-001":
            continue
        if check["state"] not in {"violated", "unknown"}:
            continue
        answer = check["rule_id"] == "OC-SESSION-ANSWER-001"
        fids = check["fids"]
        primary = [fid for fid in fids if bundle.record.fact(fid).rel == "session_final_answer"] if answer else fids
        affected_events = list(dict.fromkeys(event_id for fid in primary for event_id in
                              getattr(bundle.record, "session_fact_meta", {}).get(fid, {}).get("source_events", [])))
        message_entities = ["message:" + (str(event_map[event_id]["trace_id"]) + "/" if event_map[event_id].get("trace_id") else "") + event_id
                            for event_id in affected_events if event_id in event_map and event_map[event_id].get("type") == "session_message"]
        findings.append({"category": "rule" if check["state"] == "violated" else "uncertainty",
            "title": check["title"], "explanation": check["explanation"], "key": check["id"],
            "state": "confirmed" if check["state"] == "violated" else "unknown", "fids": fids,
            "primary_fids": primary, "step": None, "evidence": check["evidence"], "missing": check["missing"],
            "rule_id": check["rule_id"], "origin": "openclaw_session_extension",
            "certainty": "evidence_supported" if check["state"] == "violated" else "uncertain",
            "event_ids": check["event_ids"], "affected_event_ids": affected_events,
            "entity_ids": message_entities,
            "call_ids": [] if answer else check["call_ids"],
            "related_call_ids": check["call_ids"], "node_ids": [] if answer else [],
            "scope": check["scope"]})
    return findings
