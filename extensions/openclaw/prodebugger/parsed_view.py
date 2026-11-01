"""Read-only view of the existing compiler's canonical record.

The returned record is exactly Bundle.snapshot(). Safety findings are separate
links supplied by a saved report; they never become parser facts or entities.
This view intentionally includes local source text and is opt-in, unlike the
workbench's ordinary summary endpoints.
"""
from __future__ import annotations

from .record_bridge import build_bundle, digest

SCHEMA = "prodebugger-parsed-view/v1"


def parsed_source(source: dict, report: dict, case: dict, *, bundle=None) -> dict:
    bundle = bundle or build_bundle(source, online=False)
    record = bundle.snapshot()
    source_sha256 = digest(source)
    fact_ids = {fact["fid"] for fact in record["facts"]}
    links = []
    warnings = []
    if report.get("source", {}).get("sha256") != source_sha256:
        warnings.append("保存的安全报告与当前输入摘要不一致，未关联其 flags。")
    else:
        for flag in report.get("flags", []):
            evidence = [*flag.get("evidence", []), *flag.get("effect_evidence", [])]
            fids = sorted({item["fid"] for item in evidence
                           if type(item.get("fid")) is int and item["fid"] in fact_ids})
            links.append({"flag_id": flag["id"], "title": flag["title"], "rule_id": flag["rule_id"],
                          "fids": fids, "step": flag["step"], "source": "saved_safety_report",
                          "part_of_original_parse": False})
    return {
        "schema": SCHEMA,
        "case_id": case["id"], "title": case["title"], "origin": case["origin"],
        "origin_label": case["origin_label"], "source_sha256": source_sha256,
        "snapshot_sha256": digest(record), "hash_format": "record_bridge.digest canonical JSON",
        "compiler": {"entry": "build_bundle", "online": False,
                     "ir_version": record["ir_version"], "versions": bundle.versions},
        "counts": {"entities": len(record["entities"]), "relations": len(record["relations"]),
                   "facts": len(record["facts"]), "goals": len(record["goals"]),
                   "steps": len(source["steps"])},
        "record": record, "safety_links": links, "warnings": warnings,
        "notes": ["record 原样来自原有编译入口 build_bundle(..., online=False).snapshot()。",
                  "安全关联来自后加的已保存审计报告，不属于原解析器生成的事实。",
                  "本接口包含原文和实体值；未重新采集轨迹、调用模型或写入输入及报告。"],
    }
