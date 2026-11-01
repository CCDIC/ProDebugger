"""Replayable SigNoz/OTLP source import, without executing agents or using a model.

Cloud prompt snapshots are evidence, not a native OpenClaw transcript. A call
present only in model messages receives an analysis identity, never a made-up
cloud span. The original UTF-8 documents and every source pointer are retained.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .otel_import import loads
from .record_bridge import digest, valid_id

VERSION = "openclaw-signoz-session/v1"
AUDIT_VERSION = "signoz-source-import-audit/v1"
INPUT_KEYS = ("gen_ai.input.messages", "openclaw.content.input_messages")
OUTPUT_KEYS = ("gen_ai.output.messages", "openclaw.content.output_messages")
ARG_KEYS = ("gen_ai.tool.call.arguments", "openclaw.content.tool_input")
RESULT_KEYS = ("gen_ai.tool.call.result", "openclaw.content.tool_output")
TRACE_KEYS = ("trace_id", "traceId", "traceID")
SPAN_KEYS = ("span_id", "spanId", "spanID")


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _pick(value, keys, default=None):
    return next((value[key] for key in keys if key in value), default)


def _pointer(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def _child(origin, suffix):
    result = deepcopy(origin)
    key = "decoded_json_pointer" if "decoded_json_pointer" in result else "json_pointer"
    result[key] = result.get(key, "") + suffix
    return result


def _unique_refs(refs):
    return list({digest(ref): ref for ref in refs}.values())


def _json(value):
    if isinstance(value, str):
        try:
            return loads(value), True
        except (ValueError, TypeError):
            pass
    return deepcopy(value), False


def _response(value):
    """Match vendor text-block responses to OTel's serialized response value.

    Only a lossless single text block is unwrapped. Multi-block/media responses
    retain their original structure; originals remain in the raw documents.
    """
    if (isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict)
            and value[0].get("type") == "text" and isinstance(value[0].get("text"), str)):
        value = value[0]["text"]
    return _json(value)[0]


def _block_value(block):
    return {key: value for key, value in block.items()
            if key not in {"source", "source_refs", "arguments_source", "response_source"}}


def _value(value):
    """Decode declared OTLP/tag wrappers; leave unfamiliar shapes untouched."""
    if not isinstance(value, dict):
        return deepcopy(value), ""
    for key in ("stringValue", "string_value", "boolValue", "bool_value", "doubleValue", "double_value",
                "intValue", "int_value", "bytesValue", "bytes_value", "string", "bool", "int64", "float64"):
        if key in value:
            return deepcopy(value[key]), "/" + key
    for key in ("arrayValue", "array_value"):
        if key in value and isinstance(value[key], dict):
            return [_value(item)[0] for item in value[key].get("values", [])], "/" + key
    for key in ("kvlistValue", "kvlist_value"):
        if key in value and isinstance(value[key], dict):
            return {item["key"]: _value(item.get("value"))[0] for item in value[key].get("values", [])
                    if isinstance(item, dict) and isinstance(item.get("key"), str)}, "/" + key
    return deepcopy(value), ""


def _attributes(raw, origin, gaps):
    result = defaultdict(list)
    for container in ("attributes", "tags", "tagMap", "stringTagMap", "numberTagMap", "boolTagMap"):
        values = raw.get(container)
        if isinstance(values, dict):
            entries = [(key, value, "/" + container + "/" + _pointer(key)) for key, value in values.items()]
        elif isinstance(values, list):
            entries = [(item.get("key"), item.get("value"), f"/{container}/{i}/value")
                       for i, item in enumerate(values) if isinstance(item, dict)]
        elif values is None:
            continue
        else:
            gaps.append({"kind": "unsupported_attribute_container", "source": _child(origin, "/" + container)})
            continue
        for key, value, pointer in entries:
            if not isinstance(key, str):
                gaps.append({"kind": "attribute_without_string_key", "source": _child(origin, pointer)})
                continue
            decoded, suffix = _value(value)
            result[key].append({"value": decoded, "source": _child(origin, pointer + suffix)})
    return dict(result)


def _documents(documents):
    if not isinstance(documents, list) or not documents:
        raise ValueError("provide at least one {name,text} SigNoz/OTLP document")
    preserved, roots = [], []
    for index, document in enumerate(documents):
        if not isinstance(document, dict) or not isinstance(document.get("name"), str) or not isinstance(document.get("text"), str):
            raise ValueError("each document requires string name and original text")
        name, text = document["name"], document["text"]
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        origin = {"document": name, "document_index": index, "document_sha256": sha,
                  "sha256": sha, "hash_scope": "original_document_utf8", "json_pointer": ""}
        try:
            parsed = loads(text.lstrip("\ufeff"))
            roots.append((parsed, origin))
            fmt = "json"
        except (ValueError, TypeError):
            fmt = "jsonl"
            lines = [(line_number, line) for line_number, line in enumerate(text.splitlines(), 1) if line.strip()]
            if not lines:
                raise ValueError("empty document: " + name)
            for line_number, line in lines:
                try:
                    parsed = loads(line.lstrip("\ufeff"))
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"invalid JSON/JSONL in {name}, line {line_number}: {exc}") from exc
                roots.append((parsed, {**origin, "line_number": line_number, "document_scope": "jsonl_line"}))
        preserved.append({"name": name, "text": text, "sha256": sha, "format": fmt})
    return preserved, roots


def _collect(value, origin, output, unassigned, context=None, inherited_trace=None):
    context = context or {}
    if isinstance(value, list):
        for i, item in enumerate(value):
            _collect(item, _child(origin, "/" + str(i)), output, unassigned, context, inherited_trace)
        return
    if not isinstance(value, dict):
        unassigned.append({"reason": "non_object_export_record", "source": origin})
        return
    trace = _pick(value, TRACE_KEYS, inherited_trace)
    span = _pick(value, SPAN_KEYS)
    if isinstance(trace, str) and trace and isinstance(span, str) and span and any(
            key in value for key in ("attributes", "tags", "tagMap", "name", "operationName", "startTime", "duration_nano", "durationNano")):
        output.append({"raw": deepcopy(value), "trace_id": trace, "span_id": span,
                       "source": deepcopy(origin), "context": deepcopy(context)})
        return
    for key in ("resourceSpans", "resource_spans"):
        if isinstance(value.get(key), list):
            for i, resource in enumerate(value[key]):
                if not isinstance(resource, dict):
                    continue
                _collect(resource, _child(origin, f"/{key}/{i}"), output, unassigned,
                         {**context, "resource": deepcopy(resource.get("resource"))}, trace)
            return
    for key in ("scopeSpans", "scope_spans", "instrumentationLibrarySpans"):
        if isinstance(value.get(key), list):
            for i, scope in enumerate(value[key]):
                if not isinstance(scope, dict):
                    continue
                _collect(scope, _child(origin, f"/{key}/{i}"), output, unassigned,
                         {**context, "scope": deepcopy(scope.get("scope", scope.get("instrumentationLibrary")))}, trace)
            return
    for key in ("spans", "data", "result", "results", "trace"):
        if isinstance(value.get(key), (dict, list)):
            _collect(value[key], _child(origin, "/" + key), output, unassigned, context, trace)
            return
    unassigned.append({"reason": "no_supported_span_identity", "source": deepcopy(origin), "keys": list(value)})


def _normal_message(message, origin, gaps):
    if not isinstance(message, dict) or not isinstance(message.get("role"), str):
        gaps.append({"kind": "message_without_role", "source": origin})
        return None
    role, blocks = "tool" if message["role"] == "toolResult" else message["role"], []
    original = message.get("parts", message.get("content", ""))
    container = "parts" if "parts" in message else "content"
    original = [{"type": "text", "text": original}] if isinstance(original, str) else original
    if not isinstance(original, list):
        gaps.append({"kind": "unsupported_message_content", "source": origin})
        original = [{"type": "uninterpreted", "value": deepcopy(original)}]
    for i, block in enumerate(original):
        ref = _child(origin, f"/{container}/{i}") if isinstance(message.get(container), list) else _child(origin, "/" + container)
        if not isinstance(block, dict):
            blocks.append({"type": "uninterpreted", "value": deepcopy(block), "source": ref})
            continue
        kind = block.get("type", "uninterpreted")
        if kind in ("text", "input_text", "output_text", "code"):
            text = block.get("text", block.get("content", block.get("code")))
            if isinstance(text, str):
                blocks.append({"type": "text", "text": text, "original_type": kind, "source": ref})
                continue
        if kind in ("tool_call", "toolCall", "function_call"):
            call = block.get("id", block.get("call_id", block.get("toolCallId")))
            name = block.get("name", block.get("toolName"))
            arguments, _ = _json(block.get("arguments"))
            blocks.append({"type": "toolCall", "id": call, "name": name, "arguments": arguments,
                           "arguments_recorded": "arguments" in block, "arguments_source": _child(ref, "/arguments"), "source": ref})
            continue
        if kind in ("tool_call_response", "toolResult", "function_call_output"):
            response_key = next((key for key in ("response", "result", "output") if key in block), "response")
            blocks.append({"type": "toolResult", "id": block.get("id", block.get("call_id", block.get("toolCallId"))),
                           "name": block.get("name", block.get("toolName")),
                           "response": _response(block.get(response_key)), "response_source": _child(ref, "/" + response_key),
                           "response_recorded": response_key in block,
                           "source": ref})
            continue
        blocks.append({**deepcopy(block), "type": str(kind), "source": ref})
    for i, call in enumerate(message.get("tool_calls", [])):
        if not isinstance(call, dict):
            continue
        function = call.get("function", {})
        if not isinstance(function, dict):
            continue
        arguments, _ = _json(function.get("arguments"))
        blocks.append({"type": "toolCall", "id": call.get("id"), "name": function.get("name"),
                       "arguments": arguments, "arguments_recorded": "arguments" in function,
                       "arguments_source": _child(origin, f"/tool_calls/{i}/function/arguments"),
                       "source": _child(origin, f"/tool_calls/{i}")})
    if role in ("tool", "toolResult") and isinstance(message.get("tool_call_id", message.get("toolCallId")), str):
        blocks.append({"type": "toolResult", "id": message.get("tool_call_id", message.get("toolCallId")),
                       "name": message.get("name", message.get("toolName")),
                       "response": _response(message.get("content")), "response_source": _child(origin, "/content"),
                       "response_recorded": "content" in message, "source": origin})
    text = "\n".join(block["text"] for block in blocks if block["type"] == "text")
    return {"role": role, "text": text, "blocks": blocks, "source": deepcopy(origin),
            "raw_message": deepcopy(message), "finish_reason": message.get("finish_reason", message.get("stopReason")),
            "scope": "injected_runtime_context" if text.startswith("<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>") else "recorded_message"}


def _message_key(message):
    calls = [block.get("id") for block in message["blocks"] if block["type"] == "toolCall"]
    responses = [block.get("id") for block in message["blocks"] if block["type"] == "toolResult"]
    if calls or responses:
        # The same recorded call may appear in OTel parts and vendor-native
        # content, with complementary nontext blocks. Keep one message and all
        # source versions rather than claiming those snapshots executed twice.
        return digest({"role": message["role"], "calls": calls, "responses": responses})
    blocks = [_block_value(block) for block in message["blocks"]]
    # OTel's public-message view omits the vendor's empty redacted reasoning
    # marker. That marker carries no second response content: use the visible
    # blocks for identity, while _history keeps both original source references
    # and the redaction marker itself. Actual nonempty reasoning remains distinct.
    blocks = [block for block in blocks if not
              (block.get("type") in {"reasoning", "thinking"}
               and block.get("redacted") is True
               and set(block) <= {"type", "redacted"})]
    return digest({"role": message["role"], "blocks": blocks})


def _snapshots(records, gaps):
    snapshots = []
    for record in records:
        attrs = record["attrs"]
        for direction, keys in (("input", INPUT_KEYS), ("output", OUTPUT_KEYS)):
            for key in keys:
                for candidate in attrs.get(key, []):
                    value, encoded = _json(candidate["value"])
                    origin = deepcopy(candidate["source"])
                    if encoded:
                        origin.update(decoded_json_pointer="", encoding="json_string")
                    if isinstance(value, dict) and isinstance(value.get("messages"), list):
                        value, origin = value["messages"], _child(origin, "/messages")
                    if not isinstance(value, list):
                        gaps.append({"kind": "unreadable_model_messages", "attribute": key, "source": origin})
                        continue
                    messages = [_normal_message(message, _child(origin, "/" + str(i)), gaps) for i, message in enumerate(value)]
                    snapshots.append({"direction": direction, "span_id": record["span_id"],
                                      "messages": [message for message in messages if message is not None], "source": origin})
    return snapshots


def _history(snapshots, gaps):
    """Union repeated prompt snapshots, retaining per-occurrence references.

    Choose the richest recorded input as the display backbone. Snapshot copies
    are not counted as newly executed messages. No native sequence is inferred.
    """
    inputs = [snapshot for snapshot in snapshots if snapshot["direction"] == "input"]
    base = max(inputs or snapshots, key=lambda item: len(item["messages"]), default={"messages": [], "span_id": None})
    ordered, seen, copies = [], {}, []
    def add(snapshot):
        counts = defaultdict(int)
        for message in snapshot["messages"]:
            key = _message_key(message)
            occurrence = counts[key]
            counts[key] += 1
            identity = (key, occurrence)
            reference = {"source": message["source"], "model_span_id": snapshot.get("span_id"), "direction": snapshot.get("direction")}
            if identity in seen:
                target = seen[identity]
                target["observed_at"].append(reference)
                known_blocks = {digest(_block_value(block)): block for block in target["blocks"]}
                for block in message["blocks"]:
                    block_key = digest(_block_value(block))
                    if block_key not in known_blocks:
                        target["blocks"].append(deepcopy(block))
                        known_blocks[block_key] = target["blocks"][-1]
                    else:
                        original = known_blocks[block_key]
                        original["source_refs"] = _unique_refs(original.get("source_refs", [original["source"]]) + [block["source"]])
                target["text"] = "\n".join(block["text"] for block in target["blocks"] if block["type"] == "text")
                copies.append(reference)
            else:
                item = deepcopy(message)
                item.update(observed_at=[reference], identity=key + ":" + str(occurrence))
                ordered.append(item)
                seen[identity] = item
    add(base)
    # Its output follows the selected input; other snapshots add only genuinely
    # unrepresented content, with explicit uncertain display ordering.
    others = sorted(snapshots, key=lambda item: (item.get("span_id") != base.get("span_id"), item["direction"] != "output", item.get("span_id") or ""))
    for snapshot in others:
        if snapshot is not base:
            add(snapshot)
    if len({snapshot["span_id"] for snapshot in snapshots}) > 1:
        gaps.append({"kind": "snapshot_display_order", "note": "Display uses the richest recorded input plus observed outputs. Snapshot membership is retained; this is not a recovered native execution sequence."})
    return ordered, copies


def _candidates(attrs, keys):
    result = []
    for key in keys:
        for item in attrs.get(key, []):
            value, encoded = _json(item["value"])
            ref = deepcopy(item["source"])
            if encoded:
                ref.update(decoded_json_pointer="", encoding="json_string")
            result.append({"value": value, "source": ref})
    return result


def _resolve(candidates, kind, call, gaps):
    values = {digest(item["value"]): item["value"] for item in candidates}
    if not values:
        gaps.append({"kind": "missing_" + kind, "call_id": call})
        return None, [], False
    if len(values) > 1:
        gaps.append({"kind": "conflicting_" + kind, "call_id": call,
                     "candidates": deepcopy(candidates)})
        return None, _unique_refs([item["source"] for item in candidates]), True
    return next(iter(values.values())), _unique_refs([item["source"] for item in candidates]), False


def _status(raw):
    status = raw.get("status", {})
    code = status.get("code") if isinstance(status, dict) else None
    explicit = _pick(raw, ("status_code_string", "statusCodeString", "statusCode", "status_code"), code)
    if (not isinstance(explicit, bool) and explicit in ("Error", "ERROR", "STATUS_CODE_ERROR", 2)) or raw.get("has_error", raw.get("hasError")) is True:
        return "error"
    if not isinstance(explicit, bool) and explicit in ("Ok", "OK", "STATUS_CODE_OK", 1):
        return "ok"
    return "unset"


def _build(trace, records, preserved, global_unassigned, task_override):
    gaps, duplicates, conflicts = [], [], []
    declarations = [deepcopy(record["raw"]["prodebugger_input_provenance"]) for record in records
                    if isinstance(record["raw"].get("prodebugger_input_provenance"), dict)]
    constructed = any(item.get("kind") == "offline_constructed_copy" and item.get("executed") is False for item in declarations)
    source_kind = "constructed_variant_from_signoz_export" if constructed else "signoz_cloud_export"
    by_span = defaultdict(list)
    for record in records:
        record["attrs"] = _attributes(record["raw"], record["source"], gaps)
        by_span[record["span_id"]].append(record)
    for span, copies in by_span.items():
        fingerprints = defaultdict(list)
        for record in copies:
            fingerprints[digest(record["raw"])].append(record["source"])
        for refs in fingerprints.values():
            if len(refs) > 1:
                duplicates.append({"kind": "duplicate_span_record", "span_id": span, "sources": refs})
        all_attrs = defaultdict(list)
        for record in copies:
            for key, values in record["attrs"].items():
                all_attrs[key].extend(values)
        for key, values in all_attrs.items():
            if len({digest(_json(item["value"])[0]) for item in values}) > 1:
                conflicts.append({"kind": "conflicting_span_attribute", "span_id": span, "attribute": key,
                                  "candidates": deepcopy(values)})
    snapshots = _snapshots(records, gaps)
    history, snapshot_copies = _history(snapshots, gaps)
    calls = {}
    def call_record(call, name=None):
        if not isinstance(call, str) or not call:
            return None
        item = calls.setdefault(call, {"call_id": call, "names": [], "arguments": [], "results": [],
                                      "cloud_span_ids": set(), "model_span_ids": set(), "sources": [], "statuses": [],
                                      "recorded_model_call": False})
        if isinstance(name, str) and name:
            item["names"].append(name)
        return item
    for message in history:
        for block in message["blocks"]:
            if block["type"] not in ("toolCall", "toolResult"):
                continue
            item = call_record(block.get("id"), block.get("name"))
            if item is None:
                gaps.append({"kind": "tool_block_without_call_id", "source": block["source"]})
                continue
            item["sources"].append(block["source"])
            item["model_span_ids"].update(ref["model_span_id"] for ref in message["observed_at"])
            if block["type"] == "toolCall":
                item["recorded_model_call"] = True
                if block.get("arguments_recorded"):
                    item["arguments"].append({"value": block["arguments"], "source": block.get("arguments_source", _child(block["source"], "/arguments"))})
            if block["type"] == "toolResult" and block.get("response_recorded"):
                item["results"].append({"value": block.get("response"), "source": block.get("response_source", block["source"])})
    for record in records:
        attrs = record["attrs"]
        ids = attrs.get("gen_ai.tool.call.id", [])
        names = attrs.get("gen_ai.tool.name", attrs.get("openclaw.toolName", []))
        if not ids and names:
            # A real tool span can lack a toolCall ID; use a visibly analytical
            # call identity while retaining its real cloud span independently.
            ids = [{"value": "analysis-span-call-" + record["span_id"], "source": record["source"]}]
            gaps.append({"kind": "missing_recorded_tool_call_id", "span_id": record["span_id"]})
        for entry in ids:
            item = call_record(entry["value"], names[0]["value"] if names else None)
            if item is None:
                gaps.append({"kind": "invalid_recorded_tool_call_id", "source": entry["source"]})
                continue
            item["cloud_span_ids"].add(record["span_id"])
            item["sources"].append(entry["source"])
            item["arguments"].extend(_candidates(attrs, ARG_KEYS))
            item["results"].extend(_candidates(attrs, RESULT_KEYS))
            item["statuses"].append(_status(record["raw"]))
    for call, item in calls.items():
        names = set(item["names"])
        item["tool"] = next(iter(names)) if len(names) == 1 else "unknown_tool"
        if len(names) != 1:
            gaps.append({"kind": "conflicting_tool_names" if names else "missing_tool_name", "call_id": call, "names": sorted(names)})
        item["argument_value"], item["argument_sources"], item["arguments_conflict"] = _resolve(item["arguments"], "tool_arguments", call, gaps)
        item["result_value"], item["result_sources"], item["result_conflict"] = _resolve(item["results"], "tool_result", call, gaps)
        spans = sorted(item["cloud_span_ids"])
        item["span_id"] = spans[0] if len(spans) == 1 else "analysis-call-" + digest({"trace": trace, "call": call})[:20]
        item["span_identity_kind"] = "recorded_cloud_span" if len(spans) == 1 else "analysis_call_id_not_cloud_span"
        if len(spans) > 1:
            gaps.append({"kind": "ambiguous_cloud_span_for_call", "call_id": call, "span_ids": spans})
        states = set(item["statuses"]) - {"unset"}
        item["status"] = next(iter(states)) if len(states) == 1 and not item["result_conflict"] else "unset"
        if not states and isinstance(item["result_value"], dict) and type(item["result_value"].get("isError")) is bool:
            item["status"] = "error" if item["result_value"]["isError"] else "ok"
        if len(states) > 1:
            gaps.append({"kind": "conflicting_tool_status", "call_id": call, "statuses": sorted(states)})
        # OpenClaw explicitly encodes the parent call in this nested runtime ID.
        parents = [parent for parent in calls if parent != call and call.startswith("tool_call:" + parent + ":")]
        item["parent_call_id"] = max(parents, key=len) if parents else None
    task_candidates = [message for message in history if message["role"] == "user" and message["text"].strip() and message["scope"] != "injected_runtime_context"]
    if task_override is not None and (not isinstance(task_override, str) or not task_override.strip()):
        raise ValueError("task override must be a nonempty string")
    selected = task_candidates[0] if task_candidates else None
    task = task_override if task_override is not None else selected["text"] if selected else "[未提供原始用户任务；仅保留已导出的 SigNoz 记录，无法核验任务是否完成]"
    task_source = {"status": "explicit_user_override" if task_override is not None else "recorded_user_message" if selected else "missing",
                   "source": deepcopy(selected["source"]) if selected and task_override is None else None,
                   "selection": "first non-runtime-context recorded user message in the selected snapshot",
                   "candidate_count": len(task_candidates), "semantic_requirements_verified": False}
    if len(task_candidates) > 1:
        gaps.append({"kind": "multiple_user_task_candidates", "sources": [message["source"] for message in task_candidates],
                     "note": "The first recorded user message is displayed as the task; distinct later user messages remain visible. No task boundary is inferred."})
    if selected is None and task_override is None:
        gaps.append({"kind": "missing_recorded_user_task"})
    events, steps, mapping, emitted_calls, emitted_results = [], [], [], {}, {}
    def append(event, observation, action=None):
        event["step"] = len(steps) + 1
        event.setdefault("original_seq", None)
        event.setdefault("timestamp", None)
        events.append(event)
        action_text = _encoded(action) if action is not None else None
        steps.append({"t": event["step"], "env_text": _encoded(observation), "agent_text": action_text or "", "admissible": [],
                      "parts": {"memory": None, "reflection": None, "plan": None, "action": action_text}})
    def provenance(item, result=False):
        refs = item["result_sources"] if result else item["argument_sources"]
        return {"tool_call_id": item["call_id"], "source_kind": source_kind, "span_identity_kind": item["span_identity_kind"],
                "recorded_cloud_span_ids": sorted(item["cloud_span_ids"]), "recorded_model_span_ids": sorted(item["model_span_ids"]),
                "source_refs": deepcopy(refs or item["sources"]), "arguments_recorded": bool(item["arguments"]),
                "call_evidence_kind": "recorded_model_tool_call" if item["recorded_model_call"] else "recorded_tool_span" if item["cloud_span_ids"] else "result_only_call_reference",
                "arguments_conflict": item["arguments_conflict"], "result_conflict": item["result_conflict"],
                "parent_call_id": item["parent_call_id"],
                "parent_binding": "explicit_openclaw_tool_call_id_namespace" if item["parent_call_id"] else None}
    def emit_result(item):
        call = item["call_id"]
        if call in emitted_results or not item["results"]:
            return
        # An observed response with unknown status remains unset, never success.
        origin = (item["result_sources"] or item["sources"] or [{}])[0]
        envelope = {"type": "tool_result", "trace_id": trace, "span_id": item["span_id"], "tool": item["tool"],
                    "status": item["status"], "result": deepcopy(item["result_value"]), "provenance": provenance(item, True)}
        event = {"event_id": "result:" + digest(call)[:20], "type": "tool_result", "role": "tool_runtime",
                 "trace_id": trace, "span_id": item["span_id"], "call_id": call, "tool": item["tool"],
                 "parent_call_id": item["parent_call_id"], "invocation_layer": "nested_tool" if item["parent_call_id"] else "recorded_call",
                 "source": deepcopy(origin), "source_refs": deepcopy(item["result_sources"]),
                 "text": "", "content": deepcopy(item["result_value"]), "result": deepcopy(item["result_value"]),
                 "status": item["status"], "span_identity_kind": item["span_identity_kind"]}
        append(event, envelope)
        emitted_results[call] = event
    def emit_call(item):
        call = item["call_id"]
        if call in emitted_calls:
            return
        origin = (item["argument_sources"] or item["sources"] or [{}])[0]
        envelope = {"type": "tool_call", "trace_id": trace, "span_id": item["span_id"], "tool": item["tool"],
                    "arguments": deepcopy(item["argument_value"]), "provenance": provenance(item)}
        event = {"event_id": "call:" + digest(call)[:20], "type": "tool_call", "role": "assistant" if item["recorded_model_call"] else "tool_runtime",
                 "trace_id": trace, "span_id": item["span_id"], "call_id": call, "tool": item["tool"],
                 "arguments": deepcopy(item["argument_value"]), "parent_call_id": item["parent_call_id"],
                 "invocation_layer": "nested_tool" if item["parent_call_id"] else "recorded_call",
                 "source": deepcopy(origin), "source_refs": deepcopy(item["argument_sources"]), "text": "", "content": deepcopy(envelope),
                 "span_identity_kind": item["span_identity_kind"]}
        append(event, {"type": "tool_context", "trace_id": trace, "provenance": provenance(item)}, envelope)
        emitted_calls[call] = event
        for child in sorted(calls.values(), key=lambda entry: entry["call_id"]):
            if child["parent_call_id"] == call:
                emit_call(child)
                emit_result(child)
    for message in history:
        content = [{key: deepcopy(value) for key, value in _block_value(block).items() if key not in ("arguments_recorded", "response_recorded")} for block in message["blocks"]]
        event_id = "message:" + digest(message["identity"])[:20]
        event = {"event_id": event_id, "type": "session_message", "role": message["role"], "trace_id": trace,
                 "text": message["text"], "content": content, "source": message["source"],
                 "source_refs": [ref["source"] for ref in message["observed_at"]], "observed_at": message["observed_at"],
                 "message_scope": message["scope"], "stop_reason": message["finish_reason"], "raw_message": message["raw_message"]}
        append(event, {"type": "session_message", "trace_id": trace, "event_id": event_id, "role": message["role"],
                       "text": message["text"], "content": content, "provenance": message["source"]})
        for block in message["blocks"]:
            item = calls.get(block.get("id")) if block["type"] in ("toolCall", "toolResult") else None
            if item is not None:
                if block["type"] == "toolCall":
                    emit_call(item)
                else:
                    if item["call_id"] not in emitted_calls:
                        gaps.append({"kind": "result_without_recorded_call_message", "call_id": item["call_id"]})
                        emit_call(item)
                    emit_result(item)
    for item in sorted(calls.values(), key=lambda entry: entry["call_id"]):
        emit_call(item)
        emit_result(item)
    for call, event in emitted_calls.items():
        item, result = calls[call], emitted_results.get(call)
        mapping.append({"trace_id": trace, "span_id": item["span_id"], "tool_call_id": call, "tool": item["tool"],
                        "cloud_span_id": item["span_id"] if item["span_identity_kind"] == "recorded_cloud_span" else None,
                        "span_identity_kind": item["span_identity_kind"], "model_span_ids": sorted(item["model_span_ids"]),
                        "parent_call_id": item["parent_call_id"], "action_step": event["step"], "result_step": result["step"] if result else None,
                        "action_original_seq": None, "result_original_seq": None,
                        "action_source": event["source"], "result_source": result["source"] if result else None,
                        "action_event_id": event["event_id"], "result_event_id": result["event_id"] if result else None,
                        "arguments_sources": item["argument_sources"], "result_sources": item["result_sources"]})
    if not steps:
        # Keep summary-only traces in the case list. This is an explicit context
        # marker, not an invented user message, tool call or execution result.
        origin = records[0]["source"]
        append({"event_id": "summary:" + trace, "type": "trace_context", "role": "telemetry", "trace_id": trace,
                "text": "", "content": {}, "source": origin},
               {"type": "tool_context", "trace_id": trace, "provenance": {**origin, "scope": "summary_only_no_recorded_messages_or_tools"}})
        gaps.append({"kind": "summary_export_without_content", "note": "Trace retained, but no message or tool-content evidence was supplied. Export span details with gen_ai.* content before semantic diagnosis."})
    unsupported = [{"event_id": event["event_id"], "block": i, "type": block["type"], "status": "preserved_not_semantically_interpreted"}
                   for event in events if event["type"] == "session_message" for i, block in enumerate(event["content"])
                   if block["type"] not in ("text", "toolCall", "toolResult")]
    coverage = {"cloud_span_records": len(records), "cloud_spans": len(by_span), "model_message_snapshots": len(snapshots),
                "observed_snapshot_message_occurrences": sum(len(snapshot["messages"]) for snapshot in snapshots),
                "represented_messages": len(history), "repeated_snapshot_message_copies": len(snapshot_copies),
                "recorded_messages_complete": bool(snapshots) and not any(gap["kind"] in ("message_without_role", "unreadable_model_messages") for gap in gaps),
                "capture_complete_claim": None, "native_transcript_available": False,
                "tool_calls": len(calls), "matched_tool_results": len(emitted_results), "matched_tool_calls": len(calls),
                "analysis_call_identities": sum(item["span_identity_kind"] != "recorded_cloud_span" for item in calls.values()),
                "tool_join_complete": len(emitted_results) == len(calls), "semantic_parse_complete": False,
                "task_recorded": selected is not None, "uninterpreted_content_blocks": unsupported, "gaps": gaps,
                "scope": "Representation of content in the supplied cloud export snapshots; not a native transcript or complete capture claim.",
                "ordering": "richest_model_input_snapshot_plus_observed_outputs_and_exact_call_id_joins; no_native_sequence_reconstructed"}
    metadata = [{"type": "recorded_cloud_span", "span_id": record["span_id"], "source": record["source"],
                 "content": record["raw"], "resource_scope_context": record["context"]} for record in records]
    metadata += [{"type": "unassigned_export_record", **item} for item in global_unassigned]
    tid = "signoz-" + (trace.lower() if re.fullmatch(r"[a-fA-F0-9]{32}", trace) else digest(trace)[:24])
    source = {"trajectory_id": valid_id(tid), "task_type": "openclaw", "task": task, "steps": steps,
              "telemetry": {"trace_id": trace, "session_id": "signoz-trace:" + trace, "source_map": mapping,
                            "data_origin": source_kind, "task_source": task_source,
                            "source_kind": "recorded_cloud_spans_and_model_message_snapshots"},
              "session": {"schema": VERSION, "session_id": "signoz-trace:" + trace,
                          "source_label": "基于 SigNoz 真实导出构造的分析变体（未执行）" if constructed else "SigNoz 云端原始导出（模型消息快照＋工具 Span；非原生 transcript）",
                          "source_kind": source_kind, "source_declarations": declarations, "raw_documents": deepcopy(preserved),
                          "task_override": task_override, "events": events, "metadata": metadata, "coverage": coverage,
                          "duplicates": duplicates, "conflicts": conflicts,
                          "step_numbering": "step is an imported analysis-event index; original_seq is unavailable. Cloud span IDs and model snapshot positions remain independent provenance."}}
    audit = {"schema": AUDIT_VERSION, "status": "verified_with_limitations" if gaps or conflicts else "verified",
             "trace_id": trace, "documents": [{key: document[key] for key in ("name", "sha256", "format")} for document in preserved],
             "output_sha256": digest(source), "coverage": deepcopy(coverage), "duplicates": duplicates, "conflicts": conflicts,
             "task_source": task_source, "source_kind": source_kind,
             "limitations": ["Model prompt snapshots are not a native Agent transcript or proof of full capture.",
                             "Analysis identities never claim a cloud span existed.",
                             "Missing arguments, returns, task context and conflicting values remain unknown.",
                             "No unrecorded thinking, memory, reflection, plan or causal decision chain is reconstructed."]}
    return {"source": source, "audit": audit, "title": "SigNoz · " + trace[:12] + (" · 摘要缺内容" if not history and not calls else "")}


def import_documents(documents, task=None):
    """Import original ``[{name,text}]`` documents, one case per real trace ID."""
    preserved, roots = _documents(documents)
    records, unassigned = [], []
    for value, origin in roots:
        _collect(value, origin, records, unassigned)
    if not records:
        raise ValueError("No supported trace/span identity found. Supply SigNoz span-detail/trace JSON(L) or OTLP resourceSpans, not a logs/table-only summary.")
    groups = defaultdict(list)
    for record in records:
        groups[record["trace_id"]].append(record)
    return [_build(trace, records, preserved, unassigned, task) for trace, records in sorted(groups.items())]


def validate_signoz_source(source):
    """Replay raw cloud documents; verifies consistency, not cloud authenticity."""
    if not isinstance(source, dict) or source.get("session", {}).get("schema") != VERSION:
        raise ValueError("expected a preserved SigNoz session source")
    session = source["session"]
    trace = source.get("telemetry", {}).get("trace_id")
    for result in import_documents(session.get("raw_documents"), task=session.get("task_override")):
        expected = result["source"]
        if expected["telemetry"]["trace_id"] != trace:
            continue
        expected["trajectory_id"] = valid_id(source.get("trajectory_id"))
        if digest(expected) != digest(source):
            raise ValueError("SigNoz canonical source differs from preserved raw-document replay")
        return {**result["audit"], "output_sha256": digest(source), "validation": "canonical_signoz_document_replay",
                "authenticity": "requires_independent_cloud_export_receipt"}
    raise ValueError("preserved cloud documents do not contain the declared trace")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--task-file", type=Path)
    args = parser.parse_args(argv)
    documents = [{"name": path.name, "text": path.read_bytes().decode("utf-8")} for path in args.files]
    cases = import_documents(documents, task=args.task_file.read_text(encoding="utf-8-sig") if args.task_file else None)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for case in cases:
        folder = args.output_dir / case["source"]["trajectory_id"]
        folder.mkdir(exist_ok=True)
        for name in ("source", "audit"):
            (folder / (name + ".json")).write_text(json.dumps(case[name], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(str(folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
