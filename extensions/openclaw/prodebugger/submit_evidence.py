"""Bounded original evidence for SUBMIT; extraction hints are not causal verdicts."""
import re

from .record_bridge import digest
from .session_sources import is_session, source_text, feedback_text, feedback_location, message_event

MAX_EVENTS = 12
MODULE_CHARS = 280
OBSERVATION_CHARS = 600
MODULES = ("memory", "reflection", "plan", "action")
STOP = {"a", "an", "the", "some", "to", "in", "on", "at", "with", "from", "of",
        "and", "or", "is", "it", "this", "that", "i", "be", "as", "for", "my",
        "go", "put", "move", "look", "take", "open", "close", "examine", "inventory",
        "step", "task", "goal", "action", "plan", "next", "should", "need", "then",
        "have", "has", "been", "was", "will", "can", "not", "no", "nothing", "happens"}


def words(text):
    return {w.rstrip("s") for w in re.findall(r"[a-z][a-z_]+", (text or "").lower()) if w not in STOP}


def action_key(text):
    # Equality is a recurrence retrieval hint, never a formatting/fault judgement.
    return " ".join((text or "").strip().strip("'\"").lower().split())


def excerpt(text, terms, limit):
    if len(text) <= limit:
        return 0, len(text)
    match = next((m for m in re.finditer(r"[a-z][a-z_]+", text.lower())
                  if m.group().rstrip("s") in terms), None)
    start = max(0, match.start() - limit // 3) if match else 0
    start = min(start, len(text) - limit)
    return start, start + limit


def directed_context(source, candidates):
    steps = source["steps"]
    local, slices, events = {}, {}, {}

    def feedback(t):
        return feedback_text(source, t)

    def module_text(t):
        return {**{m: steps[t - 1]["parts"].get(m) for m in MODULES},
                **({"message": source_text(source, t, "message")} if message_event(source, t) else {})}

    def ref(t, field, text, terms, limit):
        if not isinstance(text, str) or not text:
            return None
        start, end = excerpt(text, terms, limit)
        eid = f"source:{t}:{field}:{start}:{end}"
        slices[eid] = {"evidence_id": eid, "step": t, "source_field": field,
                       "source_digest": digest(text), "span_start": start, "span_end": end,
                       "verbatim": text[start:end], "source_chars": len(text),
                       "excerpted": start > 0 or end < len(text)}
        return eid

    for t in sorted({c["step"] for c in candidates}):
        s = steps[t - 1]
        lid = f"local:{t}"
        local[lid] = {"step": t, "source_digest": digest(s), "env_text": s["env_text"],
                      "module_text": module_text(t),
                      "admissible": s["admissible"], "next_env_text": feedback(t)}
    action_vocabulary = words(" ".join((r["parts"].get("action") or "") for r in steps))
    for candidate in candidates:
        t = candidate["step"]
        s = steps[t - 1]
        lid = f"local:{t}"
        candidate["local_context_ref"] = lid
        key = action_key(s["parts"].get("action"))
        # Retain entity/constraint words also present in recorded actions/observations.
        terms = words(source["task"]) | (words(" ".join(v or "" for v in module_text(t).values())) & action_vocabulary)
        same = [r["t"] for r in steps if r["t"] != t and key
                and action_key(r["parts"].get("action")) == key]
        related = [r["t"] for r in steps if r["t"] != t and terms &
                   words((r["parts"].get("action") or "") + " " + (feedback(r["t"]) or "") + " " + source_text(source, r["t"], "message"))]
        prior = [i for i in related if i < t]
        later_observed = [i for i in related if i > t and feedback(i) is not None
                          and feedback(i).strip().lower() != "nothing happens."]
        later_no_effect = [i for i in related if i > t and feedback(i) is not None
                           and feedback(i).strip().lower() == "nothing happens."]
        cited = sorted({int(m) for m in re.findall(r"\bstep(?:s)?\s+(\d+)",
                       " ".join(str(candidate["assessment"].get(k, ""))
                                for k in ("proposition", "reasoning", "uncertainty")), re.I)
                        if 1 <= int(m) <= len(steps) and int(m) != t})
        buckets = [("previous_event", [t - 1] if t > 1 else []),
                   ("prior_related", prior[-2:]),
                   ("later_related_observation", later_observed[:1] + later_observed[-2:]),
                   ("later_related_no_effect", later_no_effect[:1] + later_no_effect[-1:]),
                   ("prior_same_action", [i for i in same if i < t][:1] + [i for i in same if i < t][-1:]),
                   ("later_same_action", [i for i in same if i > t][:1] + [i for i in same if i > t][-1:]),
                   ("trace_cited_step", cited[:2] + cited[-2:])]
        chosen = {}
        for role, indices in buckets:
            for i in indices:
                if i in chosen or len(chosen) < MAX_EVENTS:
                    chosen.setdefault(i, []).append(role)
        references = []
        for i in sorted(chosen):
            row = steps[i - 1]
            event_id = f"event:{i}"
            if f"local:{i}" in local:
                events[event_id] = {"step": i, "local_context_ref": f"local:{i}", "evidence_refs": []}
                references.append({"event_ref": event_id, "roles": list(dict.fromkeys(chosen[i])),
                                   "temporal_role": "prior" if i < t else "later"})
                continue
            # Related events share source fields; relevant module excerpts are bounded.
            slice_terms = words(source["task"]) | words(row["parts"].get("action"))
            output = feedback_location(source, i)
            refs = [ref(i, "env_text", row["env_text"], slice_terms, OBSERVATION_CHARS),
                    ref(i, "parts.action", row["parts"].get("action"), slice_terms, OBSERVATION_CHARS),
                    ref(output["step"], "env_text", output["text"], slice_terms, OBSERVATION_CHARS) if output else None]
            if message_event(source, i):
                refs.append(ref(i, "session.message", source_text(source, i, "message"), slice_terms, MODULE_CHARS))
            for module in MODULES[:-1]:
                text = row["parts"].get(module) or ""
                if words(text) & terms:
                    refs.append(ref(i, f"parts.{module}", text, slice_terms, MODULE_CHARS))
            refs = list(dict.fromkeys(r for r in refs if r))
            events.setdefault(event_id, {"step": i, "evidence_refs": []})
            events[event_id]["evidence_refs"] = list(dict.fromkeys(events[event_id]["evidence_refs"] + refs))
            references.append({"event_ref": event_id, "roles": list(dict.fromkeys(chosen[i])),
                               "temporal_role": "prior" if i < t else "later"})
        matches = set(same + related + cited + ([t - 1] if t > 1 else []))
        candidate["related_evidence"] = references
        candidate["evidence_selection"] = {"search_range": [1, len(steps)],
            "method": "previous/cited steps and normalized-action/entity-word matches",
            "matching_steps": len(matches), "selected_steps": sorted(chosen),
            "omitted_matching_steps": len(matches - set(chosen)), "max_related_events": MAX_EVENTS,
            "limitation": "Lexical selection is not complete causal/recovery coverage. TRACE citations are unverified hints; "
                          "missing or omitted evidence cannot establish absence, dependency or irreversibility."}
    terminal = {"steps": len(steps), "last_action_step": len(steps),
                "final_feedback_available": False,
                "limitation": "The source has no observation after its final action. Outcome and stop cause are unknown."}
    if is_session(source):
        last_action = max((step["t"] for step in steps if step["parts"].get("action")), default=None)
        terminal = {"steps": len(steps), "last_action_step": last_action,
                    "final_feedback_available": bool(last_action and feedback(last_action)),
                    "final_message_step": max((event["step"] for event in source["session"]["events"]
                                               if event.get("role") == "assistant" and event.get("stop_reason") == "stop"), default=None),
                    "limitation": "Recorded final messages and tool results are available. Their presence does not prove task completion or an internal causal explanation."}
    return {"local_context": local, "related_events": events, "evidence_slices": list(slices.values()),
            "terminal_context": terminal,
            "evidence_policy": {"max_related_events_per_candidate": MAX_EVENTS,
                "related_module_excerpt_chars": MODULE_CHARS, "related_observation_excerpt_chars": OBSERVATION_CHARS,
                "local_context_unit": "unique_candidate_step", "selection_is_semantic_verdict": False}}
