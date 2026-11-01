"""Seven abstract rules over existing Facts; no text or intention parsing."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json

if __package__:
    from .dsl import Fact, Rule, UNBOUND
else:
    from dsl import Fact, Rule, UNBOUND


MODULE_RELATIONS = {"recalled_summary", "recalled_nothing", "memory_unparsed", "planned", "assessed"}


def definition(id, scope, trigger, property, requirements, exceptions=()):
    return Rule(id, {"fact": "Fact"}, scope, trigger, "fact_in_scope", property,
                tuple(requirements), tuple(exceptions), "fact_witness")


RULES = (
    definition("assertion.consistency/v1", "prefix", "memory_claims", "consistent_assertion",
               ("typed_fact", "observation_history"), ("historical_time_ambiguity",)),
    definition("assertion.grounding/v1", "prefix", "memory_claims", "grounded_assertion",
               ("typed_fact", "observation_history")),
    definition("information.update/v1", "prefix", "memory_claims", "updated_information",
               ("typed_fact", "observation_history")),
    definition("decision.applicability/v1", "step", "actions", "admitted_decision",
               ("action_contract",)),
    definition("execution.result/v1", "response", "actions", "consistent_result",
               ("linked_feedback",)),
    definition("completion.consistency/v1", "prefix", "completion_claims", "consistent_completion",
               ("typed_fact", "task_goals")),
    definition("progress.efficiency/v1", "response", "actions", "observable_progress",
               ("linked_feedback",), ("retry_exception_unavailable",)),
)


def memory_claims(record):
    return [f for f in record.F if f.slot == "mem" and f.side == "mind"
            and f.rel not in MODULE_RELATIONS]


def actions(record):
    return [f for f in record.F if f.slot == "act"]


def completion_claims(record):
    return [f for f in memory_claims(record) if f.rel == "satisfied" and f.value is True]


def fact_in_scope(ctx):
    return ctx["fact"].t <= ctx["t"]


def observations(ctx):
    claim = ctx['fact']
    scopes = getattr(ctx['record'], 'claim_scopes', {})
    scope = scopes.get(str(claim.fid), scopes.get(claim.fid, {}))
    asserted_at = scope.get('event_step') if claim.slot == 'mem' else None
    until = min(ctx['t'], asserted_at) if type(asserted_at) is int else ctx['t']
    return sorted((f for f in ctx["record"].F if f.t <= until
                   and (ctx["scope"] != "step" or f.t == ctx["fact"].t)
                   and f.slot == "obs" and f.side == "env"), key=lambda f: (f.t, f.fid))


def linked_feedback(record, action):
    rows = [f for f in record.F if f.slot == "obs" and f.side == "env"
            and action.fid in f.basis and f.t > action.t]
    first_t = min((f.t for f in rows), default=None)
    return [f for f in rows if f.t == first_t]


def contract(ctx):
    return [f for f in ctx["record"].F if f.slot == "chk" and f.side == "env"
            and f.rel == "admissibility" and ctx["fact"].fid in f.basis
            and f.t == ctx["fact"].t]


def signature(fact):
    # Preserve the full payload and every argument; ignore time/provenance metadata.
    return (fact.rel, tuple(str(a) for a in fact.args), fact.value_type,
            json.dumps(fact.value, sort_keys=True, ensure_ascii=False, default=sorted))


def functional_key(record, fact):
    relation = record.relations[fact.rel]
    return fact.rel, tuple(fact.args[i] for i in relation.key)


def related_observations(ctx):
    record, claim = ctx["record"], ctx["fact"]
    relation = record.relations[claim.rel]
    if relation.is_functional():
        return [f for f in observations(ctx)
                if functional_key(record, f) == functional_key(record, claim)]
    return [f for f in observations(ctx) if f.key() == claim.key()]


def compatible(claim, observation):
    if claim.value_type != observation.value_type:
        return None
    if claim.args == observation.args:
        return claim.value == observation.value
    # Different positive versions of a functional relation are incompatible.
    if claim.value is True and observation.value is True:
        return False
    return None


def verdict(value, reason, facts=()):
    return value, reason, tuple(f.fid for f in facts)


def typed_fact(ctx):
    fact = ctx["fact"]
    if fact.rel in MODULE_RELATIONS or fact.value_type in ("unknown", "null"):
        return None, "unstructured_fact"
    if any(a.index in (UNBOUND, "current") for a in fact.args):
        return None, "unbound_or_page_placeholder"
    return True, "typed_fact"


def observation_history(ctx):
    return (True, "observation_history") if observations(ctx) else (None, "missing_observations")


def action_contract(ctx):
    return (True, "action_contract") if len(contract(ctx)) == 1 else (None, "missing_or_ambiguous_contract")


def feedback_requirement(ctx):
    return (True, "linked_feedback") if ctx["feedback"] else (None, "missing_feedback")


def task_goals(ctx):
    return (True, "task_goals") if ctx["record"].goals else (None, "missing_task_goals")


def historical_time_ambiguity(ctx):
    history = related_observations(ctx)
    if history and compatible(ctx["fact"], history[-1]) is False:
        if any(compatible(ctx["fact"], f) is True for f in history[:-1]):
            return None, "missing_assertion_time"
    return False, "no_historical_exception"


def consistent_assertion(ctx):
    claim, record = ctx["fact"], ctx["record"]
    if not record.relations[claim.rel].is_functional():
        return verdict(None, "nonfunctional_assertion")
    history = related_observations(ctx)
    if not history:
        return verdict(None, "missing_matching_observation")
    reference = history[-1]
    result = compatible(claim, reference)
    if result is False and record.relations[claim.rel].is_mutable():
        return verdict(None, "missing_assertion_time", (reference,))
    return verdict(result, "functional_consistency" if result is not None else "incomparable_values",
                   (reference,))


def grounded_assertion(ctx):
    history = related_observations(ctx)
    matching = [f for f in history if signature(f) == signature(ctx["fact"])]
    if matching:
        return verdict(True, "matching_record_exists_not_proof_of_use", (matching[-1],))
    if history and not ctx["record"].relations[ctx["fact"].rel].is_mutable():
        if compatible(ctx["fact"], history[-1]) is False:
            return verdict(False, "explicit_counterrecord", (history[-1],))
    return verdict(None, "missing_grounding_or_assertion_time")


def updated_information(ctx):
    history = related_observations(ctx)
    supports = [f for f in history if signature(f) == signature(ctx["fact"])]
    if not supports:
        return verdict(None, "missing_old_supported_assertion")
    invalidating = [f for f in history if f.t > supports[0].t
                    and compatible(ctx["fact"], f) is False]
    if not invalidating:
        return verdict(True, "supported_value_not_observed_invalidated", (supports[-1],))
    # Existing memory Facts do not say whether a sentence describes now or history.
    return verdict(None, "missing_current_state_assertion", (supports[0], invalidating[-1]))


def admitted_decision(ctx):
    check = contract(ctx)[0]
    if check.value in ("quoted", "not_admissible"):
        return verdict(False, "explicit_contract_mismatch", (check,))
    if check.value == "well_formed":
        return verdict(True, "parser_contract_satisfied_only", (check,))
    return verdict(None, "unsupported_contract_or_no_tag", (check,))


def consistent_result(ctx):
    failure = [f for f in ctx["feedback"] if f.rel == "effect" and f.value == "none"]
    if failure:
        return verdict(False, "explicit_no_effect_not_responsibility_judgment", failure)
    return verdict(None, "missing_expected_effect_model", ctx["feedback"])


def consistent_completion(ctx):
    claim, goals = ctx["fact"], ctx["record"].goals
    relevant = [g for g in goals if g.entity() in claim.args]
    if len(relevant) != 1 or relevant[0].status_mode != "tracked":
        return verdict(None, "unbound_or_unmodeled_completion_goal")
    # Do not infer a terminal commitment from a domain action or a placement.
    statuses = [f for f in observations(ctx) if f.key() == claim.key() and f.rel == "satisfied"]
    if not statuses or statuses[-1].t != claim.t:
        return verdict(None, "missing_contemporaneous_goal_status")
    reference = statuses[-1]
    return verdict(reference.value is True if type(reference.value) is bool else None,
                   "explicit_goal_status", (reference,))


def state_fingerprint(record, t):
    state = {}
    for f in record.F:
        if f.t <= t and f.slot == "obs" and f.side == "env" and f.rel != "effect":
            state[functional_key(record, f) if record.relations[f.rel].is_functional() else f.key()] = signature(f)
    return tuple(sorted(state.values()))


def observable_progress(ctx):
    current, record = ctx["fact"], ctx["record"]
    previous = [f for f in actions(record) if (f.t, f.fid) < (current.t, current.fid)]
    if not previous:
        return verdict(None, "missing_previous_action")
    old = previous[-1]
    if signature(old) != signature(current) or old.span != current.span:
        return verdict(None, "non_equivalent_action")
    if any(a.index in (UNBOUND, "current") for a in (*old.args, *current.args)):
        return verdict(None, "unbound_or_page_placeholder")
    before = linked_feedback(record, old)
    after = ctx["feedback"]
    if not before or not after:
        return verdict(None, "missing_comparable_results")
    refs = (old, *before, *after)
    if state_fingerprint(record, old.t) != state_fingerprint(record, current.t):
        return verdict(None, "changed_or_incomplete_context", refs)
    same = {signature(f) for f in before} == {signature(f) for f in after}
    if same:
        return verdict(False, "same_nonempty_parsed_result", refs)
    return verdict(True, "parsed_result_changed_not_optimality_proof", refs)


def retry_exception_unavailable(ctx):
    result, _, _ = observable_progress(ctx)
    # No existing field establishes that a retry/revalidation exception is absent.
    return (None, "missing_retry_exception_information") if result is False else (False, "no_repeat_candidate")


def fact_witness(ctx, reason, refs):
    refs = tuple(dict.fromkeys((ctx["fact"].fid, *refs)))
    return {"t": ctx["t"], "refs": refs, "details": {
        "event_step": ctx["fact"].t, "visible_step": ctx["t"],
        "bindings": {"fact": ctx["fact"].fid}, "reason": reason,
    }}


TRIGGERS = {f.__name__: f for f in (memory_claims, actions, completion_claims)}
APPLICABILITY = {"fact_in_scope": fact_in_scope}
PROPERTIES = {f.__name__: f for f in (consistent_assertion, grounded_assertion, updated_information,
               admitted_decision, consistent_result, consistent_completion, observable_progress)}
REQUIREMENTS = {"typed_fact": typed_fact, "observation_history": observation_history,
                "action_contract": action_contract, "linked_feedback": feedback_requirement,
                "task_goals": task_goals}
EXCEPTIONS = {f.__name__: f for f in (historical_time_ambiguity, retry_exception_unavailable)}
WITNESSES = {"fact_witness": fact_witness}


def validate_rules(rules):
    if len({r.id for r in rules}) != len(rules):
        raise ValueError("duplicate rule IDs")
    for rule in rules:
        if rule.variables != {"fact": "Fact"}:
            raise ValueError("this minimal executor binds one existing Fact")
        for name, registry in ((rule.trigger, TRIGGERS), (rule.applicability, APPLICABILITY),
                               (rule.property, PROPERTIES), (rule.witness_builder, WITNESSES),
                               *((n, REQUIREMENTS) for n in rule.evidence_requirements),
                               *((n, EXCEPTIONS) for n in rule.exceptions)):
            if name not in registry:
                raise ValueError(f"unknown registered rule function/capability: {name}")


def evaluate(ctx, rule):
    applicable = APPLICABILITY[rule.applicability](ctx)
    if applicable is not True:
        return "not_applicable" if applicable is False else "unknown", "outside_scope", ()
    for requirement in rule.evidence_requirements:
        available, reason = REQUIREMENTS[requirement](ctx)
        if available is not True:
            return "unknown", reason, ()
    for exception in rule.exceptions:
        exempt, reason = EXCEPTIONS[exception](ctx)
        if exempt is not False:
            return "not_applicable" if exempt is True else "unknown", reason, ()
    value, reason, refs = PROPERTIES[rule.property](ctx)
    return {True: "satisfied", False: "violated", None: "unknown"}[value], reason, refs


def detect_abstract_rules(record, rules=RULES):
    """Return all evaluations and append only violated candidates, idempotently."""
    validate_rules(rules)
    seen = {(e.rule, e.t, e.refs) for g in record.evidences.values() for e in g.items}
    evaluations = []
    for rule in rules:
        facts = TRIGGERS[rule.trigger](record)
        if not facts:
            evaluations.append({"rule": rule.id, "state": "unknown", "t": None, "refs": [],
                                "details": {"bindings": {}, "reason": "missing_completion_assertion"
                                            if rule.trigger == "completion_claims" else "missing_trigger_fact"}})
        for fact in facts:
            if not isinstance(fact, Fact) or record.fact(fact.fid) is not fact:
                raise ValueError("trigger must bind a Fact in this Record")
            feedback = linked_feedback(record, fact) if rule.scope == "response" else []
            t = (max((f.t for f in record.F), default=fact.t) if rule.scope == "trajectory"
                 else max((f.t for f in feedback), default=fact.t))
            ctx = {"record": record, "fact": fact, "t": t, "feedback": feedback, "scope": rule.scope}
            state, reason, refs = evaluate(ctx, rule)
            finding = WITNESSES[rule.witness_builder](ctx, reason, refs)
            if any(record.fact(fid).t > t for fid in finding["refs"]):
                raise ValueError("witness references a future Fact")
            evaluations.append({"rule": rule.id, "state": state, **finding})
            key = rule.id, t, finding["refs"]
            if state == "violated" and key not in seen:
                record.add_evidence("abstract.rule", rule=rule.id, **finding)
                seen.add(key)
    return evaluations


def summarize(evaluations):
    return {rule.id: dict(Counter(e["state"] for e in evaluations if e["rule"] == rule.id))
            for rule in RULES}


def export_rules():
    return [asdict(rule) for rule in RULES]
