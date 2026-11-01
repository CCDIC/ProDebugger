"""Join recorded native messages to verified tool telemetry without reconstruction.

Completeness here means representation of the supplied recorded messages. It
does not establish semantic understanding, recording completeness or access to
the agent's unrecorded cognition. No model is called by this importer.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from .record_bridge import digest, valid_id, PROFILES
from .otel_import import loads

VERSION = "openclaw-session/v1"
SOURCE_FIELDS = {"trajectory_id", "task_type", "task", "steps", "telemetry", "task_contract"}


def _load(value):
    if isinstance(value, (str, Path)):
        path = Path(value)
        return loads(path.read_text(encoding="utf-8-sig")), str(path.resolve())
    if not isinstance(value, dict):
        raise ValueError("session inputs must be JSON objects or file paths")
    return deepcopy(value), None


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("native message content must be text or an array")
    if any(not isinstance(block, dict) or not isinstance(block.get("type"), str)
           for block in content):
        raise ValueError("native content blocks need an explicit type")
    return "\n".join(block["text"] for block in content
                     if block.get("type") == "text" and isinstance(block.get("text"), str))


def _tool_sources(source):
    calls, results = {}, {}
    for step in source.get("steps", []):
        for kind, text in (("call", step.get("parts", {}).get("action")),
                           ("result", step.get("env_text"))):
            if not text:
                continue
            value = PROFILES["openclaw"].envelope(text)
            if value.get("type") == "tool_context" and kind == "result":
                continue
            expected = "tool_call" if kind == "call" else "tool_result"
            if value.get("type") != expected:
                raise ValueError("base source must be the original tool-only import")
            call = value.get("provenance", {}).get("tool_call_id")
            if not isinstance(call, str) or not call:
                raise ValueError("base tool envelope lacks native tool_call_id")
            target = calls if kind == "call" else results
            if call in target:
                raise ValueError("duplicate base " + kind + " ID: " + call)
            target[call] = {"envelope": value, "step": step["t"]}
    if not calls or set(calls) != set(results):
        raise ValueError("base source needs one matched recorded return per tool call")
    for call in calls:
        a, r = calls[call]["envelope"], results[call]["envelope"]
        if any(a.get(key) != r.get(key) for key in ("trace_id", "span_id", "tool")):
            raise ValueError("base tool call/result identity mismatch")
    return calls, results


def convert_session(base_source, transcript, *, trajectory_id=None, source_label=None):
    """Return a new source and auditable join report, rejecting partial joins.

    Each recorded message is represented once, plus one action event for each
    toolCall block. Non-message native metadata is retained separately. Original
    steps are preserved in ``session.base_steps`` and not edited in place.
    """
    return _convert_session(base_source, transcript, trajectory_id=trajectory_id, source_label=source_label)


def _convert_session(base_source, transcript, *, trajectory_id=None, source_label=None,
                     origin_paths=None):
    source, base_path = _load(base_source)
    native, transcript_path = _load(transcript)
    if source.get("task_type") != "openclaw" or source.get("session"):
        raise ValueError("expected an unexpanded OpenClaw tool source")
    # A source is an input document, never externally supplied facts, labels or
    # diagnostic conclusions. Keep only normal inputs for deterministic replay.
    source = {key: value for key, value in source.items() if key in SOURCE_FIELDS}
    if origin_paths is not None:
        base_path, transcript_path = origin_paths
        if any(value is not None and not isinstance(value, str) for value in origin_paths):
            raise ValueError("source paths must be strings or null")
    if source_label is not None and not isinstance(source_label, str):
        raise ValueError("source label must be a string or null")
    if native.get("schema") != "openclaw-native-transcript/v1":
        raise ValueError("expected an exported native OpenClaw transcript")
    session_id = native.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("native transcript needs session_id")
    explicit_session = source.get("telemetry", {}).get("session_id")
    if explicit_session is not None and explicit_session != session_id:
        raise ValueError("base and native session_id mismatch")
    calls, results = _tool_sources(source)
    trace_ids = {entry["envelope"]["trace_id"] for entry in calls.values()}
    if len(trace_ids) != 1:
        raise ValueError("session join currently requires one selected trace")
    trace_id = next(iter(trace_ids))
    rows = native.get("events")
    if not isinstance(rows, list) or not rows:
        raise ValueError("native transcript requires recorded events")
    seqs = [row.get("seq") for row in rows if isinstance(row, dict)]
    if (len(seqs) != len(rows) or any(type(seq) is not int for seq in seqs)
            or seqs != sorted(set(seqs))):
        raise ValueError("native event sequence must be unique and increasing")
    headers = [r["event"] for r in rows if isinstance(r.get("event"), dict)
               and r["event"].get("type") == "session"]
    if len(headers) != 1 or headers[0].get("id") != session_id:
        raise ValueError("native session header mismatch")
    source_digest, native_digest = digest(source), digest(native)
    events, metadata, steps, matched_calls, matched_results = [], [], [], {}, {}
    messages, unsupported_blocks, task_match = 0, [], []
    native_event_ids = set()

    def provenance(row, index, suffix=""):
        pointer = f"/events/{index}/event" + suffix
        return {"document": "session.native_transcript", "json_pointer": pointer,
                "original_seq": row["seq"], "native_event_id": row["event"].get("id"),
                "sha256": digest(row["event"]), "hash_scope": "canonical_native_event",
                "native_event_sha256": row.get("event_sha256"),
                "transcript_sha256": native_digest, "transcript_path": transcript_path}

    def append(event, observation, action=None):
        event["step"] = len(steps) + 1
        events.append(event)
        action_text = _encoded(action) if action is not None else None
        steps.append({"t": event["step"], "env_text": _encoded(observation),
                      "agent_text": action_text or "", "admissible": [],
                      "parts": {"memory": None, "reflection": None, "plan": None,
                                "action": action_text}})

    for index, row in enumerate(rows):
        raw = row.get("event")
        if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
            raise ValueError("native event needs a typed object")
        native_id = raw.get("id")
        if not isinstance(native_id, str) or not native_id or native_id in native_event_ids:
            raise ValueError("native event IDs must be nonempty and unique")
        native_event_ids.add(native_id)
        if raw["type"] != "message":
            metadata.append({"event_id": native_id, "original_seq": row["seq"],
                             "type": raw["type"], "content": deepcopy(raw),
                             "classification": "non_message_metadata",
                             "source": provenance(row, index)})
            continue
        message = raw.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise ValueError("native message requires an explicit role")
        role, content = message["role"], deepcopy(message.get("content"))
        text = _text(content)
        messages += 1
        if role == "user" and text == source.get("task"):
            task_match.append(row["seq"])
        origin = provenance(row, index, "/message")
        event = {"event_id": native_id, "original_seq": row["seq"], "role": role,
                 "type": "session_message", "text": text, "content": content,
                 "source": origin, "trace_id": trace_id,
                 "stop_reason": message.get("stopReason"),
                 "active_position": row.get("active_position"),
                 "timestamp": raw.get("timestamp")}
        if role == "toolResult":
            call = message.get("toolCallId")
            if call not in calls or call not in matched_calls or call in matched_results:
                raise ValueError("unmatched, out-of-order or duplicate native toolResult")
            envelope = deepcopy(results[call]["envelope"])
            if message.get("toolName") != envelope["tool"]:
                raise ValueError("native toolResult tool name mismatch: " + call)
            response = envelope.get("result")
            if not isinstance(response, dict) or digest(response.get("content")) != digest(content):
                raise ValueError("native toolResult content mismatch: " + call)
            if (("details" in response) != ("details" in message)
                    or digest(response.get("details")) != digest(message.get("details"))):
                raise ValueError("native toolResult details mismatch: " + call)
            if type(message.get("isError")) is not bool:
                raise ValueError("native toolResult lacks recorded isError status")
            if message["isError"] != (envelope.get("status") == "error"):
                raise ValueError("native toolResult error status mismatch: " + call)
            envelope["provenance"] = {**envelope.get("provenance", {}), "native_source": origin}
            event.update(type="tool_result", call_id=call, span_id=envelope["span_id"],
                         tool=envelope["tool"], status=envelope["status"],
                         result=deepcopy(response), is_error=message["isError"])
            append(event, envelope)
            matched_results[call] = event
            continue
        observation = {"type": "session_message", "trace_id": trace_id, "event_id": native_id,
                       "role": role, "text": text, "content": content, "provenance": origin}
        append(event, observation)
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if block["type"] != "toolCall":
                if block["type"] != "text":
                    unsupported_blocks.append({"event_id": native_id, "original_seq": row["seq"],
                                               "block": block_index, "type": block["type"],
                                               "status": "preserved_not_semantically_interpreted"})
                continue
            call = block.get("id")
            if role != "assistant" or call not in calls or call in matched_calls:
                raise ValueError("unmatched or duplicate native toolCall")
            envelope = deepcopy(calls[call]["envelope"])
            if (block.get("name") != envelope["tool"]
                    or "arguments" not in block
                    or digest(block["arguments"]) != digest(envelope.get("arguments"))):
                raise ValueError("native toolCall name/arguments mismatch: " + call)
            call_origin = provenance(row, index, f"/message/content/{block_index}")
            envelope["provenance"] = {**envelope.get("provenance", {}), "native_source": call_origin}
            call_event = {"event_id": native_id + ":tool:" + str(block_index),
                          "original_seq": row["seq"], "role": role, "type": "tool_call",
                          "text": "", "content": deepcopy(block), "source": call_origin,
                          "parent_message_id": native_id, "call_id": call,
                          "trace_id": trace_id, "span_id": envelope["span_id"],
                          "tool": envelope["tool"], "arguments": deepcopy(envelope["arguments"])}
            context = {"type": "tool_context", "trace_id": trace_id,
                       "provenance": {"native_source": call_origin,
                                      "parent_message_id": native_id,
                                      "scope": "recorded_call_context_not_inferred_reasoning"}}
            append(call_event, context, envelope)
            matched_calls[call] = call_event
    if not task_match:
        raise ValueError("task does not exactly match a recorded native user message")
    if set(matched_calls) != set(calls) or set(matched_results) != set(results):
        raise ValueError("native transcript does not account for every base call and return")
    recorded_complete = messages == sum(event["type"] != "tool_call" for event in events)
    coverage = {"native_events": len(rows), "native_messages": messages,
                "represented_messages": messages, "native_metadata_events": len(metadata),
                "preserved_metadata_events": len(metadata), "represented_execution_events": len(events),
                "native_tool_calls": len(matched_calls), "matched_tool_calls": len(matched_calls),
                "native_tool_results": len(matched_results), "matched_tool_results": len(matched_results),
                "recorded_messages_complete": recorded_complete,
                "tool_join_complete": True, "semantic_parse_complete": False,
                "internal_cognition": "not_reconstructed; only explicitly recorded content is available",
                "capture_complete_claim": native.get("capture_complete"),
                "scope": "all messages present in supplied native transcript; not a claim that all internal agent state was recorded",
                "uninterpreted_content_blocks": unsupported_blocks}
    original_mapping = deepcopy(source.get("telemetry", {}).get("source_map", []))
    by_span = {(item.get("trace_id"), item.get("span_id")): item for item in original_mapping}
    mapping = []
    for call, event in matched_calls.items():
        result = matched_results[call]
        old = by_span.get((event["trace_id"], event["span_id"]), {})
        mapping.append({**old, "tool": event["tool"], "trace_id": trace_id, "span_id": event["span_id"],
                        "tool_call_id": call, "action_step": event["step"], "result_step": result["step"],
                        "action_original_seq": event["original_seq"], "result_original_seq": result["original_seq"],
                        "action_event_id": event["event_id"], "result_event_id": result["event_id"],
                        "action_source": event["source"], "result_source": result["source"],
                        "base_action_step": calls[call]["step"], "base_result_step": results[call]["step"]})
    session = {"schema": VERSION, "session_id": session_id,
               "source_label": source_label or "native OpenClaw recorded session + verified tool telemetry",
               "base_source_sha256": source_digest, "base_source_path": base_path,
               "transcript_sha256": native_digest, "transcript_path": transcript_path,
               "base_trajectory_id": source["trajectory_id"], "base_source": deepcopy(source),
               "base_steps": deepcopy(source["steps"]),
               "base_source_map": original_mapping, "events": events, "metadata": metadata,
               "coverage": coverage, "native_transcript": native,
               "task_exact_match_seqs": task_match,
               "step_numbering": "one event per imported step; original_seq is the native record sequence, not an inferred agent reasoning step"}
    source["trajectory_id"] = valid_id(trajectory_id or source["trajectory_id"] + "-session")
    source["steps"], source["session"] = steps, session
    source.setdefault("telemetry", {}).update(trace_id=trace_id, session_id=session_id, source_map=mapping)
    audit = {"schema": "openclaw-session-import-audit/v1", "status": "verified",
             "base_source_sha256": source_digest, "transcript_sha256": native_digest,
             "output_sha256": digest(source), "coverage": deepcopy(coverage),
             "checks": {"task_exact_match": True, "call_ids": True, "tool_names": True,
                        "arguments": True, "return_content": True, "return_details": True,
                        "return_error_status": True, "no_silently_dropped_messages": True},
             "limitations": ["Complete representation is not complete semantic understanding.",
                             "No unrecorded memory, plan, reflection or hidden reasoning is inferred.",
                             "Metadata and unsupported content blocks are preserved without behavioral interpretation."]}
    return source, audit


def validate_session_source(source):
    """Rebuild derived session input from preserved raw sources; return its audit.

    This verifies internal source consistency, not original collector identity.
    Authenticity additionally requires an independently trusted capture receipt.
    Provenance paths are replayed as strings and are never opened by validation.
    Caller-supplied parsed entities/facts/coverage flags cannot replace replay.
    """
    if isinstance(source, dict) and source.get("session", {}).get("schema") == "openclaw-signoz-session/v1":
        from .signoz_import import validate_signoz_source
        return validate_signoz_source(source)
    if isinstance(source, dict) and source.get("session", {}).get("schema") == "openclaw-session/v2":
        from .openclaw_export_bridge import validate_export_source
        return validate_export_source(source)
    if not isinstance(source, dict) or source.get("task_type") != "openclaw":
        raise ValueError("session validation requires an OpenClaw source object")
    session = source.get("session")
    if not isinstance(session, dict) or session.get("schema") != VERSION:
        raise ValueError("missing supported recorded session source")
    base, native = session.get("base_source"), session.get("native_transcript")
    if not isinstance(base, dict) or not isinstance(native, dict):
        raise ValueError("session validation requires preserved base_source and native_transcript")
    if set(base) - SOURCE_FIELDS:
        raise ValueError("session base_source contains non-source fields")
    if set(source) - SOURCE_FIELDS - {"session"}:
        raise ValueError("session input contains non-source fields")
    if not isinstance(source.get("trajectory_id"), str):
        raise ValueError("session input needs an explicit trajectory_id")
    try:
        expected, audit = _convert_session(
            base, native, trajectory_id=source["trajectory_id"],
            source_label=session.get("source_label"),
            origin_paths=(session.get("base_source_path"), session.get("transcript_path")))
        # Digest comparisons distinguish JSON types (e.g. true versus 1) and
        # include every source field. No supplied completeness flag is trusted.
        if set(expected) != set(source):
            raise ValueError("session source fields mismatch")
        for key in sorted(expected):
            if digest(expected[key]) != digest(source[key]):
                raise ValueError("session source mismatch at " + key)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("invalid preserved session source structure") from exc
    return {**audit, "validation": "canonical_source_replay",
            "authenticity": "requires_independent_capture_receipt"}
