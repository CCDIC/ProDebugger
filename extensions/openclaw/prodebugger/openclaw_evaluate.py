"""Evaluate captured OpenClaw OTLP/SigNoz exports without scripted diagnoses.

Default: import, compile, probe provenance, and evaluate the explicit task contract.
Only --online sends source data to a model. Independent labels stay in reports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .debug_tools import DebugTools
from .debugger_agent import DebuggerAgent
from .llm_client import LLMClient, json_lines
from .openclaw_contract import evaluate
from .openclaw_demo import baseline, matches
from .otel_import import convert, inspect, load_export, load_task, save_import, spans_from_export
from .query_dsl import execute
from .record_bridge import ROOT, build_bundle, export_bundle, source_versions, valid_id, write_json
from .run_experiment import DEFAULTS

SCHEMA = "openclaw-evaluation/v1"
STATUSES = {"diagnosed", "no_failure", "uncertain", "not_importable"}


def load_manifest(path):
    manifest = load_export(path)  # The same strict JSON decoder as raw imports.
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ValueError("manifest requires schema " + SCHEMA)
    if manifest.get("data_origin") not in {"real", "controlled", "真实"}:
        raise ValueError("data_origin must be real or controlled")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest requires a nonempty cases array")
    seen = set()
    for number, case in enumerate(cases):
        location = f"cases[{number}]"
        if not isinstance(case, dict):
            raise ValueError(location + " must be an object")
        for field in ("id", "export", "task_file"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError(location + "." + field + " must be nonempty text")
        valid_id(case["id"])
        if case["id"] in seen:
            raise ValueError("duplicate case id: " + case["id"])
        seen.add(case["id"])
        if "expected_status" not in case or (case["expected_status"] is not None and case["expected_status"] not in STATUSES):
            raise ValueError(location + ".expected_status must be null or a supported status")
        roots, module = case.get("expected_root_steps", []), case.get("expected_module")
        if not isinstance(roots, list) or any(type(t) is not int or t < 1 for t in roots) or len(roots) != len(set(roots)):
            raise ValueError(location + ".expected_root_steps must be unique positive integers")
        if case["expected_status"] == "diagnosed":
            if not roots or module != "action":
                raise ValueError(location + " diagnosed labels require root steps and expected_module=action")
        elif roots or module is not None:
            raise ValueError(location + " non-diagnosed/unlabeled cases require empty root steps and null module")
        trace = case.get("trace_id")
        if trace is not None and (not isinstance(trace, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", trace)):
            raise ValueError(location + ".trace_id must be a 32-digit hex ID")
    return manifest


def probe(bundle):
    """Verify the imported call/result mapping without assuming a task/tool/path."""
    tools = DebugTools(bundle)
    action = next(f for f in bundle.record.F if f.slot == "act")
    span_id = bundle.source["telemetry"]["source_map"][0]["span_id"]
    queries = {
        "actions": "SELECT fid,step,rel,args FROM TRACK_ACTION('tool_call') ORDER BY step",
        "feedback": f"SELECT fid,step,rel,args FROM TRACE_FACT({action.fid},'forward',1)",
        "source": "SELECT step,module,text FROM SEARCH_TEXT('" + span_id + "',('action','observation')) ORDER BY step,module",
    }
    results = {name: execute(tools, sql) for name, sql in queries.items()}
    issues = [name + ": " + str(result["error"]) for name, result in results.items() if "error" in result]
    if not issues:
        if len(results["actions"]["rows"]) != len(bundle.source["telemetry"]["source_map"]):
            issues.append("SQL action count differs from imported calls")
        if not any(r["rel"] in {"tool_result", "tool_error", "tool_status"} and r["step"] == action.t + 1
                   for r in results["feedback"]["rows"]):
            issues.append("captured feedback provenance does not point to its invocation")
        if not any(r["step"] == action.t and r["module"] == "action" for r in results["source"]["rows"]):
            issues.append("original invocation is not searchable by its recorded span ID")
    return {"passed": not issues, "issues": issues, "queries": results}


def snapshot_file(path, destination, folder):
    data = path.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return {"path": str(path), "stored_path": destination.relative_to(folder).as_posix(),
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def usage_from(folder):
    rows = json_lines(folder / "usage.jsonl")
    return rows[-1].get("totals", {}) if rows else {}


def online_case(bundle, folder, cfg):
    out = folder / "online" / bundle.source["trajectory_id"]
    client = None
    try:
        export_bundle(bundle, out)
        client = LLMClient(cfg, out)
        structured = DebuggerAgent(bundle, client, out, cfg).run()
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
        structured = {"status": "error", "root": None, "error": str(exc),
                      "usage": client.snapshot() if client else usage_from(out)}
        write_json(out / "result.json", structured)
    out = folder / "baseline" / bundle.source["trajectory_id"]
    try:
        direct = baseline(bundle.source, out, cfg)
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
        direct = {"status": "error", "root": None, "error": str(exc), "usage": usage_from(out)}
        write_json(out / "result.json", direct)
    return {"structured": structured, "baseline": direct}


def evaluate_case(case, ordinal, manifest_path, folder, cfg, online):
    # A manifest supplies input locations and independent labels, never computed
    # results. Copying arbitrary case keys would let old structured/baseline
    # results survive a failed import and enter this run's accuracy/usage totals.
    fields = ("id", "export", "task_file", "trace_id", "expected_status", "expected_root_steps", "expected_module")
    row = {field: case[field] for field in fields if field in case}
    row.update(labeled=case["expected_status"] is not None, offline_status="error", inputs={})
    raw = folder / "raw" / case["id"]
    stage = "load_export"
    try:
        for field, filename in (("export", "export" + Path(case["export"]).suffix), ("task_file", "task.json")):
            stage = field
            path = (manifest_path.parent / case[field]).resolve()
            row["inputs"][field] = snapshot_file(path, raw / filename, folder)
        stage = "decode_export"
        # Read the archived snapshot so its digest identifies exactly what was used.
        spans = spans_from_export(load_export(folder / row["inputs"]["export"]["stored_path"]))
        stage = "load_task"
        spec = load_task(folder / row["inputs"]["task_file"]["stored_path"])
        stage = "inspect"
        audit = inspect(spans, spec, case.get("trace_id"))
        row["import_ready"], row["import_audit"] = audit["ready"], audit
        write_json(raw / "import_audit.json", audit)
        if not audit["ready"]:
            row.update(offline_status="not_importable", failure={"stage": stage, "reasons": audit["issues"]})
            return row
        stage = "convert"
        # Neither case labels nor descriptive case IDs become diagnostic source.
        source, audit = convert(spans, spec, case.get("trace_id"), f"openclaw_eval_{ordinal:03d}")
        row.update(trajectory_id=source["trajectory_id"], telemetry=source["telemetry"], task_outcome=evaluate(source))
        save_import(source, audit, spans, folder / "imported")
        write_json(raw / "task_outcome.json", row["task_outcome"])
        stage = "compile"
        bundle = build_bundle(source, online=False)
        stage = "probe"
        result = probe(bundle)
        row["probe"] = {k: result[k] for k in ("passed", "issues")}
        write_json(folder / "probes" / (source["trajectory_id"] + ".json"), result)
        if not result["passed"]:
            row["failure"] = {"stage": stage, "reasons": result["issues"]}
            return row
        row["offline_status"] = "imported"
        if online:
            row.update(online_case(bundle, folder, cfg))
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RuntimeError, OSError) as exc:
        row["failure"] = {"stage": stage, "type": type(exc).__name__, "reasons": [str(exc)]}
    return row


def metrics(cases, online):
    labeled = [row for row in cases if row["expected_status"] in {"diagnosed", "no_failure", "uncertain"}]
    counts = {"cases": len(cases), "labeled_diagnostic_cases": len(labeled),
              "unlabeled_cases": sum(row["expected_status"] is None for row in cases),
              "imported_cases": sum(row["offline_status"] == "imported" for row in cases), "methods": {}}
    for row in cases:
        expected = row["expected_status"]
        row["import_matches_expected"] = (row["offline_status"] == "not_importable") if expected == "not_importable" else None
        for method in ("structured", "baseline"):
            if method in row:
                row[method]["matches_expected"] = matches(row[method], row) if expected in STATUSES - {"not_importable"} else None
    if online:
        for method in ("structured", "baseline"):
            totals = {key: sum(row.get(method, {}).get("usage", {}).get(key, 0) or 0 for row in cases)
                      for key in ("model_calls", "input_tokens", "output_tokens", "unknown_usage_calls")}
            correct = sum(row.get(method, {}).get("matches_expected") is True for row in labeled)
            counts["methods"][method] = {"labeled_cases": len(labeled), "matches": correct,
                "accuracy": correct / len(labeled) if labeled else None,
                "completed_cases": sum(method in row for row in cases), "usage": totals}
    return counts


def report(folder, summary):
    lines = ["# OpenClaw 导出评估", "", f"数据来源声明：`{summary['data_origin']}`。",
        "离线结果仅包含原始导出检查、导入、SQL 来源验证和显式任务契约；未生成诊断，也未使用预写回放。",
        "未标注案例保留为 unlabeled，不计算准确率。任务契约状态不等于人工根因标签。", "",
        "| 案例 | 标签 | 离线 | 契约 | ProDebugger | 工具轨迹基线 |", "|---|---|---|---|---|---|"]
    for row in summary["cases"]:
        cells = [row["id"], row["expected_status"] or "unlabeled", row["offline_status"],
                 row.get("task_outcome", {}).get("status", "unavailable"),
                 row.get("structured", {}).get("status", "未运行"), row.get("baseline", {}).get("status", "未运行")]
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
    if summary["online"]:
        lines += ["", "两种方法使用相同任务、导入工具轨迹与确定性契约状态。ProDebugger 分阶段检索并有运行时状态校验；基线一次读取导入工具轨迹的全部 source 及同一契约状态，依靠提示词约束。这里的完整输入不包含全部原生系统日志或模型会话日志，也不代表原始采集没有缺口。调用次数、工具和校验不同。",
            "匹配只比较状态及根因 step/module，不包含错误类别。标注案例的导入/运行失败仍计入分母。", "",
            "| 方法 | 标注匹配 | 请求数 | 输入 tokens | 输出 tokens |", "|---|---:|---:|---:|---:|"]
        for name, value in summary["metrics"]["methods"].items():
            usage = value["usage"]
            lines.append(f"| {name} | {value['matches']}/{value['labeled_cases']} | {usage['model_calls']} | {usage['input_tokens']} | {usage['output_tokens']} |")
    for row in summary["cases"]:
        if "failure" in row:
            lines += ["", f"- {row['id']} / {row['failure']['stage']}: " + "; ".join(row["failure"]["reasons"])]
        for method in ("structured", "baseline"):
            result = row.get(method, {})
            if result.get("error") or result.get("stop_reason"):
                lines += [f"- {row['id']} / {method}: " + str(result.get("error") or result["stop_reason"])]
    lines += ["", "raw/ 保留原始文件字节、检查与契约；imported/ 保留规范输入和 span 映射；probes/ 保留真实 SQL 返回；online/ 和 baseline/ 仅在在线模式保存模型请求、结果与用量。",
        "summary.json 保存文件路径、SHA-256、原始 metadata、独立标签、状态/位置比较及各案例失败原因。",
        "覆盖范围是显式工具结果契约；缺失的思维过程、任意终答质量、跨 trace 会话和并发调用不能据此确认。"]
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--manifest", type=Path, required=True)
    command.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "openclaw_evaluation")
    command.add_argument("--run-id")
    command.add_argument("--online", action="store_true")
    command.add_argument("--model")
    command.add_argument("--base-url")
    command.add_argument("--api-key-env", default="OPENAI_API_KEY")
    command.add_argument("--env-file", type=Path)
    command.add_argument("--reasoning-effort", default="none", choices=["none", "low", "high", "max"])
    return command


def run(args):
    if args.online and not args.model:
        raise ValueError("--online requires --model and configured credentials")
    path = args.manifest.resolve()
    manifest = load_manifest(path)
    run_id = valid_id(args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    folder = args.output_dir.resolve() / run_id
    folder.mkdir(parents=True, exist_ok=False)
    cfg = {**DEFAULTS, "model": args.model, "base_url": args.base_url, "api_key_env": args.api_key_env,
        "env_file": str(args.env_file.resolve()) if args.env_file else None,
        "reasoning_effort": args.reasoning_effort, "max_turns": 16, "max_tool_calls": 35,
        "max_input_tokens": 400000, "max_output_tokens": 24000, "max_tokens": 3000,
        "max_parse_calls": 1, "trace_top_p": 2, "timeout": 60, "retries": 0}
    summary = {"schema": "openclaw-evaluation-result/v1", "data_origin": manifest["data_origin"],
        "online": args.online, "run_id": run_id, "model": args.model, "config": cfg,
        "source_versions": source_versions(), "manifest": manifest, "cases": []}
    summary["manifest_input"] = snapshot_file(path, folder / "manifest.json", folder)
    for ordinal, case in enumerate(manifest["cases"], 1):
        row = evaluate_case(case, ordinal, path, folder, cfg, args.online)
        summary["cases"].append(row)
        summary["metrics"] = metrics(summary["cases"], args.online)
        write_json(folder / "summary.json", summary)
        report(folder, summary)
        print(case["id"] + ": offline=" + row["offline_status"], flush=True)
    print("Report: " + str(folder / "report.md"), flush=True)
    if any(row["import_matches_expected"] is False or
           (row["offline_status"] != "imported" and row["import_matches_expected"] is not True) for row in summary["cases"]):
        return 1
    if args.online and any(row.get(method, {}).get("status") == "error" or row.get(method, {}).get("matches_expected") is False
                           for row in summary["cases"] for method in ("structured", "baseline")):
        return 2
    return 0


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    try:
        return run(args)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        command.exit(2, f"Evaluation failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
