"""What a profile must supply. Core code depends on this interface only.

One rule governs every parser here. **Report only what binds to a canonical
identifier or to a vocabulary the environment itself declares.** Do not infer
meaning from a word list. Three separate parsers were first written against verb
and attribute tables, and each produced the same failure: the table matched words
used in passing, the record filled with assertions the text never made, and the
criterion fired on a quarter of all steps. Where a parser cannot bind, it says so
and the criterion returns UNK, which the reasoner can settle with one call.
"""
from typing import Any, Dict, List, Optional, Protocol, Tuple

from kinds import Kinds
from relations import Relations
from record import Record, Entity, Goal


class Profile(Protocol):
    name: str

    def kinds(self) -> Kinds: ...
    def relations(self) -> Relations: ...

    def parse_goal(self, task: str) -> List[Goal]:
        """The task string into quantified relational expressions."""

    def parse_observation(self, text: str, admissible: List[str]) -> List[Dict[str, Any]]:
        """Environment text into relation instances. Each item is
        {rel, args:[(kind,index)], value, span}. It states facts, never verdicts."""

    def parse_memory(self, text: str, known_values: Dict[str, List[str]] = None) -> List[Dict[str, Any]]:
        """A recollection into the relation instances it asserts. A memory fact
        must state the relation it recalls, not a placeholder, or the criterion
        compares the wrong version chain and can never find a disagreement.
        Each item is {rel, args, value, span}; an item that binds to no entity is
        not emitted, since a mention that resolves to nothing is not a fact."""

    def parse_intention(self, plan_text: str, admissible: List[str]) -> Optional[Dict[str, Any]]:
        """The stated plan into the intention it declares, as {verb, args}.

        This must be parsed from the plan and never from the action. An intention
        copied from the action it is meant to be compared against makes the
        comparison an identity, and the whole class of faults where the plan says
        one thing and the action does another becomes unstatable.

        Report only what the plan actually names, at the level it names it:

            {"rel": v, "args": [...]}   both the act and its target
            {"rel": v}                  the act, with no target named
            {"args": None}              an intention with neither named
            None                        the plan declares no intention

        Never invent a target. A comparison against a target the plan never named
        is a disagreement the record cannot support, and it will fire on a large
        fraction of all actions."""

    def parse_action(self, text: str, admissible: List[str]) -> Optional[Dict[str, Any]]:
        """The emitted action into {rel, args, value, admissibility, span}, where
        admissibility is one of well_formed / quoted / not_admissible / no_tag.
        The classification is a fact about the action, not a judgement of it.

        An action that settles a requirement may carry `discharges`, a relation
        instance the compiler emits as a check. Without it nothing ever records
        that a requirement was met, and every run would read as a total failure."""

    def decompose(self, plan_text: str) -> List[str]:
        """The sub-goals a plan states explicitly, in order, or an empty list.

        This is what makes the purpose axis a tree. A plan that says it must find
        the card before it can place it declares two levels; a plan that says
        nothing declares one. Chaining consecutive steps instead produces a list
        as deep as the run, which is the trajectory relabelled, not a
        decomposition."""

    def null_verb(self) -> str:
        """The verb to record when a step emitted no parseable action."""

    def bears_on(self, item: Dict[str, Any], goals) -> Optional[Any]:
        """Which requirement, if any, this observed fact bears on.

        An observation that puts a requirement within reach is the opportunity
        against which an omission is stated. Without it `could_have` has nothing
        to report and a check that came due but never ran is invisible."""

    def situating_relation(self) -> str:
        """The functional relation that indexes an indexical observation, so a
        fact asserted at a step can carry the situation it was asserted in."""
