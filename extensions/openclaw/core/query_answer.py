"""Flat, query-specific explanations for programmable record views.

This module formats evidence; it does not parse trajectories, create facts, or
rank root causes.  The contract is deliberately shallow so an LLM can read the
answer, constraint progress, and exact evidence without traversing entity trees.
"""
from collections import defaultdict
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from process_notes import clip_text, fact_value, goal_state
from record import Entity, Fact, Goal, Record


def _walk_result(value: Any, entities: Set[str], fids: Set[int], steps: Set[int],
                 known: Set[str], key: Optional[str] = None) -> None:
    if isinstance(value, dict):
        if (key in ("evidence", "action_fids", "next_observation_fids", "decisions")
                and isinstance(value.get("items"), list)):
            fids.update(item for item in value["items"] if isinstance(item, int))
        for child_key, child in value.items():
            _walk_result(child, entities, fids, steps, known, child_key)
    elif isinstance(value, list):
        for child in value:
            _walk_result(child, entities, fids, steps, known, key)
    elif isinstance(value, str) and value in known:
        entities.add(value)
    elif isinstance(value, int):
        if (key in ("fid", "fids", "decision_fid", "primary_fid")
                or str(key).endswith("_fid") or str(key).endswith("_fids")):
            fids.add(value)
        elif key == "step":
            steps.add(value)


def _facts_for_entity(R: Record, entity: Entity) -> List[Fact]:
    ids = {fact.fid for fact in R.by_entity(entity)} | {
        fact.fid for fact in R.F
        if fact.purpose is not None and str(fact.purpose) == str(entity)
    }
    return sorted((R.fact(fid) for fid in ids), key=lambda fact: (fact.t, fact.fid))


def _expected_texts(goal: Optional[Goal]) -> List[str]:
    if goal is None:
        return []
    values = []
    for constraint in goal.constraints:
        expected = getattr(constraint, "expected", None)
        if expected not in (None, ""):
            values.append(" ".join(str(expected).lower().split()))
    return values


def _fact_meaning(fact: Fact, focus: Optional[Entity], expected: List[str]) -> str:
    text = " ".join(str(fact.span or fact.value or "").lower().split())
    if fact.t == 0 and fact.side == "task":
        return "task requirement definition"
    if fact.slot == "chk":
        if fact.rel == "admissibility":
            return f"action admissibility is {fact.value}"
        if isinstance(fact.value, bool):
            return ("recorded satisfying decision" if fact.value
                    else "recorded non-satisfying decision")
        return "recorded environment decision"
    if fact.slot == "act":
        return "agent executed this action"
    if expected and any(value and value in text for value in expected):
        return "directly mentions the required value"
    if fact.side == "mind":
        return f"agent {fact.slot} output relevant to the query"
    if fact.side == "env" and fact.slot == "obs":
        return "environment evidence relevant to the query"
    if focus is not None and focus in fact.args:
        return "relation directly involving the queried entity"
    return "linked record evidence"


def _fact_line(fact: Fact, focus: Optional[Entity], expected: List[str]) -> Dict[str, Any]:
    return {
        "fid": fact.fid,
        "step": fact.t,
        "source": f"{fact.side}/{fact.slot}",
        "relation": fact.rel,
        "arguments": [str(arg) for arg in fact.args],
        "value": fact_value(fact),
        "text": clip_text(fact.span, 220),
        "meaning": _fact_meaning(fact, focus, expected),
    }


def _edge_sample(facts: List[Fact], limit: int) -> List[Fact]:
    """Keep early and late evidence when a category repeats."""
    if len(facts) <= limit:
        return facts
    left = (limit + 1) // 2
    return facts[:left] + facts[-(limit - left):]


def _select_evidence(pool: List[Fact], focus: Optional[Entity],
                     expected: List[str], limit: int) -> List[Fact]:
    """Select a small but category-complete evidence table."""
    ordered = sorted(pool, key=lambda fact: (fact.t, fact.fid))

    def matches_expected(fact: Fact) -> bool:
        if fact.t == 0:
            return False
        text = " ".join(str(fact.span or fact.value or "").lower().split())
        return any(value and value in text for value in expected)

    buckets = [
        _edge_sample([f for f in ordered if f.t == 0 and f.side == "task"], 1),
        _edge_sample([f for f in ordered if matches_expected(f)], 4),
        _edge_sample([f for f in ordered if f.slot == "chk"], 2),
        _edge_sample([f for f in ordered if f.slot == "act"], 2),
        [f for f in ordered if focus is not None and focus in f.args][:3],
        _edge_sample([f for f in ordered if f.side == "mind" and f.slot != "act"], 2),
        _edge_sample([f for f in ordered if f.side == "env" and f.slot == "obs"], 2),
    ]
    selected: List[Fact] = []
    seen = set()
    for bucket in buckets:
        for fact in bucket:
            if fact.fid in seen:
                continue
            selected.append(fact)
            seen.add(fact.fid)
            if len(selected) == limit:
                return sorted(selected, key=lambda item: (item.t, item.fid))
    return sorted(selected, key=lambda item: (item.t, item.fid))


def _requested_note_steps(spec: str, discovered: Set[int], R: Record) -> List[int]:
    match = re.search(r"(?:^|\|)\s*steps:(\d+):(\d+)", spec)
    if match:
        lo, hi = int(match.group(1)), int(match.group(2))
        return [step for step in range(lo, hi + 1) if step in R.process_notes]
    return sorted(step for step in discovered if step in R.process_notes)


def build_query_answer(R: Record, spec: str, result: Dict[str, Any],
                       max_evidence: int = 10, max_related: int = 6) -> Dict[str, Any]:
    """Build one shallow answer over the facts returned by a DSL query."""
    entities: Set[str] = set()
    fids: Set[int] = set()
    steps: Set[int] = set()
    _walk_result(result, entities, fids, steps, set(R.E))
    for fid in list(fids):
        if 0 <= fid < len(R.F):
            fact = R.fact(fid)
            steps.add(fact.t)
            entities.update(str(arg) for arg in fact.args)
            if fact.purpose is not None:
                entities.add(str(fact.purpose))

    source = spec.split("|", 1)[0].strip()
    focus: Optional[Entity] = None
    if source.startswith("entity:") and source.count(":") >= 2:
        focus_name = source.split("entity:", 1)[1]
        focus = R.E.get(focus_name)
        entities.add(focus_name)

    note_steps = _requested_note_steps(spec, steps, R)
    focus_facts = [] if focus is None else _facts_for_entity(R, focus)
    action_ids = {
        fid
        for step in note_steps
        for fid in R.process_notes[step]["action_outcome"]["action_fids"]
    }
    pool_ids = set(fids) | {fact.fid for fact in focus_facts} | action_ids
    pool = [R.fact(fid) for fid in pool_ids if 0 <= fid < len(R.F)]
    goal = next((item for item in R.goals
                 if focus is not None and str(item.entity()) == str(focus)), None)
    pool_requirement_names = {
        str(fact.purpose) for fact in pool
        if fact.purpose is not None and fact.purpose.kind == "req"
    }
    if goal is not None:
        expected = _expected_texts(goal)
    else:
        expected = [
            value
            for item in R.goals
            if str(item.entity()) in pool_requirement_names
            for value in _expected_texts(item)
        ]
    selected_facts = _select_evidence(pool, focus, expected, max_evidence)

    at_step = max((step for step in steps if step > 0), default=R.length())
    requirement_names = set(pool_requirement_names)
    if focus is not None and focus.kind == "req":
        requirement_names.add(str(focus))
    if source in ("process_notes", "requirements", "packet"):
        requirement_names.update(str(item.entity()) for item in R.goals)

    constraint_progress = []
    for item in R.goals:
        name = str(item.entity())
        if name not in requirement_names:
            continue
        state = goal_state(R, item, at_step)
        transitions = [
            (step, transition)
            for step in range(1, at_step + 1)
            for transition in R.process_notes.get(step, {}).get("requirement_transitions", [])
            if transition["requirement"] == name
        ]
        last_transition = transitions[-1] if transitions else None
        verification_modes = sorted({
            getattr(constraint, "mode", "unknown") for constraint in item.constraints
        })
        if state["state"] == "satisfied":
            execution_state = "satisfying_decision_recorded"
        elif state["state"] == "unresolved":
            execution_state = "semantic_verification_unresolved"
        elif state["decision_fids"]:
            execution_state = "non_satisfying_decision_recorded"
        else:
            execution_state = "no_satisfying_decision_recorded"
        constraint_progress.append({
            "requirement": name,
            "state": state["state"],
            "evidence_state": ("relevant_evidence_observed"
                               if state["evidence_fids"] else "no_relevant_evidence"),
            "execution_state": execution_state,
            "verification_modes": verification_modes,
            "reason": state["reason"],
            "evidence_fids": state["evidence_fids"],
            "decision_fids": state["decision_fids"],
            "last_transition": (None if last_transition is None else
                                f"{last_transition[1]['from']} -> "
                                f"{last_transition[1]['to']}"),
            "transition_step": None if last_transition is None else last_transition[0],
        })

    step_progress = []
    for step in note_steps:
        note = R.process_notes[step]
        source_step = R.source_steps.get(step)
        action = "" if source_step is None else str(source_step.modules.get("action") or "")
        for state in note["requirements"]:
            if requirement_names and state["requirement"] not in requirement_names:
                continue
            transition = next((row for row in note["requirement_transitions"]
                               if row["requirement"] == state["requirement"]), None)
            flag = next((row for row in note["flags"]
                         if row.get("requirement") == state["requirement"]), None)
            step_progress.append({
                "step": step,
                "action": clip_text(action, 160),
                "requirement": state["requirement"],
                "state": state["state"],
                "transition": None if transition is None else
                    f"{transition['from']} -> {transition['to']}",
                "flag": None if flag is None else flag["kind"],
                "evidence_fids": state["evidence_fids"][-6:],
            })

    relation_rows = []
    if focus is not None:
        grouped = defaultdict(list)
        for fact in focus_facts:
            grouped[fact.rel].append(fact)
        for relation, facts in sorted(grouped.items()):
            last = facts[-1]
            relation_rows.append({
                "relation": relation,
                "count": len(facts),
                "last_fid": last.fid,
                "last_step": last.t,
                "last_value": fact_value(last),
            })

    related = sorted(
        (R.E[name] for name in entities if name in R.E and R.E[name] != focus),
        key=lambda entity: (0 if entity.kind == "req" else 1, entity.kind, entity.index),
    )
    related_queries = [
        {"entity": str(entity), "query": f"entity:{entity} | detail:summary"}
        for entity in related[:max_related]
    ]

    if goal is not None:
        state = goal_state(R, goal, at_step)
        answer = {
            "state": state["state"],
            "at_step": at_step,
            "conclusion": state["reason"],
            "evidence_count": len(focus_facts),
            "decision_count": len(state["decision_fids"]),
        }
    elif focus is not None:
        answer = {
            "state": "recorded",
            "at_step": at_step,
            "conclusion": (
                f"{focus} has {len(focus_facts)} linked facts; inspect direct_evidence "
                "and constraint_progress for its role in the run"
            ),
            "evidence_count": len(focus_facts),
        }
    else:
        answer = {
            "state": "evidence_returned",
            "at_step": at_step,
            "conclusion": (
                f"query returned evidence for steps {note_steps or sorted(steps)}; "
                "no root-cause judgement is made"
            ),
            "evidence_count": len(pool),
        }

    return {
        "schema": "query-answer/v2",
        "query": spec,
        "focus": None if focus is None else {"entity": str(focus), "kind": focus.kind},
        "answer": answer,
        "constraint_progress": constraint_progress,
        "relations": relation_rows,
        "direct_evidence": [_fact_line(fact, focus, expected) for fact in selected_facts],
        "step_progress": step_progress,
        "related_queries": related_queries,
        "coverage": {
            "returned_fid_count": len(fids),
            "candidate_evidence_count": len(pool),
            "direct_evidence_count": len(selected_facts),
            "direct_evidence_truncated": len(pool) > len(selected_facts),
            "returned_steps": sorted(step for step in steps if 0 <= step <= R.length()),
            "progress_steps": note_steps,
            "related_entity_count": len(related),
            "related_entities_truncated": len(related) > len(related_queries),
        },
        "boundary": (
            "This is a query-specific evidence answer, not a root-cause or module judgement."
        ),
    }
