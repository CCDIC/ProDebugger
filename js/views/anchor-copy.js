// Human-reviewed display copy, keyed by the complete recorded hypothesis.
// A changed/different hypothesis never inherits this case's explanation.
// These are local UI summaries, not new debugger findings or inferred causes.
const REVIEWED = new Map([
  [
    "Origin of the malformed-command fault. The action module emitted a QUOTED command (fid34 value 'look', span \"'look'\"; env chk fid35 admissibility='quoted', obs fid36 effect='none', 'Nothing happens'). Necessary subgoal affected: any admissible interaction needed to locate the keychain. Support: step1 unquoted 'inventory' was accepted, step2 onward navigation is emitted quoted and refused. Counter/gap: later unquoted 'look' (steps 6/8/14/16/22/24) also yields 'you see nothing', so quoting alone may not be the sole cause, and the reflection (fid32) notices failure but never the format.",
    {
      "question": "Did the quotation marks around look stop the command from working?",
      "emphasis": [
        "quotation marks around look"
      ],
      "intent": "The agent wanted to look around the room to find the keychain.",
      "intentEmphasis": [
        "look around the room",
        "keychain"
      ]
    }
  ],
  [
    "Memory module records a phantom state change. Step3's move was refused (quoted, command_accepted=False) yet mem fid45 asserts at(agent,drawer:1) changing place:room->drawer:1 (verdict viol, 'recalls drawer:1 where place:room was shown at t1'). Necessary subgoal affected: correct belief about which containers have actually been reached/searched, needed to direct the keychain search. Support: changes list fid45, and later phantom fids (73,80,94,101,163,203) plus precondition target_visible cited [19,45,66,94,163]. Counter: the phantom memory did not demonstrably suppress any better search choice, and no plan text explicitly claims 'already searched'.",
    {
      "question": "Did the agent remember reaching drawer 1 without the environment confirming it?",
      "emphasis": [
        "remember reaching drawer 1"
      ]
    }
  ],
  [
    "The plan finally selects a sidetable (the required destination object kind) but the action is emitted quoted again (fid69 \"'go to sidetable 1'\"), refused by env; mem fid73 records arrival at sidetable:1 that never happened, and plan fid68 is flagged 'viol: repeats an attempt the environment refused at t2 (quoted)'. Necessary subgoal affected: reaching/examining the destination receptacle and searching cabinets. Support: step7 audits, prior invalid steps. Counter: even had it arrived, object_held was never satisfied (no keychain obtained), so this step alone is not sufficient.",
    {
      "question": "Did the agent repeat a command format that had already produced no effect?",
      "emphasis": [
        "no effect"
      ]
    }
  ],
  [
    "Ten steps in, the action is still emitted quoted (fid104 \"'go to drawer 2'\" refused) and the plan/reflection keep cycling drawer1/drawer2/sidetable1 without ever diagnosing the inadmissible command format; mem fid101 again records a phantom arrival at sidetable:1. Necessary subgoal affected: systematic container search for the keychain. Support: step12 audits, repeated no-effect with admissibility 'quoted'. Counter: steps 6/8/14/16 unquoted 'look' returned 'nothing', suggesting the agent may have believed the room was empty rather than that its commands failed.",
    {
      "question": "Did the agent keep trying commands without checking why they had no effect?",
      "emphasis": [
        "no effect"
      ]
    }
  ],
  [
    "After ~15 refused steps the agent still re-emits the identical quoted navigation (fid139 \"'go to drawer 1'\", plan fid138 flagged viol) instead of changing the command format or the search strategy; the reflection's 'no progress' assessment never isolates the refusal cause. Necessary subgoal affected: locating the keychain before any take/put can occur. Support: step17 audits, invalid_steps covering prior identical actions. Counter: the same quoted pattern was not consistently fatal across the run (step25 unquoted worked), so the fault is intermittent formatting rather than a stable wrong decision.",
    {
      "question": "Did repeating the same drawer command keep the agent from finding the keychain?",
      "emphasis": [
        "same drawer command"
      ]
    }
  ],
  [
    "With most of the budget already burned, act fid166 (\"'go to drawer 1'\") is again inadmissible (preconditions action_admissible 'viol: quoted'), mem fid163 asserts at(agent,drawer:1) built from phantom basis [66,94], and reflection fid164 attributes the failure only to 'Nothing happens' without diagnosing the rejected command, so the loop is not corrected. Necessary subgoal affected: productive exploration for the keychain. Support: step21 audits. Counter: fid195 (step25) shows the agent could emit an admissible command, so the format issue was spontaneously recovered, weakening 'unbreakable loop' framing.",
    {
      "question": "Did the agent keep trying the same command without fixing why it had no effect?",
      "emphasis": [
        "same command",
        "no effect"
      ]
    }
  ],
  [
    "Post-recovery (step25 'go to shelf 2' accepted, revealing only alarmclock2/cellphone3, no keychain), the memory module fid203 AGAIN records at(agent,drawer:1) with change shelf:2->drawer:1 and fid204 at shelf:2, so location beliefs are contaminated by the earlier phantom chain; the plan explores shelf2/drawer1 only and never sweeps the many other containers (16 drawers, dressers, both sidetables) nor ever attempts take/put. Necessary subgoal affected: finding the keychain and then obtaining/placing it. Support: step26 audits, changes fid203/204, env arrival fid197/199. Counter: examining shelf2 and drawer1 are legitimate exploration acts, and the episode is only 30 steps; the failure may simply be budget/cutoff.",
    {
      "question": "Did the agent's remembered location match the place it actually reached?",
      "emphasis": [
        "remembered location"
      ]
    }
  ],
  [
    "Terminal regression: after two admissible steps (27,28), act fid233 is again a QUOTED command (\"'go to drawer 2'\") that env refuses ('Nothing happens'); plan fid232 is flagged 'viol: repeats an attempt the environment refused at t2 (quoted)'. No keychain was ever located and no take/put was ever attempted; the requirement stays unmet with no decision step. Necessary subgoal affected: obtain keychain and put on sidetable. Support: step29 audits; requirement status unmet, evidence_counts changes=0, fully_qualifying_steps=[]. Counter: step30 is cutoff with no feedback, so whether the agent would have recovered is unknown.",
    {
      "question": "Why did quoted commands appear again after two commands had worked?",
      "emphasis": [
        "quoted commands appear again"
      ]
    }
  ]
]);

export function recordedIntent(roles) {
  const fields = (roles.reasoning || []).filter(f => /plan|intent/i.test(f.key) && typeof f.value === "string");
  for (const field of fields) {
    const lines = field.value.split("\n").map(s => s.replace(/^\s*[-*•]\s*/, "").trim());
    for (const pattern of [/what should I do next\?/i, /what do I expect this action to achieve\?/i]) {
      const line = lines.find(s => pattern.test(s));
      const answer = line?.slice(line.indexOf("?") + 1).trim();
      if (answer) return {text: answer, source: field.label};
    }
    const stated = lines.find(s => /^(?:I|We) (?:should|will|want|need|expect|plan)\b/i.test(s));
    if (stated) return {text: stated, source: field.label};
  }
  return null;
}

export function anchorCopy(anchor, roles) {
  const reviewed = REVIEWED.get(anchor.hypothesis);
  const intent = recordedIntent(roles);
  return {
    intent: reviewed?.intent || intent?.text || null,
    intentEmphasis: reviewed?.intentEmphasis || [],
    intentIsQuote: !reviewed?.intent && Boolean(intent),
    intentSource: intent?.source || null,
    question: reviewed?.question || null,
    emphasis: reviewed?.emphasis || [],
    // Keep unfamiliar cases source-based instead of guessing a new question.
    hypothesis: anchor.hypothesis || null,
    reviewed: Boolean(reviewed),
  };
}
