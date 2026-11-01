"""Compile one trajectory: python prodebugger/run_compile.py TRAJECTORY_ID."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import os
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

import pyarrow.parquet as pq

if __package__:
    from .benchmark_parser import parse_benchmark_instance, parse_steps
    from .abstract_rules import detect_abstract_rules, export_rules
else:
    from benchmark_parser import parse_benchmark_instance, parse_steps
    from abstract_rules import detect_abstract_rules, export_rules

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "core" / "data"


def compile_trajectory(trajectory_id: str, data_dir: Path = DATA, *,
                       output_dir: Path | None = None) -> Path:
    """Compile Facts and seven abstract rules; save the record and evaluations."""
    if (not trajectory_id or re.search(r'[<>:"/\\|?*\x00-\x1f]', trajectory_id)
            or trajectory_id.endswith((".", " "))
            or trajectory_id.split(".")[0].upper() in {
                "CON", "PRN", "AUX", "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }):
        raise ValueError(f"Invalid trajectory directory name: {trajectory_id!r}")
    rows = []
    for split in ("train", "validation", "test"):
        path = Path(data_dir) / f"{split}.parquet"
        if path.is_file():
            rows.extend(pq.read_table(path, filters=[("trajectory_id", "=", trajectory_id)],
                                      columns=["trajectory_id", "task_type", "full_trajectory"]
                                      ).to_pylist())
    if len(rows) != 1:
        raise ValueError(f"Expected one trajectory for {trajectory_id!r}, found {len(rows)} in {data_dir}")
    row = rows[0]
    sys.path.insert(0, str(HERE.parent))
    from prodebugger.record_bridge import parquet_source, build_bundle
    output = (Path(output_dir) if output_dir is not None else HERE / "sessions") / trajectory_id
    output.mkdir(parents=True, exist_ok=True)
    bundle = build_bundle(parquet_source(trajectory_id, data_dir), output=output)
    record, evaluations = bundle.projection, bundle.evaluations
    payload = {
        "trajectory_id": record.tid,
        "task_type": row["task_type"],
        "task": record.task,
        "parse_audits": bundle.record.parse_audits,
        "parsing_mode": "offline-strict-audit; online backoff is available through run_experiment",
        "kinds": [asdict(kind) for kind in record.kinds.declared().values()],
        "relations": [asdict(relation) for relation in record.relations.declared().values()],
        "entities": [asdict(entity) for entity in record.E.values()],
        "goals": [asdict(goal) for goal in record.goals],
        "facts": [asdict(fact) for fact in record.F],
        "evidences": {
            kind: {**asdict(group), "refs": group.refs}
            for kind, group in record.evidences.items()
        },
    }
    payload.update(rules=export_rules(), evaluations=evaluations)
    outputs = {"record": payload, **payload, "steps": bundle.source["steps"]}
    for name, value in outputs.items():
        text = json.dumps(value, ensure_ascii=False, indent=2, default=sorted, allow_nan=False)
        (output / f"{name}.json").write_text(text + "\n", encoding="utf-8")
    return output


def compile_sample(sample_file: Path, data_dir: Path, output_dir: Path) -> dict:
    """Compile the fixed twenty IDs through the same single-trajectory entry point."""
    if __package__:
        from .abstract_rules import RULES, export_rules
    else:
        from abstract_rules import RULES, export_rules
    sample = json.loads(Path(sample_file).read_text(encoding="utf-8"))
    ids = sample["trajectory_ids"]
    if len(ids) != 20 or len(set(ids)) != 20 or sample["per_domain"] != 10:
        raise ValueError("expected the fixed 20 unique IDs, 10 per domain")
    sources = [HERE / name for name in ("dsl.py", "benchmark_parser.py",
                                         "abstract_rules.py", "run_compile.py")]
    sources += [p for p in Path(data_dir).glob("*.parquet") if p.stem in ("train", "validation", "test")]
    sources += [HERE.parent / "core" / name for name in (
        "profiles/webshop.py", "profiles/alfworld.py", "data_io.py", "compile_run.py")]
    manifest = {"trajectory_ids": ids, "per_domain": 10, "seed": sample.get("seed"),
                "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
                "source_sha256": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sources}}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    def save(name, value):
        (output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save("sample.json", manifest)
    save("rules.json", export_rules())
    results, domains = [], Counter()
    counts = {r.id: {s: 0 for s in ("satisfied", "violated", "unknown", "not_applicable")} for r in RULES}
    reasons = Counter()
    for tid in ids:
        result = {"trajectory_id": tid, "status": "error"}
        try:
            path = compile_trajectory(tid, data_dir, output_dir=output_dir)
            record = json.loads((path / "record.json").read_text(encoding="utf-8"))
            domains[record["task_type"]] += 1
            candidates = sum(len(g["items"]) for g in record["evidences"].values())
            result.update(status="compiled", task_type=record["task_type"],
                          record=f"{tid}/record.json", candidates=candidates,
                          evaluations=len(record["evaluations"]))
            for evaluation in record["evaluations"]:
                counts[evaluation["rule"]][evaluation["state"]] += 1
                if evaluation["state"] == "unknown":
                    reasons[evaluation["details"]["reason"]] += 1
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        results.append(result)
    summary = {"total": len(results), "compiled": sum(r["status"] == "compiled" for r in results),
               "by_domain": dict(domains), "candidates": sum(r.get("candidates", 0) for r in results),
               "by_rule": counts, "unknown_reasons": dict(reasons)}
    save("results.json", {"summary": summary, "records": results})
    lines = ["# 七类抽象规则：固定20例 run_compile", "",
             "R6 已删除。仅使用已有 Fact，无 intention 或新增语义解析。",
             "默认只输出七类抽象规则候选；unknown 不作为错误检出。", "",
             f"编译：{summary['compiled']}/20；来源：{dict(domains)}；候选：{summary['candidates']}。", "",
             "| 规则 | satisfied | violated | unknown | not_applicable |",
             "|---|---:|---:|---:|---:|"]
    for rule, states in counts.items():
        lines.append(f"| {rule} | " + " | ".join(str(v) for v in states.values()) + " |")
    lines += ["", "R1 可变状态缺断言时间；R2 匹配记录不证明信息实际使用；R3 缺当前断言；",
              "R4 仅检查已有解析器契约，不代表全部任务约束；R5 无效果不是责任判定；",
              "R7 不推断完成承诺；R8 无重试例外信息时 unknown，不判断计划最优性。", "",
              "| 轨迹 | 来源 | 编译 | 候选 |", "|---|---|---|---:|"]
    for r in results:
        lines.append(f"| {r['trajectory_id']} | {r.get('task_type', '')} | {r['status']} | {r.get('candidates', r.get('error', ''))} |")
    lines += ["", "小批量结果不表示总体 precision、recall 或根因准确率。", ""]
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trajectory_id", nargs="?", help="Exact dataset trajectory ID")
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--abstract-rules", action="store_true",
                        help="Compatibility alias; seven abstract rules always run")
    parser.add_argument("--sample-file", type=Path, help="Compile the fixed twenty-ID manifest")
    parser.add_argument("--output-dir", type=Path, help="Parent directory for trajectory outputs")
    args = parser.parse_args(argv)
    if args.sample_file:
        if args.trajectory_id:
            parser.error("--sample-file cannot be combined with a positional ID")
        try:
            summary = compile_sample(args.sample_file, args.data_dir,
                                     args.output_dir or HERE / "sessions/abstract_rules_n20")
        except (ValueError, OSError, KeyError) as exc:
            parser.error(str(exc))
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["compiled"] == 20 and summary["by_domain"] == {"alfworld": 10, "webshop": 10} else 1
    trajectory_id = args.trajectory_id or input("Trajectory ID: ").strip()
    try:
        output = compile_trajectory(trajectory_id, args.data_dir,
                                    output_dir=args.output_dir)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
