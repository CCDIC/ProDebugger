"""Complete domain grammars with explicit binding and token consumption.

No substring admission: every clause/token must belong to a recognized production.
Known template-local pronouns and scoped negatives are interpreted by that production;
unrecognized or ambiguous language raises NeedsBackoff before facts are admitted.
"""
import re
from .record_bridge import plain
from record import Entity, Goal, Constraint

VERSION = "complete-domain-templates/v1"


class NeedsBackoff(ValueError):
    pass


def fact(rel, args, value, span, value_type=None):
    return {"rel": rel, "args": args, "value": value, "span": span,
            "value_type": value_type or ("boolean" if type(value) is bool else "string")}


def canon(profile, noun, index):
    return profile._canon(noun, index)


def object_type(profile, entity, span):
    if entity[0] != "portable":
        return []
    noun = entity[1].rsplit("_", 1)[0]
    return [fact("has", [entity, ("attribute", "object_type"), ("value", noun)], True, span)]


def alf_task(text, profile):
    # Singular noun slots are explicit. Ambiguous plural morphology goes to LLM.
    noun = r"([a-z]+)"
    patterns = [
        (rf"(?:put|place) (a|an|some|two|three) (?:(clean|cool|cold|hot) )?{noun} (?:in|on) {noun}\.?", "put"),
        (rf"find (two|three) {noun} and put them (?:in|on) {noun}\.?", "find"),
        (rf"(clean|cool|heat) (a|an|some) {noun} and put it (?:in|on) {noun}\.?", "transform"),
        (rf"(?:look at|examine) (?:the )?{noun} (?:under|with) (?:the )?{noun}\.?", "examine"),
    ]
    for pattern, mode in patterns:
        m = re.fullmatch(pattern, text.strip(), re.I)
        if not m:
            continue
        state = None
        if mode == "put":
            quantity, state, obj, target = m.groups()
        elif mode == "find":
            quantity, obj, target = m.groups()
        elif mode == "transform":
            state, quantity, obj, target = m.groups()
        else:
            obj, target = m.groups(); quantity = "a"
        quantity, obj, target = quantity.lower(), obj.lower(), target.lower()
        count = {"a": 1, "an": 1, "some": 1, "two": 2, "three": 3}[quantity]
        # ALFWorld grammar uses singular object type tokens even at count > 1.
        known = set(profile.RECEPTACLES)
        if target not in known and mode != "examine":
            raise NeedsBackoff("unknown destination type")
        if obj.endswith("s") and obj not in {"glass", "lettuce"}:
            raise NeedsBackoff("ambiguous object type morphology")
        def constraint(key, value, typ="string"):
            return Constraint(Entity("attribute", key), "eq", value, typ,
                              mode="observation" if key != "destination_type" else "selection", span=text)
        constraints = [constraint("object_type", obj), constraint("instrument_type" if mode == "examine" else "destination_type", target)]
        if state:
            state = profile._STATE[state.lower()]
            constraints.append(constraint(state, True, "boolean"))
        return [Goal(count, "portable", "examined_with" if mode == "examine" else "in", Entity("place", target),
                     name=f"{obj}_{'with' if mode == 'examine' else 'in'}_{target}", constraints=tuple(constraints))]
    raise NeedsBackoff("unrecognized complete task grammar")


def alf_observation(text, task, profile):
    remaining = text.strip()
    header = "-= Welcome to TextWorld, ALFRED! =-"
    if remaining.startswith(header):
        remaining = remaining[len(header):].lstrip()
    # This is a redundant envelope only when the task matches exactly.
    for marker in ("\n\nYour task is to: ", "\n\nYour task is: "):
        if marker in remaining:
            observation, repeated = remaining.split(marker, 1)
            if repeated.strip() != task.strip():
                raise NeedsBackoff("embedded task differs from task contract")
            remaining = observation.rstrip()
            break
    agent = ("agent", "self")
    facts, anchor = [], None
    entity = r"([a-z]+) (\d+)"
    clauses = re.split(r"(?<=\.)\s+", remaining)
    if not clauses or any(not c.strip() for c in clauses):
        raise NeedsBackoff("empty observation")
    for clause in clauses:
        if clause == "Nothing happens.":
            if len(clauses) != 1:
                raise NeedsBackoff("no-effect mixed with additional claims")
            facts.append(fact("effect", [agent], "none", clause)); continue
        if clause == "You are not carrying anything.":
            facts.append(fact("inventory_empty", [agent], True, clause)); continue
        if clause == "You are in the middle of a room.":
            anchor = ("place", "room")
            facts.append(fact("at", [agent, anchor], True, clause)); continue
        m = re.fullmatch(rf"You (?:arrive at|are facing the) {entity}\.", clause)
        if m:
            anchor = canon(profile, *m.groups())
            if anchor[0] not in profile.RECEPTACLES:
                raise NeedsBackoff("location is not a receptacle")
            facts.append(fact("at", [agent, anchor], True, clause)); continue
        m = re.fullmatch(rf"You (open|close) the {entity}\.", clause)
        if m:
            op, name, idx = m.groups(); anchor = canon(profile, name, idx)
            facts.append(fact("open", [anchor], op == "open", clause)); continue
        m = re.fullmatch(rf"The {entity} is (open|closed)\.", clause)
        if m:
            name, idx, state = m.groups(); current = canon(profile, name, idx)
            if anchor is not None and anchor != current:
                raise NeedsBackoff("multiple possible referents")
            anchor = current
            facts.append(fact("open", [anchor], state == "open", clause)); continue
        m = re.fullmatch(rf"You pick up the {entity} from the {entity}\.", clause)
        if m:
            name, idx, place, pi = m.groups(); obj = canon(profile, name, idx); loc = canon(profile, place, pi)
            facts += object_type(profile, obj, clause)
            facts += [fact("holding", [agent, obj], True, clause), fact("in", [obj, loc], False, clause),
                      fact("inventory_empty", [agent], False, clause)]
            continue
        m = re.fullmatch(rf"You (?:move|put) the {entity} (?:to|in|on) the {entity}\.", clause)
        if m:
            name, idx, place, pi = m.groups(); obj = canon(profile, name, idx); loc = canon(profile, place, pi)
            facts += object_type(profile, obj, clause)
            facts += [fact("in", [obj, loc], True, clause), fact("holding", [agent, obj], False, clause)]
            continue
        m = re.fullmatch(rf"You (clean|cool|heat) the {entity} using the {entity}\.", clause)
        if m:
            op, name, idx, _, _ = m.groups(); obj = canon(profile, name, idx)
            facts += object_type(profile, obj, clause)
            facts.append(fact("has", [obj, ("attribute", profile._STATE[op]), ("value", "true")], True, clause)); continue
        m = re.fullmatch(rf"You turn (on|off) the {entity}\.", clause)
        if m:
            state, name, idx = m.groups(); obj = canon(profile, name, idx)
            facts += object_type(profile, obj, clause)
            facts.append(fact('has', [obj, ('attribute', 'powered_on'), ('value', 'true')], state == 'on', clause)); continue
        m = re.fullmatch(rf"You are carrying: (?:a |an )?{entity}\.", clause)
        if m:
            obj = canon(profile, *m.groups()); facts += object_type(profile, obj, clause)
            facts += [fact("holding", [agent, obj], True, clause), fact("inventory_empty", [agent], False, clause)]; continue
        mode, body, loc = None, None, None
        m = re.fullmatch(rf"(On|In) the {entity}, you see (.+)\.", clause)
        if m:
            _, name, idx, body = m.groups(); loc = canon(profile, name, idx); mode = "in"
        else:
            m = re.fullmatch(r"(In it|Next to it|Looking quickly around you), you see (.+)\.", clause)
            if m:
                prefix, body = m.groups(); loc = anchor; mode = "in" if prefix == "In it" else "visible"
        if body is not None:
            if loc is None:
                raise NeedsBackoff("pronoun/list has no unique local antecedent")
            if body == "nothing":
                facts.append(fact("visible_empty", [loc], True, clause)); continue
            pieces = re.split(r",\s*(?:and )?| and ", body)
            entities = []
            for piece in pieces:
                match = re.fullmatch(rf"(?:a|an) {entity}", piece)
                if not match:
                    raise NeedsBackoff("unparsed enumeration member")
                entities.append(canon(profile, *match.groups()))
            if len(set(entities)) != len(entities):
                raise NeedsBackoff("duplicate entity in enumeration")
            facts.append(fact("visible_empty", [loc], False, clause))
            for obj in entities:
                facts += object_type(profile, obj, clause)
                facts.append(fact(mode, [obj, loc], True, clause))
            continue
        raise NeedsBackoff("unrecognized observation clause: " + clause[:100])
    if not facts:
        raise NeedsBackoff("observation has no statements")
    return facts


def tokens(text):
    # Remove exactly one matching display wrapper; preserve apostrophes in titles.
    out = []
    for part in text.split("[SEP]"):
        part = part.strip()
        if len(part) < 2 or part[0] not in "\"'" or part[-1] != part[0]:
            raise NeedsBackoff("unrecognized page token wrapper")
        out.append(part[1:-1])
    return out


def webshop_observation(text, profile):
    parts = tokens(text)
    if parts == ["Search"]:
        return [fact("at", [("agent", "self"), ("page", "search")], True, "Search")]
    if len(parts) < 2 or parts[0] != "Back to Search":
        raise NeedsBackoff("unrecognized page header")
    page = re.fullmatch(r"Page (\d+) \(Total results: (\d+)\)", parts[1])
    if page is None:
        raise NeedsBackoff("item page requires semantic/context audit")
    number, total = page.groups(); listing = ("listing", number)
    facts = [fact("at", [("agent", "self"), listing], True, parts[1]),
             fact("result_total", [listing], int(total), parts[1], "number")]
    i = 2
    for control in ("< Prev", "Next >"):
        if i < len(parts) and parts[i] == control:
            i += 1
    while i < len(parts):
        if i + 2 >= len(parts) or not profile.PID.fullmatch(parts[i]):
            raise NeedsBackoff("incomplete listing product tuple")
        pid, title, price = parts[i:i + 3]
        parsed = profile._price(price)
        if parsed is None or not title or title.lower() in profile.NAV | profile.COMMIT or profile.PID.fullmatch(title):
            raise NeedsBackoff("invalid product title/price tuple")
        price_id, interval = parsed; product = ("product", pid.lower())
        facts += [fact("visible", [product, listing], True, pid),
                  fact("has", [product, ("attribute", "title"), ("value", title.lower())], title, title),
                  fact("has", [product, ("attribute", "price"), ("value", price_id)], interval, price, "money_interval")]
        i += 3
    return facts


def webshop_task(text, profile):
    # Preserve every free-text qualifier as semantic/unmodeled, while extracting
    # labelled option equality and the explicit numeric price comparison.
    label_pattern = r"[a-z]+(?: (?!with\b)[a-z]+)*"
    pattern = rf"Find me (.+?) with ({label_pattern}: .+), and price lower than (\d+(?:\.\d+)?) dollars"
    m = re.fullmatch(pattern, text)
    if m is None:
        raise NeedsBackoff("unrecognized complete shopping task grammar")
    description, options, price = m.groups()
    if re.search(r"\b(?:it|them|its|their|this|that|nothing|no|not|without|except|either|or|at least|at most|only|exactly)\b", description, re.I):
        raise NeedsBackoff("ambiguous qualifier logic")
    goals = [Goal(1, "product", "has", Entity("attribute", "description"),
                  attrs=(("description", description),), name="description",
                  constraints=(Constraint(Entity("attribute", "description"), "semantic_match", description,
                                          "text", mode="semantic", span=description),), status_mode="unmodeled")]
    names = set()
    for clause in options.split(", and "):
        option = re.fullmatch(r"([a-z][a-z ]*): ([^:,]+)", clause)
        if not option:
            raise NeedsBackoff("unconsumed option clause")
        label, value = option.groups(); name = label.replace(" ", "_")
        if name in names or name in {"description", "price"}:
            raise NeedsBackoff("duplicate/ambiguous option label")
        if re.search(r"\b(?:or|except|not|without|either|same|it|them|that|this)\b", value, re.I):
            raise NeedsBackoff("ambiguous option value")
        names.add(name); prop = Entity("attribute", name)
        goals.append(Goal(1, "value", "chosen", prop, attrs=((label, value.lower()),), name=name,
                          constraints=(Constraint(prop, "eq", value.lower(), "string", span=clause),)))
    prop = Entity("attribute", "price")
    goals.append(Goal(1, "value", "chosen", prop, attrs=(("price", float(price)),), name="price",
                      constraints=(Constraint(prop, "lt", float(price), "money", "USD", "observation",
                                              "price lower than " + price + " dollars"),)))
    return goals


def parse(kind, text, profile, *, task="", admissible=(), prefix=()):
    if profile.name == "openclaw":
        if kind == "task":
            return {"template": f"{profile.VERSION}/task", "facts": [],
                    "goals": plain(profile.parse_goal(text))}
        if kind != "observation":
            raise NeedsBackoff("unsupported source kind")
        return {"template": f"{profile.VERSION}/observation",
                "facts": profile.parse_observation(text, admissible), "goals": []}
    if kind == "task":
        goals = alf_task(text, profile) if profile.name == "alfworld" else webshop_task(text, profile)
        return {"template": f"{VERSION}/{profile.name}/task", "facts": [], "goals": plain(goals)}
    facts = alf_observation(text, task, profile) if profile.name == "alfworld" else webshop_observation(text, profile)
    return {"template": f"{VERSION}/{profile.name}/observation", "facts": facts, "goals": []}
