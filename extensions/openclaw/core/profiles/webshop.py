"""The WebShop profile.

The same relations as the embodied profile, read off a different surface. An
item page is a place the agent is at, an attribute holds values the way a
receptacle holds objects, and selecting an option is how a requirement is
discharged. If the core needed changing to accommodate this, the abstraction
would be wrong; it did not.
"""
import re
from semantic_scope import memory_scope, decision_clause
from typing import Any, Dict, List, Optional

from kinds import Kinds
from relations import Relations, FUNCTIONAL, MUTABLE, OBSERVED
from record import Constraint, Goal, Entity, UNBOUND

name = "webshop"


def domain_briefing() -> Dict[str, Any]:
    return {
        "name": "webshop",
        "task_shape": "search products, inspect item pages, select required variants, then purchase",
        "entity_rules": [
            "listing and item-page prices are observations, not selections",
            "size and color values become selectable only when exposed on an item page",
        ],
        "evidence_rules": [
            "an offered value is not a chosen value",
            "a qualifying product title or price is not proof that the purchased variant satisfies every constraint",
            "item-page prose is retained as description evidence; literal phrase containment can establish a free-text qualifier, while non-matches remain unresolved",
            "Buy Now closes recovery but may only execute an earlier unsupported commitment",
            "one unproductive results page does not prove the search strategy globally sterile",
        ],
        "critical_questions": [
            "Which task constraints were observed, selected, or still unresolved?",
            "Which product and variant did the intention actually serve?",
            "Did a search, click, selection, or purchase have its expected effect?",
            "Was a later action still able to repair the missing constraint?",
        ],
    }

PID = re.compile(r"\bb0[0-9a-z]{8}\b", re.I)
NAV = {"back to search", "next >", "< prev", "description", "features", "reviews", "search"}
# controls that commit or leave the page. They are clickable but are not
# offered values, so filing them under the open group invented options.
COMMIT = {"buy now", "buy"}


def kinds() -> Kinds:
    K = Kinds()
    K.declare("thing")
    K.declare("place", "thing")
    K.declare("page", "place")
    K.declare("listing", "page")
    K.declare("item_page", "page")
    K.declare("object", "thing")
    K.declare("product", "object")
    K.declare("attribute", "thing")
    K.declare("value", "thing")
    K.declare("agent", "thing")
    K.declare("req")
    K.declare("int")
    return K


def relations() -> Relations:
    R = Relations()
    R.declare("at", 2, (FUNCTIONAL, MUTABLE), key=(0,),
              arg_kinds=("agent", "page"), value_types=("boolean",))
    R.declare("in", 2, (FUNCTIONAL, MUTABLE), key=(0,),
              arg_kinds=("value", "attribute"),
              value_types=("boolean", "number_interval", "money_interval"))
    R.declare("open", 1, (MUTABLE,), key=(0,), arg_kinds=("thing",),
              value_types=("boolean",))
    R.declare("has", 3, (FUNCTIONAL, MUTABLE), key=(0, 1),
              arg_kinds=("product", "attribute", "value"),
              value_types=("string", "number_interval", "money_interval", "boolean"))
    # Item pages may contain several independent textual evidence spans.  This
    # relation is deliberately non-functional: retaining all spans avoids
    # flattening a description into one guessed attribute value.
    R.declare("describes", 3, (MUTABLE,),
              arg_kinds=("product", "attribute", "value"),
              value_types=("string",))
    R.declare("visible", 2, (MUTABLE, OBSERVED), key=(0,),
              arg_kinds=("product", "page"), value_types=("boolean",))
    R.declare("result_total", 1, (MUTABLE,), key=(0,),
              arg_kinds=("listing",), value_types=("number",))
    R.declare("offers", 2, (MUTABLE,), arg_kinds=("product", "attribute"),
              value_types=("boolean",))
    R.declare("chosen", 2, (FUNCTIONAL, MUTABLE, OBSERVED), key=(0,),
              arg_kinds=("attribute", "value"),
              value_types=("boolean", "string"))
    R.declare("effect", 1, (MUTABLE,), key=(0,), arg_kinds=("thing",))
    R.declare("admissibility", 1, (FUNCTIONAL,), key=(0,), arg_kinds=("int",),
              value_types=("string",))
    R.declare("search", 1, (MUTABLE,), key=(0,), arg_kinds=("value",),
              value_types=("string",))
    R.declare("click", 1, (MUTABLE,), key=(0,), arg_kinds=("thing",),
              value_types=("string", "null"))
    R.declare("satisfied", 1, (FUNCTIONAL, MUTABLE), key=(0,), arg_kinds=("req",),
              value_types=("null", "boolean"))
    R.declare("recalled_nothing", 1, (MUTABLE,), key=(0,), arg_kinds=("agent",),
              value_types=("boolean",))
    R.declare("memory_unparsed", 1, (MUTABLE,), key=(0,), arg_kinds=("agent",),
              value_types=("boolean",))
    # Keep the complete memory slot as evidence in addition to the entity-level
    # claims extracted from it. A lossy memory can be the fault even when every
    # identifier it retained is individually true.
    R.declare("recalled_summary", 1, (MUTABLE,), key=(0,), arg_kinds=("agent",),
              value_types=("string",))
    R.declare("assessed", 1, (MUTABLE,), key=(0,), arg_kinds=("agent",),
              value_types=("string",))
    R.declare("planned", 1, (MUTABLE,), key=(0,), arg_kinds=("agent",),
              value_types=("string",))
    return R


def bears_on(item: Dict[str, Any], goals):
    """An offered value bears on the requirement for that attribute: the moment
    it appears is the moment the requirement became decidable."""
    if item["rel"] == "in":
        attr = item["args"][1][1]
    elif item["rel"] == "has" and item["args"][1][1] == "price":
        attr = "price"
    else:
        return None
    for g in goals:
        if g.name == attr:
            return g.entity()
    return None


def commits() -> set:
    """Clicking buy ends the run: nothing after it can put a requirement right."""
    return set(COMMIT)


def situating_relation() -> str:
    return "at"


def null_verb() -> str:
    return "click"


_SUB = re.compile(r"\b(?:first|before (?:that|this|i)|in order to|so that|then)\b", re.I)


def decompose(plan_text: Optional[str]) -> List[str]:
    """WebShop plans are single-step and state no decomposition, so the tree is
    one level here. Returning nothing is the honest answer; inventing levels from
    consecutive actions would make the depth equal to the step count."""
    if not plan_text:
        return []
    parts = [p.strip() for p in _SUB.split(" ".join(plan_text.split())) if p.strip()]
    return parts[1:] if len(parts) > 1 else []


# ---------------- the goal --------------------------------------------
_PRICE = re.compile(r"price lower than ([\d.]+) dollars", re.I)
_ATTR = re.compile(
    r"(?:\bwith\s+|,\s*(?:and\s+)?)"
    r"(?P<label>[a-z](?:(?!\bwith\b)[a-z0-9 _\-/&| ]){0,47}?)\s*:\s*"
    r"(?P<value>[^,]+?)(?=,\s*(?:and\s+)?|$)",
    re.I,
)
_PREFIX_END = re.compile(
    r"(?:\bwith\s+|,\s*(?:and\s+)?)"
    r"[a-z](?:(?!\bwith\b)[a-z0-9 _\-/&| ]){0,47}?\s*:", re.I)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def _qualifier_phrases(task: str) -> List[str]:
    """Retain free-text requirements without pretending to understand them.

    WebShop tasks use a templated `Find me ... with <label>: <value>` surface.
    Everything before the first labelled option remains part of the contract.
    Splitting on commas and `with` exposes useful phrases while their semantic
    verification deliberately remains unresolved.
    """
    body = re.sub(r"^\s*find me\s+", "", task, flags=re.I).strip()
    cut = _PREFIX_END.search(body)
    if cut:
        body = body[:cut.start()].strip(" ,")
    body = re.sub(r"(?:,?\s*and\s+)?price lower than [\d.]+ dollars", "", body,
                  flags=re.I).strip(" ,")
    return [" ".join(x.split()).lower() for x in
            re.split(r"\s*,\s*|\s+with\s+", body, flags=re.I)
            if " ".join(x.split())]


def parse_goal(task: str) -> List[Goal]:
    """Parse a task contract without a closed attribute-name vocabulary.

    Labelled option constraints are dynamically materialised as property
    entities. Free-text qualifiers are preserved as typed semantic constraints
    rather than silently discarded; they remain `unmodeled` until observation
    parsing can verify the associated product feature.
    """
    out: List[Goal] = []
    for n, phrase in enumerate(_qualifier_phrases(task), start=1):
        prop = Entity("attribute", "description")
        out.append(Goal(
            count=1, of_kind="product", rel="has", target=prop,
            attrs=(("description", phrase),), name=f"qualifier_{n}_{_slug(phrase)[:28]}",
            constraints=(Constraint(
                property=prop, operator="semantic_match", expected=phrase,
                value_type="text", mode="semantic", span=phrase,
                confidence=1.0,
            ),),
            status_mode="unmodeled",
        ))
    for m_attr in _ATTR.finditer(task):
        raw_label = m_attr.group("label").strip().lower()
        # If the regex began at an earlier descriptive "with", the final
        # segment before ':' is the actual field label.
        k = re.split(r"\bwith\b", raw_label, flags=re.I)[-1].strip()
        v = m_attr.group("value").strip().lower()
        name = _slug(k)
        prop = Entity("attribute", name)
        out.append(Goal(count=1, of_kind="value", rel="chosen",
                        target=prop, attrs=((k, v),), name=name,
                        constraints=(Constraint(
                            property=prop, operator="eq", expected=v,
                            value_type="string", mode="selection",
                            span=m_attr.group(0), confidence=1.0,
                        ),)))
    m = _PRICE.search(task)
    if m:
        prop = Entity("attribute", "price")
        out.append(Goal(count=1, of_kind="value", rel="chosen",
                        target=prop,
                        attrs=(("price", float(m.group(1))),), name="price",
                        constraints=(Constraint(
                            property=prop, operator="lt", expected=float(m.group(1)),
                            value_type="money", unit="USD", mode="observation",
                            span=m.group(0), confidence=1.0,
                        ),)))
    return out


# ---------------- the environment turn ---------------------------------
_PAGE = re.compile(r"Page (\d+) \(Total results: (\d+)\)")
_OBS_PRICE = re.compile(
    r"^(?:Price:\s*)?\$(\d+(?:\.\d+)?)"
    r"(?:\s+to\s+\$(\d+(?:\.\d+)?))?$", re.I)
_WS = lambda s: " ".join((s or "").split()).lower()
_TOK = lambda s: [t.strip().strip("'\"") for t in s.split("[SEP]") if t.strip()]


def _price(raw: str):
    """Bind an environment-written price token to a stable interval value."""
    m = _OBS_PRICE.match(raw.strip())
    if not m:
        return None
    low = float(m.group(1))
    high = float(m.group(2)) if m.group(2) is not None else low
    index = f"{low:g}" if low == high else f"{low:g}_to_{high:g}"
    return index, (low, high)


def parse_observation(text: str, admissible: List[str]) -> List[Dict[str, Any]]:
    t = " ".join((text or "").split())
    out: List[Dict[str, Any]] = []
    # the admissible list writes multi-word values with a non-breaking space
    # while the observation writes an ordinary one, so a value like
    # "a1-wine red" was read as a group header and every colour after it was
    # filed under it. Both sides are whitespace-normalised before comparison.
    clickable = {_WS(a[len("click["):-1]) for a in
                 (x.strip().lower() for x in admissible) if a.startswith("click[")}

    m = _PAGE.search(t)
    if m:
        out.append({"rel": "at", "args": [("agent", "self"), ("listing", m.group(1))],
                    "value": True, "span": m.group(0)})
        # the span above is taken from the normalised text, which collapses
        # whitespace; check against the original below
        toks = _TOK(t)
        i = 0
        while i < len(toks):
            if PID.match(toks[i]):
                raw_pid = toks[i]
                pid = raw_pid.lower()
                out.append({"rel": "visible", "args": [("product", pid), ("listing", m.group(1))],
                            "value": True, "span": raw_pid})
                if i + 1 < len(toks):
                    title = toks[i + 1]
                    # the entity index is normalised, the span stays verbatim, so
                    # groundedness can be checked against the original text
                    out.append({"rel": "has", "args": [("product", pid), ("attribute", "title"),
                                                       ("value", title[:60].lower())],
                                "value": title[:60], "span": title})
                if i + 2 < len(toks):
                    raw_price = toks[i + 2]
                    parsed = _price(raw_price)
                    if parsed is not None:
                        price_id, interval = parsed
                        out.append({"rel": "has", "args": [("product", pid),
                                                           ("attribute", "price"),
                                                           ("value", price_id)],
                                    "value": interval, "value_type": "money_interval",
                                    "span": raw_price})
                i += 3
            else:
                i += 1
        return out

    toks = _TOK(t)
    # Product sub-pages such as Description often omit Buy Now while retaining
    # Back to Search. Listing pages were handled above, so this is a stable
    # environment marker for the current item context rather than a guessed UI.
    if (any(x.lower() == "buy now" for x in toks)
            or any(_WS(x) == "back to search" for x in toks)):
        # no span is claimed for the page itself, since "item page" is our word
        # an item page. The product is whatever the agent last clicked, which the
        # compiler knows; here only the offered attributes are stated.
        out.append({"rel": "at", "args": [("agent", "self"), ("item_page", "current")],
                    "value": True, "span": ""})
        key = None
        for x in toks:
            lo = _WS(x)
            parsed = _price(x)
            if parsed is not None:
                price_id, interval = parsed
                out.append({"rel": "in", "args": [("value", price_id),
                                                    ("attribute", "price")],
                            "value": interval, "value_type": "money_interval", "span": x})
                continue
            if lo in NAV or lo in COMMIT or lo == "rating: n.a.":
                continue
            if lo not in clickable and len(x.split()) <= 3 and x == lo:
                key = lo
            elif lo in clickable and key:
                out.append({"rel": "in", "args": [("value", lo), ("attribute", key.replace(" ", "_"))],
                            "value": True, "span": x})
            elif len(x.split()) > 3:
                # Preserve product prose as entity evidence.  Verification is
                # still conservative: the view only proves a semantic
                # requirement when its literal phrase occurs in this span.
                value_id = re.sub(r"[^a-z0-9]+", "_", lo).strip("_")[:80] or "text"
                out.append({
                    "rel": "describes",
                    "args": [("product", "current"), ("attribute", "description"),
                             ("value", value_id)],
                    "value": x,
                    "value_type": "string",
                    "span": x,
                })
        return out

    if t.strip().strip("'") == "Search":
        out.append({"rel": "at", "args": [("agent", "self"), ("page", "search")],
                    "value": True, "span": "Search"})

    return out


# ---------------- the agent turn ---------------------------------------
_ACT = re.compile(r"(search|click)\s*\[\s*(.*?)\s*\]", re.S)


def _act_entity(kind_hint: str, arg: str):
    a = arg.strip().lower()
    if PID.match(a):
        return ("product", a)
    if a in NAV or a in COMMIT:
        return ("page", a.replace(" ", "_"))
    return ("value", a)


def parse_action(text: Optional[str], admissible: List[str]) -> Optional[Dict[str, Any]]:
    if text is None:
        return {"rel": "click", "args": [("page", "none")], "value": None,
                "admissibility": "no_tag", "span": ""}
    raw = text.strip()
    m = _ACT.search(raw)
    if not m:
        return {"rel": "click", "args": [("page", "none")], "value": raw[:60],
                "admissibility": "not_admissible", "span": raw}
    # the entity index is normalised; the span quotes the text as it stood
    verb = m.group(1).lower()
    raw_arg = m.group(2).strip().strip("'\"")
    arg = raw_arg.lower()
    adm = {_WS(a) for a in admissible}
    if verb == "search":
        cls = "well_formed" if any(a.startswith("search[") for a in adm) else "not_admissible"
        return {"rel": "search", "args": [("value", arg[:80])], "value": arg,
                "admissibility": cls, "span": raw_arg}
    cls = "well_formed" if f"click[{arg}]" in adm else "not_admissible"
    item = {"rel": "click", "args": [_act_entity("", arg)], "value": arg,
            "admissibility": cls, "span": raw_arg}
    if not PID.match(arg) and arg not in NAV and arg not in ("buy now", "buy"):
        # clicking an offered value settles that attribute, whether or not the
        # value is the required one. Recording only the matching ones would hide
        # a selection later replaced by another, which is exactly the case a
        # functional relation exists to catch.
        item["discharges"] = {"rel": "chosen", "value": arg}
    return item


# A plan declares an intention only where it says what it will do next. Verbs
# that appear while describing what happened, or what the results were, are not
# declarations, and reading them as such invents disagreements.
_DECL = re.compile(
    r"(?:i (?:should|will|need to|plan to)|next step[:,]?|"
    r"the next step (?:should be|is) to|let me|i'?ll)\s+(.{0,240})", re.I)
_VERB = re.compile(
    r"\b(search|click|select|open|examine|buy|refine|re-?search|"
    r"navigate|proceed|move|continue)\b", re.I)
_VERB_MAP = {"search": "search", "re-search": "search", "research": "search",
             "refine": "search", "click": "click", "select": "click",
             "open": "click", "examine": "click", "buy": "click",
             "navigate": "click", "proceed": "click", "move": "click",
             "continue": "click"}
_NAV_WORDS = (("next", "next_>"), ("back to search", "back_to_search"),
              ("previous", "<_prev"), ("prev", "<_prev"),
              ("description", "description"), ("features", "features"),
              ("reviews", "reviews"), ("buy now", "buy_now"))


def _goal_expected(g: Goal) -> List[str]:
    values = []
    for c in g.constraints:
        expected = c.expected if isinstance(c, Constraint) else c[2]
        values.append(_WS(str(expected)))
    return values


def parse_intention(plan_text: Optional[str], admissible: List[str],
                    goals: List[Goal] = None) -> Optional[Dict[str, Any]]:
    """Extract a grounded, typed intention from a declaration.

    The declaration detector prevents descriptive uses of words such as
    "search results" from becoming intentions. Inside that declared tail, the
    parser extracts an action, target, typed parameters, constraint mentions,
    preconditions, expected effects, and commitment class. It does not judge
    whether the intention is good; the independent intention validator does.
    """
    if not plan_text:
        return None
    goals = goals or []
    t = decision_clause(plan_text)
    if not t:
        return {'rel': None, 'args': None, 'serves': tuple(g.entity() for g in goals),
                'extraction': 'unresolved_decision_scope', 'confidence': 0.0}
    t = re.sub(r'^clicking\b', 'click', t, flags=re.I)
    t = re.sub(r'^(?:returning|going|moving)\b', 'navigate', t, flags=re.I)
    m = _DECL.search(t)
    # The text is already isolated from the plan slot, so a missing declaration
    # marker is uncertainty about explicitness, not evidence that no intention
    # exists. This fallback is lower-confidence and remains labelled as such.
    candidate = m.group(1) if m else ""
    # A late hypothetical such as "the agent will need to conclude" must not
    # hide the actual plan at the beginning of the slot. Keep an explicit tail
    # only when it contains an actionable verb or canonical product id.
    prefix_has_action = bool(m and (_VERB.search(t[:m.start()]) or PID.search(t[:m.start()])))
    if m and not prefix_has_action and (_VERB.search(candidate) or PID.search(candidate)):
        tail = candidate
    else:
        m = None
        tail = t
    extraction_source = "decision_clause"
    low = tail.lower()
    verb_match = _VERB.search(tail)
    verb = _VERB_MAP.get(verb_match.group(1).lower()) if verb_match else None
    objects = None
    parameters = []
    execution_options = []
    command = _ACT.search(tail)
    explicit_control = False
    if command and (command.start() == 0 or re.fullmatch(r'click\s+(?:on\s+)?[\"\']*', tail[:command.start()], re.I)):
        verb = command.group(1).lower()
        arg = command.group(2).strip().strip('\"\'').lower()
        objects = [_act_entity('', arg)] if verb == 'click' else [('value', arg[:80])]
        explicit_control = True

    pid = PID.search(tail)
    if pid and objects is None:
        verb = verb or "click"
        objects = [("product", pid.group(0).lower())]

    offered = {a[len("click["):-1].lower() for a in
               (x.strip().lower() for x in admissible) if a.startswith("click[")}
    if objects is None:
        for ctl in sorted(offered, key=len, reverse=True):
            if ctl == "search":
                continue
            target = re.sub(r'^(?:click|select|open|examine)\s*(?:on\s+)?[\[\"\']*', '', low)
            if (re.match(re.escape(ctl) + r'(?:$|[^\w])', target) and not PID.match(ctl)):
                # A canonical clickable control names the immediate action even
                # if the surrounding explanation also says "search results".
                verb = "click"
                objects = [_act_entity("", ctl)]
                break

    # The first sentence carries the immediate step more reliably than later
    # rationale or contingencies. Let an explicit navigation phrase there
    # override incidental mentions of other clickable values.
    head = re.split(r"[.\n]", low, maxsplit=1)[0]
    head_navigation = (
        (("back to search", "return to the search", "back to the search",
          "revisit the search", "search page"), "back to search"),
        (("previous page",), "< prev"),
        (("next page", "following page"), "next >"),
    )
    results_return = bool(re.search(r'\b(?:back|return|revisit)\b.*?\bsearch results\b', head))
    if results_return and not explicit_control:
        verb = 'click'
        objects = ([('page', '<_prev')] if '< prev' in offered and
                   offered & {'description', 'features', 'reviews', 'buy now'} else None)
    for phrases, control in head_navigation:
        if not explicit_control and not results_return and any(p in head for p in phrases) and control in offered:
            verb = "click"
            objects = [_act_entity("", control)]
            break

    if objects is None and not results_return:
        navigation_phrases = (
            ("back to search", "back to search"),
            ("return to the search", "back to search"),
            ("back to the search", "back to search"),
            ("revisit the search", "back to search"),
            ("previous page", "< prev"),
            ("next page", "next >"),
            ("following page", "next >"),
        )
        for phrase, control in navigation_phrases:
            if phrase in low and control in offered:
                verb = "click"
                objects = [_act_entity("", control)]
                break

    numbered_page = re.search(r"\bpage\s+(\d+)\b", head)
    if objects is None and numbered_page and verb == "click":
        # A page number alone does not establish forward navigation.
        parameters.append({"name": "destination_page",
                           "value": int(numbered_page.group(1)),
                           "value_type": "number",
                           "span": numbered_page.group(0)})

    if verb == "click" and objects is None and "search" in low \
            and verb_match is not None \
            and verb_match.group(1).lower() in ("refine", "continue"):
        verb = "search"

    if verb == "search" and verb_match is not None:
        query = tail[verb_match.end():].strip(" :,-.'\"")
        query = re.sub(r"^(?:for|using|with|query)\s+", "", query, flags=re.I)
        query = re.split(r"[.\n]", query, maxsplit=1)[0].strip()
        if query:
            parameters.append({"name": "query", "value": query.lower(),
                               "value_type": "text", "span": query})

    # Some plans name a multi-step goal. On a listing page, refining a query
    # requires returning to search first; continuing exploration may be realized
    # by the next-page control. These are grounded execution options, not guesses
    # from the action that was eventually emitted.
    has_search_action = any(a.startswith("search[") for a in
                            (x.strip().lower() for x in admissible))
    if verb == "search" and "back to search" in offered and not has_search_action:
        execution_options.append({
            "verb": "click", "args": [("page", "back_to_search")],
            "role": "prerequisite", "span": "return to search",
            "preconditions": ["action_admissible"],
            "expected_effects": ["search_page_observed"],
        })
    if "next >" in offered and any(x in low for x in
                                    ("next page", "following page", "continue",
                                     "further page", "additional page", "explor")):
        execution_options.append({
            "verb": "click", "args": [("page", "next_>")],
            "role": "alternative", "span": "continue pagination",
            "preconditions": ["action_admissible"],
            "expected_effects": ["result_listing_observed"],
        })
    for ctl in sorted(offered):
        if re.search(r'\bor\s+(?:(?:click|select|open)\s+(?:on\s+)?)?[\"\']*' +
                     re.escape(ctl) + r'(?!\w)', low):
            execution_options.append({'verb': 'click', 'args': [_act_entity('', ctl)],
                                      'role': 'alternative', 'span': t,
                                      'preconditions': ['action_admissible'],
                                      'expected_effects': ['page_transition_observed']})

    mentioned = []
    for g in goals:
        if any(expected and expected in low for expected in _goal_expected(g)):
            mentioned.append(g.entity())

    target = objects[0] if objects and len(objects) == 1 else None
    if target is not None and target[0] == "page" and target[1] in ("buy_now", "buy"):
        commitment = "commit"
        preconditions = ["all_task_constraints_verified", "action_admissible"]
        expected_effects = ["purchase_committed"]
    elif verb == "search":
        commitment = "explore"
        preconditions = ["query_executable"]
        expected_effects = ["result_listing_observed"]
    elif target is not None and target[0] == "product":
        commitment = "inspect"
        preconditions = ["target_visible", "action_admissible"]
        expected_effects = ["item_page_observed"]
    elif target is not None and target[0] == "value":
        commitment = "select"
        preconditions = ["value_offered", "action_admissible"]
        expected_effects = ["selection_recorded"]
    elif target is not None and target[0] == "page":
        commitment = "navigate"
        preconditions = ["action_admissible"]
        expected_effects = ["page_transition_observed"]
    else:
        commitment = "unknown"
        preconditions = []
        expected_effects = []

    if commitment in ("explore", "inspect", "navigate", "commit"):
        serves = [g.entity() for g in goals]
    else:
        serves = list(mentioned)

    if verb is not None and objects is not None:
        confidence = 1.0
        binding_label = "full"
    elif verb is not None:
        confidence = 0.75
        binding_label = "verb_only"
    else:
        confidence = 0.4
        binding_label = "unbound"
    extraction = f"{extraction_source}:{binding_label}"

    return {
        "rel": verb,
        "args": objects,
        "parameters": parameters,
        "execution_options": execution_options,
        "serves": serves,
        "mentions": mentioned,
        "preconditions": preconditions,
        "expected_effects": expected_effects,
        "commitment": commitment,
        "extraction": extraction,
        "confidence": confidence,
    }


_MEM_ATTR = re.compile(r"\b(color|colour|size|fit type|fit|material|style)\b\s*(?:is|are|was|:|=)\s*[\"']?([a-z0-9][a-z0-9 .\-]{0,22}?)[\"'.,;]", re.I)


def parse_memory(text: str, known_values: Dict[str, List[str]] = None) -> List[Dict[str, Any]]:
    """A recollection states relations, but only where its words bind.

    Product identifiers bind because the environment writes them canonically. An
    attribute claim binds as a selection only where the page has offered that
    attribute, which is what the caller passes; before then the run was in no
    position to select anything and the same words are the brief restated."""
    t = text or ""
    known_values = known_values or {}
    out: List[Dict[str, Any]] = []
    seen = set()
    for pid in sorted({p.lower() for p in PID.findall(t)}):
        if ("visible", pid) in seen:
            continue
        seen.add(("visible", pid))
        m = re.search(pid, t, re.I)
        scope = memory_scope(t, m.start(), m.end())
        if scope is None or scope['polarity'] != 'positive':
            continue
        out.append({"rel": "visible", "args": [("product", pid), ("listing", UNBOUND)],
                    "value": True, "span": scope['source_context'], "claim_scope": scope})
    low = t.lower()
    for attr, values in known_values.items():
        for v in values:
            v = str(v).lower()
            if not v:
                continue
            if ("chosen", attr) in seen:
                continue
            for m in re.finditer(r'(?<!\w)' + re.escape(v) + r'(?!\w)', t, re.I):
                scope = memory_scope(t, m.start(), m.end())
                if scope is None or scope['polarity'] != 'positive':
                    continue
                prefix = t[scope['source_start']:m.start()].lower()
                # An explicit completed selection is required, with the owner
                # attribute or a unique offered value. Description/requirements
                # and prospective selections stay in the verbatim summary.
                explicit = re.search(r'\b(?:i |we |the user |the agent )?(?:have |has |had )?(?:selected|chose|clicked)\s+(?:on\s+)?(?:the\s+)?(?:' + re.escape(attr.replace('_', ' ')) + r'\s+)?[\"\']*$', prefix)
                assignment = re.search(r'\b(?:selected\s+' + re.escape(attr.replace('_', ' ')) + r'|'+ re.escape(attr.replace('_', ' ')) + r'\s+(?:is|was)\s+selected)\s*(?:is|was|:|=|as|to)?\s*[\"\']*$', prefix)
                owners = {a for a, vs in known_values.items() if v in [str(x).lower() for x in vs]}
                if not assignment and not (explicit and (len(owners) == 1 or attr.replace('_', ' ') in prefix)):
                    continue
                seen.add(("chosen", attr))
                out.append({"rel": "chosen", "args": [("attribute", attr), ("value", v)],
                            "value": v, "span": scope['source_context'], "claim_scope": scope})
                break
            if ('chosen', attr) in seen:
                break
    return out
