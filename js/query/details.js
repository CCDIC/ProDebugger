// Stage details: for each phase, one sentence beside the tower saying what ProDebugger did in
// this case, and behind its "more details" a popup (how it did it) and a rail (what came out).
// Both name the same entities and steps; pointing at one lights it everywhere, on the tower too.
import {esc} from "../util.js?v=27bf1baa85";
import {SIGNAL_LABEL, SLOT_LABEL} from "./model.js?v=23f9bfd6b4";
import {agentWords, cap, faultText, goalText, question, statusText, stepList, VERDICT_WORD} from "./narrate.js?v=23f9bfd6b4";

const SLOT_ORDER = ["obs", "mem", "refl", "plan", "act", "chk"];
// Who wrote a line: the environment, or the agent itself (memory, reflection, plan, action).
const SOURCE = {obs: "env", mem: "agent", refl: "agent", plan: "agent", act: "agent", chk: "check"};
const MODULE_SLOT = {observation: "obs", memory: "mem", reflection: "refl", plan: "plan", action: "act", check: "chk"};
// Entity kinds that never appear by name in the agent's text.
const UNNAMED = new Set(["agent", "attribute", "value", "int", "req"]);
// A ROUTE query by what it sets out to do: check the goal, look over the run, audit it step by
// step, or inspect single facts.
function queryKind(e) {
  if (e.name === "inspect" || e.name === "view_evidence") return "INSPECT";
  const base = String(e.spec || "").split("|")[0].trim();
  if (base === "requirements" || base.startsWith("entity:req:")) return "GOAL";
  if (/^(local_audits|steps)\b/.test(base)) return "AUDIT";
  if (/^(packet|statement_discovery|module|rules)\b/.test(base) || base.startsWith("entity:")) return "INSPECT";
  return "SCAN";
}
// How bad a trouble sign is: it bears on the task, the agent is spinning, or it is a soft hint.
const SIGNAL_SEVERITY = {
  no_new_task_evidence: "task", constraint_substitution: "task", wrong_task_decision: "task", contradicted_completion: "task",
  claims_task_complete: "task", alignment_violation: "task", failed_precondition: "task", missing_effect: "task",
  schema_repetition: "spin", repeated_action: "spin", near_duplicate_action: "spin", no_effect: "spin", invalid_action: "spin",
};
const signalSeverity = s => SIGNAL_SEVERITY[s] || "soft";
const clip = (t, n) => { t = String(t || "").replace(/\s+/g, " ").trim(); return t.length > n ? t.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : t; };
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// The names a reader meets in the text, each tied to the entity (or the goal's required value)
// it stands for and the facts that name it. Longer names win, so "countertop 1" is the entity
// and a bare "countertop" is the goal's.
function nameIndex(scene) {
  const byName = new Map();
  const add = (name, key, fids) => { const k = name.toLowerCase(); if (name.length >= 3 && !byName.has(k)) byName.set(k, {key, name, fids}); };
  for (const g of scene.goals) for (const c of g.constraints) if (typeof c.expected === "string") add(c.expected, `goal:${c.expected}`, g.facts);
  for (const e of scene.entities) {
    if (UNNAMED.has(e.kind)) continue;
    add(/^\d+$/.test(e.index) ? `${e.kind} ${e.index}` : e.index.replace(/_/g, " "), e.key, e.facts);
  }
  // A bare type ("soapbottle") stands for its object when the run has only one of that type.
  const bases = new Map();
  for (const e of scene.entities) if (!UNNAMED.has(e.kind) && /_\d+$/.test(e.index)) { const b = e.index.replace(/_\d+$/, "").replace(/_/g, " "); bases.set(b, bases.has(b) ? null : e); }
  for (const [b, e] of bases) if (e) add(b, e.key, e.facts);
  // What a name is to the task: a word of the goal (or its target), a thing the agent handled,
  // or the rest of the scenery. The page colours them apart.
  const goalWords = new Set(scene.goals.flatMap(g => [...g.constraints.map(c => c.expected), entName(g.target)]).filter(w => typeof w === "string").map(w => w.toLowerCase()));
  const acted = new Set(scene.facts.filter(f => f.slot === "act").flatMap(f => f.args));
  const ents = new Map(scene.entities.map(e => [e.key, e]));
  const whereWords = new Set(scene.goals.map(g => entName(g.target).toLowerCase()));
  const role = key => {
    if (key.startsWith("goal:")) return whereWords.has(key.slice(5).toLowerCase()) ? "where" : "goal";
    const e = ents.get(key);
    if (!e) return "other";
    const base = e.index.replace(/_\d+$/, "").replace(/_/g, " ");
    if (whereWords.has(e.kind) || whereWords.has(base)) return "where";
    if (goalWords.has(e.kind) || goalWords.has(base)) return "goal";
    return acted.has(key) && e.kind !== "place" && e.parent !== "place" ? "held" : "other";
  };
  const names = [...byName.keys()].sort((a, b) => b.length - a.length).map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = names.length ? new RegExp(`\\b(${names.join("|")})\\b`, "gi") : null;
  const facts = new Map([...byName.values()].map(v => [v.key, v.fids]));
  const label = new Map();
  for (const v of byName.values()) if (!label.has(v.key)) label.set(v.key, v.name);
  // Escapes the text, then wraps every name in a link to its entity.
  const mark = text => {
    const s = esc(text);
    return re ? s.replace(re, m => { const v = byName.get(m.toLowerCase()); return `<b class="qg-ent" data-ent="${esc(v.key)}" data-role="${role(v.key)}">${m}</b>`; }) : s;
  };
  return {mark, facts, label, role};
}

const entName = key => { const [kind, ...rest] = key.split(":"); const i = rest.join(":"); return /^\d+$/.test(i) ? `${kind} ${i}` : i.replace(/_/g, " "); };
const factCall = f => `${f.rel}(${f.args.filter(a => !a.startsWith("attribute:")).map(a => entName(a)).join(", ")})`;
// The agent's own text for a cell, without the prompt's scaffold line.
function cellText(facts) {
  const spans = [...new Set(facts.map(f => f.span).filter(Boolean))];
  const lines = spans.join("\n").split("\n").map(x => x.trim()).filter(Boolean);
  const body = lines.length > 1 && !lines[0].startsWith("-") && lines.slice(1).some(x => x.startsWith("-")) ? lines.slice(1) : lines;
  return body.map(x => x.replace(/^-\s*/, "")).join(" · ");
}

// One row per step: a small strip of the run, with marks for the steps a phase cares about.
function strip(steps, marks) {
  const cells = [];
  for (let s = 1; s <= steps; s++) {
    const m = marks.get(s);
    cells.push(`<button class="sd-cell${m ? ` is-${m.tone}` : ""}" data-goto="step:${s}" title="${esc(`step ${s}${m ? ` · ${m.label}` : ""}`)}"></button>`);
  }
  return `<div class="sd-strip" style="--n:${steps}">${cells.join("")}</div><div class="sd-strip-ax"><span>1</span><span>${steps}</span></div>`;
}

export function buildDetails(scene, model) {
  const {mark, facts: entFacts, label, role} = nameIndex(scene);
  const steps = scene.meta.steps;
  const cell = (t, slot) => scene.facts.filter(f => f.t === t && f.slot === slot);
  const windowSteps = new Map(scene.windows.map(w => [w.step, w]));
  const root = scene.root;
  const fids = list => list.join(",");
  // The agent's sentence that names the most of the task's own words, else its first one.
  const goalWords = [...new Set(scene.goals.flatMap(g => [...g.constraints.map(c => c.expected), entName(g.target)]).filter(w => typeof w === "string" && w.length > 2).map(w => w.toLowerCase()))];
  const words = f => {
    const parts = String(f.span || "").split(/\n|(?<=[.?!])\s+/).map(x => x.replace(/^-\s*/, "").trim()).filter(x => x.length > 12);
    const score = x => goalWords.filter(w => x.toLowerCase().includes(w)).length;
    const best = parts.reduce((a, x) => score(x) > score(a ?? "") ? x : a, null);
    return clip(best && score(best) ? best : agentWords(f) || f.span || factCall(f), 220);
  };
  const factRow = (f, extra = "") => `<li data-fids="${f.fid}" data-step="${f.t}"><span class="sd-k">step ${f.t} · ${SLOT_LABEL[f.slot] || f.slot}</span><span>${mark(words(f))}</span>${extra}</li>`;
  // Facts of one window, one row per distinct sentence.
  const factRows = list => { const seen = new Set(); return list.filter(f => { const k = words(f); if (seen.has(k)) return false; seen.add(k); return true; }).map(f => factRow(f)).join(""); };

  // The entities this case turns on: the ones the suspect windows and the root name, then the
  // most-mentioned others.
  const keyFids = new Set([...scene.windows.flatMap(w => w.fids), ...(root?.fid != null ? [root.fid] : [])]);
  const ranked = scene.entities.filter(e => !UNNAMED.has(e.kind) && label.has(e.key))
    .map(e => ({e, hot: e.facts.filter(f => keyFids.has(f)).length}))
    .sort((a, b) => (b.hot - a.hot) || (b.e.facts.length - a.e.facts.length)).map(x => x.e);
  const ent = key => `<b class="qg-ent" data-ent="${esc(key)}" data-role="${role(key)}">${esc(label.get(key) || entName(key))}</b>`;
  const entChip = e => `<button class="sd-chip" data-ent="${esc(e.key)}" data-role="${role(e.key)}" data-goto="ent:${esc(e.key)}"${e.key.startsWith("goal:") ? ` title="the task names it; no object of this kind appears in the run"` : ""}><b>${esc(label.get(e.key))}</b><em>${e.key.startsWith("goal:") ? "never seen" : e.facts.length}</em></button>`;

  // Shared pieces of a result line: where its facts sit in the run, its trouble signs, the
  // windows whose clue facts it returned.
  const miniStrip = (at, tone = "") => `<span class="sd-mini${tone ? ` is-${tone}` : ""}" style="--n:${steps}" title="steps it touches">${Array.from({length: steps}, (_, i) => `<i class="${at.has(i + 1) ? "is-on" : ""}${windowSteps.has(i + 1) ? " is-win" : ""}"></i>`).join("")}</span>`;
  const stepsOf = list => new Set(list.map(fid => scene.factById.get(fid)?.t));
  const signalPills = sig => (sig || []).slice(0, 3).map(([s, n]) => `<span class="sd-sig" data-sev="${signalSeverity(s)}">${esc(SIGNAL_LABEL[s])} <b>×${n}</b></span>`).join("");
  const hitsOf = list => { const got = new Set(list); const h = scene.windows.map(w => ({w, n: w.fids.filter(f => got.has(f)).length})).filter(x => x.n); return h.length ? h : null; };
  const hitChips = hit => (hit || []).map(h => `<span class="sd-hit" data-goto="step:${h.w.step}" title="returned ${h.n} of the window's ${h.w.fids.length} clue facts">→ ${esc(h.w.caseId)} · ${h.n}/${h.w.fids.length}</span>`).join("");
  const num = (n, unit) => `<b class="sd-qn">${n}</b><span class="sd-k">${unit}</span>`;
  const stepNo = html => html.replace(/\bsteps? \d+(?:[–-]\d+)?/g, m => `<b class="sd-stepno">${m}</b>`);

  const out = {};

  // PARSE: every step, the agent's text with its entities marked, and the facts it became.
  {
    const top = ranked.slice(0, 3);
    const rows = [];
    for (let t = 0; t <= steps; t++) {
      const cells = SLOT_ORDER.map(slot => [slot, cell(t, slot)]).filter(([, fs]) => fs.length);
      if (!cells.length) continue;
      const tag = t === root?.step ? `<i class="sd-tag is-root">root</i>` : windowSteps.has(t) ? `<i class="sd-tag">suspect window</i>` : "";
      rows.push(`<li class="sd-step sd-card" data-step="${t}"><h5>${t ? `Step ${t}` : "Task"}${tag}<span class="sd-modtabs">${cells.map(([slot, fs]) => `<button type="button" class="sd-modtab" data-mod="${slot}" data-slot-toggle="${slot}" aria-expanded="false" data-fids="${fids(fs.map(f => f.fid))}">${SLOT_LABEL[slot]}<em>${fs.length}</em></button>`).join("")}</span></h5>${cells.map(([slot, fs]) => {
        const calls = fs.slice(0, 4).map(f => `<code>#${f.fid} ${esc(factCall(f))}</code>`).join("") + (fs.length > 4 ? `<code class="sd-more">+${fs.length - 4}</code>` : "");
        return `<div class="sd-cellrow is-fold" data-mod="${slot}" data-slot="${slot}" data-src="${SOURCE[slot]}" data-fids="${fids(fs.map(f => f.fid))}" hidden><span class="sd-k sd-src">${SLOT_LABEL[slot]}</span><p>${mark(clip(cellText(fs), 260))}</p><div class="sd-calls"><span class="sd-arrow">→</span>${calls}</div></div>`;
      }).join("")}</li>`);
    }
    const bySlot = SLOT_ORDER.map(s => [s, scene.facts.filter(f => f.slot === s).length]).filter(([, n]) => n);
    out.PARSE = {
      say: `ProDebugger compiles the agent's ${steps}-step log into a DSL record: ${scene.facts.length} facts, each saying who saw, thought or did what at which step, about ${top.map(e => ent(e.key)).join(", ")}${ranked.length > top.length ? ` and ${ranked.length - top.length} other entities` : ""}.`,
      title: "How each step became facts",
      body: `<ol class="sd-steps">${rows.join("")}</ol>`,
      focus: null,
      rail: `<h4>DSL record</h4><dl class="sd-stats">${[["facts", scene.facts.length], ["entities", scene.entities.length], ["steps", steps]].map(([k, v]) => `<div><dd>${v}</dd><dt>${k}</dt></div>`).join("")}</dl>
        <h4>Facts by source</h4><ul class="sd-bars">${bySlot.map(([s, n]) => `<li data-src="${SOURCE[s]}"><span>${SLOT_LABEL[s]}</span><i style="--w:${(n / scene.facts.length * 100).toFixed(1)}%"></i><em>${n}</em></li>`).join("")}</ul>
        <h4>Entities <small>facts</small></h4><div class="sd-groups" data-fly>${[["goal", "the task asks for"], ["held", "the agent handled"], ["other", "the rest"]].map(([r, name]) => {
          // The goal's own words come first, even when no object of that kind ever showed up.
          const asked = r !== "goal" ? [] : [...new Set(scene.goals.flatMap(g => g.constraints.map(c => c.expected)).filter(w => typeof w === "string"))]
            .filter(w => !scene.entities.some(e => e.kind === w || e.index.replace(/_\d+$/, "") === w)).map(w => ({key: `goal:${w}`, facts: []}));
          const es = [...asked, ...ranked.filter(e => role(e.key) === r)].slice(0, r === "other" ? 8 : 6);
          return es.length ? `<div class="sd-group" data-role="${r}"><span class="sd-k">${name}</span><div class="sd-chips">${es.map(entChip).join("")}</div></div>` : "";
        }).join("")}</div>`,
      fly: true,
    };
  }

  // MAP: the task sentence, the requirement it becomes, and the facts that bear on it.
  {
    const goalEv = scene.events.find(e => e.phase === "MAP" && e.goal) || scene.events.find(e => e.goal);
    const state = goalEv?.goal?.state;
    const phrase = g => {
      const type = g.constraints.find(c => c.property?.index === "object_type")?.expected;
      return type ? `${g.count > 1 ? g.count : "a"} ${type} must end up ${g.rel} ${entName(g.target)}` : goalText(g.key);
    };
    // Things the agent acted on whose recorded type is not the one the goal asks for.
    const acted = new Set(scene.facts.filter(f => f.slot === "act").flatMap(f => f.args));
    const off = [];
    for (const g of scene.goals) for (const c of g.constraints) {
      const attr = c.property?.index;
      if (!attr || typeof c.expected !== "string") continue;
      for (const f of scene.facts) {
        if (f.args[1] !== `attribute:${attr}` || !acted.has(f.args[0]) || f.value === false) continue;
        const v = f.args[2]?.replace(/^value:/, "");
        if (v && v !== c.expected && !off.some(o => o.f.args[0] === f.args[0])) off.push({f, c, v});
      }
    }
    // Two goal shapes: placing an object somewhere (ALFWorld), or one item meeting several
    // attributes (WebShop: every attribute is its own goal on attribute:<name>).
    const typeOf = g => g.constraints.find(c => c.property?.index === "object_type")?.expected;
    const whereOf = g => entName(g.target);
    const isAttr = g => g.target.startsWith("attribute:");
    const attrGoals = scene.goals.filter(isAttr), placeGoals = scene.goals.filter(g => !isAttr(g));
    const attrName = g => entName(g.target).replace(/_/g, " ");
    const OP = {eq: "=", lt: "<", le: "≤", gt: ">", ge: "≥", semantic_match: "≈"};
    const MODE = {observation: "must be seen", selection: "must be chosen", semantic: "matched by meaning"};
    const SLOT_NAME = {action: "ACTION", count: "COUNT", what: "WHAT", rel: "RELATION", where: "WHERE"};
    // The task sentence cut into its words, each tagged with the slot it fills; a run of words
    // from one long value is named once, on its first word.
    const STOP = new Set(["a", "an", "the", "and", "or", "with", "of", "for", "in", "on", "to"]);
    const diagram = () => {
      const tag = new Map();
      const put = (w, slot, label, key) => { if (w && !tag.has(w)) tag.set(w, {slot, label, key}); };
      for (const g of placeGoals) {
        const type = typeOf(g), where = whereOf(g);
        put(String(type || "").toLowerCase(), "what", "WHAT", type ? `goal:${type}` : null);
        put(where.toLowerCase(), "where", "WHERE", `goal:${where}`);
        put(String(g.rel).toLowerCase(), "rel", "RELATION");
      }
      for (const g of attrGoals) for (const c of g.constraints) {
        const label = attrName(g).toUpperCase();
        if (typeof c.expected === "number") put(`#${c.expected}`, "attr", label);
        else if (typeof c.expected === "string") c.expected.toLowerCase().split(/[\s,]+/).filter(w => w && !STOP.has(w)).forEach(w => put(w, /\s/.test(c.expected) ? "desc" : "attr", label));
      }
      let first = true, prev = null;
      return scene.meta.task.split(/(\s+)/).map(t => {
        if (!t.trim()) return " ";
        const word = t.replace(/[.,;:!?]+$/, ""), punct = t.slice(word.length), w = word.toLowerCase();
        const num = Number.isFinite(parseFloat(w)) ? tag.get(`#${parseFloat(w)}`) : null;
        let hit = first ? {slot: "action", label: "ACTION"} : num || tag.get(w) || (placeGoals.length && /^(a|an|one|\d+)$/.test(w) ? {slot: "count", label: "COUNT"} : null);
        first = false;
        const label = hit && !(prev && prev.slot === hit.slot && prev.label === hit.label) ? hit.label : "";
        prev = hit;
        const tok = `<span class="sd-tok"${hit ? ` data-slot="${hit.slot}"` : ""}${hit?.key ? ` data-ent="${esc(hit.key)}"` : ""}><b>${esc(word)}</b>${hit ? `<i>${label || " "}</i>` : ""}</span>`;
        return tok + (punct ? `<span class="sd-tok"><b>${esc(punct)}</b></span>` : "");
      }).join("");
    };
    const slotWord = (slot, w, key) => `<span class="sd-slot" data-slot="${slot}"${key ? ` data-ent="${esc(key)}"` : ""}>${esc(w)}</span>`;
    const status = () => state ? `<dt>STATUS</dt><dd><span class="sd-state${/unmet|unsat|unres|fail|false|viol/i.test(state) ? " is-bad" : " is-ok"}">${esc(state)}</span>${goalEv.goal.decided?.length ? ` <span class="sd-k">decided at</span> <b class="sd-stepno">${esc(stepList(goalEv.goal.decided))}</b>` : ""}</dd>` : "";
    const placeCard = g => {
      const type = typeOf(g), where = whereOf(g), what = type || g.ofKind;
      const typeC = g.constraints.find(c => c.property?.index === "object_type");
      return `<dl class="sd-req" data-fids="${fids(g.facts)}">
        <dt>WHAT</dt><dd>${slotWord("what", what, type ? `goal:${type}` : null)}${typeC ? ` <span class="sd-k">object type · ${esc(MODE[typeC.mode] || typeC.mode)}</span>` : ""}</dd>
        <dt>HOW MANY</dt><dd>${slotWord("count", String(g.count ?? 1))}</dd>
        <dt>WHERE</dt><dd>${esc(g.rel)} ${slotWord("where", where, `goal:${where}`)}</dd>
        <dt>DONE WHEN</dt><dd>the run shows ${g.count > 1 ? g.count : "one"} ${slotWord("what", what)} ${esc(g.rel)} the ${slotWord("where", where)}</dd>
        ${status()}</dl>`;
    };
    const attrCard = () => {
      const item = attrGoals.find(g => g.ofKind && g.ofKind !== "value")?.ofKind || "item";
      return `<dl class="sd-req">
        <dt>WHAT</dt><dd>${slotWord("what", `one ${item}`)}</dd>
        ${attrGoals.map(g => g.constraints.map(c => `<dt>${esc(attrName(g).toUpperCase())}</dt><dd data-fids="${fids(g.facts)}"><b class="sd-op">${esc(OP[c.operator] || c.operator || "=")}</b> ${slotWord(typeof c.expected === "string" && /\s/.test(c.expected) ? "desc" : "attr", typeof c.expected === "string" ? c.expected : String(c.expected))} <span class="sd-k">${esc(MODE[c.mode] || c.mode || "")}</span></dd>`).join("")).join("")}
        <dt>DONE WHEN</dt><dd>the run ends with ${slotWord("what", `one ${item}`)} meeting all ${attrGoals.reduce((n, g) => n + g.constraints.length, 0)} conditions</dd>
        ${status()}</dl>`;
    };
    out.MAP = {
      say: `ProDebugger reads the task “${mark(scene.meta.task)}” as ${scene.goals.length === 1 ? "one requirement" : `${scene.goals.length} requirements`}: ${scene.goals.map(g => mark(phrase(g))).join("; ")}.${state ? ` At the end of the run it is <b class="sd-bad">${esc(state)}</b>.` : ""}`,
      title: "What the task maps to",
      lead: `Every suspicion raised later has to name the requirement it hurts.`,
      body: `<div class="sd-sec"><div class="sd-k">the task, word by word</div><div class="sd-diagram">${diagram()}</div></div>
        <div class="sd-sec"><div class="sd-k">the requirement it becomes</div>${placeGoals.map(placeCard).join("")}${attrGoals.length ? attrCard() : ""}</div>
        <div class="sd-sec"><div class="sd-k">facts that check the goal</div><ul class="sd-list">${scene.goals.flatMap(g => g.facts).map(fid => scene.factById.get(fid)).filter(Boolean).slice(0, 8).map(f => factRow(f, `<code>${esc(String(f.value ?? "open"))}</code>`)).join("") || `<li class="sd-k">none</li>`}</ul></div>
        ${off.length ? `<div class="sd-sec"><div class="sd-k">things the agent handled, by recorded type</div><ul class="sd-list">${off.slice(0, 6).map(({f, c, v}) => `<li data-fids="${f.fid}" data-step="${f.t}"><span class="sd-k">step ${f.t} · #${f.fid}</span><span>${ent(f.args[0])} is a ${mark(v)} <b class="sd-bad">≠ ${esc(c.expected)}</b></span></li>`).join("")}</ul></div>` : ""}`,
      rail: `<h4>Goal</h4>${scene.goals.map(g => `<div class="sd-card"><p>${mark(phrase(g))}</p><div class="sd-chips">${g.constraints.filter(c => typeof c.expected === "string").map(c => `<span class="sd-chip" data-ent="goal:${esc(c.expected)}" data-role="${role(`goal:${c.expected}`)}"><b>${esc(c.expected)}</b></span>`).join("")}</div></div>`).join("")}
        ${state ? `<h4>Status</h4><p class="sd-bad">${esc(state)}${goalEv.goal.decided?.length ? ` <span class="sd-k">decided at ${esc(stepList(goalEv.goal.decided))}</span>` : ""}</p>` : ""}
        ${off.length ? `<h4>Wrong type in hand</h4><div class="sd-chips">${off.slice(0, 6).map(o => `<button class="sd-chip" data-ent="${esc(o.f.args[0])}" data-goto="ent:${esc(o.f.args[0])}"><b>${esc(entName(o.f.args[0]))}</b></button>`).join("")}</div>` : ""}`,
    };
  }

  // ROUTE: the queries it ran and the windows it opened.
  {
    const views = scene.events.filter(e => e.phase === "ROUTE" && !e.error && e.name !== "route_cases");
    const actOf = w => w.fids.map(fid => scene.factById.get(fid)).find(f => f?.slot === "act");
    const ws = scene.windows;
    const signs = new Map();
    views.forEach(e => (e.signals || []).forEach(([k, n]) => signs.set(k, Math.max(signs.get(k) || 0, n))));
    // Per query, the windows whose clue facts it returned, and how many of them.
    const hits = views.map(e => hitsOf(e.fids));
    out.ROUTE = {
      say: `ProDebugger queries the record ${plural(views.length, "time")} for trouble signs and opens ${ws.length === 1 ? "one suspect window" : `${ws.length} suspect windows`}: ${ws.map(w => { const a = actOf(w); return `step ${w.step}${a ? `, where the agent acts “${mark(clip(a.span, 80))}”` : ""}`; }).join("; ") || "none"}.`,
      title: "What the scan found",
      lead: `ROUTE only screens: a window is a place to look, not a verdict.`,
      body: `${ws.map(w => `<div class="sd-sec sd-win" data-step="${w.step}"><div class="sd-k">suspect window ${esc(w.caseId)} · step ${w.step} · facts ${esc(w.fids.map(f => `#${f}`).join(" "))}</div>
          <ul class="sd-list">${factRows(w.fids.map(fid => scene.factById.get(fid)).filter(Boolean))}</ul>
          <details><summary>ROUTE's hypothesis</summary><p>${mark(w.hypothesis)}</p></details></div>`).join("")}
        <div class="sd-sec"><div class="sd-k">how it got there · ${plural(views.length, "query", "queries")}${hits.some(Boolean) ? ` · lit rows reached ${ws.length === 1 ? "the window" : "a window"}` : ""}</div><ol class="sd-queries">${views.map((e, k) => {
          const kind = queryKind(e), hit = hits[k];
          return `<li class="${hit ? "is-hit" : hits.some(Boolean) ? "is-dim" : ""}" data-fids="${fids(e.fids)}">
            <span class="sd-qtag" data-kind="${kind}" title="${esc(e.raw || e.name)}">${kind}</span>
            <div><p>${esc(cap(e.intent || e.name))}</p>
              <div class="sd-qres">${miniStrip(stepsOf(e.fids))}${num(e.fids.length, "facts")}${signalPills(e.signals)}${hitChips(hit)}</div></div></li>`;
        }).join("")}</ol></div>`,
      rail: `<h4>Suspect steps</h4>${strip(steps, new Map(ws.map(w => [w.step, {tone: "hot", label: `window ${w.caseId}`}])))}
        <div class="sd-chips">${ws.map(w => `<button class="sd-chip is-hot" data-goto="step:${w.step}" data-fids="${fids(w.fids)}"><b>${esc(w.caseId)}</b><em>step ${w.step}</em></button>`).join("")}</div>
        ${signs.size ? `<h4>Trouble signs</h4><ul class="sd-bars">${[...signs].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, n]) => `<li data-sev="${signalSeverity(k)}"><span>${esc(SIGNAL_LABEL[k])}</span><i style="--w:${Math.min(100, n / steps * 100 * 3).toFixed(1)}%"></i><em>${n}</em></li>`).join("")}</ul>` : ""}`,
    };
  }

  // MANAGER: per window, the question it becomes (two accounts to tell apart), where TRACE
  // will look, and what each possible answer then means, fixed before any evidence comes in.
  {
    const ls = scene.lanes;
    const usedRule = root?.rule?.id || null;
    const sentencesOf = t => String(t || "").split(/(?<=[.?!])\s+/).map(x => x.trim()).filter(Boolean);
    // The question's first sentence cut at its ", or" into account A and account B; the rival
    // the manager names is kept as one dim line.
    const accounts = l => {
      const all = sentencesOf(l.question), first = all[0] || "";
      const cut = first.endsWith("?") ? first.search(/,\s+or\s+/i) : -1;
      const rival = all.find(x => /^the rival\b/i.test(x));
      const ab = cut > 0
        ? `<ol class="sd-ab"><li><span class="sd-ab-tag">A</span><p>${mark(first.slice(0, cut))}</p></li><li><span class="sd-ab-tag">B</span><p>${mark(first.slice(cut + 1).trim().replace(/^or\s+/i, ""))}</p></li></ol>`
        : `<p class="sd-question">${mark(clip(first, 220))}</p>`;
      return ab + (rival ? `<p class="sd-rival"><span class="sd-k">rival</span> ${mark(clip(rival.replace(/^the rival is\s*/i, ""), 160))}</p>` : "");
    };
    // The quote that carries the suspicion: the agent's own words, preferring hedged ones
    // ("could potentially"); the hedge is lit.
    const HEDGE = /\b(could potentially|potentially|potential|might|maybe|possibly|probably|likely|seems?|appears?)\b/gi;
    const srcOf = ev => { const [, step, module] = String(ev.source_id || "").split(":"); return {step: +step, module: module || "", src: /observation|feedback/.test(module) ? "env" : "agent"}; };
    const keyQuote = l => {
      const agent = l.evidence.filter(ev => srcOf(ev).src === "agent");
      return agent.find(ev => new RegExp(HEDGE.source, "i").test(ev.quote)) || agent.find(ev => srcOf(ev).step === l.step) || l.evidence[0];
    };
    // Marks the hedge words outside the entity tags only.
    const lightHedge = html => html.split(/(<[^>]+>)/).map(p => p.startsWith("<") ? p : p.replace(HEDGE, m => `<mark class="sd-hedge">${m}</mark>`)).join("");
    const quoteCard = (ev, lit = false) => {
      const {step, module, src} = srcOf(ev);
      return `<blockquote class="sd-quote-card" data-src="${src}" data-step="${step || ""}"><span class="sd-k">step ${step || "?"} · ${esc(module)}</span>${lit ? lightHedge(mark(ev.quote)) : mark(ev.quote)}</blockquote>`;
    };
    // A search target as one row of the plan, in TRACE's three groups: before the window in the
    // agent's own text, what the environment and the actions show, and after the window. A target
    // that only weighs the evidence is not a search, so it leaves the plan.
    const MODS = [["memory", "agent"], ["reflection", "agent"], ["plan", "agent"], ["observation", "env"], ["feedback", "env"], ["action", "act"]];
    const modSide = m => MODS.find(x => x[0] === m)[1];
    const GROUP_WHY = {before: "where the idea first appears", facts: "what really happened", after: "whether the agent came back to it"};
    const planRow = (t, l) => {
      const verb = ((t.match(/^\s*([A-Za-z]+)/) || [])[1] || "").toLowerCase();
      if (/^(assess|decide|judge|determine|weigh|evaluate|conclude)$/.test(verb)) return null;
      let mods = MODS.filter(([m]) => new RegExp(`\\b${m}`, "i").test(t)).map(([m]) => m);
      if (/\bmental\b|\bmind\b/i.test(t)) mods = [...new Set(["memory", "reflection", "plan", ...mods])];
      const range = t.match(/steps? (\d+)\s*(?:through|to|-|–)\s*(?:step )?(\d+)/i) || t.match(/(\d+)\s*(?:through|to|–)\s*(\d+)/);
      const until = !range && t.match(/through step (\d+)/i);
      let [a, b] = range ? [+range[1], +range[2]] : until ? [l.step, +until[1]] : [null, null];
      const mind = mods.some(m => modSide(m) === "agent");
      const group = !mind || verb === "compare" ? "facts" : a != null && b > l.step && a >= l.step ? "after" : "before";
      if (group === "after" && a != null) a = Math.max(a, l.step + 1);
      // Each row searches one side: the agent's text, or the environment and the actions.
      mods = mods.filter(m => (group === "facts") === (modSide(m) !== "agent"));
      const quoted = [...new Set([...t.matchAll(/(?:^|[\s(,])'(.{2,40}?)'(?=[\s,.;:)]|$)/g)].map(m => m[1]))];
      const named = [...new Set([...mark(t).matchAll(/data-ent="([^"]+)"[^>]*>([^<]+)</g)].map(m => m[2].toLowerCase()))];
      const claimM = t.match(/([a-z_]+(?:\/[a-z_]+)+)\s+claim variants/i);
      const claims = claimM ? claimM[1].split("/").map(c => c.replace(/_/g, " ")) : [];
      // Without quoted words, the slash-joined terms of the first clause ("cool/cooling") say
      // what to look for; module lists ("memory/reflection/plan") are not terms.
      const head = (claimM ? t.replace(claimM[0], "") : t).split(/;|\.\s|,\s*(?:including|compare|do not)\b/i)[0];
      const terms = quoted.length ? [] : [...head.matchAll(/\b([a-z]+(?:\/[a-z]+(?: [a-z]+)?)+)\b/gi)].map(m => m[1])
        .filter(g => { const seg = g.toLowerCase().split("/").map(w => w.trim().split(" ")[0]); return !seg.some(w => w === "state" || MODS.some(([m]) => w === m)) && new Set(seg).size > 1; })
        .map(g => g.split("/").join(" / "));
      const words = [...(quoted.length ? quoted : named).slice(0, 4), ...terms.slice(0, 2)];
      // A row that names neither words nor steps says nothing a reader can follow.
      if (!words.length && !claims.length && a == null) return null;
      return `<li class="sd-plan-row" data-group="${group}">
        <span class="sd-qa-tag">${group.toUpperCase()}</span>
        <div class="sd-plan-what">${words.map(w => `<span class="sd-word">“${mark(w)}”</span>`).join("")}${claims.length ? `<span class="sd-k">+ claims: ${esc(claims.join(", "))}</span>` : ""}</div>
        <div class="sd-plan-where">${mods.map(m => `<span class="sd-modtag" data-src="${modSide(m) === "agent" ? "agent" : "env"}">${m}</span>`).join("")}</div>
        <span class="sd-plan-steps">${a == null ? "" : a === b ? `step ${a}` : `steps ${a}–${b}`}</span>
        <span class="sd-plan-why">${GROUP_WHY[group]}</span></li>`;
    };
    // What each answer TRACE may give then means, one line each; the line SUBMIT used is lit.
    const ANSWER = {supported: "YES", refuted: "NO", unknown: "CAN'T TELL"};
    const laneOf = r => ls.find(l => l.agent === r.agent) || ls[0];
    const meaning = r => {
      const l = laneOf(r);
      if (r.conds?.length > 1) return r.conds.every(c => c.outcome === "supported") ? "both hold: weigh which one explains the failure" : mark(clip(r.conclusion, 100));
      if (r.outcome === "supported") return usedRule === r.id && root ? `blame <b class="sd-stepno">step ${root.step}</b>'s ${esc(root.module)}` : `blame the suspected step${l ? ` (<b class="sd-stepno">step ${l.step}</b>, or an earlier source TRACE finds)` : ""}`;
      if (r.outcome === "refuted") return "drop this suspicion and weigh other evidence";
      if (r.outcome === "unknown") return "keep it open: no verdict on this suspicion";
      return mark(clip(r.conclusion, 100));
    };
    // A rule may need several answers at once (both suspicions hold): its tag joins them.
    const answerOf = r => (r.conds?.length > 1 ? r.conds : [r]).map(c => ANSWER[c.outcome] || String(c.outcome || "?").toUpperCase()).join(" + ");
    const whoOf = r => ls.length < 2 ? "" : ` <span class="sd-k">· ${(r.conds?.length > 1 ? r.conds : [r]).map(c => esc(ls.find(l => l.agent === c.agent)?.anchor || c.agent)).join(" + ")}</span>`;
    const verdicts = () => `<ol class="sd-verdicts">${scene.rules.map(r => `<li class="${usedRule === r.id ? "is-used" : usedRule ? "is-dim" : ""}">
        <span class="sd-outcome" data-outcome="${esc(r.outcome)}">${esc(answerOf(r))}</span>
        <p>${meaning(r)}${whoOf(r)}</p>
        ${usedRule === r.id ? `<span class="sd-used">★ used by SUBMIT</span>` : "<span></span>"}</li>`).join("")}</ol>
      <details class="sd-full"><summary>the rules in full</summary>${scene.rules.map(r => `<p><span class="sd-k">${esc(answerOf(r))} · ${esc(r.id.replace(/_/g, " "))}</span><br>${r.finding ? `<span class="sd-k">if</span> ${mark(r.finding)} ` : ""}<span class="sd-k">then</span> ${mark(r.conclusion)}${r.caveat ? ` <span class="sd-k">caveat</span> ${mark(r.caveat)}` : ""}</p>`).join("")}</details>`;
    const used = scene.rules.find(r => r.id === usedRule);
    out.MANAGER = {
      say: ls.length === 1
        ? `ProDebugger turns the step-${ls[0].step} suspicion into one question for a trace agent: “${mark(question(ls[0], 170))}”`
        : `ProDebugger turns the suspicions into ${ls.length} questions, one per trace agent, for ${stepList(ls.map(l => l.step))}.`,
      title: "The questions handed to TRACE",
      lead: "",
      body: `${ls.map(l => {
          const key = l.evidence.length ? keyQuote(l) : null, rest = l.evidence.filter(ev => ev !== key);
          const rows = l.targets.map(t => planRow(t, l)).filter(Boolean);
          return `<div class="sd-sec" data-step="${l.step}"><div class="sd-k">the question · ${ls.length > 1 ? `${esc(l.agent)} · ` : ""}window ${esc(l.anchor)} · step ${l.step}</div>
            ${accounts(l)}
            ${key ? quoteCard(key, true) : ""}
            <details class="sd-full"><summary>full question${rest.length ? ` and ${plural(rest.length, "more quote")}` : ""}</summary><p>${mark(l.question)}</p>${rest.map(ev => quoteCard(ev)).join("")}</details></div>
          ${rows.length ? `<div class="sd-sec"><div class="sd-k">where TRACE will look</div><ol class="sd-plan">${rows.join("")}</ol>
            <details class="sd-full"><summary>search targets in full</summary>${l.targets.map(t => `<p>${mark(t)}</p>`).join("")}</details></div>` : ""}`;
        }).join("")}
        ${scene.rules.length ? `<div class="sd-sec"><div class="sd-k">what each answer means · fixed before TRACE searches</div>${verdicts()}</div>` : ""}`,
      rail: `<h4>Suspect</h4><div class="sd-chips">${ls.map(l => `<button class="sd-chip is-hot" data-goto="step:${l.step}"><b>${esc(l.anchor)}</b><em>step ${l.step}</em></button>`).join("")}</div>
        ${used ? `<h4>Answer used</h4><p class="sd-rail-used"><span class="sd-outcome" data-outcome="${esc(used.outcome)}">${esc(answerOf(used))}</span><span>${meaning(used)}</span></p>` : ""}`,
    };
  }

  // TRACE: per lane, what it set out to find (its SQL cut into the sub-searches it runs), the
  // finding that decides it, which of the steps it read its verdict then uses, and the SQL.
  {
    const ls = scene.lanes;
    const cands = l => scene.candidates.filter(c => c.lane === l.id);
    const SQL_KW = new Set("WITH AS SELECT FROM WHERE UNION ALL AND OR NOT IN LIKE ORDER BY GROUP LIMIT DISTINCT JOIN LEFT ON CAST TEXT LOWER UPPER CASE WHEN THEN ELSE END IS NULL COUNT HAVING".split(" "));
    const MOD_SRC = {observation: "env", feedback: "env", memory: "agent", reflection: "agent", plan: "agent", action: "agent"};
    const argOf = a => { a = a.trim(); return /^'.*'$/.test(a) ? a.slice(1, -1) : /^-?\d+(\.\d+)?$/.test(a) ? Number(a) : a; };
    // The SQL's sub-searches: each CTE with the comment above it and the DSL calls inside.
    const subSearches = l => {
      const out = [];
      let note = "", cur = null;
      for (const line of l.sql.split("\n")) {
        const t = line.trim();
        if (t.startsWith("--")) { if (!cur) note = t.replace(/^--\s*/, ""); continue; }
        const m = !cur && t.match(/^(?:WITH\s+)?(\w+)\s+AS\s*\($/i);
        if (m) { cur = {name: m[1], purpose: note.replace(/^Purpose:\s*/i, ""), body: []}; note = ""; continue; }
        if (cur) { if (/^\),?$/.test(t)) { out.push(cur); cur = null; } else cur.body.push(line); }
      }
      const anchor = l.step;
      for (const c of out) {
        c.calls = [...c.body.join("\n").matchAll(/\b([A-Z][A-Z_]{3,})\(([^()]*)\)/g)].filter(m => !SQL_KW.has(m[1])).map(m => {
          const args = m[2].split(",").map(argOf);
          const fn = m[1], track = fn === "TRACK_ACTION";
          const call = {fn, args, kw: String(args[0] ?? ""), module: track ? "action" : String(args[1] ?? ""), a: track ? args[1] : args[2], b: track ? args[2] : args[3]};
          call.matched = l.scopes.find(x => x.fn === fn && JSON.stringify(x.args) === JSON.stringify(args))?.matched ?? null;
          call.hits = new Set(l.rows.filter(r => r.step >= call.a && r.step <= call.b && r.module === call.module && (fn === "FIND_CLAIMS" || String(r.text).toLowerCase().includes(call.kw.toLowerCase()))).map(r => r.step));
          return call;
        });
        // An action verb the log words differently (put → "move … to …"): its matched rows are the
        // action rows in range no other tracked verb claimed.
        const tracked = c.calls.filter(x => x.fn === "TRACK_ACTION");
        for (const x of tracked) if (x.matched && !x.hits.size) {
          const taken = new Set(tracked.filter(y => y !== x).flatMap(y => [...y.hits]));
          x.hits = new Set(l.rows.filter(r => r.module === "action" && r.step >= x.a && r.step <= x.b && !taken.has(r.step)).map(r => r.step));
        }
        const mods = [...new Set(c.calls.map(x => x.module))];
        const from = Math.min(...c.calls.map(x => x.a).filter(Number.isFinite));
        c.side = mods.every(m => MOD_SRC[m] === "env") ? "env" : from > anchor ? "later" : "agent";
        c.mods = mods;
        c.words = [...new Set(c.calls.filter(x => x.fn !== "FIND_CLAIMS").map(x => x.kw))];
        c.claims = [...new Set(c.calls.filter(x => x.fn === "FIND_CLAIMS").map(x => x.kw.replace(/_/g, " ")))];
        c.range = [from, Math.max(...c.calls.map(x => x.b).filter(Number.isFinite))];
        c.hits = new Set(c.calls.flatMap(x => [...x.hits]));
        c.matched = c.calls.reduce((n, x) => n + (x.matched || 0), 0);
      }
      return out;
    };
    // A search that decides the question: it looked up to the window and found the word only at
    // the window's own step (or nowhere), so nothing earlier set the idea up.
    const keyFindings = (l, subs) => {
      const seen = new Set();
      return subs.flatMap(c => c.calls).filter(x => x.fn === "SEARCH_TEXT" && MOD_SRC[x.module] === "agent" && Number.isFinite(x.b) && x.b <= l.step && x.a < l.step
        && (x.matched === 0 ? x.b < l.step : x.hits.size === 1 && x.hits.has(l.step))).filter(x => { const k = `${x.kw}|${x.module}`; if (seen.has(k)) return false; seen.add(k); return true; });
    };
    // One card per word and range, naming every module it was searched in.
    const groupFindings = keys => {
      const m = new Map();
      for (const x of keys) { const k = `${x.kw}|${x.a}|${x.b}`; const g = m.get(k) || {...x, modules: [], matched: 0}; g.modules.push(x.module); g.matched += x.matched || 0; m.set(k, g); }
      return [...m.values()];
    };
    const findingCard = (x, l) => `<div class="sd-key"><span class="sd-key-tag">★ key finding</span>
      <p>Searched ${x.modules.map(m => `<span class="sd-modtag" data-src="${MOD_SRC[m] || "agent"}">${esc(m)}</span>`).join(" ")} for “${mark(x.kw)}” in <b class="sd-stepno">steps ${x.a}–${x.b}</b>
      → <b>${x.matched ? `${plural(x.matched, "match", "matches")}, all at step ${l.step}` : "no match"}</b></p>
      <p class="sd-key-so">⇒ ${x.matched ? `nothing before step ${l.step} sets this up: it first appears at step ${l.step}` : `the agent never writes it before step ${l.step}`}</p></div>`;
    const SIDE_NAME = {agent: "agent's mind", env: "what happened", later: "after the window"};
    const fold = text => text ? `<details class="sd-full"><summary>full text</summary><p>${mark(text)}</p></details>` : "";
    const subCard = (c, keys) => `<li class="sd-sub" data-side="${c.side}"${c.calls.some(x => keys.includes(x)) ? ` data-key` : ""}>
        <div class="sd-sub-head"><span class="sd-sub-tag">${esc(c.name.replace(/_/g, " ").toUpperCase())}</span><span class="sd-k">${SIDE_NAME[c.side]}</span></div>
        ${c.purpose ? `<p>${mark(clip(c.purpose, 150))}</p>` : ""}
        <div class="sd-qres">${c.words.map(w => `<span class="sd-chip">${mark(w)}</span>`).join("")}${c.claims.map(w => `<span class="sd-chip"><em>claim</em> ${esc(w)}</span>`).join("")}${c.mods.map(m => `<span class="sd-modtag" data-src="${MOD_SRC[m] || "agent"}">${esc(m)}</span>`).join("")}</div>
        <div class="sd-qres">${miniStrip(c.hits)}<span class="sd-k">steps ${c.range[0]}–${c.range[1]}</span>${num(c.matched, c.matched === 1 ? "match" : "matches")}<span class="sd-k">${c.hits.size && c.hits.size <= 4 ? `at ${esc(stepList([...c.hits].sort((a, b) => a - b)))}` : `in ${plural(c.hits.size, "step")}`}</span></div>
      </li>`;
    // The steps the verdict names, read from the candidate's own argument.
    const citedOf = l => {
      const text = [...cands(l).map(c => `${c.proposition} ${c.argument}`), l.assessNote].join(" ");
      const out = new Set([...text.matchAll(/\bsteps?[- ](\d+)/gi)].map(m => +m[1]).filter(s => s >= 1 && s <= steps));
      cands(l).forEach(c => out.add(c.step));
      return out;
    };
    const hlSql = (sql, names) => {
      const re = /(--[^\n]*)|('[^'\n]*')|\b(\d+)\b|\b([A-Za-z_]\w*)\b/g;
      let html = "", i = 0, m;
      while ((m = re.exec(sql))) {
        html += esc(sql.slice(i, m.index)); i = re.lastIndex;
        if (m[1]) html += `<span class="sq-cm">${esc(m[1])}</span>`;
        else if (m[2]) { const inner = m[2].slice(1, -1), src = MOD_SRC[inner.toLowerCase()]; html += `<span class="sq-str">'${src ? `<span class="sq-mod" data-src="${src}">${esc(inner)}</span>` : mark(inner)}'</span>`; }
        else if (m[3]) html += `<span class="sq-num">${m[3]}</span>`;
        else { const w = m[4], call = sql[re.lastIndex] === "(";
          html += SQL_KW.has(w.toUpperCase()) ? `<span class="sq-kw">${w}</span>` : call && /^[A-Z][A-Z_]{3,}$/.test(w) ? `<span class="sq-fn">${w}</span>` : names.has(w) ? `<b class="sq-cte">${w}</b>` : esc(w); }
      }
      return html + esc(sql.slice(i));
    };
    const rowsBy = (l, only) => { const m = new Map(); for (const r of l.rows) { if (only && !only(r.step)) continue; if (!m.has(r.step)) m.set(r.step, []); m.get(r.step).push(r); } return [...m].sort((a, b) => a[0] - b[0]); };
    const stepRows = (l, list) => `<ol class="sd-steps">${list.map(([s, rs]) => `<li class="sd-step" data-step="${s}"><h5>Step ${s}${s === l.step ? `<i class="sd-tag">window</i>` : ""}${cands(l).some(c => c.step === s) ? `<i class="sd-tag is-root">blamed</i>` : ""}</h5>${rs.map(r => [r, goalWords.filter(w => String(r.text).toLowerCase().includes(w)).length]).sort((a, b) => b[1] - a[1]).map(([r]) => r).slice(0, 3).map(r => `<div class="sd-cellrow" data-src="${MOD_SRC[r.module] || "agent"}" data-fids="${fids(cell(r.step, MODULE_SLOT[r.module] || r.module).map(f => f.fid))}"><span class="sd-k sd-src">${esc(r.module)}</span><p>${mark(clip(/^[a-z]/.test(r.text) ? `…${r.text}` : r.text, 200))}</p></div>`).join("")}${rs.length > 3 ? `<span class="sd-k">+${rs.length - 3} more rows</span>` : ""}</li>`).join("")}</ol>`;
    const tiers = (l, cited) => {
      const blamedSteps = new Set(cands(l).map(c => c.step));
      return `<div class="sd-tiers">
        <span class="sd-k">read</span>${miniStrip(new Set(l.steps))}<b class="sd-qn">${l.steps.length}</b>
        <span class="sd-k">cited</span>${miniStrip(cited, "cited")}<b class="sd-qn is-cited">${cited.size}</b>
        <span class="sd-k">blamed</span>${miniStrip(blamedSteps, "root")}<b class="sd-qn is-root">${blamedSteps.size}</b></div>`;
    };
    // The searches answer three plain questions: did the agent hold the idea before the window,
    // what really happened, and did the agent come back to it after. A search of the agent's own
    // text that spans the window counts on both sides, cut at the window.
    const steps4 = set => { const a = [...set].sort((x, y) => x - y); return a.length > 4 ? `${a.slice(0, 4).join(", ")} …` : a.join(", "); };
    const qa = (l, subs, keys) => {
      const w = l.step, calls = subs.flatMap(c => c.calls);
      const isFact = x => x.fn === "TRACK_ACTION" || MOD_SRC[x.module] === "env";
      const mind = calls.filter(x => !isFact(x)), factCalls = calls.filter(isFact);
      const key = groupFindings(keys)[0];
      const words = [...new Set(mind.filter(x => x.fn === "SEARCH_TEXT").map(x => x.kw))];
      const disputed = key ? [key.kw] : words.slice(0, 2);
      const said = disputed.map(x => `“${mark(x)}”`).join(" / ") || "it";
      const pick = xs => xs.filter(x => !disputed.length || disputed.includes(x.kw));
      const hitsIn = (xs, ok) => new Set(pick(xs).flatMap(x => [...x.hits]).filter(ok));
      const beforeCalls = mind.filter(x => x.a <= w), afterCalls = mind.filter(x => x.b > w);
      const hits = {before: hitsIn(beforeCalls, t => t <= w), facts: new Set(factCalls.flatMap(x => [...x.hits])), after: hitsIn(afterCalls, t => t > w)};
      const span = (xs, lo, hi) => xs.length ? [Math.max(lo, Math.min(...xs.map(x => x.a))), Math.min(hi, Math.max(...xs.map(x => x.b)))] : null;
      const ranges = {before: span(beforeCalls, 1, w), facts: span(factCalls, 1, steps), after: span(afterCalls, w + 1, steps)};
      const earlier = [...hits.before].filter(t => t < w);
      const factLines = factCalls.filter(x => x.hits.size).map(x => x.fn === "TRACK_ACTION"
        ? `<b>${esc(x.kw)}</b> at step ${steps4(x.hits)}` : `${mark(x.kw)} seen at ${steps4(x.hits)}`);
      const card = (g, q, verdict, tone, detail, n) => n ? `<li class="sd-qa" data-group="${g}">
          <div class="sd-qa-q"><span class="sd-qa-tag">${g.toUpperCase()}</span>${q}</div>
          <div class="sd-qa-a"><span class="sd-qa-verdict" data-tone="${tone}">${verdict}</span>${detail}</div>
          <div class="sd-k">${plural(n, "search", "searches")}</div></li>` : "";
      return {
        hits, ranges,
        cards: card("before", `Before step ${w}, did the agent already write ${said}?`,
            key || !earlier.length ? "NO" : "YES", key || !earlier.length ? "no" : "yes",
            key || !earlier.length ? (hits.before.has(w) ? `it first appears at <b class="sd-stepno">step ${w}</b>` : `not before step ${w}`) : `already at <b class="sd-stepno">steps ${steps4(earlier)}</b>`, beforeCalls.length)
          + card("facts", `What did the environment show, and what did the agent do?`, factLines.length ? "SEEN" : "NOTHING", "env", factLines.join(" · ") || "no matching observation or action", factCalls.length)
          + card("after", `After step ${w}, did the agent come back to ${said}?`, hits.after.size ? "YES" : "NO", hits.after.size ? "yes" : "no",
            hits.after.size ? `in ${plural(hits.after.size, "later step")} (${Math.min(...hits.after)}–${Math.max(...hits.after)})` : "never mentioned again", afterCalls.length),
      };
    };
    // Where each question looked on the run, and where it hit; the window marked.
    const timeline = (l, g) => {
      const x = t => ((t - 0.5) / steps * 100).toFixed(2);
      const row = k => {
        const r = g.ranges[k];
        if (!r) return "";
        return `<span class="sd-tl-lab" data-group="${k}">${k.toUpperCase()}</span><div class="sd-tl-track" data-group="${k}">
          <i class="sd-tl-range" style="left:${((r[0] - 1) / steps * 100).toFixed(2)}%;width:${((r[1] - r[0] + 1) / steps * 100).toFixed(2)}%"></i>
          ${[...g.hits[k]].map(t => `<i class="sd-tl-dot${t === l.step ? " is-win" : ""}" style="left:${x(t)}%" title="step ${t}"></i>`).join("")}</div>`;
      };
      return `<div class="sd-tl" style="--win:${x(l.step)}%">
        <span></span><div class="sd-tl-axis"><span style="left:0">1</span><span class="is-win" style="left:${x(l.step)}%">step ${l.step}</span><span style="left:100%">${steps}</span></div>
        ${row("before")}${row("facts")}${row("after")}</div>`;
    };
    const info = ls.map(l => { const subs = subSearches(l); const keys = keyFindings(l, subs); return {l, subs, keys, cited: citedOf(l), qa: qa(l, subs, keys)}; });
    const blamed = scene.candidates;
    const named = [...new Set(blamed.map(c => `step ${c.step}'s ${c.module}`))];
    const one = ls.length === 1, v = (s1, s2) => one ? s1 : s2;
    const nRows = ls.reduce((n, l) => n + l.rows.length, 0), nSteps = new Set(ls.flatMap(l => l.steps)).size;
    const nCited = new Set(info.flatMap(x => [...x.cited])).size;
    out.TRACE = {
      say: `${one ? "A trace agent searches" : `${ls.length} trace agents search`} the logs with ${plural(info.reduce((n, x) => n + x.subs.length, 0), "sub-search", "sub-searches")} and ${v("reads", "read")} ${plural(nRows, "row")} from ${plural(nSteps, "step")}, but ${v("its verdict rests", "their verdicts rest")} on ${nCited <= 4 ? stepList([...new Set(info.flatMap(x => [...x.cited]))].sort((a, b) => a - b)) : plural(nCited, "step")}${named.length ? `: ${v("it blames", "they blame")} ${named.length > 1 ? `${named.slice(0, -1).join(", ")} and ${named[named.length - 1]}` : named[0]}` : ""}.`,
      title: "What TRACE looked for, found and used",
      lead: "",
      body: info.map(({l, subs, keys, cited, qa}) => {
        const notCited = rowsBy(l, s => !cited.has(s));
        return `<div class="sd-lane">
          <div class="sd-sec"><div class="sd-k">what it wants to know · agent ${esc(l.agent)} · ${plural(subs.length, "sub-search", "sub-searches")}</div>
            <div class="sd-tree-q">${mark(clip((l.purpose || question(l, 220)).split(/(?<=[.;])\s+/)[0].replace(/[,;]\s*$/, ""), 160))}</div>${fold(l.purpose)}
            ${timeline(l, qa)}
            <ol class="sd-qas">${qa.cards}</ol>
            <details class="sd-full sd-how"><summary>how it searched · ${plural(subs.length, "sub-search", "sub-searches")}</summary>
              ${groupFindings(keys).map(x => findingCard(x, l)).join("")}
              <ol class="sd-subs">${subs.map(c => subCard(c, keys)).join("")}</ol></details></div>
          <div class="sd-sec"><div class="sd-k">steps read vs used · ${l.steps.length} read · ${cited.size} cited</div>${tiers(l, cited)}
            ${stepRows(l, rowsBy(l, s => cited.has(s)))}
            ${notCited.length ? `<details class="sd-full"><summary>${plural(notCited.length, "more step")} read but not cited</summary>${stepRows(l, notCited)}</details>` : ""}</div>
          ${cands(l).length ? `<div class="sd-sec"><div class="sd-k">what it concludes</div><ul class="sd-list">${cands(l).map(c => `<li data-step="${c.step}"${c.fid != null ? ` data-fids="${c.fid}"` : ""}><span class="sd-k">step ${c.step} · ${esc(c.module)} · ${esc([statusText(c.status), faultText(c.faultClass)].filter(Boolean).join(", "))}</span><span>${mark(clip(c.proposition, 260))}</span></li>`).join("")}</ul></div>` : ""}
          <div class="sd-sec"><div class="sd-k">DSL query · ${plural(l.scopes.length, "search call")}</div><pre class="sd-sql">${hlSql(l.sql, new Set(subs.map(c => c.name)))}</pre></div>
        </div>`;
      }).join(""),
      rail: info.map(({l, keys, cited}) => `<h4>Steps · ${esc(l.agent)}</h4>${tiers(l, cited)}
        ${keys.length ? `<h4>Key finding</h4>${groupFindings(keys).map(x => `<p class="sd-key-mini">“${mark(x.kw)}” in ${esc(x.modules.join(", "))}, steps ${x.a}–${x.b} → ${x.matched ? `only step ${l.step}` : "none"}</p>`).join("")}` : ""}`).join("")
        + `<h4>Blamed</h4><div class="sd-chips">${blamed.map(c => `<button class="sd-chip is-root" data-goto="step:${c.step}"${c.fid != null ? ` data-fids="${c.fid}"` : ""}><b>step ${c.step}</b><em>${esc(c.module)}</em></button>`).join("") || `<span class="sd-k">none</span>`}</div>`,
    };
  }

  // SUBMIT: the verdict with the agent's own words, the rule that decided it, and the fix;
  // the free-text reasoning folds under the verdict.
  {
    const sentences = t => String(t || "").split(/(?<=[.?!])\s+/).map(x => x.trim()).filter(Boolean);
    const first = t => sentences(t)[0] || "";
    const stepsIn = t => [...String(t || "").matchAll(/\bsteps?[- ](\d+)/gi)].map(m => +m[1]).filter(s => s >= 1 && s <= steps);
    const stepHl = html => html.replace(/\bsteps?[- ]\d+(?:[–-]\d+)?/g, m => `<b class="sd-stepno">${m}</b>`);
    const fold = (text, label = "full text") => text ? `<details class="sd-full"><summary>${label}</summary><p>${stepHl(mark(text))}</p></details>` : "";
    const srcOf = m => /observation|feedback/.test(m) ? "env" : "agent";
    const ANSWER = {supported: "YES", refuted: "NO", unknown: "CAN'T TELL"};
    const answerOf = r => (r.conds?.length > 1 ? r.conds : [r]).map(c => ANSWER[c.outcome] || String(c.outcome || "?").toUpperCase()).join(" + ");
    const rule = root?.rule?.id ? scene.rules.find(r => r.id === root.rule.id) : null;
    const cited = new Set(stepsIn(root?.reasoning));
    const at = r => `step ${r.step} · ${esc(r.module || "?")}`;
    out.SUBMIT = root ? {
      say: `ProDebugger names step ${root.step}'s ${esc(root.module)} as the root cause: ${mark(clip(root.proposition, 200))}`,
      title: "The verdict, and what it rests on",
      lead: `SUBMIT weighs TRACE's candidates against the manager's rules and names one step.`,
      body: `<div class="sd-sec" data-step="${root.step}"${root.fid != null ? ` data-fids="${root.fid}"` : ""}><div class="sd-k">the verdict · step ${root.step} · ${esc(root.module)}</div>
          <div class="sd-verdict">
            <div class="sd-verdict-head"><span class="sd-verdict-tag">ROOT CAUSE</span><b>${at(root)}</b>${root.faultClass ? `<span class="sd-k">${esc(faultText(root.faultClass))}</span>` : ""}<span class="sd-state${root.accepted ? " is-ok" : ""}">${root.accepted ? "accepted" : esc(root.status || "not accepted")}</span></div>
            <p class="sd-verdict-claim">${mark(root.proposition)}</p>
            ${root.quote ? `<blockquote class="sd-quote-card" data-src="${srcOf(root.module)}" data-step="${root.step}"><span class="sd-k">the agent wrote · step ${root.step} · ${esc(root.module)}</span>${mark(root.quote)}</blockquote>` : ""}
            ${fold(root.reasoning, "SUBMIT's full reasoning")}
          </div></div>
        ${rule ? `<div class="sd-sec"><div class="sd-k">the rule that decided it · fixed by the manager before TRACE searched</div>
          <ol class="sd-verdicts">${[rule, ...scene.rules.filter(r => r !== rule)].map(r => `<li class="${r === rule ? "is-used" : "is-dim"}">
            <span class="sd-outcome" data-outcome="${esc(r.outcome)}">${esc(answerOf(r))}</span>
            <p>${r === rule ? `<span class="sd-k">TRACE found</span> ${mark(clip(first(r.finding), 170) || "—")} <span class="sd-k">so</span> blame <b class="sd-stepno">step ${root.step}</b>'s ${esc(root.module)}` : `<span class="sd-k">not taken ·</span> ${esc(r.id.replace(/_/g, " "))}`}</p>
            ${r === rule ? `<span class="sd-used">★ applied</span>` : "<span></span>"}</li>`).join("")}</ol>
          <details class="sd-full"><summary>the rule in full</summary><p><span class="sd-k">${esc(rule.id.replace(/_/g, " "))}</span><br>${rule.finding ? `<span class="sd-k">if</span> ${mark(rule.finding)} ` : ""}<span class="sd-k">then</span> ${mark(rule.conclusion)}${rule.caveat ? ` <span class="sd-k">caveat</span> ${mark(rule.caveat)}` : ""}</p></details></div>` : ""}
        ${root.repair ? `<div class="sd-sec"><div class="sd-k">how to fix it · step ${root.step} · ${esc(root.module)}</div><div class="sd-fix"><span class="sd-fix-tag">FIX</span><p>${stepHl(mark(root.repair))}</p></div></div>` : ""}`,
      rail: `<h4>Root</h4><div class="sd-card is-root"${root.fid != null ? ` data-fids="${root.fid}"` : ""}><b>step ${root.step} · ${esc(root.module)}</b><span class="sd-k">${esc(faultText(root.faultClass))}</span></div>
        <h4>Steps it cites <small>${cited.size}</small></h4>${strip(steps, new Map([...[...cited].map(s => [s, {tone: "read", label: "cited"}]), [root.step, {tone: "root", label: "root"}]]))}
        <h4>Status</h4><p><span class="sd-state${root.accepted ? " is-ok" : ""}">${root.accepted ? "accepted" : esc(root.status || "not accepted")}</span></p>
        ${rule ? `<h4>Answer used</h4><p class="sd-rail-used"><span class="sd-outcome" data-outcome="${esc(rule.outcome)}">${esc(answerOf(rule))}</span><span>blame <b class="sd-stepno">step ${root.step}</b>'s ${esc(root.module)}</span></p>` : ""}`,
    } : {
      say: "ProDebugger stops without naming a root cause.", title: "No verdict", lead: "", body: "", rail: "",
    };
  }

  // The footer caption for one tour event: an action tag, the title with its key words lit,
  // and one line of what the action produced.
  const VERDICT_TONE = {viol: "task", unk: "soft", sat: "ok"};
  const goalEv = scene.events.find(e => e.goal);
  function caption(ev) {
    const title = stepNo(mark(ev.title || ""));
    const lane = ev.lanes?.length ? scene.lanes.find(l => l.id === ev.lanes[0]) : null;
    if (ev.error) return {kind: "REJECTED", title, line: `<span class="sd-k">${esc(clip(ev.error.message, 140))}</span>`};
    switch (ev.name) {
      case "compile": {
        const env = scene.facts.filter(f => f.slot === "obs").length, chk = scene.facts.filter(f => f.slot === "chk").length, all = scene.facts.length;
        return {kind: "COMPILE", title, line: `${num(all, "facts")}${num(scene.entities.length, "entities")}${num(steps, "steps")}<span class="sd-split" title="who wrote the lines"><i data-src="env" style="flex:${env}">env ${env}</i><i data-src="agent" style="flex:${all - env - chk}">agent ${all - env - chk}</i>${chk ? `<i data-src="check" style="flex:${chk}" title="checks">${chk}</i>` : ""}</span>`};
      }
      case "view": case "view_evidence": {
        if (ev.phase === "MAP") {
          const st = ev.goal?.state || goalEv?.goal?.state, at = ev.goal?.decided || goalEv?.goal?.decided || [];
          const words = [...new Set(scene.goals.flatMap(g => [...g.constraints.filter(c => typeof c.expected === "string" && c.expected !== entName(g.target)).map(c => c.expected), `${g.rel} ${entName(g.target)}`]))];
          return {kind: "GOAL", title, line: `${words.map(w => `<span class="sd-chip" data-role="${w.includes(" ") ? "where" : "goal"}">${mark(w)}</span>`).join("")}${st ? `<span class="sd-state${/unmet|unsat|fail|false|viol/i.test(st) ? " is-bad" : ""}">${esc(st)}</span>` : ""}${at.length ? `<span class="sd-k">decided at</span><b class="sd-stepno">${esc(stepList(at))}</b>` : ""}`};
        }
        return {kind: queryKind(ev), title, line: `${miniStrip(stepsOf(ev.fids))}${num(ev.fids.length, "facts")}${signalPills(ev.signals)}${hitChips(hitsOf(ev.fids))}`};
      }
      case "inspect":
        return {kind: "INSPECT", title, line: `${miniStrip(stepsOf(ev.fids))}${(ev.verdicts || []).map(([v, n]) => `<span class="sd-sig" data-sev="${VERDICT_TONE[v] || "soft"}"><b>${n}</b> ${esc(VERDICT_WORD[v] || v)}</span>`).join("")}${hitChips(hitsOf(ev.fids))}`};
      case "route_cases": {
        const ws = scene.windows.filter(w => ev.windows.includes(w.id));
        return {kind: "WINDOW", title, line: `${miniStrip(new Set())}${ws.map(w => {
          const a = w.fids.map(f => scene.factById.get(f)).find(f => f?.slot === "act");
          return `<span class="sd-hit" data-goto="step:${w.step}">${esc(w.caseId)} · step ${w.step}</span>${num(w.fids.length, "clue facts")}${a ? `<span class="sd-quote">“${mark(clip(a.span, 60))}”</span>` : ""}`;
        }).join("")}`};
      }
      case "plan_trace": {
        const ls = scene.lanes.filter(l => ev.lanes.includes(l.id));
        return {kind: "PLAN", title, line: `${ls.map(l => `<span class="sd-chip is-hot"><b>${esc(l.agent)}</b><em>← ${esc(l.anchor)} · step ${l.step}</em></span>`).join("")}${scene.rules.length ? num(scene.rules.length, "decision rules") : ""}`};
      }
      case "query_logs":
        return {kind: "SEARCH", title, line: `${miniStrip(new Set(lane?.steps || []))}${num(lane?.rows.length ?? 0, "rows")}${num(lane?.steps.length ?? 0, "steps")}`};
      case "assess": {
        const cs = scene.candidates.filter(c => ev.candidates.includes(c.id));
        return {kind: "ASSESS", title, line: cs.map(c => `<span class="sd-cand${c.status === "error" ? " is-bad" : ""}">step ${c.step} · ${esc(c.module)} · ${esc(statusText(c.status))}</span><span class="sd-k">${esc(faultText(c.faultClass))}</span>`).join("") || `<span class="sd-k">no step blamed</span>`};
      }
      case "submit":
        return root
          ? {kind: "ROOT", title, line: `${miniStrip(new Set([root.step]), "root")}<span class="sd-cand is-bad">step ${root.step} · ${esc(root.module)}</span><span class="sd-k">${esc(faultText(root.faultClass))}</span>${root.rule?.id ? `<span class="sd-chip"><em>rule</em> <b>${esc(root.rule.id.replace(/_/g, " "))}</b></span>` : ""}<span class="sd-state${root.accepted ? " is-ok" : ""}">${root.accepted ? "accepted" : esc(root.status || "not accepted")}</span>`}
          : {kind: "ROOT", title, line: `<span class="sd-k">no root cause</span>`};
      default:
        return {kind: ev.phase, title, line: `<span class="sd-k">${esc(clip(ev.caption, 160))}</span>`};
    }
  }

  return {phases: out, mark, entFacts, caption};
}

// Mounts the sentence, the popup and the rail on the page, and wires them together.
export function createDetails(root, scene, model, stage, {onOpen} = {}) {
  const {phases, mark, entFacts, caption} = buildDetails(scene, model);
  const h1 = root.querySelector(".qg-title h1");
  if (h1) h1.innerHTML = mark(scene.meta.task || scene.meta.tid);
  root.insertAdjacentHTML("beforeend", `
    <div class="qg-sd-say" hidden><span class="qg-sd-ph"></span><p></p><button class="qg-sd-more" type="button">more details</button></div>
    <section class="qg-sd-pop" aria-label="Stage details" hidden><header><span class="qg-sd-ph"></span><h3></h3><button class="qg-sd-close" type="button" title="Close (Esc)" aria-label="Close">×</button></header><p class="qg-sd-lead"></p><div class="qg-sd-body"></div></section>
    <aside class="qg-sd-rail" aria-label="What this stage produced" hidden><div class="qg-sd-railbody"></div></aside>`);
  const say = root.querySelector(".qg-sd-say"), pop = root.querySelector(".qg-sd-pop"), rail = root.querySelector(".qg-sd-rail");
  const focusColor = getComputedStyle(root).getPropertyValue("--qg-focus").trim() || "#e0b867";
  let phase = null, open = false, flyTimer = 0;

  function fill() {
    const d = phases[phase];
    if (!d) return;
    root.querySelectorAll(".qg-sd-ph").forEach(el => { el.textContent = phase; el.dataset.phase = phase; });
    say.dataset.phase = phase;
    say.querySelector("p").innerHTML = d.say;
    pop.querySelector("h3").textContent = d.title;
    pop.querySelector(".qg-sd-lead").innerHTML = d.lead || "";
    pop.querySelector(".qg-sd-lead").hidden = !d.lead;
    pop.querySelector(".qg-sd-body").innerHTML = d.body;
    pop.dataset.phase = phase;
    // Each section's label becomes a numbered heading: its name, then the rest as a badge.
    let n = 0;
    pop.querySelectorAll(".qg-sd-body .sd-sec > .sd-k:first-child").forEach(k => {
      const [name, ...rest] = k.textContent.split(" · ");
      k.className = "sd-h";
      k.innerHTML = `<b>${++n}</b><span>${esc(name)}</span>${rest.length ? `<em>${esc(rest.join(" · "))}</em>` : ""}`;
    });
    pop.querySelector(".qg-sd-body").scrollTop = 0;
    rail.querySelector(".qg-sd-railbody").innerHTML = d.rail;
  }

  // The rail's entities fly in from where the popup first names them.
  function fly() {
    const chips = [...rail.querySelectorAll("[data-fly] [data-ent]")];
    const box = pop.querySelector(".qg-sd-body").getBoundingClientRect();
    chips.forEach(c => { c.style.opacity = "0"; });
    chips.forEach((chip, k) => {
      const from = [...pop.querySelectorAll(`.qg-ent[data-ent="${CSS.escape(chip.dataset.ent)}"]`)]
        .find(el => { const r = el.getBoundingClientRect(); return r.top >= box.top && r.bottom <= box.bottom; });
      setTimeout(() => {
        if (!open) return;
        if (!from) { chip.style.opacity = ""; return; }
        const a = from.getBoundingClientRect(), b = chip.getBoundingClientRect(), base = root.getBoundingClientRect();
        const ghost = document.createElement("b");
        ghost.className = "qg-sd-ghost";
        ghost.textContent = from.textContent;
        ghost.style.left = `${a.left - base.left}px`;
        ghost.style.top = `${a.top - base.top}px`;
        root.append(ghost);
        from.classList.add("is-hl");
        ghost.animate([{transform: "translate(0,0)", opacity: 1}, {transform: `translate(${b.left - a.left}px,${b.top - a.top}px)`, opacity: .9}],
          {duration: 650, easing: "cubic-bezier(.5,0,.2,1)"}).onfinish = () => { ghost.remove(); chip.style.opacity = ""; from.classList.remove("is-hl"); };
      }, 380 + k * 90);
    });
  }

  function setOpen(on) {
    open = on;
    // The popup and rail start under the case title, however many lines it takes.
    const title = root.querySelector(".qg-title")?.getBoundingClientRect();
    if (title) root.style.setProperty("--qg-sd-top", `${title.bottom - root.getBoundingClientRect().top + 10}px`);
    clearTimeout(flyTimer);
    root.classList.toggle("has-sd", on);
    pop.hidden = rail.hidden = false;
    requestAnimationFrame(() => { pop.classList.toggle("is-in", on); rail.classList.toggle("is-in", on); });
    if (!on) { light(null, null); setTimeout(() => { if (!open) pop.hidden = rail.hidden = true; }, 360); }
    else {
      const d = phases[phase], body = pop.querySelector(".qg-sd-body");
      const at = d?.focus != null ? body.querySelector(`[data-step="${d.focus}"]`) : null;
      // Under the sticky jump bar, with a line of the step before showing.
      if (at) body.scrollTop += at.getBoundingClientRect().top - body.getBoundingClientRect().top - (body.querySelector(".sd-jump")?.offsetHeight || 0) - 14;
      if (d?.fly) fly();
    }
  }

  // Pointing at an entity or a fact row lights its twins on the page and its facts on the tower.
  let lit = null;
  const light = (key, list) => {
    if (lit === (key || list)) return;
    lit = key || list;
    root.querySelectorAll(".qg-ent.is-hl, .sd-chip.is-hl").forEach(el => el.classList.remove("is-hl"));
    if (key) root.querySelectorAll(`[data-ent="${CSS.escape(key)}"]`).forEach(el => el.classList.add("is-hl"));
    if (root.classList.contains("is-story")) return;
    const f = key ? entFacts.get(key) : list ? list.split(",").map(Number) : null;
    stage.markFacts(f?.length ? [{fids: f, color: focusColor}] : []);
  };
  root.addEventListener("mouseover", e => {
    const t = e.target.closest?.("[data-ent], [data-fids]");
    if (!t || !t.closest(".qg-sd-say, .qg-sd-pop, .qg-sd-rail, .qg-title")) { if (lit) light(null, null); return; }
    t.dataset.ent ? light(t.dataset.ent, null) : light(null, t.dataset.fids);
  });
  // A rail chip or strip cell scrolls the popup to the first place it names, and flashes it.
  const goto = e => {
    const go = e.target.closest("[data-goto]")?.dataset.goto;
    if (!go) return;
    const [kind, ...rest] = go.split(":"), v = rest.join(":");
    const body = pop.querySelector(".qg-sd-body");
    const hit = kind === "step" ? body.querySelector(`[data-step="${v}"]`) || [...body.querySelectorAll("[data-step]")].find(el => Number(el.dataset.step) >= Number(v))
      : body.querySelector(`.qg-ent[data-ent="${CSS.escape(v)}"]`);
    if (!hit) return;
    hit.scrollIntoView({block: "center", behavior: "smooth"});
    hit.classList.remove("is-flash"); void hit.offsetWidth; hit.classList.add("is-flash");
  };
  // PARSE: a step's module tabs open and close that module's row; all start folded.
  const toggleModule = e => {
    const tab = e.target.closest("[data-slot-toggle]");
    if (!tab) return;
    const row = tab.closest(".sd-step")?.querySelector(`.sd-cellrow[data-slot="${tab.dataset.slotToggle}"]`);
    if (!row) return;
    const on = tab.getAttribute("aria-expanded") !== "true";
    tab.setAttribute("aria-expanded", String(on));
    row.hidden = !on;
  };
  rail.addEventListener("click", goto);
  pop.addEventListener("click", toggleModule);
  pop.addEventListener("click", goto);
  say.querySelector(".qg-sd-more").addEventListener("click", () => { onOpen?.(); setOpen(true); });
  pop.querySelector(".qg-sd-close").addEventListener("click", () => setOpen(false));

  return {
    get isOpen() { return open; },
    // The tour moved to a phase: the sentence follows it, and an open popup with it.
    show(ph) {
      if (ph === phase) return;
      phase = ph;
      fill();
      say.hidden = !phases[ph];
      say.classList.remove("is-new"); void say.offsetWidth; say.classList.add("is-new");
      if (open && phases[ph]?.fly) fly();
    },
    close() { if (open) setOpen(false); },
    // The footer caption in the same shape as the popup's rows.
    caption(ev) {
      const c = caption(ev), box = root.querySelector(".qg-caption");
      if (!box) return;
      box.classList.add("is-rich");
      box.querySelector(".qg-cap-title").innerHTML = `<span class="sd-qtag" data-kind="${c.kind}" title="${esc(ev.raw || ev.name || "")}">${c.kind}</span><span>${c.title}</span>`;
      box.querySelector(".qg-cap-text").innerHTML = `<span class="sd-qres">${c.line}</span>`;
    },
  };
}
