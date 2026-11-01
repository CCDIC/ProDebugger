"""Parquet to Step lists. The only place that knows the corpus file format.

It extracts three parallel lists per trajectory and nothing else: what the
environment said, what the agent said, and which actions were admissible. Parsing
any of that into facts is the profile's job, not this file's.
"""
import json
import os
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
SPLITS = ("train", "validation", "test")


def load(task_type: Optional[str] = None, root: str = None) -> List[Dict[str, Any]]:
    root = root or os.path.join(HERE, "data")
    rows: List[Dict[str, Any]] = []
    for sp in SPLITS:
        p = os.path.join(root, f"{sp}.parquet")
        if not os.path.exists(p):
            continue
        for r in pq.read_table(p).to_pylist():
            r["_split"] = sp
            rows.append(r)
    if task_type is not None:
        rows = [r for r in rows if r["task_type"] == task_type]
    return rows


_TASK = re.compile(r"(?:^|\n)(?:Your )?task is(?: to)?:\s*(.*?)(?=\nYour (?:current )?observation is:|\nYour admissible|\Z)", re.I | re.S)
_OBS = re.compile(r"(?:current )?observation is:\s*(.*?)(?:\nYour admissible|\nPlease |\Z)", re.S)
_ADM = re.compile(r"admissible actions.*?are:\s*(.*?)(?:\n\n|\nPlease |\Z)", re.S)


def unpack(row: Dict[str, Any]) -> Tuple[str, str, List[str], List[str], List[List[str]]]:
    """Returns (trajectory id, task, env texts, agent texts, admissible lists).

    A trajectory is a flat message list alternating environment and agent. Where
    the last agent turn is missing the pair is dropped, because a step without an
    agent turn is not a step the agent took."""
    msgs = json.loads(row["full_trajectory"])["messages"]
    tid = row["trajectory_id"]
    task_m = _TASK.search(msgs[0]["content"]) if msgs else None
    task = task_m.group(1).strip() if task_m else ""
    if not task:
        raise ValueError("task envelope missing: refusing silent empty task extraction")

    env, agent, adm = [], [], []
    for k in range(0, len(msgs) - 1, 2):
        u, a = msgs[k], msgs[k + 1]
        if u["role"] != "user" or a["role"] != "assistant":
            raise ValueError("unexpected message order: refusing partial trajectory extraction")
        o = _OBS.search(u["content"])
        if o is None:
            raise ValueError(f"observation envelope missing at step {k // 2 + 1}")
        env.append(o.group(1).strip() if o else "")
        agent.append(a["content"])
        m = _ADM.search(u["content"])
        adm.append([x.strip() for x in re.findall(r"'([^']+)'", m.group(1))] if m else [])
    return tid, task, env, agent, adm


def gold(row: Dict[str, Any]) -> Dict[str, Any]:
    """The annotation, kept apart from everything a method may read."""
    return {"step": row["critical_failure_step"],
            "module": row["critical_failure_module"],
            "types": list(row["failure_types"] or []),
            "reasoning": (row["failure_reasonings"] or [""])[0]}
