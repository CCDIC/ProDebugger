"""A session over one record.

The session owns the action space, the transcript, and the admissibility
constraint on a conclusion. It does not own the search. A caller decides where
to go; the session records where it went and refuses a conclusion the record
does not support.

Every move is appended to the record as a debugger-side fact, so an
investigation is readable as a continuation of the run it examines.
"""
import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from record import Record, Fact, Entity
from criterion import kappa, SAT, VIOL, UNK
import operators as OPS
import primitives as P
from local_judgement import (FAULT, MODULES as ASSESSMENT_MODULES,
                             SCHEMA as ASSESSMENT_SCHEMA,
                             group_local_instances, validate_step_judgements)


@dataclass
class Call:
    op: str
    args: Tuple[Any, ...]
    n_out: int
    model_call: bool


class Session:
    def __init__(self, record: Record, budget: Optional[int] = None):
        self.R = record
        self.budget = budget
        self.calls: List[Call] = []
        self.compared: set = set()
        self.submitted: Optional[Dict[str, Any]] = None
        self.frontier: List[int] = []            # the caller's working set
        self.notes: List[Dict[str, Any]] = []    # hypotheses, each a dbg fact
        self.local_audit_steps: set = set()
        self.assessments: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self.global_review_after_local = False
        self.full_audit_steps_after_global: set = set()
        if "asked" not in self.R.relations:
            self.R.relations.declare("asked", 1, key=(0,))
        if "concluded" not in self.R.relations:
            self.R.relations.declare("concluded", 1, key=(0,))
        if "hypothesis" not in self.R.relations:
            self.R.relations.declare("hypothesis", 1, key=(0,))
        if "hyp" not in self.R.kinds:
            self.R.kinds.declare("hyp")

    # ---------------- the action space --------------------------------
    def call(self, op: str, *args) -> Any:
        if op not in OPS.ACTION_SPACE:
            raise KeyError(f"{op!r} is not in the action space: "
                           f"{sorted(OPS.ACTION_SPACE)}")
        if self.budget is not None and len(self.calls) >= self.budget:
            raise RuntimeError(f"operator budget of {self.budget} calls is exhausted")
        out = OPS.ACTION_SPACE[op](self.R, *args)  # every call is metered
        n = len(out) if hasattr(out, "__len__") else 1
        # only `check` may spend a model call, and only when it returns UNK
        spends = (op == "check" and getattr(out, "value", None) == UNK)
        self.calls.append(Call(op, args, n, spends))
        e = self.R.ent("hyp", f"q{len(self.calls)}")
        self.R.add("asked", (e,), op, slot="chk", side="dbg", t=self.R.length())
        if op == "contrast" and len(args) >= 2:
            # an n-way contrast settles every pair in it, so the gate's record of
            # what has been compared must be closed over the set, not the call
            for pair in itertools.combinations(args, 2):
                self.compared.add(frozenset(pair))
        if op == "view" and args:
            self._track_view(str(args[0]), out)
        return out

    def _track_view(self, spec: str, out: Any) -> None:
        """Record protocol coverage without interpreting semantic content."""
        source = spec.split("|", 1)[0].strip()
        if source == "local_audits" and isinstance(out, dict):
            coverage = out.get("coverage", {})
            returned = coverage.get("returned_steps", [])
            self.local_audit_steps.update(int(step) for step in returned)
            if self.global_review_after_local and "detail:full" in spec:
                self.full_audit_steps_after_global.update(int(step) for step in returned)
        # `packet` is readable for orientation, but only `global_review`
        # combines it with the completed local ledger and advances the stage.

    @staticmethod
    def _assessment_modules() -> Tuple[str, ...]:
        return ASSESSMENT_MODULES

    def _assessments_complete(self) -> bool:
        required = set(self._assessment_modules())
        return all(
            step in self.assessments
            and set(self.assessments[step]) == required
            and all(item.get("schema") == ASSESSMENT_SCHEMA
                    for item in self.assessments[step].values())
            for step in range(1, self.R.length() + 1)
        )

    def assess_step(self, step: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Persist one rich local judgement for all four modules."""
        if step not in range(1, self.R.length() + 1):
            raise ValueError(f"step must be in 1..{self.R.length()}")
        if step not in self.local_audit_steps:
            raise ValueError("read local_audits for this step before assessing it")
        parsed = validate_step_judgements(self.R, step, payload)
        # A revised local judgement invalidates every downstream global choice.
        # Otherwise a caller could read the packet, rewrite the local ledger,
        # and submit without globally reconsidering the new hypotheses.
        if self.global_review_after_local:
            self.global_review_after_local = False
            self.full_audit_steps_after_global.clear()
            self.frontier.clear()
            self.compared.clear()
        self.assessments[step] = parsed
        e = self.R.ent("hyp", f"local{step}")
        errors = [item for item in parsed.values() if item["status"] == "error"]
        summary = "; ".join(item["proposition"] for item in errors) or "no local error"
        self.R.add("hypothesis", (e,), f"local assessment step {step}: {summary}"[:300],
                   slot="chk", side="dbg", t=self.R.length(),
                   basis=tuple(x["primary_fid"] for x in parsed.values()
                               if isinstance(x.get("primary_fid"), int)),
                   span=summary[:300])
        return {"step": step, "assessments": parsed,
                "coverage": self.protocol()["coverage"]}

    def protocol(self) -> Dict[str, Any]:
        required_steps = set(range(1, self.R.length() + 1))
        assessed = set(self.assessments)
        return {
            "coverage": {
                "audit_read_steps": sorted(self.local_audit_steps),
                "assessed_steps": sorted(assessed),
                "missing_assessment_steps": sorted(required_steps - assessed),
                "local_complete": self._assessments_complete(),
            },
            "global_review_after_local": self.global_review_after_local,
            "full_audit_steps_after_global": sorted(self.full_audit_steps_after_global),
            "held_candidates": list(self.frontier),
        }

    def global_review(self) -> Dict[str, Any]:
        """Return the global trace together with every local error judgement."""
        if not self._assessments_complete():
            missing = self.protocol()["coverage"]["missing_assessment_steps"]
            raise ValueError(
                f"complete local assessments before global review; missing {missing[:10]}"
            )
        packet = self.call("view", "packet")
        ledger = []
        status_counts = {status: 0 for status in
                         ("correct", "error", "uncertain", "not_applicable")}
        for step in sorted(self.assessments):
            for module in self._assessment_modules():
                item = self.assessments[step][module]
                status_counts[item["status"]] += 1
                if item.get("status") in ("error", "uncertain"):
                    ledger.append({"step": step, "module": module, **item})
        self.global_review_after_local = True
        return {
            "schema": "global-review/v2",
            "local_error_ledger": ledger,
            "local_instances": group_local_instances(ledger),
            "local_status_counts": status_counts,
            "packet": packet,
            "selection_protocol": {
                "cluster": (
                    "Group repeated ledger rows that express the same wrong "
                    "commitment or violate the same concrete requirement."
                ),
                "state": (
                    "For each instance, determine fix_status, chain_membership, "
                    "terminal_connection, and wasted steps from the packet."
                ),
                "select": (
                    "Choose the earliest origin among instances still in the "
                    "terminal failure chain; do not choose the earliest anomaly."
                ),
            },
            "protocol": self.protocol(),
        }

    # ---------------- the caller's own state --------------------------
    def hold(self, *fids: int) -> List[int]:
        """Keep facts in the working set. The search is a subgraph, not a path,
        so a caller must be able to carry several candidates at once."""
        for f in fids:
            if not (0 <= f < len(self.R.F)):
                raise IndexError(f"fact {f} does not exist")
            if f not in self.frontier:
                self.frontier.append(f)
        return list(self.frontier)

    def drop(self, *fids: int) -> List[int]:
        self.frontier = [f for f in self.frontier if f not in fids]
        return list(self.frontier)

    def note(self, text: str, cites: List[int] = ()) -> Dict[str, Any]:
        """State a hypothesis. It is appended to the record as a debugger-side
        fact, so an investigation reads as a continuation of the run and a later
        conclusion can cite what it was resting on."""
        e = self.R.ent("hyp", f"h{len(self.notes) + 1}")
        fid = self.R.add("hypothesis", (e,), text[:200], slot="chk", side="dbg",
                         t=self.R.length(), basis=tuple(cites), span=text[:200])
        rec = {"note": text, "fid": fid, "cites": list(cites)}
        self.notes.append(rec)
        return rec

    def recall(self, last: int = 20) -> List[Dict[str, Any]]:
        """What has already been asked, so a caller can avoid repeating itself
        and can see the shape of its own search."""
        return self.transcript()[-last:]

    def cost(self) -> Dict[str, int]:
        return {"operator_calls": self.operator_calls(),
                "model_calls": self.model_calls(),
                "budget": self.budget if self.budget is not None else -1,
                "facts_in_record": len(self.R.F)}

    def model_calls(self) -> int:
        """Counted across the whole session, not the current process. One call of
        the command line is one process, so a counter that lives only in memory
        reports zero forever, which is exactly what it did: forty conclusions
        were recorded as costing no model call when the figure had simply never
        been carried across invocations."""
        return getattr(self, "_replayed_model", 0) + sum(1 for c in self.calls if c.model_call)

    def operator_calls(self) -> int:
        return getattr(self, "_replayed", 0) + len(self.calls)

    def transcript(self) -> List[Dict[str, Any]]:
        return [{"op": c.op, "args": [str(a) for a in c.args],
                 "n_out": c.n_out, "model_call": c.model_call} for c in self.calls]

    # ---------------- the constraint ----------------------------------
    def _bearing_on(self, unmet) -> set:
        """Facts the record can show do NOT bear on any unmet requirement.

        Two earlier definitions were tried and both failed. Reachability through
        `purpose` excluded memory and reflection entirely, because only plan and
        action carry that pointer, and pushed every conclusion onto the slot that
        happened to hold it: of 40 conclusions, 29 named plan where 11 of the
        annotations name memory. The transitive closure of `basis` admitted
        almost everything, since in a linear run every late fact rests on every
        early one.

        The lesson is that "this fact had nothing to do with the failure" is a
        judgement, not a formula, and it belongs to the reasoner. What the record
        can decide is the one case that is derivable: a fact asserted after every
        unmet requirement had already stopped being decidable cannot have borne
        on any of them. Everything else is admitted, and the reasoner is asked to
        say why its choice bears on the failure rather than being refused for it.
        """
        # A qualifying observation is an opportunity, not a recovery deadline.
        # Using the last `could_have` step as a cutoff rejected every later plan
        # in never-reached search tasks (for example, merely seeing a fridge at
        # t1 made an inefficient search plan at t5 structurally unreachable).
        # Only an actual commit closes the agent's recovery window.
        commits = [g.t for g in self.R.F
                   if g.side == "mind" and g.slot == "act" and self.R.commits(g)]
        cutoff = min(commits) if commits else self.R.length()
        return {g.fid for g in self.R.F
                if g.side in ("mind", "env") and g.t <= cutoff}

    def submit(self, fid: int, module: str, fault_class: str,
               evidence: List[int], repair: str) -> Dict[str, Any]:
        self.calls.append(Call("submit", (fid, module, fault_class), 1, False))
        """C_diag. It does not say how to search. It says which conclusions the
        record supports."""
        errs: List[str] = []
        if not (0 <= fid < len(self.R.F)):
            return {"accepted": False, "refused_because": ["fid out of range"]}
        f = self.R.fact(fid)

        # The cheapest and least ambiguous check comes first. Reported after a
        # reachability refusal, an invalid class reads as a verdict about the
        # fact, and three sessions abandoned correct candidates because of it.
        expect = {"obs": "system", "mem": "memory", "refl": "reflection",
                  "plan": "plan", "act": "action", "chk": "system"}[f.slot]
        if module != expect:
            return {"accepted": False, "refused_because":
                    [f"module must be {expect}, the slot of the submitted fact",
                     f"allowed fault classes for {expect}: {FAULT.get(expect, [])}"]}
        if fault_class not in FAULT.get(expect, []):
            return {"accepted": False, "refused_because":
                    [f"fault_class must be one of {FAULT.get(expect, [])} for module {expect}"]}

        # Enforce the two-stage AgentDebug workflow.  The prompt explains how
        # to reason; these checks make skipped stages observable and reject a
        # conclusion that was reached without them.
        if not self._assessments_complete():
            missing = self.protocol()["coverage"]["missing_assessment_steps"]
            errs.append(f"local module assessments incomplete; missing steps {missing[:10]}")
        if not self.global_review_after_local:
            errs.append("global review must be run after local assessments complete")
        if f.t not in self.full_audit_steps_after_global:
            errs.append("selected step needs a full local_audits read after global review")
        if expect in self._assessment_modules():
            local = self.assessments.get(f.t, {}).get(expect, {})
            if local.get("status") != "error" or local.get("primary_fid") != fid:
                errs.append("selected mind fact must be recorded as a local module error "
                            "in the assessment for its step")

        local_error_fids = [
            item["primary_fid"]
            for step in self.assessments.values()
            for module, item in step.items()
            if module in self._assessment_modules()
            and item.get("status") == "error"
            and isinstance(item.get("primary_fid"), int)
        ]
        local_candidate_fids = [
            item["primary_fid"]
            for step in self.assessments.values()
            for module, item in step.items()
            if module in self._assessment_modules()
            and item.get("status") in ("error", "uncertain")
            and isinstance(item.get("primary_fid"), int)
        ]
        if fid not in self.frontier:
            errs.append("selected fact must be held in the global candidate shortlist")
        held_local = set(self.frontier) & set(local_candidate_fids)
        if len(set(local_candidate_fids)) >= 2 and len(held_local) < 2:
            errs.append("global shortlist must retain at least two local candidates "
                        "for cross-examination")

        unmet = OPS.failed(self.R)
        if not unmet:
            errs.append("the run left no requirement unsatisfied")
        if unmet and fid not in self._bearing_on(unmet):
            errs.append("the submitted fact was asserted after every unmet "
                        "requirement had stopped being decidable")

        v = kappa(self.R, f)
        terminal_budget_fact = (
            module == "system" and fault_class == "step_limit"
            and f.t == self.R.length()
            and not any(g.side == "mind" and g.slot == "act" and self.R.commits(g)
                        for g in self.R.F)
        )
        if v.value == SAT and not terminal_budget_fact:
            errs.append(f"the submitted fact passes its own check: {v.reason}")

        # The reasoner's candidate ledger is explicit in `frontier`. Requiring a
        # root to be contrasted with every VIOL in a long trajectory turns
        # parser base rates into dozens of irrelevant gate obligations. Compare
        # held candidates; if none were held, conservatively compare only the
        # other mind slots at the same step.
        candidate_ids = self.frontier
        # Global comparison concerns semantic local-error hypotheses, including
        # ones whose deterministic support is UNK. Restricting this to VIOL
        # would silently skip exactly the cases the reasoner must weigh.
        local_candidate_set = set(local_candidate_fids)
        rivals = [rid for rid in candidate_ids
                  if rid != fid and rid in local_candidate_set]
        uncompared = [r for r in rivals if frozenset((fid, r)) not in self.compared]
        if uncompared:
            errs.append(f"{len(uncompared)} rival candidate(s) not contrasted: {uncompared[:6]}")

        if not evidence:
            errs.append("no evidence cited")
        else:
            # A local module contract includes its immediate environment
            # response. Permit that one-step outcome, but not arbitrary future
            # evidence that could retroactively redefine the local error.
            late = [e for e in evidence if not (0 <= e < len(self.R.F))
                    or self.R.fact(e).t > f.t + 1]
            if late:
                errs.append(
                    f"evidence reads beyond the immediate next-response scope: {late[:6]}"
                )

        if not repair or "answer is" in repair.lower():
            errs.append("the repair is empty or discloses a reference answer")

        if errs:
            return {"accepted": False, "refused_because": errs}

        e = self.R.ent("hyp", f"root{fid}")
        self.R.add("concluded", (e,), fault_class, slot="chk", side="dbg",
                   t=self.R.length(), basis=(fid,), span=repair[:200])
        self.submitted = {"accepted": True, "step": f.t, "fid": fid, "module": module,
                          "fault_class": fault_class,
                          "operator_calls": self.operator_calls(),
                          "model_calls": self.model_calls()}
        return self.submitted

    # ---------------- the ablation ------------------------------------
    def default_rule(self) -> Optional[Dict[str, Any]]:
        """Eq. (10) with a fixed traversal and no agent. Reported as an ablation,
        not as the method."""
        marks = [(f.t, f.fid) for f in self.R.F
                 if f.side == "mind" and kappa(self.R, f).value == VIOL]
        if not marks:
            return None
        t, fid = min(marks)
        f = self.R.fact(fid)
        return {"step": t, "fid": fid, "slot": f.slot}
