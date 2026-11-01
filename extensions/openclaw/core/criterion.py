"""kappa, three-valued, by slot, always citing the facts it read.

The question is whether a fact follows from what it relied on, not whether it is
correct. A step that faithfully consumes an earlier wrong value is SAT and is
therefore propagation, not the introduced fault.

UNK is the third value and is not a convenience. Forced to choose between SAT
and VIOL on insufficient evidence a judge guesses, and a guessing check either
fires almost everywhere or almost nowhere.

Only the structural cases are decided here. A case this file cannot settle
returns UNK together with the operands a narrow call would need, and the caller
decides whether to spend a call. No threshold appears in this file.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from record import Record, Fact, Entity, UNBOUND

SAT, VIOL, UNK = "sat", "viol", "unk"


@dataclass
class Verdict:
    value: str
    reason: str
    cites: Tuple[int, ...] = ()
    operands: Tuple[str, ...] = ()      # what a narrow call would be shown, when UNK

    def __bool__(self):
        raise TypeError("a three-valued verdict must not be used as a boolean")


def _situation_conflict(R: Record, f: Fact) -> Optional[Verdict]:
    """If the fact claims a situation, and the situating relation is functional,
    compare the claim with the value in force. A disagreement is derived from the
    arity, not judged."""
    if f.situation is None:
        return None
    s = R.fact(f.situation)
    rel = R.relations[s.rel]
    if not rel.is_functional():
        return None
    key = tuple(s.args[i] for i in rel.key)
    # The situation pointer is an environment context.  A same-step memory
    # claim must not rewrite that context and then be used to contradict the
    # action; that lets the mind validate its own hallucination.  Compare only
    # non-mind assignments, as the recollection rule does below.
    force = [g for g in R.F if g.rel == s.rel and g.side != "mind" and g.t <= f.t
             and tuple(g.args[i] for i in rel.key) == key]
    if not force:
        return None
    latest = force[-1]
    if latest.args != s.args or latest.value != s.value:
        return Verdict(VIOL,
                       f"the situation claims {s.rel}{tuple(str(a) for a in s.args)}="
                       f"{s.value!r} while {latest.rel}{tuple(str(a) for a in latest.args)}="
                       f"{latest.value!r} is in force",
                       (f.fid, s.fid, latest.fid))
    return None


def _payload(rel, g: Fact):
    """What two facts about the same key disagree about. Where the relation has
    argument positions outside its key the version lives there; otherwise it is
    the fact's own value. Comparing the wrong one reports a disagreement between
    a recollection, which names a value, and a discharge, which names whether a
    requirement was met."""
    vp = [i for i in rel.version_positions()
          if not str(g.args[i]).endswith(":" + UNBOUND)]
    if vp:
        return tuple(str(g.args[i]) for i in vp)
    if rel.version_positions():
        return None          # every version position was left unnamed
    return g.value


def _recollection(R: Record, f: Fact) -> Verdict:
    """A recollection is answered against what the run was shown, never against
    an earlier recollection of the same thing. Comparing the mind against itself
    lets a claim repeated four times confirm itself, which is the shape of the
    hallucination this check exists to catch."""
    rel = R.relations[f.rel]
    scope = getattr(R, 'claim_scopes', {}).get(f.fid, {})
    event_step = scope.get('event_step')
    if (scope.get('modality') != 'asserted' or type(event_step) is not int
            or not 1 <= event_step <= f.t):
        return Verdict(UNK, 'recollection has no unambiguous assertion time/scope',
                       (f.fid,), (f'claim: {f.span[:160]}',))
    key = tuple(str(f.args[i]) for i in rel.key)
    shown = [g for g in R.F if g.rel == f.rel and g.side == 'env' and g.t <= event_step
             and (g.slot == 'obs' or (g.slot == 'chk' and g.rel == 'chosen'))
             and tuple(str(g.args[i]) for i in rel.key) == key]

    if not shown:
        if not f.basis:
            return Verdict(UNK, "no earlier source is recorded for this recollection",
                           (f.fid,), (f"claim: {f.span[:120]}",))
        return Verdict(UNK, "sourced, but no version chain to compare against",
                       tuple([f.fid] + list(f.basis)), (f"claim: {f.span[:120]}",))

    latest = shown[-1]
    if any(a['kind'] == 'observation' and a['status'] == 'unresolved'
           and latest.t < a['step'] <= event_step for a in getattr(R, 'parse_audits', [])):
        return Verdict(UNK, 'state may have changed during unresolved observations', (f.fid, latest.fid))
    if f.value_type == 'boolean' and latest.value_type == 'boolean':
        if f.args == latest.args:
            agrees = f.value == latest.value
        elif latest.value is True and rel.is_functional():
            agrees = f.value is False
        else:
            return Verdict(UNK, 'negative observation does not identify another positive state', (f.fid, latest.fid))
        if not agrees and scope.get('temporal') == 'historical' and scope.get('time_basis') == 'step':
            # "Step N: returned to X" can summarize action N, whose feedback
            # arrives at N+1. Do not force a pre-action interpretation when the
            # visible feedback supports the post-action interpretation.
            after = [g for g in R.F if g.slot == 'obs' and g.side == 'env'
                     and g.t == event_step + 1 and g.t <= f.t and g.rel == f.rel
                     and g.args == f.args and g.value == f.value]
            missing_after = event_step >= f.t or any(a['kind'] == 'observation'
                and a['status'] == 'unresolved' and a['step'] == event_step + 1
                for a in getattr(R, 'parse_audits', []))
            if after or missing_after:
                return Verdict(UNK, 'historical step does not distinguish pre-action observation from post-action outcome',
                               tuple([f.fid, latest.fid] + [g.fid for g in after]))
        return Verdict(SAT if agrees else VIOL,
                       f"{'agrees' if agrees else 'conflicts'} with observation at t{latest.t} for assertion time t{event_step}",
                       (f.fid, latest.fid))
    a, b = _payload(rel, f), _payload(rel, latest)
    if a is None:
        return Verdict(SAT, f"names no version, and {f.rel}({', '.join(key)}) "
                            f"was shown at t{latest.t}", (f.fid, latest.fid))
    if a != b:
        return Verdict(VIOL, f"recalls {a!r} where {b!r} was shown at t{latest.t}",
                       (f.fid, latest.fid))
    return Verdict(SAT, f"agrees with what was shown at t{latest.t}",
                   (f.fid, latest.fid))


def kappa(R: Record, f: Fact) -> Verdict:
    # an environment statement is justified by being the environment's
    if f.side == "env":
        return Verdict(SAT, "environment origin", (f.fid,))

    conflict = _situation_conflict(R, f)
    if conflict is not None:
        return conflict

    if f.slot == "mem":
        return _recollection(R, f)

    if f.slot == "act":
        i = R.intentions.get(str(f.purpose)) if f.purpose is not None else None
        if i is None:
            return Verdict(UNK, "no intention is registered for this step", (f.fid,),
                           (f"action: {f.rel}{tuple(str(a) for a in f.args)}",))
        # Compare only at the level the intention actually declares. Claiming a
        # disagreement about a target the plan never named is how a check starts
        # firing on nearly half of all actions.
        b = i.binding
        if b == "absent":
            return Verdict(UNK, "the plan declared no intention", (f.fid,),
                           (f"action: {f.rel}{tuple(str(a) for a in f.args)}",))
        if b == "unbound":
            return Verdict(UNK, "the plan declared an intention but named no act",
                           (f.fid,), (f"plan: {i.span[:140]}",
                                      f"action: {f.rel}{tuple(str(a) for a in f.args)}"))
        if not (i.extraction.startswith('decision_clause:') or i.extraction == 'rule'):
            return Verdict(UNK, 'plan extraction does not bind an audited current decision',
                           (f.fid,), (f'plan: {i.span[:160]}',))
        def option_matches(option):
            if option.verb != f.rel:
                return False
            if option.objects is None:
                return True
            return tuple(str(x) for x in option.objects) == tuple(str(a) for a in f.args)

        matched_option = next((x for x in i.execution_options if option_matches(x)), None)
        if matched_option is not None:
            return Verdict(SAT, f"implements an admitted {matched_option.role} of the intention",
                           (f.fid,))
        if b == "verb_only":
            return Verdict(UNK, f"the plan names {i.verb} without a bound target or execution sequence",
                           (f.fid,), (f"plan: {i.span[:140]}",
                                      f"action: {f.rel}{tuple(str(a) for a in f.args)}"))
        if i.verb != f.rel:
            return Verdict(VIOL, f"intends {i.verb} and performs {f.rel}",
                           (f.fid,))
        if tuple(str(x) for x in i.objects) != tuple(str(a) for a in f.args):
            return Verdict(VIOL,
                           f"intends {i.verb} on {[str(x) for x in i.objects]} "
                           f"and performs it on {[str(a) for a in f.args]}", (f.fid,))
        return Verdict(SAT, "instantiates the intention bound to it", (f.fid,))

    if f.slot == "plan":
        p = f.purpose
        if p is None:
            return Verdict(UNK, "the plan states no purpose", (f.fid,),
                           (f"plan: {f.span[:160]}",))
        ruled = [g for g in R.by_entity(p)
                 if g.t <= f.t and g.slot == "chk" and g.value is False]
        if ruled:
            return Verdict(VIOL, f"pursues a purpose ruled out at t{ruled[-1].t}",
                           (f.fid, ruled[-1].fid))
        # A rejected action is not proof that a later plan is wrong: formatting,
        # prerequisites, or the intended operation may have changed. Empty
        # bindings must never compare as equal intentions.
        i = R.intentions.get(str(p))
        if i is not None and i.binding == 'full':
            for e, j in R.intentions.items():
                if e == str(p) or j.binding != 'full' or j.verb != i.verb or j.objects != i.objects:
                    continue
                earlier = [g for g in R.by_entity(Entity("int", e.split(":")[1]))
                           if g.slot == "chk" and g.rel == "admissibility" and g.t < f.t]
                refused = [g for g in earlier if g.value != "well_formed"]
                if refused:
                    return Verdict(UNK,
                                   f"similar earlier intention had a parsed action contract issue at "
                                   f"t{refused[-1].t}; retry validity needs current prerequisites and feedback",
                                   (f.fid, refused[-1].fid))
        return Verdict(UNK, "reachability of the purpose is not settled structurally",
                       tuple([f.fid] + list(f.basis)),
                       (f"purpose: {p}", f"plan: {f.span[:160]}"))

    if f.slot == "refl":
        return Verdict(UNK, "an assessment is not settled structurally", (f.fid,),
                       (f"assessment: {f.span[:160]}",))

    return Verdict(SAT, "no case applies", (f.fid,))
