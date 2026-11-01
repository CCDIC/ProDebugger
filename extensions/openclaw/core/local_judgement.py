"""Versioned IR and validation for LLM-produced local module judgements."""
import re
from typing import Any, Dict, List, Optional

from record import Record


SCHEMA = "local-judgement/v2"
MODULES = ("memory", "reflection", "plan", "action")
SLOTS = {"memory": "mem", "reflection": "refl", "plan": "plan", "action": "act"}
FAULT = {
    "memory": ["hallucination", "over_simplification", "memory_retrieval_failure"],
    "reflection": ["progress_misjudge", "causal_misattribution",
                   "outcome_misinterpretation"],
    "plan": ["inefficient_plan", "constraint_ignorance", "impossible_action"],
    "action": ["invalid_action", "parameter_error", "misalignment", "format_error"],
    "system": ["environment_error", "step_limit", "llm_limit",
               "tool_execution_error"],
}
STATUSES = ("correct", "error", "uncertain", "not_applicable")


def _text(value: Any, field: str, required: bool = False, limit: int = 1200) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} must not be empty")
    return value[:limit]


def _fid_list(R: Record, value: Any, field: str, step: int) -> List[int]:
    if not isinstance(value, list) or any(not isinstance(fid, int) for fid in value):
        raise ValueError(f"{field} must be a list of integer fact ids")
    unique = list(dict.fromkeys(value))
    invalid = [fid for fid in unique if not (0 <= fid < len(R.F))]
    if invalid:
        raise ValueError(f"{field} has nonexistent fact ids {invalid[:6]}")
    future = [fid for fid in unique if R.fact(fid).t > step + 1]
    if future:
        raise ValueError(
            f"{field} reads beyond local next-response scope at step {step}: {future[:6]}"
        )
    return unique


def _instance_key(module: str, proposition: str, requirements: List[str]) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", proposition.lower()).strip()
    return "|".join([module, ",".join(sorted(requirements)), words[:240]])


def validate_step_judgements(
    R: Record, step: int, payload: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """Validate and normalize one complete four-module local review."""
    if not isinstance(payload, dict):
        raise ValueError("assessment payload must be a JSON object")
    if set(payload) != set(MODULES):
        raise ValueError(f"assessment needs exactly {list(MODULES)}")
    known_requirements = {str(goal.entity()) for goal in R.goals}
    normalized: Dict[str, Dict[str, Any]] = {}
    for module in MODULES:
        raw = payload[module]
        if not isinstance(raw, dict):
            raise ValueError(f"{module} judgement must be a JSON object")
        status = raw.get("status")
        if status not in STATUSES:
            raise ValueError(f"{module}.status must be one of {STATUSES}")
        output_fids = [
            fact.fid for fact in R.by_step(step)
            if fact.side == "mind" and fact.slot == SLOTS[module]
        ]
        primary: Optional[int] = raw.get("primary_fid")
        if primary is not None and not isinstance(primary, int):
            raise ValueError(f"{module}.primary_fid must be an integer or null")
        if primary is not None and primary not in output_fids:
            raise ValueError(
                f"fid {primary} is not a {module} output fact at step {step}"
            )
        fault_class = raw.get("fault_class")
        if fault_class is not None and fault_class not in FAULT[module]:
            raise ValueError(
                f"invalid {module} class {fault_class!r}; expected {FAULT[module]}"
            )
        if status == "not_applicable" and output_fids:
            raise ValueError(f"{module} has output at step {step}; it is not not_applicable")
        if status != "not_applicable" and not output_fids:
            raise ValueError(f"{module} has no output at step {step}; use not_applicable")
        if status == "error":
            if primary is None or fault_class is None:
                raise ValueError(
                    f"{module} error requires primary_fid and fault_class"
                )
        if status in ("correct", "not_applicable") and (
            primary is not None or fault_class is not None
        ):
            raise ValueError(
                f"{module} {status} judgement cannot name a fault or primary fid"
            )
        proposition = _text(
            raw.get("proposition"), f"{module}.proposition",
            required=status in ("error", "uncertain"),
        )
        reasoning = _text(
            raw.get("reasoning"), f"{module}.reasoning",
            required=status != "not_applicable",
        )
        immediate_effect = _text(
            raw.get("immediate_effect"), f"{module}.immediate_effect",
            required=status == "error",
        )
        uncertainty = _text(raw.get("uncertainty"), f"{module}.uncertainty")
        evidence_for = _fid_list(
            R, raw.get("evidence_for", []), f"{module}.evidence_for", step
        )
        evidence_against = _fid_list(
            R, raw.get("evidence_against", []), f"{module}.evidence_against", step
        )
        if status == "error" and primary not in evidence_for:
            raise ValueError(
                f"{module}.evidence_for must include primary_fid {primary}"
            )
        requirements = raw.get("requirement_links", [])
        if not isinstance(requirements, list) or any(
            not isinstance(item, str) for item in requirements
        ):
            raise ValueError(f"{module}.requirement_links must be a string list")
        requirements = list(dict.fromkeys(requirements))
        unknown = [item for item in requirements if item not in known_requirements]
        if unknown:
            raise ValueError(f"{module} names unknown requirements {unknown}")
        normalized[module] = {
            "schema": SCHEMA,
            "status": status,
            "fault_class": fault_class,
            "primary_fid": primary,
            "proposition": proposition,
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
            "immediate_effect": immediate_effect,
            "requirement_links": requirements,
            "uncertainty": uncertainty,
            "reasoning": reasoning,
            "instance_key": _instance_key(module, proposition, requirements),
        }
    return normalized


def group_local_instances(ledger: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Conservatively collapse exact repeated local propositions.

    Semantic paraphrases deliberately remain separate. The global reasoner may
    merge them, but deterministic code must not pretend string similarity proves
    that two errors are the same causal instance.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in ledger:
        groups.setdefault(str(row.get("instance_key")), []).append(row)
    instances = []
    for number, (_, rows) in enumerate(sorted(
        groups.items(), key=lambda item: min(x["step"] for x in item[1])
    ), start=1):
        rows.sort(key=lambda row: (row["step"], row["module"]))
        first = rows[0]
        instances.append({
            "instance_id": f"local:{number}",
            "origin_step": first["step"],
            "last_step": rows[-1]["step"],
            "module": first["module"],
            "proposition": first.get("proposition"),
            "requirement_links": first.get("requirement_links", []),
            "occurrences": [
                {"step": row["step"], "module": row["module"],
                 "primary_fid": row.get("primary_fid")}
                for row in rows
            ],
            "fix_status": None,
            "chain_membership": None,
            "terminal_connection": None,
            "wasted_steps": [],
            "state_status": "not_computed",
            "clustering_status": "exact_normalized_proposition_only",
        })
    return instances
