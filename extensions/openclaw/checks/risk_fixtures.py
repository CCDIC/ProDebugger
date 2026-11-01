"""Synthetic, unexecuted fixtures retained from the original regression suite."""
from copy import deepcopy
import hashlib
import json
from prodebugger.session_import import convert_session

def policy():
    return {"schema": "prodebugger-risk-policy/v1", "id": "workspace-behavior", "version": "1",
        "path_style": "posix", "working_directory": "/workspace",
        "tools": {
            "read_file": {"kind": "read", "path_arg": "path"},
            "write_file": {"kind": "write", "path_arg": "path", "content_arg": "content"},
            "send_email": {"kind": "send", "target_arg": "to", "payload_args": ["body"],
                "mode_arg": "mode", "draft_values": ["draft"], "requires_approval": True},
            "web_fetch": {"kind": "network", "target_arg": "url", "payload_args": [],
                "method_arg": "method", "effect_methods": ["POST"], "requires_approval": False},
            "http_request": {"kind": "network", "target_arg": "url", "payload_args": ["body"],
                "method_arg": "method", "effect_methods": ["POST", "PUT", "PATCH", "DELETE"], "requires_approval": False}},
        "read_scope": {"allowed_roots": ["/workspace"], "forbidden_paths": []},
        "approvals": [], "sensitive_sources": [], "protected_writes": [], "untrusted_sources": []}


def safety_policy():
    from prodebugger.safety_workbench import default_policy
    result = default_policy("/workspace")
    result["allowed_network_hosts"] = [{"host": "docs.example.test"}, {"host": "collector.example.test"}]
    # Existing auditor has no send binding; the new approval rule adds it explicitly.
    return result


def fixture(cid, task, actions, final="已返回本次记录的处理结果。"):
    trace = hashlib.sha256(cid.encode()).hexdigest()[:32]
    session = "session-" + cid
    steps, maps, messages = [], [], [{"role": "user", "content": task}]
    previous = {"type": "tool_context", "trace_id": trace,
                "provenance": {"source": "constructed_fixture_not_executed"}}
    for number, action in enumerate(actions, 1):
        tool, args, payload = action[:3]
        status = action[3] if len(action) > 3 else "ok"
        span = hashlib.sha256((cid + str(number)).encode()).hexdigest()[:16]
        call_id = "call-" + str(number)
        provenance = {"tool_call_id": call_id, "source": "constructed_fixture_not_executed"}
        call = {"type": "tool_call", "trace_id": trace, "span_id": span, "tool": tool,
                "arguments": args, "provenance": provenance}
        response = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                    "details": payload, "executed": status != "error", "ok": status != "error"}
        result = {"type": "tool_result", "trace_id": trace, "span_id": span, "tool": tool,
                  "status": status, "result": response, "provenance": provenance}
        encoded = json.dumps(call, ensure_ascii=False, separators=(",", ":"))
        steps.append({"t": number, "env_text": json.dumps(previous, ensure_ascii=False),
            "agent_text": encoded, "admissible": [],
            "parts": {"memory": None, "reflection": None, "plan": None, "action": encoded}})
        maps.append({"trace_id": trace, "span_id": span, "tool": tool, "action_step": number,
                     "result_step": number + 1, "source": "generated_tool_envelopes"})
        messages.extend([
            {"role": "assistant", "content": [{"type": "toolCall", "id": call_id,
             "name": tool, "arguments": deepcopy(args)}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolCallId": call_id, "toolName": tool,
             "isError": status == "error", **deepcopy(response)}])
        previous = result
    steps.append({"t": len(steps) + 1, "env_text": json.dumps(previous, ensure_ascii=False),
        "agent_text": "", "admissible": [], "parts": {"memory": None, "reflection": None, "plan": None, "action": None}})
    base = {"trajectory_id": cid, "task_type": "openclaw", "task": task, "steps": steps,
        "telemetry": {"trace_id": trace, "session_id": session, "source_map": maps,
            "data_origin": "constructed_fixture_not_executed", "coverage": {
                "declared_complete": True, "content_complete": True, "gaps": [],
                "scope": "complete generated fixture; no real Agent execution or hidden cognition"}}}
    messages.append({"role": "assistant", "content": [{"type": "text", "text": final}], "stopReason": "stop"})
    native = {"schema": "openclaw-native-transcript/v1", "session_id": session,
        "data_origin": "constructed_fixture_not_executed", "capture_complete": False,
        "source": {"kind": "constructed_fixture", "executed": False},
        "events": [{"seq": 0, "event": {"type": "session", "id": session}}] + [
            {"seq": n, "event": {"type": "message", "id": "message-" + str(n), "message": value}}
            for n, value in enumerate(messages, 1)]}
    source, _ = convert_session(base, native, trajectory_id=cid, source_label="构造验收轨迹 · 未实际执行")
    return source
