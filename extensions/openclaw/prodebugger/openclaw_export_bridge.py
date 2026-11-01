"""Replayable adapter for official OpenClaw trajectory exports.

Runtime tool events and recorded public messages are different observations.
Nested calls remain nested; duplicate transcript/runtime rows are mapped, not
counted as extra calls. Raw export documents are retained for strict replay.
No diagnostic labels, ground truth, or inferred cognition are accepted here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .record_bridge import digest, valid_id

VERSION = "openclaw-session/v2"
RAW_FILES = {"manifest": "manifest.json", "events": "events.jsonl",
             "session_branch": "session-branch.json", "prompts": "prompts.json",
             "tools": "tools.json", "metadata": "metadata.json", "artifacts": "artifacts.json"}
SOURCE_FIELDS = {"trajectory_id", "task_type", "task", "steps", "telemetry", "task_contract", "session"}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or any(not isinstance(b, dict) or not isinstance(b.get("type"), str) for b in content):
        raise ValueError("official message content needs text or typed content blocks")
    return "\n".join(b["text"] for b in content if b["type"] == "text" and isinstance(b.get("text"), str))


def _redacted(value):
    text = _encoded(value)
    return any(mark in text for mark in ("***", "[Truncated]", "[Malformed", "[Malfo", "trajectory-depth-limit"))


def _matches_except_truncation(partial, full):
    """Only explicit exporter placeholders relax independent cross-checks."""
    if partial == "[Truncated]":
        return True
    if isinstance(partial, dict) and partial.get("truncated") is True and partial.get("reason") == "trajectory-depth-limit":
        return True
    if isinstance(partial, dict) and isinstance(full, dict):
        return set(partial) == set(full) and all(_matches_except_truncation(partial[k], full[k]) for k in partial)
    if isinstance(partial, list) and isinstance(full, list):
        return len(partial) == len(full) and all(_matches_except_truncation(a, b) for a, b in zip(partial, full))
    return type(partial) is type(full) and partial == full


def _span_documents(files):
    if files is None:
        return []
    if isinstance(files, (str, Path)):
        path = Path(files)
        files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    result = []
    for item in files:
        value = json.loads(Path(item).read_text(encoding="utf-8-sig")) if isinstance(item, (str, Path)) else deepcopy(item)
        if isinstance(value, dict) and isinstance(value.get("span_id"), str) and isinstance(value.get("trace_id"), str):
            result.append(value)
    return result


def load_export(export_dir, span_files=None, *, trajectory_id=None, source_label=None,
                source_kind="official_openclaw_agent_export"):
    """Return ``(canonical_source, audit)``; never modify original exports.

    ``export_dir`` may also be a raw input object returned as
    ``source['session']['official_export']``. This supports independently
    copied input variants without sending evaluator annotations to the model.
    """
    if isinstance(export_dir, dict):
        raw = deepcopy(export_dir)
        origin = None
    else:
        directory = Path(export_dir)
        raw, origin = {}, str(directory.resolve())
        for key, filename in RAW_FILES.items():
            path = directory / filename
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8-sig")
            raw[key] = [json.loads(line) for line in text.splitlines() if line.strip()] if key == "events" else json.loads(text)
    return convert_export(raw, _span_documents(span_files), trajectory_id=trajectory_id,
                          source_label=source_label, export_path=origin, source_kind=source_kind)


def convert_export(raw, spans=None, *, trajectory_id=None, source_label=None, export_path=None, task_contract=None,
                   source_kind="official_openclaw_agent_export"):
    """Deterministically convert preserved official documents, with audit."""
    if not isinstance(raw, dict) or set(raw) - set(RAW_FILES):
        raise ValueError("official export only accepts normal capture documents; annotations are separate")
    raw, spans = deepcopy(raw), deepcopy(spans or [])
    if source_kind not in {"official_openclaw_agent_export", "constructed_variant_from_official_export"}:
        raise ValueError("unsupported official export source category")
    rows, branch = raw.get("events"), raw.get("session_branch")
    if not isinstance(rows, list) or not rows or not isinstance(branch, dict):
        raise ValueError("official export requires events and session_branch")
    manifest, header = raw.get("manifest", {}), branch.get("header", {})
    provenance = manifest.get("prodebugger_input_provenance")
    if provenance is not None:
        if (not isinstance(provenance, dict) or set(provenance) != {"kind", "executed", "baseline_sha256"}
                or provenance.get("kind") != "offline_constructed_copy" or provenance.get("executed") is not False
                or not isinstance(provenance.get("baseline_sha256"), str) or len(provenance["baseline_sha256"]) != 64):
            raise ValueError("invalid offline provenance; evaluation annotations must stay separate")
        source_kind = "constructed_variant_from_official_export"
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("official session header needs id")
    if manifest and (manifest.get("traceSchema") != "openclaw-trajectory" or manifest.get("schemaVersion") != 1 or manifest.get("sessionId") != session_id):
        raise ValueError("unsupported official manifest or session mismatch")
    seqs = [row.get("seq") for row in rows if isinstance(row, dict)]
    if len(seqs) != len(rows) or any(type(seq) is not int for seq in seqs) or seqs != sorted(set(seqs)):
        raise ValueError("official event seq must be unique increasing integers")
    if any(row.get("traceSchema") != "openclaw-trajectory" or row.get("schemaVersion") != 1 or row.get("sessionId") != session_id for row in rows):
        raise ValueError("official events schema/session mismatch")
    entries = branch.get("entries")
    if not isinstance(entries, list) or any((not isinstance(e, dict) or not e.get("id")) and e != "[Truncated]" for e in entries):
        raise ValueError("official session_branch requires identified entries")
    branch_truncated = any(e == "[Truncated]" for e in entries)
    entry_by_id = {e["id"]: e for e in entries if isinstance(e, dict)}
    if len(entry_by_id) != sum(isinstance(e, dict) for e in entries):
        raise ValueError("duplicate branch entry id")
    calls, returns, call_rows, result_rows = {}, {}, {}, {}
    messages, message_rows, nested, direct_calls = {}, {}, {}, {}
    gaps, conflicts = [], []
    branch_unverified = []
    if branch_truncated:
        gaps.append({"kind": "truncated_session_branch", "note": "Official branch export contains explicit truncated entries; events.jsonl remains the primary recorded source for those entries."})
    for i, row in enumerate(rows):
        data, kind = row.get("data", {}), row.get("type")
        if not isinstance(data, dict):
            raise ValueError("official event data must be object")
        message = data.get("message")
        if row.get("source") == "transcript" and isinstance(message, dict) and row.get("entryId"):
            entry = entry_by_id.get(row["entryId"])
            if not entry and branch_truncated:
                branch_unverified.append(row["entryId"])
            elif entry and digest(entry.get("message")) != digest(message) and _matches_except_truncation(entry.get("message"), message):
                branch_unverified.append(row["entryId"])
            elif not entry or digest(entry.get("message")) != digest(message):
                raise ValueError("events/branch message mismatch: " + row["entryId"])
            if kind in {"user.message", "assistant.message"}:
                messages[i], message_rows[row["entryId"]] = message, i
                for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                    if message.get("role") == "assistant" and isinstance(block, dict) and block.get("type") == "toolCall" and block.get("id"):
                        direct_calls[block["id"]] = row["entryId"]
            if message.get("customType") == "openclaw.nested-tool.v1":
                detail = message.get("details", {})
                if detail.get("toolCallId"):
                    nested[detail["toolCallId"]] = {"details": detail, "index": i}
        if kind == "tool.call":
            call = data.get("toolCallId")
            if not isinstance(call, str) or not call:
                raise ValueError("official tool.call needs toolCallId")
            call_rows.setdefault(call, []).append(i)
            args = data.get("args", data.get("arguments"))
            candidate = {"name": data.get("name"), "arguments": deepcopy(args), "index": i}
            if row.get("source") == "runtime" and call in calls and rows[calls[call]["index"]].get("source") == "runtime":
                raise ValueError("duplicate runtime tool.call id: " + call)
            if row.get("source") == "runtime" or call not in calls:
                calls[call] = candidate
        elif kind == "tool.result":
            if row.get("source") == "runtime":
                call = data.get("toolCallId")
                if call in returns:
                    raise ValueError("duplicate runtime tool.result for " + str(call))
                returns[call] = {"name": data.get("name"), "result": deepcopy(data.get("result")),
                                 "status": "ok" if data.get("success") is True else "error" if data.get("success") is False else "unset", "index": i}
            else:
                call = message.get("toolCallId") if isinstance(message, dict) else None
            if call:
                result_rows.setdefault(call, []).append(i)
    if not messages or not calls:
        raise ValueError("official export needs recorded messages and tool calls")
    if branch_unverified and not branch_truncated:
        gaps.append({"kind": "partially_truncated_session_branch", "note": "Independent branch message cross-check unavailable for explicitly truncated fields."})
    for call, entry in nested.items():
        detail = entry["details"]
        if call not in calls:
            raise ValueError("nested transcript call missing runtime tool.call: " + call)
        if detail.get("parentToolCallId") not in calls:
            raise ValueError("nested call has unknown recorded parent")
        if digest(detail.get("input")) != digest(calls[call]["arguments"]) or detail.get("toolName") != calls[call]["name"]:
            raise ValueError("nested call/runtime arguments disagree")
        if call in returns and digest(detail.get("result")) != digest(returns[call]["result"]):
            raise ValueError("nested call/runtime result disagrees")
    for call, result in returns.items():
        if call not in calls or result["name"] != calls[call]["name"] or result["index"] <= calls[call]["index"]:
            raise ValueError("unmatched/out of order official tool result")
        for i in result_rows.get(call, []):
            message = rows[i].get("data", {}).get("message")
            if isinstance(message, dict):
                actual = {key: message[key] for key in ("content", "details") if key in message}
                wanted = {key: result["result"][key] for key in ("content", "details") if isinstance(result["result"], dict) and key in result["result"]}
                if digest(actual) != digest(wanted) or message.get("isError") is not (result["status"] == "error"):
                    raise ValueError("transcript/runtime result disagreement: " + call)
    task_candidates = [_text(message.get("content")) for message in messages.values() if message.get("role") == "user"]
    task = raw.get("prompts", {}).get("latestSubmittedPrompt") or (task_candidates[-1] if task_candidates else None)
    if not isinstance(task, str) or not task or task not in task_candidates:
        raise ValueError("task must exactly match a recorded user message")
    span_by_call = {}
    for span in spans:
        attrs = span.get("attributes", {})
        call = attrs.get("gen_ai.tool.call.id")
        if call not in calls:
            continue
        if call in span_by_call:
            raise ValueError("multiple cloud spans share native tool call id")
        checks = {}
        for attr, expected in (("gen_ai.tool.call.arguments", calls[call]["arguments"]), ("gen_ai.tool.call.result", returns.get(call, {}).get("result"))):
            content = attrs.get(attr)
            try:
                content = json.loads(content) if isinstance(content, str) else content
                checks[attr] = "matched" if content is not None and digest(content) == digest(expected) else "different" if content is not None else "missing"
            except (ValueError, TypeError):
                checks[attr] = "unparsed"
        # A raw-source variant must not silently inherit cloud verification.
        span_by_call[call] = {"span": span, "checks": checks,
                              "content_verified": all(value == "matched" for value in checks.values())}
    cloud_traces = {x["span"]["trace_id"] for x in span_by_call.values()}
    if len(cloud_traces) > 1:
        raise ValueError("one official session cannot be silently merged across cloud traces")
    trace_id = next(iter(cloud_traces)) if cloud_traces else "native-" + session_id
    spans = [span for span in spans if span.get("trace_id") in cloud_traces]
    events, steps, metadata, mapping, unsupported = [], [], [], [], []
    row_map = [{"document": "official_export.events", "json_pointer": f"/events/{i}", "original_seq": row["seq"],
                "record_type": row.get("type"), "record_source": row.get("source"), "sha256": digest(row),
                "classification": "metadata", "event_ids": [], "imported_steps": []} for i, row in enumerate(rows)]
    export_digest = digest(raw)

    def origin(i, suffix=""):
        row = rows[i]
        return {"document": "session.official_export", "json_pointer": f"/events/{i}" + suffix,
                "original_seq": row["seq"], "source_seq": row.get("sourceSeq"), "record_source": row.get("source"),
                "native_event_id": row.get("entryId") or f"runtime-{row['seq']}", "sha256": digest(row),
                "hash_scope": "canonical_official_event", "export_sha256": export_digest, "export_path": export_path}

    def append(event, observation, action=None, source_indices=()):
        event["step"] = len(steps) + 1
        events.append(event)
        encoded = _encoded(action) if action is not None else None
        steps.append({"t": event["step"], "env_text": _encoded(observation), "agent_text": encoded or "", "admissible": [],
                      "parts": {"memory": None, "reflection": None, "plan": None, "action": encoded}})
        for i in source_indices:
            row_map[i]["classification"] = "represented" if i == source_indices[0] else "duplicate_evidence"
            row_map[i]["event_ids"].append(event["event_id"])
            row_map[i]["imported_steps"].append(event["step"])

    call_at = {item["index"]: call for call, item in calls.items()}
    result_at = {item["index"]: call for call, item in returns.items()}
    call_events, result_events = {}, {}
    for i, row in enumerate(rows):
        if i in messages:
            message, entry_id = messages[i], row["entryId"]
            content = deepcopy(message.get("content"))
            event = {"event_id": entry_id, "original_seq": row["seq"], "type": "session_message", "role": message["role"],
                     "text": _text(content), "content": content, "trace_id": trace_id, "source": origin(i, "/data/message"),
                     "timestamp": row.get("ts"), "stop_reason": message.get("stopReason")}
            for b, block in enumerate(content if isinstance(content, list) else []):
                if block.get("type") not in {"text", "toolCall"}:
                    unsupported.append({"event_id": entry_id, "original_seq": row["seq"], "block": b, "type": block.get("type"),
                                        "status": "recorded_preserved_not_semantically_interpreted"})
            append(event, {"type": "session_message", "trace_id": trace_id, "event_id": entry_id,
                           "role": event["role"], "text": event["text"], "content": content, "provenance": event["source"]}, source_indices=(i,))
        if i not in call_at and i not in result_at:
            continue
        call = call_at.get(i, result_at.get(i))
        item, cloud = calls[call], span_by_call.get(call)
        # Without OTel a deterministic local identifier is an analysis ID, not a
        # claimed real Span ID. This distinction is kept in every provenance.
        span_id = cloud["span"]["span_id"] if cloud else hashlib.sha256((session_id + "/" + call).encode()).hexdigest()[:16]
        parent = nested.get(call, {}).get("details", {}).get("parentToolCallId")
        prov = {"tool_call_id": call, "native_source": origin(i, "/data"), "source": "official_openclaw_export",
                "parent_tool_call_id": parent, "span_identity": "recorded_otel" if cloud else "synthetic_local_analysis_id",
                "cloud_content_checks": deepcopy(cloud["checks"]) if cloud else {},
                "cloud_content_verified": cloud["content_verified"] if cloud else False}
        common = {"trace_id": trace_id, "span_id": span_id, "tool": item["name"], "provenance": prov}
        layer = "nested_tool" if parent else "model_tool" if call in direct_calls else "runtime_tool_unclassified"
        event = {"event_id": ("call:" if i in call_at else "result:") + call, "original_seq": row["seq"],
                 "call_id": call, "trace_id": trace_id, "span_id": span_id, "tool": item["name"],
                 "source": origin(i, "/data"), "parent_call_id": parent,
                 "parent_message_id": direct_calls.get(call),
                 "invocation_layer": layer, "timestamp": row.get("ts"), "text": ""}
        if i in call_at:
            indices = [i] + [x for x in call_rows[call] if x != i]
            if call in nested:
                indices.append(nested[call]["index"])
            event.update(type="tool_call", arguments=deepcopy(item["arguments"]), role="tool_runtime" if parent else "assistant")
            envelope = {"type": "tool_call", **common, "arguments": deepcopy(item["arguments"])}
            if _redacted(item["arguments"]):
                gaps.append({"kind": "redacted_tool_arguments", "call_id": call, "original_seq": row["seq"], "note": "Recorded parameters contain collector redaction; original code cannot be recovered."})
            for other in call_rows[call]:
                other_args = rows[other]["data"].get("args", rows[other]["data"].get("arguments"))
                if digest(other_args) != digest(item["arguments"]):
                    conflicts.append({"kind": "argument_source_difference", "call_id": call, "seqs": [row["seq"], rows[other]["seq"]],
                                      "resolution": "runtime arguments retained; alternate raw source preserved; do not infer original redacted values"})
            append(event, {"type": "tool_context", "trace_id": trace_id, "provenance": prov}, envelope, tuple(indices))
            call_events[call] = event
        else:
            result = returns[call]
            event.update(type="tool_result", role="tool_runtime" if parent else "toolResult", result=deepcopy(result["result"]), status=result["status"], is_error=result["status"] == "error")
            indices = [i] + [x for x in result_rows[call] if x != i]
            if call in nested:
                indices.append(nested[call]["index"])
            append(event, {"type": "tool_result", **common, "status": result["status"], "result": deepcopy(result["result"])}, source_indices=tuple(indices))
            result_events[call] = event
    for call, event in call_events.items():
        result = result_events.get(call)
        mapping.append({"trace_id": trace_id, "span_id": event["span_id"], "tool": event["tool"], "tool_call_id": call,
                        "action_step": event["step"], "result_step": result["step"] if result else None,
                        "action_original_seq": event["original_seq"], "result_original_seq": result["original_seq"] if result else None,
                        "action_event_id": event["event_id"], "result_event_id": result["event_id"] if result else None,
                        "action_source": event["source"], "result_source": result["source"] if result else None,
                        "parent_tool_call_id": event.get("parent_call_id"), "invocation_layer": event["invocation_layer"],
                        "span_identity": "recorded_otel" if call in span_by_call else "synthetic_local_analysis_id"})
        if not result:
            gaps.append({"kind": "missing_tool_result", "call_id": call, "original_seq": event["original_seq"]})
    for i, row in enumerate(rows):
        if row_map[i]["classification"] == "metadata":
            metadata.append({"event_id": row.get("entryId") or f"metadata-{row['seq']}", "original_seq": row["seq"],
                             "type": row["type"], "classification": "non_behavior_metadata_or_unsupported", "content": deepcopy(row), "source": origin(i)})
    transcript_messages = sum(isinstance(row.get("data", {}).get("message"), dict) and row.get("source") == "transcript" for row in rows)
    coverage = {"native_events": len(rows), "native_messages": transcript_messages, "represented_messages": transcript_messages,
                "public_messages": len(messages), "branch_message_crosscheck_unavailable": branch_unverified,
                "represented_execution_events": len(events), "native_metadata_events": len(metadata), "preserved_metadata_events": len(metadata),
                "native_tool_calls": len(calls), "matched_tool_calls": len(call_events), "native_tool_results": len(returns), "matched_tool_results": len(result_events),
                "recorded_messages_complete": True, "tool_join_complete": set(call_events) == set(result_events), "semantic_parse_complete": False,
                "recorded_export_rows_accounted": len(row_map), "cloud_verified_calls": sum(x["content_verified"] for x in span_by_call.values()),
                "uninterpreted_content_blocks": unsupported, "gaps": gaps, "source_conflicts": conflicts,
                "scope": "All supplied export records are mapped or preserved; not a claim that every internal Agent state was recorded.",
                "internal_cognition": "recorded blocks preserved without reconstructing or assigning memory/reflection/plan semantics"}
    source = {"trajectory_id": valid_id(trajectory_id or "official-" + session_id), "task_type": "openclaw", "task": task, "steps": steps,
              "telemetry": {"trace_id": trace_id, "session_id": session_id, "source_map": mapping,
                            "data_origin": source_kind, "coverage": {"content_complete": not gaps, "gaps": gaps, "scope": coverage["scope"]}},
              "session": {"schema": VERSION, "session_id": session_id, "source_label": source_label or "真实 OpenClaw Agent 官方导出",
                          "official_export": raw, "export_sha256": export_digest, "export_path": export_path,
                          "source_kind": source_kind,
                          "cloud_spans": spans, "events": events, "metadata": metadata, "record_map": row_map,
                          "coverage": coverage, "step_numbering": "Imported event number; original_seq is an official export record number, never an inferred Agent reasoning step."}}
    if task_contract is not None:
        source["task_contract"] = deepcopy(task_contract)
    audit = {"schema": "openclaw-official-import-audit/v1", "status": "verified_with_limitations" if gaps or conflicts else "verified",
             "coverage": deepcopy(coverage), "record_map": deepcopy(row_map), "call_map": deepcopy(mapping), "export_sha256": export_digest,
             "output_sha256": digest(source), "checks": {"branch_message_identity": not branch_unverified, "task_exact_match": True, "all_records_accounted": True,
                                                         "runtime_transcript_dedup": True, "nested_parent_from_explicit_record": True},
             "limitations": ["Synthetic local IDs are explicitly distinguished from actual OTel Span IDs.",
                             "Messages/metadata are preserved; arbitrary business semantics and hidden cognition are not reconstructed.",
                             "Replaying an input proves internal consistency, not independent collector authenticity."]}
    return source, audit


def validate_export_source(source):
    """Strictly replay preserved source; completeness sidecars are not trusted."""
    if not isinstance(source, dict) or set(source) - SOURCE_FIELDS or source.get("task_type") != "openclaw":
        raise ValueError("invalid official session source fields")
    session = source.get("session", {})
    if session.get("schema") != VERSION:
        raise ValueError("unsupported official session schema")
    expected, audit = convert_export(session.get("official_export"), session.get("cloud_spans"), trajectory_id=source.get("trajectory_id"),
                                     source_label=session.get("source_label"), export_path=session.get("export_path"), task_contract=source.get("task_contract"),
                                     source_kind=session.get("source_kind"))
    for key in sorted(set(expected) | set(source)):
        if key not in source or key not in expected or digest(expected[key]) != digest(source[key]):
            raise ValueError("official session source mismatch at " + key)
    return {**audit, "validation": "canonical_official_export_replay", "authenticity": "requires_independent_capture_receipt"}
