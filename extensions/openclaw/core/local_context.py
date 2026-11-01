"""Rich, diagnosis-neutral context for bottom-up module review.

This module turns one compiled step into a readable evidence packet.  It does
not detect faults or rank candidates.  Keeping this renderer separate from the
DSL executor prevents `views.py` from becoming the place where parsing,
presentation, and diagnosis are all mixed together.
"""
from typing import Any, Dict, Iterable, List

from record import Record


MODULES = ("memory", "reflection", "plan", "action")

REVIEW_QUESTIONS = {
    "memory": (
        "Does the memory faithfully retain the current observation and relevant "
        "prior history, without inventing, dropping, or weakening task state?"
    ),
    "reflection": (
        "Given the observation and memory, does the reflection correctly assess "
        "the last outcome, progress, remaining uncertainty, and cause?"
    ),
    "plan": (
        "Given the task, observation, memory, reflection, available alternatives, "
        "and live constraints, is this plan feasible and decision-relevant?"
    ),
    "action": (
        "Given the plan and admissible controls, does the concrete action realize "
        "the intention with valid syntax, parameters, and preconditions?"
    ),
}


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def _clip(text: Any, limit: int) -> str:
    value = _one_line(text)
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def _module_text(R: Record, step: int, module: str) -> str:
    source = R.source_steps.get(step)
    if source is None:
        return ""
    return str(source.modules.get(module) or "")


def _fids(rows: Iterable[Dict[str, Any]]) -> List[int]:
    return [row["fid"] for row in rows if isinstance(row.get("fid"), int)]


def _recent_history(R: Record, step: int, window: int = 3) -> List[Dict[str, Any]]:
    rows = []
    for prior in range(max(1, step - window), step):
        source = R.source_steps.get(prior)
        if source is None:
            continue
        following = R.source_steps.get(prior + 1)
        rows.append({
            "step": prior,
            "action": _clip(source.modules.get("action"), 300),
            "next_response": _clip(
                "" if following is None else following.observation, 500
            ),
        })
    return rows


def build_rich_step_context(R: Record, episode: Dict[str, Any]) -> Dict[str, Any]:
    """Build the stable local-review contract for one step."""
    step = int(episode["step"])
    source = R.source_steps.get(step)
    following = R.source_steps.get(step + 1)
    observation_text = "" if source is None else source.observation
    next_response_text = "" if following is None else following.observation
    module_text = {name: _module_text(R, step, name) for name in MODULES}
    task_state = episode["before"].get("task_state", [])
    state_text = ", ".join(
        f"{row.get('requirement')}={row.get('state')}" for row in task_state
    ) or "no parsed requirements"
    budget = episode["after"].get("budget_effect") or {}
    progress_text = ", ".join([
        f"command_accepted={budget.get('command_accepted')}",
        f"state_changed={budget.get('state_changed')}",
        f"new_task_evidence={budget.get('new_task_evidence')}",
        f"no_effect={budget.get('no_effect_observed')}",
    ])
    labels = {
        "observation": _clip(observation_text, 280),
        **{name: _clip(module_text[name], 220) for name in MODULES},
        "next_response": _clip(next_response_text, 280),
    }
    narrative_parts = [
        f"Step {step} entered with {state_text}.",
        f"Observation: {labels['observation'] or '(none)' }",
    ]
    for name in MODULES:
        narrative_parts.append(
            f"{name.capitalize()}: {labels[name] or '(no module output)'}"
        )
    narrative_parts.extend([
        f"Next response: {labels['next_response'] or '(terminal/no response)'}",
        f"Recorded progress: {progress_text}.",
    ])

    modules = episode["at"]["modules"]
    current_obs_fids = [
        fact.fid for fact in R.by_step(step) if fact.side == "env"
    ]
    next_obs_fids = _fids(episode["after"].get("observation", []))
    common = {
        "task_contract": R.task,
        "current_observation": observation_text,
        "recent_history": _recent_history(R, step),
        "task_state": task_state,
    }
    input_context = {
        "memory": {
            **common,
            "history_scope": "the three preceding action/response pairs plus current observation",
        },
        "reflection": {
            **common,
            "memory_output": module_text["memory"],
        },
        "plan": {
            **common,
            "memory_output": module_text["memory"],
            "reflection_output": module_text["reflection"],
            "available_targets": episode["before"].get("available_targets"),
            "admissible_controls": [] if source is None else list(source.admissible),
        },
        "action": {
            "task_contract": R.task,
            "plan_output": module_text["plan"],
            "intention": episode["at"].get("intention"),
            "admissible_controls": [] if source is None else list(source.admissible),
            "preconditions": episode["at"].get("preconditions", []),
        },
    }
    review_packets = {}
    for name in MODULES:
        review_packets[name] = {
            "question": REVIEW_QUESTIONS[name],
            "input_context": input_context[name],
            "output_text": module_text[name],
            "output_fids": _fids(modules[name]),
            "next_feedback": {
                "environment_response": next_response_text,
                "environment_fids": next_obs_fids,
                "budget_effect": budget,
                "changes": episode["after"].get("changes", []),
            },
            "evidence_scope": {
                "current_observation_fids": current_obs_fids,
                "upstream_module_fids": {
                    upstream: _fids(modules[upstream])
                    for upstream in MODULES[:MODULES.index(name)]
                },
                "next_observation_fids": next_obs_fids,
            },
            "boundary": (
                "Judge this module's input-output contract locally. Do not infer "
                "global root-cause priority from this packet."
            ),
        }
    return {
        "step_summary": " ".join(narrative_parts),
        "transition": {
            "observation_text": observation_text,
            "module_text": module_text,
            "next_response_text": next_response_text,
            "task_state_text": state_text,
            "progress_text": progress_text,
        },
        "recent_history": _recent_history(R, step),
        "review_packets": review_packets,
    }


def summarize_rich_context(rich: Dict[str, Any]) -> Dict[str, Any]:
    """Loss-bounded projection that keeps prose rather than only counts."""
    out = {
        "step_summary": _clip(rich.get("step_summary"), 1900),
        "transition": {
            "observation_text": _clip(
                rich.get("transition", {}).get("observation_text"), 550
            ),
            "next_response_text": _clip(
                rich.get("transition", {}).get("next_response_text"), 550
            ),
            "task_state_text": rich.get("transition", {}).get("task_state_text"),
            "progress_text": rich.get("transition", {}).get("progress_text"),
        },
        "recent_history": rich.get("recent_history", [])[-2:],
        "review_packets": {},
    }
    for name, packet in rich.get("review_packets", {}).items():
        inputs = packet.get("input_context", {})
        module_specific = {
            "memory": ("history_scope",),
            "reflection": ("memory_output",),
            "plan": ("memory_output", "reflection_output"),
            "action": ("plan_output", "intention", "preconditions"),
        }
        kept_inputs = {key: inputs.get(key) for key in module_specific.get(name, ())}
        for key, value in list(kept_inputs.items()):
            if isinstance(value, str):
                kept_inputs[key] = _clip(value, 320)
        out["review_packets"][name] = {
            "question": packet.get("question"),
            "input_context": kept_inputs,
            "output_text": _clip(packet.get("output_text"), 500),
            "output_fids": packet.get("output_fids", []),
            "next_feedback": {
                "environment_fids": packet.get("next_feedback", {}).get(
                    "environment_fids", []
                ),
                "budget_effect": packet.get("next_feedback", {}).get("budget_effect"),
            },
            "evidence_scope": packet.get("evidence_scope"),
            "boundary": packet.get("boundary"),
        }
    return out
