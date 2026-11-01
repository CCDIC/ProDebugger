"""The action space.

Three families. Steering moves the focus and reports what it passed through, so
the direction is the caller's and the path is the record's. Evidence gathers at
a focus. Framing opens and closes an investigation.

The set is closed. That is what makes an investigation reproducible, auditable
and countable. Nothing here decides where to go next; that is the caller's job,
which is why the search is a subgraph rather than a path or a binary tree.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Set
import collections

from record import Record, Fact, Entity
from criterion import kappa, Verdict, SAT, VIOL, UNK
import primitives as P
import views as V


@dataclass
class Move:
    """The result of a steering operator."""
    focus: List[int]          # the new focus, as fact ids
    passed: List[int]         # what the move went through
    note: str = ""


# ---------------- steering ---------------------------------------------
def back(R: Record, fid: int) -> Move:
    f = R.fact(fid)
    return Move(list(f.basis), [fid], "one step along the dependence axis")


def up(R: Record, fid: int) -> Move:
    f = R.fact(fid)
    if f.purpose is None:
        return Move([], [fid], "this fact states no purpose")
    parent = R.purpose_parent(f.purpose)
    target = parent if parent is not None else f.purpose
    return Move([g.fid for g in R.by_entity(target)], [fid], f"up to {target}")


def down(R: Record, purpose: Entity) -> Move:
    """The sub-intentions that serve this purpose."""
    kids = [e for e in R.E.values()
            if e.kind == "int" and str(R.purpose_parent(e)) == str(purpose)]
    out: List[int] = []
    for k in kids:
        out.extend(g.fid for g in R.by_entity(k))
    return Move(out, [], f"down to {len(kids)} sub-intentions of {purpose}")


def versions(R: Record, rel: str, args: Tuple[Entity, ...]) -> Move:
    c = R.chain(rel, args)
    return Move([f.fid for f in c], [], f"{len(c)} assignments of {rel}")


def neighbours(R: Record, fid: int, span: int = 1) -> Move:
    f = R.fact(fid)
    out: List[int] = []
    for t in range(f.t - span, f.t + span + 1):
        out.extend(g.fid for g in R.by_step(t))
    return Move(out, [fid], f"steps {f.t - span} to {f.t + span}")


def siblings(R: Record, fid: int) -> Move:
    f = R.fact(fid)
    if f.purpose is None:
        return Move([], [fid], "this fact states no purpose")
    out = [g.fid for g in R.F if g.purpose is not None
           and str(g.purpose) == str(f.purpose) and g.fid != fid]
    return Move(out, [fid], f"{len(out)} facts under the same purpose")


# ---------------- evidence ---------------------------------------------
def check(R: Record, fid: int) -> Verdict:
    """The criterion is evidence the caller may request, not a control flow the
    code runs. A caller is free never to invoke it."""
    return kappa(R, R.fact(fid))


def state(R: Record, t: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for (rel, args) in list(R._chain.keys()):
        f = R.in_force(rel, args, t)
        if f is not None:
            out[f"{rel}({','.join(str(a) for a in args)})"] = f.value
    return out


def span(R: Record, fid: int) -> str:
    return R.fact(fid).span


def _live_at(R: Record, t: int):
    """The unmet requirements that were still reachable at this step: either an
    opportunity at or after it was left undecided, or none had come yet."""
    live = []
    for u in failed(R):
        q = Entity(*u["requirement"].split(":", 1))
        opps = could_have(R, q)
        if not opps or any(o["step"] >= t and not o["met"] for o in opps):
            live.append(str(q))
    return live


def _put_right_after(R: Record, f: Fact):
    """Whether anything the run was shown after this step re-established what it
    concerns. A later observation on the same key is the record putting the
    matter right, and condition 3 then rules the step out."""
    rel = R.relations.declared().get(f.rel)
    if rel is None or f.side == "env":
        return []
    key = tuple(str(f.args[i]) for i in rel.key)
    return [g.fid for g in R.F
            if g.rel == f.rel and g.side != "mind" and g.t > f.t
            and tuple(str(g.args[i]) for i in rel.key) == key][:6]


def contrast(R: Record, *fids: int) -> Dict[str, Any]:
    """Put candidates side by side against the three conditions.

    Ranking is not on offer. What is on offer is the one thing a reader cannot
    get by looking at candidates one at a time: which column actually separates
    them. A column on which every candidate agrees has told you nothing, whatever
    it says."""
    if len(fids) < 2:
        raise ValueError("contrast needs at least two facts")

    rows = []
    facts = [R.fact(x) for x in fids]
    reach = {f.fid: {g.fid for g in P.why()(R, [f])} for f in facts}
    for f in facts:
        v = kappa(R, f)
        put_right = _put_right_after(R, f)
        rows.append({
            "fid": f.fid, "step": f.t, "slot": f.slot,
            "states": repr(f)[:90],
            # condition 1: did its own content follow from what it had
            "verdict": v.value, "because": v.reason[:110],
            # condition 2: does the failure reach it, and is it downstream of a rival
            "requirements_live_here": _live_at(R, f.t),
            "reaches": sorted(reach[f.fid] & {g.fid for g in facts if g.fid != f.fid}),
            # condition 3: did anything afterwards put the matter right
            "put_right_after": put_right,
            "after_commit": any(g.t <= f.t and g.slot == "act" and R.commits(g)
                                and g.t < f.t for g in R.F),
        })
    cols = ("verdict", "requirements_live_here", "reaches",
            "put_right_after", "after_commit")
    same = [c for c in cols if len({repr(r[c]) for r in rows}) == 1]
    return {"rows": rows,
            "discriminates": [c for c in cols if c not in same],
            "tells_you_nothing": same}


def count(R: Record, rel: Optional[str] = None, slot: Optional[str] = None,
          side: Optional[str] = None, value: Any = "<any>") -> Dict[str, Any]:
    """An aggregate. The caller decides what a number means; this returns the
    number and the facts behind it."""
    hits = [f for f in R.F
            if (rel is None or f.rel == rel)
            and (slot is None or f.slot == slot)
            and (side is None or f.side == side)
            and (value == "<any>" or f.value == value)]
    return {"n": len(hits), "of": len(R.F),
            "steps": sorted({f.t for f in hits}), "fids": [f.fid for f in hits][:64]}


def conflicts(R: Record, at_step: Optional[int] = None) -> List[Dict[str, Any]]:
    """Two facts that a functional relation cannot both satisfy.

    A mutable relation taking different values at different steps is the ordinary
    case, not a conflict: an agent is meant to move. A conflict is two values in
    force at the same step, or, for a relation that is not mutable, two values at
    all. Both are derived from the declared arity, not judged."""
    limit = R.length() if at_step is None else at_step
    out: List[Dict[str, Any]] = []
    for name, rel in R.relations.declared().items():
        if not rel.is_functional():
            continue
        groups: Dict[Tuple[Entity, ...], List[Fact]] = collections.defaultdict(list)
        for f in R.by_rel(name):
            if f.t <= limit:
                groups[tuple(f.args[i] for i in rel.key)].append(f)
        for key, facts in groups.items():
            if rel.is_mutable():
                by_t: Dict[int, Dict[Tuple[Entity, ...], Fact]] = collections.defaultdict(dict)
                for f in facts:
                    by_t[f.t][f.args] = f
                clashes = [(t, list(v.values())) for t, v in by_t.items() if len(v) > 1]
            else:
                distinct = {f.args: f for f in facts}
                clashes = [(None, list(distinct.values()))] if len(distinct) > 1 else []
            for t, fs in clashes:
                out.append({"relation": name, "key": [str(k) for k in key],
                            "at_step": t, "survivors": [repr(f) for f in fs]})
    return out


# ---------------- retrieval by composition ------------------------------
def view(R: Record, spec: str) -> Dict[str, Any]:
    """Execute a declarative entity-evidence view program."""
    return V.program_view(R, spec)


def about(R: Record, kind: str, index: str) -> List[int]:
    """Every fact mentioning one entity. The usual head of a composition."""
    return [f.fid for f in P.run(R, P.about(Entity(kind, str(index))))]


_LENS = {"about": None, "why": P.why, "purpose": P.purpose, "window": P.window}


def lens(R: Record, spec: str) -> List[int]:
    """Compose the primitives from a spec, so a caller can ask a compound
    question instead of being restricted to the ones we thought of.

        about:portable:creditcard_1 | purpose          what it was used for
        about:portable:creditcard_1 | why              where its claims came from
        about:place:sofa_1 | why | window:1:10         the early part of that

    Each stage maps facts to facts, which is why they compose at all."""
    stages = [x.strip() for x in spec.split("|") if x.strip()]
    if not stages:
        raise ValueError("an empty lens")
    lenses = []
    for st in stages:
        parts = st.split(":")
        head = parts[0]
        if head not in _LENS:
            raise KeyError(f"unknown lens {head!r}; known: {sorted(_LENS)}")
        if head == "about":
            if len(parts) != 3:
                raise ValueError("about takes kind and index, as about:kind:index")
            lenses.append(P.about(Entity(parts[1], parts[2])))
        elif head == "why":
            lenses.append(P.why(int(parts[1]) if len(parts) > 1 else 1 << 30))
        elif head == "purpose":
            lenses.append(P.purpose(to_requirement=(len(parts) < 2 or parts[1] != "one")))
        elif head == "window":
            if len(parts) != 3:
                raise ValueError("window takes two steps, as window:t1:t2")
            lenses.append(P.window(int(parts[1]), int(parts[2])))
    return [f.fid for f in P.compose(*lenses)(R, [])]


def attempts(R: Record, kind: str, index: str) -> List[Dict[str, Any]]:
    """Every action taken in service of one purpose, in order, with what the
    environment returned next and whether any of it was new.

    Judging whether a line of attack was worth continuing is not settled by any
    structure in the record, so it is left to the caller. What the record can do
    is put the attempts side by side with their outcomes, which is the operand
    such a judgement needs and which no schema of isolated events provides."""
    target = Entity(kind, str(index))
    if str(target) not in R.E:
        reqs = sorted(str(e) for e in R.E.values() if e.kind == "req")
        raise KeyError(f"{target} is not an entity of this record; "
                       f"requirements are {reqs}")
    # An action usually serves several open requirements at once while purpose
    # holds one. Asking about a requirement therefore means asking about the
    # actions taken while it was open, not only those whose pointer names it.
    served = [g.t for g in R.F if g.purpose is not None
              and str(g.purpose) == str(target) and g.side != "task"]
    discharged = [g.t for g in R.F if g.slot == "chk" and g.value is True
                  and g.purpose is not None and str(g.purpose) == str(target)]
    until = min(discharged) if discharged else R.length()

    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for f in R.F:
        if f.slot != "act" or f.purpose is None:
            continue
        chain = [str(e) for e in R.purpose_chain(f.purpose)]
        if str(target) not in chain and not (served or True and f.t <= until):
            continue
        if str(target) not in chain and f.t > until:
            continue
        after = [g for g in R.F if g.t == f.t + 1 and g.side == "env"]
        # what the attempt surfaced, counted in objects rather than in facts.
        # Counting every argument makes the figure never fall, because the page
        # and the agent are arguments of something at every step.
        produced = {str(a) for g in after for a in g.args
                    if a.kind not in ("agent", "page", "listing", "item_page",
                                      "attribute", "req", "int")}
        new = sorted(produced - seen)
        seen |= produced
        effects = [g.value for g in after if g.rel == "effect"]
        out.append({
            "step": f.t, "fid": f.fid,
            "attempt": f"{f.rel}({','.join(str(a) for a in f.args)})",
            "returned": len(after),
            "new_to_the_run": len(new),
            "no_effect": "none" in effects,
            "span": f.span[:90],
        })
    return out


# ---------------- framing ----------------------------------------------
def failed(R: Record) -> List[Dict[str, Any]]:
    """The goals the run left unsatisfied, as the entry point of a session."""
    out = []
    for g in R.goals:
        if g.status_mode != "tracked":
            continue
        q = g.entity()
        met = [f for f in R.F if f.rel == g.rel and f.slot == "chk"
               and f.value is True and f.purpose is not None and str(f.purpose) == str(q)]
        if len(met) < g.count:
            out.append({"requirement": str(q), "needs": g.count, "met": len(met),
                        "expression": f"exists^{g.count} x: {g.of_kind} and "
                                      f"{g.rel}(x, {g.target}) {list(g.attrs)}"})
    return out


def could_have(R: Record, q: Entity) -> List[Dict[str, Any]]:
    if str(q) not in R.E:
        known = sorted(str(e) for e in R.E.values() if e.kind == "req")
        raise KeyError(f"{q} is not a requirement of this record; known: {known}")
    """The moments at which this requirement was decidable and was not decided.
    An omission has no fact of its own, so it is stated as an absence against an
    opportunity."""
    opps = [f for f in R.F if f.slot == "chk" and f.purpose is not None
            and str(f.purpose) == str(q)]
    decided = {f.t: f for f in opps if f.value is not None}
    offered = {f.t for f in R.F if f.slot == "obs" and f.purpose is not None
               and str(f.purpose) == str(q)}
    out = []
    for t in sorted(offered | set(decided)):
        g = decided.get(t)
        # `met` separates an opportunity the run took correctly from one it took
        # with a value the task did not name. An opportunity decided and not met
        # is sterile: arriving at it could never have discharged the requirement.
        out.append({"step": t, "decided": t in decided,
                    "met": (None if g is None else bool(g.value)),
                    "took": (None if g is None else str(g.args[-1]).split(":", 1)[-1])})
    return out


def profile(R: Record) -> Dict[str, Any]:
    """A run-level picture, so a caller can route before it locates. The fields
    are counts and structure only; no field is a verdict."""
    return {
        "ir_version": R.ir_version,
        "domain": getattr(R, "domain", "unknown"),
        "domain_briefing": getattr(R, "domain_briefing", {}),
        "trajectory": R.tid,
        "task": R.task,
        "steps": R.length(),
        "facts": len(R.F),
        "by_slot": dict(collections.Counter(f.slot for f in R.F)),
        "by_side": dict(collections.Counter(f.side for f in R.F)),
        "relations_touched": sorted({f.rel for f in R.F}),
        "goals": [g.name or g.rel for g in R.goals],
        "tracked_goals": [g.name or g.rel for g in R.goals
                          if g.status_mode == "tracked"],
        "unmodeled_goals": [g.name or g.rel for g in R.goals
                            if g.status_mode != "tracked"],
        "unsatisfied": failed(R),
        "functional_conflicts": len(conflicts(R)),
        "facts_without_basis": sum(1 for f in R.F if f.side == "mind" and not f.basis),
        "intentions": len(R.intentions),
        "purpose_depth": max((len(R.purpose_chain(f.purpose)) for f in R.F
                              if f.purpose is not None), default=0),
        "ending": _ending(R),
        "timeline": _timeline(R),
    }


# The reader answers with a step, so it needs the steps in front of it. Both
# fields below are read off the record and cost nothing.

def _timeline(R: Record):
    """One line per step: what was done, whether the environment took it, and
    which sides of the mind asserted anything there."""
    out = []
    for t in range(1, R.length() + 1):
        at = [f for f in R.F if f.t == t and f.slot == "act"]
        adm = [f for f in R.F if f.t == t and f.rel == "admissibility"]
        said = sorted({f.slot for f in R.F if f.t == t and f.side == "mind"
                       and f.slot != "act"})
        out.append({"step": t,
                    "did": (at[0].span or str(at[0].value))[:48] if at else None,
                    "taken": (adm[0].value if adm else None),
                    "said": said})
    return out


def _ending(R: Record):
    """How the run stopped, which is what decides where recovery ended. A run
    that committed cannot be repaired after the commit; a run that was cut off
    was recoverable to its last step, so nothing late can be the critical one
    on the strength of lateness alone."""
    last = [f for f in R.F if f.t == R.length() and f.slot == "act"]
    if last and R.commits(last[0]):
        return {"kind": "committed", "at": R.length()}
    return {"kind": "cut_off", "at": R.length()}


RETRIEVAL = {"view": view, "about": about, "lens": lens, "attempts": attempts}
STEERING = {"back": back, "up": up, "down": down, "versions": versions,
            "neighbours": neighbours, "siblings": siblings}
EVIDENCE = {"check": check, "state": state, "span": span, "contrast": contrast,
            "count": count, "conflicts": conflicts}
FRAMING = {"failed": failed, "could_have": could_have, "profile": profile}
ACTION_SPACE = {**RETRIEVAL, **STEERING, **EVIDENCE, **FRAMING}
