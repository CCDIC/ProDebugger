"""Minimal trajectory DSL for compilation experiments.

Entities, kinds, relations, facts, constraints, goals, records, and derived
evidence live here with their registries and validation vocabulary. Benchmark
parsing belongs in benchmark_parser.py; detection lives in abstract_rules.py.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, Iterator, List, Optional, Tuple


@dataclass(frozen=True)
class Kind:
    """A named entity type with an optional parent type."""
    name: str
    parent: Optional[str] = None


class Kinds:
    """Registry and is-a order for declared kinds."""

    def __init__(self) -> None:
        self._kinds: Dict[str, Kind] = {}

    def declare(self, kind: str, parent: Optional[str] = None) -> Kind:
        if parent is not None and parent not in self._kinds:
            raise KeyError(f"parent kind {parent!r} not declared before {kind!r}")
        definition = Kind(kind, parent)
        previous = self._kinds.get(kind)
        if previous is not None and previous != definition:
            raise ValueError(f"kind {kind!r} already declared with parent {previous.parent!r}")
        self._kinds[kind] = definition
        return definition

    def __getitem__(self, kind: str) -> Kind:
        return self._kinds[kind]

    def __contains__(self, kind: str) -> bool:
        return kind in self._kinds

    def ancestors(self, kind: str) -> Iterator[str]:
        if kind not in self._kinds:
            raise KeyError(f"undeclared kind {kind!r}")
        seen = set()
        current: Optional[str] = kind
        while current is not None:
            if current in seen:
                raise ValueError(f"cycle in the kind order at {current!r}")
            seen.add(current)
            yield current
            current = self._kinds[current].parent

    def le(self, a: str, b: str) -> bool:
        """Whether a is-a b."""
        if b not in self._kinds:
            raise KeyError(f"undeclared kind {b!r}")
        return b in self.ancestors(a)

    def declared(self) -> Dict[str, Kind]:
        return dict(self._kinds)


FUNCTIONAL = "functional"   # the value is determined by the key positions
MUTABLE = "mutable"         # it may be re-established later by an event
SYMMETRIC = "symmetric"
TRANSITIVE = "transitive"
OBSERVED = "observed"       # all shown instances are recorded; absence is negation
PROPERTIES = frozenset({FUNCTIONAL, MUTABLE, SYMMETRIC, TRANSITIVE, OBSERVED})


@dataclass(frozen=True)
class Relation:
    name: str
    arity: int
    props: FrozenSet[str]
    key: Tuple[int, ...]   # argument positions that determine the value
    # Empty means unconstrained. Otherwise Record.add verifies each
    # argument by kind subsumption before admitting the fact.
    arg_kinds: Tuple[Optional[str], ...] = ()
    value_types: FrozenSet[str] = frozenset({"any"})

    def is_functional(self) -> bool:
        return FUNCTIONAL in self.props

    def is_mutable(self) -> bool:
        return MUTABLE in self.props

    def is_observed(self) -> bool:
        return OBSERVED in self.props

    def version_positions(self) -> Tuple[int, ...]:
        """Argument positions that carry the version rather than the key. Where
        they exist the value lives in the arguments, and two facts are compared
        on these; where they do not, the payload is the fact's own value."""
        return tuple(i for i in range(self.arity) if i not in self.key)


class Relations:
    def __init__(self) -> None:
        self._r: Dict[str, Relation] = {}

    def declare(self, name: str, arity: int, props=(), key=None,
                arg_kinds=None, value_types=("any",)) -> Relation:
        bad = set(props) - PROPERTIES
        if bad:
            raise ValueError(f"unknown properties {sorted(bad)} on {name!r}")
        if key is None:
            key = tuple(range(arity))          # the whole tuple keys the value
        if any(i < 0 or i >= arity for i in key):
            raise ValueError(f"key positions {key} out of range for arity {arity}")
        if arg_kinds is not None and len(arg_kinds) != arity:
            raise ValueError(f"arg_kinds for {name!r} must have arity {arity}")
        if name in self._r:
            raise ValueError(f"relation {name!r} declared twice")
        rel = Relation(name, arity, frozenset(props), tuple(key),
                       tuple(arg_kinds or ()), frozenset(value_types))
        self._r[name] = rel
        return rel

    def __getitem__(self, name: str) -> Relation:
        if name not in self._r:
            raise KeyError(f"undeclared relation {name!r}")
        return self._r[name]

    def __contains__(self, name: str) -> bool:
        return name in self._r

    def declared(self):
        return dict(self._r)


SLOTS = ("obs", "mem", "refl", "plan", "act", "chk")
SIDES = ("env", "mind", "task", "dbg")

RULE_TYPES = ("Fact", "Entity", "Goal", "Constraint")
RULE_SCOPES = ("step", "prefix", "response", "trajectory")


@dataclass(frozen=True)
class Rule:
    """Abstract rule data. Symbolic descriptions are stored, never executed."""
    id: str
    variables: Dict[str, str]
    scope: str
    trigger: str
    applicability: str
    property: str
    evidence_requirements: Tuple[str, ...]
    exceptions: Tuple[str, ...]
    witness_builder: str

    def __post_init__(self):
        names = (self.id, self.trigger, self.applicability, self.property, self.witness_builder)
        if any(not isinstance(v, str) or not v.strip() for v in names):
            raise ValueError("rule identifiers/descriptions must be nonempty strings")
        if self.scope not in RULE_SCOPES:
            raise ValueError("unknown rule scope")
        if not isinstance(self.variables, dict) or not self.variables or any(
                not isinstance(k, str) or not k or v not in RULE_TYPES
                for k, v in self.variables.items()):
            raise ValueError("invalid rule variables")
        if any(not isinstance(v, str) or not v.strip()
               for v in (*self.evidence_requirements, *self.exceptions)):
            raise ValueError("rule requirements/exceptions must be nonempty strings")

    @classmethod
    def from_dict(cls, definition):
        data = dict(definition)
        data["variables"] = dict(data["variables"])
        data["evidence_requirements"] = tuple(data["evidence_requirements"])
        data["exceptions"] = tuple(data["exceptions"])
        return cls(**data)


@dataclass(frozen=True)
class Entity:
    kind: str
    index: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.index}"


CONSTRAINT_OPERATORS = (
    "eq", "neq", "lt", "le", "gt", "ge", "contains", "subset",
    "member_of", "string_match", "semantic_match",
)


CONSTRAINT_VALUE_TYPES = (
    "boolean", "number", "money", "string", "text", "enum", "entity", "set",
)


CONSTRAINT_MODES = ("selection", "observation", "semantic")


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
class Evidence:
    """A derived finding whose refs index facts in the same Record.F."""
    eid: int
    kind: str
    t: int
    refs: Tuple[int, ...]
    status: str = "candidate"
    rule: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if type(self.eid) is not int or self.eid < 0:
            raise ValueError("evidence eid must be a nonnegative integer")
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("evidence kind must be a nonempty string")
        if type(self.t) is not int or self.t < 0:
            raise ValueError("evidence t must be a nonnegative integer")
        if self.status not in ("candidate", "confirmed", "dismissed"):
            raise ValueError("unknown evidence status")
        if not self.refs or any(type(fid) is not int or fid < 0 for fid in self.refs):
            raise ValueError("evidence refs must contain nonnegative Fact indices")
        self.refs = tuple(dict.fromkeys(self.refs))


@dataclass
class Evidences:
    """Same-kind findings, preserving each event and exposing merged refs."""
    kind: str
    items: List[Evidence] = field(default_factory=list)

    def __post_init__(self):
        if any(item.kind != self.kind for item in self.items):
            raise ValueError("only same-kind evidence can be grouped")

    def add(self, evidence: Evidence) -> None:
        if evidence.kind != self.kind:
            raise ValueError("only same-kind evidence can be grouped")
        self.items.append(evidence)

    @property
    def refs(self) -> Tuple[int, ...]:
        return tuple(sorted({fid for item in self.items for fid in item.refs}))


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
    # attrs carries relational payload; constraints define how observed values
    # are compared with the task requirement.
    constraints: Tuple[Constraint, ...] = ()
    # `tracked` requirements participate in the benchmark's terminal success
    # test. `unmodeled` requirements remain first-class task constraints but
    # cannot yet be discharged deterministically from this profile's facts.
    status_mode: str = "tracked"

    def __post_init__(self):
        if self.status_mode not in ("tracked", "unmodeled"):
            raise ValueError("goal status_mode must be tracked or unmodeled")

    def entity(self) -> Entity:
        return Entity("req", self.name or f"{self.rel}_{self.of_kind}")


UNBOUND = "*"  # an entity argument left unnamed by the source


class Record:
    """A trajectory containing declarations, entities, facts, and goals."""

    def __init__(self, tid: str, kinds: Kinds, relations: Relations,
                 task: str = "", goals: Iterable[Goal] = ()):
        self.tid = tid
        self.kinds = kinds
        self.relations = relations
        self.task = task
        self.goals: List[Goal] = list(goals)
        self.E: Dict[str, Entity] = {}
        self.F: List[Fact] = []
        self.evidences: Dict[str, Evidences] = {}
        self._next_eid = 0
        self._chain: Dict[Tuple[str, Tuple[Entity, ...]], List[int]] = collections.defaultdict(list)
        self._by_entity: Dict[str, List[int]] = collections.defaultdict(list)
        self._by_step: Dict[int, List[int]] = collections.defaultdict(list)
        self._by_rel: Dict[str, List[int]] = collections.defaultdict(list)
        for g in self.goals:
            self.ent(g.entity().kind, g.entity().index)

    def ent(self, kind: str, index: str) -> Entity:
        if kind not in self.kinds:
            raise KeyError(f"undeclared kind {kind!r}")
        e = Entity(kind, str(index))
        self.E.setdefault(str(e), e)
        return e

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

    def add_evidence(self, kind: str, t: int, refs: Iterable[int],
                     status: str = "candidate", rule: Optional[str] = None,
                     details: Optional[Dict[str, Any]] = None) -> Evidence:
        evidence = Evidence(self._next_eid, kind, t, tuple(refs), status, rule,
                            dict(details or {}))
        for fid in evidence.refs:
            if fid >= len(self.F):
                raise IndexError(f"evidence Fact index {fid} does not exist")
            if self.F[fid].t > evidence.t:
                raise ValueError("evidence must not reference a future fact")
        group = self.evidences.setdefault(kind, Evidences(kind))
        group.add(evidence)
        self._next_eid += 1
        return evidence

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

    def dependence_ok(self) -> bool:
        return all(self.F[b].t <= f.t for f in self.F for b in f.basis)

    def summary(self) -> str:
        c = collections.Counter(f.slot for f in self.F)
        return (f"{self.tid}: |E|={len(self.E)} |F|={len(self.F)} steps={self.length()} "
                f"slots={dict(c)} goals={len(self.goals)}")


__all__ = [
    "Fact", "Constraint", "Goal", "Entity", "Relation", "Kind", "Record",
    "Evidence", "Evidences", "Rule", "RULE_TYPES", "RULE_SCOPES",
    "Kinds", "Relations", "SLOTS", "SIDES", "UNBOUND",
    "CONSTRAINT_OPERATORS", "CONSTRAINT_VALUE_TYPES", "CONSTRAINT_MODES",
    "FUNCTIONAL", "MUTABLE", "SYMMETRIC", "TRANSITIVE", "OBSERVED", "PROPERTIES",
]
