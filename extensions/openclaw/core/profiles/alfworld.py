"""ALFWorld profile for the typed trajectory IR.

The parser binds only environment-declared commands and canonical entity ids.
Portable object types are represented as values of object_type rather than as
an open-ended Python kind vocabulary.
"""
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kinds import Kinds
from relations import Relations, FUNCTIONAL, MUTABLE, OBSERVED
from record import Constraint, Entity, Goal, Record
from semantic_scope import memory_scope, decision_clause

name = "alfworld"

RECEPTACLES = tuple(
    "cabinet drawer countertop shelf desk sidetable diningtable coffeetable "
    "armchair sofa bed dresser garbagecan fridge microwave sinkbasin "
    "stoveburner toilet bathtubbasin handtowelholder towelholder safe "
    "laundryhamper tvstand ottoman coffeemachine desklamp toiletpaperhanger"
    .split()
)

_ENT = re.compile(r"\b([a-z]+)\s+(\d+)\b", re.I)
_NUM = {"two": 2, "three": 3, "some": 1, "a": 1, "an": 1, None: 1}
_STATE = {"clean": "clean", "cool": "cool", "cold": "cool",
          "heat": "hot", "hot": "hot"}


def kinds() -> Kinds:
    K = Kinds()
    K.declare("thing")
    K.declare("object", "thing")
    # Receptacles are physical objects too (e.g. a lamp on a desk).
    K.declare("place", "object")
    K.declare("portable", "object")
    K.declare("agent", "thing")
    K.declare("attribute")
    K.declare("value")
    K.declare("req")
    K.declare("int")
    for r in RECEPTACLES:
        K.declare(r, "place")
    return K


def relations() -> Relations:
    R = Relations()
    R.declare("at", 2, (FUNCTIONAL, MUTABLE), key=(0,),
              arg_kinds=("agent", "place"), value_types=("boolean",))
    R.declare("holding", 2, (FUNCTIONAL, MUTABLE, OBSERVED), key=(0,),
              arg_kinds=("agent", "portable"), value_types=("boolean",))
    R.declare("in", 2, (FUNCTIONAL, MUTABLE, OBSERVED), key=(0,),
              arg_kinds=("object", "object"), value_types=("boolean",))
    R.declare("open", 1, (MUTABLE,), key=(0,),
              arg_kinds=("place",), value_types=("boolean",))
    R.declare("has", 3, (FUNCTIONAL, MUTABLE), key=(0, 1),
              arg_kinds=("object", "attribute", "value"), value_types=("boolean",))
    R.declare("visible", 2, (MUTABLE,),
              arg_kinds=("thing", "place"), value_types=("boolean",))
    R.declare("effect", 1, (MUTABLE,), key=(0,),
              arg_kinds=("agent",), value_types=("string",))
    # Scoped absence observations; these never negate every world relation.
    R.declare("visible_empty", 1, (MUTABLE,), key=(0,),
              arg_kinds=("thing",), value_types=("boolean",))
    R.declare("inventory_empty", 1, (MUTABLE,), key=(0,),
              arg_kinds=("agent",), value_types=("boolean",))
    R.declare("admissibility", 1, (FUNCTIONAL,), key=(0,),
              arg_kinds=("int",), value_types=("string",))
    R.declare("go", 1, (MUTABLE,), arg_kinds=("place",), value_types=("string",))
    R.declare("take", 2, (MUTABLE,), arg_kinds=("portable", "place"),
              value_types=("string",))
    R.declare("put", 2, (MUTABLE,), arg_kinds=("portable", "place"),
              value_types=("string",))
    R.declare("open_action", 1, (MUTABLE,), arg_kinds=("place",),
              value_types=("string",))
    R.declare("close", 1, (MUTABLE,), arg_kinds=("place",),
              value_types=("string",))
    R.declare("look", 1, (MUTABLE,), arg_kinds=("thing",), value_types=("string",))
    R.declare("use", 1, (MUTABLE,), arg_kinds=("thing",), value_types=("string",))
    for rel in ("clean", "heat", "cool"):
        R.declare(rel, 2, (MUTABLE,), arg_kinds=("portable", "place"),
                  value_types=("string",))
    R.declare("slice", 2, (MUTABLE,), arg_kinds=("portable", "portable"),
              value_types=("string",))
    R.declare("examined_with", 2, (MUTABLE,),
              arg_kinds=("portable", "place"), value_types=("boolean",))
    R.declare("satisfied", 1, (FUNCTIONAL, MUTABLE), key=(0,),
              arg_kinds=("req",), value_types=("boolean", "null"))
    R.declare("recalled_nothing", 1, (MUTABLE,), key=(0,),
              arg_kinds=("agent",), value_types=("boolean",))
    R.declare("memory_unparsed", 1, (MUTABLE,), key=(0,),
              arg_kinds=("agent",), value_types=("boolean",))
    R.declare("recalled_summary", 1, (MUTABLE,), key=(0,),
              arg_kinds=("agent",), value_types=("string",))
    R.declare("assessed", 1, (MUTABLE,), key=(0,),
              arg_kinds=("agent",), value_types=("string",))
    R.declare("planned", 1, (MUTABLE,), key=(0,),
              arg_kinds=("agent",), value_types=("string",))
    return R


def null_verb() -> str:
    return "look"


def situating_relation() -> str:
    return "at"


def opens_a_line(verb: str) -> bool:
    return verb in {"go", "open_action", "look", "use"}


def closes_a_line(verb: str) -> bool:
    return verb in {"take", "put", "clean", "heat", "cool", "slice"}


def commits() -> set:
    return set()


def domain_briefing() -> Dict[str, Any]:
    return {
        "name": "alfworld",
        "task_shape": "locate objects, optionally transform them, then place or inspect them",
        "entity_rules": [
            "portable:bowl_1 is an instance; attribute:object_type=value:bowl records its type",
            "receptacle kinds such as shelf:1 and fridge:1 are places",
        ],
        "evidence_rules": [
            "seeing an object in a receptacle is not holding it",
            "holding an object is not placing it at the requested destination",
            "placement alone does not verify clean/cool/hot; missing state observations leave satisfaction unknown, not false",
            "Nothing happens is the observed effect of the previous action",
            "there is no irreversible commit; a cutoff can remain recoverable through its last action",
        ],
        "critical_questions": [
            "When was the required object first reachable?",
            "Did the plan preserve the required transformation and destination?",
            "Was the issued command admissible, and what did the next observation report?",
            "Did a later action repair the object, state, or destination?",
        ],
    }


def _constraint(prop: str, expected: Any, value_type: str, mode: str,
                span: str) -> Constraint:
    return Constraint(Entity("attribute", prop), "eq", expected,
                      value_type=value_type, mode=mode, span=span)


def parse_goal(task: str) -> List[Goal]:
    text = " ".join(task.strip().rstrip(".").split())
    low = text.lower()
    look = re.search(
        r"(?:look at|examine)\s+(?:the\s+)?(?P<obj>[a-z]+)\s+"
        r"(?:under|with)\s+(?:the\s+)?(?P<tool>[a-z]+)", low)
    if look:
        obj, tool = look.group("obj"), look.group("tool")
        return [Goal(
            count=1, of_kind="portable", rel="examined_with",
            target=Entity("place", tool), name=f"{obj}_with_{tool}",
            constraints=(
                _constraint("object_type", obj, "string", "observation", obj),
                _constraint("instrument_type", tool, "string", "selection", tool),
            ),
        )]

    count, obj, dest, state = 1, None, None, None
    m = re.search(
        r"find\s+(?P<q>two|three)\s+(?P<obj>[a-z]+)\s+and\s+"
        r"put\s+them\s+(?:in|on)\s+(?P<dest>[a-z]+)", low)
    if m:
        count = _NUM[m.group("q")]
        obj, dest = m.group("obj").rstrip("s"), m.group("dest")
    else:
        m = re.search(
            r"(?P<state>clean|cool|heat)\s+(?P<q>some|a|an)?\s*"
            r"(?P<obj>[a-z]+)\s+and\s+put\s+it\s+(?:in|on)\s+"
            r"(?P<dest>[a-z]+)", low)
        if m:
            count = _NUM[m.group("q")]
            obj, dest = m.group("obj").rstrip("s"), m.group("dest")
            state = _STATE[m.group("state")]
        else:
            m = re.search(
                r"(?:put|place)\s+(?P<q>two|three|some|a|an)?\s*"
                r"(?:(?P<state>clean|cool|cold|hot)\s+)?"
                r"(?P<obj>[a-z]+)\s+(?:in|on)\s+(?P<dest>[a-z]+)", low)
            if m:
                count = _NUM[m.group("q")]
                obj, dest = m.group("obj").rstrip("s"), m.group("dest")
                state = _STATE.get(m.group("state"))
    if not obj or not dest:
        return []
    specs = [
        _constraint("object_type", obj, "string", "observation", obj),
        _constraint("destination_type", dest, "string", "selection", dest),
    ]
    if state:
        specs.append(_constraint(state, True, "boolean", "observation", state))
    return [Goal(
        count=count, of_kind="portable", rel="in",
        target=Entity("place", dest), name=f"{obj}_in_{dest}",
        constraints=tuple(specs),
    )]


def _canon(kind: str, index: str) -> Tuple[str, str]:
    k, i = kind.lower(), str(index)
    if k in RECEPTACLES:
        return k, i
    return "portable", f"{k}_{i}"


def _portable_type(index: str) -> str:
    return index.rsplit("_", 1)[0].lower()


def _type_facts(kind: str, index: str, span: str) -> List[Dict[str, Any]]:
    ck, ci = _canon(kind, index)
    if ck != "portable":
        return []
    return [{
        "rel": "has",
        "args": [(ck, ci), ("attribute", "object_type"), ("value", kind.lower())],
        "value": True, "value_type": "boolean", "span": span,
    }]


def parse_observation(text: str, admissible: List[str]) -> List[Dict[str, Any]]:
    t = " ".join(text.split())
    low = t.lower()
    out: List[Dict[str, Any]] = []
    agent = ("agent", "self")
    if low.startswith("nothing happens"):
        return [{"rel": "effect", "args": [agent], "value": "none",
                 "value_type": "string", "span": t[:80]}]

    loc = (re.search(r"You arrive at (\w+) (\d+)", t, re.I)
           or re.search(r"You are facing the (\w+) (\d+)", t, re.I))
    if loc:
        place = _canon(loc.group(1), loc.group(2))
        out.append({"rel": "at", "args": [agent, place], "value": True,
                    "value_type": "boolean", "span": loc.group(0)})
        if "is closed" in low:
            out.append({"rel": "open", "args": [place], "value": False,
                        "value_type": "boolean", "span": "is closed"})

    opened = re.search(r"You open the (\w+) (\d+)", t, re.I)
    if opened:
        place = _canon(opened.group(1), opened.group(2))
        out.append({"rel": "open", "args": [place], "value": True,
                    "value_type": "boolean", "span": opened.group(0)})
    closed = re.search(r"You close the (\w+) (\d+)", t, re.I)
    if closed:
        place = _canon(closed.group(1), closed.group(2))
        out.append({"rel": "open", "args": [place], "value": False,
                    "value_type": "boolean", "span": closed.group(0)})

    pickup = re.search(
        r"You pick up the (\w+) (\d+) from the (\w+) (\d+)", t, re.I)
    if pickup:
        obj = _canon(pickup.group(1), pickup.group(2))
        src = _canon(pickup.group(3), pickup.group(4))
        out.extend(_type_facts(pickup.group(1), pickup.group(2),
                               f"{pickup.group(1)} {pickup.group(2)}"))
        out.append({"rel": "holding", "args": [agent, obj], "value": True,
                    "value_type": "boolean", "span": pickup.group(0)})
        out.append({"rel": "in", "args": [obj, src], "value": False,
                    "value_type": "boolean", "span": pickup.group(0)})

    moved = re.search(
        r"You (?:move|put) the (\w+) (\d+) (?:to|in|on) the (\w+) (\d+)", t, re.I)
    if moved:
        obj = _canon(moved.group(1), moved.group(2))
        dest = _canon(moved.group(3), moved.group(4))
        out.extend(_type_facts(moved.group(1), moved.group(2),
                               f"{moved.group(1)} {moved.group(2)}"))
        out.append({"rel": "in", "args": [obj, dest], "value": True,
                    "value_type": "boolean", "span": moved.group(0)})
        out.append({"rel": "holding", "args": [agent, obj], "value": False,
                    "value_type": "boolean", "span": moved.group(0)})

    transformed = re.search(
        r"You (clean|cool|heat) the (\w+) (\d+) using the (\w+) (\d+)", t, re.I)
    if transformed:
        state = _STATE[transformed.group(1).lower()]
        obj = _canon(transformed.group(2), transformed.group(3))
        out.extend(_type_facts(transformed.group(2), transformed.group(3),
                               f"{transformed.group(2)} {transformed.group(3)}"))
        out.append({
            "rel": "has",
            "args": [obj, ("attribute", state), ("value", "true")],
            "value": True, "value_type": "boolean", "span": transformed.group(0),
        })

    carrying = re.search(r"You are carrying:\s*(?:a |an )?(\w+) (\d+)", t, re.I)
    if carrying:
        obj = _canon(carrying.group(1), carrying.group(2))
        out.extend(_type_facts(carrying.group(1), carrying.group(2),
                               f"{carrying.group(1)} {carrying.group(2)}"))
        out.append({"rel": "holding", "args": [agent, obj], "value": True,
                    "value_type": "boolean", "span": carrying.group(0)})

    content = re.search(r"(?:On|In) the (\w+) (\d+), you see (.*?)\.?$", t, re.I)
    if content:
        place = _canon(content.group(1), content.group(2))
        body = content.group(3)
    else:
        implicit = re.search(r"In it, you see (.*?)\.?$", t, re.I)
        place = (_canon(opened.group(1), opened.group(2))
                 if implicit and opened else None)
        body = implicit.group(1) if implicit else ""
    if place is not None and body.strip().lower() != "nothing":
        for k, i in _ENT.findall(body):
            obj = _canon(k, i)
            if obj[0] == "portable":
                span = f"{k} {i}"
                out.extend(_type_facts(k, i, span))
                out.append({"rel": "in", "args": [obj, place], "value": True,
                            "value_type": "boolean", "span": span})

    if "middle of a room" in low:
        room = ("place", "room")
        out.append({"rel": "at", "args": [agent, room], "value": True,
                    "value_type": "boolean", "span": "middle of a room"})
        around = re.search(r"you see (.*?)(?:\. Your task|\.$)", t, re.I)
        if around:
            for k, i in _ENT.findall(around.group(1)):
                ent = _canon(k, i)
                out.extend(_type_facts(k, i, f"{k} {i}"))
                out.append({"rel": "visible", "args": [ent, room], "value": True,
                            "value_type": "boolean", "span": f"{k} {i}"})
    return out


_MEM_AT = re.compile(r"(?:at|to|reached|arrived at)\s+(?:the\s+)?(\w+)\s+(\d+)", re.I)
_MEM_IN = re.compile(
    r"(\w+)\s+(\d+)\s+(?:is|was|are|were)?\s*(?:in|on|inside)\s+"
    r"(?:the\s+)?(\w+)\s+(\d+)", re.I)
_MEM_HOLD = re.compile(
    r"(?:holding|carrying|picked up|took)\s+(?:the\s+)?(\w+)\s+(\d+)", re.I)


def parse_memory(text: str, known_values: Dict[str, List[str]] = None) -> List[Dict[str, Any]]:
    t = text or ""
    out: List[Dict[str, Any]] = []
    seen = set()
    def emit(match, rel, args):
        scope = memory_scope(t, match.start(), match.end())
        if scope is None:
            return
        key = (rel, tuple(args), scope['temporal'], scope['event_step'], scope['polarity'])
        if key in seen:
            return
        seen.add(key)
        out.append({'rel': rel, 'args': args, 'value': scope['polarity'] == 'positive',
                    'value_type': 'boolean', 'span': scope['source_context'], 'claim_scope': scope})
    for match in _MEM_IN.finditer(t):
        a, ai, b, bi = match.groups()
        obj, place = _canon(a, ai), _canon(b, bi)
        if obj[0] != "portable" or place[0] == "portable":
            continue
        emit(match, 'in', [obj, place])
    for match in _MEM_HOLD.finditer(t):
        a, ai = match.groups()
        obj = _canon(a, ai)
        if obj[0] != "portable":
            continue
        emit(match, 'holding', [('agent', 'self'), obj])
    for match in _MEM_AT.finditer(t):
        a, ai = match.groups()
        place = _canon(a, ai)
        if place[0] == "portable":
            continue
        before = t[:match.start()]
        # Looking at something is not being at it; a destination in a command
        # is not an arrival. Admit only explicit location/arrival assertions.
        if re.search(r'\b(?:look(?:ing)?|examine|point|aim)\s*$', before, re.I):
            continue
        word = match.group().lower().split()[0]
        if word == 'to' and not re.search(r'\b(?:went|moved|returned)\s*$', before, re.I):
            continue
        if word == 'at' and not (re.search(r'(?:^|[.:\n])\s*[\-\"\']*\s*$', before)
                or re.search(r'\b(?:arrive|arrived|am|was|were|is|are|currently|now)(?:\s+not)?\s*$', before, re.I)):
            continue
        emit(match, 'at', [('agent', 'self'), place])
    return out


_COMMAND = re.compile(
    r"\b(?:"
    r"go to (?:the )?[a-z]+ \d+|"
    r"(?:open|close|examine|use) (?:the )?[a-z]+ \d+|"
    r"take (?:the )?[a-z]+ \d+ from (?:the )?[a-z]+ \d+|"
    r"(?:move|put) (?:the )?[a-z]+ \d+ (?:to|in|on) (?:the )?[a-z]+ \d+|"
    r"(?:clean|heat|cool) (?:the )?[a-z]+ \d+ (?:with|using) (?:the )?[a-z]+ \d+|"
    r"slice (?:the )?[a-z]+ \d+ with (?:the )?[a-z]+ \d+"
    r")\b", re.I)

_VERB_ONLY = (
    (re.compile(r"\b(?:go(?: back)? to|move (?:towards?|to))\b", re.I), "go"),
    (re.compile(r"\b(?:take|pick up)\b", re.I), "take"),
    (re.compile(r"\b(?:move(?! (?:towards?|to)\b)|put|place)\b", re.I), "put"),
    (re.compile(r"\bopen\b", re.I), "open_action"),
    (re.compile(r"\bclose\b", re.I), "close"),
    (re.compile(r"\b(?:examine|inspect|look|check inventory)\b", re.I), "look"),
    (re.compile(r"\bclean\b", re.I), "clean"),
    (re.compile(r"\b(?:heat|warm)\b", re.I), "heat"),
    (re.compile(r"\bcool\b", re.I), "cool"),
    (re.compile(r"\bslice\b", re.I), "slice"),
)


def _command(raw: str) -> Dict[str, Any]:
    norm = " ".join(raw.strip("'\" ").lower().split())
    ents = [_canon(k, i) for k, i in _ENT.findall(norm)]
    if norm.startswith("go to ") and ents:
        return {"rel": "go", "args": [ents[0]], "value": norm}
    if norm.startswith("open ") and ents:
        return {"rel": "open_action", "args": [ents[0]], "value": norm}
    if norm.startswith("close ") and ents:
        return {"rel": "close", "args": [ents[0]], "value": norm}
    if norm.startswith("take ") and len(ents) >= 2:
        return {"rel": "take", "args": ents[:2], "value": norm}
    if (norm.startswith("move ") or norm.startswith("put ")) and len(ents) >= 2:
        return {"rel": "put", "args": ents[:2], "value": norm}
    if norm.startswith("examine ") and ents:
        return {"rel": "look", "args": [ents[0]], "value": norm}
    if norm.startswith("use ") and ents:
        return {"rel": "use", "args": [ents[0]], "value": norm}
    for verb in ("clean", "heat", "cool", "slice"):
        if norm.startswith(verb + " ") and len(ents) >= 2:
            return {"rel": verb, "args": ents[:2], "value": norm}
    return {"rel": "look", "args": [("agent", "self")], "value": norm}


def parse_action(text: Optional[str], admissible: List[str]) -> Optional[Dict[str, Any]]:
    if text is None:
        return {"rel": "effect", "args": [("agent", "self")], "value": "none",
                "value_type": "string", "admissibility": "no_tag", "span": ""}
    raw = text.strip()
    norm = " ".join(raw.strip("'\" ").lower().split())
    adm = {" ".join(a.lower().split()) for a in admissible}
    cls = "well_formed" if norm in adm and raw == norm else (
        "quoted" if norm in adm else "not_admissible")
    item = _command(norm)
    item.update({"admissibility": cls, "span": raw, "value_type": "string"})
    return item


def _goal_expected(goal: Goal, prop: str) -> Optional[Any]:
    for c in goal.constraints:
        if isinstance(c, Constraint) and c.property.index == prop:
            return c.expected
    return None


def _preconditions(verb: str) -> Tuple[str, ...]:
    return {
        "go": ("action_admissible", "target_accessible"),
        "open_action": ("action_admissible", "target_visible"),
        "close": ("action_admissible", "target_visible"),
        "look": ("action_admissible",),
        "take": ("action_admissible", "object_visible", "source_visible", "hand_free"),
        "put": ("action_admissible", "object_held", "target_visible"),
        "clean": ("action_admissible", "object_held", "tool_or_appliance_available"),
        "heat": ("action_admissible", "object_held", "tool_or_appliance_available"),
        "cool": ("action_admissible", "object_held", "tool_or_appliance_available"),
        "slice": ("action_admissible", "object_held", "tool_or_appliance_available"),
        "use": ("action_admissible", "target_visible"),
    }.get(verb, ("action_admissible",))


def _effects(verb: str) -> Tuple[str, ...]:
    return {
        "go": ("location_changed",),
        "open_action": ("container_opened",),
        "close": ("container_closed",),
        "look": ("observation_refreshed",),
        "take": ("object_held",),
        "put": ("object_placed",),
        "clean": ("clean_state_recorded",),
        "heat": ("hot_state_recorded",),
        "cool": ("cool_state_recorded",),
        "slice": ("sliced_state_recorded",),
        "use": ("state_change_observed",),
    }.get(verb, ())


def parse_intention(plan_text: Optional[str], admissible: List[str],
                    goals: Iterable[Goal] = ()) -> Optional[Dict[str, Any]]:
    if not plan_text:
        return None
    text = decision_clause(plan_text)
    if not text:
        return {'rel': None, 'args': None, 'serves': tuple(g.entity() for g in goals),
                'extraction': 'unresolved_decision_scope', 'confidence': 0.0}
    low = text.lower()
    # Use the first planning declaration.  Taking the last occurrence lets a
    # later rationale ("this should help") hide the actual intended operation
    # stated near the beginning of the slot.
    anchors = [low.find(x) for x in
               ("next step", "should", "plan is to", "plan to", "intend to")]
    anchors = [x for x in anchors if x >= 0]
    anchor = min(anchors) if anchors else -1
    matches = list(_COMMAND.finditer(text))
    if not matches or matches[0].start() != 0:
        focus = text[max(0, anchor):] if anchor >= 0 else text
        candidates = [(m.start(), verb) for pattern, verb in _VERB_ONLY
                      for m in pattern.finditer(focus) if m.start() == 0]
        if not candidates:
            return {
                "rel": None, "args": None, "parameters": (), "execution_options": (),
                "serves": tuple(g.entity() for g in goals), "mentions": (),
                "preconditions": (), "expected_effects": (), "commitment": "unknown",
                "extraction": "unbound_plan", "confidence": 0.35,
            }
        _, verb = min(candidates)
        commitment = (
            "navigate" if verb == "go" else
            "inspect" if verb in {"open_action", "close", "look", "use"} else
            "select" if verb == "take" else
            "commit" if verb in {"put", "clean", "heat", "cool", "slice"} else "unknown"
        )
        return {
            "rel": verb, "args": None, "parameters": (), "execution_options": (),
            "serves": tuple(g.entity() for g in goals), "mentions": (),
            "preconditions": _preconditions(verb), "expected_effects": _effects(verb),
            "commitment": commitment, "extraction": "decision_clause:verb_only",
            "confidence": 0.85,
        }
    after = [m for m in matches if m.start() >= anchor] if anchor >= 0 else []
    direct_match = matches[0]
    direct = _command(direct_match.group(0))
    verb, args = direct["rel"], direct["args"]
    options = []
    for m in matches:
        if m is direct_match:
            continue
        if not re.search(r'\bor\s+[\"\']*$', text[direct_match.end():m.start()], re.I):
            continue
        parsed = _command(m.group(0))
        lo, hi = sorted((m.end(), direct_match.end()))
        role = "alternative" if " or " in low[lo:hi] else (
            "prerequisite" if m.start() < direct_match.start() else "follow_up")
        options.append({
            "verb": parsed["rel"], "args": parsed["args"], "role": role,
            "span": m.group(0), "preconditions": _preconditions(parsed["rel"]),
            "expected_effects": _effects(parsed["rel"]),
        })
    params = []
    if args:
        names = (["target"] if len(args) == 1 else
                 ["object", "source" if verb == "take" else
                  "destination" if verb == "put" else "instrument"])
        for param_name, arg in zip(names, args):
            params.append({"name": param_name, "value": f"{arg[0]}:{arg[1]}",
                           "value_type": "entity", "span": direct_match.group(0)})
    expected_tokens = {
        str(c.expected).lower(): g.entity()
        for g in goals for c in g.constraints if isinstance(c, Constraint)
    }
    mentions = tuple(dict.fromkeys(
        q for token, q in expected_tokens.items() if token and token in low))
    serves = tuple(g.entity() for g in goals)
    commitment = (
        "navigate" if verb == "go" else
        "inspect" if verb in {"open_action", "close", "look", "use"} else
        "select" if verb == "take" else
        "commit" if verb in {"put", "clean", "heat", "cool", "slice"} else "unknown"
    )
    adm_norm = {" ".join(a.lower().split()) for a in admissible}
    exact = " ".join(direct_match.group(0).lower().split()) in adm_norm
    # A named manipulation/inspection may first require navigating to its
    # explicit source, destination or instrument. Ground this option in the
    # offered controls, never in the action that was actually emitted.
    if not exact and verb in {'open_action', 'close', 'look', 'use', 'take', 'put', 'clean', 'cool', 'heat'}:
        destination = args[-1] if args else None
        if destination and destination[0] in RECEPTACLES:
            navigation = f'go to {destination[0]} {destination[1]}'
            if navigation in adm_norm:
                options.append({'verb': 'go', 'args': [destination], 'role': 'prerequisite',
                                'span': direct_match.group(0), 'preconditions': _preconditions('go'),
                                'expected_effects': _effects('go')})
    return {
        "rel": verb, "args": args, "parameters": tuple(params),
        "execution_options": tuple(options), "serves": serves,
        "mentions": mentions, "preconditions": _preconditions(verb),
        "expected_effects": _effects(verb), "commitment": commitment,
        "extraction": "decision_clause:full",
        "confidence": 0.98 if exact else 0.82,
    }


def decompose(plan_text: Optional[str]) -> List[str]:
    text = " ".join((plan_text or "").split())
    return list(dict.fromkeys(m.group(0).lower() for m in _COMMAND.finditer(text)))


def bears_on(item: Dict[str, Any], goals: Iterable[Goal]) -> Optional[Entity]:
    args = item.get("args", ())
    for goal in goals:
        obj = str(_goal_expected(goal, "object_type") or "").lower()
        dest = str(_goal_expected(goal, "destination_type") or "").lower()
        states = {c.property.index for c in goal.constraints
                  if isinstance(c, Constraint) and c.value_type == "boolean"}
        for kind, index in args:
            if kind == "portable" and _portable_type(index) == obj:
                return goal.entity()
            if kind == "value" and str(index).lower() == obj:
                return goal.entity()
            if dest and (kind == dest or (kind == "place" and index == dest)):
                return goal.entity()
            if kind == "attribute" and index in states:
                return goal.entity()
    return None


def evaluate_discharges(action: Dict[str, Any], goals: Iterable[Goal],
                        record: Record, step: int) -> List[Dict[str, Any]]:
    if action.get("rel") != "put" or len(action.get("args", ())) < 2:
        return []
    obj = record.ent(*action["args"][0])
    dest = record.ent(*action["args"][1])
    out = []
    for goal in goals:
        if goal.rel != "in":
            continue
        wanted_obj = str(_goal_expected(goal, "object_type") or "").lower()
        wanted_dest = str(_goal_expected(goal, "destination_type") or "").lower()
        object_ok = obj.kind == "portable" and _portable_type(obj.index) == wanted_obj
        destination_ok = dest.kind == wanted_dest or (
            dest.kind == "place" and dest.index == wanted_dest)
        state_ok, state_evidence = True, []
        for c in goal.constraints:
            if not isinstance(c, Constraint) or c.value_type != "boolean":
                continue
            attr = record.ent("attribute", c.property.index)
            val = record.ent("value", "true")
            fact = record.in_force("has", (obj, attr, val), step)
            # Missing observation is unknown, never evidence that an object is
            # dirty/cold/etc. Keep explicit negatives distinct from absence.
            ok = fact.value if fact is not None and type(fact.value) is bool else None
            state_ok = False if state_ok is False or ok is False else (None if ok is None or state_ok is None else True)
            if fact is not None:
                state_evidence.append(fact.fid)
        met = False if not object_ok or not destination_ok else state_ok
        out.append({
            # This is a goal verification, not an observation of location.
            # A dirty bowl successfully placed on a shelf is still on the shelf.
            "rel": "satisfied", "args": [(goal.entity().kind, goal.entity().index)],
            "value": met,
            "value_type": "null" if met is None else "boolean", "purpose": goal.entity(),
            "span": action.get("span", ""), "basis": tuple(state_evidence),
            "components": {"object": object_ok, "destination": destination_ok,
                           "state": state_ok},
        })
    return out
