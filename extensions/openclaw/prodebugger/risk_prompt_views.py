"""Remove repeated source text and finished-query documentation, never evidence.

Original requests, queries and tool journals remain available. These views keep
all structural row fields, status/relations/values/offsets and retrieval limits;
only exact text already represented by visible references is replaced by IDs.
"""
from copy import deepcopy
import json
from .record_bridge import append_json, digest


def _cover(text, step, module, catalog):
    if not isinstance(text, str) or not text:
        return []
    spans = []
    for ref in catalog.get("references", []):
        if (ref["step"], ref["module"]) != (step, module):
            continue
        start = text.find(ref["quote"])
        if start >= 0:
            spans.append((start, start + len(ref["quote"]), ref["reference_id"]))
    end, ids = 0, []
    for start, stop, rid in sorted(spans):
        if start > end:
            break
        if stop > end:
            end = stop
            ids.append(rid)
    return ids if end == len(text) else []


def query_view(data):
    value = deepcopy(data)
    catalog = value.get("reference_catalog", {})
    for row in value.get("rows", []):
        if not isinstance(row, dict):
            continue
        # Keep value (including booleans/state/relations), every offset and all
        # metadata. Only literal source-text fields can be represented by refs.
        for field in ("text", "quote", "span"):
            ids = _cover(row.get(field), row.get("step", row.get("t")), row.get("module"), catalog)
            if ids:
                row.pop(field)
                row[field + "_reference_ids"] = ids
    value["model_view"] = {"schema": "risk-query-view/v1",
        "source_text": "Exact text is preserved in reference_catalog; row text_reference_ids select its same-source slices.",
        "structure": "All original row values/relations/status/offsets and query errors, partial results and scope limits are retained.",
        "original_query_digest": digest(data)}
    return value


def assessment_context(tools, cid, history):
    query = tools.trace_queries.get(cid)
    if not query or not query.get("reference_catalog"):
        return deepcopy(history)
    catalog = query["reference_catalog"]
    lookup = {r["reference_id"]: r for r in catalog["references"]}
    view = deepcopy(history)
    for message in view:
        try:
            data = json.loads(message.get("content") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if message.get("role") == "user" and data.get("reference_catalog"):
            old = data["reference_catalog"]
            # The completed query's catalog already contains the full initial
            # evidence. If that invariant ever fails, retain both copies.
            if all(lookup.get(r["reference_id"]) == r for r in old["references"]):
                data.pop("reference_catalog")
                data["evidence_catalog_location"] = "The committed query tool response contains all initial and retrieved reference IDs."
            data.pop("query_language", None)  # No second SQL is available.
            source = data.get("source", {})
            step = source.get("step")
            ids = _cover(source.get("observation"), step, "observation", catalog)
            if ids:
                source.pop("observation")
                source["observation_reference_ids"] = ids
            modules = source.get("modules", {})
            for module, text in list(modules.items()):
                ids = _cover(text, step, module, catalog)
                if ids:
                    modules[module] = {"reference_ids": ids}
            data["query_state"] = "The single query is committed; assess only. Full SQL documentation remains in the original query-stage request."
            message["content"] = json.dumps(data, ensure_ascii=False)
        if message.get("role") == "tool" and isinstance(data.get("data"), dict) and data["data"].get("reference_catalog"):
            data["data"] = query_view(data["data"])
            message["content"] = json.dumps(data, ensure_ascii=False)
    if tools.output:
        append_json(tools.output / "model_evidence_views.jsonl", {
            "schema": "risk-model-evidence-view/v1", "owner": cid,
            "original_history_digest": digest(history), "model_view_digest": digest(view),
            "original_characters": len(json.dumps(history, ensure_ascii=False)),
            "model_view_characters": len(json.dumps(view, ensure_ascii=False)),
            "view": view, "preserved_query_digest": digest(query),
            "changes": ["remove_completed_query_language_documentation", "replace_duplicate_exact_source_text_with_reference_ids", "avoid_reprinting_initial_reference_catalog"]})
    return view
