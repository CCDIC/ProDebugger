"""Entity-centred evidence views.

Low-level operators return fact ids or local graph moves.  A reasoner should
not have to reconstruct a requirement state, an item's assignments, or an
intention/action pair from those fragments.  This module assembles that
information without making a root-cause judgement.

Every derived label is deliberately narrow:

* constraint matching says whether an observed value satisfies the comparison
  encoded by the goal;
* support is the existing three-valued structural criterion;
* record status says what the compiled record establishes, not what must have
  happened in the underlying environment.

All evidence remains traceable to fact ids.
"""
from typing import Any, Dict, Iterable, List, Optional, Tuple
from collections import Counter
import copy
import re

from criterion import kappa
from intention_validation import validate_intention
from record import Constraint, Entity, Fact, Goal, Record
import primitives as P
from local_context import build_rich_step_context, summarize_rich_context
from process_notes import compact_process_note
from query_answer import build_query_answer


SCHEMA = "entity-evidence/v4"


def _norm(value: Any) -> str:
    return " ".join(str(value).split()).lower()


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def _fact(R: Record, f: Fact, with_support: bool = False) -> Dict[str, Any]:
    out = {
        "fid": f.fid,
        "step": f.t,
        "slot": f.slot,
        "side": f.side,
        "relation": f.rel,
        "arguments": [str(a) for a in f.args],
        "value": _json_value(f.value),
        "value_type": f.value_type,
        "basis": list(f.basis),
        "purpose": None if f.purpose is None else str(f.purpose),
        "span": f.span[:240],
    }
    if with_support and f.side == "mind":
        verdict = kappa(R, f)
        out["support"] = {
            "verdict": verdict.value,
            "reason": verdict.reason,
            "cites": list(verdict.cites),
            "operands": list(verdict.operands),
        }
    if f.fid in getattr(R, 'claim_scopes', {}):
        out['claim_scope'] = dict(R.claim_scopes[f.fid])
    return out


def _constraints(g: Goal) -> List[Dict[str, Any]]:
    raw = g.constraints or tuple((a.replace(" ", "_"), "eq", v)
                                 for a, v in g.attrs)
    out = []
    for item in raw:
        if isinstance(item, Constraint):
            out.append({
                "property": str(item.property),
                "attribute": item.property.index,
                "operator": item.operator,
                "expected": item.expected,
                "value_type": item.value_type,
                "unit": item.unit,
                "mode": item.mode,
                "source_span": item.span,
                "confidence": item.confidence,
            })
        else:
            a, op, expected = item
            out.append({"property": f"attribute:{a}", "attribute": a,
                        "operator": op, "expected": expected,
                        "value_type": "unknown", "unit": None,
                        "mode": "legacy", "source_span": "", "confidence": 1.0})
    return out


def _candidate_value(g: Goal, f: Fact) -> Any:
    """The value in a fact that can be compared with this goal.

    The function only reads explicit relation arguments or payloads.  It does
    not infer an attribute from prose.
    """
    serves_goal = f.purpose is not None and str(f.purpose) == str(g.entity())
    if (g.target is not None and not serves_goal
            and not any(str(a) == str(g.target) for a in f.args)):
        return None
    if isinstance(f.value, tuple) and len(f.value) == 2:
        return tuple(float(x) for x in f.value)
    values = [a.index for a in f.args if a.kind == "value"]
    if values:
        return values[-1]
    if isinstance(f.value, (str, int, float)) and f.value is not True:
        return f.value
    return None


def _match_one(candidate: Any, operator: str, expected: Any) -> str:
    if candidate is None:
        return "unknown"
    if operator == "eq":
        return "yes" if _norm(candidate) == _norm(expected) else "no"
    if operator == "neq":
        return "yes" if _norm(candidate) != _norm(expected) else "no"
    if operator in ("contains", "string_match"):
        return "yes" if _norm(expected) in _norm(candidate) else "no"
    if operator == "semantic_match":
        # Literal containment is decidable.  Anything less remains unknown and
        # is left to the reasoner; this is not a semantic-similarity heuristic.
        return "yes" if _norm(expected) in _norm(candidate) else "unknown"
    if operator == "lt":
        try:
            threshold = float(expected)
            if isinstance(candidate, tuple) and len(candidate) == 2:
                low, high = float(candidate[0]), float(candidate[1])
                if high < threshold:
                    return "yes"
                if low < threshold <= high:
                    return "partial"
                return "no"
            return "yes" if float(candidate) < threshold else "no"
        except (TypeError, ValueError):
            return "unknown"
    return "unknown"


def _constraint_candidate(g: Goal, f: Fact, spec: Dict[str, Any]) -> Any:
    prop = spec["attribute"]
    if prop == "object_type" and f.rel == "has":
        if any(a.kind == "attribute" and a.index == "object_type" for a in f.args):
            values = [a.index for a in f.args if a.kind == "value"]
            return values[-1] if values else None
    if prop in ("destination_type", "instrument_type"):
        expected = _norm(spec["expected"])
        places = [a for a in f.args if a.kind not in
                  ("agent", "portable", "attribute", "value", "req", "int")]
        for place in places:
            if _norm(place.kind) == expected or _norm(place.index) == expected:
                return spec["expected"]
        return None
    if spec["value_type"] == "boolean" and f.rel == "has":
        if any(a.kind == "attribute" and a.index == prop for a in f.args):
            return bool(f.value)
        return None
    if prop == "description" and f.rel == "describes":
        return f.value
    return _candidate_value(g, f)


def _qualification(g: Goal, f: Fact) -> Tuple[str, Any, List[Dict[str, Any]]]:
    specs = _constraints(g)
    if not specs:
        candidate = _candidate_value(g, f)
        return "unknown", candidate, []
    details = []
    for spec in specs:
        candidate = _constraint_candidate(g, f, spec)
        details.append({
            "property": spec["property"],
            "expected": spec["expected"],
            "observed": _json_value(candidate),
            "match": _match_one(candidate, spec["operator"], spec["expected"]),
        })
    results = [x["match"] for x in details]
    addressed = [x for x in details if x["observed"] is not None]
    candidate = ({x["property"]: x["observed"] for x in addressed}
                 if len(specs) > 1 else details[0]["observed"])
    if all(x == "yes" for x in results):
        return "yes", candidate, details
    if "no" in results:
        return "no", candidate, details
    if "yes" in results:
        return "partial", candidate, details
    if "partial" in results:
        return "partial", candidate, details
    return "unknown", candidate, details


def _mental_context(R: Record, step: int) -> List[Dict[str, Any]]:
    return [_fact(R, f, with_support=True) for f in R.by_step(step)
            if f.side == "mind" and f.slot in ("mem", "refl", "plan", "act")]


def _mentions(span: str, expected: Any) -> bool:
    return bool(_norm(expected)) and _norm(expected) in _norm(span)


def _contract_coverage(R: Record, span: str) -> Dict[str, Any]:
    """Literal task-constraint coverage for one module span.

    Coverage is evidence about information flow, not a fault verdict.  A module
    need not restate the whole task on every step, but a reasoner can now test
    whether a detail disappeared before a downstream decision instead of
    inferring omission from an empty parser result.
    """
    present, missing = [], []
    for g in R.goals:
        expected = [c[2] if not isinstance(c, Constraint) else c.expected
                    for c in (g.constraints or ())]
        named = any(_mentions(span, value) for value in expected)
        row = {"requirement": str(g.entity()), "expected": expected}
        (present if named else missing).append(row)
    return {
        "mentioned": present,
        "not_literal": missing,
        "warning": "not_literal is a retrieval cue, not proof that the module ignored the requirement",
    }


def _assessment_claims(span: str) -> Dict[str, Any]:
    """Expose coarse claims made by a reflection/memory without judging them."""
    low = _norm(span)
    neg_complete = bool(re.search(
        r"\b(?:not|hasn'?t|haven'?t|wasn'?t|isn'?t)\b.{0,30}\b(?:complete|completed|achieved|done)\b",
        low,
    ))
    pos_complete = bool(re.search(
        r"\b(?:task|goal).{0,35}\b(?:is|was|appears to be|has been|have)\b.{0,18}"
        r"\b(?:complete|completed|achieved|done)\b|"
        r"\bprogress (?:is|was|appears to be) (?:complete|completed|done)\b|"
        r"\bi have (?:successfully )?(?:completed|achieved)\b",
        low,
    )) and not neg_complete
    no_progress = bool(re.search(
        r"\b(?:no|not|did not|without)\b.{0,20}\bprogress\b|\bprogress (?:is )?(?:stalled|limited)\b",
        low,
    ))
    progress = bool(re.search(r"\b(?:made|making|is being made|have made) progress\b", low)) \
        and not no_progress
    success = bool(re.search(r"\b(?:was|is) successful\b|\bsuccessfully\b", low))
    failure = bool(re.search(
        r"\b(?:was|is) (?:not successful|unsuccessful|refused)\b|"
        r"\b(?:did not|does not) (?:work|succeed|complete|result)\b|\bnothing happens\b",
        low,
    ))
    causal = [m.group(0) for m in re.finditer(
        r"[^.]{0,100}\b(?:because|due to|caused by|issue (?:is|seems to be)|suggests that)\b[^.]{0,140}",
        low,
    )]
    return {
        "task_completion": "complete" if pos_complete else
                           ("incomplete" if neg_complete else "unstated"),
        "progress": "progress" if progress else
                    ("no_progress" if no_progress else "unstated"),
        "last_outcome": "success" if success and not failure else
                        ("failure" if failure and not success else
                         ("mixed" if success and failure else "unstated")),
        "causal_spans": causal[:3],
    }


def _task_state_at(R: Record, step: int) -> List[Dict[str, Any]]:
    rows = []
    for g in R.goals:
        decisions = [f for f in R.F if f.t <= step and f.slot == "chk"
                     and f.purpose is not None
                     and str(f.purpose) == str(g.entity())
                     and f.rel in {g.rel, "satisfied"} and isinstance(f.value, bool)]
        latest = decisions[-1] if decisions else None
        rows.append({
            "requirement": str(g.entity()),
            "state": "satisfied" if latest is not None and latest.value is True else
                     ("unmet" if latest is not None and latest.value is False else "unknown"),
            "decision_fid": None if latest is None else latest.fid,
        })
    return rows


def _action_history(action: Optional[Fact], prior: List[Fact]) -> Dict[str, Any]:
    if action is None:
        return {"prior_exact_steps": [], "prior_schema_steps": [],
                "prior_target_family_steps": [], "near_duplicate_steps": []}
    exact = (action.rel, tuple(str(a) for a in action.args), _norm(action.value))
    schema = (action.rel, tuple(a.kind for a in action.args))
    target_kinds = tuple(a.kind for a in action.args if a.kind not in ("value", "agent"))
    words = set(re.findall(r"[a-z0-9]+", _norm(action.value)))
    exact_steps, schema_steps, family_steps, near_steps = [], [], [], []
    for old in prior:
        old_exact = (old.rel, tuple(str(a) for a in old.args), _norm(old.value))
        old_schema = (old.rel, tuple(a.kind for a in old.args))
        old_targets = tuple(a.kind for a in old.args if a.kind not in ("value", "agent"))
        if old_exact == exact:
            exact_steps.append(old.t)
        if old_schema == schema:
            schema_steps.append(old.t)
        if target_kinds and old_targets == target_kinds:
            family_steps.append(old.t)
        old_words = set(re.findall(r"[a-z0-9]+", _norm(old.value)))
        union = words | old_words
        if action.rel == old.rel and union and len(words & old_words) / len(union) >= 0.8:
            near_steps.append(old.t)
    return {
        "prior_exact_steps": exact_steps,
        "prior_schema_steps": schema_steps,
        "prior_target_family_steps": family_steps,
        "near_duplicate_steps": near_steps,
    }


def _available_target_summary(R: Record, step: int) -> Dict[str, Any]:
    """Compact, non-ranking inventory of targets exposed by the record so far."""
    targets: Dict[str, set] = {}
    for f in R.F:
        if f.t > step or f.side != "env" or f.rel not in ("visible", "at", "in", "open"):
            continue
        for entity in f.args:
            if entity.kind in ("agent", "value", "attribute", "int", "req"):
                continue
            targets.setdefault(entity.kind, set()).add(str(entity))
    return {
        "counts_by_kind": {kind: len(values) for kind, values in sorted(targets.items())},
        "examples_by_kind": {
            kind: sorted(values)[:5] for kind, values in sorted(targets.items())
        },
        "note": "Inventory only: the view does not rank likelihood or utility.",
    }


def _exploration_coverage(memory_facts: List[Fact], prior_actions: List[Fact]) -> Dict[str, Any]:
    """Measure which previously acted-on entities are preserved in memory text."""
    span = _norm(" ".join(f.span for f in memory_facts if f.span))
    targets = []
    for action in prior_actions:
        for entity in action.args:
            if entity.kind in ("agent", "value", "page", "attribute"):
                continue
            key = str(entity)
            if key not in targets:
                targets.append(key)

    def mentioned(key: str) -> bool:
        kind, _, index = key.partition(":")
        variants = {key.lower(), f"{kind} {index}".lower(), index.lower()}
        return any(v and v in span for v in variants)

    kept = [x for x in targets if mentioned(x)]
    return {
        "prior_targets": targets,
        "mentioned_targets": kept,
        "omitted_targets": [x for x in targets if x not in kept],
        "note": "Omission is a retrieval cue, not a fault; test whether it causes a later revisit or lost option.",
    }


def _observed_product_summaries(after: List[Fact]) -> List[Dict[str, Any]]:
    """Compact the current page without judging whether a product is suitable."""
    products: Dict[str, Dict[str, Any]] = {}
    for f in after:
        if not f.args or f.args[0].kind != "product":
            continue
        key = str(f.args[0])
        row = products.setdefault(key, {"product": key, "observed": {}, "fids": []})
        row["fids"].append(f.fid)
        if f.rel == "has" and len(f.args) >= 2 and f.args[1].kind == "attribute":
            row["observed"][f.args[1].index] = f.value
    return list(products.values())


def _intention_row(R: Record, step: int) -> Optional[Dict[str, Any]]:
    cache = getattr(R, "_entity_view_intention_cache", None)
    if cache is None:
        cache = {}
        setattr(R, "_entity_view_intention_cache", cache)
    cache_key = (step, len(R.F))
    if cache_key in cache:
        return cache[cache_key]

    int_e = Entity("int", f"i{step}")
    intention = R.intentions.get(str(int_e))
    plans = [f for f in R.by_step(step) if f.slot == "plan" and f.side == "mind"]
    actions = [f for f in R.by_step(step) if f.slot == "act" and f.side == "mind"]
    if intention is None and not plans and not actions:
        cache[cache_key] = None
        return None
    plan = plans[0] if plans else None
    action = actions[0] if actions else None
    checks = [f for f in R.by_step(step) if f.rel == "admissibility"]
    mentions = []
    for g in R.goals:
        for c in _constraints(g):
            mentions.append({
                "requirement": str(g.entity()),
                "attribute": c["attribute"],
                "operator": c["operator"],
                "expected": c["expected"],
                "value_type": c["value_type"],
                "mode": c["mode"],
                "in_plan": _mentions(plan.span if plan else "", c["expected"]),
                "in_action": _mentions(action.span if action else "", c["expected"]),
            })
    row = {
        "step": step,
        "intention": None if intention is None else {
            "entity": str(int_e),
            "binding": intention.binding,
            "verb": intention.verb,
            "objects": None if intention.objects is None else [str(x) for x in intention.objects],
            "recorded_parent": None if intention.purpose is None else str(intention.purpose),
            "parent_note": "The recorded parent is a structural anchor, not proof that the intention serves only that requirement.",
            "parameters": [{"name": x.name, "value": x.value,
                            "value_type": x.value_type, "span": x.span}
                           for x in intention.parameters],
            "execution_options": [{
                "verb": x.verb,
                "objects": None if x.objects is None else [str(o) for o in x.objects],
                "role": x.role,
                "span": x.span,
                "preconditions": list(x.preconditions),
                "expected_effects": list(x.expected_effects),
            } for x in intention.execution_options],
            "serves": [str(x) for x in intention.serves],
            "mentions": [str(x) for x in intention.mentions],
            "preconditions": list(intention.preconditions),
            "expected_effects": list(intention.expected_effects),
            "commitment": intention.commitment,
            "extraction": intention.extraction,
            "confidence": intention.confidence,
        },
        "plan": None if plan is None else _fact(R, plan, with_support=True),
        "action": None if action is None else _fact(R, action, with_support=True),
        "admissibility": None if not checks else checks[-1].value,
        "constraint_mentions": mentions,
        "validation": validate_intention(R, step),
        "absence_note": "A missing literal is not by itself proof that a constraint was dropped.",
    }
    cache[cache_key] = row
    return row


def _requirement_view(R: Record, q: Entity) -> Dict[str, Any]:
    cache = getattr(R, "_entity_view_requirement_cache", None)
    if cache is None:
        cache = {}
        setattr(R, "_entity_view_requirement_cache", cache)
    cache_key = (str(q), len(R.F))
    if cache_key in cache:
        return cache[cache_key]

    g = next((x for x in R.goals if str(x.entity()) == str(q)), None)
    if g is None:
        known = sorted(str(x.entity()) for x in R.goals)
        raise KeyError(f"{q} is not a requirement; known: {known}")

    relevant = [f for f in R.F
                if (f.purpose is not None and str(f.purpose) == str(q))
                or (g.target is not None and any(str(a) == str(g.target) for a in f.args))]
    observed = [f for f in relevant if f.slot == "obs" and f.side == "env"]
    decisions = [f for f in relevant if f.slot == "chk" and f.rel in {g.rel, "satisfied"}
                 and f.value is not None]
    claims = [f for f in relevant if f.side == "mind" and f.rel == g.rel]
    met = [f for f in decisions if f.value is True]

    observations: List[Dict[str, Any]] = []
    qualifying_steps = set()
    fully_qualifying_steps = set()
    for step in sorted({f.t for f in observed}):
        rows = []
        for f in [x for x in observed if x.t == step]:
            match, candidate, constraint_matches = _qualification(g, f)
            if match in ("yes", "partial"):
                qualifying_steps.add(step)
            if match == "yes":
                fully_qualifying_steps.add(step)
            rows.append({
                "fid": f.fid,
                "relation": f.rel,
                "arguments": [str(a) for a in f.args],
                "observed_value": _json_value(candidate),
                "qualification": match,
                "constraint_matches": constraint_matches,
                "span": f.span[:160],
            })
        observations.append({
            "step": step,
            "event": "observation",
            "values": rows,
            "context": _mental_context(R, step),
        })

    decision_rows = []
    for f in decisions:
        decision_rows.append({
            "step": f.t,
            "event": "decision",
            "selected_value": _candidate_value(g, f),
            "satisfied": bool(f.value),
            "fact": _fact(R, f),
            "context": _mental_context(R, f.t),
        })

    claim_rows = []
    for f in claims:
        claim_rows.append({
            "step": f.t,
            "event": "claim",
            "claimed_value": _candidate_value(g, f),
            "fact": _fact(R, f, with_support=True),
        })

    if g.status_mode != "tracked":
        state, reason = "unresolved", "the constraint is parsed but has no deterministic discharge rule"
    elif len(met) >= g.count:
        state, reason = "satisfied", "a satisfying decision fact is recorded"
    elif decisions:
        state, reason = "unmet", "the recorded decision did not satisfy the constraint"
    elif qualifying_steps:
        state, reason = "unknown", "qualifying evidence exists but no satisfying decision is recorded"
    elif observations:
        state, reason = "unknown", "only non-qualifying or unresolved observations are recorded"
    else:
        state, reason = "unknown", "no relevant observation or satisfying decision is recorded"

    limitations = ["Missing extracted state is unknown, not false. Compiled decisions are not observed effects; task satisfaction is not agent responsibility."]
    if g.status_mode != "tracked":
        limitations.append(
            "This free-text constraint is preserved in the contract but is not yet deterministically verified."
        )
    if g.name == "price" and not decisions:
        limitations.append(
            "Price observations do not currently create a chosen(price, value) discharge fact."
        )

    related_steps = ({f.t for f in observed + decisions + claims}
                     | {f.t for f in R.F if f.slot == "act" and f.rel == "search"}
                     | {f.t for f in R.F if f.slot == "act" and R.commits(f)})
    intentions = [row for row in (_intention_row(R, t) for t in sorted(related_steps))
                  if row is not None]

    changes = []
    prior = None
    for f in sorted(claims + decisions, key=lambda x: (x.t, x.fid)):
        value = _candidate_value(g, f)
        changes.append({
            "step": f.t,
            "fid": f.fid,
            "source": "claim" if f.side == "mind" else "decision",
            "from": prior,
            "to": value,
            "satisfied": bool(f.value) if f.slot == "chk" else None,
        })
        prior = value

    gaps = []
    if state in ("unmet", "unknown", "unresolved"):
        gaps.append({"kind": reason.replace(" ", "_"),
                     "qualifying_steps": sorted(qualifying_steps),
                     "decision_steps": sorted({f.t for f in decisions})})

    result = {
        "schema": SCHEMA,
        "entity": {"id": str(q), "kind": "requirement"},
        "definition": {
            "count": g.count,
            "relation": g.rel,
            "target": None if g.target is None else str(g.target),
            "constraints": _constraints(g),
            "status_mode": g.status_mode,
        },
        "status": {
            "state": state,
            "recorded_met": len(met),
            "required_count": g.count,
            "reason": reason,
            "qualifying_steps": sorted(qualifying_steps),
            "fully_qualifying_steps": sorted(fully_qualifying_steps),
            "decision_steps": sorted({f.t for f in decisions}),
            "limitations": limitations,
        },
        "evidence": observations + decision_rows + claim_rows,
        "value_changes": changes,
        "intentions": intentions,
        "gaps": gaps,
    }
    cache[cache_key] = result
    return result


def _product_visits(R: Record, product: Entity) -> List[Dict[str, Any]]:
    clicks = [f for f in R.F if f.slot == "act" and f.rel == "click"
              and any(str(a) == str(product) for a in f.args)]
    visits = []
    for click in clicks:
        start = click.t + 1
        end = R.length()
        for action in [f for f in R.F if f.slot == "act" and f.t > click.t]:
            value = _norm(action.value)
            other_product = any(a.kind == "product" and str(a) != str(product)
                                for a in action.args)
            if other_product or action.rel == "search" or value in {"back to search", "< prev", "next >"}:
                end = action.t
                break
            if R.commits(action):
                end = action.t
                break
        env = [f for f in R.F if start <= f.t <= end and f.side == "env"]
        offered: Dict[str, List[Dict[str, Any]]] = {}
        for f in env:
            if f.rel == "in" and len(f.args) == 2 and f.args[1].kind == "attribute":
                offered.setdefault(f.args[1].index, []).append({
                    "fid": f.fid, "step": f.t, "value": f.args[0].index,
                    "payload": _json_value(f.value),
                })
        selections = [f for f in env if f.rel == "chosen"]
        visits.append({
            "click": _fact(R, click, with_support=True),
            "observation_steps": sorted({f.t for f in env}),
            "offered_assignments": offered,
            "recorded_selections": [_fact(R, f) for f in selections],
        })
    return visits


def _intention_view(R: Record, e: Entity) -> Dict[str, Any]:
    i = R.intentions.get(str(e))
    if i is None:
        known = sorted(R.intentions)
        raise KeyError(f"{e} is not an intention; known: {known[:12]}")
    try:
        step = int(e.index.lstrip("i"))
    except ValueError:
        step = -1
    row = _intention_row(R, step)
    facts = P.entity_facts(R, e)
    return {
        "schema": SCHEMA,
        "entity": {"id": str(e), "kind": "intention"},
        "definition": row["intention"] if row else None,
        "evidence": [_fact(R, f, with_support=True) for f in facts],
        "execution": row,
    }


def _generic_view(R: Record, e: Entity) -> Dict[str, Any]:
    if str(e) not in R.E:
        same_kind = sorted(str(x) for x in R.E.values() if x.kind == e.kind)
        raise KeyError(f"{e} is not in the record; known {e.kind}: {same_kind[:20]}")
    facts = P.entity_facts(R, e)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for f in facts:
        groups.setdefault(f.rel, []).append(_fact(R, f, with_support=True))
    out = {
        "schema": SCHEMA,
        "entity": {"id": str(e), "kind": e.kind},
        "relations": groups,
        "evidence": [_fact(R, f, with_support=True) for f in facts],
        "intentions": [],
    }
    related_steps = sorted({f.t for f in facts if f.slot in ("plan", "act")})
    out["intentions"] = [row for row in (_intention_row(R, t) for t in related_steps)
                          if row is not None]
    if e.kind == "product":
        assignments: Dict[str, List[Dict[str, Any]]] = {}
        for f in facts:
            if f.rel == "has" and len(f.args) == 3:
                assignments.setdefault(f.args[1].index, []).append({
                    "fid": f.fid, "step": f.t, "value": f.args[2].index,
                    "payload": _json_value(f.value),
                })
        out["assignments"] = assignments
        out["page_visits"] = _product_visits(R, e)
    return out


def entity_view(R: Record, kind: str, index: str) -> Dict[str, Any]:
    e = Entity(kind, str(index))
    if kind == "req":
        return _requirement_view(R, e)
    if kind == "int":
        return _intention_view(R, e)
    return _generic_view(R, e)


def _all_changes(R: Record, include_initial: bool = False) -> Dict[str, Any]:
    """Every explicit assignment transition under a relation's declared key.

    This is algebraic rather than domain-specific: version positions are the
    assigned value when a relation has them, otherwise the fact payload is.
    Repeated observations of the same assignment are not changes.
    """
    rows = []
    for rel_name, rel in R.relations.declared().items():
        if not rel.is_functional():
            continue
        groups: Dict[Tuple[str, ...], List[Fact]] = {}
        for f in R.by_rel(rel_name):
            key = tuple(str(f.args[i]) for i in rel.key)
            groups.setdefault(key, []).append(f)
        for key, facts in groups.items():
            previous = None
            initialized = False
            for f in sorted(facts, key=lambda x: (x.t, x.fid)):
                positions = rel.version_positions()
                assigned = ([str(f.args[i]) for i in positions]
                            if positions else _json_value(f.value))
                changed = initialized and assigned != previous
                if changed or (include_initial and not initialized):
                    rows.append({
                        "event": "change" if changed else "initial_assignment",
                        "step": f.t,
                        "fid": f.fid,
                        "relation": rel_name,
                        "key": list(key),
                        "entities": [str(a) for a in f.args],
                        "source": {"side": f.side, "slot": f.slot},
                        "from": previous,
                        "to": assigned,
                        "fact_value": _json_value(f.value),
                        "purpose": None if f.purpose is None else str(f.purpose),
                        "span": f.span[:160],
                    })
                previous, initialized = assigned, True
    return {"schema": SCHEMA, "changes": sorted(rows, key=lambda x: (x["step"], x["fid"]))}


def _requirements_view(R: Record) -> Dict[str, Any]:
    rows = []
    for g in R.goals:
        v = _requirement_view(R, g.entity())
        rows.append({
            "entity": v["entity"],
            "definition": v["definition"],
            "status": v["status"],
            "gaps": v["gaps"],
            # Full evidence remains available through entity:req:<name>.
            "evidence_counts": {
                "events": len(v["evidence"]),
                "changes": len(v["value_changes"]),
                "intentions": len(v["intentions"]),
            },
        })
    return {
        "schema": SCHEMA,
        "task_contract": {
            "verbatim": R.task,
            "structured_requirement_count": len(rows),
            "tracked_requirement_count": sum(g.status_mode == "tracked" for g in R.goals),
            "unmodeled_requirement_count": sum(g.status_mode != "tracked" for g in R.goals),
            "coverage": "not_proven_exhaustive",
            "limitation": (
                "The verbatim task is authoritative. Structured requirements "
                "include dynamic labelled properties and preserve free-text "
                "qualifiers, but a preserved phrase may not be semantically decomposed."
            ),
        },
        "requirements": rows,
    }


def _intentions_view(R: Record) -> Dict[str, Any]:
    rows = []
    for e in sorted((x for x in R.E.values() if x.kind == "int"), key=lambda x: x.index):
        try:
            step = int(e.index.lstrip("i"))
        except ValueError:
            continue
        row = _intention_row(R, step)
        if row is not None:
            rows.append(row)
    return {"schema": SCHEMA, "intentions": rows}


def _episodes_view(R: Record) -> Dict[str, Any]:
    """One temporal bracket per action step.

    This is a retrieval surface, not a ranking function.  Signals name recorded
    discontinuities so the reasoner can request a small candidate window; they
    never declare which discontinuity is the root cause.
    """
    changes = _all_changes(R, include_initial=False)["changes"]
    by_change_step: Dict[int, List[Dict[str, Any]]] = {}
    for change in changes:
        by_change_step.setdefault(change["step"], []).append(change)
    rows = []
    seen_env_signatures = set()
    previous_actions = set()
    prior_action_facts: List[Fact] = []
    prior_budget = {
        "invalid_steps": [],
        "no_effect_steps": [],
        "productive_steps": [],
        "unproductive_steps": [],
    }
    for step in range(1, R.length() + 1):
        intent = _intention_row(R, step)
        mind = [f for f in R.by_step(step)
                if f.side == "mind" and f.slot in ("mem", "refl", "plan", "act")]
        action = next((f for f in mind if f.slot == "act"), None)
        plan = next((f for f in mind if f.slot == "plan"), None)
        memories = [f for f in mind if f.slot == "mem"]
        reflections = [f for f in mind if f.slot == "refl"]
        reflection = reflections[0] if reflections else None
        after = [f for f in R.by_step(step + 1) if f.side == "env"]
        validation = {} if intent is None else intent.get("validation", {})
        intention = {} if intent is None else (intent.get("intention") or {})
        admissibility = None if intent is None else intent.get("admissibility")
        bindings = validation.get("constraint_bindings", [])
        preconditions = validation.get("preconditions", [])
        effects = validation.get("expected_effects", [])
        alignment = validation.get("plan_action_alignment", {})
        history = _action_history(action, prior_action_facts)
        module_rows = {
            "memory": [_fact(R, f, with_support=True) for f in memories],
            "reflection": [_fact(R, f, with_support=True) for f in reflections],
            "plan": [] if plan is None else [_fact(R, plan, with_support=True)],
            "action": [] if action is None else [_fact(R, action, with_support=True)],
        }
        module_analysis = {}
        for module, facts in (("memory", memories), ("reflection", reflections),
                              ("plan", [] if plan is None else [plan]),
                              ("action", [] if action is None else [action])):
            span = "\n".join(f.span for f in facts if f.span)
            module_analysis[module] = {
                "fact_ids": [f.fid for f in facts],
                "contract_coverage": _contract_coverage(R, span),
                "claims": _assessment_claims(span) if module in ("memory", "reflection") else None,
            }
        module_analysis["memory"]["exploration_coverage"] = _exploration_coverage(
            memories, prior_action_facts
        )

        signals = []
        if action is not None and admissibility not in (None, "well_formed"):
            signals.append("invalid_action")
        if alignment.get("verdict") == "viol":
            signals.append("alignment_violation")
        if intention.get("binding") in ("unbound", "absent"):
            signals.append("unbound_intention")
        if any(x.get("status") == "viol" for x in preconditions):
            signals.append("failed_precondition")
        if any(x.get("status") == "missing" for x in effects):
            signals.append("missing_effect")
        if any(x.get("relation") == "substitutes" for x in bindings):
            signals.append("constraint_substitution")
        if any(f.rel == "effect" and f.value == "none" for f in after):
            signals.append("no_effect")
        if by_change_step.get(step + 1):
            signals.append("state_change")
        task_changes = [x for x in by_change_step.get(step + 1, [])
                        if str(x.get("purpose") or "").startswith("req:")]
        if task_changes:
            signals.append("task_state_change")
        after_task = [f for f in after if f.purpose is not None
                      and str(f.purpose).startswith("req:")]
        new_task = []
        for f in after_task:
            signature = (f.rel, tuple(str(a) for a in f.args), repr(f.value))
            if signature not in seen_env_signatures:
                new_task.append(f)
            seen_env_signatures.add(signature)
        if new_task:
            signals.append("new_task_evidence")
        elif action is not None and after:
            signals.append("no_new_task_evidence")
        if action is not None:
            action_signature = (action.rel, tuple(str(a) for a in action.args),
                                _norm(action.value))
            if action_signature in previous_actions:
                signals.append("repeated_action")
            previous_actions.add(action_signature)
        if history["near_duplicate_steps"]:
            signals.append("near_duplicate_action")
        if history["prior_schema_steps"]:
            signals.append("schema_repetition")
        reflection_claims = module_analysis["reflection"]["claims"] or {}
        if reflection_claims.get("task_completion") == "complete":
            signals.append("claims_task_complete")
            if any(x["state"] != "satisfied" for x in _task_state_at(R, step)):
                signals.append("contradicted_completion")
        if action is not None and R.commits(action):
            signals.append("commit")
        if step == R.length() and action is not None and not R.commits(action):
            signals.append("terminal_cutoff")
        decisions = [f for f in R.by_step(step)
                     if f.slot == "chk" and f.purpose is not None
                     and f.rel != "admissibility" and f.value is not None]
        if decisions:
            signals.append("task_decision")
        if any(f.value is False for f in decisions):
            signals.append("wrong_task_decision")
        if any(f.value is True for f in decisions):
            signals.append("satisfying_task_decision")

        unresolved = [x["requirement"] for x in _task_state_at(R, step)
                      if x["state"] != "satisfied"]
        completion_slots = [
            module for module in ("memory", "reflection")
            if (module_analysis[module]["claims"] or {}).get("task_completion") == "complete"
        ]
        decision_boundary = {
            "commit_action": bool(action is not None and R.commits(action)),
            "unresolved_requirements": unresolved,
            "completion_claim_slots": completion_slots,
            "authorization_slot": (
                "reflection" if "reflection" in completion_slots else
                ("memory" if "memory" in completion_slots else
                 ("plan" if action is not None and R.commits(action) else None))
            ),
            "note": "authorization_slot identifies where uncertainty became permission to commit; it is not a root-cause verdict.",
        }

        budget_effect = {
            "step_consumed": action is not None,
            "command_accepted": None if action is None else admissibility == "well_formed",
            "no_effect_observed": any(f.rel == "effect" and f.value == "none" for f in after),
            "new_task_evidence": bool(new_task),
            "state_changed": bool(by_change_step.get(step + 1)),
        }

        before = sorted({fid for f in (memories + reflections +
                                       [x for x in (plan, action) if x is not None])
                         for fid in f.basis})
        rows.append({
            "step": step,
            "signals": list(dict.fromkeys(signals)),
            "task_links": bindings,
            "before": {
                "basis_fids": before,
                "candidate_fids": [f.fid for f in mind],
                "task_state": _task_state_at(R, step),
                "action_history": history,
                "budget_history": {k: list(v) for k, v in prior_budget.items()},
                "available_targets": _available_target_summary(R, step),
            },
            "at": {
                "modules": module_rows,
                "module_analysis": module_analysis,
                "plan": None if plan is None else _fact(R, plan, with_support=True),
                "action": None if action is None else _fact(R, action, with_support=True),
                "intention": intention or None,
                "admissibility": admissibility,
                "alignment": alignment or None,
                "preconditions": preconditions,
                "decision_boundary": decision_boundary,
            },
            "after": {
                "observation": [_fact(R, f) for f in after],
                "expected_effects": effects,
                "changes": by_change_step.get(step + 1, []),
                "new_task_evidence_fids": [f.fid for f in new_task],
                "task_decisions": [_fact(R, f) for f in decisions],
                "budget_effect": budget_effect,
                "observed_products": _observed_product_summaries(after),
            },
        })
        if action is not None:
            prior_action_facts.append(action)
            if admissibility != "well_formed":
                prior_budget["invalid_steps"].append(step)
            if budget_effect["no_effect_observed"]:
                prior_budget["no_effect_steps"].append(step)
            if new_task:
                prior_budget["productive_steps"].append(step)
            elif after:
                prior_budget["unproductive_steps"].append(step)
    return {"schema": SCHEMA, "episodes": rows}


def _scan_view(R: Record) -> Dict[str, Any]:
    """Loss-bounded first-pass view over every step.

    The scan preserves all four agent slots, local outcome, constraint state,
    coverage/budget summaries, and fact ids.  It intentionally omits the bulk
    observation graph; the reasoner can reopen any step or entity afterward.
    """
    full = _episodes_view(R)
    requirements = _requirements_view(R)
    contract_words = set(re.findall(
        r"[a-z0-9]+", _norm(requirements["task_contract"]["verbatim"])
    )) - {"the", "a", "an", "and", "or", "with", "in", "on", "to", "me", "find"}
    rows = []
    for episode in full["episodes"]:
        analysis = episode["at"]["module_analysis"]

        def compact_module(name: str) -> Dict[str, Any]:
            facts = episode["at"]["modules"][name]
            spans = []
            for fact in facts:
                span = str(fact.get("span") or "")
                if span and span not in spans:
                    spans.append(span)
            return {
                "fids": [f["fid"] for f in facts],
                "text": " ".join(spans)[:260],
                "support": [f.get("support", {}).get("verdict") for f in facts],
                "claims": {
                    k: (analysis[name].get("claims") or {}).get(k)
                    for k in ("task_completion", "progress", "last_outcome")
                } if name in ("memory", "reflection") else None,
                "contract_mentioned_count": len(
                    analysis[name]["contract_coverage"]["mentioned"]
                ),
                "contract_missing_count": len(
                    analysis[name]["contract_coverage"]["not_literal"]
                ),
            }

        memory = compact_module("memory")
        coverage = analysis["memory"].get("exploration_coverage", {})
        memory["exploration_coverage"] = {
            "prior_count": len(coverage.get("prior_targets", [])),
            "mentioned_count": len(coverage.get("mentioned_targets", [])),
            "omitted_count": len(coverage.get("omitted_targets", [])),
            "omitted_examples": coverage.get("omitted_targets", [])[-6:],
        }
        after = episode["after"]
        products = []
        for product in after.get("observed_products", []):
            observed = product.get("observed", {})
            products.append({
                "product": product["product"],
                "title": str(observed.get("title") or "")[:120],
                "price": observed.get("price"),
                "fids": product.get("fids", []),
                "literal_overlap": len(
                    contract_words & set(re.findall(
                        r"[a-z0-9]+", _norm(observed.get("title") or "")
                    ))
                ),
            })
        products.sort(key=lambda x: (-x["literal_overlap"], x["product"]))
        product_count = len(products)
        products = products[:4]
        history = episode["before"]["action_history"]
        budget = episode["before"]["budget_history"]
        rows.append({
            "step": episode["step"],
            "signals": episode["signals"],
            "memory": memory,
            "reflection": compact_module("reflection"),
            "plan": compact_module("plan"),
            "action": compact_module("action"),
            "control": {
                "admissibility": episode["at"]["admissibility"],
                "alignment": (episode["at"]["alignment"] or {}).get("verdict"),
                "violated_preconditions": [
                    p.get("name") for p in episode["at"]["preconditions"]
                    if p.get("status") == "viol"
                ],
                "decision_boundary": {
                    "commit": episode["at"]["decision_boundary"]["commit_action"],
                    "unresolved_count": len(episode["at"]["decision_boundary"][
                        "unresolved_requirements"
                    ]),
                    "completion_claim_slots": episode["at"]["decision_boundary"][
                        "completion_claim_slots"
                    ],
                    "authorization_slot": episode["at"]["decision_boundary"][
                        "authorization_slot"
                    ],
                },
            },
            "history": {
                "exact_repeat_steps": history["prior_exact_steps"],
                "near_duplicate_steps": history["near_duplicate_steps"],
                "schema_count": len(history["prior_schema_steps"]),
                "invalid_steps": budget["invalid_steps"],
                "no_effect_steps": budget["no_effect_steps"],
                "productive_count": len(budget["productive_steps"]),
                "unproductive_count": len(budget["unproductive_steps"]),
                "recent_unproductive_steps": budget["unproductive_steps"][-5:],
            },
            "available_target_kinds": episode["before"]["available_targets"][
                "counts_by_kind"
            ],
            "outcome": {
                **after["budget_effect"],
                "new_task_evidence_fids": after["new_task_evidence_fids"],
                "task_decisions": after["task_decisions"],
                "observed_products": products,
                "observed_product_count": product_count,
            },
        })
    return {
        "schema": "trajectory-scan/v1",
        "contract": requirements["task_contract"],
        "requirements": [
            {
                "requirement": x["entity"]["id"],
                "constraints": [
                    {
                        "attribute": c["attribute"],
                        "operator": c["operator"],
                        "expected": c["expected"],
                        "mode": c["mode"],
                    }
                    for c in x["definition"]["constraints"]
                ],
                "terminal_state": x["status"]["state"],
                "status_reason": x["status"]["reason"],
            }
            for x in requirements["requirements"]
        ],
        "episodes": rows,
    }


def _summarize_scan(data: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(data)
    for episode in out.get("episodes", []):
        for module in ("memory", "reflection", "plan", "action"):
            if isinstance(episode.get(module), dict):
                episode[module]["text"] = episode[module].get("text", "")[:160]
                episode[module].pop("support", None)
        coverage = episode.get("memory", {}).get("exploration_coverage", {})
        coverage["omitted_examples"] = coverage.get("omitted_examples", [])[-3:]
        products = episode.get("outcome", {}).get("observed_products", [])
        episode.get("outcome", {})["observed_products"] = products[:2]
    return out


def _contract_feasibility(R: Record) -> Dict[str, Any]:
    """Expose conservative lexical tensions; never decide infeasibility."""
    values = []
    for goal in R.goals:
        for constraint in _constraints(goal):
            values.append({
                "requirement": str(goal.entity()),
                "text": _norm(constraint["expected"]),
                "span": constraint.get("source_span", ""),
            })
    opposite_pairs = (
        ({"men", "mens", "male"}, {"women", "womens", "female"}),
    )
    tensions = []
    for i, left in enumerate(values):
        left_words = set(re.findall(r"[a-z0-9]+", left["text"]))
        for right in values[i + 1:]:
            right_words = set(re.findall(r"[a-z0-9]+", right["text"]))
            for group_a, group_b in opposite_pairs:
                if ((left_words & group_a and right_words & group_b)
                        or (left_words & group_b and right_words & group_a)):
                    tensions.append({
                        "left_requirement": left["requirement"],
                        "right_requirement": right["requirement"],
                        "left_text": left["text"],
                        "right_text": right["text"],
                        "kind": "lexical_category_tension",
                    })
    return {
        "status": "needs_review" if tensions else "not_disproven",
        "tensions": tensions,
        "limitation": "Lexical tension is a review cue, not proof that the environment cannot satisfy the contract.",
    }


def _candidate_view(R: Record) -> Dict[str, Any]:
    """High-recall repair-point enumeration without causal ranking."""
    episodes = _episodes_view(R)["episodes"]
    facts = {f.fid: f for f in R.F}
    consumers: Dict[int, List[int]] = {}
    for fact in R.F:
        for parent in fact.basis:
            consumers.setdefault(parent, []).append(fact.fid)

    future_targets: Dict[str, List[Tuple[int, int]]] = {}
    for fact in R.F:
        if fact.slot != "act":
            continue
        for entity in fact.args:
            if entity.kind in ("agent", "value", "page", "attribute"):
                continue
            future_targets.setdefault(str(entity), []).append((fact.t, fact.fid))

    candidates: List[Dict[str, Any]] = []
    seen = set()

    def add(fid: int, module: str, trigger: str, evidence_for: Iterable[int],
            repair: str, evidence_against: Iterable[int] = (),
            features: Optional[Dict[str, Any]] = None) -> None:
        key = (fid, module, trigger)
        if fid not in facts or key in seen:
            return
        seen.add(key)
        fact = facts[fid]
        candidates.append({
            "candidate_id": f"cand:{fid}:{trigger}",
            "fid": fid,
            "step": fact.t,
            "module": module,
            "trigger": trigger,
            "proposition": fact.span[:240],
            "evidence_for": sorted(set(int(x) for x in evidence_for if x in facts)),
            "evidence_against": sorted(set(int(x) for x in evidence_against if x in facts)),
            "direct_consumers": consumers.get(fid, [])[:12],
            "local_repair": repair,
            "features": features or {},
            "ranking_status": "unranked",
        })

    negative_candidate_claim = re.compile(
        r"\b(?:no|none|not)\b.{0,50}\b(?:relevant|match|matching|suitable|qualif)"
    )
    acknowledged_strategy_failure = re.compile(
        r"\b(?:not (?:the most )?effective|ineffective|failed to yield|"
        r"(?:has|have|had|did) not yield|no (?:useful |relevant )?(?:result|results|"
        r"progress)|progress (?:is |has )?(?:stalled|limited)|lack of relevant)\b"
    )
    enumeration_commitment = re.compile(
        r"\b(?:systematic(?:ally)?|sequential(?:ly)?|one by one|all (?:remaining )?|"
        r"each (?:remaining )?|continue (?:to )?(?:check|search|inspect|examine)|"
        r"proceed (?:to|through) (?:the )?(?:next|remaining))\b"
    )
    completion_authorization = re.compile(
        r"\b(?:progress (?:is|was) (?:complete|completed|done)|"
        r"(?:task|goal) (?:is|was|has been) (?:complete|completed|done)|"
        r"no issues? (?:were |was |have been )?(?:encountered|found)|"
        r"already (?:identified|found|selected)|ready to (?:buy|purchase|submit|finish))\b"
    )
    prior_products: List[Dict[str, Any]] = []
    for episode in episodes:
        step = episode["step"]
        modules = episode["at"]["modules"]
        analysis = episode["at"]["module_analysis"]
        signals = set(episode["signals"])

        for module in ("memory", "reflection"):
            violated = [row for row in modules[module]
                        if row.get("support", {}).get("verdict") == "viol"]
            if violated:
                primary = next((row for row in modules[module]
                                if row.get("relation") in ("recalled_summary", "assessed")),
                               violated[0])
                cites = [fid for row in violated
                         for fid in row.get("support", {}).get("cites", [])]
                add(primary["fid"], module, "contradicted_claim", cites,
                    "Replace the contradicted claim with the recorded state.",
                    features={"violated_fact_fids": [row["fid"] for row in violated]})
            dismissals = [row for row in modules[module]
                          if negative_candidate_claim.search(_norm(
                              facts[row["fid"]].span if row["fid"] in facts
                              else row.get("span", "")
                          ))]
            partial = [p for p in prior_products
                       if p.get("observed", {}).get("title") is not None]
            if dismissals and partial:
                product_fids = [fid for p in partial for fid in p.get("fids", [])]
                add(dismissals[0]["fid"], module, "candidate_dismissal",
                    [dismissals[0]["fid"], *product_fids],
                    "Preserve partially relevant candidates until their unresolved constraints are checked.",
                    features={"visible_product_count": len(partial)})

        memory_fids = analysis["memory"]["fact_ids"]
        coverage = analysis["memory"].get("exploration_coverage", {})
        if memory_fids and coverage.get("omitted_targets"):
            revisits = []
            for target in coverage["omitted_targets"]:
                revisits.extend(fid for t, fid in future_targets.get(target, []) if t > step)
            if revisits:
                add(memory_fids[0], "memory", "coverage_loss",
                    [memory_fids[0], *revisits],
                    "Retain the explored-target set so later planning avoids revisits.",
                    features={
                        "omitted_targets": coverage["omitted_targets"],
                        "later_revisit_fids": sorted(set(revisits)),
                    })

        reflection_fids = analysis["reflection"]["fact_ids"]
        if "contradicted_completion" in signals and reflection_fids:
            add(reflection_fids[0], "reflection", "premature_completion",
                [reflection_fids[0], *episode["before"]["candidate_fids"]],
                "Keep the task incomplete until every live requirement is verified.")

        # A commit may be authorized by looser completion language that the
        # structured claim parser cannot safely normalize.  Keep the raw-span
        # cue, but require both a commit and unresolved task requirements.
        boundary = episode["at"]["decision_boundary"]
        reflection_text = "\n".join(
            facts[fid].span for fid in reflection_fids if fid in facts
        )
        if (reflection_fids and boundary["commit_action"]
                and boundary["unresolved_requirements"]
                and completion_authorization.search(_norm(reflection_text))):
            add(reflection_fids[0], "reflection", "premature_completion",
                [reflection_fids[0], *episode["before"]["candidate_fids"]],
                "Keep the task incomplete until every live requirement is verified.",
                features={"unresolved_requirements": boundary["unresolved_requirements"]})

        plan = episode["at"]["plan"]
        action = episode["at"]["action"]
        if plan is not None:
            plan_text = facts[plan["fid"]].span if plan["fid"] in facts else plan.get("span", "")
            context_text = "\n".join(x for x in (plan_text, reflection_text) if x)
            if acknowledged_strategy_failure.search(_norm(context_text)):
                add(plan["fid"], "plan", "acknowledged_strategy_failure",
                    [plan["fid"], *reflection_fids],
                    "Change strategy when the agent's own assessment says the current one is not producing useful evidence.",
                    features={
                        "prior_schema_steps": episode["before"]["action_history"]["prior_schema_steps"],
                        "prior_unproductive_steps": episode["before"]["budget_history"]["unproductive_steps"],
                    })
            target_counts = episode["before"]["available_targets"].get("counts_by_kind", {})
            enumerable_targets = sum(
                count for kind, count in target_counts.items()
                if kind not in {"agent", "value", "page", "attribute"}
            )
            if (enumeration_commitment.search(_norm(plan_text))
                    and enumerable_targets > 1):
                add(plan["fid"], "plan", "broad_enumeration_commitment",
                    [plan["fid"]],
                    "Compare the expected information gain of continued enumeration with a different target class or strategy.",
                    features={
                        "available_target_counts": target_counts,
                        "prior_schema_steps": episode["before"]["action_history"]["prior_schema_steps"],
                    })
            substitutions = [x for x in episode["task_links"]
                             if x.get("relation") == "substitutes"]
            if substitutions:
                add(plan["fid"], "plan", "constraint_substitution",
                    [plan["fid"], *[fid for x in substitutions
                                     for fid in x.get("evidence_fids", [])]],
                    "Restore the requested value instead of substituting another constraint.",
                    features={"requirements": [x.get("requirement") for x in substitutions]})
            history = episode["before"]["action_history"]
            budget = episode["before"]["budget_history"]
            if history["near_duplicate_steps"] and budget["unproductive_steps"]:
                prior_action_fids = [f.fid for f in R.F
                                     if f.slot == "act"
                                     and f.t in history["near_duplicate_steps"]]
                add(plan["fid"], "plan", "repeated_low_yield_strategy",
                    [plan["fid"], *prior_action_fids],
                    "Choose a materially different information-gathering strategy.",
                    features={
                        "near_duplicate_steps": history["near_duplicate_steps"],
                        "prior_unproductive_steps": budget["unproductive_steps"],
                    })
            violated = [x for x in episode["at"]["preconditions"]
                        if x.get("status") == "viol"]
            if violated:
                add(plan["fid"], "plan", "failed_precondition",
                    [plan["fid"], *[fid for x in violated
                                     for fid in x.get("evidence_fids", [])]],
                    "Select an intention whose prerequisites hold first.",
                    features={"preconditions": [x.get("name") for x in violated]})

        if action is not None:
            if "invalid_action" in signals:
                add(action["fid"], "action", "invalid_action",
                    [action["fid"]],
                    "Emit one admissible environment command in the required syntax.")
            if "alignment_violation" in signals:
                add(action["fid"], "action", "plan_action_mismatch",
                    [action["fid"], *([] if plan is None else [plan["fid"]])],
                    "Execute the supported bound plan or explicitly revise it first.")
            if "wrong_task_decision" in signals:
                decision_fids = [x["fid"] for x in episode["after"]["task_decisions"]]
                add(action["fid"], "action", "wrong_task_decision",
                    [action["fid"], *decision_fids],
                    "Choose a value or object that satisfies every live constraint.")

        prior_products = episode["after"].get("observed_products", [])

    feasibility = _contract_feasibility(R)
    task_facts = [f for f in R.F if f.side == "task"]
    if feasibility["tensions"] and task_facts:
        add(task_facts[0].fid, "system", "contract_feasibility_tension",
            [task_facts[0].fid],
            "Clarify or relax the conflicting contract before attributing failure to the agent.",
            features={"tensions": feasibility["tensions"]})
        if candidates and candidates[-1]["fid"] == task_facts[0].fid:
            candidates[-1]["step"] = max(1, candidates[-1]["step"])

    last_step = R.length()
    terminal = [f for f in R.by_step(last_step)
                if f.slot == "chk" and f.rel == "admissibility"]
    if terminal and not any(R.commits(f) for f in R.by_step(last_step)
                            if f.slot == "act"):
        add(terminal[-1].fid, "system", "terminal_cutoff",
            [terminal[-1].fid],
            "Extend the budget only if no earlier agent candidate has greater causal leverage.")

    candidates.sort(key=lambda x: (x["step"], x["fid"], x["trigger"]))
    return {"schema": "repair-candidates/v1", "candidates": candidates}


def _candidate_families_view(R: Record) -> Dict[str, Any]:
    candidates = _candidate_view(R)["candidates"]
    by_signature: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for candidate in candidates:
        by_signature.setdefault(
            (candidate["module"], candidate["trigger"]), []
        ).append(candidate)
    candidate_families = []
    for (module, trigger), occurrences in sorted(by_signature.items()):
        occurrences.sort(key=lambda x: (x["step"], x["fid"]))
        unique_steps = sorted({x["step"] for x in occurrences})
        candidate_families.append({
            "family_id": f"family:{module}:{trigger}",
            "module": module,
            "trigger": trigger,
            "occurrence_steps": unique_steps,
            "occurrence_fids": [x["fid"] for x in occurrences],
            "first_step": unique_steps[0],
            "last_step": unique_steps[-1],
            "evidence_for": sorted({fid for x in occurrences
                                    for fid in x["evidence_for"]})[:20],
            "evidence_against": sorted({fid for x in occurrences
                                        for fid in x["evidence_against"]})[:8],
            "direct_consumers": sorted({fid for x in occurrences
                                         for fid in x["direct_consumers"]})[:16],
            "local_repair": occurrences[0]["local_repair"],
            "ranking_status": "unranked",
            "selection_warning": (
                "Occurrence steps are retrieval points, not equivalent errors. "
                "Resolve the annotated repair frontier from temporal context."
            ),
        })
    candidate_families.sort(key=lambda x: (
        x["first_step"], x["module"], x["trigger"]
    ))
    return {
        "schema": "candidate-families/v1",
        "families": candidate_families,
        "warning": (
            "Optional second-pass hints only. Form an independent global and "
            "structure-guided candidate before reading these families."
        ),
    }


def _local_audits_view(R: Record) -> Dict[str, Any]:
    """Lossless-in-record module contexts for local judgement at every step.

    This view does not label errors.  It exposes what each module could consume,
    what it emitted, and what happened next, so an LLM can perform AgentDebug-
    style local analysis without reading raw trajectory files or confusing a
    detector trigger with a verdict.
    """
    episodes = _episodes_view(R)["episodes"]

    rows = []
    for episode in episodes:
        step = episode["step"]
        rich = build_rich_step_context(R, episode)
        current_observation = [
            _fact(R, fact, with_support=True)
            for fact in R.by_step(step) if fact.side == "env"
        ]
        modules = episode["at"]["modules"]
        analysis = episode["at"]["module_analysis"]

        def module_row(module: str, input_refs: List[str],
                       validation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            return {
                "outputs": modules[module],
                "input_refs": input_refs,
                "review_context": rich["review_packets"][module],
                "derived_checks": {
                    "contract_coverage": analysis[module]["contract_coverage"],
                    "claims": analysis[module].get("claims"),
                    "exploration_coverage": analysis[module].get("exploration_coverage"),
                    "validation": validation,
                },
                "retrieval_hints": [],
                "judgement": "not_computed",
            }

        rows.append({
            "step": step,
            "step_summary": rich["step_summary"],
            "transition": rich["transition"],
            "recent_history": rich["recent_history"],
            "shared_context": {
                "current_observation": current_observation,
                "task_state": episode["before"]["task_state"],
                "available_targets": episode["before"]["available_targets"],
                "prior_action_history": episode["before"]["action_history"],
                "prior_budget_history": episode["before"]["budget_history"],
                "intention": episode["at"]["intention"],
                "preconditions": episode["at"]["preconditions"],
            },
            "modules": {
                "memory": module_row("memory", [
                    "shared_context.current_observation",
                    "shared_context.prior_action_history",
                    "shared_context.prior_budget_history",
                ]),
                "reflection": module_row("reflection", [
                    "shared_context.current_observation", "modules.memory.outputs",
                ]),
                "plan": module_row("plan", [
                    "shared_context.task_state", "shared_context.available_targets",
                    "modules.memory.outputs", "modules.reflection.outputs",
                ]),
                "action": module_row("action", [
                    "modules.plan.outputs", "shared_context.intention",
                    "shared_context.preconditions",
                ], validation={
                    "admissibility": episode["at"]["admissibility"],
                    "alignment": episode["at"]["alignment"],
                    "expected_effects": episode["after"]["expected_effects"],
                }),
            },
            "next_outcome": episode["after"],
            "routing_signals": episode["signals"],
        })
    return {
        "schema": "local-module-audits/v2",
        "task_contract": R.task,
        "domain_briefing": getattr(R, "domain_briefing", {}),
        "local_review_contract": {
            "unit": "one module output at one step",
            "input_order": ["task", "observation/history", "same-step upstream modules"],
            "feedback": "the next environment response and recorded state delta",
            "output": "a separate local-judgement/v2 assessment",
            "boundary": (
                "Local review decides whether the module output is correct, erroneous, "
                "uncertain, or absent. It does not rank global causal importance."
            ),
        },
        "coverage": {
            "steps": [1, R.length()],
            "step_count": R.length(),
            "modules_per_step": ["memory", "reflection", "plan", "action"],
            "module_outputs": "all compiled facts, untruncated in full detail",
            "observations": "all compiled environment facts at each step",
            "boundary": (
                "complete over the compiled record, not proof that the parser "
                "captured every semantic detail in the raw trajectory"
            ),
        },
        "audits": rows,
    }


def _packet_view(R: Record) -> Dict[str, Any]:
    scan = _summarize_scan(_scan_view(R))
    candidates = _candidate_view(R)["candidates"]
    candidate_families = _candidate_families_view(R)["families"]
    return {
        "schema": "debug-packet/v3",
        "trajectory": R.tid,
        "contract": scan["contract"],
        "requirements": scan["requirements"],
        "feasibility": _contract_feasibility(R),
        "linear_scan": scan["episodes"],
        # Detector proposals are intentionally not injected into the global
        # read.  They are available on demand after an independent candidate
        # has been formed; otherwise frequent cues anchor the localizer.
        "candidate_family_count": len(candidate_families),
        "candidate_families_query": "view \"candidate_families\"",
        "candidate_detail_count": len(candidates),
        "candidate_details_query": "view \"candidates | steps:<step>:<step>\"",
        "complete_local_audits_query": (
            "page with view \"local_audits | steps:<a>:<b> | detail:summary\" "
            "until coverage spans all steps"
        ),
        "exact_local_audit_query": "view \"local_audits | steps:<step>:<step> | detail:full\"",
        "supported_reasoning_routes": {
            "default_local_first": (
                "complete local module judgements, form step-module candidates, "
                "then perform global causal selection"
            ),
            "global_challenger": (
                "use global/structural reading to challenge locally generated "
                "tuples, never to bypass local coverage"
            ),
            "hybrid": (
                "interleave global and paged local reads while preserving full "
                "local-module coverage before submission"
            ),
        },
        "ranking_contract": {
            "target": "the earliest new mistake that put the run on its unrecovered failure chain",
            "conditions": [
                "the selected fact's own content is unsupported or dominated at that time",
                "an unmet requirement causally reaches the fact",
                "nothing later repairs the relevant failure chain",
                "correcting this fact alone would plausibly redirect the run toward success",
            ],
            "generator_role": "optional_second-pass_hints_only",
            "reasoner_role": (
                "locally_judge_modules_then_globally_select_and_lock_one_step_module_tuple"
            ),
            "warning": (
                "Early exploration is normal unless the step contains a clear "
                "fundamental commitment; detector hints are not visible until requested."
            ),
        },
    }


def _parse_program(spec: str) -> Tuple[str, Dict[str, Any]]:
    stages = [x.strip() for x in spec.split("|") if x.strip()]
    if not stages:
        raise ValueError("view needs a source")
    source = stages[0]
    options: Dict[str, Any] = {}
    for stage in stages[1:]:
        key, sep, raw = stage.partition(":")
        if not sep:
            raise ValueError(f"view stage {stage!r} must be key:value")
        key = key.strip()
        raw = raw.strip()
        if key in ("section", "event", "support", "slot", "qualification", "signal",
                   "commitment", "binding", "alignment", "precondition",
                   "effect", "constraint_relation"):
            options[key] = [x.strip() for x in raw.split(",") if x.strip()]
        elif key == "state":
            if raw not in ("satisfied", "unmet", "unknown", "unresolved"):
                raise ValueError("state must be satisfied, unmet, unknown, or unresolved")
            options[key] = raw
        elif key == "steps":
            lo, hi = raw.split(":", 1)
            options[key] = (int(lo), int(hi))
        elif key == "limit":
            options[key] = int(raw)
        elif key in ("entity", "purpose", "constraint"):
            options[key] = raw
        elif key == "mention":
            if raw not in ("present", "missing"):
                raise ValueError("mention must be present or missing")
            options[key] = raw
        elif key in ("relation", "side"):
            options[key] = [x.strip() for x in raw.split(",") if x.strip()]
        elif key == "detail":
            if raw not in ("summary", "full"):
                raise ValueError("detail must be summary or full")
            options[key] = raw
        else:
            raise KeyError(f"unknown view stage {key!r}; known: "
                           "section,event,support,slot,qualification,state,steps,"
                           "limit,detail,entity,relation,side,purpose,constraint,mention,"
                           "commitment,binding,alignment,precondition,effect,constraint_relation,signal")
    return source, options


def _project(data: Dict[str, Any], sections: List[str]) -> Dict[str, Any]:
    if "requirements" in data:
        available = set(data["requirements"][0]) - {"entity"} if data["requirements"] else set()
        unknown = set(sections) - available
        if unknown:
            raise KeyError(f"unknown requirement section(s) {sorted(unknown)}; "
                           f"available: {sorted(available)}")
        projected = {
            "schema": data["schema"],
            "requirements": [
                {"entity": row["entity"], **{key: row[key] for key in sections}}
                for row in data["requirements"]
            ],
        }
        if "task_contract" in data:
            projected["task_contract"] = data["task_contract"]
        return projected
    if "episodes" in data:
        available = (set(data["episodes"][0]) - {"step"}
                     if data["episodes"] else
                     {"signals", "task_links", "before", "at", "after"})
        unknown = set(sections) - available
        if unknown:
            raise KeyError(f"unknown episode section(s) {sorted(unknown)}; "
                           f"available: {sorted(available)}")
        return {
            "schema": data["schema"],
            "episodes": [
                {"step": row["step"], **{key: row[key] for key in sections}}
                for row in data["episodes"]
            ],
        }
    allowed = set(data) - {"schema", "entity"}
    unknown = set(sections) - allowed
    if unknown:
        raise KeyError(f"unknown section(s) {sorted(unknown)}; available: {sorted(allowed)}")
    out = {"schema": data["schema"]}
    if "entity" in data:
        out["entity"] = data["entity"]
    for key in sections:
        out[key] = data[key]
    return out


def _row_step(row: Dict[str, Any]) -> Optional[int]:
    if isinstance(row.get("step"), int):
        return row["step"]
    fact = row.get("fact")
    if isinstance(fact, dict) and isinstance(fact.get("step"), int):
        return fact["step"]
    click = row.get("click")
    if isinstance(click, dict) and isinstance(click.get("step"), int):
        return click["step"]
    return None


def _filter_rows(data: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(data)
    if "requirements" in out and "state" in options:
        out["requirements"] = [x for x in out["requirements"]
                               if x["status"]["state"] == options["state"]]

    list_keys = ("evidence", "value_changes", "intentions", "changes", "page_visits",
                 "episodes", "candidates", "audits", "notes")
    for key in list_keys:
        rows = out.get(key)
        if not isinstance(rows, list):
            continue
        if "steps" in options:
            lo, hi = options["steps"]
            rows = [x for x in rows if _row_step(x) is not None
                    and lo <= _row_step(x) <= hi]
        if key in ("evidence", "changes") and "event" in options:
            rows = [x for x in rows if x.get("event") in options["event"]]
        if key == "evidence" and "slot" in options:
            rows = [x for x in rows if x.get("fact", {}).get("slot") in options["slot"]]
        if key == "evidence" and "support" in options:
            rows = [x for x in rows
                    if x.get("fact", {}).get("support", {}).get("verdict")
                    in options["support"]]
        if key == "evidence" and "qualification" in options:
            narrowed = []
            for row in rows:
                values = row.get("values")
                if not isinstance(values, list):
                    continue
                row["values"] = [x for x in values
                                 if x.get("qualification") in options["qualification"]]
                if row["values"]:
                    narrowed.append(row)
            rows = narrowed
        if key == "intentions" and "constraint" in options:
            narrowed = []
            for row in rows:
                mentions = [x for x in row.get("constraint_mentions", [])
                            if x.get("requirement") == options["constraint"]]
                row["constraint_mentions"] = mentions
                validation = row.get("validation", {})
                if isinstance(validation.get("constraint_bindings"), list):
                    validation["constraint_bindings"] = [
                        x for x in validation["constraint_bindings"]
                        if x.get("requirement") == options["constraint"]
                    ]
                if mentions:
                    narrowed.append(row)
            rows = narrowed
        if key == "intentions" and "mention" in options:
            want_present = options["mention"] == "present"
            rows = [x for x in rows if any(
                bool(m.get("in_plan") or m.get("in_action")) == want_present
                for m in x.get("constraint_mentions", []))]
        if key == "intentions" and "commitment" in options:
            rows = [x for x in rows
                    if x.get("intention", {}).get("commitment") in options["commitment"]]
        if key == "intentions" and "binding" in options:
            rows = [x for x in rows
                    if x.get("intention", {}).get("binding") in options["binding"]]
        if key == "intentions" and "alignment" in options:
            rows = [x for x in rows
                    if x.get("validation", {}).get("plan_action_alignment", {}).get("verdict")
                    in options["alignment"]]
        if key == "intentions" and "precondition" in options:
            rows = [x for x in rows if any(
                p.get("status") in options["precondition"]
                for p in x.get("validation", {}).get("preconditions", []))]
        if key == "intentions" and "effect" in options:
            rows = [x for x in rows if any(
                e.get("status") in options["effect"]
                for e in x.get("validation", {}).get("expected_effects", []))]
        if key == "intentions" and "constraint_relation" in options:
            rows = [x for x in rows if any(
                b.get("relation") in options["constraint_relation"]
                for b in x.get("validation", {}).get("constraint_bindings", []))]
        if key == "episodes" and "signal" in options:
            rows = [x for x in rows
                    if any(s in options["signal"] for s in x.get("signals", []))]
        if key == "episodes" and "constraint" in options:
            rows = [x for x in rows if any(
                b.get("requirement") == options["constraint"]
                for b in x.get("task_links", []))]
        if "entity" in options:
            rows = [x for x in rows if options["entity"] in x.get("entities", [])
                    or options["entity"] in x.get("arguments", [])
                    or options["entity"] in x.get("fact", {}).get("arguments", [])]
        if "relation" in options:
            rows = [x for x in rows if x.get("relation", x.get("fact", {}).get("relation"))
                    in options["relation"]]
        if "side" in options:
            rows = [x for x in rows if x.get("source", {}).get("side",
                                                               x.get("fact", {}).get("side"))
                    in options["side"]]
        if "purpose" in options:
            rows = [x for x in rows if x.get("purpose", x.get("fact", {}).get("purpose"))
                    == options["purpose"]]
        if "limit" in options:
            rows = rows[:max(0, options["limit"])]
        out[key] = rows

    if isinstance(out.get("audits"), list) and isinstance(out.get("coverage"), dict):
        returned_steps = [row["step"] for row in out["audits"]]
        out["coverage"]["returned_steps"] = returned_steps
        out["coverage"]["returned_step_count"] = len(returned_steps)
        out["coverage"]["whole_trajectory_complete"] = (
            returned_steps == list(range(1, out["coverage"].get("step_count", 0) + 1))
        )
        if "steps" in options:
            lo, hi = options["steps"]
            expected = list(range(
                max(1, lo), min(hi, out["coverage"]["step_count"]) + 1
            ))
            out["coverage"]["requested_range_complete"] = returned_steps == expected

    if options.get("detail") == "summary":
        if isinstance(out.get("notes"), list):
            out["notes"] = [compact_process_note(note) for note in out["notes"]]
        for event in out.get("evidence", []):
            event.pop("context", None)
            values = event.get("values")
            if isinstance(values, list):
                counts = Counter(str(value.get("qualification") or "unknown")
                                 for value in values)
                informative = [value for value in values
                               if value.get("qualification") in ("yes", "partial", "unknown")]
                nonmatches = [value for value in values
                              if value.get("qualification") == "no"]
                retained = (informative + nonmatches[:2])[:6]
                event["value_summary"] = {
                    "counts_by_qualification": dict(sorted(counts.items())),
                    "returned": len(retained),
                    "total": len(values),
                    "truncated": len(retained) < len(values),
                }
                event["values"] = retained
        for intent in out.get("intentions", []):
            if intent.get("plan"):
                intent["plan"] = {k: intent["plan"][k]
                                  for k in ("fid", "step", "slot", "span")}
            if intent.get("action"):
                intent["action"] = {k: intent["action"][k]
                                    for k in ("fid", "step", "slot", "span")}
            validation = intent.get("validation")
            if isinstance(validation, dict):
                validation["constraint_bindings"] = [
                    {k: binding.get(k) for k in
                     ("requirement", "relation", "explicit_in_plan",
                      "explicit_in_action", "recorded_met_before_or_at_step",
                      "evidence_fids")}
                    for binding in validation.get("constraint_bindings", [])
                ]
        for episode in out.get("episodes", []):
            if out.get("schema") == "trajectory-scan/v1":
                for module in ("memory", "reflection", "plan", "action"):
                    if isinstance(episode.get(module), dict):
                        episode[module]["text"] = episode[module].get("text", "")[:160]
                        episode[module].pop("support", None)
                coverage = episode.get("memory", {}).get("exploration_coverage", {})
                coverage["omitted_examples"] = coverage.get("omitted_examples", [])[-3:]
                products = episode.get("outcome", {}).get("observed_products", [])
                episode.get("outcome", {})["observed_products"] = products[:2]
                continue
            observation = episode.get("after", {}).get("observation", [])
            episode.get("after", {})["observation"] = [
                {k: row.get(k) for k in ("fid", "step", "relation", "arguments",
                                         "value", "purpose", "span")}
                for row in observation[:12]
            ]
        for audit in out.get("audits", []):
            summarized = summarize_rich_context({
                "step_summary": audit.get("step_summary"),
                "transition": audit.get("transition", {}),
                "recent_history": audit.get("recent_history", []),
                "review_packets": {
                    name: module.get("review_context", {})
                    for name, module in audit.get("modules", {}).items()
                },
            })
            audit["step_summary"] = summarized["step_summary"]
            audit["transition"] = summarized["transition"]
            audit["recent_history"] = summarized["recent_history"]
            for name, module in audit.get("modules", {}).items():
                if name in summarized["review_packets"]:
                    module["review_context"] = summarized["review_packets"][name]
                module["outputs"] = [
                    {k: fact.get(k) for k in ("fid", "span")}
                    for fact in module.get("outputs", [])
                ]
                for fact in module["outputs"]:
                    fact["span"] = str(fact.get("span") or "")[:180]
                checks = module.get("derived_checks", {})
                coverage = checks.get("contract_coverage") or {}
                exploration = checks.get("exploration_coverage") or {}
                module["derived_checks"] = {
                    "claims": checks.get("claims"),
                    "contract_mentioned_count": len(coverage.get("mentioned", [])),
                    "contract_not_literal_count": len(coverage.get("not_literal", [])),
                    "exploration_prior_count": len(exploration.get("prior_targets", [])),
                    "exploration_omitted_count": len(exploration.get("omitted_targets", [])),
                    "validation": checks.get("validation"),
                }
                module["retrieval_hints"] = [
                    {"trigger": hint.get("trigger"), "fid": hint.get("fid")}
                    for hint in module.get("retrieval_hints", [])
                ]
            shared = audit.get("shared_context", {})
            if isinstance(shared.get("current_observation"), list):
                shared["current_observation"] = [
                    {"fid": fact.get("fid"),
                     "span": str(fact.get("span") or "")[:160]}
                    for fact in shared["current_observation"][:8]
                ]
            shared["task_state"] = [
                {"requirement": row.get("requirement"), "state": row.get("state")}
                for row in shared.get("task_state", [])
            ]
            shared["available_targets"] = (
                shared.get("available_targets", {}).get("counts_by_kind", {})
            )
            for history_key in ("prior_action_history", "prior_budget_history"):
                history = shared.get(history_key, {})
                shared[history_key] = {
                    key: (len(value) if isinstance(value, list) else value)
                    for key, value in history.items()
                }
            intention = shared.get("intention") or {}
            shared["intention"] = {
                key: intention.get(key) for key in
                ("entity", "verb", "objects", "commitment", "binding", "serves")
                if key in intention
            }
            shared["preconditions"] = [
                {"name": row.get("name"), "status": row.get("status")}
                for row in shared.get("preconditions", [])
            ]
            outcome = audit.get("next_outcome", {})
            audit["next_outcome"] = {
                "observation": [
                    {"fid": fact.get("fid"),
                     "span": str(fact.get("span") or "")[:160]}
                    for fact in outcome.get("observation", [])[:8]
                ],
                "changes": outcome.get("changes", [])[:8],
                "new_task_evidence_fids": outcome.get("new_task_evidence_fids", []),
                "task_decisions": outcome.get("task_decisions", []),
                "budget_effect": outcome.get("budget_effect"),
                "observed_products": outcome.get("observed_products", [])[:3],
            }
        for visit in out.get("page_visits", []):
            offered = visit.get("offered_assignments")
            if isinstance(offered, dict):
                visit["offered_counts"] = {
                    name: len(rows) for name, rows in sorted(offered.items())
                }
                visit["offered_evidence_fids"] = [
                    row["fid"]
                    for rows in offered.values()
                    for row in rows
                    if isinstance(row.get("fid"), int)
                ]
                visit.pop("offered_assignments", None)
    return out


def program_view(R: Record, spec: str) -> Dict[str, Any]:
    """Execute a small declarative view program.

    Grammar::

        <source> [| <stage>:<value>]...

        source := entity:<kind>:<index> | requirements | changes | assignments |
                  intentions | episodes | scan | local_audits | candidates |
                  candidate_families | process_notes | packet
        stage  := section | state | steps | event | support | slot |
                  qualification | entity | relation | side | purpose |
                  constraint | mention | commitment | binding | alignment |
                  precondition | effect | constraint_relation | signal | limit | detail

    The language can select, filter, and project evidence.  It cannot execute
    arbitrary code or manufacture new facts.
    """
    source, options = _parse_program(spec)
    parts = source.split(":", 2)
    if parts[0] == "entity" and len(parts) == 3:
        data = entity_view(R, parts[1], parts[2])
    elif source == "requirements":
        data = _requirements_view(R)
    elif source == "changes":
        data = _all_changes(R)
    elif source == "assignments":
        data = _all_changes(R, include_initial=True)
    elif source == "intentions":
        data = _intentions_view(R)
    elif source == "episodes":
        data = _episodes_view(R)
    elif source == "scan":
        data = _scan_view(R)
    elif source == "local_audits":
        data = _local_audits_view(R)
    elif source == "candidates":
        data = _candidate_view(R)
    elif source == "candidate_families":
        data = _candidate_families_view(R)
    elif source == "process_notes":
        data = {"schema": "process-notes/v1",
                "notes": [R.process_notes[step] for step in sorted(R.process_notes)]}
    elif source == "packet":
        data = _packet_view(R)
    else:
        raise KeyError("view source must be entity:<kind>:<index>, requirements, "
                       "changes, assignments, intentions, episodes, scan, local_audits, candidates, "
                       "candidate_families, process_notes, or packet")
    data = _filter_rows(data, options)
    if "section" in options:
        data = _project(data, options["section"])
    data["query"] = spec
    answer = build_query_answer(R, spec, data)
    return {"query_answer": answer, **data}
