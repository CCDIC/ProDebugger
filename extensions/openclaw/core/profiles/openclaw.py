"""Deterministic profile for imported OpenClaw tool telemetry.

The importer supplies JSON source envelopes, not reconstructed reasoning.
Execution status is an observation about one call and never task satisfaction.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from kinds import Kinds
from record import Constraint, Entity, Goal
from relations import FUNCTIONAL, Relations

name = "openclaw"
VERSION = "openclaw-tool-envelope/v1"


def kinds() -> Kinds:
    result = Kinds()
    result.declare("thing")
    for kind in ("trace", "call", "tool", "agent", "message"):
        result.declare(kind, "thing")
    for kind in ("attribute", "value", "req", "int"):
        result.declare(kind)
    return result


def relations() -> Relations:
    result = Relations()
    result.declare("session_context", 1, (FUNCTIONAL,),
                   arg_kinds=("trace",), value_types=("string",))
    for relation in ("message_content", "message_role"):
        result.declare(relation, 1, (FUNCTIONAL,),
                       arg_kinds=("message",), value_types=("string",))
    result.declare("tool_call", 2, (FUNCTIONAL,), key=(0,),
                   arg_kinds=("call", "tool"), value_types=("string",))
    for relation in ("tool_result", "tool_error", "tool_status"):
        result.declare(relation, 1, (FUNCTIONAL,),
                       arg_kinds=("call",), value_types=("string",))
    result.declare("admissibility", 1, (FUNCTIONAL,),
                   arg_kinds=("int",), value_types=("string",))
    result.declare("satisfied", 1, (FUNCTIONAL,),
                   arg_kinds=("req",), value_types=("boolean", "null"))
    # Preserve any explicitly supplied prose without interpreting it as reasoning.
    for relation in ("recalled_summary", "assessed", "planned"):
        result.declare(relation, 1, arg_kinds=("agent",), value_types=("string",))
    for relation in ("recalled_nothing", "memory_unparsed"):
        result.declare(relation, 1, arg_kinds=("agent",), value_types=("boolean",))
    return result


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError("non-finite JSON value: " + value)


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON values must be finite")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def envelope(text: str, expected: Optional[str] = None) -> Dict[str, Any]:
    """Validate the complete JSON envelope; unknown data belongs in provenance."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("tool source requires a nonempty JSON string")
    data = json.loads(text, object_pairs_hook=_object, parse_constant=_bad_constant)
    if not isinstance(data, dict):
        raise ValueError("tool source envelope must be an object")
    _finite(data)
    schemas = {
        "tool_call": ({"type", "trace_id", "span_id", "tool", "arguments"}, {"provenance"}),
        "tool_context": ({"type", "trace_id"}, {"provenance"}),
        "session_message": ({"type", "trace_id", "event_id", "role", "text", "content"},
                            {"provenance"}),
        "tool_result": ({"type", "trace_id", "span_id", "tool", "status"},
                        {"result", "error", "provenance"}),
    }
    event_type = data.get("type")
    if not isinstance(event_type, str) or event_type not in schemas:
        raise ValueError("unsupported tool source envelope type")
    if expected is not None and event_type != expected:
        raise ValueError("expected a " + expected + " envelope")
    required, optional = schemas[event_type]
    if not required <= data.keys() or data.keys() - required - optional:
        raise ValueError("missing or unknown " + event_type + " envelope fields")
    for key in ("trace_id", "span_id", "tool", "event_id", "role"):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise ValueError(key + " must be a nonempty string")
    if event_type == "tool_result" and data["status"] not in ("ok", "error", "unset"):
        raise ValueError("tool status must be ok, error or unset")
    if event_type == "session_message":
        if not isinstance(data["text"], str) or not isinstance(data["content"], (str, list)):
            raise ValueError("session message requires text and preserved content")
        content = data["content"]
        if isinstance(content, list) and any(not isinstance(item, dict) or not isinstance(item.get("type"), str)
                                             for item in content):
            raise ValueError("session content blocks require explicit types")
        extracted = (content if isinstance(content, str) else
                     "\n".join(item["text"] for item in content
                               if item.get("type") == "text" and isinstance(item.get("text"), str)))
        if data["text"] != extracted:
            raise ValueError("session message text must exactly reflect recorded text blocks")
    # provenance is optional JSON evidence; do not require particular vendor keys.
    return data


def call_id(data: Dict[str, Any]) -> str:
    return data["trace_id"] + "/" + data["span_id"]


def parse_goal(task: str) -> List[Goal]:
    if not isinstance(task, str) or not task.strip():
        raise ValueError("OpenClaw requires the original nonempty task")
    return [Goal(
        1, "thing", "satisfied", None, name="task_contract",
        constraints=(Constraint(Entity("attribute", "description"), "semantic_match",
                                task, "text", mode="semantic", span=task),),
        status_mode="unmodeled",
    )]


def _fact(relation, args, value, text):
    return {"rel": relation, "args": args, "value": value,
            "value_type": "string", "span": text}


def parse_observation(text: str, admissible=None) -> List[Dict[str, Any]]:
    data = envelope(text)
    if data["type"] == "tool_context":
        return [_fact("session_context", [("trace", data["trace_id"])], text, text)]
    if data["type"] == "session_message":
        args = [("message", data["trace_id"] + "/" + data["event_id"])]
        return [_fact("message_content", args, text, text),
                _fact("message_role", args, data["role"], text)]
    if data["type"] != "tool_result":
        raise ValueError("an observation must be tool_context, tool_result or session_message")
    args = [("call", call_id(data))]
    facts = [_fact("tool_result", args, text, text),
             _fact("tool_status", args, data["status"], text)]
    if data["status"] == "error" or ("error" in data and data["error"] is not None):
        facts.append(_fact("tool_error", args, text, text))
    # An error can follow a partial side effect. Never synthesize effect=none.
    return facts


def parse_action(text: Optional[str], admissible=None) -> Optional[Dict[str, Any]]:
    if text is None:
        return None
    data = envelope(text, "tool_call")
    return {"rel": "tool_call", "args": [("call", call_id(data)), ("tool", data["tool"])],
            "value": text, "value_type": "string", "span": text,
            "admissibility": "unsupported"}


def parse_memory(text, known_values=None):
    return []


def parse_intention(plan_text, admissible=None, goals=()):
    return None


def decompose(plan_text):
    return []


def bears_on(item, goals):
    return None


def situating_relation():
    return "session_context"


def null_verb():
    return "tool_call"


def commits():
    return set()


def domain_briefing():
    return {
        "name": name,
        "task_shape": "inspect recorded session messages, sequential tool calls and recorded responses",
        "evidence_rules": [
            "tool arguments, results, errors and span provenance are source evidence",
            "session messages are recorded utterances, not reconstructed memory, reflection or plan",
            "missing memory, reflection and plan are unknown, never reconstructed",
            "tool success does not establish that the user's task succeeded",
            "an error does not establish that no side effects occurred",
            "admissibility is unsupported without an independently recorded action contract",
            "the task remains unmodeled unless separately verified against explicit conditions",
        ],
    }
