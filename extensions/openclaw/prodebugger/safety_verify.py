"""Read-only acceptance of the active controlled safety capture and reports.

Re-runs only policy analysis and SOURCE SQL queries. No model calls, tool
execution, HTTP requests, recapture, or rewriting of existing evidence occurs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from . import otel_import
from .otel_receiver import decode_trace_request
from .record_bridge import digest, read_json
from .safety_audit import audit_source


def _inside(root, value, base=None):
    path = Path(value)
    resolved = (path if path.is_absolute() else (base or root) / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Evidence path is outside the selected data directory")
    return resolved


def _query_slices(source, report):
    steps = {row["t"]: row for row in source["steps"]}
    checked = 0
    for query in report["queries"]:
        for row in query["result"].get("rows", []):
            step = steps[row["step"]]
            if row["module"] == "observation":
                text = step["env_text"]
            elif row["module"] == "action":
                text = step["parts"]["action"]
            else:
                return False, checked
            start, end = row["start"], row["end"]
            if not (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                    and text[start:end] == row["text"]):
                return False, checked
            checked += 1
    return True, checked


def verify(data_dir):
    root = Path(data_dir).resolve()
    index = read_json(root / "index.json")
    run_id = index.get("active_run")
    if not run_id or Path(run_id).name != run_id:
        raise ValueError("No valid active controlled capture is selected")
    run = _inside(root, Path("captures") / run_id)
    manifest = read_json(run / "capture-manifest.json")
    acceptance = read_json(run / "safety-acceptance.json")
    if manifest.get("data_origin") != "controlled_tool_runtime":
        raise ValueError("This acceptance script requires a controlled demonstration capture")
    capture = _inside(root, manifest["capture_path"], run)
    runtime = read_json(_inside(root, manifest["runtime_receipt_path"], run))
    report = {"schema": "prodebugger-safety-verification/v1", "active_run": run_id,
              "read_only": True, "model_calls": 0, "tool_executions": 0,
              "passed": False, "checks": {}, "cases": [], "historical": [],
              "note": "Expected verdicts are used only after audit_source returns; this is acceptance, not an accuracy benchmark."}
    checks = report["checks"]
    checks["capture_sha256_matches"] = hashlib.sha256(capture.read_bytes()).hexdigest() == manifest["capture_sha256"]
    checks["runtime_export_ack"] = runtime.get("export_code") == 0
    checks["runtime_sdk_shutdown_ack"] = runtime.get("sdk_shutdown_ack") is True and manifest.get("sdk_shutdown_ack") is True
    checks["controlled_origin_is_explicit"] = (manifest.get("not_openclaw_agent_run") is True
        and runtime.get("not_openclaw_agent_run") is True and manifest.get("model_calls") == runtime.get("model_calls") == 0)
    fixtures = []
    for name, expected_hash in manifest["fixture_sha256"].items():
        path = _inside(root, name, run)
        fixtures.append({"path": name, "sha256_matches": hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash})
    report["fixtures"] = fixtures
    checks["fixture_hashes_match"] = bool(fixtures) and all(row["sha256_matches"] for row in fixtures)
    documents = otel_import.load_export(capture)
    requests = [json.loads(line) for line in (capture.parent / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    decoded = [decode_trace_request(_inside(root, row["protobuf_file"], capture.parent).read_bytes()) for row in requests]
    stored_json = [read_json(_inside(root, row["json_file"], capture.parent)) for row in requests]
    checks["raw_protobuf_matches_json_capture"] = decoded == stored_json == documents
    spans = otel_import.spans_from_export(documents)
    expected_ids = {(row["trace_id"], row["span_id"]) for row in runtime["cases"]}
    checks["all_eight_executed_spans_arrived"] = (len(spans) == len(expected_ids) == len(manifest["cases"]) == 8
        and {(row["trace_id"], row["span_id"]) for row in spans} == expected_ids)
    accepted = {row["id"]: row for row in acceptance["cases"]}
    indexed = {row["id"]: row for row in index["cases"]}
    for item in manifest["cases"]:
        if Path(item["id"]).name != item["id"]:
            raise ValueError("Invalid case ID")
        folder = _inside(root, Path("audited") / item["id"], run)
        source, policy, stored = (read_json(folder / name) for name in ("input.json", "policy.json", "report.json"))
        # Expected labels are deliberately unavailable to the engine call.
        current = audit_source(source, policy)
        exact, rows_checked = _query_slices(source, current)
        stored_exact, _ = _query_slices(source, stored)
        case_id = run_id + "-" + item["id"]
        accepted_row = accepted.get(case_id, {})
        selected = [span["raw"] for span in spans if span["trace_id"] == item["trace_id"]]
        imported = read_json(_inside(root, item["source_path"], run))
        row = {"id": item["id"], "current_result": current["status"], "stored_result": stored["status"],
               "report_match": current == stored, "expected_result": item["expected_verdict"],
               "expected_matches": current["status"] == item["expected_verdict"],
               "flag_count": len(current["flags"]), "query_rows_checked": rows_checked,
               "query_slices_match": exact and stored_exact, "imported_source_matches": source == imported,
               "raw_input_digest_matches": source["telemetry"]["input_digest"] == digest(selected),
               "indexed": case_id in indexed,
               "acceptance_matches": (accepted_row.get("status") == current["status"]
                   and accepted_row.get("expected_verdict") == item["expected_verdict"]
                   and accepted_row.get("flags") == len(current["flags"]))}
        row["passed"] = all(row[key] for key in ("report_match", "expected_matches", "query_slices_match",
            "imported_source_matches", "raw_input_digest_matches", "indexed", "acceptance_matches"))
        report["cases"].append(row)
    counts = Counter(row["current_result"] for row in report["cases"])
    report["counts"] = {status: counts[status] for status in ("unsafe", "safe", "unknown")}
    checks["controlled_counts_4_3_1"] = report["counts"] == {"unsafe": 4, "safe": 3, "unknown": 1}
    checks["acceptance_receipt_matches_run"] = (acceptance.get("run_id") == run_id
        and acceptance.get("passed") is True and len(acceptance["cases"]) == 8)
    # Only the six seeded historical examples have a known acceptance target.
    # User imports and other batches are not assumed to have any fixed verdict.
    for case in index["cases"]:
        if case.get("origin") != "historical_openclaw" or case["id"] not in {f"historical-case-{i:02d}" for i in range(1, 7)}:
            continue
        source = read_json(_inside(root, case["source_path"]))
        current = audit_source(source, case["policy"])
        stored = read_json(_inside(root, case["report_path"]))
        exact, rows_checked = _query_slices(source, current)
        history = [read_json(_inside(root, name)) for name in case.get("history", [])]
        row = {"id": case["id"], "current_result": current["status"], "stored_result": stored["status"],
               "flag_count": len(current["flags"]), "query_slices_match": exact, "query_rows_checked": rows_checked,
               "history_count": len(history),
               "history_unknown_without_flags": all(value["status"] == "unknown" and not value["flags"] for value in history)}
        row["passed"] = (current["status"] == stored["status"] == "unknown" and not current["flags"]
            and not stored["flags"] and exact and row["history_unknown_without_flags"])
        report["historical"].append(row)
    report["historical_available"] = len(report["historical"])
    checks["historical_examples_consistent"] = all(row["passed"] for row in report["historical"])
    report["passed"] = all(checks.values()) and len(report["cases"]) == 8 and all(row["passed"] for row in report["cases"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Optional NEW receipt JSON file; existing files are never overwritten")
    args = parser.parse_args(argv)
    if args.output and args.output.exists():
        parser.error("output already exists; choose a new receipt path")
    try:
        receipt = verify(args.data_dir)
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        receipt = {"schema": "prodebugger-safety-verification/v1", "passed": False,
                   "read_only": True, "error": str(exc)}
    text = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(text)
    print(text, end="")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
