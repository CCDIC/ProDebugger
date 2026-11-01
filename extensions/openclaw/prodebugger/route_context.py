"""Loss-aware ROUTE context projection and deterministic retrieval progress."""
import json

from .record_bridge import digest, plain


def evidence_preview(value, budget=9000):
    """Keep actual source/identity fields, marking every shortened collection."""
    priority = {key: i for i, key in enumerate((
        "step", "t", "fid", "rel", "args", "slot", "side", "source_kind",
        "assertion_time", "claim_scope", "claim_polarity", "audits", "transition",
        "module_text", "memory", "reflection", "plan", "action", "span", "text",
        "observation_text", "next_response_text", "state", "value", "requirements",
        "items", "facts", "coverage", "truncation", "parse_audit"))}
    remaining = budget

    def preview(item):
        nonlocal remaining
        if isinstance(item, dict):
            result = {}
            keys = sorted(item, key=lambda k: priority.get(k, len(priority)))
            for i, key in enumerate(keys):
                if remaining < 300:
                    result["_omitted_fields"] = keys[i:]
                    break
                remaining -= len(key.encode()) + 8
                result[key] = preview(item[key])
            return result
        if isinstance(item, list):
            rows = []
            for child in item[:4]:
                if remaining < 300:
                    break
                rows.append(preview(child))
            if len(rows) < len(item):
                return {"items": rows, "total_items": len(item), "omitted_items": len(item) - len(rows)}
            return rows
        if isinstance(item, str):
            encoded = item.encode()
            kept = encoded[:min(1200, max(0, remaining - 120))].decode(errors="ignore")
            remaining -= len(kept.encode()) + 64
            return item if kept == item else {"text_prefix": kept, "omitted_chars": len(item) - len(kept)}
        remaining -= 32
        return item

    return preview(value)


def compact(messages, completed):
    """Every result in the latest assistant batch reaches the model in full.

    Earlier duplicate results reference the latest retained copy. Large older
    unique results keep an evidence preview and their exact retrieval arguments.
    The durable transcript and tool journal are never modified.
    """
    projected = plain(messages)
    boundary = next((i for i in range(len(projected) - 1, -1, -1)
                     if projected[i]["role"] == "assistant" and projected[i].get("tool_calls")), len(projected))
    signatures, latest = {}, {}
    for i, message in enumerate(projected):
        if message["role"] != "tool":
            continue
        event = completed.get(message["tool_call_id"], {})
        signature = digest({"name": event.get("name"), "arguments": event.get("arguments"),
                            "result": json.loads(message["content"])})
        signatures[i] = signature
        latest[signature] = message["tool_call_id"]
    for i, message in enumerate(projected[:boundary]):
        if message["role"] != "tool":
            continue
        event = completed.get(message["tool_call_id"], {})
        reference = latest[signatures[i]]
        if reference != message["tool_call_id"]:
            message["content"] = json.dumps({"schema": "debugger-context-reference/v1",
                "same_result_as": reference, "request": {"name": event.get("name"), "arguments": event.get("arguments")},
                "instruction": "Identical result is retained under that tool_call_id in this context. Reuse it; repeating this request adds no evidence."}, ensure_ascii=False)
        elif len(message["content"].encode()) > 12000:
            result = json.loads(message["content"])
            message["content"] = json.dumps({"schema": "debugger-context-summary/v2",
                "tool_call_id": message["tool_call_id"], "scope": result.get("scope"),
                "request": {"name": event.get("name"), "arguments": event.get("arguments")},
                "original_digest": digest(result), "truncated": True,
                "evidence_preview": evidence_preview(result.get("data", result)),
                "instruction": "Partial retained evidence, not proof of absence. Reuse visible sources. If a specific omitted claim matters, request its Fact span or a narrower section; do not repeat the same broad view."}, ensure_ascii=False)
    return projected


def progress(messages, completed):
    """Count completed consecutive ROUTE rounds with no new successful reads."""
    seen, stalled = set(), 0
    for message in messages:
        if message["role"] != "assistant" or not message.get("tool_calls"):
            continue
        calls = message["tool_calls"]
        events = [completed.get(c.get("id")) for c in calls]
        if any(e is None for e in events):
            continue
        events = [e for e in events if e.get("phase") == "ROUTE"]
        if not events:
            continue
        if any(e["name"] in {"route_cases", "finish", "reroute"} and "error" not in e["result"] for e in events):
            stalled = 0
            continue
        fresh = False
        for event in events:
            if "error" in event["result"] or event["name"] in {"hold", "drop", "contrast"}:
                continue
            signature = digest({"name": event["name"], "arguments": event["arguments"], "result": event["result"]})
            fresh |= signature not in seen
            seen.add(signature)
        stalled = 0 if fresh else stalled + 1
    return {"consecutive_rounds_without_new_evidence": stalled, "distinct_successful_reads": len(seen)}
