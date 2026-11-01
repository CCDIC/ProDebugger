"""Presentation-only comparison. Private answers never enter review jobs."""
from __future__ import annotations

import hashlib

from .record_bridge import digest, read_json
from .risk_validation import TERMINAL, compare


def manifest(store):
    path = store.folder / "showcase.json"
    return read_json(path) if path.is_file() else {"scenarios": [], "acquisition_cases": []}


def _scenario(store, sid):
    value = next((s for s in manifest(store)["scenarios"] if s["id"] == sid), None)
    if value is None:
        raise KeyError(sid)
    return value


def _answers(store):
    path = store.folder.parent / "private-evaluation" / "answers.json"
    wanted = path.with_suffix(".sha256").read_text(encoding="ascii").strip()
    if hashlib.sha256(path.read_bytes()).hexdigest() != wanted:
        raise ValueError("预先固定的标准答案校验失败，停止对照。")
    return read_json(path)


def _compare(expected, findings, **kwargs):
    # Reuse exact-call/type evaluation. Input warnings are a distinct finding,
    # not a confirmed Agent violation; compare them separately at the same call.
    ordinary = [e for e in expected if e.get("status") != "input_warning"]
    value = compare(ordinary, findings, **kwargs)
    if not kwargs.get("completed", True) or not kwargs.get("available", True):
        value["expected_count"] = len(expected)
        return value
    warnings = []
    for item in expected:
        if item.get("status") != "input_warning":
            continue
        hits = [f for f in findings if f.get("risk_type") == item["risk_type"]
                and f.get("call_id") == item["call_id"] and f.get("status") == "input_warning"]
        ok = any(f.get("evidence") for f in hits)
        warnings.append({"status": "hit" if ok else "missed", "expected": item,
                         "exact_call_match": bool(hits), "evidence_cited": ok,
                         "actual": [{k: f.get(k) for k in ('call_id', 'span_id', 'import_step', 'risk_type', 'status')}
                                    for f in hits]})
    value["warning_checks"] = warnings
    value["exact_hits"] += sum(w["status"] == "hit" for w in warnings)
    value["expected_count"] += len(warnings)
    if warnings and not all(w["status"] == "hit" for w in warnings):
        value.update(status="missed", label="部分预设输入警告未命中")
    elif warnings and value["status"] == "control_pass":
        value.update(status="hit", label="精确命中输入警告")
    return value


def evaluation(store, sid):
    scenario = _scenario(store, sid)
    answers = _answers(store)
    selected = [a for a in answers["cases"] if a["scenario_id"] == sid]
    jobs = {a["case_id"]: store.latest_risk_review(a["case_id"]) for a in selected}
    done = bool(selected) and all(j and j.get("status") in TERMINAL for j in jobs.values())
    result = []
    all_match = True
    for answer in selected:
        cid = answer["case_id"]
        case, source, base_audit = store._material(cid)
        auto, job = store.risk_report(cid), jobs[cid]
        matches = (digest(source) == answer["input_sha256"]
                   and digest(case.get("risk_policy")) == answer["risk_policy_sha256"])
        all_match = all_match and matches
        model_result = (job or {}).get("result") or {}
        hidden = {"status": "awaiting_analysis", "label": "两条轨迹的模型流程结束后开放对照"}
        comparison = dict(hidden)
        if done and matches:
            comparison = {
                "automatic": _compare(answer["expected"], auto.get("findings", [])),
                "model": _compare(answer["expected"], model_result.get("findings", []),
                    available=bool(job), completed=bool(job and job.get("status") == "succeeded"
                                                       and job.get("full_flow_completed"))),
            }
        elif not matches:
            comparison = {"status": "input_changed", "label": "输入或策略已改变，固定答案不再适用"}
        findings = auto.get("findings", [])
        result.append({"case_id": cid, "role": answer["role"],
            "is_constructed": answer["is_constructed"], "source_category": answer["source_category"],
            "rule_summary": auto.get("summary"), "automatic": auto, "rule_report": auto,
            "base_safety": base_audit,
            "model_status": (job or {}).get("status", "not_run"),
            "review_id": (job or {}).get("id"),
            "model": {"status": (job or {}).get("status", "not_run"),
                      "stage": (job or {}).get("stage"), "error": (job or {}).get("error"),
                      "full_flow_completed": (job or {}).get("full_flow_completed", False),
                      "review_protocol": (job or {}).get("review_protocol"),
                      "reference_audit": (job or {}).get("reference_audit", {}),
                      "usage": (job or {}).get("usage", {}),
                      "stages": (job or {}).get("stages", []), "result": model_result},
            "findings": findings, "model_findings": model_result.get("findings", []),
            "uncertainties": [f for f in findings if f.get("status") in {"unknown", "suspected"}],
            "evaluation": comparison})
    return {"schema": "showcase-evaluation/v1", "scenario_id": scenario["id"],
        "analysis_complete": done, "reference_available": done and all_match,
        "cases": result, "notice": "自动规则、模型判断、事后标准答案分别保存。失败仍保留自动结果。"}


def reference(store, sid):
    view = evaluation(store, sid)
    if not view["reference_available"]:
        raise ValueError("先运行该场景两条轨迹的完整风险审查；达到终态且输入未改变后再展开独立答案。")
    fixed = _answers(store)
    selected = [a for a in fixed["cases"] if a["scenario_id"] == sid]
    return {"schema": "showcase-reference/v1", "scenario_id": sid,
        "fixed_at": fixed["fixed_at"], "ground_truth": selected,
        "diff": [{"case_id": a["case_id"], "changes": a.get("changes", []),
                  "official_export_changes": a.get("official_export_changes", [])} for a in selected],
        "evaluation": view, "notice": "答案和差异只供分析后对照；没有进入模型输入或检索库。开发展示样例不证明总体准确率。"}


def source_map(store, cid):
    case, source, _ = store._material(cid)
    session, telemetry = source.get("session", {}), source.get("telemetry", {})
    return {"case_id": cid, "source_category": case.get("source_category"),
        "is_constructed": case.get("is_constructed", False),
        "session_schema": session.get("schema"), "record_map": session.get("record_map", []),
        "source_map": telemetry.get("source_map", []),
        "source_summary": session.get("coverage", session.get("summary", {})), "coverage": telemetry.get("coverage", {}),
        "originals": case.get("showcase_source", {}), "input_sha256": digest(source),
        "notice": "导入序号只是展示编号。原始事件、工具调用ID与本地分析标识分别保存；无云Span时不冒称云Span。"}
