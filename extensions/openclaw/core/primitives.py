"""Four composable primitives.

Each maps a set of facts to a set of facts, so they compose. The composition is
what answers a real question: what a thing was used for is `about` then
`purpose`; where a claim came from is `about` then `why`.

A primitive retrieves and arranges. It decides nothing, and it holds no
threshold.
"""
from typing import Callable, Iterable, List, Optional, Set

from record import Record, Fact, Entity

Lens = Callable[[Record, List[Fact]], List[Fact]]


def _uniq(fs: Iterable[Fact]) -> List[Fact]:
    seen: Set[int] = set()
    out: List[Fact] = []
    for f in fs:
        if f.fid in seen:
            continue
        seen.add(f.fid)
        out.append(f)
    return sorted(out, key=lambda f: (f.t, f.fid))


def about(e: Entity) -> Lens:
    """Every fact that mentions this entity, as an argument or as a purpose.
    Ignores the incoming set, so this is the usual head of a composition."""
    def go(R: Record, _: List[Fact]) -> List[Fact]:
        out = list(R.by_entity(e))
        out += [f for f in R.F if f.purpose is not None and str(f.purpose) == str(e)]
        return _uniq(out)
    return go


def why(depth: int = 1 << 30) -> Lens:
    """Walk the dependence axis backwards from each fact, transitively."""
    def go(R: Record, fs: List[Fact]) -> List[Fact]:
        seen: Set[int] = set()
        order: List[Fact] = []
        stack = [f.fid for f in fs]
        while stack and len(order) < depth:
            i = stack.pop()
            if i in seen:
                continue
            seen.add(i)
            f = R.fact(i)
            order.append(f)
            stack.extend(reversed(f.basis))
        return _uniq(order)
    return go


def purpose(to_requirement: bool = True) -> Lens:
    """Walk the purpose axis upward. With to_requirement, follow intentions until
    a requirement is reached; otherwise take one step."""
    def go(R: Record, fs: List[Fact]) -> List[Fact]:
        out: List[Fact] = []
        for f in fs:
            p = f.purpose
            if p is None:
                continue
            chain = R.purpose_chain(p) if to_requirement else [p]
            for e in chain:
                out.extend(R.by_entity(e))
        return _uniq(out)
    return go


def window(t1: int, t2: int) -> Lens:
    def go(R: Record, fs: List[Fact]) -> List[Fact]:
        return _uniq(f for f in fs if t1 <= f.t <= t2)
    return go


def compose(*lenses: Lens) -> Lens:
    def go(R: Record, fs: Optional[List[Fact]] = None) -> List[Fact]:
        cur = list(fs or [])
        for l in lenses:
            cur = l(R, cur)
        return cur
    return go


def run(R: Record, *lenses: Lens) -> List[Fact]:
    return compose(*lenses)(R, [])


def entity_facts(R: Record, e: Entity) -> List[Fact]:
    """Facts that mention the entity, including as a purpose. A requirement is
    rarely an argument of anything; it is what facts point at."""
    out = list(R.by_entity(e))
    out += [f for f in R.F if f.purpose is not None and str(f.purpose) == str(e)]
    return _uniq(out)
