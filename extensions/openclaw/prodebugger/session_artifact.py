"""Evidence-bound session states on the original Record/Fact version machinery.

This extension only runs for explicit ``openclaw-session/v1`` inputs. A public
utterance is an utterance, not a memory; temporal proximity is never support.
The sidecar describes source locations and the meaning of the original Fact IDs.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import ntpath
import re

SCHEMA = "prodebugger-session-artifact/v1"
REL_LABELS = {
    "session_message_text": "消息原文", "session_public_statement": "Agent 公开表达",
    "session_final_answer": "最终回答", "session_task_text": "用户任务原文",
    "session_contract": "显式任务约束", "session_file_path": "文件路径",
    "session_read_target": "读取目标", "session_file_content": "读取到的内容",
    "session_file_exists": "文件存在状态（读取时）", "session_read_result": "读取结果",
    "session_explicit_plan": "明确表达的下一步操作", "tool_call": "工具调用参数",
    "tool_result": "工具返回原文", "tool_status": "工具运行状态", "tool_error": "工具运行错误",
}


def _json(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _text(value):
    """Only concatenate explicitly typed text; never stringify a model's thought."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        return _text(value.get("content"))
    if isinstance(value, list):
        return "\n".join(item["text"] for item in value if isinstance(item, dict)
                         and item.get("type") == "text" and isinstance(item.get("text"), str))
    return ""


def _call_key(event):
    if event.get("trace_id") and event.get("span_id"):
        return str(event["trace_id"]) + "/" + str(event["span_id"])
    return None


def _has_text(value):
    if isinstance(value, str):
        return True
    if isinstance(value, dict):
        return isinstance(value.get("text"), str) or _has_text(value.get("content"))
    if isinstance(value, list):
        return any(isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str) for item in value)
    return False


def _path(arguments):
    if not isinstance(arguments, dict):
        return None
    paths = [arguments[key] for key in ("path", "file_path", "filePath")
             if isinstance(arguments.get(key), str) and arguments[key]]
    # Conflicting aliases do not supply a unique target.
    return paths[0] if paths and len(set(paths)) == 1 else None


def _not_found(data):
    """A permission error, timeout or unknown status cannot prove absence."""
    error = data.get("error")
    error_text = str(error or "")
    body = data.get("result")
    if isinstance(body, dict):
        error_text += " " + str(body.get("error") or "")
        if body.get("isError") is True or data.get("status") == "error":
            error_text += " " + _text(body)
    return bool(re.search(r"\b(?:ENOENT|File not found|No such file or directory)\b|文件不存在", error_text, re.I))


def _result_error(data):
    body = data.get("result")
    return (data.get("status") == "error" or data.get("error") is not None
            or (isinstance(body, dict) and (body.get("isError") is True or body.get("status") == "error")))


def _file_id(path):
    # Preserve spelling and do not silently conflate two case-sensitive paths.
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]


def _verify_events(source, events):
    """Do not let a conflicting display sidecar silently create new evidence."""
    steps = source.get("steps", [])
    if not isinstance(events, list) or len(events) != len(steps):
        raise ValueError("session events must represent every imported step exactly once")
    if [event.get("step") for event in events] != list(range(1, len(steps) + 1)):
        raise ValueError("session event steps must be unique, consecutive and ordered")
    if len({event.get("event_id") for event in events}) != len(events):
        raise ValueError("session event IDs must be unique")
    for event, step in zip(events, steps):
        kind = event.get("type")
        envelope = _json(step.get("parts", {}).get("action") if kind == "tool_call" else step.get("env_text"))
        if envelope.get("type") != kind or envelope.get("trace_id") != event.get("trace_id"):
            raise ValueError("session event type/trace disagrees with its recorded input step")
        if kind == "session_message":
            if any(event.get(key) != envelope.get(key) for key in ("event_id", "role", "text", "content")):
                raise ValueError("session message sidecar disagrees with recorded message content")
        elif kind in {"tool_call", "tool_result"}:
            if event.get("span_id") != envelope.get("span_id"):
                raise ValueError("session tool event disagrees with recorded call identity")
        else:
            raise ValueError("unsupported session event type")


def _provenance(event, span, source):
    origin = deepcopy(event.get("source", {}))
    index = event["step"] - 1
    step = source["steps"][index]
    candidates = [(f"/steps/{index}/env_text", step.get("env_text")),
                  (f"/steps/{index}/parts/action", step.get("parts", {}).get("action")),
                  (f"/session/events/{index}/text", event.get("text"))]
    if span:
        for pointer, text in candidates:
            if isinstance(text, str) and span in text:
                start = text.index(span)
                origin.update(analysis_pointer=pointer, analysis_document="analysis_input",
                              analysis_start=start, analysis_end=start + len(span),
                              analysis_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
                break
    return origin


def augment_record(record, source):
    """Append derived, quoted session facts; repair unsupported automatic bases.

    Additions use Record.add so projection, chain(), in_force() and evidence
    queries keep canonical Fact identities. Repeated calls are linked by exact
    call ID only. An existing output→next-action edge is deliberately removed.
    """
    session = source.get("session", {})
    if source.get("task_type") != "openclaw" or session.get("schema") not in {"openclaw-session/v1", "openclaw-session/v2", "openclaw-signoz-session/v1"}:
        return record
    if getattr(record, "session_augmented", False):
        return record
    events = session.get("events", [])
    _verify_events(source, events)
    by_step = {event["step"]: event for event in events if type(event.get("step")) is int}
    fact_meta = {}
    record.session_fact_meta = fact_meta
    record.session_augmented = True
    record.session_semantic_notes = []
    for kind in ("message", "file"):
        if kind not in record.kinds:
            record.kinds.declare(kind, "thing")
    declarations = {
        "session_message_text": ("message",), "session_public_statement": ("agent",),
        "session_final_answer": ("agent",), "session_task_text": ("req",),
        "session_contract": ("req",), "session_file_path": ("file",),
        "session_read_target": ("call", "file"), "session_file_content": ("file",),
        "session_file_exists": ("file",), "session_read_result": ("call", "file"),
        "session_explicit_plan": ("agent",),
    }
    for rel, kinds in declarations.items():
        if rel not in record.relations:
            record.relations.declare(rel, len(kinds), ("functional", "mutable"),
                                     arg_kinds=kinds, value_types=("any",))
    calls = {str(f.args[0]): f for f in record.F if f.rel == "tool_call"}
    results = {}
    for f in record.F:
        event = by_step.get(f.t)
        if f.rel in {"message_content", "message_role"} and event:
            # The profile parses the envelope through observations, but a model
            # saying X is not environmental evidence that X actually holds.
            f.side = "mind" if event.get("role") == "assistant" else "task" if event.get("role") == "user" else "env"
        if f.rel == "tool_call":
            f.basis = ()
            f.situation = None
        elif f.slot == "obs":
            # Compiler prev_act is chronological, not an evidential dependency.
            target = calls.get(str(f.args[0])) if f.rel in {"tool_result", "tool_status", "tool_error"} else None
            f.basis = (target.fid,) if target and target.t < f.t else ()
            f.situation = None
        if event:
            status = "observed" if f.side == "env" else "asserted"
            fact_meta[f.fid] = {"source_events": [event["event_id"]], "source": _provenance(event, f.span, source),
                               "status": status, "derivation": "recorded_tool_envelope"}
        if f.rel == "tool_result":
            results.setdefault(str(f.args[0]), []).append(f)

    agent = record.ent("agent", "self")
    req = record.ent("req", "task_contract")

    def add(rel, args, value, event, *, side="env", basis=(), span=None, status="observed", derivation="explicit_record"):
        text = event.get("text", "")
        # The original abstract rule trigger treats every act Fact as a tool
        # action. Public utterances and derived target fields are observations
        # of recorded statements (mind side), never extra executed actions.
        fid = record.add(rel, args, value, "chk" if side == "task" else "obs",
                         side, event["step"], basis=basis,
                         span=text if span is None else span)
        fact_meta[fid] = {"source_events": [event["event_id"]], "source": _provenance(event, text if span is None else span, source),
                          "status": status, "derivation": derivation}
        return fid

    user = next((e for e in events if e.get("role") == "user"), None)
    if user:
        add("session_task_text", (req,), user.get("text", ""), user, side="task", status="required")
    contract = source.get("task_contract")
    if isinstance(contract, dict) and user:
        # Contract is an explicit input declaration, never claimed to be an
        # automatically extracted user quote or a complete task model.
        fid = add("session_contract", (req,), deepcopy(contract), user, side="task", span="",
                  status="required", derivation="explicit_task_contract")
        fact_meta[fid]["source"] = {"json_pointer": "/task_contract", "source_document": "analysis_input",
                                    "sha256": hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
        fact_meta[fid]["source_events"] = []

    for event in events:
        kind, role = event.get("type"), event.get("role")
        if kind == "session_message":
            text = event.get("text", "")
            if not isinstance(text, str):
                continue
            message_key = (str(event["trace_id"]) + "/" if event.get("trace_id") else "") + event["event_id"]
            message = record.ent("message", message_key)
            text_fid = add("session_message_text", (message,), text, event,
                           side="mind" if role == "assistant" else "task" if role == "user" else "env",
                           status="asserted" if role == "assistant" else "required" if role == "user" else "observed")
            if role == "assistant" and text:
                final = event.get("stop_reason") == "stop" or event.get("is_final") is True
                add("session_final_answer" if final else "session_public_statement", (agent,), text, event,
                    side="mind", basis=(text_fid,), status="asserted", derivation="public_utterance_not_internal_memory")
                # A narrow publicly stated plan is supported by a literal quote.
                # It is not a hidden plan, an intention hierarchy, or causal proof.
                plan = re.search(r"(?im)(?:^|(?<=[.!?])\s+)(?:I(?:'ll| will| am going to)\s+(?:read|open|check|search)\b[^\n]*|(?:我将|我会|接下来(?:我)?(?:将|会)?)\s*(?:读取|打开|检查|搜索)[^\n]*)", text)
                if plan and not final:
                    add("session_explicit_plan", (agent,), plan.group(0).strip(), event,
                        side="mind", basis=(text_fid,), span=plan.group(0).strip(), status="asserted",
                        derivation="explicit_public_future_action_only")
            continue
        if kind != "tool_call":
            continue
        key = _call_key(event)
        call = calls.get("call:" + key) if key else None
        if call is None:
            continue
        data = _json(call.value)
        if str(data.get("tool", "")).lower() not in {"read", "read_file", "readfile"}:
            continue
        path = _path(data.get("arguments"))
        if not path:
            continue
        file = record.ent("file", _file_id(path))
        add("session_file_path", (file,), path, event, basis=(call.fid,), span=call.span,
            derivation="read_argument_path")
        add("session_read_target", (call.args[0], file), path, event, side="mind", basis=(call.fid,),
            span=call.span, status="asserted", derivation="recorded_call_target")
        for result in results.get(str(call.args[0]), []):
            result_event = by_step.get(result.t)
            if not result_event:
                continue
            result_data = _json(result.value)
            body = result_data.get("result")
            content = _text(body)
            error = _result_error(result_data)
            absent = error and _not_found(result_data)
            # The last observation is a remembered observation, not a promise
            # that a mutable file stays unchanged until the next read.
            has_text = _has_text(body)
            exists = False if absent else True if not error and has_text and result_data.get("status") in {"ok", "unset"} else None
            add("session_file_exists", (file,), exists, result_event, basis=(result.fid,), span=result.span,
                status="unknown" if exists is None else "observed", derivation="read_result_at_observation_time")
            result_value = "not_found" if absent else "error" if error else "returned_content" if has_text else "unknown"
            add("session_read_result", (call.args[0], file), result_value, result_event,
                basis=(result.fid,), span=result.span,
                status="unknown" if result_value == "unknown" else "observed", derivation="read_result_at_observation_time")
            if not error and has_text:
                add("session_file_content", (file,), content, result_event, basis=(result.fid,), span=result.span,
                    derivation="tool_returned_text_not_independently_verified_filesystem")
            else:
                # Failed later reads must not silently carry forward old content
                # as a fresh observation of the file's current content.
                add("session_file_content", (file,), None, result_event, basis=(result.fid,), span=result.span,
                    status="unknown", derivation="current_content_unavailable")
    return record


def _label_entity(record, fact):
    files = [arg for arg in fact.args if arg.kind == "file"]
    if files:
        paths = record.chain("session_file_path", (files[0],))
        return ntpath.basename(str(paths[-1].value)) if paths else str(files[0])
    entity = fact.args[0] if fact.args else None
    if entity and entity.kind == "agent":
        return "Agent"
    if entity and entity.kind == "req":
        return "用户任务"
    if entity and entity.kind == "message":
        return "消息 " + str(fact.t)
    if entity and entity.kind == "call":
        call = next((f for f in record.by_entity(entity) if f.rel == "tool_call"), None)
        return (str(_json(call.value).get("tool", "工具")) + " · 调用 " + str(call.t)) if call else str(entity)
    return str(entity or "记录")


def build_artifact(bundle, analysis=None):
    """Present original Fact chains, version lifetimes and typed evidence edges."""
    source, record = bundle.source, bundle.record
    session = source.get("session", {})
    if session.get("schema") not in {"openclaw-session/v1", "openclaw-session/v2", "openclaw-signoz-session/v1"}:
        return {"schema": SCHEMA, "available": False, "events": [], "columns": [], "rows": [], "pointers": [],
                "checks": [], "coverage": {}, "missing_modules": [],
                "limitations": ["此轨迹尚未接入原生完整会话；旧工具输入不代表完整 Agent 执行过程。"]}
    from .session_checks import session_checks
    events = deepcopy(session.get("events", []))
    meta = getattr(record, "session_fact_meta", {})
    event_map = {e["event_id"]: e for e in events}
    groups = defaultdict(list)
    for event in events:
        event["fact_ids"] = []
        event["semantic_status"] = "preserved_only"
        event["semantic_note"] = "原文已保留；其全部语义尚未建模。"
        event["unsupported_blocks"] = [deepcopy(block) for block in session.get("coverage", {}).get("uninterpreted_content_blocks", [])
                                       if block.get("event_id") == event["event_id"]]
    for fact in record.F:
        if fact.rel not in REL_LABELS:
            continue
        groups[(fact.rel, tuple(map(str, fact.args)), fact.side)].append(fact)
        for ident in meta.get(fact.fid, {}).get("source_events", []):
            if ident in event_map:
                event_map[ident]["fact_ids"].append(fact.fid)
                if fact.rel not in {"session_message_text", "session_public_statement", "session_final_answer", "session_task_text"}:
                    event_map[ident]["semantic_status"] = "semantic"
                    event_map[ident]["semantic_note"] = "已提取部分明确属性；不表示该事件全部语义已理解。"
    rows, pointers = [], []
    visible_fids = {f.fid for facts in groups.values() for f in facts}
    for (rel, args, side), facts in groups.items():
        facts.sort(key=lambda f: (f.t, f.fid))
        label = _label_entity(record, facts[0])
        versions = []
        for index, fact in enumerate(facts):
            detail = meta.get(fact.fid, {})
            versions.append({"fid": fact.fid, "step": fact.t, "value": deepcopy(fact.value), "value_type": fact.value_type,
                "span": fact.span, "source_events": detail.get("source_events", []), "basis_fids": list(fact.basis),
                "status": detail.get("status", "unknown"), "source": deepcopy(detail.get("source", {})),
                "derivation": detail.get("derivation"),
                "in_force_until": facts[index + 1].t - 1 if index + 1 < len(facts) else None,
                "version_note": "沿用表示最近一次记录；不是对其后实际世界状态的持续观测。"})
            for event_id in detail.get("source_events", []):
                pointers.append({"id": f"source-{fact.fid}-{event_id}", "type": "source", "from": {"fid": fact.fid},
                    "to": {"event_id": event_id}, "label": "出处", "certainty": "recorded",
                    "note": "事实对应的原始会话事件；位置和摘要可在原文中核对。"})
            if not detail.get("source_events") and detail.get("source"):
                pointers.append({"id": f"source-{fact.fid}-input", "type": "source", "from": {"fid": fact.fid},
                    "to": {"source": deepcopy(detail["source"])}, "label": "出处：显式输入声明", "certainty": "recorded",
                    "note": "来自输入任务契约，不声称它是从自然语言自动抽取或穷尽了任务要求。"})
            for basis in fact.basis:
                if basis not in visible_fids:
                    continue
                pointers.append({"id": f"basis-{fact.fid}-{basis}", "type": "basis", "from": {"fid": fact.fid},
                    "to": {"fid": basis}, "label": "依据：记录关联／明确提取", "certainty": "derived",
                    "note": "仅表示同一调用返回或从明确原文提取；不证明 Agent 的内部决策因果。"})
        rows.append({"id": "state-" + str(facts[0].fid), "label": label + " · " + REL_LABELS[rel],
                     "entity_id": args[0] if args else None, "entity_label": label, "relation": rel,
                     "side": side, "versions": versions})
    checks = session_checks(bundle)
    for check in checks:
        for fid in check.get("primary_fids", []):
            if fid in visible_fids:
                pointers.append({"id": f"check-{check['id']}-{fid}", "type": "check",
                    "from": {"check_id": check["id"]}, "to": {"fid": fid}, "label": "检查定位",
                    "certainty": "unknown" if check["state"] == "unknown" else "derived",
                    "note": check["explanation"]})
        left, right = check.get("comparison_fids", [[], []])
        for a in left:
            for b in right:
                if a in visible_fids and b in visible_fids:
                    pointers.append({"id": f"compare-{check['id']}-{a}-{b}", "type": "correspondence",
                        "from": {"fid": a}, "to": {"fid": b}, "label": "对应比较",
                        "certainty": "unknown" if check["state"] == "unknown" else "derived",
                        "note": check["explanation"], "check_id": check["id"]})
    semantic_count = sum(e["semantic_status"] == "semantic" for e in events)
    unsupported = sum(bool(e.get("unsupported_blocks")) for e in events)
    return {"schema": SCHEMA, "available": True, "session_id": session.get("session_id"), "events": events,
        "columns": [{"step": e["step"], "event_id": e["event_id"], "original_seq": e.get("original_seq"),
                     "label": "事件 " + str(e["step"]), "type": e.get("type"), "role": e.get("role")} for e in events],
        "rows": rows, "pointers": pointers, "checks": checks,
        "coverage": {"events_total": len(events), "events_preserved": len(events), "events_semantic": semantic_count,
            "events_preserved_only": len(events) - semantic_count, "events_unsupported": unsupported,
            "source_coverage": deepcopy(session.get("coverage", {})),
            "notes": ["已保留计原生事件内容进入统一输入；已语义化只计提取了明确属性的事件。",
                      "两项口径不同：普通消息保留原文不等于已完整理解其含义。",
                      "事件编号是分析导入编号；original_seq 是原生会话序号，同一原生消息可拆成多个事件。"]},
        "missing_modules": [{"module": name, "label": label, "reason": "未建立有独立出处的该内部模块语义状态；未支持的原生内容仍保留，不将普通话语推断为该模块。"}
                            for name, label in (("memory", "内部记忆"), ("reflection", "内部反思"), ("internal_plan", "内部计划"))],
        "limitations": ["本适配覆盖已记录会话；不读取或补造未记录的内部思维。",
                        "公开说明和最终答复作为 Agent 说法保存，不能当成环境事实。",
                        "确定性语义目前限于显式任务契约、读取目标、读取结果和窄范围最终回答对照。",
                        "版本链说明同一属性的记录变化；相邻事件或版本本身不是因果证明。",
                        "未知意图占位不作为已解析认知展示；通用任务语义和唯一根因仍需额外证据。"]}
