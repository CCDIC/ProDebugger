// Builds the Query Graph scene from one run directory: the DSL record (facts, entities,
// goals, rule evidence) plus the debugger's tool calls replayed as an event timeline.
// Every event says which DSL nodes it touched, so the renderer only has to light them.
import {getJSON, JsonlFollower} from "../api.js?v=27bf1baa85";
import {short} from "../util.js?v=27bf1baa85";
import {cap, faultText, goalText, question, statusText, stepList, VERDICT_WORD} from "./narrate.js?v=23f9bfd6b4";

export const SLOTS = ["obs", "mem", "refl", "plan", "act", "chk"];
export const SLOT_LABEL = {obs: "observation", mem: "memory", refl: "reflection", plan: "plan", act: "action", chk: "check"};
const MODULE_SLOT = {observation: "obs", memory: "mem", reflection: "refl", plan: "plan", action: "act", check: "chk"};
// Keys whose integer values name a fact the result put in front of the debugger.
const FACT_KEYS = new Set(["fid", "fids", "refs", "issue_fids", "returned_fids"]);

const entityKey = e => e && typeof e === "object" ? `${e.kind}:${e.index}` : String(e);

async function readJsonl(ref, name) {
  const follower = new JsonlFollower(ref, name);
  let added;
  do { added = await follower.poll(); } while (added.length);
  return follower.rows;
}

export async function loadQueryScene(ref) {
  const [record, evidences, routeCandidates, result, managerPlan, goalMap, toolCalls, traceQueries, input] = await Promise.all([
    getJSON(ref, "canonical_record.json"),
    getJSON(ref, "evidences.json", {}),
    getJSON(ref, "route_candidates.json", []),
    getJSON(ref, "result.json", null),
    getJSON(ref, "manager_plan.json", null),
    getJSON(ref, "goal_map.json", null),
    readJsonl(ref, "tool_calls.jsonl"),
    readJsonl(ref, "trace_queries.jsonl"),
    getJSON(ref, "input.json", null),
  ]);
  const scene = buildQueryScene({record, evidences, routeCandidates, result, managerPlan, goalMap, toolCalls, traceQueries});
  scene.source = {input, goalMap, result, managerPlan};
  return scene;
}

// Collects fact ids (and evidence ids) anywhere inside a tool result.
function collectIds(value, out = {fids: new Set(), eids: new Set()}) {
  if (Array.isArray(value)) value.forEach(v => collectIds(v, out));
  else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) {
      if (FACT_KEYS.has(k)) (Array.isArray(v) ? v : [v]).forEach(x => Number.isInteger(x) && out.fids.add(x));
      else if (k === "eid" && Number.isInteger(v)) out.eids.add(v);
      collectIds(v, out);
    }
  }
  return out;
}

// Routing signals that point at trouble (the benign ones, like a state change, are left out).
export const SIGNAL_LABEL = {
  no_new_task_evidence: "no progress", schema_repetition: "same pattern", near_duplicate_action: "near-repeat",
  repeated_action: "repeated action", unbound_intention: "vague plan", invalid_action: "invalid action",
  no_effect: "no effect", failed_precondition: "failed precondition", missing_effect: "missing effect",
  terminal_cutoff: "cut off", claims_task_complete: "claims done", contradicted_completion: "false completion",
  constraint_substitution: "constraint swapped", wrong_task_decision: "wrong decision", alignment_violation: "plan/action mismatch",
};

function countSignals(value, out = new Map()) {
  if (Array.isArray(value)) value.forEach(v => countSignals(v, out));
  else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) {
      if ((k === "signals" || k === "routing_signals") && Array.isArray(v)) {
        for (const s of v) if (SIGNAL_LABEL[s]) out.set(s, (out.get(s) || 0) + 1);
      } else countSignals(v, out);
    }
  }
  return out;
}

// What a view asks, in plain words, with its step range.
const VIEW_INTENT = {
  requirements: "read the task goal", episodes: "review the phases", scan: "scan every step", local_audits: "audit every step",
  changes: "list state changes", steps: "read steps", state: "read the world state", module: "read the agent's own text",
  packet: "read the evidence packet", statement_discovery: "find the agent's statements", rules: "list the rules",
};
export function viewIntent(name, spec) {
  if (name === "view_evidence") return "look up rule evidence";
  const [base, ...mods] = String(spec || "").split("|").map(s => s.trim());
  const range = mods.map(m => m.match(/^steps:(\d+):(\d+)$/)).find(Boolean);
  const section = mods.find(m => m.startsWith("section:"))?.slice(8).split(",")[0].replace(/_/g, " ");
  let text;
  if (base.startsWith("entity:req:")) text = section ? `check the goal's ${section}` : "check the goal";
  else if (base.startsWith("entity:")) text = `follow the ${base.split(":").pop().replace(/_/g, " ")}`;
  else text = VIEW_INTENT[base.split(":")[0]] || VIEW_INTENT[base.split(":").pop()] || `read ${base.replace(/_/g, " ")}`;
  if (range) text = / every step$/.test(text) ? text.replace(/every step$/, `steps ${range[1]}–${range[2]}`) : `${text} in steps ${range[1]}–${range[2]}`;
  return text;
}

// The goal's recorded state, wherever the view put it.
function goalState(data) {
  const s = data?.status || data?.requirements?.[0]?.status;
  const state = s?.state || data?.requirements?.[0]?.terminal_state;
  return state ? {state, decided: s?.decision_steps || []} : null;
}

export function buildQueryScene({record, evidences, routeCandidates, result, managerPlan, goalMap, toolCalls, traceQueries}) {
  const facts = (record.facts || []).map(f => ({
    id: `f${f.fid}`, type: "fact", fid: f.fid, t: f.t, slot: f.slot, side: f.side, rel: f.rel,
    args: (f.args || []).map(entityKey), value: f.value, basis: f.basis || [], span: f.span || "",
  }));
  const factById = new Map(facts.map(f => [f.fid, f]));
  const steps = Math.max(1, ...facts.map(f => f.t));
  const cellFacts = (t, slot) => facts.filter(f => f.t === t && f.slot === slot);

  // Entities: every key in the record, plus any argument the record forgot to list.
  const kinds = record.kinds || {};
  const entityMap = new Map();
  const addEntity = key => {
    if (entityMap.has(key)) return entityMap.get(key);
    const [kind, ...rest] = key.split(":");
    const e = {id: `e:${key}`, type: "entity", key, kind, index: rest.join(":"), parent: kinds[kind] ?? null, facts: []};
    entityMap.set(key, e);
    return e;
  };
  Object.keys(record.entities || {}).forEach(addEntity);
  facts.forEach(f => f.args.forEach(a => addEntity(a).facts.push(f.fid)));
  const entities = [...entityMap.values()].filter(e => e.kind !== "req");

  const goals = (record.goals || []).map(g => {
    const key = `req:${g.name || `${g.rel}_${g.of_kind}`}`;
    return {
      id: `g:${key}`, type: "goal", key, name: g.name, rel: g.rel, count: g.count, ofKind: g.of_kind,
      target: entityKey(g.target), constraints: g.constraints || [],
      facts: facts.filter(f => f.args.includes(key)).map(f => f.fid),
    };
  });

  const evidence = Object.values(evidences || {}).flatMap(group => (group.items || []).map(item => ({
    id: `v${item.eid}`, type: "evidence", eid: item.eid, kind: item.kind, rule: item.rule, t: item.t,
    refs: (item.refs || []).filter(fid => factById.has(fid)), status: item.status,
    reason: item.details?.reason || "",
  })));
  const evidenceById = new Map(evidence.map(v => [v.eid, v]));

  const lastVersion = Array.isArray(routeCandidates) ? routeCandidates[routeCandidates.length - 1] : routeCandidates;
  const windows = (lastVersion?.windows || []).map(w => ({
    id: `w:${w.candidate_id}`, type: "window", caseId: w.candidate_id, step: w.issue_step,
    fids: (w.issue_fids || []).filter(fid => factById.has(fid)), goals: w.goal_links || [], hypothesis: w.hypothesis || "",
  }));
  const windowByCase = new Map(windows.map(w => [w.caseId, w]));

  // A module named at a step resolves to the facts in that (step, slot) cell.
  const moduleFact = (step, module, fid) => {
    if (factById.has(fid)) return fid;
    const cell = cellFacts(step, MODULE_SLOT[module] || module);
    return cell.length ? cell[0].fid : null;
  };

  const lanes = new Map();
  const candidates = [];
  const rules = managerPlan?.decision_rules?.length || 0;
  const events = [];
  const committed = new Map();
  for (const row of toolCalls) if (row.event === "committed") committed.set(row.tool_call_id || row.ordinal, row);

  events.push({
    phase: "PARSE", name: "compile", title: "Compile the log into the DSL record",
    caption: `${facts.length} facts over ${steps} steps: every observation, memory, reflection, plan and action becomes one fact, saying who did or thought what, and at which step.`,
    reveal: "base", fids: [], eids: [], entities: [], goals: [],
  });

  for (const call of [...committed.values()].sort((a, b) => a.ordinal - b.ordinal)) {
    const data = call.result?.data;
    const error = call.result?.error;
    const args = call.arguments || {};
    const ev = {phase: call.phase, name: call.name, ordinal: call.ordinal, call, error, fids: [], eids: [], goals: [], windows: [], lanes: [], candidates: []};
    if (error) {
      ev.intent = viewIntent(call.name, args.spec);
      ev.raw = `${call.name}(${args.spec ? JSON.stringify(args.spec) : ""})`;
      ev.title = `Rejected: ${ev.intent}`;
      ev.caption = `The tool refused ${call.phase}'s request to ${ev.intent}: ${short(error.message, 120)}`;
      events.push(ev);
      continue;
    }
    const ids = collectIds(data);
    if (call.name === "view" || call.name === "view_evidence") {
      ev.fids = [...ids.fids].filter(fid => factById.has(fid));
      ev.eids = [...ids.eids].filter(eid => evidenceById.has(eid));
      const spec = args.spec || (call.name === "view_evidence" ? "evidence" : "");
      ev.spec = spec;
      if (spec === "requirements" || spec.startsWith("entity:req:")) {
        ev.goals = goals.map(g => g.id);
        for (const g of goals) ev.fids.push(...g.facts);
      }
      ev.fids = [...new Set(ev.fids)];
      ev.intent = viewIntent(call.name, spec);
      ev.signals = [...countSignals(data)].sort((a, b) => b[1] - a[1]);
      ev.goal = goalState(data);
      ev.changes = Array.isArray(data?.changes) ? (data.truncation?.sections?.find(s => s.path === "changes")?.total ?? data.changes.length) : null;
      ev.raw = `${call.name}(${JSON.stringify(spec)})`;
      ev.title = cap(ev.intent);
      const signs = ev.signals.slice(0, 3).map(([k, n]) => `${SIGNAL_LABEL[k]} ×${n}`).join(", ");
      ev.caption = call.phase === "MAP"
        ? `MAP reads the task's goal${goals.length ? ` (${goals.map(g => goalText(g.key)).join("; ")})` : ""}${ev.goal ? ` and finds it ${ev.goal.state}` : ""}. Every suspicion ROUTE raises later has to name the goal it hurts.`
        : `ROUTE asks the record to ${ev.intent}: ${ev.fids.length} fact${ev.fids.length === 1 ? " comes" : "s come"} back${signs ? `; trouble signs: ${signs}` : ev.goal ? `; the goal is ${ev.goal.state}` : ""}.`;
    } else if (call.name === "route_cases") {
      const cases = args.candidates || [];
      ev.windows = cases.map(c => `w:${c.candidate_id}`).filter(id => windows.some(w => w.id === id));
      ev.fids = [...ids.fids].filter(fid => factById.has(fid));
      ev.raw = `route_cases → ${cases.map(c => `${c.candidate_id}@S${c.issue_step}`).join(", ")}`;
      ev.title = `Open ${cases.length === 1 ? "a suspect window" : `${cases.length} suspect windows`}: ${stepList(cases.map(c => c.issue_step))}`;
      ev.caption = `ROUTE ends its search and marks ${cases.length === 1 ? "one place" : `${cases.length} places`} to investigate: ${cases.map(c => `step ${c.issue_step} (${(c.issue_fids || []).length} clue facts)`).join("; ")}. These are suspicions, not verdicts.`;
    } else if (call.name === "plan_trace") {
      ev.phase = "MANAGER";
      for (const a of args.assignments || []) {
        const lane = {id: `l:${a.agent_id}`, type: "lane", agent: a.agent_id, anchor: a.anchor_case_id, step: windowByCase.get(a.anchor_case_id)?.step,
                      cases: a.route_case_ids || [], question: a.question || "", targets: a.search_targets || [], evidence: a.evidence || [],
                      rows: [], steps: [], fids: [], candidates: [], purpose: "", cards: [], scopes: [], sql: "", assessNote: ""};
        lanes.set(a.agent_id, lane);
        ev.lanes.push(lane.id);
        const w = windowByCase.get(a.anchor_case_id);
        if (w) ev.windows.push(w.id);
      }
      const planned = ev.lanes.map(id => [...lanes.values()].find(l => l.id === id));
      ev.raw = `plan_trace → ${planned.map(l => `${l.agent} ← ${l.anchor}`).join(", ")}`;
      ev.title = `Plan the investigation: ${planned.length === 1 ? "one question" : `${planned.length} questions`}`;
      const fixed = rules ? ` Before any evidence comes in, it fixes ${rules} decision rule${rules === 1 ? "" : "s"} for judging the answer${planned.length === 1 ? "" : "s"}.` : "";
      ev.caption = planned.length === 1
        ? `The manager turns the suspicion at step ${planned[0].step} into one question for TRACE: “${question(planned[0], 150)}”${fixed}`
        : `The manager turns each suspicion into one question for TRACE, for ${stepList(planned.map(l => l.step))}.${fixed}`;
    } else if (call.name === "query_logs") {
      const lane = lanes.get(args.candidate_id || data?.candidate_id);
      const rows = data?.rows || [];
      const fids = new Set([...ids.fids].filter(fid => factById.has(fid)));
      for (const r of rows) cellFacts(r.step, MODULE_SLOT[r.module] || r.module).forEach(f => fids.add(f.fid));
      ev.fids = [...fids];
      ev.sql = data?.query || args.sql || "";
      ev.lanes = lane ? [lane.id] : [];
      if (lane) {
        lane.rows = rows; lane.steps = data?.returned_steps || []; lane.fids = ev.fids;
        lane.purpose = (ev.sql.match(/Purpose:\s*(.+)/) || [])[1]?.trim() || "";
        // The agent's sentences the search returned, split into structured statements.
        lane.cards = data?.agent_statements?.cards || [];
        // Every search function the SQL called, with how many rows it matched.
        lane.scopes = (data?.function_scopes || []).map(f => ({fn: f.function, args: f.arguments || [], matched: f.matched_rows ?? 0}));
        lane.sql = ev.sql;
      }
      ev.raw = `query_logs(${lane?.agent || "?"})`;
      ev.title = `Search the logs for step ${lane?.step ?? "?"}`;
      const purpose = lane?.purpose ? ` The search sets out to ${short(lane.purpose[0].toLowerCase() + lane.purpose.slice(1), 150)}` : "";
      ev.caption = `TRACE runs one search over the logs: ${rows.length} rows from ${(data?.returned_steps || []).length} steps come back as evidence.${purpose}`;
    } else if (call.name === "assess") {
      const lane = lanes.get(args.window_id || data?.window_id);
      if (lane) lane.assessNote = args.uncertainty || data?.uncertainty || "";
      for (const [i, c] of (args.candidates || data?.candidates || []).entries()) {
        const cand = {id: `c:${lane?.agent || "?"}:${i}`, type: "candidate", lane: lane?.id, step: c.step, module: c.module,
                      fid: moduleFact(c.step, c.module, c.fid), status: c.status, faultClass: c.fault_class,
                      proposition: c.proposition || "", quote: c.output_quote || "",
                      // The candidate's own argument; the steps it names are the ones TRACE used.
                      argument: [c.reasoning, c.immediate_effect, c.uncertainty].filter(Boolean).join(" ")};
        candidates.push(cand);
        lane?.candidates.push(cand.id);
        ev.candidates.push(cand.id);
        if (cand.fid != null) ev.fids.push(cand.fid);
      }
      ev.lanes = lane ? [lane.id] : [];
      const cs = ev.candidates.map(id => candidates.find(c => c.id === id));
      ev.raw = `assess(${lane?.agent || "?"})`;
      ev.title = `Weigh the evidence for step ${lane?.step ?? "?"}`;
      ev.caption = `TRACE weighs what the search returned and blames ${cs.length ? cs.map(c => `step ${c.step}'s ${c.module} (${[statusText(c.status), faultText(c.faultClass)].filter(Boolean).join(": ")})`).join("; ") : "no step"}.`;
    } else if (FACT_TOOLS.has(call.name)) {
      // One fact at a time: check(fid) judges it, span(fid) reads its source, and so on.
      ev.fids = [...new Set([args.fid, ...ids.fids])].filter(fid => factById.has(fid));
      ev.verdict = call.name === "check" ? data?.value : null;
    } else {
      ev.fids = [...ids.fids].filter(fid => factById.has(fid));
      ev.raw = call.name;
      ev.title = cap(call.name.replace(/_/g, " "));
      ev.caption = `${call.phase} calls the ${call.name.replace(/_/g, " ")} tool.`;
    }
    events.push(ev);
  }

  // Consecutive single-fact calls become one "inspect" event, so a run of forty check/span calls
  // reads as one access instead of forty.
  for (let i = 0; i < events.length; i++) {
    if (!FACT_TOOLS.has(events[i].name)) continue;
    let j = i;
    while (j + 1 < events.length && FACT_TOOLS.has(events[j + 1].name) && events[j + 1].phase === events[i].phase) j++;
    const run = events.slice(i, j + 1);
    events.splice(i, run.length, inspectEvent(run));
  }

  // Candidates written by assess without tool_calls (older logs) still come from trace_queries.
  for (const q of traceQueries || []) {
    const lane = lanes.get(q.candidate_id);
    if (lane && !lane.steps.length) lane.steps = q.returned_steps || [];
    if (lane && !lane.cards.length) lane.cards = q.agent_statements?.cards || [];
  }

  let root = null;
  const r = result?.root;
  if (r && r.step != null) {
    const fid = moduleFact(r.step, r.module, r.fid);
    const cand = candidates.find(c => c.step === r.step && (c.module === r.module || c.fid === fid));
    root = {id: "root", type: "root", step: r.step, module: r.module, fid, faultClass: r.fault_class,
            proposition: r.proposition || "", quote: r.output_quote || "", reasoning: r.reasoning || result.reasoning || "", candidate: cand?.id || null,
            accepted: !!result.accepted, status: result.status, rule: decidingRule(result, managerPlan),
            repair: result.repair || ""};
  }
  events.push({
    phase: "SUBMIT", name: "submit", title: root ? `Root cause: step ${root.step} ${root.module}` : "No root cause",
    raw: root ? `root = S${root.step} ${root.module}${root.fid != null ? ` (f${root.fid})` : ""}${root.rule?.id ? ` · rule ${root.rule.id}` : ""}` : "",
    caption: root
      ? `SUBMIT weighs the candidates against the manager's rules and names the root cause: step ${root.step}'s ${root.module}${root.faultClass ? `, which ${faultText(root.faultClass)}` : ""}. ${short(root.proposition, 150)}`
      : `SUBMIT stops without a root: ${short(result?.stop_reason || result?.status || "no result", 220)}`,
    fids: root?.fid != null ? [root.fid] : [], eids: [], goals: [], windows: [], lanes: [], candidates: root?.candidate ? [root.candidate] : [],
    root: !!root,
  });

  for (const ev of events) {
    const ents = new Set();
    for (const fid of ev.fids || []) factById.get(fid)?.args.forEach(a => entityMap.has(a) && entityMap.get(a).kind !== "req" && ents.add(`e:${a}`));
    ev.entities = [...ents];
  }

  return {
    meta: {tid: record.trajectory_id, task: record.task, taskType: record.task_type, steps,
           relations: record.relations || {}, kinds},
    facts, factById, entities, goals, evidence, windows, lanes: [...lanes.values()], candidates, root, events,
    goalMap: goalMap || null, rules: (managerPlan?.decision_rules || []).map(r => ({id: r.rule_id, agent: r.conditions?.[0]?.agent_id || "", conds: (r.conditions || []).map(c => ({agent: c.agent_id, outcome: c.outcome})), outcome: r.conditions?.[0]?.outcome || "", finding: r.conditions?.[0]?.finding || "", conclusion: r.conclusion || "", caveat: r.caveat || ""})),
  };
}

const FACT_TOOLS = new Set(["check", "span", "neighbours", "state", "trace_fact"]);
const tally = list => { const m = new Map(); list.forEach(x => x != null && m.set(x, (m.get(x) || 0) + 1)); return [...m].sort((a, b) => b[1] - a[1]); };
const VERDICT_ORDER = ["viol", "unk", "sat"];

function inspectEvent(run) {
  const fids = [...new Set(run.flatMap(e => e.fids))];
  const tools = tally(run.map(e => e.name));
  const verdicts = tally(run.map(e => e.verdict)).sort((a, b) => VERDICT_ORDER.indexOf(a[0]) - VERDICT_ORDER.indexOf(b[0]));
  const errors = run.filter(e => e.error).length;
  const only = tools.length === 1 ? tools[0][0] : null;
  const intent = !fids.length ? "inspect single facts" : only === "check" ? `check ${fids.length} facts` : only === "span" ? `read the source of ${fids.length} facts` : `inspect ${fids.length} facts`;
  const toolText = tools.map(([t, n]) => `${t} ×${n}`).join(", ");
  return {
    phase: run[0].phase, name: "inspect", ordinal: run[0].ordinal, call: run[0].call, calls: run, fids, eids: [], goals: [],
    windows: [], lanes: [], candidates: [], tools, verdicts, errors, intent, spec: toolText,
    title: cap(intent), raw: toolText,
    caption: !fids.length ? `${run[0].phase} tries ${run.length} single-fact call${run.length === 1 ? "" : "s"}; none returns a fact.` : `${run[0].phase} looks at ${fids.length} facts one by one${verdicts.length ? `: ${verdicts.map(([v, n]) => `${n} ${VERDICT_WORD[v] || v}`).join(", ")}` : ""}.`,
  };
}

// The manager's decision rule SUBMIT leaned on: the first rule its reasoning names.
function decidingRule(result, plan) {
  const rules = plan?.decision_rules || [];
  const text = [result.root?.reasoning, ...(result.candidate_comparisons || []).map(c => c.reasoning)].join(" ");
  const named = rules.map(r => [r, text.indexOf(r.rule_id)]).filter(([, i]) => i >= 0).sort((a, b) => a[1] - b[1]);
  const r = named[0]?.[0];
  return r ? {id: r.rule_id, conclusion: r.conclusion || "", count: rules.length} : rules.length ? {id: null, count: rules.length} : null;
}

export const factRepr = f => `[${f.fid}] t${f.t} ${f.slot}/${f.side} ${f.rel}(${f.args.join(", ")}) = ${short(typeof f.value === "string" ? JSON.stringify(f.value) : JSON.stringify(f.value), 90)}`;
