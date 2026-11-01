"""Read-only entity graphs, reproducible pool sampling, and explicit live stubs."""
from __future__ import annotations

from collections import Counter
import json
import random
import secrets

from .parsed_view import parsed_source
from .record_bridge import build_bundle
from .trace_analysis import analyze_source, annotate_nodes, verified_evidence

GRAPH_SCHEMA = "prodebugger-trace-graph/v1"
ORIGINS = {"controlled_tool_runtime", "historical_openclaw", "user_import"}
STATUSES = {"safe", "unsafe", "unknown"}


def _entity_id(value):
    return value["kind"] + ":" + value["index"]


def _verified_evidence(item, facts, source, queries):
    """Link only a returned, complete query row that still matches original text."""
    return verified_evidence(item, facts, source, queries)


def graph_source(source, report, case, *, analysis=None, bundle=None, diagnosis=None):
    bundle = bundle or build_bundle(source, online=False)
    parsed = parsed_source(source, report, case, bundle=bundle)
    record = parsed["record"]
    case_id = case["id"]
    facts = {fact["fid"]: fact for fact in record["facts"]}
    nodes = {entity_id: {"id": case_id + "::" + entity_id, "entity_id": entity_id,
                        "kind": entity["kind"], "index": entity["index"], "status": "neutral",
                        "reason": "结构实体；未对该实体作行为安全判断。", "flag_ids": [], "fids": [],
                        "action_steps": [], "role": "behavior" if entity["kind"] == "call" else "structure"}
             for entity_id, entity in record["entities"].items()}
    edges, warnings = [], []
    for fact in record["facts"]:
        refs = [_entity_id(arg) for arg in fact["args"]]
        for entity_id in refs:
            if entity_id in nodes:
                nodes[entity_id]["fids"].append(fact["fid"])
                if fact["rel"] == "tool_call":
                    nodes[entity_id]["action_steps"].append(fact["t"])
        for index, target in enumerate(refs[1:], 1):
            if refs[0] in nodes and target in nodes:
                edges.append({"id": f"{case_id}::fact:{fact['fid']}:0:{index}",
                              "source": nodes[refs[0]]["id"], "target": nodes[target]["id"],
                              "relation": fact["rel"], "fid": fact["fid"], "step": fact["t"],
                              "source_arg": 0, "target_arg": index})
    source_matches = report.get("source", {}).get("sha256") == parsed["source_sha256"]
    queries = {query["id"]: query for query in report.get("queries", []) if isinstance(query, dict) and "id" in query}
    invalid_flags = False
    if source_matches:
        for flag in report.get("flags", []):
            primary = flag.get("evidence", [])
            supported = bool(primary) and all(_verified_evidence(item, facts, source, queries) for item in primary)
            supported = supported and any(facts[item["fid"]]["rel"] == "tool_call"
                                          and facts[item["fid"]]["t"] == flag.get("step") for item in primary)
            if not supported:
                invalid_flags = True
                warnings.append({"id": "unlinked-" + flag["id"], "type": "evidence_unavailable", "severity": "warning",
                                 "title": "违规关联证据不完整", "message": "保存的 flag 无法通过当前事实和查询片段核对，未将节点标红。",
                                 "node_ids": [], "fids": [], "flag_id": flag["id"]})
                continue
            extras = [item for item in flag.get("effect_evidence", []) if _verified_evidence(item, facts, source, queries)]
            fids = sorted({item["fid"] for item in [*primary, *extras]})
            linked = sorted({_entity_id(arg) for fid in fids for arg in facts[fid]["args"]
                             if _entity_id(arg) in nodes and arg["kind"] == "call"})
            for entity_id in linked:
                if nodes[entity_id]["role"] == "behavior":
                    nodes[entity_id]["flag_ids"].append(flag["id"])
            warnings.append({"id": "warning-" + flag["id"], "type": "policy_violation", "severity": flag.get("severity", "high"),
                             "title": flag["title"], "message": flag.get("recommendation", "已记录违反本策略的调用尝试。"),
                             "node_ids": [nodes[key]["id"] for key in linked], "fids": fids, "flag_id": flag["id"]})
    else:
        warnings.append({"id": "source-mismatch", "type": "source_mismatch", "severity": "warning",
                         "title": "安全报告与当前轨迹不匹配", "message": "原记录可查看；旧报告的安全状态及 flags 均未应用到当前图。",
                         "node_ids": [node["id"] for node in nodes.values() if node["role"] == "behavior"], "fids": []})
    capture = source.get("telemetry", {}).get("coverage", {})
    reported_coverage = report.get("coverage", {}) if source_matches else {}
    complete = (capture.get("declared_complete") is True and capture.get("content_complete") is True
                and reported_coverage.get("declared_complete") is True and reported_coverage.get("content_complete") is True
                and not capture.get("gaps"))
    steps = {}
    if source_matches:
        for row in report.get("steps", []):
            steps.setdefault(row.get("step"), []).append(row)
    for node in nodes.values():
        node["fids"] = sorted(set(node["fids"]))
        node["action_steps"] = sorted(set(node["action_steps"]))
        node["flag_ids"] = sorted(set(node["flag_ids"]))
        if node["role"] == "structure":
            continue
        local_states = []
        for t in node["action_steps"]:
            rows = steps.get(t, [])
            call = next((fact for fact in record["facts"] if fact["rel"] == "tool_call" and fact["t"] == t), None)
            try:
                action = json.loads(call["value"]) if call else {}
            except (ValueError, TypeError):
                action = {}
            matched = len(rows) == 1 and all(rows[0].get(key) == action.get(key) for key in ("trace_id", "span_id", "tool"))
            if not source_matches or not complete or not matched or rows[0].get("gaps"):
                local_states.append("unknown")
            else:
                state = rows[0].get("status")
                # An unverified red report row alone is insufficient to make
                # a graph node unsafe; only independently checked flag links do.
                local_states.append("safe" if state == "safe" else "unknown" if state != "unsafe" else "unsafe")
        summary = Counter(local_states)
        node["assessment_counts"] = {key: summary[key] for key in ("safe", "unsafe", "unknown")}
        if node["flag_ids"]:
            node["status"] = "unsafe"
            mixed = "；同一实体还关联其他通过或未知调用" if summary["safe"] or summary["unknown"] else ""
            node["reason"] = f"已核对 {len(node['flag_ids'])} 个违规 flag 的实际事实引用{mixed}。"
        elif not local_states or any(state != "safe" for state in local_states):
            node["status"] = "unknown"
            node["reason"] = "相关行为存在未知结果、采集缺口或无法核对的报告；未判定安全。"
        else:
            node["status"] = "safe"
            node["reason"] = f"该实体关联的 {len(local_states)} 次可评估行为均通过当前策略；不代表系统整体安全。"
    coverage_gaps = reported_coverage.get("gaps", []) if source_matches else []
    if not complete or coverage_gaps:
        affected = [node for node in nodes.values() if node["role"] == "behavior" and node["status"] == "unknown"]
        warnings.append({"id": "coverage-gaps", "type": "coverage_gap", "severity": "warning",
                         "title": "轨迹或判断覆盖不完整", "message": "；".join(str(gap) for gap in coverage_gaps) or "缺少完整采集声明或内容；没有 flag 的节点不能据此判为安全。",
                         "node_ids": [node["id"] for node in affected],
                         "fids": sorted({fid for node in affected for fid in node["fids"]})})
    confirmed_violation = any(warning["type"] == "policy_violation" for warning in warnings)
    status = "unsafe" if confirmed_violation else report.get("status", "unknown") if source_matches and not invalid_flags else "unknown"
    analysis = analysis or analyze_source(source, report, case, bundle=bundle, diagnosis=diagnosis)
    annotate_nodes(list(nodes.values()), analysis)
    from .session_artifact import build_artifact
    session_artifact = build_artifact(bundle, analysis) if source.get("session") else None
    return {"schema": GRAPH_SCHEMA, "case_id": case_id, "title": case["title"], "origin": case["origin"],
            "origin_label": case["origin_label"], "status": status, "policy": case.get("policy", report.get("policy")),
            "report": report, "record": record, "source_sha256": parsed["source_sha256"],
            "snapshot_sha256": parsed["snapshot_sha256"], "compiler": parsed["compiler"],
            "counts": parsed["counts"], "nodes": list(nodes.values()), "edges": edges, "warnings": warnings,
            "analysis": analysis, "session_artifact": session_artifact,
            "report_matches_source": source_matches,
            "notes": ["节点只来自原 record.entities；边只来自同一事实的 args[0] 到其余参数。",
                      "单参数事实附在节点上；tool/agent/trace/目标等结构节点不作行为着色。",
                      "行为标记优先精确关联 call；红色错误、橙色安全风险、运行异常和未知保留独立来源。"]}


def sample_cases(cases, request):
    allowed = {"count", "seed", "origins", "statuses", "query", "session_only"}
    if not isinstance(request, dict) or "count" not in request or set(request) - allowed:
        raise ValueError("sample requires count and only optional seed/origins/statuses/query")
    count = request["count"]
    if type(count) is not int or not 1 <= count <= 24:
        raise ValueError("count must be an integer from 1 to 24")
    seed = request.get("seed", secrets.token_hex(12))
    if not isinstance(seed, str) or not 1 <= len(seed) <= 64:
        raise ValueError("seed must contain 1 to 64 characters")
    filters = {}
    for key, values in (("origins", ORIGINS), ("statuses", STATUSES)):
        selected = request.get(key, sorted(values))
        if not isinstance(selected, list) or any(not isinstance(value, str) or value not in values for value in selected):
            raise ValueError(key + " must be an array of supported enum values")
        filters[key] = sorted(set(selected))
    query = request.get("query", "")
    if not isinstance(query, str) or len(query) > 180:
        raise ValueError("query must be a string no longer than 180 characters")
    filters["query"] = query.strip()
    filters["query_fields"] = ["case_id", "title"]
    session_only = request.get("session_only", False)
    if type(session_only) is not bool:
        raise ValueError("session_only must be a boolean")
    if session_only:
        filters["session_only"] = True
    pool = sorted({case["id"] for case in cases if case["origin"] in filters["origins"]
                   and (not session_only or case.get("session_available") is True)
                   and case["status"] in filters["statuses"] and (not filters["query"] or
                       filters["query"].casefold() in (case["id"] + " " + case["title"]).casefold())})
    selected = random.Random(seed).sample(pool, min(count, len(pool)))
    return {"schema": "prodebugger-trace-sample/v1", "case_ids": selected, "seed": seed,
            "eligible_count": len(pool), "selected_count": len(selected), "requested_count": count,
            "pool_exhausted": count > len(pool), "method": "uniform_without_replacement", "filters": filters,
            "note": "只在当前可见案例池中无放回抽样，不采集新轨迹；这不是安全检测准确率统计。"}


def integrations():
    return {"schema": "prodebugger-integrations/v1",
            "connections": [{"id": ident, "label": label, "status": "not_connected", "implemented": False,
                             "transport": "OTLP/HTTP", "endpoint": "/api/safety/live/events"}
                            for ident, label in (("openclaw", "OpenClaw 实时接入"), ("otlp", "其他 agent OTLP 实时接入"))],
            "note": "预留接口尚未实现实时接收、缓冲、会话聚合和增量审计；请使用已保存轨迹导入。"}


def live_stub():
    return {"schema": "prodebugger-live-stub/v1", "error": "live_ingestion_not_implemented",
            "implemented": False, "accepted": False, "stored": False,
            "message": "实时轨迹流处理尚未实现。本次数据未接收为轨迹、未保存、未审计。"}
