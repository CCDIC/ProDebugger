"""Evaluate explicit tool-result task contracts, never infer goals from telemetry.

The small v1 contract is intentionally limited to obtaining declared tool results.
It cannot certify a final natural-language answer, external side effects, or an
arbitrary user request. A caller must declare that the checks exhaust its task.
"""
from __future__ import annotations

import json
import re


SCHEMA = "openclaw-tool-contract/v1"


def _pointer(value, pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("contract keys must be JSON pointers (for example /path)")
    if not pointer:
        return value
    for part in pointer[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise ValueError("invalid JSON pointer escape")
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", part):
                raise KeyError(part)
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise KeyError(part)
    return value


def _equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_equal(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _matches(value, predicates):
    for pointer, expected in predicates.items():
        try:
            actual = _pointer(value, pointer)
        except (KeyError, IndexError):
            return False
        # JSON true is not the number 1.
        if not _equal(actual, expected):
            return False
    return True


def validate(contract):
    if not isinstance(contract, dict) or set(contract) != {"schema", "complete", "checks"}:
        raise ValueError("task_contract requires schema, complete and checks")
    if contract["schema"] != SCHEMA or type(contract["complete"]) is not bool:
        raise ValueError("unsupported task contract schema or completeness value")
    if not isinstance(contract["checks"], list) or not 1 <= len(contract["checks"]) <= 20:
        raise ValueError("task contract requires 1..20 checks")
    ids = set()
    for check in contract["checks"]:
        if not isinstance(check, dict) or set(check) != {"id", "tool", "argument_equals", "result_equals"}:
            raise ValueError("each check requires id, tool, argument_equals and result_equals")
        if any(not isinstance(check[k], str) or not check[k].strip() for k in ("id", "tool")):
            raise ValueError("check ID and tool must be nonempty strings")
        if check["id"] in ids:
            raise ValueError("duplicate contract check ID")
        ids.add(check["id"])
        for key in ("argument_equals", "result_equals"):
            if not isinstance(check[key], dict) or not check[key]:
                raise ValueError("contract predicates must be nonempty JSON-pointer maps")
            for pointer, expected in check[key].items():
                if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
                    raise ValueError("contract keys must be JSON pointers")
                if re.search(r"~(?![01])", pointer):
                    raise ValueError("invalid JSON pointer escape")
                json.dumps(expected, allow_nan=False)
    return contract


def evaluate(source):
    """Recompute outcome from captured calls/results plus explicit requirements.

    Never accept a precomputed success/failure label from the importer or model.
    Absence only refutes a requirement when the capture is declared complete and
    the importer found no lost/truncated/missing events. Status UNSET is common in
    OpenClaw: matching captured output can establish this limited output contract;
    the span status alone cannot.
    """
    outcome = {"status": "unknown", "evidence": [], "checks": [], "limitations": [],
               "provenance": "deterministic explicit tool-result contract; not a general semantic verdict"}
    contract = source.get("task_contract")
    if contract is None:
        outcome["limitations"].append("No explicit, complete task contract was supplied.")
        return outcome
    try:
        validate(contract)
    except (ValueError, TypeError) as exc:
        outcome["limitations"].append("Invalid task contract: " + str(exc))
        return outcome
    telemetry = source.get("telemetry") or {}
    coverage = telemetry.get("coverage", {}) if isinstance(telemetry, dict) else {}
    coverage = coverage if isinstance(coverage, dict) else {}
    complete_capture = coverage.get("declared_complete") is True and coverage.get("content_complete") is True
    if not complete_capture:
        outcome["limitations"].append("Capture completeness is not established; missing events are unknown.")
    if not contract["complete"]:
        outcome["limitations"].append("Declared checks do not exhaust the original task.")
    calls, results = {}, []
    try:
        # Import lazily: record_bridge establishes the canonical core import path.
        from .record_bridge import PROFILES
        envelope = PROFILES["openclaw"].envelope
        pending = None
        session_mode = bool(source.get("session"))
        trace_id = source.get("telemetry", {}).get("trace_id") if session_mode else None
        seen_results = set()
        steps = source["steps"]
        if not isinstance(steps, list) or len(steps) < 2:
            raise ValueError("sequential source needs context, a call and its terminal response")
        for number, step in enumerate(steps, 1):
            if type(step["t"]) is not int or step["t"] != number:
                raise ValueError("source steps must be consecutive and one-based")
            item = envelope(step["env_text"])
            if session_mode:
                if item.get("trace_id") != trace_id:
                    raise ValueError("session events must stay in the bound trace")
                if item["type"] == "tool_result":
                    identity = (item["trace_id"], item["span_id"])
                    call = calls.get(identity)
                    if identity in seen_results or call is None or call[1]["tool"] != item["tool"]:
                        raise ValueError("result must uniquely answer an earlier matching call")
                    seen_results.add(identity)
                    results.append((step, item))
                elif item["type"] not in {"session_message", "tool_context"}:
                    raise ValueError("unsupported session observation")
            elif number == 1:
                if item["type"] != "tool_context":
                    raise ValueError("the first observation must be tool_context")
                trace_id = item["trace_id"]
            else:
                if item["type"] != "tool_result" or pending is None:
                    raise ValueError("each later observation must answer the preceding call")
                identity = (item["trace_id"], item["span_id"])
                if identity in seen_results or identity != pending[:2] or item["tool"] != pending[2]:
                    raise ValueError("result identity/tool must uniquely match the preceding call")
                seen_results.add(identity)
                results.append((step, item))
                pending = None
            action = step["parts"].get("action")
            if action is None:
                if not session_mode and (number != len(steps) or number == 1):
                    raise ValueError("only the final result step may omit an action")
            else:
                item = envelope(action, "tool_call")
                identity = (item["trace_id"], item["span_id"])
                if item["trace_id"] != trace_id or identity in calls:
                    raise ValueError("call identities must be unique within one trace")
                calls[identity] = (step["t"], item)
                pending = (*identity, item["tool"])
        if not calls or (not session_mode and pending is not None) or (session_mode and set(calls) != seen_results):
            raise ValueError("the terminal result is missing")
    except (ValueError, KeyError, TypeError, AttributeError):
        outcome["limitations"].append("Malformed structured source; outcome cannot be evaluated.")
        return outcome
    for check in contract["checks"]:
        matches, considered = [], []
        for step, result in results:
            identity = (result.get("trace_id"), result.get("span_id"))
            call = calls.get(identity)
            if call is None or call[1].get("tool") != check["tool"] or result.get("tool") != check["tool"]:
                continue
            if step["t"] <= call[0] or not _matches(call[1].get("arguments"), check["argument_equals"]):
                continue
            considered.append(step["t"])
            provenance = result.get("provenance", {})
            provenance = provenance if isinstance(provenance, dict) else {}
            blocked = provenance.get("outcome") == "blocked"
            explicit_error = (result.get("status") == "error" or result.get("error") is not None
                              or provenance.get("outcome") == "error")
            payload = result.get("result")
            if isinstance(payload, dict) and payload.get("isError") is True:
                explicit_error = True
            if (not blocked and not explicit_error and "result" in result
                    and _matches(payload, check["result_equals"])):
                matches.append(step["t"])
                outcome["evidence"].append({"step": step["t"], "module": "observation",
                    "quote": step["env_text"], "trace_id": identity[0], "span_id": identity[1],
                    "check_id": check["id"]})
        state = "satisfied" if matches else "unmet" if complete_capture else "unknown"
        if not matches:
            for step, result in results:
                if step["t"] in considered:
                    outcome["evidence"].append({"step": step["t"], "module": "observation",
                        "quote": step["env_text"], "trace_id": result["trace_id"],
                        "span_id": result["span_id"], "check_id": check["id"],
                        "role": "observed_nonmatching_result"})
        outcome["checks"].append({**check, "state": state, "matching_steps": matches,
                                  "considered_result_steps": considered})
    if contract["complete"] and complete_capture:
        outcome["status"] = "success" if all(c["state"] == "satisfied" for c in outcome["checks"]) else "failure"
    outcome["limitations"].append("Outcome is limited to the supplied tool-result contract; no claim about unrecorded reasoning or final-answer quality.")
    return outcome
