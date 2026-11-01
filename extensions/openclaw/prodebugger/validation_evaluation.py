"""Independent post-analysis comparison against immutable injection answers.

Neither the diagnostic worker nor the automatic analyzers import this module.
Private answers are read only after every case has a terminal, matching run.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from .record_bridge import digest, read_json, write_json

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inside(root, value):
    path = (Path(root) / value).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("验证文件超出已注册案例目录")
    return path


def registry(store):
    path = store.folder / "validation/registry.json"
    return read_json(path) if path.exists() else {"schema": "localization-registry/v1", "suites": []}


def register_suite(store, manifest_path):
    """Local CLI-only registration; no client-selected filesystem paths."""
    from .safety_audit import validate_policy
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    if manifest.get("schema") != "prodebugger-localization-suite/v1" or not manifest.get("cases"):
        raise ValueError("无效的定位验证清单")
    root = manifest_path.parent
    for key, hash_key in (("private_answers", "ground_truth_sha256"), ("private_diffs", "diffs_sha256")):
        if file_hash(inside(root, manifest[key])) != manifest[hash_key]:
            raise ValueError("固定标准答案或差异的摘要已改变")
    policy_hashes = {}
    for item in manifest["cases"]:
        source_path = inside(root, item["source_path"])
        if file_hash(source_path) != item["source_sha256"]:
            raise ValueError("已固定的输入发生改变")
        policy_path = inside(root, item["policy_path"])
        policy_hashes[item["case_id"]] = file_hash(policy_path)
        source, policy = read_json(source_path), validate_policy(read_json(policy_path))
        existing = store._find(item["case_id"])
        if existing:
            if read_json(store._resolve(existing["source_path"])) != source or existing["policy"] != policy:
                raise ValueError("案例 ID 已被不同输入或策略使用")
        else:
            store.add_source(source, policy, title="定位验证 · " + item["label"] +
                             (" · 构造数据" if item["is_constructed"] else " · 真实对照"), case_id=item["case_id"])
        with store.lock:
            case = store._find(item["case_id"])
            case.update(source_category=item["source_category"], is_constructed=item["is_constructed"],
                        validation_suite=manifest["id"], origin_label="基于真实轨迹构造的错误变体" if item["is_constructed"]
                        else "真实 OpenClaw 运行 · 只读对照副本")
            store._save()
    with store.lock:
        current = registry(store)
        old = next((row for row in current["suites"] if row["id"] == manifest["id"]), None)
        entry = {"id": manifest["id"], "manifest_path": str(manifest_path), "manifest_sha256": file_hash(manifest_path),
                 "case_policy_sha256": policy_hashes}
        # An initial registry written by this feature before policy pinning can
        # only be upgraded through explicit local registration. All saved cases
        # above must still equal the fixed inputs and normalized policies.
        if old and "case_policy_sha256" not in old and all(old.get(key) == entry[key]
                for key in ("id", "manifest_path", "manifest_sha256")):
            old["case_policy_sha256"] = policy_hashes
            write_json(store.folder / "validation/registry.json", current)
        if old and old != entry:
            raise ValueError("同一验证组已有不同清单；请使用新的组 ID")
        if not old:
            current["suites"].append(entry)
            write_json(store.folder / "validation/registry.json", current)
    return manifest


def loaded_suite(store, suite_id):
    entry = next((row for row in registry(store)["suites"] if row["id"] == suite_id), None)
    if entry is None:
        raise KeyError(suite_id)
    path = Path(entry["manifest_path"])
    if file_hash(path) != entry["manifest_sha256"]:
        raise ValueError("验证清单已改变，停止显示旧评测结论")
    return read_json(path), path.parent


def matching_runs(store, manifest):
    from .safety_audit import validate_policy
    jobs, reasons, configurations = {}, [], set()
    registered = next((row for row in registry(store)["suites"] if row["id"] == manifest["id"]), {})
    fixed_root = Path(registered["manifest_path"]).parent if registered.get("manifest_path") else None
    for item in manifest["cases"]:
        cid = item["case_id"]
        case, source, _ = store._material(cid)
        try:
            if fixed_root is None:
                raise ValueError("missing registered source directory")
            fixed_source_path = inside(fixed_root, item["source_path"])
            if file_hash(fixed_source_path) != item["source_sha256"] or digest(read_json(fixed_source_path)) != digest(source):
                reasons.append(cid + ": 当前轨迹不再等于预先固定的验证输入")
            fixed_policy_path = inside(fixed_root, item["policy_path"])
            pinned_policy = registered.get("case_policy_sha256", {}).get(cid)
            if not pinned_policy or file_hash(fixed_policy_path) != pinned_policy:
                reasons.append(cid + ": 固定策略摘要缺失或策略文件已经改变")
            elif validate_policy(read_json(fixed_policy_path)) != case["policy"]:
                reasons.append(cid + ": 当前策略不再等于固定验证策略")
        except (OSError, ValueError, TypeError, KeyError):
            reasons.append(cid + ": 无法核对固定输入或策略来源")
        job = store.latest_diagnosis(cid)
        if not job or job.get("status") not in TERMINAL:
            reasons.append(cid + ": 诊断尚未结束")
            continue
        if job.get("source_sha256") != digest(source):
            reasons.append(cid + ": 诊断输入与当前轨迹不一致")
        if job.get("created_at", "") < manifest["created_at"]:
            reasons.append(cid + ": 诊断早于标准答案固定时间")
        folder = store.diagnoses().folder / job["id"]
        config_path, context_path = folder / "config.json", folder / "context.json"
        if not config_path.is_file() or not context_path.is_file():
            reasons.append(cid + ": 缺少真实诊断配置或上下文快照")
        else:
            configurations.add(digest(read_json(config_path)))
            if digest(read_json(context_path).get("policy", {})) != digest(case["policy"]):
                reasons.append(cid + ": 诊断使用的策略与当前策略不一致")
        jobs[cid] = job
    if len(configurations) != 1:
        reasons.append("本组尚未使用同一配置完成所有对照")
    return jobs, reasons


def _mentions(text, target):
    import ntpath
    name = ntpath.basename(target.replace("/", "\\")).casefold()
    return bool(name) and name in text.casefold()


def compare_case(expected, analysis, job):
    issues = analysis["issues"]
    loc = expected["injected_location"]
    call = loc["call_id"]
    rules = [row for row in issues if row["category"] == "rule" and row["state"] == "confirmed"]
    risks = [row for row in issues if row["category"] == "safety" and row["state"] == "violation_attempt"]
    model = [row for row in issues if row["category"] == "model"]
    confirmed = [row for row in model if row["state"] == "confirmed"]
    matched = [row for row in confirmed if call in row.get("call_ids", [])]
    others = [row["id"] for row in confirmed if call not in row.get("call_ids", [])]
    result = job.get("result") or {}
    full = job.get("status") == "succeeded" and job.get("full_flow_completed") is True
    task_rules = [row for row in rules if row["rule_id"] == "OC-TASK-001"]
    expected_risks = expected.get("expected_safety_rules", [])
    risk_hits = [row for row in risks if row["rule_id"] in expected_risks and call in row["call_ids"]]
    row = {"case_id": expected["case_id"], "source_category": expected["source_category"],
        "constructed": expected["constructed"], "expected_location": loc,
        "diagnosis_job_id": job["id"], "diagnosis_state": job["status"], "model_status": result.get("status"),
        "full_flow_completed": full, "rules": {"findings": rules, "task_target_detected": bool(task_rules),
            "exact_call_match": any(call in issue["call_ids"] for issue in task_rules)},
        "safety": {"findings": risks, "expected_rules": expected_risks,
            "expected_rules_detected": all(any(issue["rule_id"] == rule for issue in risk_hits) for rule in expected_risks),
            "unexpected_rule_ids": sorted({issue["rule_id"] for issue in risks} - set(expected_risks))},
        "model_findings": model, "root_candidates": analysis["root_candidates"],
        "actual_locations": [{"call_ids": issue["call_ids"], "import_steps": issue["import_steps"],
                               "fact_ids": issue["fact_ids"], "state": issue["state"]} for issue in model],
        "false_attributions": others,
        "false_attribution_scope": "不同调用上的确定错误声明；额外问题不自动认定为虚假，保留逐条核对。",
        "overclaim": any(issue["state"] == "confirmed" and (not issue["evidence"] or issue["missing"]) for issue in model),
        "evaluation_limitations": ["类型判定采用保守的词项与来源核对，无法机械确认时保留未验证。",
                                   "精确定位按同一案例内 trace/span 对照，不使用相邻步骤容差。",
                                   "模型选中候选仍不等于证明唯一根因。"]}
    if not expected["constructed"]:
        outcome = analysis.get("task_outcome") or {}
        row.update(detection=None, exact_call_match=None, issue_type_match=None,
            evidence_cited=not confirmed, outcome="对照未完成" if not full else "对照有待核对的错误声明" if confirmed else "对照未发现已支持的错误",
            explanation="对照仅核对已记录的读取目标与返回；不把原始轨迹预设为在所有方面无错。",
            observed_task_outcome=outcome.get("status"), visible_control_error_claims=len(confirmed))
        return row
    target = expected["semantic_changes"][0]["after"]
    # Type correctness requires a task-target classification AND both target
    # names in the saved explanation. A nearby step or generic error is not a hit.
    type_hits = [issue for issue in matched
        if issue.get("fault_class") in {"parameter_error", "invalid_action", "misalignment"}
        and _mentions(" ".join(str(issue.get(k, "")) for k in ("explanation", "proposition")), expected["original_task_requirement"])
        and _mentions(" ".join(str(issue.get(k, "")) for k in ("explanation", "proposition")), target)]
    cited = [issue for issue in matched if issue.get("evidence") and issue.get("output_quote")
             and any(e.get("module") == "action" and e.get("step") == loc["import_action_step"] for e in issue["evidence"])]
    exact, typed, evidence = bool(matched), bool(type_hits), bool(cited)
    if not full or result.get("status") == "uncertain":
        verdict = "不确定"
    elif not confirmed:
        verdict = "漏检"
    elif not exact:
        verdict = "错位"
    elif not typed or not evidence or others or row["overclaim"]:
        verdict = "位置命中，解释待核对"
    else:
        verdict = "命中"
    row.update(detection=bool(confirmed), exact_call_match=exact, issue_type_match=typed, evidence_cited=evidence,
        outcome=verdict, explanation="按固定的目标替换故障核对具体调用、任务/实际目标及原文证据；安全与任务规则单列。")
    return row


def reference_view(store, suite_id):
    manifest, root = loaded_suite(store, suite_id)
    jobs, reasons = matching_runs(store, manifest)
    if reasons:
        raise ValueError("暂不开放标准答案：" + "；".join(reasons))
    answer_path, diff_path = inside(root, manifest["private_answers"]), inside(root, manifest["private_diffs"])
    if file_hash(answer_path) != manifest["ground_truth_sha256"] or file_hash(diff_path) != manifest["diffs_sha256"]:
        raise ValueError("固定答案或差异已改变，拒绝将旧结果计为命中")
    truth = read_json(answer_path)
    comparisons = [compare_case(row, store.analysis(row["case_id"]), jobs[row["case_id"]]) for row in truth["cases"]]
    variants = [row for row in comparisons if row["constructed"]]
    comparison = {"schema": "prodebugger-localization-evaluation/v1", "suite_id": suite_id,
        "ground_truth_sha256": manifest["ground_truth_sha256"], "cases": comparisons,
        "summary": {"variant_count": len(variants), "precise_hits": sum(row["outcome"] == "命中" for row in variants),
                    "outcomes": dict(Counter(row["outcome"] for row in variants))},
        "evaluation_code_sha256": file_hash(__file__), "development_acceptance_examples": True,
        "scope": "本组小规模开发验收，不是泛化准确率估计；标准答案不进入诊断输入。"}
    return {"ground_truth": truth, "diff": read_json(diff_path), "comparison": comparison,
            "limitations": manifest["limitations"]}


def suites_view(store):
    suites = []
    for entry in registry(store)["suites"]:
        manifest, _ = loaded_suite(store, entry["id"])
        jobs, reasons = matching_runs(store, manifest)
        public = {key: manifest[key] for key in ("id", "title", "description", "source", "limitations")}
        public["cases"] = [{key: item[key] for key in ("id", "case_id", "label", "source_category", "is_constructed")}
                           for item in manifest["cases"]]
        for item in public["cases"]:
            item["diagnosis"] = jobs.get(item["case_id"]) or store.latest_diagnosis(item["case_id"])
        public.update(reference_available=not reasons, reference_gate_reasons=reasons,
                      ground_truth_fixed_at=manifest["created_at"], ground_truth_sha256=manifest["ground_truth_sha256"])
        if not reasons:
            public["comparison"] = reference_view(store, manifest["id"])["comparison"]
        suites.append(public)
    return {"schema": "prodebugger-validation-view/v1", "suites": suites}
