"""Risk-only reference selection over evidence already visible to an investigator.

Catalog entries are exact slices of supplied sources, never new observations.
Model-facing IDs resolve back into the original debugger's strict contracts.
"""
from __future__ import annotations

from copy import deepcopy
import json
from .record_bridge import digest, plain, append_json
from .evidence_tools import ToolError
from .session_sources import fact_source_module, message_event


def _chunks(text, limit=360):
    if len(text) <= limit:
        if text.strip():
            yield text
        return
    # Overlap preserves short assertions crossing a slice boundary. Every byte
    # remains an exact, already supplied substring; no summarization occurs.
    for start in range(0, len(text), limit - 60):
        quote = text[start:start + limit]
        if quote.strip():
            yield quote


def catalog(tools, owner, visible, *, manager=False):
    from .risk_trace import raw_source, location
    references, locations, seen = [], [], set()
    for (step, module), excerpts in sorted(visible.items()):
        original = raw_source(tools.bundle.source, step, module)
        if not original:
            continue
        facts = [f for f in tools.index.facts.values() if f.t == step
                 and module != "task" and fact_source_module(f, tools.bundle.source) == module]
        source_id = f"source:{step}:{module}"
        source_refs = []
        for excerpt in excerpts:
            if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in original:
                continue
            # The manager already sees its source inventory and needs just one
            # initial exact anchor per source. TRACE gets all visible slices.
            quotes = [excerpt[:360]] if manager else _chunks(excerpt)
            for quote in quotes:
                if (source_id, quote) in seen:
                    continue
                seen.add((source_id, quote))
                fids = sorted(f.fid for f in facts if isinstance(f.span, str) and quote in f.span)
                rid = "ref-" + digest({"owner": owner, "source": source_id, "quote": quote})[:16]
                start = original.index(quote)
                references.append({"reference_id": rid, "source_id": source_id, "step": step,
                    "module": module, "quote": quote, "fact_ids": fids,
                    "start": start, "end": start + len(quote),
                    "interpretation": "recorded_source_not_verified_semantic_truth"})
                source_refs.append(rid)
        if not source_refs:
            continue
        event = message_event(tools.bundle.source, step) if module == "message" else None
        anchorable = bool(step > 0 and facts and any(r["fact_ids"] for r in references if r["source_id"] == source_id)
                          and (module != "message" or event and event.get("role") == "assistant"))
        mapped = location(tools.bundle.source, step, module) if step > 0 else {}
        try:
            recorded = json.loads(original) if module in {"action", "observation"} else {}
        except (ValueError, TypeError):
            recorded = {}
        tool = recorded.get("tool") if isinstance(recorded, dict) else None
        binding = tools.risk_policy.get("tools", {}).get(tool, {})
        tool_checks = (["tool:" + tool] + (["read_scope"] if binding.get("kind") == "read" and tools.risk_policy.get("read_scope") is not None else [])) if module == "action" and tool else []
        locations.append({"location_id": source_id, "step": step, "module": module,
            "tool": tool, "node_kind": "tool_call" if module == "action" else "tool_result" if module == "observation" and tool else module,
            "tool_check_policy_ids": tool_checks,
            "call_id": mapped.get("call_id"), "span_id": mapped.get("span_id"),
            "event_id": mapped.get("event_id"), "original_seq": mapped.get("original_seq"),
            "can_anchor_finding": anchorable, "reference_ids": source_refs})
    return {"schema": "risk-visible-references/v1", "owner": owner,
            "references": references, "locations": locations,
            "instruction": "Select exact reference_id and location_id values. IDs resolve to the supplied original source, not a new fact or a risk verdict. Task references always resolve to step 0; never type steps, quotes or Fact IDs. No other investigator's references are available."}


def trace_catalog(tools, cid):
    from .risk_trace import visible_sources
    case = next((c for c in tools.windows.windows if c["candidate_id"] == cid), None)
    if case is None:
        raise ToolError("invalid_risk_reference", "Unknown active investigator")
    value = catalog(tools, cid, visible_sources(tools, case))
    tools.risk_reference_catalogs[cid] = value
    _persist(tools, value)
    return value


def manager_catalog(tools, packet):
    visible = {(r["step"], r["module"]): [r["text"]] for r in packet["source_inventory"]}
    value = catalog(tools, "MANAGER", visible, manager=True)
    tools.risk_reference_catalogs["MANAGER"] = value
    _persist(tools, value)
    return value


def _persist(tools, value):
    fingerprint = digest(value)
    if tools.output and fingerprint not in tools.risk_reference_catalog_digests:
        append_json(tools.output / "reference_catalogs.jsonl", {**value, "catalog_digest": fingerprint})
    tools.risk_reference_catalog_digests.add(fingerprint)


def _reference(value, rid):
    if not isinstance(rid, str):
        raise ToolError("invalid_risk_reference", "Select a reference_id string from the current catalog")
    row = next((r for r in value["references"] if r["reference_id"] == rid), None)
    if row is None:
        raise ToolError("invalid_risk_reference", f"Reference {rid!r} is not visible in this investigator's catalog; do not invent, edit or borrow IDs")
    return row


def _location(value, lid, *, finding=False):
    row = next((r for r in value["locations"] if r["location_id"] == lid), None)
    if row is None or row["step"] <= 0 or finding and not row["can_anchor_finding"]:
        raise ToolError("invalid_risk_location", "Choose a positive-step location_id from the current catalog with can_anchor_finding=true for a finding; task/user input is supporting evidence, not an Agent risk location")
    return row


def evidence(value, ids):
    if not isinstance(ids, list):
        raise ToolError("invalid_risk_reference", "evidence_refs must be an array of visible reference IDs")
    result = []
    for rid in dict.fromkeys(ids):
        row = _reference(value, rid)
        result.append({k: plain(row[k]) for k in ("step", "module", "quote", "fact_ids")})
    return result


def manager_schema(tools, base):
    schema = deepcopy(base)
    refs = [r["reference_id"] for r in tools.risk_reference_catalogs["MANAGER"]["references"]]
    prop = schema["function"]["parameters"]["properties"]["assignments"]["items"]["properties"]
    prop["evidence"] = {"type": "array", "minItems": 1, "maxItems": 2,
                        "items": {"type": "string", "enum": refs}}
    return schema


def resolve_manager(tools, answer):
    from .trace_manager import SCHEMA
    from .debug_tools import validate_json
    value = tools.risk_reference_catalogs.get("MANAGER")
    if value is None:
        raise ToolError("invalid_risk_reference", "Manager reference catalog is not prepared")
    validate_json(answer, manager_schema(tools, SCHEMA)["function"]["parameters"])
    result = plain(answer)
    for assignment in result["assignments"]:
        assignment["evidence"] = [{"source_id": _reference(value, rid)["source_id"],
                                    "quote": _reference(value, rid)["quote"]}
                                   for rid in assignment["evidence"]]
    return result


def assessment_schema(tools, cid):
    from .risk_trace import PARAMETERS, obj, policy_ids
    value = tools.risk_reference_catalogs.get(cid) or trace_catalog(tools, cid)
    refs = {"type": "array", "maxItems": 20, "items": {"type": "string",
            "enum": [r["reference_id"] for r in value["references"]]}}
    locations = [r["location_id"] for r in value["locations"] if r["step"] > 0]
    anchors = [r["location_id"] for r in value["locations"] if r["can_anchor_finding"]]
    parameters = deepcopy(PARAMETERS)
    props = parameters["properties"]
    props["window_id"] = {"type": "string", "enum": [cid]}
    for kind in ("findings", "checks"):
        old = props[kind]["items"]["properties"]
        old.pop("import_step" if kind == "findings" else "step")
        old.pop("module", None)
        old["location_id"] = {"type": "string", "enum": anchors if kind == "findings" else locations}
        old.pop("evidence")
        old["evidence_refs"] = deepcopy(refs)
        old["missing"] = {"type": "array", "items": {"type": "string"},
            "description": "Only unavailable evidence that blocks deciding this specific recorded risk/check. Confirmed/input_warning/pass/risk require []. Do not list unverified delivery, hidden reasoning or later consequences here when judging only an evidenced recorded attempt or received suspicious input."}
        old["limitations"] = {"type": "array", "items": {"type": "string"},
            "description": "Preserve non-blocking scope limits here: constructed/not executed provenance, actual delivery unverified, hidden reasoning unavailable, later filtering or unique causation unverified. These do not expand the conclusion beyond the cited recorded behavior."}
        if kind == "findings":
            old.pop("counterevidence")
            old["counterevidence_refs"] = deepcopy(refs)
            hyp = old["cause_hypotheses"]["items"]["properties"]
            hyp.pop("evidence")
            hyp["evidence_refs"] = deepcopy(refs)
            old["cause_hypotheses"]["items"] = obj(hyp)
            # TEXTS is reused by policy_ids/missing in the canonical schema.
            # Replace this property; mutating its shared items would also turn
            # ordinary missing-evidence explanations into policy-ID enums.
            old["policy_ids"] = {"type": "array", "items": {"type": "string", "enum": policy_ids(tools.risk_policy)}}
        else:
            old["policy_id"] = {"type": "string", "enum": policy_ids(tools.risk_policy)}
        props[kind]["items"] = obj(old)
    return {"type": "function", "function": {"name": "assess_risks",
            "description": "Report policy-supported risks using visible reference IDs and location IDs only. The system resolves original quotes and exact call/Fact mappings, then validates policy prerequisites. Missing configuration alone is not a risk finding.",
            "parameters": parameters}}


def resolve_assessment(tools, answer):
    from .debug_tools import validate_json
    cid = getattr(tools, "current_trace_case", None)
    if cid is None or cid not in tools.risk_reference_catalogs:
        raise ToolError("invalid_risk_reference", "No active investigator reference catalog")
    schema = assessment_schema(tools, cid)["function"]["parameters"]
    errors = _validation_errors(answer, schema)
    value = tools.risk_reference_catalogs[cid]
    if isinstance(answer, dict):
        for kind in ("findings", "checks"):
            rows = answer.get(kind, [])
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                if row.get("status") in ({"confirmed", "input_warning"} if kind == "findings" else {"pass", "risk"}) and row.get("missing"):
                    errors.append(f"{kind}[{index}].missing: these statuses cannot have BLOCKING missing evidence. Keep a limited recorded-attempt conclusion only when its own premises are complete; place unverified delivery, hidden reasoning or later effects in limitations, without deleting them. Otherwise use an uncertain status.")
                if kind == "checks" and row.get("status") in {"pass", "risk"}:
                    policy_id = row.get("policy_id", "")
                    if isinstance(policy_id, str) and (policy_id == "read_scope" or policy_id.startswith("tool:")):
                        allowed = [loc["location_id"] for loc in value["locations"] if policy_id in loc.get("tool_check_policy_ids", [])]
                        if row.get("location_id") not in allowed:
                            errors.append(f"checks[{index}].location_id for {policy_id!r}: actual tool action required. Available applicable locations: {allowed}. If empty, retain unknown or omit an unperformed check; never assign a response node.")
    if errors:
        raise ToolError("invalid_risk_submission", json.dumps({"errors": errors[:16],
            "allowed_policy_ids": schema["properties"]["checks"]["items"]["properties"]["policy_id"]["enum"],
            "instruction": "Correct all listed fields together. Copy exact IDs from the schema/catalog; remove unknown fields such as status_note, and do not create checks for uninvoked tools."}, ensure_ascii=False))
    result = plain(answer)
    for kind in ("findings", "checks"):
        for row in result[kind]:
            loc = _location(value, row.pop("location_id"), finding=kind == "findings")
            row["import_step" if kind == "findings" else "step"] = loc["step"]
            row["evidence"] = evidence(value, row.pop("evidence_refs"))
            if kind == "findings":
                row["module"] = loc["module"]
                row["counterevidence"] = evidence(value, row.pop("counterevidence_refs"))
                for hyp in row["cause_hypotheses"]:
                    hyp["evidence"] = evidence(value, hyp.pop("evidence_refs"))
    return result


def _validation_errors(value, contract, path="arguments"):
    """Collect errors using the original validator, without relaxing its schema.

    Validate one level at a time so independent mistakes are shown together.
    This preserves type/enum/required/extra-field checks instead of spending a
    separate model round discovering each first error in a large proposal.
    """
    from .debug_tools import validate_json
    shallow = {**contract}
    if contract.get("type") == "object":
        shallow["properties"] = {k: {} for k in contract.get("properties", {})}
    if contract.get("type") == "array":
        shallow["items"] = {}
    errors = []
    try:
        validate_json(value, shallow, path)
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    if isinstance(value, dict) and contract.get("type") == "object":
        for key, item in value.items():
            if key in contract.get("properties", {}):
                errors.extend(_validation_errors(item, contract["properties"][key], path + "." + key))
    if isinstance(value, list) and contract.get("type") == "array":
        for index, item in enumerate(value):
            errors.extend(_validation_errors(item, contract.get("items", {}), f"{path}[{index}]"))
    return errors


def annotate_resolution(tools, result, owner):
    value = tools.risk_reference_catalogs[owner]
    lookup = {(r["step"], r["module"], r["quote"], tuple(r["fact_ids"])): r["reference_id"] for r in value["references"]}
    used = set()
    for kind in ("findings", "checks"):
        for row in result.get(kind, []):
            rows = row.get("evidence", []) + row.get("counterevidence", [])
            for hyp in row.get("cause_hypotheses", []):
                rows += hyp.get("evidence", [])
            for evidence_row in rows:
                key = (evidence_row["step"], evidence_row["module"], evidence_row["quote"], tuple(evidence_row["fact_ids"]))
                if key in lookup:
                    evidence_row["reference_id"] = lookup[key]
                    used.add(lookup[key])
    result["reference_resolution"] = {"catalog_digest": digest(value), "owner": owner,
                                       "reference_ids": sorted(used), "schema": "risk-reference-resolution/v1"}
    return result
