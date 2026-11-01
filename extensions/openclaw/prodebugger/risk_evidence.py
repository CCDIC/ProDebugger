"""Small exact-fragment adapter over the existing SOURCE query implementation.

Indexing locates a candidate field. Only a successful original SOURCE query
turns it into evidence. No inferred data-flow/causal edges enter the Record.
"""
from __future__ import annotations

import json
import re

from .safety_audit import _Evidence, _locations
from .query_dsl import EXCERPT_CHARS, match_position


MAX_NEARBY_ANCHORS = 16


def _nearby_anchors(text, lo, hi, path, positions):
    """Bounded original JSON hints; only SOURCE can turn them into evidence.

    A duplicated long string can place SOURCE's first match in another member.
    Look at actual lexical boundaries in the immediate enclosing JSON value,
    near the requested fragment. A sibling value/key boundary can distinguish
    the correct copy without changing SOURCE or trusting decoded offsets.
    """
    parent = path[:-1]
    start, end, _ = positions[parent]
    left, right = max(start, lo - EXCERPT_CHARS), min(end, hi + 90)
    boundaries = {lo, hi}
    for member, offsets in positions.items():
        if member[:len(parent)] == parent:
            boundaries.update(value for value in offsets if left <= value < right)
    seen = set()
    for boundary in sorted(boundaries, key=lambda value: (abs(value - lo), value)):
        if not left <= boundary < right:
            continue
        for length in (12, 24, 48, 96, 120):
            needle = text[boundary:min(end, boundary + length)].strip()
            if (needle in seen or not 3 <= len(needle) <= 120
                    or len(re.sub(r"\W", "", needle)) < 3 or re.search(r"[%_*]", needle)):
                continue
            # Prediction only saves failed queries. The original query below
            # must still return a covering source span and the exact fragment.
            first = match_position(text, needle)
            excerpt_start = max(0, first - 90)
            if first < 0 or not excerpt_start <= lo < hi <= min(len(text), excerpt_start + EXCERPT_CHARS):
                continue
            seen.add(needle)
            yield needle
            if len(seen) >= MAX_NEARBY_ANCHORS:
                return


def pointer_parts(pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("expected a JSON pointer")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")] if pointer else []


def pointer(value, expression):
    for part in pointer_parts(expression):
        if isinstance(value, list):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise KeyError(expression)
    return value


def located_path(value, parts):
    result = []
    for part in parts:
        key = int(part) if isinstance(value, list) else part
        value = value[key]
        result.append(key)
    return tuple(result)


class RiskEvidence:
    def __init__(self, bundle, report):
        self.original = _Evidence(bundle, report)

    def _get(self, fact, module, text, lo, hi, identity, path, positions):
        pointer = "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in path)
        proof = self.original.get(fact.t, module, text, lo, hi, identity, pointer)
        if proof:
            return proof if proof.get("parse_status") != "unresolved" else None
        # Tool results often retain the same value in both text content and a
        # details object. SOURCE returns the first literal occurrence. Query an
        # actual quoted JSON member/ancestor instead of silently accepting the
        # first occurrence as evidence for a different field.
        candidates = []
        for depth in range(len(path), 0, -1):
            start, end, member_start = positions[path[:depth]]
            candidates.extend((text[start:end], text[member_start:start].strip()))
        candidates.extend(_nearby_anchors(text, lo, hi, path, positions))
        for needle in dict.fromkeys(candidates):
            if not (3 <= len(needle) <= 120 and len(re.sub(r"\W", "", needle)) >= 3
                    and not re.search(r"[%_*]", needle)):
                continue
            self.original.cache.pop((fact.t, module, lo, hi), None)
            proof = self.original.get(fact.t, module, text, lo, hi, identity, pointer, needle)
            if proof:
                return proof if proof.get("parse_status") != "unresolved" else None
        return None

    def member(self, fact, parts, *, substring=None):
        """A real JSON member or exact substring of its original lexical token.

        Escaped strings are proven in the original JSON form. A long field
        without a bounded, unique fragment stays unavailable, never guessed.
        """
        try:
            text = fact.value
            data = json.loads(text)
            path = located_path(data, parts)
            positions = _locations(text)
            module = "action" if fact.rel == "tool_call" else "observation"
            identity = (data["trace_id"], data["span_id"], data.get("tool"))
            if substring is None:
                _, hi, lo = positions[path]
                return self._get(fact, module, text, lo, hi, identity, path, positions)
            start, end, _ = positions[path]
            raw = text[start:end]
            encodings = list(dict.fromkeys(json.dumps(str(substring), ensure_ascii=ascii_)[1:-1]
                                           for ascii_ in (False, True)))
            matches = []
            for encoded in encodings:
                # This is not a fuzzy/decoded-offset match. The source JSON
                # token must contain exactly this recorded byte-for-character form.
                if encoded and raw.count(encoded) == 1:
                    offset = raw.index(encoded)
                    matches.append((start + offset, start + offset + len(encoded)))
            matches = list(dict.fromkeys(matches))
            if len(matches) != 1:
                return None
            lo, hi = matches[0]
            return self._get(fact, module, text, lo, hi, identity, path, positions)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            return None

    def argument(self, fact, field, *, substring=None):
        return self.member(fact, ("arguments", field), substring=substring)

    def result(self, fact, expression, *, substring=None):
        return self.member(fact, ("result", *pointer_parts(expression)), substring=substring)
