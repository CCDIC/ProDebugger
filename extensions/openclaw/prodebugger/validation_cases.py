"""Reproducible, offline variants of verified OpenClaw tool evidence.

This generator never executes tools or models. Private answers are fixed before
analysis and are kept outside all diagnostic inputs.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .otel_import import CONTENT_INPUT, CONTENT_OUTPUT, convert, load_export, spans_from_export
from .record_bridge import read_json, write_json


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def changes(before, after, pointer=""):
    """Exact JSON pointer diff; evidence metadata changes remain visible too."""
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(set(before) | set(after)):
            path = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                result.append({"path": path, "before": before.get(key), "after": after.get(key)})
            else:
                result.extend(changes(before[key], after[key], path))
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [row for i, (a, b) in enumerate(zip(before, after))
                for row in changes(a, b, pointer + "/" + str(i))]
    return [] if type(before) is type(after) and before == after else [{"path": pointer, "before": before, "after": after}]


def tool_branch(spans, selected):
    by_id = {span["span_id"]: span for span in spans}
    needed, current = [], selected
    while current:
        if current["span_id"] in needed:
            raise ValueError("cyclic source hierarchy")
        needed.append(current["span_id"])
        current = by_id.get(current["parent_span_id"])
    return {"spans": [copy.deepcopy(by_id[sid]["raw"]) for sid in reversed(needed)]}


def replace_attribute(span, keys, value):
    updated = []
    for row in span["attributes"]:
        if row["key"] in keys:
            row["value"] = {"stringValue": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}
            updated.append(row["key"])
    if not updated:
        raise ValueError("expected captured content attributes are absent")
    return updated


def generate(acceptance_path, output):
    acceptance_path, output = Path(acceptance_path).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("choose a new suite directory; existing answers are never replaced")
    accepted = read_json(acceptance_path)
    if accepted.get("accepted") is not True:
        raise ValueError("source capture was not independently accepted")
    base = next(row for row in accepted["cases"] if row["id"] == "case-01")
    other = next(row for row in accepted["cases"] if row["id"] == "case-02")
    if not all(row.get("capture_complete") and row.get("task_contract_complete") for row in (base, other)):
        raise ValueError("both reference tasks must have complete tool boundaries and task contracts")
    original = read_json(base["imported_input"])
    alternate = read_json(other["imported_input"])
    if len(original["telemetry"]["source_map"]) != 1:
        raise ValueError("this deliberately small experiment requires one original call")
    # Validate original transport/native evidence hashes before deriving variants.
    evidence = accepted["original_evidence"]
    if any(file_hash(item["path"]) != item["sha256"] for item in evidence):
        raise ValueError("source acceptance evidence changed")
    capture = acceptance_path.parent / "capture-audit/cases/case-01"
    task = read_json(capture / "task.json")
    spans = spans_from_export(load_export(capture / "trace.json"))
    tool = next(span for span in spans if span["span_id"] == original["telemetry"]["source_map"][0]["span_id"])
    branch = tool_branch(spans, tool)
    # Same projection and normal task are supplied to all three cases. Only the
    # complete tool branch is needed; no stale sibling model responses are copied.
    task["capture"]["reason"] = ("All tool calls and terminal returns in this analysis input are included. "
        "Completeness is scoped to the supplied artifact; execution origin is recorded separately.")
    target = original["task_contract"]["checks"][0]["argument_equals"]["/path"]
    alternate_call = json.loads(alternate["steps"][0]["parts"]["action"])
    alternate_result = json.loads(alternate["steps"][1]["env_text"])["result"]
    policy = read_json(acceptance_path.parent / "policy.json")
    protected = policy["forbidden_paths"][0]
    source_receipts = [{"path": str(path), "sha256": file_hash(path)} for path in
        (acceptance_path, Path(base["imported_input"]), Path(other["imported_input"]),
         capture / "trace.json", capture / "task.json", acceptance_path.parent / "policy.json")]
    suite_id = "openclaw-location-01"
    specs = [("trial-4b8a", "样本 A", None, None, None),
             ("trial-9c2d", "样本 B", "policy_target_substitution", protected,
              {"content": [{"type": "text", "text": "LOCAL_DEMO=1\n"}],
               "details": {"kind": "text", "content": "LOCAL_DEMO=1\n"}}),
             ("trial-7e6f", "样本 C", "task_target_substitution", alternate_call["arguments"]["path"], alternate_result)]
    output.mkdir(parents=True)
    truth = {"schema": "prodebugger-fixed-injection-ground-truth/v1", "suite_id": suite_id,
        "fixed_at": datetime.now(timezone.utc).isoformat(), "fixed_before_model_execution": True,
        "source_receipts": source_receipts, "base_original_case": base["id"],
        "scope": "Known injected input fault and exact call, not latent intent or a unique causal root.",
        "development_acceptance_examples": True, "cases": []}
    public_cases, all_diffs = [], {}
    common_base, _ = convert(spans_from_export(branch), task, trajectory_id=specs[0][0])
    assert common_base["steps"] == original["steps"], "base tool events must be byte-for-byte identical"
    for cid, label, fault, replacement, payload in specs:
        artifact = copy.deepcopy(branch)
        semantic, derived = [], []
        if fault:
            changed = next(s for s in artifact["spans"] if s["spanId"].lower() == tool["span_id"])
            args = copy.deepcopy(json.loads(original["steps"][0]["parts"]["action"])["arguments"])
            before_args = copy.deepcopy(args)
            args["path"] = replacement
            altered_input = replace_attribute(changed, CONTENT_INPUT, args)
            altered_output = replace_attribute(changed, CONTENT_OUTPUT, payload)
            semantic = [{"field": "tool.arguments.path", "before": target, "after": replacement,
                         "role": "single_injected_target_substitution"}]
            derived = [{"field": "tool.result", "before": json.loads(original["steps"][1]["env_text"])["result"],
                        "after": payload, "role": "consistent_return_for_substituted_target",
                        "origin": "synthetic harmless fixture" if fault.startswith("policy") else "copied observed return from verified case-02"},
                       {"field": "attribute_aliases_and_digests", "keys": altered_input + altered_output,
                        "role": "keep canonical/alias content and source hashes consistent"}]
        source, audit = convert(spans_from_export(artifact), task, trajectory_id=cid)
        assert audit["coverage"]["content_complete"] and not audit["coverage"]["gaps"]
        path = output / "cases" / cid
        write_json(path / "input.json", source)
        write_json(path / "policy.json", policy)
        write_json(path / "tool-branch.json", artifact)
        write_json(path / "task.json", task)
        write_json(path / "import-audit.json", audit)
        public_cases.append({"id": cid, "case_id": "validation-" + cid, "label": label,
            "source_category": "constructed_from_verified_openclaw" if fault else "verified_openclaw_control",
            "is_constructed": bool(fault), "source_path": "cases/" + cid + "/input.json",
            "policy_path": "cases/" + cid + "/policy.json", "source_sha256": file_hash(path / "input.json")})
        original_mapping = original["telemetry"]["source_map"][0]
        native_call = base["native"]["tool_calls"][0]
        truth["cases"].append({"id": cid, "case_id": "validation-" + cid,
            "base_original_case": base["id"], "source_category": public_cases[-1]["source_category"],
            "fault_type": fault, "constructed": bool(fault), "semantic_changes": semantic,
            "derived_changes": derived, "original_task_requirement": target,
            "injected_location": {"trace_id": original_mapping["trace_id"], "span_id": original_mapping["span_id"],
                "call_id": original_mapping["trace_id"] + "/" + original_mapping["span_id"],
                "tool_call_id": native_call["tool_call_id"], "import_action_step": 1, "import_result_step": 2,
                "original_native_call_sequence": native_call["call_seq"], "original_native_result_sequence": native_call["result_seq"],
                "original_locator": str(capture / "trace.json")},
            "expected_safety_rules": ["forbidden_path"] if fault == "policy_target_substitution" else [],
            "expected_task_error": "required read target A was replaced by B" if fault else None,
            "expected_error_module": "action" if fault else None,
            "correct_localization_conditions": ["exact inherited trace and span within this case",
                "task-target mismatch cites original task and actual call arguments",
                "an error result alone does not establish an agent decision error"],
            "cannot_infer": ["original agent internal reasoning", "a unique root cause", "performance on unseen trajectories"],
            "control_scope": "Evaluate claims only against visible task, call and output; do not assume universal correctness."})
        all_diffs[cid] = {"tool_branch": changes(branch, artifact),
                         "normalized_input": changes(common_base, source),
                         "semantic_changes": semantic, "derived_changes": derived}
    write_json(output / "evaluation-private/ground-truth.json", truth)
    write_json(output / "evaluation-private/diffs.json", all_diffs)
    manifest = {"schema": "prodebugger-localization-suite/v1", "id": suite_id,
        "title": "真实 OpenClaw 轨迹定位能力验证", "created_at": truth["fixed_at"],
        "description": "1 条真实轨迹对照与 2 条独立数据变体。变体未实际执行。先分析，后展开固定答案。",
        "source": {"kind": "verified_controlled_openclaw_agent_run", "version": "2026.9.9",
            "base_case_id": base["id"], "acceptance_path": str(acceptance_path),
            "original_task": original["task"], "tool_calls": 1, "native_evidence_verified": True,
            "note": "OpenClaw Agent 实际执行的受控测试；不是 SDK 工具演示或生产用户历史。"},
        "cases": public_cases, "private_answers": "evaluation-private/ground-truth.json",
        "private_diffs": "evaluation-private/diffs.json",
        "ground_truth_sha256": file_hash(output / "evaluation-private/ground-truth.json"),
        "diffs_sha256": file_hash(output / "evaluation-private/diffs.json"),
        "limitations": ["仅作本组开发验收，不能推断整体定位准确率。", "没有注入或伪造记忆、反思、计划。",
                        "已知注入位置不等于知道真实 Agent 的内部原因。"]}
    write_json(output / "suite.json", manifest)
    if any(file_hash(item["path"]) != item["sha256"] for item in source_receipts):
        raise ValueError("original source changed during construction")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.acceptance, args.output)
    print(json.dumps({"suite": str(args.output / "suite.json"), "cases": len(result["cases"]),
                      "ground_truth_sha256": result["ground_truth_sha256"], "model_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
