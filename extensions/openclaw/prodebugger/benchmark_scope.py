"""Task-specific diagnostic vocabulary and benchmark-only exclusions."""
import re
from .record_bridge import CORE  # Initialize the established core import path.
from .evidence_tools import ToolError
from local_judgement import FAULT, MODULES

EXCLUDED = {"format_error", "spelling_error", "syntax_error", "typo"}
ALLOWED = {module: [name for name in names if name not in EXCLUDED]
           for module, names in FAULT.items()}
OPENCLAW_ALLOWED = {module: list(FAULT[module]) for module in MODULES}


def is_openclaw(task_type=None):
    if isinstance(task_type, dict):
        task_type = task_type.get("task_type")
    return task_type == "openclaw"


def allowed(task_type=None):
    from .session_sources import is_session
    if is_session(task_type):
        return {**OPENCLAW_ALLOWED, "message": ["statement_mismatch", "unsupported_claim", "task_misalignment"]}
    return OPENCLAW_ALLOWED if is_openclaw(task_type) else ALLOWED


def validate_cause(module, item, task_type=None):
    """Reject overt action-format relabelling, not a general semantic classifier."""
    if is_openclaw(task_type) or item.get("status") != "error" or module != "action":
        return
    reason = " ".join(str(item.get(k, "")) for k in ("proposition", "reasoning", "immediate_effect"))
    if re.search(r"\b(?:format(?:ting)?|spell(?:ing)?|typo|syntax|serializ\w*)\b|"
                 r"(?:literal|surrounding) (?:single |double )?quot|quoted (?:string|form|command|action)|"
                 r"(?:rather than|instead of) (?:the )?bare|unquoted", reason, re.I):
        raise ToolError("excluded_benchmark_cause", "Action formatting/spelling/quoted-vs-bare causes are excluded, "
                        "even under invalid_action/parameter_error/misalignment. Evaluate supported semantic choices within the current phase's output contract.")


def scope(task_type=None):
    if is_openclaw(task_type):
        return {"schema": "trajectory-error-scope/v1", "allowed_by_module": allowed(task_type),
                "source": "core/local_judgement.py:FAULT for source-backed agent modules",
                "excluded_causes": [],
                "premise": "Assess only original modules and existing Facts. Tool calls belong to action; "
                           "missing memory, reflection or plan text is unknown, not an invented module. "
                           "Formatting or serialization may matter when actual tool feedback establishes an effect. "
                           "System is not a selectable module without a supported source and Fact mapping. "
                           "An agent fault is not necessarily the cause of final task failure; check the verified task outcome and recovery. "
                           "When message is an allowed module, it means recorded public assistant text/final answers, never internal memory. "
                           "Compare those statements with original task requirements and tool evidence; do not infer their hidden causes."}
    return {"schema": "benchmark-error-scope/v1", "allowed_by_module": ALLOWED,
            "source": "core/local_judgement.py:FAULT with user-requested exclusions",
            "excluded_causes": ["formatting", "spelling", "literal quotation marks", "serialization"],
            "premise": "All genuine benchmark errors are within allowed_by_module. "
                       "Formatting and spelling cannot cause the benchmark failure. "
                       "Do not relabel these causes as another allowed error."}
