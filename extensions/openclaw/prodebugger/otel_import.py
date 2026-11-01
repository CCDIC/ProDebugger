"""Inspect/import a single sequential OpenClaw trace from local OTLP JSON.

No SigNoz credentials or network access are needed. Native content attributes are
optional in OpenClaw; missing content is reported rather than reconstructed.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .record_bridge import digest, read_json, write_json, valid_id
from .openclaw_contract import validate as validate_contract

VERSION = "openclaw-otel-import/v1"
CONTENT_INPUT = ("gen_ai.tool.call.arguments", "openclaw.content.tool_input")
CONTENT_OUTPUT = ("gen_ai.tool.call.result", "openclaw.content.tool_output")


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key: " + key)
        value[key] = item
    return value


def loads(text):
    def bad_number(value):
        raise ValueError("non-finite JSON number: " + value)
    return json.loads(text, object_pairs_hook=_unique, parse_constant=bad_number)


def load_export(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    if Path(path).suffix.lower() == ".jsonl":
        return [loads(line) for line in text.splitlines() if line.strip()]
    return loads(text)


def _value(value):
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "doubleValue", "bytesValue",
                "string_value", "bool_value", "double_value", "bytes_value"):
        if key in value:
            return value[key]
    for key in ("intValue", "int_value"):
        if key in value:
            return int(value[key])
    for key in ("arrayValue", "array_value"):
        if key in value:
            container = value[key]
            if not isinstance(container, dict) or not isinstance(container.get("values", []), list):
                raise ValueError("OTLP arrayValue requires a values array")
            return [_value(v) for v in container.get("values", [])]
    for key in ("kvlistValue", "kvlist_value"):
        if key in value:
            container = value[key]
            if not isinstance(container, dict):
                raise ValueError("OTLP kvlistValue must be an object")
            return _attributes(container.get("values", []))
    raise ValueError("unsupported OTLP attribute value")


def _attributes(rows):
    if isinstance(rows, dict):
        return rows
    if not isinstance(rows, list):
        raise ValueError("attributes must be an OTLP list or decoded object")
    result = {}
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or "value" not in item:
            raise ValueError("OTLP attributes require string keys and values")
        key = item["key"]
        if key in result:
            raise ValueError("duplicate OTLP attribute: " + key)
        result[key] = _value(item["value"])
    return result


def _field(item, camel, snake=None, default=None):
    return item.get(camel, item.get(snake or camel, default))


def _documents(data):
    """Unwrap OTLP documents and the common SigNoz trace API envelopes.

    SigNoz's trace endpoint returns rows such as ``traceID``/``spanID`` in a
    ``data`` (or ``result``) wrapper, while an OTLP export uses
    ``resourceSpans``.  Keep the wrapper handling deliberately narrow so an
    arbitrary ``data`` attribute cannot be mistaken for a trace.
    """
    if isinstance(data, list):
        return [document for item in data for document in _documents(item)]
    if not isinstance(data, dict):
        raise ValueError("each OTLP document must be an object")
    if any(key in data for key in ("resourceSpans", "resource_spans", "spans",
                                   "traceId", "trace_id", "traceID", "spanID",
                                   "spanId", "span_id", "operationName")):
        return [data]
    for wrapper in ("data", "result"):
        if wrapper in data:
            return _documents(data[wrapper])
    raise ValueError("expected OTLP resourceSpans, SigNoz data/result, spans, or a span object")


def _integer(value, field):
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(field + " must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(field + " must be an integer") from None
    if isinstance(value, str) and str(parsed) != value.strip():
        raise ValueError(field + " must be an integer")
    return parsed


def _signoz_tag_value(value):
    """Decode a SigNoz tag value without inventing a value for unknown shapes."""
    if not isinstance(value, dict):
        return value
    # SigNoz API rows use one of these scalar keys; retain nested values when
    # they are already decoded so the raw row remains the authoritative source.
    for key in ("string", "stringValue", "bool", "boolValue", "int64", "intValue",
                "float64", "doubleValue"):
        if key in value:
            return value[key]
    for key in ("array", "arrayValue"):
        if key in value:
            items = value[key]
            if isinstance(items, dict):
                items = items.get("values", [])
            if isinstance(items, list):
                return [_signoz_tag_value(item) for item in items]
    return value


def _signoz_tags(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        # Some API versions return {key: value}, others return an object whose
        # entries are the OTLP-like {string: ...} values.
        return {str(key): _signoz_tag_value(item) for key, item in value.items()}
    if not isinstance(value, list):
        raise ValueError("SigNoz tags must be an object or list")
    attrs = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise ValueError("SigNoz tags require string keys")
        key = item["key"]
        if key in attrs:
            raise ValueError("duplicate SigNoz tag: " + key)
        attrs[key] = _signoz_tag_value(item.get("value"))
    return attrs


def _is_signoz_span(raw):
    return isinstance(raw, dict) and any(key in raw for key in
        ("traceID", "spanID", "parentSpanID", "operationName", "durationNano", "duration_nano", "tags"))


def _signoz_to_otlp(raw):
    """Normalize a SigNoz trace row to the small OTLP span shape we consume.

    SigNoz trace rows expose ``startTime`` and ``durationNano`` as epoch
    nanoseconds.  We require both and compute the end timestamp arithmetically;
    no status, tool content, or error is synthesized from a missing field.
    """
    tid = raw.get("traceID", raw.get("traceId", raw.get("trace_id")))
    sid = raw.get("spanID", raw.get("spanId", raw.get("span_id")))
    parent = raw.get("parentSpanID", raw.get("parentSpanId", raw.get("parent_span_id", "")))
    begin = raw.get("startTime", raw.get("timestamp", raw.get("startTimeUnixNano", raw.get("start_time_unix_nano"))))
    duration = raw.get("durationNano", raw.get("duration_nano"))
    end = raw.get("endTimeUnixNano", raw.get("end_time_unix_nano"))
    if begin is None:
        raise ValueError("SigNoz span requires startTime")
    begin = _integer(begin, "SigNoz startTime")
    if duration is not None:
        duration = _integer(duration, "SigNoz durationNano")
        if duration < 0:
            raise ValueError("SigNoz durationNano must be nonnegative")
        computed_end = begin + duration
        if end is not None and _integer(end, "SigNoz endTimeUnixNano") != computed_end:
            raise ValueError("SigNoz endTime conflicts with durationNano")
        end = computed_end
    elif end is not None:
        end = _integer(end, "SigNoz endTimeUnixNano")
    else:
        raise ValueError("SigNoz span requires durationNano or endTimeUnixNano")
    attrs = _signoz_tags(raw.get("tags", raw.get("attributes", {})))
    # Keep serviceName as resource information only.  hasError/statusCode are
    # copied as explicit metadata and interpreted by the audit layer; neither
    # creates tool content or an inferred task result.
    resource = raw.get("resource", {})
    if not isinstance(resource, dict):
        raise ValueError("SigNoz resource must be an object")
    resource = dict(resource)
    if isinstance(raw.get("serviceName"), str) and raw["serviceName"]:
        resource["service.name"] = raw["serviceName"]
    for camel, snake in (("hasError", "has_error"), ("statusCode", "status_code"),
                         ("statusMessage", "status_message")):
        if camel in raw or snake in raw:
            attrs["signoz." + camel] = raw.get(camel, raw.get(snake))
    normalized = dict(raw)
    normalized.update({"traceId": tid, "spanId": sid, "parentSpanId": parent or "",
                       "name": raw.get("operationName", raw.get("name", "")),
                       "startTimeUnixNano": begin, "endTimeUnixNano": end,
                       "attributes": attrs, "resource": resource})
    status = raw.get("status")
    if status is not None:
        normalized["status"] = status
    return normalized


def _export_gaps(data):
    """Keep API pagination/missing-span declarations as capture evidence."""
    if isinstance(data, list):
        return sorted({gap for item in data for gap in _export_gaps(item)})
    if not isinstance(data, dict):
        return []
    gaps = []
    if data.get("hasMissingSpans") is True or data.get("has_missing_spans") is True:
        gaps.append("signoz_missing_spans")
    if any(data.get(key) for key in ("nextCursor", "next_cursor", "prevCursor", "prev_cursor")):
        gaps.append("signoz_paginated_response")
    if "spans" in data and isinstance(data["spans"], list):
        count = data.get("totalSpansCount", data.get("total_spans_count"))
        if count is not None and _integer(count, "SigNoz totalSpansCount") > len(data["spans"]):
            gaps.append("signoz_partial_span_list")
    for key in ("data", "result"):
        if key in data:
            gaps.extend(_export_gaps(data[key]))
    return sorted(set(gaps))


def spans_from_export(data):
    """Support OTLP resourceSpans/scopeSpans JSON and decoded spans lists."""
    documents = _documents(data)
    export_gaps = _export_gaps(data)
    result = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("each OTLP document must be an object")
        resources = _field(document, "resourceSpans", "resource_spans")
        if resources is None:
            if "spans" in document:
                resources = [{"scopeSpans": [{"spans": document["spans"]}]}]
            elif any(key in document for key in ("traceId", "trace_id", "traceID", "spanID",
                                                 "spanId", "span_id", "operationName")):
                resources = [{"scopeSpans": [{"spans": [document]}]}]
            else:
                raise ValueError("expected OTLP resourceSpans, spans, or a span object")
        if not isinstance(resources, list):
            raise ValueError("resourceSpans must be an array")
        for resource in resources:
            if not isinstance(resource, dict):
                raise ValueError("each resourceSpans entry must be an object")
            resource_info = resource.get("resource", {})
            if not isinstance(resource_info, dict):
                raise ValueError("OTLP resource must be an object")
            resource_attrs = _attributes(resource_info.get("attributes", []))
            scopes = _field(resource, "scopeSpans", "scope_spans", [])
            if not isinstance(scopes, list):
                raise ValueError("scopeSpans must be an array")
            if not scopes:
                scopes = resource.get("instrumentationLibrarySpans", [])
            if not isinstance(scopes, list):
                raise ValueError("scopeSpans must be an array")
            for scope in scopes:
                if not isinstance(scope, dict):
                    raise ValueError("each scopeSpans entry must be an object")
                span_rows = scope.get("spans", [])
                if not isinstance(span_rows, list):
                    raise ValueError("spans must be an array")
                for original in span_rows:
                    if not isinstance(original, dict):
                        raise ValueError("each span must be an object")
                    signoz_row = _is_signoz_span(original)
                    raw = _signoz_to_otlp(original) if signoz_row else original
                    span_resource_attrs = resource_attrs
                    if signoz_row and isinstance(raw.get("resource"), dict):
                        span_resource_attrs = {**resource_attrs, **raw["resource"]}
                    tid_value = _field(raw, "traceId", "trace_id", "")
                    sid_value = _field(raw, "spanId", "span_id", "")
                    parent_value = _field(raw, "parentSpanId", "parent_span_id", "")
                    if not all(isinstance(value, str) for value in (tid_value, sid_value)
                               ) or (parent_value is not None and not isinstance(parent_value, str)):
                        raise ValueError("span IDs must be strings")
                    tid = tid_value.lower()
                    sid = sid_value.lower()
                    parent = (parent_value or "").lower()
                    if not re.fullmatch(r"[0-9a-f]{32}", tid) or int(tid, 16) == 0:
                        raise ValueError("traceId must be a nonzero 32-character hexadecimal ID")
                    if not re.fullmatch(r"[0-9a-f]{16}", sid) or int(sid, 16) == 0:
                        raise ValueError("spanId must be a nonzero 16-character hexadecimal ID")
                    if parent and not re.fullmatch(r"[0-9a-f]{16}", parent):
                        raise ValueError("parentSpanId must be 16 hexadecimal characters")
                    begin = _field(raw, "startTimeUnixNano", "start_time_unix_nano")
                    end = _field(raw, "endTimeUnixNano", "end_time_unix_nano")
                    if begin is None or end is None:
                        raise ValueError("spans require startTimeUnixNano and endTimeUnixNano")
                    begin = _integer(begin, "span startTimeUnixNano")
                    end = _integer(end, "span endTimeUnixNano")
                    if begin <= 0 or end < begin:
                        raise ValueError("invalid span timestamp order")
                    result.append({"trace_id": tid, "span_id": sid, "parent_span_id": parent,
                        "name": raw.get("name", ""), "start": begin, "end": end,
                        "attributes": _attributes(raw.get("attributes", [])),
                        "status": _status(raw.get("status", {})), "resource": span_resource_attrs,
                        "raw": original, "source_digest": digest(original), "export_gaps": export_gaps})
    if not result:
        raise ValueError("export contains no spans")
    seen, unique = {}, []
    for span in result:
        identity = (span["trace_id"], span["span_id"])
        if identity in seen:
            if seen[identity] != span["source_digest"]:
                raise ValueError("conflicting duplicate span IDs")
            continue  # OTLP retry deliveries do not create another execution.
        seen[identity] = span["source_digest"]
        unique.append(span)
    return unique


def _tool(span):
    attrs = span["attributes"]
    if (span["name"] == "openclaw.tool.execution"
            or attrs.get("gen_ai.operation.name") == "execute_tool"):
        value = attrs.get("gen_ai.tool.name", attrs.get("openclaw.toolName"))
        return value if isinstance(value, str) and value.strip() else ""
    return None


def _content(attrs, keys):
    for key in keys:
        if key in attrs:
            value = attrs[key]
            if isinstance(value, str):
                try:
                    return True, loads(value), key
                except (ValueError, TypeError):
                    pass  # Tool results can legitimately be plain text.
            return True, value, key
    return False, None, None


def _content_conflict(attrs, keys):
    values = [_content(attrs, (key,))[1] for key in keys if key in attrs]
    return bool(values) and any(digest(value) != digest(values[0]) for value in values[1:])


def _signoz_error(attrs):
    return (attrs.get("signoz.hasError") is True
            or attrs.get("signoz.statusCode") in
            (2, "2", "ERROR", "STATUS_CODE_ERROR"))


def _status(value):
    # OTLP JSON permits null for an absent optional message.
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("span status must be an object or null")
    code = value.get("code", 0)
    if (type(code) not in (str, int)
            or code not in (0, 1, 2, "0", "1", "2", "STATUS_CODE_UNSET", "UNSET",
                            "STATUS_CODE_OK", "OK", "STATUS_CODE_ERROR", "ERROR")):
        raise ValueError("unsupported span status code")
    if "message" in value and not isinstance(value["message"], str):
        raise ValueError("span status message must be a string")
    return value


def _truncated(value):
    if isinstance(value, dict):
        return (value.get("truncated") is True
                or any(v is True or (type(v) in (int, float) and v > 0)
                       for k, v in value.items() if k in {"omittedItems", "omittedFields"})
                or any(_truncated(v) for v in value.values()))
    if isinstance(value, list):
        return any(_truncated(v) for v in value)
    if isinstance(value, str):
        return (len(value.encode("utf-16-le")) // 2 >= 128 * 1024
                or any(marker in value.lower() for marker in ("[truncated]", "...(truncated)", "[redacted]"))
                or bool(re.search(r"\.\.\.\(\d+ more text parts omitted\)", value)))
    return False


def load_task(path=None, task=None):
    if path and task:
        raise ValueError("use --task-file or --task, not both")
    value = loads(Path(path).read_text(encoding="utf-8-sig")) if path else {
        "schema": "openclaw-task/v1", "task": task or "",
        "capture": {"complete": False, "reason": "No completeness declaration supplied."}}
    if not isinstance(value, dict) or set(value) - {"schema", "task", "capture", "contract"}:
        raise ValueError("task file supports only schema, task, capture and optional contract")
    if value.get("schema") != "openclaw-task/v1" or not isinstance(value.get("task"), str):
        raise ValueError("task file requires openclaw-task/v1 and original task text")
    capture = value.get("capture", {"complete": False, "reason": "Unknown capture completeness."})
    if (not isinstance(capture, dict) or set(capture) != {"complete", "reason"}
            or type(capture["complete"]) is not bool or not isinstance(capture["reason"], str)
            or not capture["reason"].strip()):
        raise ValueError("capture requires an explicit boolean complete and nonempty reason")
    if "contract" in value:
        validate_contract(value["contract"])
    return {**value, "capture": capture}


def inspect(spans, task_spec=None, trace_id=None):
    task_spec = task_spec or load_task()
    trace_ids = sorted({s["trace_id"] for s in spans})
    if trace_id and trace_id.lower() not in trace_ids:
        raise ValueError("requested trace ID is absent")
    selected = trace_id.lower() if trace_id else trace_ids[0] if len(trace_ids) == 1 else None
    rows = [s for s in spans if s["trace_id"] == selected] if selected else []
    tools = sorted([s for s in rows if _tool(s) is not None], key=lambda s: (s["start"], s["end"], s["span_id"]))
    gaps, issues = [], []
    export_gaps = sorted({gap for span in rows for gap in span.get("export_gaps", [])})
    if export_gaps:
        gaps.append({"missing": export_gaps, "reason": "Trace API reports missing or unreturned spans."})
    if selected is None:
        issues.append("Multiple traces found; select --trace-id. Separate traces are not assumed to be one task.")
    if not task_spec["task"].strip():
        issues.append("Original task text is required via --task-file or --task; a model prompt is not assumed to be the complete task.")
    if selected and not tools:
        issues.append("No recognized OpenClaw tool execution spans; metadata/model-only exports cannot reconstruct tool decisions.")
    for span in tools:
        attrs = span["attributes"]
        inp, arguments, _ = _content(attrs, CONTENT_INPUT)
        out, result, _ = _content(attrs, CONTENT_OUTPUT)
        missing = []
        if not _tool(span):
            missing.append("tool_name")
        if not inp:
            missing.append("tool_arguments")
        if not out:
            missing.append("tool_result")
        raw_status = _status(span["status"]).get("code", 0)
        error_recorded = (raw_status in (2, "2", "STATUS_CODE_ERROR", "ERROR")
                          or attrs.get("openclaw.outcome") in ("blocked", "error")
                          or _signoz_error(attrs))
        if missing:
            gaps.append({"span_id": span["span_id"], "missing": missing})
        if (_content_conflict(attrs, CONTENT_INPUT) or _content_conflict(attrs, CONTENT_OUTPUT)
                or ("gen_ai.tool.name" in attrs and "openclaw.toolName" in attrs
                    and attrs["gen_ai.tool.name"] != attrs["openclaw.toolName"])):
            gaps.append({"span_id": span["span_id"], "missing": ["conflicting_tool_aliases"],
                         "reason": "Preferred standard attributes retained; conflicting aliases are unresolved."})
        # Errors without a result are meaningful recorded outcomes; no result is invented.
        if not inp or not _tool(span) or (not out and not error_recorded):
            issues.append("Tool span " + span["span_id"] + " lacks required captured arguments or outcome.")
        if _truncated(arguments) or _truncated(result) or any(
                "truncat" in k.lower() and v not in (False, 0, "false") for k, v in attrs.items()):
            gaps.append({"span_id": span["span_id"], "missing": ["content_truncated_or_redacted"]})
    for before, after in zip(tools, tools[1:]):
        if after["start"] < before["end"]:
            issues.append("Overlapping tool spans are unsupported by the sequential importer; preserve the graph instead of flattening concurrency.")
            break
    all_ids = {s["span_id"] for s in rows}
    if any(s["parent_span_id"] and int(s["parent_span_id"], 16) and s["parent_span_id"] not in all_ids for s in rows):
        gaps.append({"missing": ["parent_spans"], "reason": "Export contains a partial call tree."})
    roots = [s for s in rows if not s["parent_span_id"] or int(s["parent_span_id"], 16) == 0]
    if selected and len(roots) != 1:
        gaps.append({"missing": ["single_trace_root"]})
    parents = {s["span_id"]: s["parent_span_id"] for s in rows}
    resolved, cyclic = set(), set()
    for span_id in parents:
        path, positions = [], {}
        current = span_id
        while current in parents and current not in resolved:
            if current in positions:
                cyclic.update(path[positions[current]:])
                break
            positions[current] = len(path)
            path.append(current)
            parent = parents[current]
            if not parent or int(parent, 16) == 0:
                break
            current = parent
        resolved.update(path)
    if cyclic:
        gaps.append({"missing": ["acyclic_parent_chain"], "span_ids": sorted(cyclic),
                     "reason": "Span parent chains contain a cycle and do not reach a trace root."})
    if any(int(_field(s["raw"], "droppedAttributesCount", "dropped_attributes_count", 0) or 0)
           or int(_field(s["raw"], "droppedEventsCount", "dropped_events_count", 0) or 0) for s in rows):
        gaps.append({"missing": ["dropped_telemetry_fields"]})
    # Missing payload on an explicitly failed call need not prevent a known task
    # failure, but remains a coverage gap and prevents completeness assertions.
    coverage = {"declared_complete": task_spec["capture"]["complete"],
        "declaration_reason": task_spec["capture"]["reason"],
        "content_complete": not gaps and not issues, "gaps": gaps,
        "mental_modules": "not captured; memory/reflection/plan remain null",
        "session_boundary": "one selected trace; cross-trace session stitching is not implemented"}
    return {"schema": VERSION, "trace_ids": trace_ids, "selected_trace_id": selected,
        "span_count": len(rows), "tool_span_count": len(tools), "ready": not issues,
        "issues": issues, "coverage": coverage,
        "field_mapping": {"tool": "gen_ai.tool.name or openclaw.toolName",
                          "arguments": list(CONTENT_INPUT), "result": list(CONTENT_OUTPUT)},
        "notes": ["OTel UNSET does not mean failure or verified success.",
                  "Tool output matching can verify an explicit tool-result contract only.",
                  "Missing mental modules cannot support memory/plan/reflection diagnoses."]}


def convert(spans, task_spec, trace_id=None, trajectory_id=None):
    audit = inspect(spans, task_spec, trace_id)
    if not audit["ready"]:
        raise ValueError("Cannot import: " + " ".join(audit["issues"]))
    tid = audit["selected_trace_id"]
    selected = [s for s in spans if s["trace_id"] == tid]
    tools = sorted([s for s in selected if _tool(s) is not None], key=lambda s: (s["start"], s["end"], s["span_id"]))
    encode = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    steps, mapping = [], []
    previous = {"type": "tool_context", "trace_id": tid,
                "provenance": {"source": "OTLP export", "task_source": "explicit task sidecar"}}
    for span in tools:
        attrs = span["attributes"]
        _, arguments, input_key = _content(attrs, CONTENT_INPUT)
        has_result, result, output_key = _content(attrs, CONTENT_OUTPUT)
        provenance = {"source_digest": span["source_digest"], "parent_span_id": span["parent_span_id"],
            "start_time_unix_nano": str(span["start"]), "end_time_unix_nano": str(span["end"]),
            "span_name": span["name"], "argument_attribute": input_key, "result_attribute": output_key}
        for original, key in (("gen_ai.tool.call.id", "tool_call_id"), ("openclaw.outcome", "outcome"),
                              ("openclaw.deniedReason", "denied_reason")):
            if original in attrs:
                provenance[key] = attrs[original]
        identity = {"trace_id": tid, "span_id": span["span_id"], "tool": _tool(span)}
        action = {"type": "tool_call", **identity, "arguments": arguments, "provenance": provenance}
        span_status = _status(span["status"])
        raw_status = span_status.get("code", 0)
        status = "error" if raw_status in (2, "2", "STATUS_CODE_ERROR", "ERROR") else "ok" if raw_status in (1, "1", "STATUS_CODE_OK", "OK") else "unset"
        feedback_provenance = dict(provenance)
        # This is the exporter's recorded output-source claim, not a result or
        # completeness guarantee. Absent attributes stay absent. Keep output
        # provenance on the observation, not on the earlier action.
        if "openclaw.content.tool_output_source" in attrs:
            feedback_provenance["tool_output_source"] = attrs["openclaw.content.tool_output_source"]
        feedback = {"type": "tool_result", **identity, "status": status, "provenance": feedback_provenance}
        if has_result:
            feedback["result"] = result
        if status == "error" or attrs.get("openclaw.outcome") == "error" or _signoz_error(attrs):
            feedback["error"] = {"message": span_status.get("message", ""),
                **{k: attrs[k] for k in ("openclaw.errorCategory", "openclaw.errorCode",
                                         "signoz.hasError", "signoz.statusCode", "signoz.statusMessage") if k in attrs}}
        action_text = encode(action)
        step = len(steps) + 1
        steps.append({"t": step, "env_text": encode(previous), "agent_text": action_text,
            "admissible": [], "parts": {"memory": None, "reflection": None, "plan": None, "action": action_text}})
        mapping.append({"action_step": step, "result_step": step + 1, **identity,
                        "source_digest": span["source_digest"]})
        previous = feedback
    # Retain the final tool response explicitly, without fabricating another action.
    steps.append({"t": len(steps) + 1, "env_text": encode(previous), "agent_text": "",
                  "admissible": [], "parts": dict.fromkeys(("memory", "reflection", "plan", "action"))})
    source = {"trajectory_id": valid_id(trajectory_id or "openclaw_" + tid), "task_type": "openclaw",
        "task": task_spec["task"], "steps": steps,
        "telemetry": {"schema": VERSION, "trace_id": tid, "coverage": audit["coverage"],
            "source_map": mapping, "task_spec_digest": digest(task_spec),
            "input_digest": digest([s["raw"] for s in selected])}}
    if "contract" in task_spec:
        source["task_contract"] = task_spec["contract"]
    return source, audit


def save_import(source, audit, spans, output_dir):
    folder = Path(output_dir) / source["trajectory_id"]
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "input.json", source)
    write_json(folder / "steps.json", source["steps"])
    write_json(folder / "record.json", {k: v for k, v in source.items() if k != "steps"})
    write_json(folder / "import_audit.json", audit)
    write_json(folder / "source_map.json", source["telemetry"]["source_map"])
    write_json(folder / "original_spans.json", [s["raw"] for s in spans if s["trace_id"] == source["telemetry"]["trace_id"]])
    return folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="OTLP JSON/JSONL export (not protobuf bytes)")
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--task", help="Original task text; completeness/outcome remain unknown")
    parser.add_argument("--trace-id")
    parser.add_argument("--trajectory-id")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        spans = spans_from_export(load_export(args.input))
        spec = load_task(args.task_file, args.task)
        audit = inspect(spans, spec, args.trace_id)
        if args.inspect_only or not audit["ready"]:
            path = args.output_dir / "import_audit.json"
            if path.exists():
                raise ValueError("output already exists; select a new output directory")
            write_json(path, audit)
            print(json.dumps({"ready": audit["ready"], "issues": audit["issues"], "audit": str(path)}, ensure_ascii=False))
            return 0 if args.inspect_only else 2
        source, audit = convert(spans, spec, args.trace_id, args.trajectory_id)
        folder = save_import(source, audit, spans, args.output_dir)
        print(json.dumps({"trajectory_id": source["trajectory_id"], "compiled_dir": str(folder),
                          "action_count": len(source["steps"]) - 1}, ensure_ascii=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(2, f"Import failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
