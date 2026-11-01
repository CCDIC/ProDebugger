"""Independent structural validation for parsed intentions.

The parser says what a plan declared.  This module compares that declaration
with task constraints, available state, the emitted action, and its recorded
effect.  Keeping the two stages separate prevents an extractor from validating
its own interpretation.
"""
from typing import Any, Dict, List, Optional

from criterion import kappa
from record import Constraint, Entity, Goal, Record


def _norm(value: Any) -> str:
    return " ".join(str(value).split()).lower()


def _specs(goal: Goal) -> List[Constraint]:
    out = []
    for raw in goal.constraints:
        if isinstance(raw, Constraint):
            out.append(raw)
        else:
            attr, op, expected = raw
            out.append(Constraint(Entity("attribute", attr), op, expected))
    if not out:
        out.extend(Constraint(Entity("attribute", a.replace(" ", "_")), "eq", v)
                   for a, v in goal.attrs)
    return out


def _action(R: Record, step: int):
    return next((f for f in R.by_step(step)
                 if f.slot == "act" and f.side == "mind"), None)


def _admissibility(R: Record, step: int) -> Optional[str]:
    rows = [f for f in R.by_step(step) if f.rel == "admissibility"]
    return None if not rows else str(rows[-1].value)


def _recorded_met(R: Record, goal: Goal, step: int) -> bool:
    q = str(goal.entity())
    return any(f.slot == "chk" and f.value is True and f.t <= step
               and f.purpose is not None and str(f.purpose) == q
               for f in R.F)


def _observation_available(R: Record, goal: Goal, step: int) -> bool:
    """Whether an observation bears on a non-selection constraint.

    Availability is deliberately not verification: for example a price shown
    on some listing is not proof that the eventually purchased product had that
    price.  The distinction keeps the validator three-valued.
    """
    if goal.target is None:
        return False
    return any(f.side == "env" and f.t <= step
               and any(str(a) == str(goal.target) for a in f.args)
               for f in R.F)


def _constraint_binding(R: Record, goal: Goal, step: int,
                        plan_span: str, action_span: str) -> Dict[str, Any]:
    q = goal.entity()
    specs = _specs(goal)
    expected = [x.expected for x in specs]
    in_plan = [x for x in expected if _norm(x) and _norm(x) in _norm(plan_span)]
    in_action = [x for x in expected if _norm(x) and _norm(x) in _norm(action_span)]
    decisions = [f for f in R.by_step(step) if f.slot == "chk"
                 and f.purpose is not None and str(f.purpose) == str(q)
                 and f.rel in {goal.rel, "satisfied"} and f.value is not None]

    if decisions:
        relation = "satisfies" if any(f.value is True for f in decisions) else "substitutes"
        evidence = [f.fid for f in decisions]
    elif in_plan or in_action:
        relation = "explicitly_preserves"
        evidence = []
    elif goal.status_mode != "tracked":
        relation = "unmodeled"
        evidence = []
    elif any(x.mode == "observation" for x in specs) and _observation_available(R, goal, step):
        relation = "evidence_available_unverified"
        evidence = []
    elif _recorded_met(R, goal, step):
        relation = "already_satisfied"
        evidence = []
    else:
        relation = "unmentioned"
        evidence = []

    return {
        "requirement": str(q),
        "status_mode": goal.status_mode,
        "constraints": [{
            "property": str(x.property),
            "operator": x.operator,
            "expected": x.expected,
            "value_type": x.value_type,
            "unit": x.unit,
            "mode": x.mode,
        } for x in specs],
        "relation": relation,
        "explicit_in_plan": bool(in_plan),
        "explicit_in_action": bool(in_action),
        "recorded_met_before_or_at_step": _recorded_met(R, goal, step),
        "evidence_fids": evidence,
        "interpretation_limit": "Literal mentions and selective plan extraction are not semantic coverage; unmentioned does not mean ignored. Decisions are compiled checks, not observed outcomes.",
    }


def _preconditions(R: Record, step: int, names, action) -> List[Dict[str, Any]]:
    out = []
    admissibility = _admissibility(R, step)
    for name in names:
        if name == "action_admissible":
            status = "sat" if admissibility == "well_formed" else "viol"
            detail = admissibility
        elif name == "query_executable":
            status = "sat" if action is not None and action.rel == "search" \
                and admissibility == "well_formed" else "viol"
            detail = None if action is None else action.span
        elif name == "target_visible":
            targets = [] if action is None else list(action.args)
            evidence = [f.fid for f in R.F if f.rel == "visible" and f.t <= step
                        and any(a in f.args for a in targets)]
            situated = [f.fid for f in R.F if f.t <= step and f.rel in ("at", "in")
                        and any(a in f.args for a in targets)]
            if admissibility == "well_formed":
                status = "sat"
            else:
                status = "sat" if evidence or situated else "unk"
            detail = evidence + situated
        elif name == "value_offered":
            targets = [] if action is None else [a for a in action.args if a.kind == "value"]
            evidence = [f.fid for f in R.F if f.rel == "in" and f.t <= step
                        and any(a in f.args for a in targets)]
            status = "sat" if evidence else "unk"
            detail = evidence
        elif name == "target_accessible":
            status = "sat" if admissibility == "well_formed" else "unk"
            detail = admissibility
        elif name in ("object_visible", "source_visible"):
            targets = [] if action is None else list(action.args[:2])
            evidence = [f.fid for f in R.F if f.t <= step and f.rel in ("visible", "in", "at")
                        and any(a in f.args for a in targets)]
            status = "sat" if admissibility == "well_formed" or evidence else "unk"
            detail = evidence
        elif name == "hand_free":
            held = [f for f in R.F if f.rel == "holding" and f.side == "env"
                    and f.t <= step and f.value is True]
            status = "sat" if admissibility == "well_formed" or not held else "viol"
            detail = [f.fid for f in held[-3:]]
        elif name == "object_held":
            objects = [] if action is None else [a for a in action.args if a.kind == "portable"][:1]
            evidence = [f.fid for f in R.F if f.rel == "holding" and f.side == "env"
                        and f.t <= step and f.value is True
                        and any(a in f.args for a in objects)]
            status = "sat" if admissibility == "well_formed" or evidence else "unk"
            detail = evidence
        elif name == "tool_or_appliance_available":
            status = "sat" if admissibility == "well_formed" else "unk"
            detail = admissibility
        elif name == "all_task_constraints_verified":
            unmet = []
            unresolved = []
            for g in R.goals:
                if _recorded_met(R, g, step):
                    continue
                specs = _specs(g)
                if (g.status_mode != "tracked"
                        or any(x.mode == "observation" for x in specs)):
                    unresolved.append(str(g.entity()))
                elif any(f.slot == "chk" and f.value is False and f.t <= step
                         and f.purpose == g.entity() and f.rel in {g.rel, "satisfied"} for f in R.F):
                    unmet.append(str(g.entity()))
                else:
                    unresolved.append(str(g.entity()))
            if unmet:
                status = "viol"
            elif unresolved:
                status = "unk"
            else:
                status = "sat"
            detail = {"unmet": unmet, "unresolved": unresolved}
        else:
            status, detail = "unk", None
        out.append({"name": name, "status": status, "detail": detail})
    return out


def _effects(R: Record, step: int, names, action) -> List[Dict[str, Any]]:
    out = []
    after = [f for f in R.by_step(step + 1) if f.side == "env"]
    for name in names:
        evidence = []
        status = "unk" if step >= R.length() else "missing"
        if name == "result_listing_observed":
            evidence = [f.fid for f in after if f.rel in ("at", "visible")]
        elif name == "item_page_observed":
            evidence = [f.fid for f in after if f.rel == "at"
                        and any(a.kind == "item_page" for a in f.args)]
        elif name == "search_page_observed":
            evidence = [f.fid for f in after if f.rel == "at"
                        and any(a.kind == "page" and a.index == "search"
                                for a in f.args)]
        elif name == "selection_recorded":
            evidence = [f.fid for f in R.by_step(step)
                        if f.rel == "chosen" and f.slot == "chk"]
        elif name == "page_transition_observed":
            evidence = [f.fid for f in after if f.rel == "at"]
        elif name == "purchase_committed":
            evidence = [] if action is None or not R.commits(action) else [action.fid]
        elif name == "location_changed":
            targets = [] if action is None else list(action.args)
            evidence = [f.fid for f in after if f.rel == "at"
                        and any(a in f.args for a in targets)]
        elif name == "container_opened":
            targets = [] if action is None else list(action.args)
            evidence = [f.fid for f in after if f.rel == "open" and f.value is True
                        and any(a in f.args for a in targets)]
        elif name == "container_closed":
            targets = [] if action is None else list(action.args)
            evidence = [f.fid for f in after if f.rel == "open" and f.value is False
                        and any(a in f.args for a in targets)]
        elif name == "object_held":
            targets = [] if action is None else [a for a in action.args if a.kind == "portable"][:1]
            evidence = [f.fid for f in after if f.rel == "holding" and f.value is True
                        and any(a in f.args for a in targets)]
        elif name == "object_placed":
            targets = [] if action is None else list(action.args[:2])
            evidence = [f.fid for f in after if f.rel == "in" and f.value is True
                        and all(a in f.args for a in targets)]
        elif name in ("clean_state_recorded", "hot_state_recorded",
                      "cool_state_recorded", "sliced_state_recorded"):
            wanted = name.split("_state_recorded", 1)[0]
            targets = [] if action is None else [a for a in action.args if a.kind == "portable"][:1]
            evidence = [f.fid for f in after if f.rel == "has" and f.value is True
                        and any(a.kind == "attribute" and a.index == wanted for a in f.args)
                        and any(a in f.args for a in targets)]
        elif name in ("observation_refreshed", "state_change_observed"):
            evidence = [f.fid for f in after if not (f.rel == "effect" and f.value == "none")]
        if evidence:
            status = "observed"
        out.append({"name": name, "status": status, "evidence_fids": evidence})
    return out


def validate_intention(R: Record, step: int) -> Dict[str, Any]:
    cache = getattr(R, "_intention_validation_cache", None)
    if cache is None:
        cache = {}
        setattr(R, "_intention_validation_cache", cache)
    cache_key = (step, len(R.F))
    if cache_key in cache:
        return cache[cache_key]

    entity = Entity("int", f"i{step}")
    intention = R.intentions.get(str(entity))
    action = _action(R, step)
    plan = next((f for f in R.by_step(step)
                 if f.slot == "plan" and f.side == "mind"), None)
    if intention is None:
        result = {"entity": str(entity), "status": "absent"}
        cache[cache_key] = result
        return result

    if action is None:
        alignment = {"verdict": "unk", "reason": "no action fact at this step",
                     "cites": []}
    else:
        verdict = kappa(R, action)
        alignment = {"verdict": verdict.value, "reason": verdict.reason,
                     "cites": list(verdict.cites)}

    def option_matches(option) -> bool:
        if action is None or option.verb != action.rel:
            return False
        if option.objects is None:
            return True
        return tuple(str(x) for x in option.objects) == tuple(str(a) for a in action.args)

    realized_option = next((x for x in intention.execution_options
                            if option_matches(x)), None)
    effective_preconditions = (realized_option.preconditions
                               if realized_option is not None
                               else intention.preconditions)
    effective_effects = (realized_option.expected_effects
                         if realized_option is not None
                         else intention.expected_effects)

    result = {
        "entity": str(entity),
        "status": "parsed",
        "extraction_coverage": "selective_heuristic_not_semantically_audited",
        "plan_action_alignment": alignment,
        "constraint_bindings": [
            _constraint_binding(R, g, step, plan.span if plan else "",
                                action.span if action else "")
            for g in R.goals
        ],
        "realized_option": None if realized_option is None else {
            "verb": realized_option.verb,
            "objects": None if realized_option.objects is None else
            [str(x) for x in realized_option.objects],
            "role": realized_option.role,
        },
        "preconditions": _preconditions(R, step, effective_preconditions, action),
        "expected_effects": _effects(R, step, effective_effects, action),
    }
    cache[cache_key] = result
    return result
