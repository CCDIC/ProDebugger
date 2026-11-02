// Debugger process (one card per phase, expanding into one node per model call) above the
// original agent steps. Selecting a phase or call opens it in the side panel and links it
// to the steps it touched.
import {getJSON} from "../api.js?v=27bf1baa85";
import {EVAL_CLASS, factChip, factText, icon} from "../case/format.js?v=27bf1baa85";
import {callFindings, insightFor, plainReason, worstVerdict} from "../case/findings.js?v=b596342864";
import {actionLabel, ROLE_LABEL, stepRoles} from "../case/roles.js?v=27bf1baa85";
import {esc, pretty, short} from "../util.js?v=27bf1baa85";
import {callDetail, expandable, handoffHtml, layoutPhases, layoutProcess, overviewHtml, phaseDetail, processHtml, requestHtml} from "./process.js?v=27bf1baa85";
import {anchorsByStep, anchorTitle} from "./route.js?v=27bf1baa85";
import {candidateCard, candidatesByStep, planOf} from "./trace.js?v=27bf1baa85";
import {anchorCopy} from "./anchor-copy.js?v=readable-step-inspector-20261102";

export function createView(root, ctx) {
  const state = {
    model: null, sel: null, selPhase: null, openPhases: new Set(), hover: null, openStep: null, selStep: null, hoverStep: null,
    arrowPin: null, arrowHover: null, arrowMuted: null, stepSection: "facts", stepAnchor: null,
    requests: new Map(), phases: [], layout: null, bodyScroll: new Map(),
    linkPin: null,
  };
  root.innerHTML = `
    <div class="traj-head">
      <div><div class="eyebrow">Execution path</div><h2 class="traj-title">Debugger process ↔ agent trajectory</h2>
        <p class="traj-hint" id="tj-hint">Each card is one debugger phase; open it to see its model calls. Select a phase or call to inspect it; select a step to see every call that touched it.</p></div>
    </div>
    <div class="lanes" id="tj-lanes">
      <div class="proc is-side-closed" id="tj-proc">
        <section class="proc-graph" aria-label="Debugger model calls"><div class="proc-view" id="tj-graph" tabindex="0"></div></section>
        <div class="proc-side-col">
          <button class="side-toggle" id="tj-side-toggle" aria-controls="tj-side"></button>
          <aside class="proc-side" id="tj-side" aria-label="Selected call" hidden></aside>
        </div>
      </div>
      <div class="lane-gap" aria-hidden="true"></div>
      <section class="lane lane-steps" aria-label="Agent steps">
        <div class="lane-label"><span class="eyebrow">Agent</span><span id="tj-step-count" class="lane-count"></span><span class="lane-nav"><button class="btn" data-pan="tj-steps" data-dir="-1" aria-label="Scroll agent lane left">←</button><button class="btn" data-pan="tj-steps" data-dir="1" aria-label="Scroll agent lane right">→</button></span></div>
        <div class="lane-overview" id="tj-step-overview" role="group" aria-label="Step overview: red marks the selected root cause; gray marks all other steps"></div>
        <div class="lane-scroll" id="tj-steps" tabindex="0"><div class="lane-track" id="tj-step-track"></div></div>
      </section>
      <svg class="lane-links" id="tj-links" aria-hidden="true"></svg>
    </div>
    <div class="arrow-pop" id="tj-arrow-pop" role="tooltip" hidden></div>
    <div class="arrow-pop link-pop" id="tj-link-pop" role="tooltip" hidden></div>`;
  const $ = id => root.querySelector("#" + id);
  const graph = $("tj-graph"), side = $("tj-side"), steps = $("tj-steps"), lanes = $("tj-lanes");

  // The side panel starts collapsed to a strip and only opens from its toggle; selecting a
  // phase or call keeps it as it is. The graph refits to the width it frees or takes.
  function setSideOpen(open) {
    const toggle = $("tj-side-toggle");
    $("tj-proc").classList.toggle("is-side-closed", !open);
    side.hidden = !open;
    toggle.setAttribute("aria-expanded", open);
    toggle.title = open ? "Hide details" : "Show details";
    toggle.innerHTML = `<span class="side-toggle-ico" aria-hidden="true">${open ? "›" : "‹"}</span><span class="side-toggle-label">Details</span>`;
  }
  setSideOpen(false);

  // ---------- derived lookups ----------
  function stepIndex(model) {
    const facts = new Map(), evals = new Map(), refs = new Map();
    model.dsl?.facts.forEach(f => { if (!facts.has(f.t)) facts.set(f.t, []); facts.get(f.t).push(f); });
    model.evaluations.forEach(e => { if (!evals.has(e.t)) evals.set(e.t, []); evals.get(e.t).push(e); });
    model.debugger?.calls.forEach(c => c.steps.forEach(t => { if (!refs.has(t)) refs.set(t, []); refs.get(t).push(c.index); }));
    // Parse gaps: source text the parser could not account for (unknown, never an agent error).
    const gaps = new Map();
    (model.debugger?.parseAudit || []).filter(a => a.status === "unresolved").forEach(a => {
      if (!gaps.has(a.step)) gaps.set(a.step, []);
      gaps.get(a.step).push(a);
    });
    const anchors = anchorsByStep(model.debugger?.routeCandidates);
    const candidates = candidatesByStep(model.debugger?.calls || []);
    return {facts, evals, refs, gaps, anchors, candidates, maxRefs: Math.max(1, ...[...refs.values()].map(v => v.length))};
  }

  // manager_plan.json once saved; the committed plan_trace result while live.
  const planFor = d => d.managerPlan || d.calls.map(planOf).filter(Boolean).at(-1) || null;

  const callOf = key => key == null ? null : state.model?.debugger?.calls[Number(key)];
  const phaseOf = name => state.phases.find(p => p.phase === name);
  const cardOf = name => graph.querySelector(`.ph-card[data-phase="${name}"]`);

  // ---------- process graph ----------
  function renderGraph() {
    const d = state.model.debugger;
    $("tj-proc").hidden = !d;
    root.querySelector(".lane-gap").hidden = !d;
    if (!d) {
      $("tj-hint").textContent = "Compiled session: no debugger run yet. Click a step's facts or rule-check button to inspect its records.";
      return;
    }
    state.phases = layoutPhases(d.calls, d.parse, planFor(d));
    state.layout = state.phases.length ? layoutProcess(state.phases, state.openPhases) : null;
    // A card reopens scrolled to the top; open ones keep their scroll across re-renders.
    [...state.bodyScroll.keys()].forEach(name => { if (!state.openPhases.has(name)) state.bodyScroll.delete(name); });
    graph.innerHTML = state.layout ? processHtml(state.layout, {sel: state.sel, selPhase: state.selPhase}, Object.fromEntries(state.bodyScroll))
      : `<p class="empty lane-empty">No model calls recorded yet.</p>`;
    graph.querySelectorAll(".ph-body").forEach(body => { body.scrollTop = state.bodyScroll.get(body.dataset.body) || 0; syncBody(body); });
    drawHandoffs();
    fitGraph();
  }

  // Records a card body's scroll, fades the edge that has more calls and counts those below.
  function syncBody(body) {
    const top = body.scrollTop, bottom = top + body.clientHeight;
    if (body.classList.contains("is-scroll")) state.bodyScroll.set(body.dataset.body, top);
    const below = [...body.querySelectorAll(".pnode")].filter(n => n.offsetTop + n.offsetHeight > bottom + 1).length;
    body.classList.toggle("more-above", top > 1);
    body.classList.toggle("more-below", below > 0);
    const more = graph.querySelector(`[data-more="${body.dataset.body}"]`);
    if (more) { more.hidden = !below; more.textContent = `↓ ${below} more`; }
  }

  const drawHandoffs = () => {
    const g = graph.querySelector(".proc-handoffs");
    if (g && state.layout) g.innerHTML = handoffHtml(state.layout, Object.fromEntries(state.bodyScroll));
  };

  // Scrolls a selected call into view inside its card.
  function revealNode(key) {
    const node = key == null ? null : graph.querySelector(`.pnode[data-call="${key}"]`), body = node?.closest(".ph-body.is-scroll");
    if (!body) return;
    const top = node.offsetTop - 6, bottom = node.offsetTop + node.offsetHeight + 6;
    if (top < body.scrollTop) body.scrollTop = top;
    else if (bottom > body.scrollTop + body.clientHeight) body.scrollTop = bottom - body.clientHeight;
    else return;
    syncBody(body); drawHandoffs();
  }

  // Scrolls the frame sideways until a card is in view, when the graph overflows it.
  function revealCard(name) {
    const card = cardOf(name);
    if (!card || !graph.classList.contains("is-overflow")) return;
    const g = graph.getBoundingClientRect(), r = card.getBoundingClientRect();
    if (r.left < g.left + 16) graph.scrollLeft += r.left - g.left - 16;
    else if (r.right > g.right - 16) graph.scrollLeft += Math.min(r.right - g.right + 16, r.left - g.left - 16);
  }

  // Opening or closing cards morphs the graph (View Transitions): every card in sight moves and
  // resizes into place, and an opened card's calls fade in row by row. Only cards in sight are
  // named, since a named element is drawn above the page, unclipped, while the morph runs.
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  function nameCards(on) {
    const g = graph.getBoundingClientRect();
    const top = Math.max(g.top, ...[...document.querySelectorAll(".site-header, .wb-tabs")].map(el => el.getBoundingClientRect().bottom));
    graph.querySelectorAll(".ph-card, .ph-body").forEach(el => {
      const r = el.getBoundingClientRect();
      const seen = on && r.right > g.left && r.left < g.right && r.bottom > top && r.top < Math.min(g.bottom, innerHeight);
      el.style.viewTransitionName = seen ? `${el.dataset.phase ? "ph" : "phb"}-${el.dataset.phase || el.dataset.body}` : "";
    });
  }
  // was: the phases open before the change.
  function morph(update, was) {
    const run = () => {
      update();
      state.openPhases.forEach(name => { if (!was.has(name)) graph.querySelector(`[data-body="${name}"]`)?.classList.add("is-entering"); });
      scheduleLinks();
    };
    if (!document.startViewTransition || reduceMotion.matches) { run(); return; }
    nameCards(true);
    document.startViewTransition(() => { run(); nameCards(true); }).finished.finally(() => nameCards(false));
  }

  // The card a pointer is on, counting the open card's body of calls as part of it.
  const cardAt = el => el.closest(".ph-card") || cardOf(el.closest(".ph-body")?.dataset.body);

  // The graph fits its frame: a canvas wider than the frame or taller than its max-height is
  // scaled down (never up, and no further than MIN_SCALE so the text stays readable), and the
  // negative margins give back the space it frees. Whatever still does not fit scrolls.
  const MIN_SCALE = .75;
  function fitGraph() {
    const canvas = graph.querySelector(".proc-canvas");
    if (!canvas) return;
    const cs = getComputedStyle(graph);
    const w = parseFloat(canvas.style.width), h = parseFloat(canvas.style.height);
    const roomW = graph.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const roomH = parseFloat(cs.maxHeight) - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    if (roomW <= 0) return; // hidden tab: refit once the frame has a width
    const scale = Math.max(MIN_SCALE, Math.min(1, roomW / w, roomH / h));
    graph.classList.toggle("is-overflow", w * scale > roomW + .5 || h * scale > roomH + .5);
    canvas.style.transform = scale < 1 ? `scale(${scale})` : "";
    canvas.style.marginRight = `${w * (scale - 1)}px`;
    canvas.style.marginBottom = `${h * (scale - 1)}px`;
  }

  function renderSide() {
    const d = state.model.debugger;
    if (!d) return;
    const call = callOf(state.sel), phase = call ? null : phaseOf(state.selPhase);
    const detailCtx = {requests: state.requests, factsById: state.model.dsl?.factsById, model: state.model, managerPlan: planFor(d)};
    const key = call ? state.sel : phase ? "phase:" + phase.phase : null;
    const top = side.scrollTop;
    side.innerHTML = call ? callDetail(call, detailCtx) : phase ? phaseDetail(phase, detailCtx) : overviewHtml(d, state.phases);
    side.scrollTop = key != null && state.sideKey === key ? top : 0;
    state.sideKey = key;
  }

  // ---------- step lane ----------
  function block(role, f, tone) {
    const value = typeof f.value === "string" ? f.value : pretty(f.value);
    return `<div class="detail-block role-${role} tone-${tone}"><h4>${esc(f.label)}<span class="role-tag">${ROLE_LABEL[role]}</span></h4><div class="text-box" data-zoom>${esc(value)}</div></div>`;
  }

  function gapBlock(gaps) {
    if (!gaps?.length) return "";
    return `<div class="detail-block tone-gap"><h4>Parse gap<span class="role-tag">unknown, not an agent error</span></h4>
      ${gaps.map(a => `${(a.unresolved || []).map(u => `<blockquote>${esc(u.quote)}</blockquote><p class="muted">${esc(u.reason)}</p>`).join("")}
        <details><summary>${esc(a.kind)} · ${esc(a.source_id || "")}</summary><ul class="gap-reasons">${(a.reasons || []).map(r => `<li class="mono">${esc(r)}</li>`).join("")}</ul></details>`).join("")}
    </div>`;
  }

  function anchorBlock(anchors) {
    if (!anchors?.length) return "";
    return `<div class="detail-block tone-route"><h4>ROUTE anchor${anchors.length === 1 ? "" : "s"}</h4>
      ${anchors.map(c => `<p><span class="mono">${esc(c.candidate_id)}</span> · ${(c.goal_links || []).map(esc).join(", ")}</p><div class="text-box" data-zoom>${esc(c.hypothesis || "")}</div>`).join("")}</div>`;
  }

  function stepDetail(step, roles, idx) {
    const t = step.t;
    const facts = idx.facts.get(t) || [];
    const evals = idx.evals.get(t) || [];
    const bySide = {};
    facts.forEach(f => { (bySide[f.side || "other"] ||= []).push(f); });
    const counts = {};
    evals.forEach(e => { counts[e.state] = (counts[e.state] || 0) + 1; });
    const others = roles.other.map(f => Array.isArray(f.value)
      ? `<details class="detail-block"><summary>${esc(f.label)} · ${f.value.length}</summary><div class="chip-row">${f.value.map(v => `<span class="chip mono">${esc(typeof v === "string" ? v : JSON.stringify(v))}</span>`).join("")}</div></details>`
      : `<details class="detail-block"><summary>${esc(f.label)}${f.raw ? " · raw source" : ""}</summary><div class="text-box">${esc(typeof f.value === "string" ? f.value : pretty(f.value))}</div></details>`).join("");
    return `<div class="card-detail step-detail">
      <div class="detail-col">${roles.state.map(f => block("state", f, "teal")).join("") || `<div class="detail-block muted">No state field.</div>`}
        ${roles.action.map(f => block("action", f, "plain")).join("")}
        ${roles.outcome.length ? roles.outcome.map(f => block("outcome", {...f, label: `${f.label} → S${String(t + 1).padStart(2, "0")}`}, "teal-2")).join("") : `<div class="detail-block muted">No following state (last step).</div>`}</div>
      <div class="detail-col">${roles.reasoning.map(f => block("reasoning", f, "info")).join("") || `<div class="detail-block muted">No reasoning fields.</div>`}</div>
      <div class="detail-col">${(idx.candidates.get(t) || []).map(candidateCard).join("")}${anchorBlock(idx.anchors.get(t))}${gapBlock(idx.gaps.get(t))}
        <div class="detail-block tone-violet"><h4>Facts · ${facts.length}</h4>${Object.entries(bySide).map(([side, list]) => `<div class="fact-group"><span class="eyebrow">${esc(side)}</span><div class="chip-col">${list.map(f => factChip(f, `<span class="slot mono">${esc(f.slot)}</span>`)).join("")}</div></div>`).join("") || `<p class="muted">No facts at this step.</p>`}</div>
        <div class="detail-block tone-ok"><h4>Rule evaluations · ${evals.length}</h4>
          <div class="chip-row">${Object.entries(counts).map(([s, n]) => `<span class="badge ${EVAL_CLASS[s] || ""}">${esc(s)} ${n}</span>`).join("")}</div>
          ${evals.filter(e => e.state !== "satisfied").map(e => `<div class="eval-row"><span class="badge ${EVAL_CLASS[e.state] || ""}">${esc(e.state)}</span><span class="mono">${esc(e.rule)}</span><span class="muted">${esc(e.details?.reason || "")}</span>${(e.refs || []).map(fid => `<button class="mono link" data-fid="${fid}">f${fid}</button>`).join("")}</div>`).join("")}
        </div>
        ${others}
      </div>
    </div>`;
  }

  function stepCard(step, i, idx) {
    const roles = stepRoles(step, state.model.steps[i + 1]);
    const t = step.t ?? i + 1;
    const open = state.openStep === t;
    const evals = idx.evals.get(t) || [];
    const violated = evals.filter(e => e.state === "violated").length;
    const refs = idx.refs.get(t)?.length || 0;
    const label = actionLabel(roles) || "(no action)";
    const thought = roles.reasoning.at(-1)?.value;
    const cls = ["st-card", open && "is-open", state.selStep === t && "is-selected", violated && "has-violation"].filter(Boolean).join(" ");
    return `<article class="${cls}" data-step="${t}">
      ${i ? `<button class="step-arrow ${violated ? "is-warn" : ""}" data-arrow="${t}" aria-label="Rules at S${String(t).padStart(2, "0")}" aria-expanded="${state.arrowPin === t}">→</button>` : ""}
      <div class="card-meta">
        <div class="card-top"><span class="mono card-idx">S${String(t).padStart(2, "0")}</span></div>
        <span class="card-icon">${icon("step")}</span>
        <h3 class="card-title" title="${esc(label)}">${esc(short(label, open ? 120 : 34))}</h3>
        <p class="card-sub">${esc(short(typeof thought === "string" ? thought.split("\n").filter(Boolean).at(-1) : "", open ? 160 : 60))}</p>
        <div class="card-foot step-actions">${(idx.anchors.get(t) || []).map(c => stepButton(t, "anchor", `anchor ${c.candidate_id}`, "anchor", open, c.candidate_id, anchorTitle(c))).join("")}${idx.gaps.has(t) ? '<span class="badge gap" title="The parser could not account for part of the source text at this step">parse gap</span>' : ""}${violated ? stepButton(t, "violated", `⚠ ${violated} violated`, "warn", open, null, "See which automatic rule checks failed and the facts they cite") : ""}${stepButton(t, "facts", `${(idx.facts.get(t) || []).length} facts`, "", open, null, "See the structured records extracted from this step")}</div>
        ${state.model.debugger ? `<div class="heat" title="${refs} model calls touched this step"><span style="width:${Math.round(100 * refs / idx.maxRefs)}%"></span></div>` : ""}
      </div>
      ${open ? stepSectionDetail(step, roles, idx, t) : ""}
    </article>`;
  }

  function stepButton(t, section, label, tone, open, anchor = null, hint = "") {
    const active = open && state.stepSection === section && (section !== "anchor" || state.stepAnchor === anchor);
    return `<button type="button" class="badge step-info-button ${tone}${active ? " is-active" : ""}" data-step-section="${section}" data-step-target="${t}"${anchor == null ? "" : ` data-anchor-id="${esc(anchor)}"`} aria-expanded="${active}"${open ? ` aria-controls="step-info-${t}"` : ""} title="${esc(hint)}">${esc(label)}<span aria-hidden="true">${active ? "⌄" : "›"}</span></button>`;
  }

  function stepSectionDetail(step, roles, idx, t) {
    const section = state.stepSection;
    const factCount = (idx.facts.get(t) || []).length;
    const titles = {anchor: "Why check this step?", violated: "Rule checks", facts: `${factCount} fact${factCount === 1 ? "" : "s"} extracted from this step`};
    const failed = (idx.evals.get(t) || []).filter(e => e.state === "violated");
    let body = "";
    if (section === "anchor") {
      const anchors = (idx.anchors.get(t) || []).filter(c => state.stepAnchor == null || c.candidate_id === state.stepAnchor);
      body = anchors.map(c => anchorRows(c, roles)).join("") || '<p class="muted">No anchor is recorded for this step.</p>';
    } else if (section === "violated") {
      const names = {"assertion.consistency": "Claims match observations", "assertion.grounding": "Claims have supporting evidence", "information.update": "Remembered information is up to date", "decision.applicability": "Action meets execution constraints", "execution.result": "Action and feedback are consistent", "completion.consistency": "Completion claim matches task requirements", "progress.efficiency": "Action makes observable progress"};
      const reasons = {explicit_contract_mismatch: "Execution constraint mismatch", explicit_no_effect_not_responsibility_judgment: "Feedback reports no effect"};
      const refs = [...new Set(failed.flatMap(e => e.refs || []))];
      body = failed.length ? `<div class="step-rule-block"><table class="step-rule-table"><thead><tr><th scope="col">Check</th><th scope="col">Result</th><th scope="col">Recorded reason</th></tr></thead><tbody>${failed.map(e => {
        const reason = e.details?.reason;
        const reasonText = typeof reason === "string" ? reasons[reason] || reason.replace(/_/g, " ") : "No reason was recorded.";
        return `<tr><td><strong>${esc(names[String(e.rule).replace(/\/v\d+$/, "")] || e.rule)}</strong><code class="rule-code">${esc(e.rule)}</code></td><td><span class="step-rule-failed">Failed</span></td><td>${esc(reasonText)}${typeof reason === "string" ? `<code class="rule-code">${esc(reason)}</code>` : ""}${failed.length > 1 && e.refs?.length ? `<span class="rule-row-refs">Facts: ${e.refs.map(factLink).join(" ")}</span>` : ""}</td></tr>`;
      }).join("")}</tbody></table><div class="step-rule-footer"><span>Action: <code>${esc(actionLabel(roles) || "No action recorded")}</code></span><span>Referenced facts: ${refs.map(factLink).join(" ") || "None recorded"}</span></div></div>` : '<p class="muted">No failed checks are recorded for this step.</p>';
    } else {
      const modules = {mem: "Memory", refl: "Reflection", plan: "Plan", act: "Action", chk: "Execution check", obs: "Observation"};
      body = (idx.facts.get(t) || []).map(f => `<details class="step-fact-row"><summary><span class="step-fact-id mono">f${esc(f.fid)}</span><span class="step-fact-name">${esc(f.rel || "Unspecified relation")}</span><span class="step-fact-module">${esc(modules[f.slot] || f.slot || "Unspecified module")}</span></summary><div class="step-fact-source"><pre>${esc(f.span || "No source quotation recorded.")}</pre><button type="button" class="step-evidence-link" data-fid="${esc(f.fid)}">View linked fact in DSL Graph ↗</button></div></details>`).join("") || '<p class="muted">No extracted facts are recorded for this step.</p>';
    }
    const anchorId = section === "anchor" ? `Anchor ${state.stepAnchor || ""} · Step ${t}` : section === "facts" ? `S${String(t).padStart(2, "0")} · Compiled records` : `Step ${t}`;
    return `<section class="step-info-panel is-${section}" id="step-info-${t}" aria-label="${esc(titles[section])}"><header><div><div class="step-panel-eyebrow">${esc(anchorId)}</div><h3>${esc(titles[section])}${section === "violated" ? `<span class="step-check-count">${failed.length} check${failed.length === 1 ? "" : "s"} failed</span>` : ""}</h3></div><button type="button" class="btn" data-close-step="${t}">Close</button></header>${section === "facts" ? '<p class="step-info-explanation">A fact is a structured record extracted from the log. It can describe an observation or an agent claim; extraction does not make the claim true. Open a row to read its source.</p>' : ""}<div class="step-info-records">${body}</div></section>`;
  }

  function factLink(fid) {
    const fact = state.model.dsl?.factsById.get(fid);
    return `<button type="button" class="step-evidence-link mono" data-fid="${esc(fid)}" title="${esc(fact?.span || "Open the original fact")}">f${esc(fid)}</button>`;
  }

  function emphasize(text, phrases = []) {
    // Split plain text first so emphasis can never turn source text into HTML.
    let pieces = [{text: String(text), strong: false}];
    for (const phrase of phrases.filter(Boolean)) {
      pieces = pieces.flatMap(part => part.strong ? [part] : part.text.split(phrase).flatMap((s, i) => [...(i ? [{text: phrase, strong: true}] : []), {text: s, strong: false}]));
    }
    return pieces.map(p => p.strong ? `<strong>${esc(p.text)}</strong>` : esc(p.text)).join("");
  }

  function sourceDisclosure(label, fields) {
    if (!fields.length) return "";
    return `<details class="anchor-source"><summary>${esc(label)}</summary>${fields.map(f => `<pre>${esc(typeof f.value === "string" ? f.value : pretty(f.value))}</pre>`).join("")}</details>`;
  }

  function anchorRows(anchor, roles) {
    const copy = anchorCopy(anchor, roles);
    const command = roles.action.map(f => typeof f.value === "string" ? f.value : pretty(f.value)).join("\n");
    const response = roles.outcome.map(f => typeof f.value === "string" ? f.value : pretty(f.value)).join("\n");
    const noEffect = /^Nothing happens\.?$/i.test(response.trim());
    const quoted = command.match(/^(['"])([\s\S]*)\1$/);
    const commandHtml = quoted ? `<span class="anchor-quote">${esc(quoted[1])}</span>${esc(quoted[2])}<span class="anchor-quote">${esc(quoted[1])}</span>` : esc(command);
    const planFields = roles.reasoning.filter(f => /plan|intent/i.test(f.key));
    return `<ol class="step-anchor-summary" data-copy-origin="${copy.reviewed ? "reviewed-hypothesis" : "source-record"}">
      <li><span class="anchor-number">1</span><div><h4>What it wanted to do</h4><p>${copy.intent ? emphasize(short(copy.intent, 300), copy.intentEmphasis) : "No intended next action is recorded for this step."}</p>${copy.intentIsQuote ? sourceDisclosure("View full plan", planFields) : ""}</div></li>
      <li><span class="anchor-number">2</span><div><h4>${noEffect ? "What happened instead" : "What happened"}</h4><p class="anchor-action-response">${command ? `<span>It sent <mark class="anchor-command"><code>${commandHtml}</code></mark></span>` : '<span class="muted">No action recorded.</span>'}<span class="anchor-result-arrow" aria-hidden="true">→</span>${response ? `<mark class="anchor-response${noEffect ? " has-no-effect" : ""}"><code>${esc(response)}</code></mark>` : '<span class="muted">No following feedback was recorded.</span>'}</p>${noEffect ? '<p class="anchor-outcome-meaning">The environment reported no visible effect.</p>' : ""}<div class="anchor-source-links">${sourceDisclosure("View command", roles.action)}${sourceDisclosure("View response", roles.outcome)}</div></div></li>
      <li><span class="anchor-number">3</span><div><h4>Why it is worth checking</h4>${copy.question ? `<p>${emphasize(copy.question, copy.emphasis)}</p>` : `<p>No short investigation question is recorded for this lead.</p>${copy.hypothesis ? sourceDisclosure("View the recorded investigation note", [{value: copy.hypothesis}]) : ""}`}</div></li>
    </ol>`;
  }

  function openStepSection(t, section, anchor = null) {
    state.openStep = state.openStep === t && state.stepSection === section && state.stepAnchor === anchor ? null : t;
    state.stepSection = section; state.stepAnchor = anchor; state.selStep = t;
    render();
    const card = steps.querySelector(`[data-step="${t}"]`);
    scrollToCard(steps, card);
    [...card.querySelectorAll("[data-step-section]")].find(b => b.dataset.stepSection === section && (b.dataset.anchorId || null) === anchor)?.focus({preventScroll: true});
  }

  function renderSteps() {
    const idx = state.idx;
    const list = state.model.steps;
    $("tj-step-track").innerHTML = list.map((s, i) => stepCard(s, i, idx)).join("") || `<p class="empty lane-empty">No steps recorded.</p>`;
    $("tj-step-count").textContent = `${list.length} steps`;
    const rootStep = state.model.debugger?.result?.root?.step;
    $("tj-step-overview").innerHTML = list.map((s, i) => {
      const t = s.t ?? i + 1;
      const isRoot = rootStep != null && Number(rootStep) === Number(t);
      const label = `S${String(t).padStart(2, "0")}${isRoot ? " · Root cause (selected diagnosis)" : ""}`;
      return `<button class="tick step-tick${isRoot ? " is-root" : ""}" data-jump-step="${t}" title="${esc(label)}" aria-label="${esc(label)}"></button>`;
    }).join("");
  }

  // ---------- arrow popover ----------
  // The arrow into S(t) summarises the rules S(t) violated and the facts they cite. Hover
  // previews it; a click pins it until the same arrow is clicked again.
  const pop = $("tj-arrow-pop");
  const MAX_REFS = 3;

  function arrowHtml(t, pinned) {
    const evals = state.idx.evals.get(t) || [];
    const counts = {};
    evals.forEach(e => { counts[e.state] = (counts[e.state] || 0) + 1; });
    const violated = evals.filter(e => e.state === "violated");
    const fact = fid => state.model.dsl?.factsById.get(fid);
    const rows = violated.map(e => {
      const refs = (e.refs || []).map(fact).filter(Boolean);
      return `<li><div class="pop-rule"><span class="mono">${esc(e.rule)}</span>${e.details?.reason ? `<span class="muted">${esc(e.details.reason)}</span>` : ""}</div>
        ${refs.length ? `<div class="chip-col">${refs.slice(0, MAX_REFS).map(f => pinned ? factChip(f) : `<span class="fact-chip side-${esc(f.side)}"><span class="mono fid">f${f.fid}</span><span class="fact-text">${esc(short(factText(f), 70))}</span></span>`).join("")}${refs.length > MAX_REFS ? `<span class="muted">+${refs.length - MAX_REFS} more</span>` : ""}</div>` : `<p class="muted">No cited facts.</p>`}</li>`;
    }).join("");
    return `<div class="pop-head"><span class="mono">→ S${String(t).padStart(2, "0")}</span>
        <span class="chip-row">${Object.entries(counts).map(([s, n]) => `<span class="badge ${EVAL_CLASS[s] || ""}">${esc(s)} ${n}</span>`).join("") || `<span class="muted">no rule evaluations</span>`}</span></div>
      ${violated.length ? `<ol class="pop-rules">${rows}</ol>` : `<p class="muted">No violated rules at this step.</p>`}
      <p class="pop-foot">${pinned ? "Pinned · click the arrow again to close" : "Click the arrow to pin"}</p>`;
  }

  function placePop() {
    const t = state.arrowHover ?? state.arrowPin;
    const arrow = t == null ? null : steps.querySelector(`[data-arrow="${t}"]`);
    root.querySelectorAll(".step-arrow").forEach(el => {
      const at = Number(el.dataset.arrow);
      el.classList.toggle("is-pinned", at === state.arrowPin);
      el.setAttribute("aria-expanded", at === state.arrowPin);
    });
    if (!arrow) { pop.hidden = true; pop.dataset.key = ""; return; }
    const pinned = t === state.arrowPin;
    const key = `${t}:${pinned}`;
    if (pop.dataset.key !== key) { pop.innerHTML = arrowHtml(t, pinned); pop.dataset.key = key; }
    pop.classList.toggle("is-pinned", pinned);
    pop.hidden = false;
    const a = arrow.getBoundingClientRect(), s = steps.getBoundingClientRect();
    // An arrow scrolled out of the lane hides its popover rather than float over the graph.
    if (a.right < s.left || a.left > s.right) { pop.hidden = true; return; }
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const left = Math.max(8, Math.min(innerWidth - w - 8, a.left + a.width / 2 - w / 2));
    const below = a.bottom + 8;
    pop.style.left = `${left}px`;
    pop.style.top = `${below + h > innerHeight - 8 ? Math.max(8, a.top - h - 8) : below}px`;
  }

  function hoverArrow(t) {
    if (t !== state.arrowMuted) state.arrowMuted = null;
    if (t === state.arrowMuted) t = null;
    if (t === state.arrowHover) return;
    state.arrowHover = t;
    placePop();
  }

  function clickArrow(t) {
    if (state.arrowPin === t) { state.arrowPin = null; state.arrowHover = null; state.arrowMuted = t; }
    else { state.arrowPin = t; state.arrowHover = null; }
    placePop();
  }

  // ---------- links between graph and steps ----------
  function cardRect(el, scroller, base) {
    const r = el.getBoundingClientRect(), s = scroller.getBoundingClientRect();
    const cx = Math.min(Math.max(r.left + r.width / 2, s.left + 8), s.right - 8);
    const off = r.right < s.left || r.left > s.right || r.bottom < s.top || r.top > s.bottom;
    // A call scrolled out of its card's body links from the body's edge.
    const clip = el.closest(".ph-body")?.getBoundingClientRect();
    const bottom = clip ? Math.min(Math.max(r.bottom, clip.top), clip.bottom) : r.bottom;
    return {x: cx - base.left, top: r.top - base.top, bottom: Math.min(bottom, s.bottom) - base.top, off};
  }

  // A selected call links to each step it concluded something about, the curve weighted and
  // coloured by its strongest verdict and tagged with a badge where it lands; the steps it only
  // read fold into one bracket per run of neighbours. Hovering a curve, badge or bracket opens
  // what the call found there (linkPopHtml).
  const findingsOf = c => callFindings(c, state.model.dsl?.factsById);
  const curve = (a, y1, b, y2) => { const mid = (y1 + y2) / 2; return `M${a},${y1} C${a},${mid} ${b},${mid} ${b},${y2}`; };
  const BADGE_CH = 6.6;
  let badges = [];

  function linkHtml(c, t, a, b, verdict, cls, badge) {
    const key = `${c.index}:${t}`, d = curve(a.x, a.bottom, b.x, b.top);
    const list = findingsOf(c).get(t) || [];
    const text = badge && list.length ? `${list[0].subject} ${list[0].word}${list.length > 1 ? ` +${list.length - 1}` : ""}` : "";
    const w = text.length * BADGE_CH + 12;
    // Badges sit in the gap above the agent lane, clear of its labels and step ticks.
    const by = Math.min(b.top - 27, root.querySelector(".lane-steps").getBoundingClientRect().top - lanes.getBoundingClientRect().top - 24);
    const out = `<path class="link v-${verdict} phase-${c.phase} ${cls}" d="${d}"/><circle class="link-end v-${verdict} phase-${c.phase}" cx="${b.x}" cy="${b.top}" r="3.5"/>
      <path class="link-hit" data-link="${key}" d="${d}"/>
`;
    // Badges go on top of every curve's hit area, so they are drawn last (drawLinks).
    if (text) badges.push(`<g class="link-badge v-${verdict} phase-${c.phase}" data-link="${key}"><rect x="${b.x - w / 2}" y="${by}" width="${w}" height="18" rx="4"/><text x="${b.x}" y="${by + 12.5}" text-anchor="middle">${esc(text)}</text></g>`);
    return out;
  }

  // Consecutive read-only steps (in lane order) share one bracket over their cards.
  function bracketHtml(c, run, a, base, cls) {
    const s = steps.getBoundingClientRect();
    const first = steps.querySelector(`[data-step="${run[0]}"]`)?.getBoundingClientRect(), last = steps.querySelector(`[data-step="${run.at(-1)}"]`)?.getBoundingClientRect();
    if (!first || !last) return "";
    // A run scrolled out of the lane keeps a faint curve to the edge it lies beyond.
    if (last.right < s.left || first.left > s.right) {
      const x = (last.right < s.left ? s.left + 8 : s.right - 8) - base.left;
      return `<path class="link v-read phase-${c.phase} is-off ${cls}" d="${curve(a.x, a.bottom, x, first.top - base.top)}"/>`;
    }
    const x1 = Math.max(first.left + 10, s.left + 4) - base.left, x2 = Math.min(last.right - 10, s.right - 4) - base.left;
    if (x2 <= x1) return "";
    const y = Math.min(first.top, root.querySelector(".lane-steps").getBoundingClientRect().top) - base.top - 8, cx = (x1 + x2) / 2, key = `read:${c.index}:${run[0]}:${run.at(-1)}`;
    const label = run.length > 1 ? `read S${String(run[0]).padStart(2, "0")}–S${String(run.at(-1)).padStart(2, "0")}` : `read S${String(run[0]).padStart(2, "0")}`;
    const d = curve(a.x, a.bottom, cx, y);
    return `<path class="link v-read phase-${c.phase} ${cls}" d="${d}"/><path class="link-hit" data-link="${key}" d="${d}"/>
      <g class="link-bracket phase-${c.phase}" data-link="${key}"><path d="M${x1},${y + 6} V${y} H${x2} V${y + 6}"/><rect class="link-bracket-hit" x="${x1}" y="${y - 14}" width="${x2 - x1}" height="20"/>
        <text x="${cx}" y="${y - 4}" text-anchor="middle">${label}</text></g>`;
  }

  function drawLinks() {
    const svg = $("tj-links");
    const base = lanes.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${base.width} ${base.height}`);
    root.querySelectorAll(".is-linked").forEach(el => el.classList.remove("is-linked"));
    const d = state.model?.debugger;
    if (!d) { svg.innerHTML = ""; return; }
    const current = state.sel ?? (state.selPhase ? "phase:" + state.selPhase : null);
    const key = state.hover ?? (state.hoverStep == null ? current : null);
    const stepT = key == null ? (state.hoverStep ?? state.selStep) : null;
    if (key?.startsWith("phase:")) {
      // A whole phase touches too many steps for curves: light up its card and the steps only.
      cardOf(key.slice(6))?.classList.add("is-linked");
      new Set(d.calls.filter(c => c.phase === key.slice(6)).flatMap(c => c.steps)).forEach(t => steps.querySelector(`[data-step="${t}"]`)?.classList.add("is-linked"));
      svg.innerHTML = "";
      return;
    }
    const cls = state.hover != null || state.hoverStep != null ? "is-preview" : "";
    // A call in a collapsed phase links from its phase card.
    const anchor = c => {
      const node = graph.querySelector(`.pnode[data-call="${c.index}"]`) || cardOf(c.phase);
      if (!node) return null;
      node.classList.add("is-linked");
      const a = cardRect(node, graph, base);
      return a.off ? null : a; // a node scrolled out of view would draw across the side panel
    };
    const stepAt = t => {
      const st = steps.querySelector(`[data-step="${t}"]`);
      if (!st) return null;
      st.classList.add("is-linked");
      return cardRect(st, steps, base);
    };
    let html = "";
    badges = [];
    const call = key != null ? callOf(key) : null;
    if (call) {
      const a = anchor(call);
      if (a) {
        const found = findingsOf(call);
        const touched = new Set([...call.steps, ...found.keys()]);
        const lane = state.model.steps.map((s, i) => s.t ?? i + 1);
        const runs = [];
        lane.forEach((t, i) => {
          if (!touched.has(t)) return;
          if (found.has(t)) {
            const b = stepAt(t);
            if (b) html += linkHtml(call, t, a, b, worstVerdict(found.get(t)), `${cls} ${b.off ? "is-off" : ""}`, !b.off);
            return;
          }
          stepAt(t);
          if (runs.length && runs.at(-1).at(-1) === lane[i - 1]) runs.at(-1).push(t);
          else runs.push([t]);
        });
        html += runs.map(run => bracketHtml(call, run, a, base, cls)).join("");
      }
    } else if (stepT != null) {
      // Every call that touched one step: one curve per node, weighted by what it found there. A
      // collapsed card's curve stands for the call in it that drew the strongest verdict.
      const b = stepAt(stepT);
      const best = new Map();
      if (b) (state.idx.refs.get(stepT) || []).forEach(i => {
        const c = d.calls[i], id = graph.querySelector(`.pnode[data-call="${c.index}"]`) ? c.index : c.phase;
        const verdict = worstVerdict(findingsOf(c).get(stepT)), had = best.get(id);
        if (!had ? best.size < 80 : worstVerdict([{verdict}, {verdict: had.verdict}]) !== had.verdict) best.set(id, {c, verdict});
      });
      best.forEach(({c, verdict}) => {
        const a = anchor(c);
        if (a) html += linkHtml(c, stepT, a, b, verdict, `${cls} ${b.off ? "is-off" : ""}`, false);
      });
    }
    svg.innerHTML = html + badges.join("");
  }

  // ---------- link popover ----------
  const linkPop = $("tj-link-pop");
  const VERDICT_CLASS = {viol: "bad", unk: "warn", sat: "ok", cand: "anchor", read: ""};

  // Hover previews a link's findings; a click pins the popover so its folded facts can open.
  function linkPopHtml(key, pinned) {
    const read = key.startsWith("read:");
    const [ci, lo, hi] = (read ? key.slice(5) : key).split(":").map(Number);
    const c = state.model?.debugger?.calls[ci];
    if (!c) return "";
    const tools = [...new Set(c.tools.map(t => t.name).filter(Boolean))].join(" · ") || "no tools";
    const step = n => `S${String(n).padStart(2, "0")}`;
    const label = read && lo !== hi ? `${step(lo)}–${step(hi)}` : step(lo);
    const list = read ? [] : findingsOf(c).get(lo) || [];
    const head = `<div class="pop-head"><span class="mono">${esc(c.phase)} #${c.n} · ${esc(tools)}</span><span class="mono">→ ${label}</span></div>
      <p class="pop-insight">${esc(insightFor(list, label))}</p>`;
    if (!list.length) return head;
    const facts = [...new Set(list.flatMap(f => f.cites))].map(fid => state.model.dsl?.factsById.get(fid)).filter(Boolean);
    const rows = list.length > 1 ? `<ol class="pop-rules">${list.map(f => `<li><div class="pop-rule"><span class="badge ${VERDICT_CLASS[f.verdict]}">${esc(f.word)}</span><span class="mono">${esc(f.subject || "")}</span>
        ${f.reason ? `<span class="muted">${esc(short(plainReason(f.reason), 140))}</span>` : ""}</div></li>`).join("")}</ol>` : "";
    const factList = facts.length ? `<details class="pop-facts"><summary>${facts.length} fact${facts.length === 1 ? "" : "s"}</summary>
        <div class="chip-col">${facts.map(f => pinned ? factChip(f) : `<span class="fact-chip side-${esc(f.side)}"><span class="mono fid">f${f.fid}</span><span class="fact-text">${esc(short(factText(f), 70))}</span></span>`).join("")}</div></details>` : "";
    return `${head}${rows}${factList}<p class="pop-foot">${pinned ? "Pinned · click the link again or press Esc to close" : "Click to pin and open the facts"}</p>`;
  }

  function placeLinkPop(key, x, y) {
    if (!key) { linkPop.hidden = true; linkPop.dataset.key = ""; linkPop.classList.remove("is-pinned"); return; }
    const pinned = key === state.linkPin, id = `${key}:${pinned}`;
    if (linkPop.dataset.key !== id) { linkPop.innerHTML = linkPopHtml(key, pinned); linkPop.dataset.key = id; }
    linkPop.classList.toggle("is-pinned", pinned);
    linkPop.hidden = !linkPop.innerHTML;
    if (x == null) return;
    const w = linkPop.offsetWidth, h = linkPop.offsetHeight;
    linkPop.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, x + 14))}px`;
    linkPop.style.top = `${y + 16 + h > innerHeight - 8 ? Math.max(8, y - h - 12) : y + 16}px`;
  }
  const unpinLink = () => { state.linkPin = null; placeLinkPop(null); };
  root.addEventListener("pointermove", event => {
    if (state.linkPin) return;
    const link = event.target.closest?.("[data-link]");
    placeLinkPop(link?.dataset.link, event.clientX, event.clientY);
  });
  // Capture phase: a pinned popover closes on any click outside it before other handlers run.
  root.addEventListener("click", event => {
    const link = event.target.closest?.("[data-link]")?.dataset.link;
    if (link) {
      event.stopPropagation();
      state.linkPin = state.linkPin === link ? null : link;
      placeLinkPop(state.linkPin ? link : null, event.clientX, event.clientY);
      return;
    }
    if (state.linkPin && !event.target.closest(".link-pop")) unpinLink();
  }, true);

  let raf = 0;
  const scheduleLinks = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { drawLinks(); placePop(); }); };

  // ---------- render & events ----------
  function render() {
    if (!state.model) return;
    const keep = steps.scrollLeft;
    renderGraph(); renderSide(); renderSteps();
    steps.scrollLeft = keep;
    scheduleLinks();
  }

  function scrollToCard(scroller, el, smooth = true) {
    if (!el) return;
    const s = scroller.getBoundingClientRect(), r = el.getBoundingClientRect();
    const target = scroller.scrollLeft + r.left - s.left - (el.classList.contains("is-open") ? 16 : (s.width - r.width) / 2);
    scroller.scrollTo({left: target, behavior: smooth ? "smooth" : "auto"});
  }

  // reveal: expand the call's phase so its node is visible.
  function select(key, reveal = false) {
    state.sel = key;
    const call = callOf(key), was = new Set(state.openPhases);
    if (call) state.selPhase = call.phase;
    const opens = call && reveal && expandable(phaseOf(call.phase)) && !state.openPhases.has(call.phase);
    if (opens) state.openPhases.add(call.phase);
    const update = () => { renderGraph(); renderSide(); revealNode(key); if (call) revealCard(call.phase); scheduleLinks(); };
    opens ? morph(update, was) : update();
  }

  // open: true/false expands or collapses the phase; null leaves it as it is.
  function selectPhase(name, open = null) {
    state.sel = null; state.selPhase = name;
    const was = new Set(state.openPhases);
    if (open === true && expandable(phaseOf(name))) state.openPhases.add(name);
    if (open === false) state.openPhases.delete(name);
    const update = () => { renderGraph(); renderSide(); revealCard(name); scheduleLinks(); };
    was.has(name) !== state.openPhases.has(name) ? morph(update, was) : update();
  }

  function selectStep(t, scroll = true) {
    state.selStep = state.selStep === t && !scroll ? null : t;
    root.querySelectorAll(".st-card").forEach(el => el.classList.toggle("is-selected", Number(el.dataset.step) === state.selStep));
    if (scroll) scrollToCard(steps, steps.querySelector(`[data-step="${t}"]`));
    scheduleLinks();
  }

  root.addEventListener("click", async event => {
    const el = event.target;
    if (el.closest("#tj-side-toggle")) { setSideOpen(side.hidden); return; }
    const info = el.closest("[data-step-section]");
    if (info) { openStepSection(Number(info.dataset.stepTarget), info.dataset.stepSection, info.dataset.anchorId || null); return; }
    const close = el.closest("[data-close-step]");
    if (close) { openStepSection(Number(close.dataset.closeStep), state.stepSection, state.stepAnchor); return; }
    const arrow = el.closest("[data-arrow]")?.dataset.arrow;
    if (arrow != null) { clickArrow(Number(arrow)); return; }
    const fid = el.closest("[data-fid]")?.dataset.fid;
    if (fid != null) { ctx.navigate("graph", "facts", fid); return; }
    const req = el.closest("[data-request]")?.dataset.request;
    if (req) {
      state.requests.set(req, "loading");
      const paint = () => root.querySelectorAll(`[data-request-box="${CSS.escape(req)}"]`).forEach(box => { box.innerHTML = requestHtml(req, state.requests); });
      paint();
      try { state.requests.set(req, await getJSON(ctx.ref, req)); } catch (error) { state.requests.set(req, {error: error.message}); }
      paint();
      return;
    }
    const gotoCall = el.closest("[data-goto-call]")?.dataset.gotoCall;
    if (gotoCall != null) { select(gotoCall, true); return; }
    const gotoPhase = el.closest("[data-goto-phase]")?.dataset.gotoPhase;
    if (gotoPhase) { selectPhase(gotoPhase); return; }
    const goto = el.closest("[data-goto-step]")?.dataset.gotoStep;
    if (goto) { selectStep(Number(goto)); return; }
    const zoom = el.closest("[data-zoom]");
    if (zoom) { zoom.classList.toggle("is-full"); scheduleLinks(); return; }
    const node = el.closest(".pnode");
    if (node) { select(state.sel === node.dataset.call ? null : node.dataset.call); return; }
    const more = el.closest("[data-more]")?.dataset.more;
    if (more) { const body = graph.querySelector(`[data-body="${more}"]`); body?.scrollBy({top: body.clientHeight - 40, behavior: "smooth"}); return; }
    const toggle = el.closest("[data-toggle-phase]")?.dataset.togglePhase;
    if (toggle) { selectPhase(toggle, !state.openPhases.has(toggle)); return; }
    const card = cardAt(el);
    if (card) { selectPhase(card.dataset.phase, true); return; }
    const b = el.closest("button");
    if (b?.dataset.pan) { $(b.dataset.pan).scrollBy({left: Number(b.dataset.dir) * steps.clientWidth * .8, behavior: "smooth"}); return; }
    if (b?.dataset.jumpStep) { selectStep(Number(b.dataset.jumpStep)); return; }
    if (el.closest(".card-detail, .step-info-panel") || el.closest(".proc-side")) return;
    const st = el.closest("[data-step]");
    if (st) selectStep(Number(st.dataset.step), false);
  });

  root.addEventListener("pointerover", event => {
    // Keep a previewed popover while the pointer moves onto it.
    const arrow = event.target.closest("[data-arrow]");
    if (!event.target.closest(".arrow-pop")) hoverArrow(arrow ? Number(arrow.dataset.arrow) : null);
    const node = event.target.closest(".pnode"), card = cardAt(event.target), st = arrow ? null : event.target.closest(".st-card");
    const hn = node ? node.dataset.call : card ? "phase:" + card.dataset.phase : null;
    const hs = st && !st.classList.contains("is-open") ? Number(st.dataset.step) : null;
    if (hn !== state.hover || hs !== state.hoverStep) { state.hover = hn; state.hoverStep = hs; scheduleLinks(); }
  });
  lanes.addEventListener("pointerleave", () => { state.hover = state.hoverStep = null; scheduleLinks(); });
  root.addEventListener("pointerleave", () => { hoverArrow(null); if (!state.linkPin) placeLinkPop(null); });

  root.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      if (state.linkPin) { unpinLink(); return; }
      state.arrowPin = state.arrowHover = null; state.openStep = null; state.sel = state.selPhase = null;
      const was = new Set(state.openPhases);
      state.openPhases.clear();
      was.size ? morph(render, was) : render();
      return;
    }
    if (event.target.closest("button, summary, a, input, textarea, select, .step-info-panel")) return;
    const lane = event.target.closest(".proc-view, .lane-scroll");
    const keys = lane === graph ? ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter"] : ["ArrowLeft", "ArrowRight", "Enter"];
    if (!lane || !keys.includes(event.key)) return;
    event.preventDefault();
    if (lane === graph) {
      // Left/right move between phase cards, Enter opens or closes one, up/down walk the calls.
      const names = state.phases.map(p => p.phase);
      const current = state.selPhase ?? callOf(state.sel)?.phase;
      if (event.key === "Enter") {
        if (current) selectPhase(current, !state.openPhases.has(current));
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        const pos = names.indexOf(current);
        if (names.length) selectPhase(names[pos < 0 ? 0 : Math.max(0, Math.min(names.length - 1, pos + (event.key === "ArrowLeft" ? -1 : 1)))]);
        return;
      }
      const calls = state.model.debugger?.calls || [];
      if (!calls.length) return;
      const dir = event.key === "ArrowUp" ? -1 : 1, phase = phaseOf(current);
      const pos = state.sel != null ? Number(state.sel)
        : phase?.calls.length ? (dir > 0 ? phase.calls[0].index - 1 : phase.calls.at(-1).index + 1) : -1;
      select(String(Math.max(0, Math.min(calls.length - 1, pos + dir))), true);
    } else {
      const ts = state.model.steps.map((s, i) => s.t ?? i + 1);
      const pos = ts.indexOf(state.selStep);
      if (event.key === "Enter") { if (state.selStep != null) openStepSection(state.selStep, "facts"); return; }
      selectStep(ts[Math.max(0, Math.min(ts.length - 1, pos + (event.key === "ArrowRight" ? 1 : -1)))]);
    }
  });

  steps.addEventListener("scroll", scheduleLinks, {passive: true});
  // Scroll does not bubble: catch the card bodies' scrolling on the way down.
  graph.addEventListener("scroll", event => {
    if (event.target.classList?.contains("ph-body")) { syncBody(event.target); drawHandoffs(); }
    scheduleLinks();
  }, {capture: true, passive: true});
  window.addEventListener("scroll", scheduleLinks, {passive: true});
  const resize = new ResizeObserver(scheduleLinks);
  resize.observe(lanes);
  // Refit when the frame changes width (window resize, side panel wrapping below).
  let fitWidth = 0;
  const refit = new ResizeObserver(() => {
    if (graph.clientWidth === fitWidth) return;
    fitWidth = graph.clientWidth;
    fitGraph(); scheduleLinks();
  });
  refit.observe(graph);
  window.addEventListener("resize", fitGraph);

  return {
    update(model, {changed}) {
      state.model = model;
      state.idx = stepIndex(model);
      if (!["all", "usage_rows", "tool_calls", "claim_usage", "parse_audit_usage", "canonical_record", "input", "record", "steps",
            "evaluations", "result", "route_candidates", "manager_plan", "trace_queries", "parse_audit"].some(k => changed.has(k))) return;
      render();
    },
    show(route) {
      if (route.extra[0] === "step" && route.extra[1]) { state.openStep = Number(route.extra[1]); state.selStep = state.openStep; render(); selectStep(state.selStep); }
      if (route.extra[0] === "call" && route.extra[1]) select(String(Number(route.extra[1]) - 1));
      scheduleLinks();
    },
    destroy() { resize.disconnect(); refit.disconnect(); cancelAnimationFrame(raf); window.removeEventListener("scroll", scheduleLinks); window.removeEventListener("resize", fitGraph); },
  };
}
