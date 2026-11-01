"""Parse benchmark rows into the minimal DSL using the existing domain parsers."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from .dsl import Constraint, Entity, Goal, Kinds, MUTABLE, Record, Relations
    from .abstract_rules import detect_abstract_rules
else:
    from dsl import Constraint, Entity, Goal, Kinds, MUTABLE, Record, Relations
    from abstract_rules import detect_abstract_rules

# Existing core profiles use imports relative to the core directory.
CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.append(str(CORE))

from compile_run import Step, segment
from data_io import unpack
from profiles import alfworld, webshop

PROFILES = {"webshop": webshop, "alfworld": alfworld}


def parse_steps(instance: Mapping[str, Any]) -> tuple[str, list[Step]]:
    """Return the task and segmented steps, accepting encoded or decoded JSON."""
    row = dict(instance)
    if not isinstance(row["full_trajectory"], str):
        row["full_trajectory"] = json.dumps(row["full_trajectory"])
    _, task, env, agent, admissible = unpack(row)
    return task, segment(env, agent, admissible)


def parse_benchmark_instance(instance: Mapping[str, Any], *, with_evidence: bool = True) -> Record:
    """Compile existing Facts and, by default, derive abstract-rule candidates."""
    task_type = instance["task_type"]
    if task_type not in PROFILES:
        raise ValueError(f"Unsupported task_type {task_type!r}; expected webshop or alfworld")
    profile = PROFILES[task_type]
    task, steps = parse_steps(instance)

    kinds, relations = Kinds(), Relations()
    for name, parent in profile.kinds().declared().items():
        if name != "int":
            kinds.declare(name, parent)
    for relation in profile.relations().declared().values():
        if relation.name != "admissibility":
            relations.declare(**asdict(relation))
    # Keep action admissibility without introducing the original intention model.
    relations.declare("admissibility", 1, (MUTABLE,), arg_kinds=("agent",),
                      value_types=("string",))

    goals = []
    for goal in profile.parse_goal(task):
        fields = asdict(goal)
        fields["target"] = Entity(**fields["target"]) if fields["target"] else None
        fields["constraints"] = tuple(
            Constraint(**{**constraint, "property": Entity(**constraint["property"])})
            for constraint in fields["constraints"]
        )
        goals.append(Goal(**fields))

    record = Record(instance["trajectory_id"], kinds, relations, task, goals)
    agent = record.ent("agent", "self")
    for goal in goals:
        if goal.target is not None:
            record.ent(goal.target.kind, goal.target.index)
        for constraint in goal.constraints:
            record.ent(constraint.property.kind, constraint.property.index)

    situation, previous_action = None, None
    for step in steps:
        observation_ids, mind_ids = [], []

        def emit(item, slot, side, basis=(), purpose=None):
            args = tuple(record.ent(kind, index) for kind, index in item["args"])
            return record.add(
                item["rel"], args, item["value"], slot, side, step.t,
                situation=situation, basis=basis, purpose=purpose,
                span=item.get("span", ""), value_type=item.get("value_type"),
            )

        for item in profile.parse_observation(step.env_text, step.admissible):
            fid = emit(item, "obs", "env",
                       basis=(previous_action,) if previous_action is not None else (),
                       purpose=profile.bears_on(item, goals))
            observation_ids.append(fid)
            if item["rel"] == profile.situating_relation() and item["value"] is True:
                situation = fid

        for module, relation, slot in (
            ("memory", "recalled_summary", "mem"),
            ("reflection", "assessed", "refl"),
            ("plan", "planned", "plan"),
        ):
            text = step.parts.get(module)
            if text is not None:
                mind_ids.append(record.add(
                    relation, (agent,), text, slot, "mind", step.t,
                    situation=situation, basis=tuple(observation_ids), span=text,
                ))
            if module == "memory" and text is not None:
                known = {}
                for fact in record.F:
                    if fact.rel == "in" and fact.t < step.t and fact.args[0].kind == "value":
                        known.setdefault(fact.args[1].index, []).append(fact.args[0].index)
                for goal in goals:
                    for attribute, value in goal.attrs:
                        attribute = attribute.replace(" ", "_")
                        if attribute in known:
                            known[attribute].append(value)
                for item in profile.parse_memory(text, known):
                    args = tuple(Entity(kind, index) for kind, index in item["args"])
                    prior = record.chain(item["rel"], args, upto=step.t - 1)
                    mind_ids.append(emit(item, "mem", "mind",
                                         basis=tuple(f.fid for f in prior[-2:])))

        action = profile.parse_action(step.parts.get("action"), step.admissible)
        previous_action = None
        if action is not None:
            previous_action = emit(action, "act", "mind", basis=tuple(mind_ids))
            record.add("admissibility", (agent,), action["admissibility"], "chk", "env",
                       step.t, basis=(previous_action,), span=action["span"])
    if with_evidence:
        detect_abstract_rules(record)
    return record
