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
      if (d?.value != null) add(stepOf(a.fid), {verdict: verdict(d.value), word: word(d.value), subject: `f${a.fid}`, reason: d.reason, cites: d.cites});
      break;
    case "contrast":
      (d?.rows || []).forEach(r => add(r.step ?? stepOf(r.fid), {verdict: verdict(r.verdict), word: word(r.verdict), subject: `f${r.fid}`, reason: r.because, cites: [r.fid]}));
      break;
    case "view_evidence":
      (d?.items || []).forEach(e => add(e.t, {verdict: verdict(e.evaluation_state), word: word(e.evaluation_state), subject: ruleName(e.rule), reason: e.details?.reason, cites: e.refs}));
      break;
    case "route_cases": case "trace_window":
      (a.candidates || []).forEach(c => add(c.issue_step, {verdict: "cand", word: "case", subject: c.candidate_id, reason: c.hypothesis, cites: c.issue_fids}));
      break;
    case "hold":
      (Array.isArray(a.fids) ? a.fids : []).forEach(fid => add(stepOf(fid), {verdict: "cand", word: "held", subject: `f${fid}`, reason: "kept as evidence for TRACE", cites: [fid]}));
      break;
    case "assess":
      (a.candidates || []).forEach(c => add(c.step, {verdict: verdict(c.status), word: word(c.status), subject: c.module || "step",
        reason: [c.fault_class, c.proposition || c.output_quote].filter(Boolean).join(": "), cites: c.fids}));
      break;
  }
}

// step → findings, strongest first, memoised on the call (its tools only change when the call is rebuilt).
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
  out.forEach(list => list.sort((a, b) => RANK[b.verdict] - RANK[a.verdict]));
  return (call._findings = out);
}

export const worstVerdict = list => (list || []).reduce((w, f) => RANK[f.verdict] > RANK[w] ? f.verdict : w, "read");
