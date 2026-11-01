"""Deterministic per-step processing notes over a compiled record.

Notes explain what the record contains at each step. They may flag an unmet or
unresolved requirement, a rejected command, or a state transition, but never
name a faulty module or root cause.
"""
from collections import Counter
from typing import Any, Dict, Iterable, List

from record import Fact, Goal, Record


SCHEMA = "process-notes/v1"


def clip_text(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def fact_value(fact: Fact) -> Any:
    return list(fact.value) if isinstance(fact.value, tuple) else fact.value


def goal_state(R: Record, goal: Goal, at_step: int) -> Dict[str, Any]:
    requirement = str(goal.entity())
    relevant = [
        fact for fact in R.F
        if fact.t <= at_step and fact.purpose is not None
        and str(fact.purpose) == requirement
    ]
    decisions = [
        fact for fact in relevant
        if fact.slot == "chk" and fact.rel in {goal.rel, "satisfied"}
        and isinstance(fact.value, bool)
    ]
    successes = [fact for fact in decisions if fact.value is True]
    observed = [fact for fact in relevant if fact.side == "env" and fact.slot == "obs"]
    if goal.status_mode != "tracked":
        state = "unresolved"
        reason = "semantic constraint is preserved but has no deterministic discharge rule"
    elif len(successes) >= goal.count:
        state = "satisfied"
        reason = "a satisfying decision is recorded"
    elif decisions:
        state = "unmet"
        reason = "a recorded decision failed to satisfy the requirement"
    elif observed:
        state = "unknown"
        reason = "relevant evidence was observed but no satisfying decision is recorded"
    else:
        state = "unknown"
        reason = "no satisfying evidence or decision is recorded"
    return {
        "requirement": requirement,
        "state": state,
        "reason": reason,
        "evidence_fids": [fact.fid for fact in relevant[-12:]],
        "decision_fids": [fact.fid for fact in decisions],
    }


def _entities(facts: Iterable[Fact]) -> List[str]:
    return sorted({str(entity) for fact in facts for entity in fact.args})


def _assignment_rows(R: Record, facts: Iterable[Fact]) -> List[Dict[str, Any]]:
    rows = []
    for fact in facts:
        relation = R.relations[fact.rel]
        if not relation.is_functional():
            continue
        rows.append({
            "fid": fact.fid,
            "step": fact.t,
            "relation": fact.rel,
            "key": [str(fact.args[i]) for i in relation.key],
            "arguments": [str(arg) for arg in fact.args],
            "value": fact_value(fact),
            "source": f"{fact.slot}/{fact.side}",
            "purpose": None if fact.purpose is None else str(fact.purpose),
        })
    return rows


def build_process_notes(R: Record) -> Dict[int, Dict[str, Any]]:
    first_seen: Dict[str, int] = {}
    for fact in R.F:
        for entity in fact.args:
            first_seen.setdefault(str(entity), fact.t)
    notes: Dict[int, Dict[str, Any]] = {}
    previous_states = {
        str(goal.entity()): goal_state(R, goal, 0)["state"] for goal in R.goals
    }
    for step in range(1, R.length() + 1):
        current = R.by_step(step)
        following = R.by_step(step + 1)
        observation = [f for f in current if f.side == "env" and f.slot == "obs"]
        mind = [f for f in current if f.side == "mind"]
        actions = [f for f in mind if f.slot == "act"]
        checks = [f for f in current if f.slot == "chk"]
        next_observation = [
            f for f in following if f.side == "env" and f.slot == "obs"
        ]
        states = [goal_state(R, goal, step) for goal in R.goals]
        transitions = [
            {"requirement": row["requirement"],
             "from": previous_states.get(row["requirement"]), "to": row["state"]}
            for row in states
            if previous_states.get(row["requirement"]) != row["state"]
        ]
        previous_states.update({row["requirement"]: row["state"] for row in states})
        admissibility = [f for f in checks if f.rel == "admissibility"]
        rejected = [f for f in admissibility if f.value not in {"well_formed", "unsupported", "unknown", None}]
        effects = [f for f in next_observation if f.rel == "effect"]
        no_effect = [f for f in effects if str(f.value).lower() == "none"]
        touched_facts = current + next_observation
        touched = _entities(touched_facts)
        new_entities = [entity for entity in touched if first_seen.get(entity) == step]
        flags = []
        flags.extend({"kind": f"constraint_{row['state']}",
                      "requirement": row["requirement"],
                      "reason": row["reason"],
                      "evidence_fids": row["evidence_fids"]}
                     for row in states if row["state"] != "satisfied")
        if rejected:
            flags.append({"kind": "action_rejected", "requirement": None,
                          "reason": str(rejected[-1].value),
                          "evidence_fids": [f.fid for f in rejected]})
        if no_effect:
            flags.append({"kind": "no_observed_effect", "requirement": None,
                          "reason": "the next response records no effect",
                          "evidence_fids": [f.fid for f in no_effect]})
        source = R.source_steps.get(step)
        next_source = R.source_steps.get(step + 1)
        observation_text = "" if source is None else source.observation
        action_text = "" if source is None else str(source.modules.get("action") or "")
        next_text = "" if next_source is None else next_source.observation
        state_text = ", ".join(
            f"{row['requirement']}={row['state']}" for row in states
        ) or "no parsed requirement"
        note_text = (
            f"Step {step}: observed {clip_text(observation_text, 360) or '(none)'}. "
            f"Agent action: {clip_text(action_text, 220) or '(none)'}. "
            f"Next environment response: {clip_text(next_text, 360) or '(terminal/no response)'}. "
            f"Requirement state after the step: {state_text}. "
            f"Entities touched: {', '.join(touched[:12]) or '(none)'}."
        )
        notes[step] = {
            "schema": SCHEMA,
            "step": step,
            "note": note_text,
            "observation": {"text": observation_text,
                            "fids": [f.fid for f in observation]},
            "agent_modules": {
                slot: [{"fid": f.fid, "text": f.span} for f in mind if f.slot == slot]
                for slot in ("mem", "refl", "plan", "act")
            },
            "action_outcome": {
                "action_fids": [f.fid for f in actions],
                "admissibility": None if not admissibility else admissibility[-1].value,
                "admissibility_fids": [f.fid for f in admissibility],
                "next_response_text": next_text,
                "next_observation_fids": [f.fid for f in next_observation],
                "no_effect": bool(no_effect),
            },
            "requirements": states,
            "requirement_transitions": transitions,
            "flags": flags,
            "entities": {
                "touched": touched,
                "introduced": new_entities,
            },
            "assignments": _assignment_rows(R, touched_facts),
            "evidence_fids": sorted({f.fid for f in touched_facts}),
            "boundary": "Process note summarizes recorded evidence; it is not a fault attribution.",
        }
    return notes


def _bounded(values: List[Any], limit: int) -> Dict[str, Any]:
    """Return a stable evidence window and make any truncation explicit."""
    return {
        "items": values[-limit:],
        "count": len(values),
        "truncated": len(values) > limit,
    }


def compact_process_note(note: Dict[str, Any]) -> Dict[str, Any]:
    requirements = [
        {
            "requirement": row["requirement"],
            "state": row["state"],
        }
        for row in note["requirements"]
    ]
    flags = [
        {
            "kind": row["kind"],
            "requirement": row.get("requirement"),
            "reason": row.get("reason"),
            "evidence": _bounded(row.get("evidence_fids", []), 6),
        }
        for row in note["flags"]
    ]
    return {
        "step": note["step"],
        "note": note["note"],
        "requirements": requirements,
        "requirement_transitions": note["requirement_transitions"],
        "flags": flags,
        "entities": {
            "touched_count_by_kind": dict(sorted(Counter(
                value.split(":", 1)[0]
                for value in note["entities"].get("touched", [])
            ).items())),
            "introduced": _bounded([
                clip_text(value, 100)
                for value in note["entities"].get("introduced", [])
            ], 8),
        },
        "action_outcome": {
            "action_fids": _bounded(note["action_outcome"]["action_fids"], 6),
            "admissibility": note["action_outcome"]["admissibility"],
            "next_observation_fids": _bounded(
                note["action_outcome"]["next_observation_fids"], 12
            ),
            "no_effect": note["action_outcome"]["no_effect"],
        },
        "evidence": _bounded(note["evidence_fids"], 24),
        "boundary": note["boundary"],
    }
