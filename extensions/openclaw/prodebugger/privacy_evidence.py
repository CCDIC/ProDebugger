"""Grounded privacy fields and observed snapshot links, never causal edges.

Policy supplies categories/labels, not sample values. Label extraction is a
bounded parser; natural-language PARSE interpretations remain candidates. Exact
SOURCE queries verify displayed evidence before automatic findings are emitted.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .record_bridge import digest, plain
from .session_sources import source_text, fact_source_module

SCHEMA = "privacy-evidence/v1"
OUTPUT_TYPE = "sensitive_output_disclosure"
OUTPUT_RULE = "RR-PRIVACY-OUTPUT-001"
CONTEXT_RULE = "RR-PRIVACY-CONTEXT-001"
ACQUIRE_TYPE = "sensitive_data_acquisition"
ACQUIRE_RULE = "RR-PRIVACY-ACQUIRE-001"


def validate_rules(rules):
    if not isinstance(rules, list) or len(rules) > 64:
        raise ValueError("privacy_rules must be an array of at most 64 categories")
    required = {"id", "category", "description", "labels", "subject_labels"}
    optional = {"allow_acquire", "allow_in_context", "allow_in_output"}
    ids = set()
    for row in rules:
        if not isinstance(row, dict) or not required <= row.keys() or row.keys() - required - optional:
            raise ValueError("invalid privacy rule fields")
        for key in ("id", "category", "description"):
            if not isinstance(row[key], str) or not row[key].strip() or len(row[key]) > 1200:
                raise ValueError("privacy rule requires bounded " + key)
        if row["id"] in ids:
            raise ValueError("privacy rule IDs must be unique")
        ids.add(row["id"])
        for key in ("labels", "subject_labels"):
            if not isinstance(row[key], list) or not 1 <= len(row[key]) <= 32 or any(
                    not isinstance(v, str) or not v.strip() or len(v) > 100 for v in row[key]):
                raise ValueError("privacy labels must be explicit nonempty field-name lists")
        for key in optional:
            if key in row and type(row[key]) is not bool:
                raise ValueError(key + " must be boolean; omission is unconfigured")
    return deepcopy(rules)


def _object(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _leaves(value, path=""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, path + "/" + str(key).replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _leaves(item, path + "/" + str(i))


def _content_leaves(value, path="", depth=0):
    """Decode recorded JSON strings for inspection; retain each decoding boundary."""
    if isinstance(value, str):
        if depth < 5:
            try:
                decoded = json.loads(value)
                if isinstance(decoded, (dict, list)):
                    yield from _content_leaves(decoded, path + "/@decoded-json", depth + 1)
                    return
            except (ValueError, TypeError):
                pass
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _content_leaves(child, path + "/" + str(key).replace("~", "~0").replace("/", "~1"), depth)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _content_leaves(child, path + "/" + str(i), depth)


def _label_values(text, labels):
    # A label must be a field, not an arbitrary substring such as project P6.
    names = "|".join(re.escape(v) for v in sorted(labels, key=len, reverse=True))
    pattern = re.compile(r"(?im)(?:^|[\n\r,，;；|])\s*[#*\- ]*(?P<label>" + names +
                         r")[* ]*(?:[:：]\s*|[ \t]+)(?P<value>[^\n\r,，;；|]+)")
    for match in pattern.finditer(text):
        value = match["value"].strip().strip("* ").rstrip("。.")
        if not value or len(value) > 256 or "(truncated)" in value:
            continue
        start = match.start("label")
        end = match.start("value") + match["value"].find(value) + len(value)
        yield {"label": match["label"], "value": value, "start": start,
               "end": end, "quote": text[start:end]}


def _reference(source, step, module, quote, *, pointer=None):
    original = source_text(source, step, module)
    lo, hi = 0, len(original)
    if pointer and module in {"action", "observation"}:
        from .safety_audit import _locations
        from .risk_evidence import located_path, pointer_parts
        try:
            path = located_path(json.loads(original), pointer_parts(pointer))
            lo, hi, _ = _locations(original)[path]
        except (KeyError, TypeError, ValueError, IndexError):
            return None
    options = [quote, json.dumps(quote, ensure_ascii=False)[1:-1], json.dumps(quote, ensure_ascii=True)[1:-1]]
    # A duplicate fragment is not assigned an invented offset.
    member = original[lo:hi]
    matched = [(q, lo + member.index(q)) for q in dict.fromkeys(options) if q and member.count(q) == 1]
    if not matched:
        return None
    q, start = matched[0]
    if len(q) > 360:
        return None
    from .risk_trace import location
    mapping = location(source, step, module)
    event = next((e for e in source.get("session", {}).get("events", []) if e.get("step") == step), {})
    return {"step": step, "module": module, "quote": q, "start": start, "end": start + len(q),
            "source_id": f"source:{step}:{module}", "source_pointer": pointer, "lookup_value": quote,
            "source_ref": deepcopy(event.get("source", {})),
            "call_id": mapping.get("call_id"), "event_id": mapping.get("event_id"),
            "fact_ids": [], "reference_validation": "exact_source_interval"}


def _sources(source, policy):
    events = {e.get("step"): e for e in source.get("session", {}).get("events", [])}
    for step in source.get("steps", []):
        t = step["t"]
        observation = _object(step["env_text"])
        if observation.get("type") == "tool_result":
            for pointer, text in _leaves(observation.get("result")):
                yield t, "observation", text, "/result" + pointer, "return", events.get(t, {})
        event = events.get(t, {})
        if event.get("type") == "session_message" and event.get("role") == "assistant" and event.get("text"):
            yield t, "message", event["text"], "/text", "output", event
        action = _object(step["parts"].get("action"))
        binding = policy.get("tools", {}).get(action.get("tool"), {})
        if action.get("type") == "tool_call" and binding.get("kind") == "write":
            name = binding.get("content_arg")
            for pointer, text in _leaves((action.get("arguments") or {}).get(name)):
                yield t, "action", text, "/arguments/" + str(name) + pointer, "output", events.get(t, {})


def semantic_field_rejection(rule, item, text):
    """Reject a name-only ID interpretation, without guessing ID validity.

    This guard is scoped to identifier categories. Explicit identifier fields,
    alphabetic identifiers, grade/salary and future person_name policies retain
    their existing semantics. Rejection is not evidence that the record is safe.
    """
    category = str(rule.get("category", "")).strip().lower().replace("-", "_").replace(" ", "_")
    if category not in {"national_id", "national_identifier", "personal_identifier", "personal_id", "government_id"}:
        return None
    value = str(item.get("value", "")).strip()
    if not value:
        return None
    views = [text]
    try:
        views.extend(body for _, body in _content_leaves(json.loads(text)))
    except (ValueError, TypeError):
        pass
    # A bounded PARSE chunk may end inside a JSON string. Decode only known
    # line escapes for classification; exact original references remain intact.
    views.extend(v.replace("\\r", "\r").replace("\\n", "\n") for v in tuple(views))
    normalize = lambda v: str(v).strip().casefold()
    names = {normalize(item.get("subject"))} if item.get("subject") else set()
    names.update(normalize(s["value"]) for body in views for s in _label_values(body, rule.get("subject_labels", [])))
    labelled_identifier = any(normalize(field["value"]) == normalize(value)
                             for body in views for field in _label_values(body, rule.get("labels", [])))
    if normalize(value) in names and not labelled_identifier:
        return "identity_category_value_is_subject_name_without_identifier_field"
    return None


def extract_fields(source, policy, semantics=None, *, rejections=None):
    """Only observed field values. Explicit labels and model interpretations differ."""
    rules = {r["id"]: r for r in policy.get("privacy_rules", [])}
    fields = []
    for step, module, text, pointer, kind, event in _sources(source, policy):
        for rule in rules.values():
            subjects = list(_label_values(text, rule["subject_labels"]))
            for item in _label_values(text, rule["labels"]):
                if not _disclosed_value(item):
                    continue
                prior = [s for s in subjects if s["start"] <= item["start"]]
                subject = prior[-1] if prior else subjects[0] if len(subjects) == 1 else None
                ref = _reference(source, step, module, item["quote"], pointer=pointer)
                sref = _reference(source, step, module, subject["quote"], pointer=pointer) if subject else None
                if not ref:
                    continue
                fields.append({"category": rule["category"], "policy_id": rule["id"],
                    "subject": subject["value"] if subject else None, "value": item["value"],
                    "label": item["label"], "category_labels": rule["labels"], "subject_labels": rule["subject_labels"], "classification_method": "explicit_policy_label",
                    "semantic_status": "configured_field_label", "kind": kind,
                    "source": ref, "subject_source": sref, "event_id": event.get("event_id")})
    for item in (semantics or {}).get("privacy_fields", []):
        rule = rules.get(item.get("policy_id"))
        if not rule or item.get("category") != rule["category"]:
            continue
        step, module = item.get("step"), item.get("module")
        if type(step) is not int or not 1 <= step <= len(source.get("steps", [])):
            continue
        allowed = [(t, m, kind) for t, m, _, _, kind, _ in _sources(source, policy) if t == step and m == module]
        if not allowed:
            continue
        rejection = semantic_field_rejection(rule, item, source_text(source, step, module))
        if rejection:
            if rejections is not None:
                rejections.append({"policy_id": rule["id"], "category": rule["category"],
                    "step": step, "module": module, "value": item.get("value"), "subject": item.get("subject"),
                    "quote": item.get("quote"), "reason": rejection, "status": "rejected_semantic_candidate",
                    "meaning": "This rejected classification is retained for audit; it does not establish safety or absence of other identifiers."})
            continue
        ref = _reference(source, step, module, item.get("quote", ""))
        if not ref or not _contains(ref["quote"], item.get("value", "")):
            continue
        sref = _reference(source, step, module, item.get("subject_quote", "")) if item.get("subject_quote") else None
        subject = item.get("subject") if sref and _contains(sref["quote"], item.get("subject", "")) else None
        fields.append({"category": rule["category"], "policy_id": rule["id"], "subject": subject,
            "value": item["value"], "label": None, "category_labels": rule["labels"], "subject_labels": rule["subject_labels"], "classification_method": "model_parse",
            "semantic_status": "model_interpretation_not_verified_classification", "kind": allowed[0][2],
            "source": ref, "subject_source": sref, "event_id": ref.get("event_id")})
    unique = {}
    for field in fields:
        key = (field["policy_id"], field["source"]["step"], field["source"]["module"], field["subject"], field["value"])
        if key in unique and unique[key]["classification_method"] == "explicit_policy_label":
            continue
        field["id"] = "privacy-field-" + digest(key)[:20]
        unique[key] = field
    return list(unique.values())


def _contains(text, value):
    if not isinstance(value, str) or not value:
        return False
    # Short values require lexical boundaries and separate subject/category checks.
    for v in (value, json.dumps(value, ensure_ascii=False)[1:-1], json.dumps(value, ensure_ascii=True)[1:-1]):
        if re.search(r"(?<![\w])" + re.escape(v) + r"(?![\w])", text):
            return True
    return False


def _same_field(left, right):
    return (left["policy_id"] == right["policy_id"] and left["value"] == right["value"]
            and bool(left.get("subject")) and left.get("subject") == right.get("subject"))


def _disclosed_value(field):
    """Explicit removal placeholders are not recorded personal plaintext.

    This is deliberately not a guess about partial masking or ID validity. A
    fictional ID in a labelled fixture remains observable fixture evidence.
    """
    value = str(field.get("value", "")).strip().casefold()
    if value.strip("[](){}<>【】 ") in {
            "redacted", "removed", "masked", "withheld", "not provided", "n/a", "none",
            "已脱敏", "已删除", "已隐藏", "未提供", "不提供", "无"}:
        return False
    return bool(value) and not re.fullmatch(r"(?:[＊*●•\s-]+|x{3,})", value)


def _acquisition_location(source, field):
    """Join a real return to one unique recorded invocation, never by adjacency."""
    from .risk_policy_contract import _calls, _resolve_call
    ref = field["source"]
    call = _resolve_call(source, {"import_step": ref["step"], "module": "observation",
                                "call_id": ref.get("call_id")}, _calls(source))
    if call is None:
        return {"verified": False, "missing": ["unique_recorded_result_call_mapping"]}
    results = [r for r in call["results"] if r["step"] == ref["step"]]
    if len(results) != 1:
        return {"verified": False, "missing": ["unique_recorded_result_call_mapping"]}
    return {"verified": True, "call_id": call["id"], "action_step": call["step"],
            "result_step": ref["step"], "tool": call["data"].get("tool"), "missing": [],
            "scope": "recorded_tool_result_interface_not_physical_io_or_model_input"}


def _resolve_cloud_ref(source, ref):
    """Resolve preserved document pointers; a supplied direction label alone is insufficient."""
    import hashlib
    from .risk_evidence import pointer
    try:
        doc = source["session"]["raw_documents"][ref["document_index"]]
        text = doc["text"]
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != ref["document_sha256"]:
            return None
        if ref.get("line_number") is not None:
            text = text.splitlines()[ref["line_number"] - 1]
        value = pointer(json.loads(text.lstrip("\ufeff")), ref.get("json_pointer", ""))
        if "decoded_json_pointer" in ref:
            value = pointer(json.loads(value), ref["decoded_json_pointer"])
        return value
    except (ValueError, KeyError, TypeError, IndexError):
        return None


def _snapshot_membership(source, event, direction, field):
    values = []
    for occurrence in event.get("observed_at", []):
        if occurrence.get("direction") != direction or not occurrence.get("model_span_id"):
            continue
        ref = occurrence.get("source") or {}
        # Reconstruct the membership from the original hashed cloud span. A
        # forged direction/model_span_id on a canonical event is not evidence.
        from .signoz_import import _attributes, _snapshots, _pick, SPAN_KEYS
        verified = False
        for meta in source.get("session", {}).get("metadata", []):
            if meta.get("type") != "recorded_cloud_span":
                continue
            original_span = _resolve_cloud_ref(source, meta.get("source", {}))
            if not isinstance(original_span, dict) or _pick(original_span, SPAN_KEYS) != occurrence["model_span_id"]:
                continue
            snapshots = _snapshots([{"span_id": occurrence["model_span_id"],
                "attrs": _attributes(original_span, meta["source"], [])}], [])
            if any(s["direction"] == direction and any(m["source"] == ref for m in s["messages"]) for s in snapshots):
                verified = True
                break
        if not verified:
            continue
        raw = _resolve_cloud_ref(source, ref)
        if raw is None:
            continue
        if direction == "input" and not field.get("subject"):
            continue
        matched_text = []
        for leaf_path, body in _content_leaves(raw):
            matches = [m for m in _label_values(body, field.get("category_labels", [])) if m["value"] == field["value"]]
            subjects = list(_label_values(body, field.get("subject_labels", [])))
            for match in matches:
                prior = [s for s in subjects if s["start"] <= match["start"]]
                subject = prior[-1] if prior else subjects[0] if len(subjects) == 1 else None
                if (subject and subject["value"] == field.get("subject")) or (not subject and not field.get("subject")):
                    matched_text.append((leaf_path, match["quote"], subject["quote"] if subject else ""))
        if not matched_text:
            continue
        values.append({"model_span_id": occurrence["model_span_id"], "direction": direction,
            "step": event.get("step"), "module": "message" if event.get("text") else "observation", "event_id": event.get("event_id"),
            "source_ref": deepcopy(ref), "quote": matched_text[0][1], "subject_quote": matched_text[0][2],
            "message_content_locator": matched_text[0][0],
            "locator_notation": "JSON pointer segments; @decoded-json marks an explicit recorded JSON-string decoding boundary",
            "verification": "preserved_cloud_message_pointer", "call_id": event.get("call_id")})
    return values


def _proof(bundle, report, ref):
    """Run the unchanged SOURCE DSL; never promote an indexed offset without retrieval."""
    from .debug_tools import DebugTools
    from .query_dsl import execute
    from .safety_audit import _sql_string, _needle
    original = source_text(bundle.source, ref["step"], ref["module"])
    lo, hi, quote = ref["start"], ref["end"], ref["quote"]
    if original[lo:hi] != quote:
        return None
    facts = [f for f in bundle.record.F if f.t == ref["step"] and not f.rel.startswith("sem_")
             and fact_source_module(f, bundle.source) == ref["module"]]
    if ref.get("source_pointer") and ref["module"] in {"action", "observation"}:
        from .risk_evidence import RiskEvidence, pointer_parts
        for fact in facts:
            if fact.rel != ("tool_call" if ref["module"] == "action" else "tool_result"):
                continue
            proof = RiskEvidence(bundle, report).member(fact, pointer_parts(ref["source_pointer"]), substring=ref.get("lookup_value", quote))
            if proof and proof.get("start") == lo and proof.get("end") == hi:
                return {**proof, "quote": quote, "fact_ids": [fact.fid],
                        "event_id": ref.get("event_id"), "call_id": ref.get("call_id"),
                        "source_ref": ref.get("source_ref", {}), "reference_validation": "verified_SOURCE_query"}
    tools = DebugTools(bundle)
    for fact in facts:
        for needle in dict.fromkeys([_needle(quote[:80]), _needle(quote), quote[:100]]):
            if not needle:
                continue
            sql = ("-- Purpose: Verify a policy-labelled privacy field in its original source.\n"
                "SELECT source_id, fid, step, module, start AS excerpt_start, end AS excerpt_end, "
                f"{lo} AS start, {hi} AS end, substr(text, {lo} - start + 1, {hi-lo}) AS text, source_kind, parse_status "
                f"FROM SOURCE({fact.fid}, {_sql_string(needle)}) WHERE start <= {lo} AND end >= {hi}")
            result = execute(tools, sql)
            qid = "q" + str(len(report["queries"]) + 1).zfill(3)
            report["queries"].append({"id": qid, "sql": sql, "result": plain(result)})
            if any(result.get(k) for k in ("error", "partial", "truncated", "function_errors", "skipped_branches")):
                continue
            rows = result.get("rows", [])
            if len(rows) == 1 and all(rows[0].get(k) == v for k, v in {"step": ref["step"], "module": ref["module"], "fid": fact.fid, "start": lo, "end": hi, "text": quote}.items()) and rows[0].get("parse_status") != "unresolved":
                return {**rows[0], "quote": quote, "query_id": qid, "fact_ids": [fact.fid],
                        "event_id": ref.get("event_id"), "call_id": ref.get("call_id"),
                        "source_ref": ref.get("source_ref", {}), "reference_validation": "verified_SOURCE_query"}
    return None


def build_privacy(source, policy, *, semantics=None, bundle=None, report=None):
    rules = {r["id"]: r for r in policy.get("privacy_rules", [])}
    semantic_rejections = []
    fields = extract_fields(source, policy, semantics, rejections=semantic_rejections)
    events = source.get("session", {}).get("events", [])
    by_step = {e.get("step"): e for e in events}
    for field in fields:
        if bundle is not None and report is not None:
            field["evidence"] = [p for p in (_proof(bundle, report, field["source"]),
                _proof(bundle, report, field["subject_source"]) if field.get("subject_source") else None) if p]
        else:
            field["evidence"] = []
        field["evidence_validated"] = any(p["start"] == field["source"]["start"] for p in field["evidence"])
    returned = [f for f in fields if f["kind"] == "return"]
    outputs = [f for f in fields if f["kind"] == "output"]
    for field in returned:
        field["acquisition"] = _acquisition_location(source, field)
        mappings = source.get("telemetry", {}).get("source_map", [])
        own = [m for m in mappings if str(m.get("trace_id")) + "/" + str(m.get("span_id")) == field["source"].get("call_id")]
        native_ids = {m.get("tool_call_id") for m in own if m.get("tool_call_id")}
        parent_ids = {m.get("parent_call_id") for m in own if m.get("parent_call_id")}
        parents = [p for p in returned if p["policy_id"] == field["policy_id"]
            and p["value"] == field["value"] and p.get("subject") == field.get("subject") and any(
            m.get("tool_call_id") in parent_ids and str(m.get("trace_id")) + "/" + str(m.get("span_id")) == p["source"].get("call_id") for m in mappings)]
        if parents:
            field["forwarding_return_field_ids"] = [p["id"] for p in parents]
            for parent in parents:
                parent.setdefault("forwarded_from_field_ids", []).append(field["id"])
        occurrences = []
        for event in events:
            if event.get("role") not in {"tool", "toolResult", "tool_runtime"}:
                continue
            for inp in _snapshot_membership(source, event, "input", field):
                raw = _resolve_cloud_ref(source, inp["source_ref"])
                ids = {v for k, v in _leaves(raw) if k.rsplit("/", 1)[-1] in {"id", "tool_call_id", "toolCallId"}}
                direct = bool(native_ids & ids)
                via_parent = bool(parents and parent_ids & ids)
                if direct or via_parent:
                    occurrences.append({**inp, "invocation_link": "exact_tool_result_id" if direct else "recorded_parent_call_id_and_matching_forwarded_return",
                        "source_call_id": field["source"].get("call_id"),
                        "parent_call_id": next(iter(parent_ids & ids)) if via_parent and not direct else None,
                        "forwarding_return_field_ids": [p["id"] for p in parents] if via_parent and not direct else []})
        field["input_occurrences"] = occurrences
    flows = []
    for output in outputs:
        candidates = [f for f in returned if _same_field(f, output) and not f.get("forwarded_from_field_ids")]
        event = by_step.get(output["source"]["step"], {})
        out_occurrences = _snapshot_membership(source, event, "output", output)
        source_candidates = []
        for candidate in candidates:
            call_id = candidate["source"].get("call_id")
            linked_inputs = [i for i in candidate.get("input_occurrences", [])
                             if any(o["model_span_id"] == i["model_span_id"] for o in out_occurrences)]
            source_candidates.append({"field_id": candidate["id"], "call_id": call_id,
                "step": candidate["source"]["step"], "module": "observation",
                "refs": [candidate["source"], *([candidate["subject_source"]] if candidate.get("subject_source") else [])],
                "input_occurrences": linked_inputs, "input_link_verified": bool(linked_inputs)})
        linked = [i for c in source_candidates for i in c["input_occurrences"]]
        output_recorded = bool(out_occurrences) or output["source"]["module"] == "action" or source.get("session", {}).get("schema") != "openclaw-signoz-session/v1"
        complete = bool(source_candidates and linked and out_occurrences)
        missing = []
        if not source_candidates:
            missing.append("matching_subject_category_value_return")
        if not linked:
            missing.append("matching_tool_result_in_same_model_input_snapshot")
        if not output_recorded:
            missing.append("recorded_output_membership")
        flow = {"id": "privacy-flow-" + digest(output["id"])[:20], "field_id": output["id"],
            "category": output["category"], "policy_id": output["policy_id"], "subject": output["subject"], "value": output["value"],
            "source_candidates": source_candidates, "input_occurrences": linked,
            "output": {**output["source"], "refs": [output["source"]], "observed_at": out_occurrences,
                       "model_span_id": out_occurrences[0]["model_span_id"] if len(out_occurrences) == 1 else None,
                       "recorded": output_recorded},
            "stage_status": {"return": "recorded" if source_candidates else "missing",
                             "model_input": "verified" if linked else "missing",
                             "output": "verified" if output_recorded else "missing"},
            "status": "verified_observed_chain" if complete else "partial", "missing": missing,
            "limitations": ["Observed content and snapshot membership do not prove unique causation or hidden reasoning.",
                            "All matching recorded source candidates are retained."]}
        flows.append(flow)
    return {"schema": SCHEMA, "fields": fields, "flows": flows, "semantic_rejections": semantic_rejections,
        "coverage": {"configured_categories": sorted({r["category"] for r in rules.values()}),
            "field_count": len(fields), "verified_chain_count": sum(f["status"] == "verified_observed_chain" for f in flows),
            "classification_scope": "explicit_policy_labels_and_source_grounded_PARSE_candidates",
            "unrestricted_values_not_assumed_safe": True}}


def append_checks(source, policy, bundle, report):
    privacy = build_privacy(source, policy, semantics=getattr(bundle.record, "openclaw_semantics", {}), bundle=bundle, report=report)
    report["privacy"] = privacy
    rules = {r["id"]: r for r in policy.get("privacy_rules", [])}
    _append_acquisition_checks(policy, privacy, report)
    for field in privacy["fields"]:
        rule, ref = rules[field["policy_id"]], field["source"]
        if field["kind"] != "return" or field.get("forwarded_from_field_ids") or "allow_in_context" not in rule:
            continue
        explicit = field["classification_method"] == "explicit_policy_label"
        verified = explicit and field["evidence_validated"] and bool(field.get("input_occurrences"))
        allowed = rule["allow_in_context"]
        status = "passed" if verified and allowed else "confirmed" if verified else "unknown"
        reason = ("A prohibited personal field is present in a recorded model input, joined to its exact tool result ID." if verified and not allowed else
                  "This field is explicitly permitted in the recorded model input." if verified else
                  "A returned field is recorded, but the matching model-input membership or field evidence is incomplete.")
        proofs = field["evidence"]
        common = {"risk_type": "sensitive_context_exposure", "rule_id": CONTEXT_RULE, "source": "risk_rule",
            "module": "observation", "import_step": ref["step"], "call_id": ref.get("call_id"),
            "call_ids": [ref["call_id"]] if ref.get("call_id") else [], "event_id": ref.get("event_id"),
            "fact_ids": sorted({p["fid"] for p in proofs}), "primary_fids": sorted({p["fid"] for p in proofs}),
            "evidence": proofs, "evidence_validated": verified,
            "policy_ids": [rule["id"]], "policy_refs": ["/privacy_rules/" + str(policy["privacy_rules"].index(rule))],
            "privacy_rule_id": rule["id"], "privacy_category": rule["category"], "privacy_field_ids": [field["id"]],
            "privacy_flow_ids": [f["id"] for f in privacy["flows"] if any(c["field_id"] == field["id"] for c in f["source_candidates"])],
            "privacy_input_occurrences": field.get("input_occurrences", []),
            "missing": [] if verified else ["verified_matching_model_input_membership"]}
        report["checks"].append({**common, "id": "privacy-context-check-" + digest(field["id"])[:20],
            "status": status, "state": status, "applied": verified, "reason": reason})
        if not allowed:
            report["findings"].append({**common, "id": "privacy-context-risk-" + digest(field["id"])[:20],
                "status": "confirmed" if verified else "unknown", "category": "safety" if verified else "uncertainty",
                "title": "Restricted information in model input", "explanation": reason, "counterevidence": [],
                "effect": "recorded_model_input", "limitations": ["Recorded input does not prove model use or later disclosure."],
                "causal_status": "not_proven"})
    for field in privacy["fields"]:
        rule = rules[field["policy_id"]]
        if field["kind"] != "output" or "allow_in_output" not in rule:
            continue
        flow = next(f for f in privacy["flows"] if f["field_id"] == field["id"])
        ref, proofs = field["source"], field["evidence"]
        complete = field["evidence_validated"] and flow["output"]["recorded"]
        explicit = field["classification_method"] == "explicit_policy_label"
        status = "passed" if complete and explicit and rule["allow_in_output"] else "confirmed" if complete and explicit else "unknown"
        reason = ("The recorded output contains a field explicitly prohibited by the supplied privacy policy." if status == "confirmed" else
                  "This recorded field is explicitly permitted in output." if status == "passed" else
                  "The field remains a semantic candidate or its recorded output/source evidence is incomplete.")
        fids = sorted({p["fid"] for p in proofs})
        common = {"risk_type": OUTPUT_TYPE, "rule_id": OUTPUT_RULE, "source": "risk_rule", "module": ref["module"],
            "import_step": ref["step"], "call_id": ref.get("call_id"), "call_ids": [ref["call_id"]] if ref.get("call_id") else [],
            "event_id": ref.get("event_id"), "fact_ids": fids, "primary_fids": fids,
            "evidence": proofs, "evidence_validated": complete, "missing": [] if complete and explicit else ["verified_explicit_output_field"],
            "policy_ids": [rule["id"]], "policy_refs": ["/privacy_rules/" + str(policy["privacy_rules"].index(rule))],
            "privacy_rule_id": rule["id"], "privacy_category": rule["category"], "privacy_field_ids": [field["id"]], "privacy_flow_ids": [flow["id"]]}
        report["checks"].append({**common, "id": "privacy-check-" + digest(field["id"])[:20],
            "status": status, "state": status, "applied": status in {"passed", "confirmed"}, "reason": reason})
        if rule["allow_in_output"] is False:
            report["findings"].append({**common, "id": "privacy-risk-" + digest(field["id"])[:20],
                "status": "confirmed" if status == "confirmed" else "suspected" if complete else "unknown",
                "category": "safety" if status == "confirmed" else "uncertainty", "title": "Restricted information in output",
                "explanation": reason, "counterevidence": [], "effect": "recorded_output",
                "limitations": ["Output-policy violation is independent of external transmission.", *flow["limitations"]],
                "causal_status": "not_proven"})
    return privacy


def _append_acquisition_checks(policy, privacy, report):
    """Content policy is independent of file permission and model membership."""
    rules = {r["id"]: r for r in policy.get("privacy_rules", [])}
    for field in privacy["fields"]:
        rule, ref = rules[field["policy_id"]], field["source"]
        if (field["kind"] != "return" or field.get("forwarded_from_field_ids")
                or "allow_acquire" not in rule or not _disclosed_value(field)):
            continue
        mapping = field.get("acquisition", {})
        explicit = field["classification_method"] == "explicit_policy_label"
        verified = bool(explicit and field["evidence_validated"] and mapping.get("verified"))
        allowed = rule["allow_acquire"]
        status = "passed" if verified and allowed else "confirmed" if verified else "unknown"
        missing = ([] if field["evidence_validated"] else ["verified_original_return_field"])
        if not explicit:
            missing.append("verified_category_classification")
        missing.extend(mapping.get("missing", []))
        reason = ("The exact tool return exposes a personal field whose acquisition is explicitly prohibited, even if its file path is allowed."
                  if verified and not allowed else
                  "Acquisition of this recorded field is explicitly permitted by this category policy."
                  if verified else
                  "A returned personal field is a candidate, but its category, source evidence or unique invocation mapping is incomplete.")
        proofs = field["evidence"]
        forwarded = [f for f in privacy["fields"] if f["id"] in field.get("forwarding_return_field_ids", [])]
        common = {"risk_type": ACQUIRE_TYPE, "rule_id": ACQUIRE_RULE, "source": "risk_rule",
            "module": "observation", "import_step": ref["step"], "call_id": ref.get("call_id"),
            "call_ids": [ref["call_id"]] if ref.get("call_id") else [], "event_id": ref.get("event_id"),
            "fact_ids": sorted({p["fid"] for p in proofs}), "primary_fids": sorted({p["fid"] for p in proofs}),
            "evidence": proofs, "evidence_validated": verified, "missing": missing,
            "policy_ids": [rule["id"]], "policy_refs": ["/privacy_rules/" + str(policy["privacy_rules"].index(rule))],
            "privacy_rule_id": rule["id"], "privacy_category": rule["category"], "privacy_field_ids": [field["id"]],
            "privacy_flow_ids": [f["id"] for f in privacy["flows"] if any(c["field_id"] == field["id"] for c in f["source_candidates"])],
            "acquisition": mapping, "related_forwarding_returns": [f["source"] for f in forwarded]}
        report["checks"].append({**common, "id": "privacy-acquire-check-" + digest(field["id"])[:20],
            "status": status, "state": status, "applied": verified, "reason": reason})
        if allowed is False:
            report["findings"].append({**common, "id": "privacy-acquire-risk-" + digest(field["id"])[:20],
                "status": "confirmed" if verified else "suspected" if field["evidence_validated"] and mapping.get("verified") else "unknown",
                "category": "safety" if verified else "uncertainty", "title": "Restricted information acquired through a tool return",
                "explanation": reason, "counterevidence": [], "effect": "recorded_tool_result_acquisition",
                "limitations": ["This identifies recorded disclosure at the tool-result interface; it does not prove physical file access, deliberate intent or unique causation.",
                                "Model-input membership and later output are separate checks. Constructed records establish represented behavior only."],
                "causal_status": "not_proven"})


def matching_output_fields(privacy, row):
    return [f for f in privacy.get("fields", []) if f["kind"] == "output"
            and f["source"]["step"] == row.get("import_step") and f["source"]["module"] == row.get("module")
            and f["policy_id"] in row.get("policy_ids", [])]


def finding_details(privacy, row):
    matches = (matching_output_fields(privacy, row) if row.get("risk_type") == OUTPUT_TYPE else
               [f for f in privacy.get("fields", []) if f["kind"] == "return"
                and f["policy_id"] in row.get("policy_ids", []) and f["source"].get("call_id") == row.get("call_id")])
    rules = sorted({f["policy_id"] for f in matches})
    return {"privacy_rule_id": rules[0] if len(rules) == 1 else None,
            "privacy_category": matches[0]["category"] if len(rules) == 1 else None,
            "privacy_field_ids": [f["id"] for f in matches],
            "privacy_flow_ids": [f["id"] for f in privacy.get("flows", []) if f["field_id"] in {m["id"] for m in matches}
                                 or any(c["field_id"] in {m["id"] for m in matches} for c in f["source_candidates"])],
            "privacy_input_occurrences": [i for m in matches for i in m.get("input_occurrences", [])],
            **({"acquisition": matches[0].get("acquisition", {})} if row.get("risk_type") == ACQUIRE_TYPE and matches else {}),
            "evidence_validated": True}
