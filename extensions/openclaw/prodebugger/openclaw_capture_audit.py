"""Bind native OpenClaw sessions to OTLP by exact tool-call IDs, offline.

Original capture, manifest and transcripts remain unchanged. Transcript content
is comparison evidence only: it never fills missing telemetry or creates spans.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from .otel_import import (CONTENT_INPUT, CONTENT_OUTPUT, _content, _content_conflict,
                          _tool, inspect, load_export, loads, spans_from_export)
from .openclaw_transcript import _message_text
from .openclaw_collect_gateway import audit_gateway_shutdown
from .record_bridge import digest, valid_id, write_json


def evidence(path):
    data = Path(path).read_bytes()
    return {"path": str(Path(path).resolve()), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def select_native_trace(value, trace):
    """Filter only traversal containers; preserve native resource/scope/span data."""
    if isinstance(value, list):
        rows = [item for row in value if (item := select_native_trace(row, trace)) is not None]
        return rows or None
    if not isinstance(value, dict):
        return None
    native_id = value.get("traceId", value.get("trace_id", value.get("traceID")))
    if isinstance(native_id, str):
        return value if native_id.lower() == trace else None
    keys = ("resourceSpans", "resource_spans", "scopeSpans", "scope_spans",
            "instrumentationLibrarySpans", "spans", "data", "result")
    updates = {key: select_native_trace(value[key], trace) for key in keys if key in value}
    if not any(item is not None for item in updates.values()):
        return None
    return {**value, **{key: item if item is not None else [] for key, item in updates.items()}}


def native_session(record, raw_events, case, prompt, manifest_sha256):
    """Validate the exported active branch before interpreting any tool call."""
    if (record.get("schema") != "openclaw-native-transcript/v1" or record.get("otlp") is not False
            or record.get("case_id") != case["id"] or record.get("session_id") != case.get("session_id")
            or not case.get("session_id")):
        raise ValueError("native_session_identity_mismatch")
    if record.get("source", {}).get("manifest_sha256") != manifest_sha256:
        raise ValueError("native_transcript_manifest_hash_mismatch")
    events, active, index = record.get("events"), record.get("active_events"), record.get("active_index")
    if not isinstance(events, list) or not events or not isinstance(active, list) or not active or not isinstance(index, dict):
        raise ValueError("native_active_branch_missing")
    raw_lines = [line for line in raw_events.splitlines() if line.strip()]
    if len(raw_lines) != len(events):
        raise ValueError("native_raw_event_count_mismatch")
    by_seq, event_ids = {}, set()
    for row, raw in zip(events, raw_lines):
        seq = row.get("seq")
        if (type(seq) is not int or seq < 0 or seq in by_seq or (by_seq and seq <= max(by_seq))
                or not isinstance(row.get("event"), dict)):
            raise ValueError("native_duplicate_or_invalid_event_seq")
        if (hashlib.sha256(raw).hexdigest() != row.get("event_sha256")
                or len(raw) != row.get("event_utf8_bytes")
                or digest(loads(raw.decode("utf-8"))) != digest(row["event"])):
            raise ValueError("native_raw_event_integrity_mismatch")
        event_id = row["event"].get("id")
        if not isinstance(event_id, str) or not event_id or event_id in event_ids:
            raise ValueError("native_event_id_duplicate_or_invalid")
        event_ids.add(event_id)
        by_seq[seq] = row
    if (index.get("needs_rebuild") not in (False, 0) or index.get("indexed_seq") != max(by_seq)
            or index.get("active_event_count") != len(active)
            or index.get("active_message_count") != sum(row.get("message_position") is not None for row in active)):
        raise ValueError("native_active_index_stale")
    if ([row.get("active_position") for row in active] != list(range(len(active)))
            or len({row.get("event_seq") for row in active}) != len(active)):
        raise ValueError("native_active_index_ambiguous")
    branch = []
    for row in active:
        event = by_seq.get(row.get("event_seq"))
        if event is None or event.get("active_position") != row["active_position"]:
            raise ValueError("native_active_index_event_mismatch")
        branch.append(event)
    active_seqs = {row["seq"] for row in branch}
    if any(row.get("active_position") is not None for row in events if row["seq"] not in active_seqs):
        raise ValueError("native_inactive_event_annotation_mismatch")
    headers = [row for row in events if row["event"].get("type") == "session"]
    if any(row["event"]["id"] != case["session_id"] for row in headers) or len(headers) > 1:
        raise ValueError("native_session_header_identity_mismatch")
    previous = None
    message_positions = []
    for active_row, row in zip(active, branch):
        event = row["event"]
        first_parent_allowed = (None, headers[0]["event"]["id"]) if headers else (None,)
        if ("parentId" not in event or (previous is None and event["parentId"] not in first_parent_allowed)
                or (previous is not None and event["parentId"] != previous)):
            raise ValueError("native_active_parent_chain_mismatch")
        previous = event["id"]
        if isinstance(event.get("message"), dict):
            message_positions.append(active_row.get("message_position"))
        elif active_row.get("message_position") is not None:
            raise ValueError("native_nonmessage_position_mismatch")
    if message_positions != list(range(len(message_positions))):
        raise ValueError("native_message_positions_mismatch")
    if index.get("leaf_event_id") != branch[-1]["event"].get("id"):
        raise ValueError("native_active_leaf_mismatch")
    gaps = []
    if list(by_seq) != list(range(max(by_seq) + 1)):
        gaps.append("native_event_sequence_incomplete")
    if not headers or headers[0]["seq"] != 0 or headers[0]["seq"] in active_seqs:
        gaps.append("native_fresh_session_header_missing")
    if any(row["seq"] not in active_seqs and row["event"].get("type") != "session" for row in events):
        gaps.append("native_history_has_inactive_branch")
    users = [row for row in branch if isinstance(row["event"].get("message"), dict)
             and row["event"]["message"].get("role") == "user"]
    if len(users) != 1 or _message_text(users[0]["event"]) != prompt:
        raise ValueError("native_original_prompt_not_unique_exact_active_user_message")
    prompt_position = users[0]["active_position"]
    calls, results = {}, {}
    last_message = None
    for row in branch:
        message = row["event"].get("message")
        if not isinstance(message, dict):
            continue
        last_message = message
        role = message.get("role")
        if role == "assistant":
            content = message.get("content", [])
            if not isinstance(content, list):
                raise ValueError("native_assistant_content_shape_unsupported")
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "toolCall":
                    continue
                call_id = item.get("id")
                if (not isinstance(call_id, str) or not call_id or call_id in calls
                        or not isinstance(item.get("name"), str) or not item["name"]
                        or "arguments" not in item or row["active_position"] <= prompt_position):
                    raise ValueError("native_tool_call_duplicate_or_invalid")
                calls[call_id] = {"id": call_id, "name": item["name"], "arguments": item["arguments"],
                                  "call_seq": row["seq"], "call_position": row["active_position"]}
        elif role == "toolResult":
            call_id = message.get("toolCallId")
            if call_id not in calls or call_id in results:
                raise ValueError("native_tool_result_orphan_or_duplicate")
            if (message.get("toolName") != calls[call_id]["name"] or "content" not in message
                    or type(message.get("isError")) is not bool
                    or row["active_position"] <= calls[call_id]["call_position"]):
                raise ValueError("native_tool_result_identity_or_shape_mismatch")
            results[call_id] = {"message": message, "seq": row["seq"]}
    if not calls or set(calls) != set(results):
        raise ValueError("native_tool_boundary_missing_call_or_result")
    closed = (last_message is not None and last_message.get("role") == "assistant"
              and last_message.get("stopReason") == "stop"
              and not any(isinstance(p, dict) and p.get("type") == "toolCall"
                          for p in last_message.get("content", [])))
    for call_id, call in calls.items():
        call.update(result=results[call_id]["message"], result_seq=results[call_id]["seq"])
    return {"calls": list(calls.values()), "prompt_seq": users[0]["seq"], "closed": closed,
            "active_event_seqs": [row["seq"] for row in branch], "gaps": gaps}


def compare_call(call, span):
    """Compare captured payloads; missing content remains a gap, never supplied."""
    attrs, errors, gaps = span["attributes"], [], []
    names = [attrs[key] for key in ("gen_ai.tool.name", "openclaw.toolName") if key in attrs]
    if not names or any(name != call["name"] for name in names):
        errors.append("tool_name_mismatch")
    if _content_conflict(attrs, CONTENT_INPUT) or _content_conflict(attrs, CONTENT_OUTPUT):
        errors.append("conflicting_tool_content_aliases")
    has_input, arguments, _ = _content(attrs, CONTENT_INPUT)
    has_output, output, _ = _content(attrs, CONTENT_OUTPUT)
    if not has_input:
        gaps.append("tool_arguments_not_captured")
    elif digest(arguments) != digest(call["arguments"]):
        errors.append("tool_arguments_mismatch")
    native = call["result"]
    if not has_output:
        gaps.append("tool_result_not_captured")
    elif not isinstance(output, dict) or "content" not in output:
        errors.append("tool_result_shape_mismatch")
    else:
        # Transcript envelope metadata (role, timestamp, __openclaw) is not part
        # of execute()'s result. Its actual content/details must still agree.
        if digest(output["content"]) != digest(native["content"]):
            errors.append("tool_result_content_mismatch")
        if ("details" in output) != ("details" in native) or digest(output.get("details")) != digest(native.get("details")):
            errors.append("tool_result_details_mismatch")
        # OpenClaw's harness can derive the transcript flag from structured
        # details after execute(). Absence in the captured result is not False;
        # an explicit flag must still agree, as must the span outcome below.
        if "isError" in output and (type(output["isError"]) is not bool or output["isError"] != native["isError"]):
            errors.append("tool_result_error_flag_mismatch")
        if any(key not in native or digest(value) != digest(native[key])
               for key, value in output.items() if key not in {"content", "details", "isError"}):
            errors.append("tool_result_extra_field_mismatch")
    error = (span["status"].get("code", 0) in (2, "2", "ERROR", "STATUS_CODE_ERROR")
             or attrs.get("openclaw.outcome") in {"error", "blocked"})
    if error != native["isError"]:
        errors.append("tool_error_outcome_mismatch")
    return {"tool_call_id": call["id"], "span_id": span["span_id"], "call_seq": call["call_seq"],
            "result_seq": call["result_seq"], "errors": errors, "gaps": gaps}


def bind_session(session, spans):
    indexed = defaultdict(list)
    for span in spans:
        if _tool(span) is not None:
            call_id = span["attributes"].get("gen_ai.tool.call.id")
            if isinstance(call_id, str) and call_id:
                indexed[call_id].append(span)
    errors, matches, comparisons = [], [], []
    for call in session["calls"]:
        rows = indexed[call["id"]]
        if len(rows) != 1:
            errors.append("missing_tool_call_span" if not rows else "ambiguous_tool_call_span")
            continue
        matches.append(rows[0])
        comparisons.append(compare_call(call, rows[0]))
    traces = sorted({span["trace_id"] for span in matches})
    if len(traces) != 1:
        errors.append("tool_calls_cross_trace_or_unbound")
    if len(traces) == 1:
        native_ids = {call["id"] for call in session["calls"]}
        trace_tools = [span for span in spans if span["trace_id"] == traces[0] and _tool(span) is not None]
        if (len(trace_tools) != len(native_ids)
                or {span["attributes"].get("gen_ai.tool.call.id") for span in trace_tools} != native_ids):
            errors.append("trace_contains_unmatched_tool_calls")
        else:
            native_order = [call["id"] for call in session["calls"]]
            span_order = [row["attributes"].get("gen_ai.tool.call.id") for row in sorted(
                trace_tools, key=lambda row: (row["start"], row["end"], row["span_id"]))]
            if span_order != native_order:
                errors.append("native_tool_order_mismatch")
    errors.extend(error for comparison in comparisons for error in comparison["errors"])
    return {"candidate_trace_ids": traces, "trace_id": traces[0] if len(traces) == 1 and not errors else None,
            "errors": sorted(set(errors)), "comparisons": comparisons,
            "gaps": sorted(set(session.get("gaps", [])) |
                           {gap for comparison in comparisons for gap in comparison["gaps"]})}


def finite_contract(acceptance):
    """Describe requirement coverage independently of capture health.

    A simple read has an exhaustive tool-result check even when telemetry is
    incomplete. The evaluator separately requires complete capture before any
    success/failure verdict. Recovery order and unavailable-file semantics are
    not modeled by this v1 contract and must remain nonexhaustive.
    """
    if any(not isinstance(acceptance.get(key), str) or not acceptance[key]
           for key in ("expected_tool", "expected_path", "expected_text")):
        raise ValueError("pre_run_acceptance_missing")
    return {"schema": "openclaw-tool-contract/v1",
            "complete": acceptance.get("scenario") == "read",
            "checks": [{"id": "declared-file-content", "tool": acceptance["expected_tool"],
                        "argument_equals": {"/path": acceptance["expected_path"]},
                        "result_equals": {"/content/0/text": acceptance["expected_text"]}}]}


def audit_capture(manifest_path, transcript_dir=None, output_dir=None):
    manifest_path = Path(manifest_path).resolve()
    source = load_export(manifest_path)
    root, source_evidence = manifest_path.parent, evidence(manifest_path)
    shutdown = audit_gateway_shutdown(root, source.get("gateway_shutdown"), source.get("gateway_exit_code"))
    recorded_shutdown = source.get("gateway_shutdown_audit")
    if recorded_shutdown is not None and digest(recorded_shutdown) != digest(shutdown):
        shutdown["reasons"] = sorted(set(shutdown["reasons"]) | {"gateway_shutdown_receipt_mismatch"})
        shutdown["clean"] = False
        shutdown["receipt_status"] = "mismatch"
    else:
        shutdown["receipt_status"] = "verified" if recorded_shutdown is not None else "legacy_log_recheck"
    cases = source.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("capture manifest needs cases")
    ids = [valid_id(case["id"]) for case in cases]
    if len(set(ids)) != len(ids) or len({case.get("session_id") for case in cases}) != len(cases):
        raise ValueError("case and session IDs must be unique")
    transcripts = Path(transcript_dir).resolve() if transcript_dir else root / "native-transcripts"
    destination = Path(output_dir).resolve() if output_dir else root / "capture-audit"
    destination.mkdir(parents=True, exist_ok=False)
    capture_path = root / source.get("capture", "capture/traces.jsonl")
    capture_evidence, documents, spans, capture_error = None, None, [], None
    try:
        capture_evidence = evidence(capture_path)
        documents = load_export(capture_path)
        spans = spans_from_export(documents)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        capture_error = "capture_unavailable_or_invalid:" + type(exc).__name__
    audits, sessions, tasks = [], {}, {}
    for case in cases:
        cid = case["id"]
        folder = root / "cases" / cid
        audit = {"schema": "openclaw-native-capture-audit/v1", "case_id": cid, "session_id": case.get("session_id"),
                 "method": "exact_active_user_prompt_and_session_then_native_tool_call_id",
                 "status": "rejected", "errors": [], "gaps": [], "trace_id": None,
                 "source_manifest": source_evidence, "source_capture": capture_evidence,
                 "gateway_shutdown_audit": shutdown}
        audits.append(audit)
        tasks[cid] = {"schema": "openclaw-task/v1", "task": "",
                      "capture": {"complete": False, "reason": "Native-session/OTLP audit has not established completeness."}}
        try:
            prompt = (folder / "prompt.txt").read_text(encoding="utf-8")
            tasks[cid]["task"] = prompt
            audit["source_prompt"] = evidence(folder / "prompt.txt")
            path = transcripts / cid / "transcript.json"
            events_path = path.with_name("events.jsonl")
            audit["source_transcript"], audit["source_events"] = evidence(path), evidence(events_path)
            session = native_session(load_export(path), events_path.read_bytes(), case, prompt, source_evidence["sha256"])
            sessions[cid] = session
            audit["native_boundary"] = {key: value for key, value in session.items() if key != "calls"}
            audit["native_tool_call_ids"] = [call["id"] for call in session["calls"]]
            if capture_error:
                audit["errors"].append(capture_error)
            else:
                audit.update(bind_session(session, spans))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            audit["errors"].append(str(exc) if isinstance(exc, ValueError) else "native_evidence_invalid:" + type(exc).__name__)
    calls_by_id = Counter(call["id"] for session in sessions.values() for call in session["calls"])
    bound_traces = Counter(audit["trace_id"] for audit in audits if audit["trace_id"])
    evaluation_cases, provenances = [], {}
    for case, audit in zip(cases, audits):
        cid, task = case["id"], tasks[case["id"]]
        audit["gaps"] = sorted(set(audit["gaps"]) | set(shutdown["reasons"]))
        if any(calls_by_id[call_id] > 1 for call_id in audit.get("native_tool_call_ids", [])):
            audit["errors"].append("tool_call_id_shared_between_native_sessions")
        if audit["trace_id"] and bound_traces[audit["trace_id"]] > 1:
            audit["errors"].append("trace_shared_between_cases")
        if audit["errors"]:
            audit["trace_id"] = None
        target = destination / "cases" / cid
        target.mkdir(parents=True)
        if audit["trace_id"]:
            trace = audit["trace_id"]
            selected = select_native_trace(documents, trace)
            identity = lambda rows: {(s["trace_id"], s["span_id"]): s["source_digest"] for s in rows}
            if identity(spans_from_export(selected)) != identity([s for s in spans if s["trace_id"] == trace]):
                raise ValueError("native_trace_selection_changed_span_contents")
            write_json(target / "trace.json", selected)
            audit["selected_export"] = evidence(target / "trace.json")
            audit["status"] = "bound"
            imported = inspect(spans, task, trace)
            audit["import_audit"] = imported
            closed = sessions[cid]["closed"]
            runtime_closed = (case.get("execution_status") == "exited" and case.get("exit_code") == 0
                              and shutdown["clean"] is True)
            complete = closed and runtime_closed and not audit["gaps"] and imported["coverage"]["content_complete"]
            audit["tool_boundary_complete"] = complete
            task["capture"] = {"complete": complete,
                               "reason": ("Exact native active-session calls/results match all selected trace tool spans; "
                                          "terminal assistant and graceful exporter shutdown verified."
                                          if complete else "Trace identity matched; native boundary, flush, or telemetry content remains incomplete.")}
            provenances[cid] = {"schema": "openclaw-trace-selection/v1",
                "source_capture": capture_evidence, "source_transcript": audit["source_transcript"],
                "gateway_shutdown_audit": shutdown,
                "method": audit["method"], "trace_id": trace,
                "tool_call_ids": audit["native_tool_call_ids"], "comparisons": audit["comparisons"],
                "export": audit["selected_export"],
                "boundary": "All original spans with this trace ID; no timestamp slicing, merging, or reconstructed content."}
        try:
            acceptance_path = root / "cases" / cid / "acceptance.json"
            acceptance = load_export(acceptance_path)
            audit["source_acceptance"] = evidence(acceptance_path)
            task["contract"] = finite_contract(acceptance)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            audit["gaps"].append("acceptance_unavailable:" + type(exc).__name__)
        evaluation_cases.append({"id": cid, "session_id": case.get("session_id"),
            "export": f"cases/{cid}/trace.json", "task_file": f"cases/{cid}/task.json", "trace_id": audit["trace_id"],
            "expected_status": None, "expected_root_steps": [], "expected_module": None,
            "capture_audit": f"cases/{cid}/audit.json", "binding_status": audit["status"]})
    # A collector must have stopped writing before this audit starts. Recheck
    # before publishing any completeness claim; changed logs may contain a late
    # cleanup failure even when the entry receipt was valid.
    final_shutdown = audit_gateway_shutdown(root, source.get("gateway_shutdown"), source.get("gateway_exit_code"))
    if digest(final_shutdown["logs"]) != digest(shutdown["logs"]):
        shutdown["reasons"] = sorted(set(shutdown["reasons"]) | set(final_shutdown["reasons"]) |
                                     {"gateway_shutdown_logs_changed_during_audit"})
        shutdown.update(clean=False, receipt_status="changed_during_audit", final_logs=final_shutdown["logs"])
        for audit in audits:
            audit["gaps"] = sorted(set(audit["gaps"]) | set(shutdown["reasons"]))
            if "tool_boundary_complete" in audit:
                audit["tool_boundary_complete"] = False
            task = tasks[audit["case_id"]]
            task["capture"] = {"complete": False, "reason": "Gateway shutdown logs changed during capture audit."}
    if evidence(manifest_path) != source_evidence:
        raise ValueError("source_manifest_changed_during_audit")
    for audit in audits:
        cid = audit["case_id"]
        target = destination / "cases" / cid
        write_json(target / "task.json", tasks[cid])
        write_json(target / "audit.json", audit)
        if cid in provenances:
            write_json(target / "trace.provenance.json", provenances[cid])
    result = {"schema": "openclaw-evaluation/v1", "data_origin": source.get("data_origin", "controlled"),
              "source_manifest": source_evidence, "gateway_shutdown_audit": shutdown, "cases": evaluation_cases,
              "audit_summary": {"bound": sum(row["status"] == "bound" for row in audits),
                                "complete_tool_boundaries": sum(row.get("tool_boundary_complete", False) for row in audits),
                                "rejected": sum(row["status"] == "rejected" for row in audits)},
              "notes": ["Rejected cases intentionally have no trace export; original capture is retained.",
                        "No diagnostic labels are inferred. Contracts cover declared file-read results only."]}
    write_json(destination / "audit-manifest.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--transcript-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = audit_capture(args.manifest, args.transcript_dir, args.output_dir)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, "Capture audit failed: " + type(exc).__name__ + "\n")
    print(json.dumps(result["audit_summary"]))
    return 0 if not result["audit_summary"]["rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
