"""The relation registry.

A relation declares its arity and its algebraic properties. Properties are not
documentation. FUNCTIONAL is what makes a contradiction derivable from the arity
rather than from a judgement: if at(agent, .) is functional then two facts
asserting it with different values, with no mutating event between them, conflict.

Nothing in this file is benchmark specific. A profile declares the relations.
"""
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Tuple

FUNCTIONAL = "functional"   # the value is determined by the key positions
MUTABLE = "mutable"         # it may be re-established later by an event
SYMMETRIC = "symmetric"
TRANSITIVE = "transitive"
OBSERVED = "observed"       # the record holds every instance the run was shown,
                            # so an absence is a negation and not an unknown
PROPERTIES = frozenset({FUNCTIONAL, MUTABLE, SYMMETRIC, TRANSITIVE, OBSERVED})


@dataclass(frozen=True)
class Relation:
    name: str
    arity: int
    props: FrozenSet[str]
    key: Tuple[int, ...]   # argument positions that determine the value
    # Empty means legacy/unconstrained. Otherwise Record.add verifies each
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
        if FUNCTIONAL in props and len(key) == arity and arity > 0:
            # a fully keyed functional relation is a flag, which is allowed, but
            # the common case is a proper key, so make the intent explicit.
            pass
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
