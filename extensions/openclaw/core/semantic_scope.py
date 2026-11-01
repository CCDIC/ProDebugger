"""Conservative source scope for heuristic agent-module extraction.

These rules admit local declarations, never infer intent from the executed
action. Unrecognized language stays in the original module for semantic review.
"""
import re


def memory_scope(text, start, end):
    """Return the scope of an asserted state, or None for a non-assertion."""
    boundary = max(text.rfind('.', 0, start), text.rfind('!', 0, start),
                   text.rfind('?', 0, start), text.rfind('\n', 0, start)) + 1
    before = text[boundary:start]
    after = re.split(r'[.!?\n]', text[end:], maxsplit=1)[0]
    clause = text[boundary:end] + after
    if re.search(r'\b(?:perhaps|possibly|probably|either|or)\b', clause, re.I):
        return None
    # A quoted command/attempt, possible state, requirement or question does not
    # assert that its target state holds, irrespective of a matching noun.
    if (re.search(r'\b(?:attempt\w*|tr(?:y|ied|ying)|fail\w*|unsuccessful|could|would|should|will|may|might|'
                  r'want\w*|need\w*|plan\w*|hope\w*|if|whether|action|command|task|requirement\w*)\b', before, re.I)
            or re.search(r'\b(?:without success|did not (?:succeed|achieve)|unsuccessful|nothing happens)\b', after, re.I)):
        return None
    temporal, event_step = 'unspecified', None
    # Time labels are local to a sentence/list item. A new sentence must not
    # silently inherit a date from an unrelated old observation.
    anchors = list(re.finditer(r'\b(?:(?:at |in )?step|observation)\s+(\d+)\s*[:,]', before, re.I))
    if anchors:
        temporal, event_step = 'historical', int(anchors[-1].group(1))
    elif re.search(r'\b(?:currently|now|i am|we are|you are)\b', before, re.I):
        temporal = 'current'
    elif re.search(r'\b(?:was|were|previously|earlier|past|arrived|reached|went|picked up|took)\b', clause, re.I):
        temporal = 'historical'
    negative = bool(re.search(r'\b(?:not|never|no longer)\s+(?:(?:at|in|on|holding|carrying)\s+)?(?:the\s+)?$', before, re.I))
    if re.search(r'\b(?:not|never|no longer|cannot|can.t)\b', before, re.I) and not negative:
        return None
    return {'temporal': temporal, 'event_step': event_step,
            'time_basis': 'observation' if re.search(r'\bobservation\b', before, re.I) else 'step',
            'polarity': 'negative' if negative else 'positive', 'modality': 'asserted',
            'source_context': clause.strip(),
            'source_start': boundary + len(clause) - len(clause.lstrip())}


_DIRECTIVE = re.compile(
    r"\b(?:i|we|the agent|the user)\s+(?:should|will|must|need to|plan to|intend to|can)\s+"
    r"|\b(?:the )?(?:next|first|immediate)\s+(?:logical\s+)?(?:step|action)\s+"
    r"(?:is|should be|would be|will be)\s+(?:to\s+)?"
    r"|\b(?:the |my |a helpful )?plan\s+is\s+to\s+"
    r"|\b(?:a |the )?(?:logical |good |best )?(?:action|step)\s+(?:would|should)\s+be\s+to\s+"
    r"|\b(?:it would be (?:logical|helpful|useful) to|let me|i'll)\s+"
    r"|\b(?:a more helpful|a better|the best) approach\s+(?:might|would|should)\s+be\s+to\s+"
    r"|\b(?:a good place to start would be to)\s+", re.I)
_ACTION_START = re.compile(
    r"(?:go(?:ing)?|move|moving|take|taking|pick up|put|place|open(?:ing)?|close|"
    r"examine|inspect|look|use|clean|cool|heat|slice|search(?:ing)?|click(?:ing)?|"
    r"select(?:ing)?|refine|return(?:ing)?|revisit|navigate|navigating|proceed|continue|check)\b", re.I)


def decision_clause(text):
    """Extract a current action declaration; otherwise leave intent unbound.

    Explanations, task descriptions and conditional future branches are not
    execution options. A subsequent explicit 'first'/'instead'/'however'
    declaration supersedes the earlier tentative action.
    """
    text = ' '.join((text or '').split())
    candidates = []
    for sentence in re.split(r'(?<=[.!?])\s+|\s+-\s+', text):
        sentence = sentence.lstrip(' -')
        if not sentence:
            continue
        low = sentence.lower()
        if re.match(r'(?:please explain|what do i expect|i expect|this (?:plan|action) is|'
                    r'the expected|because |if |once |after |when )', low):
            continue
        starts = [(m.end(), 'directive') for m in _DIRECTIVE.finditer(sentence)]
        # A bare command or an explicitly proposed gerund is also a declaration.
        bare = re.match(r'(?:however,? |instead,? |first,? |now,? |the plan is to )*', sentence, re.I)
        if _ACTION_START.match(sentence[bare.end():]):
            starts.append((bare.end(), 'bare'))
        # "Since ... , clicking on Description could ..." names an immediate
        # proposal; it does not name a product option merely mentioned earlier.
        for m in re.finditer(r',\s+(?=(?:clicking|returning|going|moving)\b)', sentence, re.I):
            starts.append((m.end(), 'proposal'))
        for start, origin in starts:
            prefix = sentence[:start]
            if re.search(r'\b(?:if|once|unless|when|after)\b', prefix, re.I):
                continue
            tail = sentence[start:].strip()
            modifier = re.match(r'(?:(?:now|first|next|then|immediately|instead)\s+)*', tail, re.I)
            priority = 2 if re.search(r'\b(?:first|instead)\b', modifier.group(), re.I) else 0
            if re.search(r'\b(?:however|instead)\b', prefix, re.I):
                priority += 2
            tail = tail[modifier.end():].strip()
            # "try to" is a planned attempt (unlike a recalled attempt).
            tail = re.sub(r'^(?:try to|attempt to)\s+', '', tail, flags=re.I).lstrip("'\"")
            if not _ACTION_START.match(tail):
                continue
            tail = re.split(r',\s*(?:as|given|considering)\b|\b(?:because|since|which|as they|as it|to see if|to ensure|'
                            r'in order to|so that|once|if|would|could|will allow|will help)\b', tail, maxsplit=1, flags=re.I)[0].strip(' ,.')
            if re.search(r"\b(?:not|never|avoid|without|before|rather than|previous|previously|earlier)\b", tail, re.I):
                continue
            if tail:
                candidates.append((priority, tail, origin))
    if not candidates:
        return None
    best = max(p for p, _, _ in candidates)
    chosen = [(t, o) for p, t, o in candidates if p == best]
    # Source order is the default execution order; an explicit priority clause
    # may revise it. Alternatives inside the chosen clause remain visible.
    return chosen[-1][0] if best else chosen[0][0]
