"""The kind order.

An entity is a kind with an index. Kinds carry an is-a order so that a goal can
quantify over a class ("two creditcards") rather than over a name, and so that a
required value can be compared with an offered one by subsumption rather than by
string equality.

Nothing in this file is benchmark specific. A profile declares the kinds.
"""
from typing import Dict, Optional, Iterator


class Kinds:
    def __init__(self) -> None:
        self._parent: Dict[str, Optional[str]] = {}

    def declare(self, kind: str, parent: Optional[str] = None) -> str:
        if parent is not None and parent not in self._parent:
            raise KeyError(f"parent kind {parent!r} not declared before {kind!r}")
        prev = self._parent.get(kind, "<absent>")
        if prev != "<absent>" and prev != parent:
            raise ValueError(f"kind {kind!r} already declared with parent {prev!r}")
        self._parent[kind] = parent
        return kind

    def __contains__(self, kind: str) -> bool:
        return kind in self._parent

    def ancestors(self, kind: str) -> Iterator[str]:
        seen = set()
        cur: Optional[str] = kind
        while cur is not None:
            if cur in seen:
                raise ValueError(f"cycle in the kind order at {cur!r}")
            seen.add(cur)
            yield cur
            cur = self._parent.get(cur)

    def le(self, a: str, b: str) -> bool:
        """a is-a b."""
        if a not in self._parent:
            raise KeyError(f"undeclared kind {a!r}")
        if b not in self._parent:
            raise KeyError(f"undeclared kind {b!r}")
        return b in set(self.ancestors(a))

    def declared(self) -> Dict[str, Optional[str]]:
        return dict(self._parent)
