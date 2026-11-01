"""Exact public-message source access shared by original diagnostic stages."""
from __future__ import annotations
import json


def is_session(source):
    return isinstance(source, dict) and source.get("task_type") == "openclaw" and source.get("session", {}).get("schema") in {"openclaw-session/v1", "openclaw-session/v2", "openclaw-signoz-session/v1"}


def message_event(source, step):
    if not is_session(source):
        return None
    return next((event for event in source["session"].get("events", [])
                 if event.get("step") == step and event.get("type") == "session_message"), None)


def source_text(source, step, module):
    if module == "message":
        event = message_event(source, step)
        return event.get("text", "") if event else ""
    row = source["steps"][step - 1]
    return row["env_text"] if module == "observation" else row["parts"].get(module) or ""


def feedback_text(source, step):
    """Recorded tool response by identity; chronological next text is not it."""
    result = feedback_location(source, step)
    return result["text"] if result else None


def feedback_location(source, step):
    if not is_session(source):
        return {"step": step + 1, "text": source["steps"][step]["env_text"]} if step < len(source["steps"]) else None
    action = source["steps"][step - 1]["parts"].get("action")
    if not action:
        return None
    call = json.loads(action)
    for item in source["steps"]:
        if item["t"] <= step:
            continue
        value = json.loads(item["env_text"])
        if value.get("type") == "tool_result" and all(value.get(key) == call.get(key) for key in ("trace_id", "span_id", "tool")):
            return {"step": item["t"], "text": item["env_text"]}
    return None


def slots_for(source):
    from local_judgement import SLOTS
    return {**SLOTS, **({"message": "obs"} if is_session(source) else {})}


MESSAGE_FACTS = {"session_final_answer", "session_public_statement", "session_message_text", "session_explicit_plan"}


def owned_facts(facts, source, step, module):
    slots = slots_for(source)
    if module not in slots:
        return []
    event = message_event(source, step) if module == "message" else None
    if module == "message" and (not event or event.get("role") != "assistant"):
        return []
    return [fact for fact in facts if fact.t == step and fact.side == "mind" and fact.slot == slots[module]
            and (module != "message" or fact.rel in MESSAGE_FACTS)]


def fact_source_module(fact, source):
    if (fact.rel in MESSAGE_FACTS or fact.rel in {"message_content", "message_role", "session_task_text"}) and message_event(source, fact.t):
        return "message"
    if is_session(source) and fact.rel in {"session_file_path", "session_read_target"}:
        return "action"
    return {"mem": "memory", "refl": "reflection", "plan": "plan", "act": "action", "obs": "observation"}.get(fact.slot)
