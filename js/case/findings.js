// What each debugger call concluded about each agent step, read only from its tools'
// structured arguments and results, never the model's prose. A step the call touched
// without concluding anything about it is only "read".

// Worst first: a step's link takes the strongest verdict any of its findings carries.
const RANK = {viol: 4, cand: 3, unk: 2, sat: 1, read: 0};
const VERDICT = {viol: "viol", violated: "viol", error: "viol", unk: "unk", uncertain: "unk", unknown: "unk",
                 sat: "sat", satisfied: "sat", ok: "sat", no_error: "sat"};
const WORD = {violated: "viol", satisfied: "sat"};

const ruleName = rule => String(rule || "rule").split("/")[0].split(".").at(-1);

function fromTool(tool, stepOf, add) {
  const a = tool.args && typeof tool.args === "object" ? tool.args : {}, d = tool.result?.data;
  const verdict = raw => VERDICT[raw] || "unk", word = raw => WORD[raw] || String(raw);
  switch (tool.name) {
    case "check":
      if (d?.value != null) add(stepOf(a.fid), {tool: "check", verdict: verdict(d.value), word: word(d.value), subject: `f${a.fid}`, reason: d.reason, cites: d.cites});
      break;
    case "contrast":
      (d?.rows || []).forEach(r => add(r.step ?? stepOf(r.fid), {tool: "contrast", verdict: verdict(r.verdict), word: word(r.verdict), subject: `f${r.fid}`, reason: r.because, cites: [r.fid]}));
      break;
    case "view_evidence":
      (d?.items || []).forEach(e => add(e.t, {tool: "rule", verdict: verdict(e.evaluation_state), word: word(e.evaluation_state), subject: ruleName(e.rule), reason: e.details?.reason, cites: e.refs}));
      break;
    case "route_cases": case "trace_window":
      (a.candidates || []).forEach(c => add(c.issue_step, {tool: "case", verdict: "cand", word: "case", subject: c.candidate_id, reason: c.hypothesis, cites: c.issue_fids}));
      break;
    case "hold":
      (Array.isArray(a.fids) ? a.fids : []).forEach(fid => add(stepOf(fid), {tool: "hold", verdict: "cand", word: "held", subject: `f${fid}`, reason: "kept as evidence for TRACE", cites: [fid]}));
      break;
    case "assess":
      (a.candidates || []).forEach(c => add(c.step, {tool: "assess", verdict: verdict(c.status), word: word(c.status), subject: c.module || "step", fault: c.fault_class,
        reason: [c.fault_class, c.proposition || c.output_quote].filter(Boolean).join(": "), cites: c.fids}));
      break;
  }
}

// step → findings, strongest first (held last), memoised on the call (its tools only change when the call is rebuilt).
export function callFindings(call, factsById) {
  if (call._findings) return call._findings;
  const out = new Map();
  const stepOf = fid => factsById?.get(Number(fid))?.t;
  const add = (t, f) => {
    t = Number(t);
    if (!(t > 0)) return;
    if (!out.has(t)) out.set(t, []);
    out.get(t).push({...f, cites: (Array.isArray(f.cites) ? f.cites : []).map(Number).filter(Number.isFinite)});
  };
  call.tools.filter(t => !t.error).forEach(t => fromTool(t, stepOf, add));
  // Holding a fact says nothing about it: held findings sort after every other kind.
  out.forEach(list => list.sort((a, b) => (a.tool === "hold") - (b.tool === "hold") || RANK[b.verdict] - RANK[a.verdict]));
  return (call._findings = out);
}

export const worstVerdict = list => {
  const told = (list || []).filter(f => f.tool !== "hold");
  return (told.length ? told : list || []).reduce((w, f) => RANK[f.verdict] > RANK[w] ? f.verdict : w, "read");
};

// DSL ids and tuples read as plain words: ('drawer:1',) → drawer 1, place:room → the room,
// agent:self → the agent, t2 → step 2, snake_case → spaced.
export function plainReason(text) {
  return String(text || "")
    .replace(/\(\s*((?:'[^']*'\s*,?\s*)+)\)/g, (_, inner) => inner.match(/'[^']*'/g).map(q => q.slice(1, -1)).join(", "))
    .replace(/\bagent:self\b/g, "the agent")
    .replace(/\bplace:([\w-]+)/g, "the $1")
    .replace(/\breq:([\w-]+)/g, (_, r) => `requirement "${r.replace(/_/g, " ")}"`)
    .replace(/\bint:i(\d+)\b/g, "intention $1")
    .replace(/\b([a-z]+):(\d+)\b/g, "$1 $2")
    .replace(/\bt(\d+)\b/g, "step $1")
    .replace(/\b([a-z]+(?:_[a-z]+)+)\b/g, m => m.replace(/_/g, " "))
    .replace(/\s+/g, " ").trim();
}

// Reasons often start at the verb ("recalls …", "intends put …"): give them the agent as subject.
const NOT_VERB = new Set(["is", "was", "has", "does", "this", "its", "plus", "less", "unless", "across"]);
const firstSentence = (text, max = 170) => {
  let s = plainReason(text).split(/(?<=[.!?])\s/)[0].replace(/[.;:,\s]+$/, "");
  const w = s.match(/^[a-z]+/)?.[0];
  if (w && w.length > 3 && w.endsWith("s") && !w.endsWith("ss") && !NOT_VERB.has(w)) s = `the agent ${s}`;
  return s.length > max ? s.slice(0, max - 1).replace(/\s+\S*$/, "") + "…" : s;
};

// One plain sentence, led by the strongest finding: what this call learned about the step.
// Holding a fact says nothing about it, so a held step leads with what else was found.
export function insightFor(list, label) {
  const f = list?.[0];
  if (!f) return `Only read ${label}; it drew no verdict here.`;
  const sentence = leadFor(f, label);
  return f.tool !== "hold" && list.some(x => x.tool === "hold") ? `${sentence} Kept as evidence for TRACE.` : sentence;
}

function leadFor(f, label) {
  const why = firstSentence(f.reason), tail = why ? `: ${why}.` : ".";
  if (f.tool === "assess") return f.verdict === "viol" ? `TRACE judged the ${f.subject} at ${label} faulty${f.fault ? ` (${plainReason(f.fault)})` : ""}.`
    : f.verdict === "sat" ? `TRACE cleared the ${f.subject} at ${label}.` : `TRACE could not decide whether the ${f.subject} at ${label} is at fault.`;
  if (f.tool === "case") { const h = firstSentence(f.reason, 100); return `Flagged ${label} as candidate ${f.subject} for TRACE to examine${h ? `: ${h}.` : "."}`; }
  if (f.tool === "hold") return `Kept ${f.subject} at ${label} as evidence for TRACE.`;
  if (f.tool === "rule") return f.verdict === "viol" ? `Found a fault at ${label}: rule "${f.subject}" is violated${why ? ` (${why})` : ""}.`
    : `Rule "${f.subject}" at ${label} is ${f.word}${why ? ` (${why})` : ""}.`;
  return f.verdict === "viol" ? `Found a fault at ${label}${tail}` : f.verdict === "sat" ? `${label} checks out${tail}` : `Couldn't settle ${label}${tail}`;
}
