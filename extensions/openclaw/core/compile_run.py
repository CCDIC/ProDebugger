"""S, R, L. The compiler is profile-driven and contains no benchmark string.

S partitions a step into slots. R binds mentions to entities and emits facts
carrying their span and the situation they were asserted in. L assigns the basis
and the purpose, which are the two axes.

Three invariants are checked here rather than requested in a prompt:
    aligned   one entry per native step
    grounded  every span occurs verbatim in its step
    ordered   every basis and every situation points strictly backwards
The third is enforced by Record.add and so cannot be violated silently.
"""
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from record import (Record, Entity, Goal, IntentionOption, IntentionParameter,
                    SourceStep)
from process_notes import build_process_notes

TAGS = ("memory", "reflection", "plan", "action")
SLOT_OF = {"memory": "mem", "reflection": "refl", "plan": "plan", "action": "act"}


@dataclass
class Step:
    t: int
    env_text: str
    agent_text: str
    admissible: List[str]
    parts: Dict[str, Optional[str]]


def segment(env_texts: List[str], agent_texts: List[str],
            admissible: List[List[str]]) -> List[Step]:
    """S. Tag extraction is tolerant of the malformed delimiters the corpus
    contains, because a delimiter is not a judgement."""
    steps: List[Step] = []
    for i, (u, a, adm) in enumerate(zip(env_texts, agent_texts, admissible), start=1):
        parts: Dict[str, Optional[str]] = {tag: None for tag in TAGS}
        # A quoted '<action>' inside memory/reflection is source content, not a
        # new output slot. Only a top-level tag, or a standalone line repairing
        # a missing delimiter, can open a different module.
        active, start = None, 0
        pattern = r'[<\[](/?)(memory|reflection|plan|action)[>\]]'
        for match in re.finditer(pattern, a):
            closing, tag = match.groups()
            line_prefix = a[a.rfind('\n', 0, match.start()) + 1:match.start()]
            standalone = not line_prefix.strip()
            quoted_literal = (match.start() > 0 and match.end() < len(a)
                              and a[match.start()-1] in '\"\'`'
                              and a[match.end()] == a[match.start()-1])
            if quoted_literal:
                continue
            if closing:
                if active == tag:
                    parts[tag] = a[start:match.start()].strip()
                    active = None
            elif active is None or standalone:
                if active is not None:
                    parts[active] = a[start:match.start()].strip()
                active, start = tag, match.end()
        if active is not None:
            parts[active] = a[start:].strip()
        steps.append(Step(i, u, a, adm, parts))
    return steps


def build(tid: str, task: str, steps: List[Step], profile, *, parsed=None) -> Record:
    K, RL = profile.kinds(), profile.relations()
    goals = parsed["goals"] if parsed is not None else profile.parse_goal(task)
    if parsed is not None:
        # Even a fully consumed observation template does not make the world
        # closed or the heuristic memory extraction exhaustive. Missing Facts
        # must not become negative observations in native criterion checks.
        for name, relation in RL.declared().items():
            RL._r[name] = replace(relation, props=relation.props - {"observed"})
    R = Record(tid, K, RL, task=task, goals=goals)
    R.domain = getattr(profile, "name", "unknown")
    R.parse_audits = parsed["audits"] if parsed is not None else []
    R.domain_briefing = (profile.domain_briefing()
                         if hasattr(profile, "domain_briefing") else
                         {"name": R.domain})
    R.commit_values = set(profile.commits()) if hasattr(profile, "commits") else set()
    agent = R.ent("agent", "self")
    sit_rel = profile.situating_relation()

    for g in goals:
        if g.target is not None and g.target.kind not in K:
            K.declare(g.target.kind, "place")
        R.add("satisfied", (g.entity(),), None, slot="chk", side="task", t=0,
              span=task[:160], purpose=g.entity())

    prev_act: Optional[int] = None
    situation: Optional[int] = None
    open_intention = None

    for st in steps:
        R.source_steps[st.t] = SourceStep(
            observation=st.env_text,
            agent=st.agent_text,
            modules=dict(st.parts),
            admissible=tuple(st.admissible),
        )
        # --- R over the environment turn -----------------------------
        obs_ids: List[int] = []
        observations = (parsed["observations"][str(st.t)] if parsed is not None
                        else profile.parse_observation(st.env_text, st.admissible))
        for item in observations:
            args = tuple(R.ent(k, i) for k, i in item["args"])
            fid = R.add(item["rel"], args, item["value"], slot="obs", side="env",
                        t=st.t, situation=situation,
                        basis=(prev_act,) if prev_act is not None else (),
                        purpose=profile.bears_on(item, goals),
                        span=item.get("span", ""),
                        value_type=item.get("value_type"))
            obs_ids.append(fid)
            if item["rel"] == sit_rel and item["value"] is True:
                situation = fid          # the situation in force from here on

        # --- R over the agent turn, slot by slot ---------------------
        mind_ids: List[int] = []

        # A recollection asserts relations. Emitting a placeholder would make the
        # criterion compare the chain of the placeholder instead of the chain of
        # what was recalled, so no disagreement could ever be found.
        mem_txt = st.parts.get("memory")
        if mem_txt is not None:
            # The typed claims below are intentionally selective. Preserve the
            # complete slot as a first-class fact as well, otherwise omissions,
            # weakened task contracts, and summary-level causal claims disappear
            # from the IR and a memory diagnosis becomes impossible to inspect.
            raw_mem = R.add(
                "recalled_summary", (agent,), mem_txt, slot="mem", side="mind",
                t=st.t, situation=situation, basis=tuple(obs_ids), span=mem_txt,
                value_type="string",
            )
            mind_ids.append(raw_mem)
            # An attribute the run has never been offered cannot have been
            # selected, so a recollection naming its required value there is the
            # brief restated, not a claim about what happened. Only attributes
            # the environment has put on the page are open to such a claim, and
            # within those the recalled value may be any of them.
            offered = {}
            for f in R.F:
                if f.rel == "in" and f.t < st.t:
                    offered.setdefault(str(f.args[1]).split(":")[1], []).append(
                        str(f.args[0]).split(":")[1])
            known = {a: list(vs) for a, vs in offered.items()}
            for g in goals:
                for a, v in g.attrs:
                    a = a.replace(" ", "_")
                    if a in offered:
                        known[a].append(v)
            items = profile.parse_memory(mem_txt, known)
            for item in items:
                args = tuple(R.ent(k, i) for k, i in item["args"])
                prior = R.chain(item["rel"], args, upto=st.t - 1)
                fid = R.add(item["rel"], args, item["value"], slot="mem", side="mind",
                            t=st.t, situation=situation,
                            basis=tuple(f.fid for f in prior[-2:]),
                            span=item.get("span", ""),
                            value_type=item.get("value_type"))
                scope = dict(item.get('claim_scope') or {'temporal': 'unspecified', 'event_step': None})
                if scope['temporal'] == 'current':
                    scope['event_step'] = st.t
                R.claim_scopes[fid] = scope
                mind_ids.append(fid)
            if not items:
                # This is parser metadata, not a claim that the agent recalled
                # nothing. Nonempty unbound prose remains in recalled_summary.
                fid = R.add("memory_unparsed", (agent,), True, slot="mem", side="mind",
                            t=st.t, situation=situation, basis=tuple(obs_ids),
                            span=mem_txt[:160])
                mind_ids.append(fid)

        refl_txt = st.parts.get("reflection")
        if refl_txt is not None:
            fid = R.add("assessed", (agent,), "stated", slot="refl", side="mind", t=st.t,
                        situation=situation, basis=tuple(obs_ids), span=refl_txt)
            mind_ids.append(fid)

        # --- L: the intention, which the purpose axis hangs from ------
        plan_txt = st.parts.get("plan")
        act_item = profile.parse_action(st.parts.get("action"), st.admissible)
        tracked_goals = [g for g in goals if g.status_mode == "tracked"]
        req_ent = tracked_goals[0].entity() if tracked_goals else (goals[0].entity() if goals else None)
        int_ent = None
        if plan_txt is not None or act_item is not None:
            # The intention comes from the plan. Taking it from the action would
            # make the two identical by construction, and the comparison between
            # them could never fail.
            decl = profile.parse_intention(plan_txt, st.admissible, goals) if plan_txt else None
            if decl is None:
                verb, objs = None, None
                params, execution_options, serves, mentions = (), (), (), ()
                preconditions, expected_effects = (), ()
                commitment, extraction, confidence = "unknown", "absent", 0.0
            else:
                verb = decl.get("rel")
                a = decl.get("args")
                objs = None if a is None else tuple(R.ent(k, i) for k, i in a)
                params = tuple(IntentionParameter(**x) for x in decl.get("parameters", ()))
                execution_options = tuple(
                    IntentionOption(
                        verb=x["verb"],
                        objects=None if x.get("args") is None else
                        tuple(R.ent(k, i) for k, i in x["args"]),
                        role=x.get("role", "alternative"),
                        span=x.get("span", ""),
                        preconditions=tuple(x.get("preconditions", ())),
                        expected_effects=tuple(x.get("expected_effects", ())),
                    ) for x in decl.get("execution_options", ())
                )
                serves = tuple(decl.get("serves", ()))
                mentions = tuple(decl.get("mentions", ()))
                preconditions = tuple(decl.get("preconditions", ()))
                expected_effects = tuple(decl.get("expected_effects", ()))
                commitment = decl.get("commitment", "unknown")
                extraction = decl.get("extraction", "rule")
                confidence = float(decl.get("confidence", 1.0))
            # The purpose axis branches only where a plan says it does. Hanging
            # each intention off the previous one produces a chain as deep as the
            # run, which is the trajectory relabelled rather than a decomposition.
            sub = profile.decompose(plan_txt) if plan_txt else []
            tracked_entities = {str(g.entity()) for g in tracked_goals}
            anchor = next((x for x in serves if str(x) in tracked_entities), req_ent)
            parent = open_intention if (open_intention is not None and sub) else anchor
            int_ent, _ = R.intend(
                st.t, verb, objs, parent, span=(plan_txt or "")[:240],
                parameters=params, execution_options=execution_options,
                serves=serves, mentions=mentions,
                preconditions=preconditions, expected_effects=expected_effects,
                commitment=commitment, extraction=extraction,
                confidence=confidence,
            )
            open_intention = int_ent if sub else None
            if plan_txt is not None:
                fid = R.add("planned", (agent,), "stated", slot="plan", side="mind", t=st.t,
                            situation=situation, basis=tuple(obs_ids + mind_ids),
                            purpose=int_ent, span=plan_txt)
                mind_ids.append(fid)

        if act_item is not None:
            args = tuple(R.ent(k, i) for k, i in act_item["args"])
            # Captured tool calls have no fabricated mental modules. Their
            # preceding observations remain contextual provenance, not proof of
            # the agent's reasoning. Other profiles retain their existing graph.
            action_basis = obs_ids + mind_ids if R.domain == "openclaw" else mind_ids
            aid = R.add(act_item["rel"], args, act_item["value"], slot="act", side="mind",
                        t=st.t, situation=situation, basis=tuple(action_basis),
                        purpose=int_ent, span=act_item["span"],
                        value_type=act_item.get("value_type"))
            R.add("admissibility", (R.ent("int", f"i{st.t}"),), act_item["admissibility"],
                  slot="chk", side="env", t=st.t, basis=(aid,), span=act_item["span"])
            # an action that settles a requirement writes the discharge back, so
            # that a met requirement is visible as a fact rather than inferred
            d = act_item.get("discharges")
            if d is not None and act_item["admissibility"] == "well_formed":
                val = str(d["value"]).lower()
                # which attribute does this value belong to, as the page offered it
                owner = None
                for f in R.F:
                    if f.rel == "in" and f.t <= st.t and str(f.args[0]).endswith(":" + val):
                        owner = f.args[1]
                if owner is None:
                    for g in goals:
                        if any(str(v).lower() == val for _, v in g.attrs):
                            owner = g.target
                if owner is not None:
                    g0 = next((g for g in goals if g.target is not None
                               and str(g.target) == str(owner)), None)
                    q = g0.entity() if g0 is not None else None
                    # taking a value settles the attribute. It meets the
                    # requirement only where it is the value the task named, so
                    # a coarser stand-in is written as a decision that did not
                    # meet it rather than as a discharge.
                    want = [" ".join(str(v).split()).lower()
                            for _, v in (g0.attrs if g0 is not None else ())]
                    met = (not want) or (val in want)
                    R.add(d["rel"], (owner, R.ent("value", val)), met,
                          slot="chk", side="env", t=st.t, basis=(aid,),
                          purpose=q, span=act_item["span"])
            # Domain profiles may expose a deterministic completion rule whose
            # operands are richer than one selected value.  The profile returns
            # fact declarations; the compiler remains responsible for ids,
            # ordering, and grounding.
            if (act_item["admissibility"] == "well_formed"
                    and hasattr(profile, "evaluate_discharges")):
                for outcome in profile.evaluate_discharges(act_item, goals, R, st.t):
                    out_args = tuple(R.ent(k, i) for k, i in outcome["args"])
                    out_basis = tuple(dict.fromkeys(
                        (aid,) + tuple(outcome.get("basis", ()))
                    ))
                    R.add(outcome["rel"], out_args, outcome["value"],
                          slot="chk", side="env", t=st.t,
                          basis=out_basis,
                          purpose=outcome.get("purpose"),
                          span=outcome.get("span", act_item["span"]),
                          value_type=outcome.get("value_type"))
            prev_act = aid
        else:
            prev_act = None

    R.process_notes = build_process_notes(R)
    return R


def invariants(R: Record, steps: List[Step]) -> Dict[str, bool]:
    aligned = R.length() == len(steps)
    by_t = {s.t: s for s in steps}
    def _flat(x): return " ".join(x.split())
    # groundedness means the span occurs in the step. Whitespace is normalised on
    # both sides, because a line break is not a difference in what was said.
    grounded = all(
        (not f.span) or f.t not in by_t
        or (_flat(f.span) in _flat(by_t[f.t].agent_text))
        or (_flat(f.span) in _flat(by_t[f.t].env_text))
        for f in R.F)
    return {"aligned": aligned, "grounded": grounded, "ordered": R.dependence_ok()}
