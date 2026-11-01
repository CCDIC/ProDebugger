"""Canonical core record and a read-only, identity-preserving DSL projection."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import compile_run as compiler
from profiles import alfworld, webshop, openclaw

from . import dsl as D
from .abstract_rules import detect_abstract_rules, export_rules

PROFILES = {"webshop": webshop, "alfworld": alfworld, "openclaw": openclaw}


def plain(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((plain(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(plain(value), sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2,
                                     allow_nan=False) + "\n", encoding="utf-8")
    for attempt in range(5):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))


def append_json(path: Path, value: Any) -> None:
    path = Path(path)
    if path.is_file() and path.stat().st_size:
        with path.open("r+b") as stream:
            stream.seek(-1, 2)
            if stream.read(1) != b"\n":
                stream.seek(0)
                content = stream.read()
                boundary = content.rfind(b"\n") + 1
                try:
                    json.loads(content[boundary:].decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    stream.truncate(boundary)
                else:
                    stream.seek(0, 2)
                    stream.write(b"\n")
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(plain(value), ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()


def valid_id(value: str) -> str:
    if (not value or value in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value.endswith((".", " ")) or value.split(".")[0].upper() in
            {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}):
        raise ValueError("invalid trajectory directory name")
    return value


def source_versions() -> dict[str, str]:
    paths = list(CORE.glob("*.py")) + list((CORE / "profiles").glob("*.py"))
    paths += list(Path(__file__).parent.glob("*.py"))
    paths += list((Path(__file__).parent / "prompts").glob("*.md"))
    return {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def saved_source(path: Path, tid: str | None = None) -> dict:
    path = Path(path)
    if tid and not (path / "steps.json").is_file():
        path = path / valid_id(tid)
    record = read_json(path / "record.json")
    actual = valid_id(record["trajectory_id"])
    if tid and actual != tid:
        raise ValueError("compiled input trajectory_id does not match requested ID")
    # Whitelist source material. Historical facts/evidence/labels are never reused.
    steps = read_json(path / "steps.json")
    return {"trajectory_id": actual, "task_type": record["task_type"], "task": record["task"],
            **{k: record[k] for k in ("telemetry", "task_contract", "session") if k in record and record["task_type"] == "openclaw"},
            "steps": [{key: step[key] for key in
                       ("t", "env_text", "agent_text", "admissible", "parts")} for step in steps]}


def parquet_source(tid: str, data_dir: Path) -> dict:
    try:
        import pyarrow.parquet as pq
        from data_io import unpack
    except ImportError as exc:
        raise RuntimeError("Parquet input needs pyarrow; use --compiled-dir for saved steps") from exc
    rows = []
    for split in ("train", "validation", "test"):
        path = Path(data_dir) / f"{split}.parquet"
        if path.is_file():
            rows.extend(pq.read_table(path, filters=[("trajectory_id", "=", tid)],
                                      columns=["trajectory_id", "task_type", "full_trajectory"]).to_pylist())
    if len(rows) != 1:
        raise ValueError(f"expected one trajectory, found {len(rows)}")
    _, task, env, agent, admissible = unpack(rows[0])
    return {"trajectory_id": tid, "task_type": rows[0]["task_type"], "task": task,
            "steps": plain(compiler.segment(env, agent, admissible))}


def project(record) -> D.Record:
    kinds, relations = D.Kinds(), D.Relations()
    for name, parent in record.kinds.declared().items():
        kinds.declare(name, parent)
    for relation in record.relations.declared().values():
        relations.declare(**asdict(relation))
    goals = []
    for goal in record.goals:
        fields = asdict(goal)
        fields["target"] = D.Entity(**fields["target"]) if fields["target"] else None
        fields["constraints"] = tuple(D.Constraint(**{**c, "property": D.Entity(**c["property"])})
                                      for c in fields["constraints"])
        goals.append(D.Goal(**fields))
    result = D.Record(record.tid, kinds, relations, record.task, goals)
    result.claim_scopes = plain(getattr(record, 'claim_scopes', {}))
    for entity in record.E.values():
        result.ent(entity.kind, entity.index)
    for fact in record.F:
        args = tuple(result.ent(e.kind, e.index) for e in fact.args)
        purpose = result.ent(fact.purpose.kind, fact.purpose.index) if fact.purpose else None
        fid = result.add(fact.rel, args, fact.value, fact.slot, fact.side, fact.t,
                         situation=fact.situation, basis=fact.basis, purpose=purpose,
                         span=fact.span, value_type=fact.value_type)
        if fid != fact.fid or asdict(result.fact(fid)) != asdict(fact):
            raise ValueError("canonical/DSL Fact identity mismatch")
    return result


@dataclass
class Bundle:
    record: Any
    projection: D.Record
    evaluations: list[dict]
    source: dict
    fingerprint: str
    versions: dict[str, str]

    def snapshot(self) -> dict:
        record = self.record
        return plain({"trajectory_id": record.tid, "task_type": self.source["task_type"],
                      "task": record.task, "ir_version": record.ir_version,
                      "kinds": record.kinds.declared(), "relations": record.relations.declared(),
                      "entities": record.E, "goals": record.goals, "facts": self.projection.F,
                      "parse_audits": getattr(record, "parse_audits", []),
                      "source_steps": record.source_steps, "intentions": record.intentions,
                      "claim_scopes": getattr(record, 'claim_scopes', {}),
                      "agent_statements": getattr(record, 'agent_statements', []),
                      "openclaw_semantics": getattr(record, 'openclaw_semantics', None),
                      "semantic_fact_metadata": getattr(record, 'semantic_fact_metadata', {}),
                      "process_notes": record.process_notes, "commit_values": record.commit_values})


def build_bundle(source: dict, *, audited=None, semantics=None, output=None, config=None, online=False) -> Bundle:
    if source.get("session"):
        from .session_import import validate_session_source
        validate_session_source(source)
    source = {k: source[k] for k in ("trajectory_id", "task_type", "task", "steps", "telemetry", "task_contract", "session") if k in source}
    valid_id(source["trajectory_id"])
    if source["task_type"] not in PROFILES:
        raise ValueError("task_type must be webshop, alfworld or openclaw")
    if not isinstance(source["steps"], list) or any(not isinstance(item, dict)
            or not isinstance(item.get("parts"), dict) for item in source["steps"]):
        raise ValueError("steps must contain objects with a module parts object")
    steps = [compiler.Step(**{**{key: item[key] for key in
                             ("t", "env_text", "agent_text", "admissible")},
                             "parts": {tag: item["parts"].get(tag) for tag in compiler.TAGS}})
             for item in source["steps"]]
    if not steps or not isinstance(source["task"], str):
        raise ValueError("input requires a task string and at least one complete step")
    for step in steps:
        if (type(step.t) is not int or not isinstance(step.env_text, str) or not isinstance(step.agent_text, str)
                or not isinstance(step.admissible, list) or any(not isinstance(a, str) for a in step.admissible)
                or any(v is not None and not isinstance(v, str) for v in step.parts.values())):
            raise ValueError("invalid step source fields")
    if [s.t for s in steps] != list(range(1, len(steps) + 1)):
        raise ValueError("steps must be consecutive and one-based")
    source = {**source, "steps": plain(steps)}
    from .parse_audit import prepare, compilation_input
    profile = PROFILES[source["task_type"]]
    audited = audited if audited is not None else prepare(source, output, config, online=online)
    parsed = compilation_input(audited, profile, source["task"])
    record = compiler.build(source["trajectory_id"], source["task"], steps, profile, parsed=parsed)
    from .claim_parser import source_cards
    record.agent_statements = source_cards(source)
    if source.get("session") and source["task_type"] == "openclaw":
        from .session_artifact import augment_record
        augment_record(record, source)
    if source["task_type"] == "openclaw":
        from .openclaw_semantics import prepare as prepare_semantics, apply as apply_semantics
        semantics = semantics if semantics is not None else prepare_semantics(source, output, config, online=online)
        apply_semantics(record, source, semantics)
    projection = project(record)
    evaluations = detect_abstract_rules(projection)
    incomplete = [a for a in parsed["audits"] if a["status"] == "unresolved"]
    for evaluation in evaluations:
        relevant = [a["source_id"] for a in incomplete if a["step"] <= (evaluation.get("t") or 0)]
        if relevant and evaluation["state"] == "violated":
            evaluation["state"] = "unknown"
            evaluation["details"] = {**evaluation["details"], "reason": "parse_incomplete", "parse_gaps": relevant}
    for group in projection.evidences.values():
        group.items[:] = [e for e in group.items if not any(a["step"] <= e.t for a in incomplete)]
    versions = source_versions()
    from .direct_submit import policy_for
    return Bundle(record, projection, evaluations, source,
                  digest({"source": source, "versions": versions, "audited_parse": audited,
                          "openclaw_semantics": semantics if source["task_type"] == "openclaw" else None,
                          "trace_protocol": "evidence-query-step/v2-elements", "policy": policy_for(source),
                          "canonical_facts": projection.F, "goals": projection.goals}), versions)


def export_bundle(bundle: Bundle, output: Path) -> None:
    write_json(output / "input.json", bundle.source)
    write_json(output / "canonical_record.json", bundle.snapshot())
    write_json(output / "evidences.json", bundle.projection.evidences)
    write_json(output / "evaluations.json", bundle.evaluations)
    write_json(output / "rules.json", export_rules())
    if bundle.source.get("session"):
        from .session_artifact import build_artifact
        write_json(output / "session_artifact.json", build_artifact(bundle))
