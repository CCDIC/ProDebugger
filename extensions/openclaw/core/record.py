"""The execution record D = <E, R, F, G>.

A fact states which relation holds of which entities, with what value, in which
slot, from which side, at which step, in which situation, on what basis, and for
what purpose. The two orders the record needs are induced by the last two:

    f' < f      iff f' in basis(f)          the dependence axis, acyclic
    purpose(f)  in requirements or intentions   the purpose axis, a tree

Nothing in this file is benchmark specific.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Iterable
import collections

from kinds import Kinds
from relations import Relations

SLOTS = ("obs", "mem", "refl", "plan", "act", "chk")
SIDES = ("env", "mind", "task", "dbg")
IR_VERSION = "trajectory-ir/v3"


@dataclass(frozen=True)
class Entity:
    kind: str
    index: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.index}"


BINDINGS = ("full", "verb_only", "unbound", "absent")
CONSTRAINT_OPERATORS = (
    "eq", "neq", "lt", "le", "gt", "ge", "contains", "subset",
    "member_of", "string_match", "semantic_match",
)
CONSTRAINT_VALUE_TYPES = (
    "boolean", "number", "money", "string", "text", "enum", "entity", "set",
)
CONSTRAINT_MODES = ("selection", "observation", "semantic")
COMMITMENTS = ("explore", "inspect", "select", "navigate", "commit", "unknown")


@dataclass(frozen=True)
class Constraint:
    """One typed predicate in the task contract.

    Property names are data produced by a profile parser, not members of a
    closed Python enum.  The closed part of the language is the operator and
    value-type vocabulary.  `mode` records how the predicate can be verified:
    by an explicit selection, by an observation, or only semantically from
    free text.  Every extracted predicate retains its source span.
    """
    property: Entity
    operator: str
    expected: Any
    value_type: str = "string"
    unit: Optional[str] = None
    mode: str = "selection"
    span: str = ""
    confidence: float = 1.0

    def __post_init__(self):
        if self.operator not in CONSTRAINT_OPERATORS:
            raise ValueError(f"unknown constraint operator {self.operator!r}")
        if self.value_type not in CONSTRAINT_VALUE_TYPES:
            raise ValueError(f"unknown constraint value type {self.value_type!r}")
        if self.mode not in CONSTRAINT_MODES:
            raise ValueError(f"unknown constraint verification mode {self.mode!r}")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("constraint confidence must be in [0, 1]")
        if self.operator in ("lt", "le", "gt", "ge") \
                and self.value_type not in ("number", "money"):
            raise TypeError(f"{self.operator} needs number or money, got {self.value_type}")
        if self.operator == "semantic_match" and self.value_type not in ("text", "string"):
            raise TypeError("semantic_match needs a text or string value")


@dataclass(frozen=True)
class IntentionParameter:
    """A typed argument named by a plan, with its grounding span."""
    name: str
    value: Any
    value_type: str = "string"
    span: str = ""


@dataclass(frozen=True)
class IntentionOption:
    """One action that may legitimately realize or advance an intention."""
    verb: str
    objects: Optional[Tuple[Entity, ...]]
    role: str = "alternative"
    span: str = ""
    preconditions: Tuple[str, ...] = ()
    expected_effects: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Intention:
    """An intention is a structure, not a paragraph, but it is not always a
    complete one. A plan may name both the act and what it acts on, name only the
    act, name neither while still declaring that something is intended, or state
    no intention at all. Each of those is a different thing to compare against,
    and flattening them is how a comparison starts inventing disagreements.

        full       "click on B085WQKRRJ"        verb and objects
        verb_only  "search again"               verb, no target
        unbound    "try something different"    an intention with neither
        absent     the plan declares no action

    `objects` is None when nothing was named and an empty tuple when the plan
    named that the act takes no object. The two are not the same."""
    verb: Optional[str]
    objects: Optional[Tuple[Entity, ...]]
    purpose: Optional[Entity]
    span: str = ""
    parameters: Tuple[IntentionParameter, ...] = ()
    execution_options: Tuple[IntentionOption, ...] = ()
    serves: Tuple[Entity, ...] = ()
    mentions: Tuple[Entity, ...] = ()
    preconditions: Tuple[str, ...] = ()
    expected_effects: Tuple[str, ...] = ()
    commitment: str = "unknown"
    extraction: str = "rule"
    confidence: float = 1.0

    def __post_init__(self):
        if self.commitment not in COMMITMENTS:
            raise ValueError(f"unknown intention commitment {self.commitment!r}")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("intention confidence must be in [0, 1]")

    @property
    def binding(self) -> str:
        if self.verb is None and not self.objects:
            return "absent" if not self.span else "unbound"
        if self.verb is None:
            return "unbound"
        if self.objects is None:
            return "verb_only"
        return "full"        # a requirement, or another intention


@dataclass(frozen=True)
class SourceStep:
    """Lossless source envelope retained beside the relational IR.

    Facts support programmable checks, but a semantic reviewer also needs the
    exact module prose and environment turn that those facts summarize.  This
    envelope is evidence, never a diagnosis; annotations are not stored here.
    """
    observation: str
    agent: str
    modules: Dict[str, Optional[str]]
    admissible: Tuple[str, ...] = ()


@dataclass
class Fact:
    fid: int
    rel: str
    args: Tuple[Entity, ...]
    value: Any
    slot: str
    side: str
    t: int
    situation: Optional[int] = None          # the fid of the fact that fixes the situation
    basis: Tuple[int, ...] = ()
    purpose: Optional[Entity] = None
    span: str = ""
    value_type: str = "unknown"

    def key(self) -> Tuple[str, Tuple[Entity, ...]]:
        return (self.rel, self.args)

    def __repr__(self) -> str:
        s = f" @s{self.situation}" if self.situation is not None else ""
        b = f" <-{list(self.basis)}" if self.basis else ""
        p = f" ->{self.purpose}" if self.purpose else ""
        a = ",".join(str(x) for x in self.args)
        return f"[{self.fid}] t{self.t} {self.slot}/{self.side} {self.rel}({a})={self.value!r}{s}{b}{p}"


@dataclass
class Goal:
    """A relational expression with a count.

        exists^{=count} x : kind(x) <= of_kind and rel(x, target)
                            and has(x, a, v) for each (a, v) in attrs
    """
    count: int
    of_kind: str
    rel: str
    target: Optional[Entity]
    attrs: Tuple[Tuple[str, Any], ...] = ()
    name: str = ""
    # Explicit comparison semantics for evidence views. `attrs` is retained as
    # the relational payload used by the compiler; constraints say how an
    # observed value is tested against the task requirement.
    constraints: Tuple[Any, ...] = ()
    # `tracked` requirements participate in the benchmark's terminal success
    # test. `unmodeled` requirements remain first-class task constraints but
    # cannot yet be discharged deterministically from this profile's facts.
    status_mode: str = "tracked"

    def __post_init__(self):
        if self.status_mode not in ("tracked", "unmodeled"):
            raise ValueError("goal status_mode must be tracked or unmodeled")

    def entity(self) -> Entity:
        return Entity("req", self.name or f"{self.rel}_{self.of_kind}")


# An argument a recollection leaves unnamed. A memory that says "I saw this
# product" names the product and not the page it was on, and comparing it
# against a page it never claimed manufactures a disagreement.
UNBOUND = "*"


class Record:
    def __init__(self, tid: str, kinds: Kinds, relations: Relations,
                 task: str = "", goals: Iterable[Goal] = ()):
        self.tid = tid
        self.ir_version = IR_VERSION
        self.kinds = kinds
        self.relations = relations
        self.task = task
        self.goals: List[Goal] = list(goals)
        self.E: Dict[str, Entity] = {}
        self.F: List[Fact] = []
        self.intentions: Dict[str, Intention] = {}
        self.source_steps: Dict[int, SourceStep] = {}
        self.process_notes: Dict[int, Dict[str, Any]] = {}
        # Sidecar metadata preserves the Fact and TRACE/SUBMIT wire contracts.
        # Fact.t is the utterance time; event_step is the time being recalled.
        self.claim_scopes: Dict[int, Dict[str, Any]] = {}
        self._chain: Dict[Tuple[str, Tuple[Entity, ...]], List[int]] = collections.defaultdict(list)
        self._by_entity: Dict[str, List[int]] = collections.defaultdict(list)
        self._by_step: Dict[int, List[int]] = collections.defaultdict(list)
        self._by_rel: Dict[str, List[int]] = collections.defaultdict(list)
        # action values that end the run beyond recall. The profile supplies
        # them, because what counts as committing is a fact about the world the
        # run acted in, not about the record.
        self.commit_values: set = set()
        for g in self.goals:
            self.ent(g.entity().kind, g.entity().index)

    def commits(self, f) -> bool:
        """Whether this action put the run past recovery."""
        v = " ".join(str(f.value or "").split()).lower()
        return bool(self.commit_values) and v in self.commit_values

    # ---------------- construction -----------------------------------
    def ent(self, kind: str, index: str) -> Entity:
        if kind not in self.kinds:
            raise KeyError(f"undeclared kind {kind!r}")
        e = Entity(kind, str(index))
        self.E.setdefault(str(e), e)
        return e

    def intend(self, t: int, verb: Optional[str], objects: Optional[Iterable[Entity]],
               purpose: Optional[Entity], span: str = "",
               parameters: Iterable[IntentionParameter] = (),
               execution_options: Iterable[IntentionOption] = (),
               serves: Iterable[Entity] = (), mentions: Iterable[Entity] = (),
               preconditions: Iterable[str] = (), expected_effects: Iterable[str] = (),
               commitment: str = "unknown", extraction: str = "rule",
               confidence: float = 1.0) -> Tuple[Entity, Intention]:
        """Register an intention as an entity, so that the purpose axis can be a
        tree: purpose may point at another intention. Pass objects=None when the
        plan named no target; an empty tuple means it named that there is none."""
        e = self.ent("int", f"i{t}")
        i = Intention(
            verb=verb,
            objects=None if objects is None else tuple(objects),
            purpose=purpose,
            span=span,
            parameters=tuple(parameters),
            execution_options=tuple(execution_options),
            serves=tuple(serves),
            mentions=tuple(mentions),
            preconditions=tuple(preconditions),
            expected_effects=tuple(expected_effects),
            commitment=commitment,
            extraction=extraction,
            confidence=float(confidence),
        )
        self.intentions[str(e)] = i
        return e, i

    def add(self, rel: str, args: Iterable[Entity], value: Any, slot: str, side: str,
            t: int, situation: Optional[int] = None, basis: Iterable[int] = (),
            purpose: Optional[Entity] = None, span: str = "",
            value_type: Optional[str] = None) -> int:
        if slot not in SLOTS:
            raise ValueError(f"unknown slot {slot!r}")
        if side not in SIDES:
            raise ValueError(f"unknown side {side!r}")
        r = self.relations[rel]                    # raises if undeclared
        args = tuple(args)
        if len(args) != r.arity:
            raise ValueError(f"{rel} takes {r.arity} arguments, got {len(args)}")
        if r.arg_kinds:
            for pos, (arg, expected_kind) in enumerate(zip(args, r.arg_kinds)):
                if expected_kind is not None and not self.kinds.le(arg.kind, expected_kind):
                    raise TypeError(
                        f"{rel} argument {pos} needs {expected_kind}, got {arg.kind}"
                    )
        basis = tuple(basis)
        for b in basis:
            if not (0 <= b < len(self.F)):
                raise IndexError(f"basis {b} does not exist")
            if self.F[b].t > t:
                raise ValueError("ordered: a basis must not point forward")
        if situation is not None:
            if not (0 <= situation < len(self.F)):
                raise IndexError(f"situation {situation} does not exist")
            if self.F[situation].t > t:
                raise ValueError("ordered: a situation must not point forward")
        if value_type is None:
            if value is None:
                value_type = "null"
            elif isinstance(value, bool):
                value_type = "boolean"
            elif isinstance(value, (int, float)):
                value_type = "number"
            elif (isinstance(value, tuple) and len(value) == 2
                  and all(isinstance(x, (int, float)) for x in value)):
                value_type = "number_interval"
            else:
                value_type = "string"
        if "any" not in r.value_types and value_type not in r.value_types:
            raise TypeError(
                f"{rel} value needs one of {sorted(r.value_types)}, got {value_type}"
            )
        f = Fact(len(self.F), rel, args, value, slot, side, t, situation, basis,
                 purpose, span, value_type)
        self.F.append(f)
        self._chain[f.key()].append(f.fid)
        self._by_step[t].append(f.fid)
        self._by_rel[rel].append(f.fid)
        for a in args:
            self._by_entity[str(a)].append(f.fid)
        return f.fid

    # ---------------- access ------------------------------------------
    def fact(self, fid: int) -> Fact:
        return self.F[fid]

    def chain(self, rel: str, args: Iterable[Entity], upto: Optional[int] = None) -> List[Fact]:
        """The successive assignments of one <relation, arguments>."""
        ids = self._chain.get((rel, tuple(args)), [])
        return [self.F[i] for i in ids if upto is None or self.F[i].t <= upto]

    def in_force(self, rel: str, args: Iterable[Entity], at_step: int) -> Optional[Fact]:
        """The value in force at a step, which for a mutable relation is the last
        assignment at or before it."""
        c = self.chain(rel, args, upto=at_step)
        c = [f for f in c if f.slot != 'mem' or
             self.claim_scopes.get(f.fid, {}).get('temporal') == 'current']
        return c[-1] if c else None

    def keyed_in_force(self, rel: str, key_args: Tuple[Entity, ...], at_step: int) -> List[Fact]:
        """Every assignment in force for a functional relation under one key, so
        that a conflict is visible as more than one survivor."""
        r = self.relations[rel]
        out: Dict[Tuple[Entity, ...], Fact] = {}
        for fid in self._by_rel.get(rel, []):
            f = self.F[fid]
            if f.t > at_step:
                continue
            if f.slot == 'mem' and self.claim_scopes.get(f.fid, {}).get('temporal') != 'current':
                continue
            if tuple(f.args[i] for i in r.key) != key_args:
                continue
            out[f.args] = f
        return sorted(out.values(), key=lambda f: f.t)

    def by_step(self, t: int) -> List[Fact]:
        return [self.F[i] for i in self._by_step.get(t, [])]

    def by_entity(self, e: Entity) -> List[Fact]:
        return [self.F[i] for i in self._by_entity.get(str(e), [])]

    def by_rel(self, rel: str) -> List[Fact]:
        return [self.F[i] for i in self._by_rel.get(rel, [])]

    def length(self) -> int:
        return max((f.t for f in self.F), default=0)

    # ---------------- the two axes ------------------------------------
    def dependence_ok(self) -> bool:
        return all(self.F[b].t <= f.t for f in self.F for b in f.basis)

    def purpose_parent(self, e: Optional[Entity]) -> Optional[Entity]:
        if e is None or e.kind != "int":
            return None
        i = self.intentions.get(str(e))
        return i.purpose if i else None

    def purpose_chain(self, e: Optional[Entity], limit: int = 64) -> List[Entity]:
        out: List[Entity] = []
        seen = set()
        cur = e
        while cur is not None and len(out) < limit:
            if str(cur) in seen:
                raise ValueError(f"cycle in the purpose axis at {cur}")
            seen.add(str(cur))
            out.append(cur)
            cur = self.purpose_parent(cur)
        return out

    def summary(self) -> str:
        c = collections.Counter(f.slot for f in self.F)
        return (f"{self.tid}: |E|={len(self.E)} |F|={len(self.F)} steps={self.length()} "
                f"slots={dict(c)} goals={len(self.goals)}")
