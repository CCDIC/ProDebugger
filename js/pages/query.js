// #/query[/<group>/<trajectory>]: the Query Graph. The DSL record is one dense core; every
// debugger access (a ROUTE view, a window, a TRACE lane's SQL) is a point cloud on a tier
// above it, ending in the root. Click a cloud to open it by entity, an entity to read its facts.
import {esc, short, loader} from "../util.js?v=27bf1baa85";
import {caseHref, queryHref} from "../routes.js?v=27bf1baa85";
import {loadQueryScene, factRepr, SLOT_LABEL} from "../query/model.js?v=ad59c1cadf";
import {agentWords} from "../query/narrate.js?v=ad59c1cadf";
import {buildClusters, chainPath, factLabel, journeyOf, phaseFocus, roleFacts} from "../query/clusters.js?v=ad59c1cadf";
import {layoutClusters, TIERS} from "../query/layout.js?v=ad59c1cadf";
import {createStage, clusterTitle} from "../query/stage.js?v=ad59c1cadf";
import {createTour, eventDuration} from "../query/tour.js?v=ad59c1cadf";
import {buildStory} from "../query/story.js?v=ad59c1cadf";
import {coachSeen, runCoach} from "../query/coach.js?v=ad59c1cadf";
import {createDetails} from "../query/details.js?v=ad59c1cadf";
import {mountOverviewNarrative} from "../query/overview.js?v=overview-narrative1";

const GROUP = "evidence-query-claims-n30-20261013";
// Cases the page is tuned on (one window/lane, two windows/lanes, a large WebShop record), then
// the other examples the project site carries; every one of them runs the MANAGER phase.
const CASES = [
  {ref: [GROUP, "GPT-4o_013_alfworld_task_013"], label: "ALFWorld 013 · 1 lane"},
  {ref: [GROUP, "GPT-4o_024_alfworld_task_024"], label: "ALFWorld 024 · 2 lanes"},
  {ref: [GROUP, "GPT-4o_003_chat_b000_t00_e02-1e119842"], label: "WebShop 003 · 3 candidates"},
  {ref: [GROUP, "GPT-4o_019_alfworld_task_019"], label: "ALFWorld 019 · 2 lanes"},
  {ref: [GROUP, "Qwen3-8B_017_chat_b000_t00_e17-fbe4c42d"], label: "WebShop Qwen3-8B 017 · 2 windows"},
  {ref: [GROUP, "Llama3.3-70B-Turbo_017_webshop_task_017"], label: "WebShop Llama 017 · 921 facts"},
];
export const DEFAULT_CASE = CASES[0].ref;
const PHASES = ["PARSE", "MAP", "ROUTE", "MANAGER", "TRACE", "SUBMIT"];
// Overview camera presets: the whole tower from the front, from above, from the side. The stage
// fits distance and target to the canvas; these give the angle.
const OVERVIEW_CAM = {
  front: {fit: true, target: [0, 7, 0], yaw: 0.55, pitch: 0.32, dist: 56},
  top: {fit: true, target: [0, 7, 0], yaw: 0.55, pitch: 1.42, dist: 56},
  side: {fit: true, target: [0, 7, 0], yaw: 0.55 + Math.PI / 2, pitch: 0.08, dist: 56},
};
// The Story mode frames every cloud revealed so far from the overview's front angle.
const STORY_CAM = {fit: true, target: [0, 7, 0], yaw: 0.55, pitch: 0.32, dist: 56};
const PHASE_NOTE = {
  PARSE: "compile the log into the DSL record", MAP: "read the task goal", ROUTE: "query the record, open suspect windows",
  MANAGER: "turn each suspicion into a question", TRACE: "search the logs to test each suspicion", SUBMIT: "name the root cause",
};

export function mountQuery(app, route) {
  const ref = ["runs", ...(route.ref || DEFAULT_CASE)];
  app.innerHTML = `<div class="qg">${loader("Building the query graph…")}</div>`;
  const root = app.firstElementChild;
  let stage = null, narrative = null, keyHandler = null, destroyed = false;

  loadQueryScene(ref).then(scene => {
    if (destroyed) return;
    const model = buildClusters(scene);
    const layout = layoutClusters(model);
    const roles = roleFacts(model.clusters);
    const story = buildStory(scene, model);
    root.innerHTML = template(scene, model, ref, roles, story);
    stage = createStage(root.querySelector(".qg-canvas"), scene, model, layout);
    const frameAt = createTour(scene, model, layout);
    const last = scene.events.length - 1;
    const plays = scene.events.map((e, i) => i).filter(i => !scene.events[i].error);
    // The nearest played step from i in direction d (0: i itself, else forward first).
    const playable = (i, d = 0) => {
      i = Math.max(0, Math.min(last, i));
      if (!scene.events[i].error) return i;
      const after = plays.find(k => k > i), before = plays.findLast(k => k < i);
      return d < 0 ? before ?? after : after ?? before;
    };
    const lastPlay = plays[plays.length - 1];
    // Tour plays the calls in order; Overview shows every cloud at once for free exploration.
    const state = {i: 0, p: 0, playing: true, speed: 1, mode: "tour", view: "front", chain: null, phase: null,
                   filter: {phases: new Set()}, row: story.rows.length - 1};
    const ui = bindUi(root, scene, model, stage, roles, ref, story);
    // The sentence beside the tower follows the tour's phase; its popup pauses the tour.
    const details = createDetails(root, scene, model, stage, {onOpen: () => setPlaying(false)});
    narrative = mountOverviewNarrative(root, scene, model, stage);
    ui.on("event", (ph, i) => { details.show(ph); details.caption(scene.events[i]); });
    ui.setEvent(state.i);

    stage.start(dt => {
      // Story: the tower stands where the current board row's call left it, that row's clouds in focus.
      if (state.mode === "story") {
        const row = story.rows[state.row];
        const out = frameAt(row.event, 1);
        const beatSpec = row.bridge?.tower?.[state.beat - 1];
        const lit = state.peek || (beatSpec?.focus.length ? beatSpec.focus : row.focus);
        Object.assign(out.frame, {focus: new Set(lit), chain: state.chain?.chain || null, chainEdges: state.chain?.edges, phase: null, filter: state.filter, labelAll: false, mini: true});
        out.camera = STORY_CAM;
        return out;
      }
      const overview = state.mode === "overview";
      if (!overview && state.playing) {
        state.p += dt * state.speed / eventDuration(scene.events[state.i]);
        if (state.p >= 1) {
          if (state.i < lastPlay) { state.i = playable(state.i + 1, 1); state.p = 0; stage.clearHover(); ui.setEvent(state.i); }
          else { state.p = 1; setPlaying(false); }
        }
      }
      if (!overview) ui.setProgress(state.i, state.p);
      const out = overview ? frameAt(last, 1, true) : frameAt(state.i, state.p);
      Object.assign(out.frame, {chain: state.chain?.chain || null, chainEdges: state.chain?.edges, phase: state.phase, filter: state.filter, labelAll: overview});
      if (overview) { out.frame.focus = new Set(); out.camera = OVERVIEW_CAM[state.view]; }
      return out;
    });

    const setMode = mode => {
      state.mode = mode;
      history.replaceState(null, "", location.hash.split("?")[0] + (mode === "tour" ? "" : `?mode=${mode}`));
      stage.follow();
      stage.release();
      state.chain = null;
      setPhase(null);
      details.close();
      ui.setMode(mode, state.view);
      narrative.mode(mode);
      if (mode !== "tour") setPlaying(false);
      else { go(0); setPlaying(true); }
      if (mode === "story") setRow(0);
    };

    const setPlaying = on => { state.playing = on; ui.setPlaying(on); };
    const go = (i, p = 0, d = 0) => { stage.clearHover(); state.i = playable(i, d); state.p = p; ui.setEvent(state.i); };
    const toggle = () => {
      if (!state.playing && state.i === lastPlay && state.p >= 1) go(0);
      if (!state.playing && stage.view.cluster) stage.follow();
      setPlaying(!state.playing);
    };
    ui.on("play", toggle);
    ui.on("step", d => go(state.i + d, 0, d));
    // In Overview the timeline and the phase bar pick a cloud instead of moving the playhead.
    const pick = i => { stage.select(scene.events[i].cluster || "core"); narrative.event(i); };
    ui.on("seek", i => state.mode === "overview" ? pick(i) : go(i, 0.5));
    // In Overview a phase button focuses that phase's clouds instead; a second click lets go.
    const setPhase = ph => {
      state.phase = ph ? phaseFocus(model, ph) : null;
      ui.setPhase(state.phase, state.mode === "overview");
      narrative.phase(ph);
    };
    ui.on("phase", ph => {
      if (state.mode === "overview") {
        const again = state.phase?.phase === ph;
        stage.setJourney(null);
        stage.select(null);
        stage.release();
        setPhase(again ? null : ph);
        return;
      }
      const i = scene.events.findIndex(e => e.phase === ph && !e.error);
      if (i >= 0) go(i);
    });
    ui.on("endPhase", () => setPhase(null));
    stage.on("back", () => state.phase && setPhase(null));
    ui.on("mode", setMode);
    ui.on("view", v => { state.view = v; stage.release(); ui.setMode(state.mode, v); });
    ui.on("filterPhase", ph => { state.filter.phases.has(ph) ? state.filter.phases.delete(ph) : state.filter.phases.add(ph); ui.setFilter(state.filter); });
    // A role row (or a fact's "follow its journey") draws one fact's way to its verdict; that
    // needs every cloud on screen, so the tour gives way to the overview first.
    const follow = (role, fid) => {
      if (state.mode !== "overview") setMode("overview");
      setPhase(null);
      stage.setJourney(journeyOf(scene, model, fid ?? roles.byRole.get(role)[0]));
      ui.showJourney(role);
    };
    // The legend row toggles; the panel's role tabs only switch.
    ui.on("journeyRole", (role, tab) => !tab && stage.journey && ui.journeyRole === role ? stage.setJourney(null) : follow(role));
    ui.on("journeyFact", (fid, role) => follow(role || roles.reach(fid), fid));
    ui.on("cta", () => follow(ctaOf(roles).role));
    stage.on("journey", j => ui.journey(j));
    ui.on("speed", s => { state.speed = s; });
    ui.on("follow", () => { stage.follow(); stage.release(); });
    ui.on("jump", i => { stage.follow(); go(i, 0.5); setPlaying(false); });
    stage.on("select", sel => {
      if (state.mode === "story" && sel?.kind === "cluster") {
        const hit = rowOf(sel.cluster.id);
        if (hit) {
          setRow(hit.k);
          if (hit.beat) { state.beat = hit.beat; showBeat(); }
          return;
        }
      }
      if (sel) { setPlaying(false); if (state.phase) { state.phase = null; ui.setPhase(null, true); } }
      const id = sel?.cluster && sel.cluster.id !== "core" ? sel.cluster.id : null;
      if (id !== state.chain?.id) state.chainAll = false;
      state.chain = id ? chainPath(model, id, state.chainAll) : null;
      ui.setChain(state.chain);
      ui.inspect(sel);
      narrative.selection(sel);
    });
    stage.on("details", id => narrative.details(id));
    // "show all" on the chain list puts the parallel queries back on the chain, and folds them.
    ui.on("chainAll", () => {
      if (!state.chain) return;
      state.chainAll = !state.chainAll;
      state.chain = chainPath(model, state.chain.id, state.chainAll);
      ui.setChain(state.chain);
      ui.inspect({kind: "cluster", cluster: model.byId.get(state.chain.id), opened: false});
    });
    stage.on("manual", () => ui.syncFollow());
    stage.on("view", () => ui.syncFollow());
    stage.on("hover", h => { ui.hover(state.mode === "overview" ? null : h); narrative.hover(h); });
    keyHandler = e => {
      if (e.target.closest("input, textarea, select")) return;
      if (e.key === "Escape" && details.isOpen) { details.close(); return; }
      const j = stage.journey;
      if (state.mode === "story" && !stage.view.cluster && [" ", "ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        if (e.key === "Home" || e.key === "End") setRow(e.key === "Home" ? 0 : story.rows.length - 1);
        else stepStory(e.key === "ArrowLeft" ? -1 : 1);
      } else if (j && !stage.view.cluster && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
        const k = j.focus == null ? (e.key === "ArrowRight" ? 0 : j.stops.length - 1) : j.focus + (e.key === "ArrowRight" ? 1 : -1);
        stage.focusStop(k < 0 || k >= j.stops.length ? null : k);
      } else if (e.key === " ") { e.preventDefault(); toggle(); }
      else if (e.key === "ArrowRight") go(state.i + 1, 0, 1);
      else if (e.key === "ArrowLeft") go(state.i - 1, 0, -1);
      else if (e.key === "Escape") stage.back();
    };
    document.addEventListener("keydown", keyHandler);
    const beats = k => story.rows[k]?.bridge?.beats || null;
    const towerBeat = () => {
      // The board carries the one set of numbers; the tower beside it only lights its clouds.
      stage.markFacts([]);
      if (stage.journey) stage.setJourney(null);
    };
    const showBeat = () => {
      const b = beats(state.row);
      towerBeat();
      if (!b) return;
      ui.setBeat(state.beat, b[state.beat - 1]);
      $count().textContent = `${state.row + 1} / ${story.rows.length} · beat ${state.beat}/${b.length}`;
    };
    const $count = () => root.querySelector(".qg-sb-count");
    const stepStory = d => {
      const b = beats(state.row);
      if (b && state.beat + d >= 1 && state.beat + d <= b.length) { state.beat += d; showBeat(); return; }
      const next = state.row + d;
      setRow(next);
      if (beats(state.row)) { state.beat = d < 0 ? beats(state.row).length : 1; showBeat(); }
    };
    const setRow = k => {
      state.row = Math.max(0, Math.min(story.rows.length - 1, k));
      state.beat = beats(state.row)?.length || 0;
      stage.clearHover();
      stage.select(null);
      stage.release();
      state.chain = null;
      ui.setRow(state.row);
      showBeat();
    };
    ui.on("row", setRow);
    // Pointing at the board lights its clouds on the tower (no camera move); pointing at a
    // tower cloud lights the steps it read on the board.
    ui.on("peekRow", k => { state.peek = k == null ? null : story.rows[k]?.focus || null; });
    ui.on("peekMark", n => { state.peek = n == null ? null : story.rows[state.row]?.bridge?.tower?.marks?.[n]?.focus || null; });
    stage.on("hover", h => {
      if (state.mode !== "story") return;
      const c = h?.kind === "cluster" ? h.cluster : null;
      ui.peekSteps(c && c.kind !== "core" ? [...new Set(c.fids.map(f => scene.factById.get(f)?.t).filter(t => t != null))] : null);
    });
    // A click on a tower cloud sends the board to the first row (or evidence beat) that used it.
    const rowOf = id => {
      if (id === "core") return {k: 0};
      for (let k = 0; k < story.rows.length; k++) {
        const r = story.rows[k], beat = r.bridge?.tower?.findIndex(b => b.focus.includes(id));
        if (beat >= 0) return {k, beat: beat + 1};
        if (r.focus.includes(id)) return {k};
      }
      return null;
    };
    const asked = route.query?.get("mode");
    if (asked === "overview" || asked === "story") setMode(asked);

    // The guide: once per browser, again from the header's Guide button; playback waits for it.
    const pick1 = (...sel) => sel.map(q => root.querySelector(q)).find(el => el?.getClientRects().length);
    const guide = () => {
      const was = state.playing;
      setPlaying(false);
      runCoach(root, [
        {title: "Pick a run", target: () => root.querySelector(".qg-case"),
         text: "Each case is one failed agent run. The debugger compiles its log into a DSL record, queries it, and names the step that caused the failure."},
        {title: "Every cloud is a query",
         text: "Floors rise PARSE → ROUTE → TRACE → SUBMIT. <b>Click</b> a cloud to light its causal chain, <b>click it again</b> to open it by entity. Drag to turn the tower, scroll to zoom."},
        {title: "Follow a fact", before: () => ui.openPane("legend"), target: () => pick1(".qg-follows", ".qg-cta"), side: "right",
         text: "These buttons trace one fact from the record to the verdict, stop by stop. Start with the root cause."},
      ], () => { if (was && state.mode === "tour") setPlaying(true); });
    };
    ui.on("guide", guide);
    if (!coachSeen()) guide();
  }).catch(error => {
    if (destroyed) return;
    root.innerHTML = `<div class="qg-error"><h2>Could not build the query graph</h2><p>${esc(error.message)}</p>
      <p>Open a run from <a href="#/runs">Runs</a>, or use <code>${esc(queryHref(DEFAULT_CASE))}</code>.</p></div>`;
  });

  return {
    unmount() {
      destroyed = true;
      narrative?.destroy();
      stage?.destroy();
      if (keyHandler) document.removeEventListener("keydown", keyHandler);
    },
  };
}

// The three roles a fact can earn, as the legend and the journey panel name them.
const ROLE_ROWS = [
  {role: "issue", label: "clue in a suspect window", tab: "clue"},
  {role: "candidate", label: "step TRACE blames", tab: "blamed"},
  {role: "root", label: "root cause", tab: "root"},
];
const REACH = {issue: "clue only", candidate: "blamed", root: "ROOT"};

// The footer's call to action: the furthest role that has facts.
const ctaOf = roles => [{role: "root", name: "root cause"}, {role: "candidate", name: "blamed step"}, {role: "issue", name: "clues"}]
  .find(r => roles.byRole.get(r.role).length);

// One row's marks on the fisheye step axis, in three shapes: a dot is something seen at a step,
// a bar is steps read, a diamond is a judgement on a step. Inside a folded band the dots and
// bars of one kind collapse into a count.
const pct = v => `${v.toFixed(2)}%`;
function storyMarks(row, ax) {
  const at = s => pct(ax.x(s));
  // Notes sit to the left of their mark when there is room, else above its right shoulder.
  const side = s => ax.x(s) > 22 ? "is-left" : "is-raised";
  const note = (s, t, cls = "") => `<em class="sb-note ${cls} ${side(s)}" style="left:${at(s)}">${esc(t)}</em>`;
  const dot = (s, tone, extra = "", tip = "") => `<i class="sb-dot" data-tone="${tone}" style="left:${at(s)}${extra}"${tip ? ` title="${esc(tip)}"` : ""}></i>`;
  const dia = (s, kind) => `<i class="sb-dia" data-kind="${kind}" style="left:${at(s)}"></i>`;
  const bar = (p, q, cls = "", top = "") => { const [l, w] = ax.span(p, q); return `<i class="sb-bar ${cls}" style="left:${pct(l)};width:${pct(w)}${top}"></i>`; };
  const SIG = {goal: "touches the goal", invalid: "action with no effect", noise: "routine noise"};
  // Folded bands: one count per kind.
  const folds = new Map();
  const fold = (s, key) => { const band = ax.bands.find(b => b.fold && s >= b.a && s <= b.b); const m = folds.get(band) || new Map(); m.set(key, (m.get(key) || 0) + 1); folds.set(band, m); };
  let lane = 0, labelled = false;
  const html = row.marks.map(m => {
    if ((m.type === "signal" || m.type === "hit") && ax.folded(m.step)) { fold(m.step, m.type === "hit" ? "hit" : m.kind); return ""; }
    switch (m.type) {
      case "x": return `${dot(m.step, "goal", "", m.label)}${note(m.step, m.label, "is-bad")}`;
      case "signal": return dot(m.step, m.kind, "", `step ${m.step}: ${SIG[m.kind]}`);
      case "range": {
        if (m.tone === "all") return bar(m.a, m.b, "is-all", ";top:4px");
        const first = m.tone === "near" && !labelled; if (first) labelled = true;
        return bar(m.a, m.b, `is-${m.tone}`, `;top:${10 + lane++ * 6}px`) + (first ? `<em class="sb-note is-tag" style="left:${pct(ax.span(m.a, m.b)[0])};top:${10 + (lane - 1) * 6}px">read closely</em>` : "");
      }
      case "anchor": return `${dia(m.step, "suspect")}${m.label ? note(m.step, m.label, "is-anchor") : ""}`;
      case "question": return dia(m.step, "pending");
      case "hit": { const [l, w] = ax.col(m.step); return `<i class="sb-bar is-hit" style="left:${pct(l + w * 0.32)};width:${pct(w * 0.36)}"></i>`; }
      case "tnode": return dot(m.step, m.lane === "env" ? "env" : m.lane === "goal" ? "goal" : "agent", `;top:${m.lane === "env" ? 72 : m.lane === "goal" ? 28 : 50}%`);
      case "bworld": return dot(m.step, "env", ";top:72%");
      case "blink": return dot(m.step, m.role === "root" ? "root" : m.role === "guess" ? "guess" : "agent", `;margin-left:${m.shift * 9}px`);
      case "bfail": return dot(m.step, "goal", ";top:28%");
      case "blamed": return dia(m.step, "cand");
      case "rival": return `${dia(m.step, "rival")}${note(m.step, m.label, "is-struck")}`;
      case "root": return `${dia(m.step, "root")}${note(m.step, m.label, "is-root")}`;
      default: return "";
    }
  }).join("");
  const counts = [...folds].map(([band, m]) => { const [l, w] = ax.span(band.a, band.b);
    return `<span class="sb-fold" style="left:${pct(l)};width:${pct(w)}">${[...m].map(([k, c]) => `<span title="steps ${band.a}–${band.b}: ${c} × ${k === "hit" ? "step read" : SIG[k]}">${k === "hit" ? `<i class="sb-bar is-hit"></i>` : `<i class="sb-dot" data-tone="${k}"></i>`}${c}</span>`).join("")}</span>`; }).join("");
  return html + counts;
}

// The evidence thread, zoomed: the steps around the anchor (the anchor's column wider), one
// lane each for the goal check, the environment and the agent, chips at their step, and a
// trace lane underneath. Links are drawn after layout (drawThread), from the chips' boxes.
const LANE_TOP = {goal: 22, env: 56, agent: 90};
const LANE_NAME = [["goal", "Goal check"], ["env", "Environment shows"], ["agent", "Agent believes or does"]];
function threadPanel(th) {
  const steps = Array.from({length: th.to - th.from + 1}, (_, i) => th.from + i);
  const weight = s => s === th.anchor ? 3.4 : 1, total = steps.reduce((n, s) => n + weight(s), 0);
  let acc = 0;
  // Columns fill 88% of the track, so a chip centred on the last step still has room to its right.
  const FILL = 88;
  const x = new Map(steps.map(s => { const c = (acc + weight(s) / 2) / total * FILL; acc += weight(s); return [s, c]; }));
  const span = s => { let a = 0; for (const t of steps) { if (t === s) return [a / total * FILL, weight(t) / total * FILL]; a += weight(t); } return [0, 0]; };
  const stack = new Map();
  const chips = th.nodes.map(n => {
    const k = n.lane === "agent" ? (stack.get(n.step) || 0) : 0;
    if (n.lane === "agent") stack.set(n.step, k + 1);
    const tip = `step ${n.step} · ${n.lane === "env" ? "observation" : n.slot === "chk" ? "goal check" : n.slot}\n${n.full || n.label}`;
    return `<span class="th-node" data-id="${n.id}" data-lane="${n.lane}" style="left:${x.get(n.step).toFixed(2)}%;top:${LANE_TOP[n.lane] + k * 30}px" title="${esc(tip)}">${esc(n.label)}${n.sub ? ` <small>${esc(n.sub)}</small>` : ""}</span>`;
  });
  const deepest = Math.max(1, ...stack.values());
  const traceTop = LANE_TOP.agent + deepest * 30 + 4;
  const col = (s, cls) => { const [l, w] = span(s); return `<i class="th-col ${cls}" style="left:${l.toFixed(2)}%;width:${w.toFixed(2)}%"></i>`; };
  const failAt = th.trace?.from;
  return `
    <div class="qg-thread" data-links="${esc(JSON.stringify(th.links))}" data-trace="${th.trace ? `${x.get(th.trace.from)},${x.get(th.trace.to)}` : ""}" style="--trace-top:${traceTop}px">
      <span class="th-lanes">${LANE_NAME.map(([k, t]) => `<b style="top:${LANE_TOP[k]}px">${t}</b>`).join("")}${th.trace ? `<b class="is-trace" style="top:${traceTop}px">Debugger traces back</b>` : ""}</span>
      <span class="th-track">
        ${col(th.anchor, "is-anchor")}${failAt ? col(failAt, "is-fail") : ""}
        ${steps.map(s => `<em class="th-step${s === th.anchor || s === failAt ? " is-marked" : ""}" style="left:${x.get(s).toFixed(2)}%">${s}</em>`).join("")}
        <svg class="th-links" aria-hidden="true"></svg>
        ${chips.join("")}
        ${th.trace ? `<em class="th-trace-label" style="left:${((x.get(th.trace.from) + x.get(th.trace.to)) / 2).toFixed(2)}%;top:${traceTop + 14}px">${esc(th.trace.label)}</em>` : ""}
      </span>
    </div>`;
}

// The evidence bridge (HUD): the goal's rail on top, the world's rail below, the agent's links
// between them. drawBridge lays it out from the track's measured size; every element carries
// the beat it belongs to and shows once the scene reaches that beat.
function bridgePanel(br) {
  return `
    <div class="qg-bridge" data-bridge="${esc(JSON.stringify(br))}">
      <span class="br-lanes">
        <span class="br-lane" data-lane="need" data-beat="1"><i>Goal needs</i><b>${esc(br.need)}</b></span>
        <span class="br-lane" data-lane="link" data-beat="3"><i>Agent links them</i></span>
        <span class="br-lane" data-lane="got" data-beat="2"><i>World shows</i><b>${esc(br.got)}</b></span>
      </span>
      <span class="br-track"><svg class="br-svg" aria-hidden="false" role="img"><title>Evidence bridge</title></svg></span>
    </div>`;
}

function storyBoard(story) {
  const ax = story.axis;
  const marked = new Set(story.cols.map(c => c.step));
  // Each band of the axis: a folded band is one label ("1–12"); the open band labels every step.
  const axis = ax.bands.map(b => b.fold
    ? `<span class="is-fold" style="left:${pct(ax.span(b.a, b.b)[0])};width:${pct(ax.span(b.a, b.b)[1])}">${b.a}–${b.b}</span>`
    : Array.from({length: b.b - b.a + 1}, (_, i) => b.a + i).map(s => `<span${marked.has(s) ? ` class="is-marked"` : ""} style="left:${pct(ax.x(s))}">${s}</span>`).join("")).join("");
  // Under every row: the open band's column lines, the folded bands' shade, the key columns' tint.
  const open = ax.bands.find(b => !b.fold);
  const under = [
    ...ax.bands.filter(b => b.fold).map(b => { const [l, w] = ax.span(b.a, b.b); return `<i class="is-fold" style="left:${pct(l)};width:${pct(w)}"></i>`; }),
    ...Array.from({length: open.b - open.a + 2}, (_, i) => `<i class="is-line" style="left:${pct(i <= open.b - open.a ? ax.col(open.a + i)[0] : ax.span(open.a, open.b).reduce((p, q) => p + q))}"></i>`),
    ...story.cols.map(c => { const [l, w] = ax.col(c.step); return `<i class="is-col" data-tone="${c.tone}" style="left:${pct(l)};width:${pct(w)}"></i>`; }),
  ].join("");
  const lanes = r => r.marks.filter(m => m.type === "range" && m.tone !== "all").length;
  return `
    <section class="qg-board" aria-label="Case board">
      <span class="qg-sb-under" aria-hidden="true">${under}</span>
      <div class="qg-sb-axis"><span class="qg-sb-lab">Agent step</span><span class="qg-sb-track">${axis}</span></div>
      ${story.rows.map((r, k) => `
      <button class="qg-sb-row" data-row="${k}" data-phase="${r.phase}"${lanes(r) ? ` style="--lanes:${lanes(r)}"` : ""} title="${esc(`${r.question}
${r.answer}`)}">
        <span class="qg-sb-lab"><i class="qg-chip" data-phase="${r.phase}">${r.phase}</i><b class="is-q">${esc(r.question)}</b><b class="is-a">${esc(r.short || r.question)}</b></span>
        <span class="qg-sb-track">${storyMarks(r, ax)}</span>
      </button>`).join("")}
      <svg class="qg-sb-spine" aria-hidden="true"></svg>
      <span class="qg-sb-peek" aria-hidden="true"></span>
      ${story.rows.filter(r => r.thread).map(r => r.bridge ? bridgePanel(r.bridge) : threadPanel(r.thread)).join("")}
    </section>
    <aside class="qg-cards" aria-label="Evidence">
      <div class="qg-sb-say"><p class="qg-sb-head"><span class="qg-chip"></span><span class="qg-sb-count"></span></p><strong></strong><p class="qg-sb-answer"></p></div>${story.cards.map(c => `
      <article class="qg-card-ev" data-row="${story.rows.findIndex(r => r.key === c.row)}" data-phase="${c.phase}">
        <h3>${esc(c.title)}</h3>
        ${c.lines.map(l => `<p class="${[l.strong && "is-strong", l.quote && "is-quote", l.quiet && "is-quiet", l.tone === "bad" && "is-bad"].filter(Boolean).join(" ")}"${l.rule ? ` title="${esc(l.rule)}"` : ""}>${l.label ? `<b>${esc(l.label)}</b> ` : ""}${esc(l.text)}</p>`).join("")}
      </article>`).join("")}
    </aside>`;
}

function template(scene, model, ref, roles, story) {
  const cta = ctaOf(roles);
  const n = k => model.clusters.filter(c => c.kind === k).length;
  const ticks = scene.events.map((e, i) => `<button class="qg-tick" data-i="${i}" data-phase="${e.phase}" style="flex-grow:${eventDuration(e)}" title="${esc(`${e.phase} · ${e.title}`)}"${e.error ? " data-error" : ""}></button>`).join("");
  const rels = Object.keys(scene.meta.relations).length;
  return `
    <canvas class="qg-canvas" aria-label="Query graph"></canvas>
    <header class="qg-head">
      <div class="qg-title">
        <span class="qg-kicker">Query Graph</span>
        <h1>${esc(scene.meta.task || scene.meta.tid)}</h1>
        <p><select class="qg-case" aria-label="Case">${CASES.map(c => `<option value="${esc(queryHref(c.ref))}"${c.ref[1] === ref[2] ? " selected" : ""}>${esc(c.label)}</option>`).join("")}${CASES.some(c => c.ref[1] === ref[2]) ? "" : `<option selected>${esc(scene.meta.tid)}</option>`}</select></p>
      </div>
      <div class="qg-modes">
        <div class="qg-seg"><button data-act="guide" title="Show the guide again">Guide</button></div>
        <div class="qg-seg" role="group" aria-label="Mode"><button data-mode="story" aria-pressed="false">Story</button><button data-mode="tour" aria-pressed="true">Tour</button><button data-mode="overview" aria-pressed="false">Overview</button></div>
        <div class="qg-seg qg-views" role="group" aria-label="Camera" hidden><button data-view="front">Front</button><button data-view="top">Top</button><button data-view="side">Side</button></div>
      </div>
      <ol class="qg-phases">${PHASES.filter(ph => scene.events.some(e => e.phase === ph)).map((ph, k) => `<li><button data-phase="${ph}" title="${esc(PHASE_NOTE[ph])}"><span>${String(k + 1).padStart(2, "0")}</span><b>${ph}</b></button></li>`).join("")}</ol>
    </header>
    ${storyBoard(story)}
    <aside class="qg-legend is-collapsed" aria-label="Legend">
      <button class="qg-pane-tab" data-pane="legend" aria-expanded="false" title="Show or hide the legend"><span>Legend</span><i>›</i></button>
      <div class="qg-legend-body">
      <h2>A fact becomes evidence</h2>
      <ul class="qg-key">
        <li><i class="k-pt"></i>fact the debugger read</li>
      </ul>
      <p class="qg-sub">Follow a fact from the record to the verdict:</p>
      <div class="qg-follows">
        ${ROLE_ROWS.map(r => { const n = roles.byRole.get(r.role).length; return `<button data-journey-role="${r.role}"${n ? ` title="Follow these facts from the record to the verdict"` : " disabled"}><i class="k-pt" data-role="${r.role}"></i><span>${r.label}</span><em>${n}</em><b>Follow ›</b></button>`; }).join("")}
      </div>
      <h2>DSL record</h2>
      <dl class="qg-stats">${[["facts", scene.facts.length], ["entities", scene.entities.length], ["relations", rels], ["steps", scene.meta.steps]].map(([k, v]) => `<div><dd>${v}</dd><dt>${k}</dt></div>`).join("")}</dl>
      <p class="qg-formula">One fact = who did or thought what, at which step</p>
      <ul class="qg-key qg-ties">
        <li><i class="k-hub"></i>entity a fact names</li>
        <li><i class="k-thread"></i>same state, next step</li>
        <li><i class="k-dot"></i>pulled from the record</li>
        <li><i class="k-line"></i>leads to</li>
      </ul>
      <h2>Show clouds by phase</h2>
      <p class="qg-sub">Click a phase to hide or show its clouds:</p>
      <div class="qg-phase-toggles">
        ${["PARSE", "MAP", "ROUTE", "TRACE", "SUBMIT"].map(ph => [ph, model.clusters.filter(c => c.phaseKey === ph).length]).filter(([, n]) => n).map(([ph, n]) => `<button data-phase="${ph}" data-filter-phase="${ph}" aria-pressed="true" title="${esc(`${PHASE_NOTE[ph]} · click to hide or show`)}"><i></i><span>${ph.toLowerCase()}</span><em>${n}</em></button>`).join("")}
      </div>
      </div>
    </aside>
    <aside class="qg-inspect is-collapsed" hidden>
      <button class="qg-pane-tab" data-pane="inspect" aria-expanded="false" title="Show or hide the details"><i>‹</i><span>Details</span></button>
      <div class="qg-inspect-body"></div>
    </aside>
    <div class="qg-tip" hidden></div>
    <div class="qg-hovercard" hidden></div>
    <footer class="qg-foot">
      <div class="qg-caption"><span class="qg-chip"></span><div><strong class="qg-cap-title"></strong><p class="qg-cap-text"></p><p class="qg-cap-next"></p></div><p class="qg-cap-full"></p>
        ${cta ? `<button class="qg-cta" data-act="cta" title="Draw this fact's way from the record to the verdict">▶ Follow the ${esc(cta.name)}</button>` : ""}</div>
      <div class="qg-bar">
        <button class="qg-btn" data-act="prev" title="Previous call (←)" aria-label="Previous call">‹</button>
        <button class="qg-btn qg-play" data-act="play" title="Play / pause (space)" aria-label="Play or pause">❚❚</button>
        <button class="qg-btn" data-act="next" title="Next call (→)" aria-label="Next call">›</button>
        <div class="qg-track">${ticks}<span class="qg-head-mark"></span></div>
        <span class="qg-count"></span>
        <button class="qg-btn qg-speed" data-act="speed" title="Playback speed">1×</button>
        <button class="qg-btn qg-follow" data-act="follow" title="Fold everything and follow the tour" hidden>Follow tour</button>
      </div>
    </footer>`;
}

function bindUi(root, scene, model, stage, roles, ref, story) {
  const $ = s => root.querySelector(s);
  // Story mode: the board sits under the header and the tower under the board; both edges are
  // measured, so a two-line title or a wrapped answer moves the tower down instead of under.
  function placeStory() {
    const head = $(".qg-head"), board = $(".qg-board");
    root.style.setProperty("--qg-story-top", `${head.offsetTop + head.offsetHeight + 10}px`);
    root.style.setProperty("--qg-tower-top", `${board.offsetTop + board.offsetHeight + 8}px`);
  }
  new ResizeObserver(() => root.classList.contains("is-story") && placeStory()).observe($(".qg-board"));
  // The evidence spine: a red line through each shown row's key steps, ending in an arrow at
  // the current row; drawn over the rows from their measured boxes.
  let shown = 0;
  function drawSpine() {
    const board = $(".qg-board"), svg = $(".qg-sb-spine");
    if (!root.classList.contains("is-story") || board.classList.contains("is-zoomed")) return;
    const base = board.getBoundingClientRect(), track = board.querySelector(".qg-sb-row .qg-sb-track").getBoundingClientRect();
    const X = s => track.left - base.left + story.axis.x(s) / 100 * track.width;
    const pts = [];
    board.querySelectorAll(".qg-sb-row").forEach((el, i) => {
      if (i > shown) return;
      const r = el.getBoundingClientRect(), y = (r.top + r.bottom) / 2 - base.top;
      story.rows[i].spine.forEach(s => pts.push([X(s), y]));
    });
    svg.setAttribute("viewBox", `0 0 ${base.width} ${base.height}`);
    const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
    svg.innerHTML = `<defs><marker id="sb-arrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M1 1L8 5L1 9Z" fill="var(--qg-sig-goal)"/></marker></defs>`
      + (pts.length ? `<circle class="sb-spine-start" cx="${pts[0][0]}" cy="${pts[0][1]}" r="5"/>` : "")
      + (pts.length > 1 ? `<path class="sb-spine" d="${d}" marker-end="url(#sb-arrow)"/>` : "");
  }
  new ResizeObserver(drawSpine).observe($(".qg-board"));
  // The thread's links: each joins two chips in time order, leaving the earlier chip's side (or
  // bottom, when they share a step) and entering the later one from above or below, a little off
  // its centre so links that meet at one chip do not share a segment.
  function drawThread() {
    const panel = $(".qg-thread"), board = $(".qg-board");
    if (!panel || !board.classList.contains("is-zoomed")) return;
    const track = panel.querySelector(".th-track"), svg = panel.querySelector(".th-links"), base = track.getBoundingClientRect();
    // Chips in one lane row that overlap: the wider one narrows (it is centred, so by twice the
    // overlap) until there is a gap; its full text stays in the tooltip.
    const chips = [...panel.querySelectorAll(".th-node")];
    chips.forEach(c => { c.style.maxWidth = ""; });
    for (let pass = 0; pass < 3; pass++) for (const a of chips) for (const b of chips) {
      if (a === b) continue;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      if (ra.bottom <= rb.top || rb.bottom <= ra.top || ra.right + 8 <= rb.left || rb.right + 8 <= ra.left || ra.width < rb.width) continue;
      const over = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left) + 8;
      a.style.maxWidth = `${Math.max(80, ra.width - 2 * over)}px`;
    }
    const box = id => { const r = panel.querySelector(`[data-id="${id}"]`).getBoundingClientRect(); return {l: r.left - base.left, r: r.right - base.left, t: r.top - base.top, b: r.bottom - base.top, cx: (r.left + r.right) / 2 - base.left, cy: (r.top + r.bottom) / 2 - base.top}; };
    const paths = JSON.parse(panel.dataset.links).map(([from, to]) => {
      const a = box(from), b = box(to);
      if (Math.abs(a.cx - b.cx) < 4) return b.t > a.b ? `M${a.cx} ${a.b} V${b.t - 2}` : `M${a.cx} ${a.t} V${b.b + 2}`;
      const right = b.cx > a.cx, tx = b.cx + (right ? -16 : 16);
      const sx = right ? a.r : a.l;
      return b.t > a.cy ? `M${sx} ${a.cy} H${tx} V${b.t - 2}` : `M${sx} ${a.cy} H${tx} V${b.b + 2}`;
    });
    const [from, to] = (panel.dataset.trace || "").split(",").map(Number);
    const y = parseFloat(panel.style.getPropertyValue("--trace-top")) + 8, W = base.width;
    svg.setAttribute("viewBox", `0 0 ${W} ${base.height}`);
    svg.innerHTML = `<defs><marker id="th-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.6"/></marker></defs>`
      + paths.map(d => `<path class="th-link" d="${d}" marker-end="url(#th-arrow)"/>`).join("")
      + (Number.isFinite(from) ? `<circle class="th-trace-dot" cx="${from / 100 * W}" cy="${y}" r="5"/><path class="th-trace" d="M${from / 100 * W} ${y} H${to / 100 * W + 10}" marker-end="url(#th-arrow)"/>` : "");
  }
  new ResizeObserver(drawThread).observe($(".qg-board"));
  const boardEl = $(".qg-board");
  boardEl.addEventListener("mouseover", e => {
    const mark = e.target.closest("[data-mark]");
    if (mark) { handlers.peekMark?.(mark.dataset.mark); return; }
    const row = e.target.closest(".qg-sb-row");
    handlers.peekRow?.(row && !row.classList.contains("is-hidden") ? Number(row.dataset.row) : null);
  });
  boardEl.addEventListener("mouseleave", () => { handlers.peekRow?.(null); });
  let beat = 6;
  function drawBridge() {
    const panel = $(".qg-bridge"), board = $(".qg-board");
    if (!panel || !board.classList.contains("is-zoomed")) return;
    const br = JSON.parse(panel.dataset.bridge), track = panel.querySelector(".br-track"), svg = panel.querySelector(".br-svg");
    const W = track.clientWidth, H = track.clientHeight;
    if (H < 80) return;
    // The bridge unfolds on the board's own axis, so its steps stand in the board's columns.
    const steps = Array.from({length: br.to - br.from + 1}, (_, i) => br.from + i);
    const X = new Map(steps.map(s => [s, story.axis.x(s) / 100 * W]));
    const colOf = s => story.axis.col(s).map(v => v / 100 * W);
    const yTop = 44, yBot = H - 58, yMid = (yTop + yBot) / 2, end = Math.min(W, (story.axis.span(br.from, br.to).reduce((p, q) => p + q) + 2) / 100 * W);
    const sq = (x, y, r, cls) => `<rect class="${cls}" x="${x - r}" y="${y - r}" width="${2 * r}" height="${2 * r}"/>`;
    const brackets = (x0, y0, x1, y1, cls) => `<path class="${cls}" d="M${x0} ${y0 + 6}V${y0}H${x0 + 6}M${x1 - 6} ${y0}H${x1}V${y0 + 6}M${x1} ${y1 - 6}V${y1}H${x1 - 6}M${x0 + 6} ${y1}H${x0}V${y1 - 6}"/>`;
    const tip = t => `<title>${esc(t)}</title>`;
    // The numbered squares the tower repeats beside the same facts.
    const badge = (x, y, n, kind) => `<g class="br-badge is-${kind}"><rect x="${x - 9}" y="${y - 9}" width="18" height="18"/><text x="${x}" y="${y + 4}">${n}</text></g>`;
    const parts = [];
    // Columns: the suspect step and the failing step tinted; the step ruler along the bottom.
    for (const [s, cls] of [[br.anchor, "is-anchor"], [br.fail, "is-fail"]]) if (s != null && X.has(s)) { const [l, w] = colOf(s); parts.push(`<rect class="br-col ${cls}" x="${l}" y="0" width="${w}" height="${H - 22}"/>`); }
    parts.push(`<line class="br-ruler" x1="0" y1="${H - 20}" x2="${end}" y2="${H - 20}"/>`);
    for (const s of steps) parts.push(`<line class="br-ruler" x1="${X.get(s)}" y1="${H - 24}" x2="${X.get(s)}" y2="${H - 16}"/><text class="br-step${s === br.anchor || s === br.fail ? " is-on" : ""}" x="${X.get(s)}" y="${H - 3}">${s}</text>`);
    // Beat 1 · the goal's rail.  Beat 2 · the world's rail and what happened to the object.
    parts.push(`<g data-beat="1"><line class="br-rail is-need" x1="0" y1="${yTop}" x2="${end}" y2="${yTop}"/></g>`);
    const w0 = br.world[0]?.step ?? br.anchor;
    parts.push(`<g data-beat="2" data-mark="1"><line class="br-rail is-got" x1="${Math.max(0, X.get(w0) - 40)}" y1="${yBot}" x2="${end}" y2="${yBot}"/>${br.world.map((w, i) => `<g>${tip(w.full || w.label)}${sq(X.get(w.step), yBot, i ? 4 : 5, "br-pt is-got")}<text class="br-note is-got" x="${X.get(w.step)}" y="${yBot + 20}">${esc(w.label)}</text></g>`).join("")}${br.world.length ? badge(X.get(br.world[0].step) - 22, yBot - 16, 1, "env") : ""}</g>`);
    // Beats 3–5 · the agent's links: a guess, the commitment, the repeats; the failed check.
    const sameStep = s => br.bridges.filter(b => b.step === s).length > 1;
    const xOf = b => X.get(b.step) + (sameStep(b.step) ? (b.role === "guess" ? -26 : 26) : 0);
    const root = br.bridges.find(b => b.role === "root");
    const lastRepeat = br.bridges.filter(b => b.role === "repeat").pop();
    for (const b of br.bridges) {
      const x = xOf(b), beatNo = b.role === "guess" ? 3 : b.role === "root" ? 4 : 5;
      const label = b.role === "guess" ? `<text class="br-label is-guess" x="${x - 14}" y="${yMid - 4}" text-anchor="end">${esc(b.label)}</text><text class="br-kicker" x="${x - 14}" y="${yMid + 14}" text-anchor="end">GUESS · S${b.step}</text>`
        : b.role === "root" ? `<text class="br-label is-root" x="${x + 14}" y="${yMid - 4}">${esc(b.label)}</text><text class="br-kicker is-root" x="${x + 14}" y="${yMid + 14}">COMMITS · S${b.step}</text>`
        : b === lastRepeat ? `<text class="br-label is-repeat" x="${x + 12}" y="${yMid - 4}">${esc(b.label)}</text><text class="br-kicker" x="${x + 12}" y="${yMid + 14}">REPEATS · S${b.step}</text>` : "";
      const num = b.role === "guess" ? 2 : b.role === "root" ? 3 : b === lastRepeat ? 4 : 0;
      const numAt = b.role === "guess" ? badge(x - 14, yTop + 18, 2, "agent") : num ? badge(x + 14, yTop + 42, num, b.role === "root" ? "root" : "agent") : "";
      parts.push(`<g data-beat="${beatNo}"${num ? ` data-mark="${num}"` : ""}>${numAt}${tip(`step ${b.step} · ${b.quotes.join("\n")}`)}<line class="br-link is-${b.role}" x1="${x}" y1="${yTop + 5}" x2="${x}" y2="${yBot - 5}"/>${sq(x, yTop, 3, `br-pt is-${b.role}`)}${sq(x, yBot, 3, `br-pt is-${b.role}`)}${b.role === "guess" ? `<circle class="br-q" cx="${x}" cy="${yTop + (yMid - yTop) * 0.45}" r="8"/><text class="br-qmark" x="${x}" y="${yTop + (yMid - yTop) * 0.45 + 4}">?</text>` : ""}${label}</g>`);
    }
    if (br.fail != null && X.has(br.fail)) {
      const x = X.get(br.fail);
      parts.push(`<g data-beat="5" data-mark="5">${badge(x - 18, yTop - 14, 5, "goal")}<path class="br-x" d="M${x - 6} ${yTop - 6}L${x + 6} ${yTop + 6}M${x + 6} ${yTop - 6}L${x - 6} ${yTop + 6}"/><text class="br-kicker is-fail" x="${x + 12}" y="${yTop - 10}">CHECK FAILS · S${br.fail}</text></g>`);
      // Beat 6 · the trace back to the first commitment, which gets its brackets.
      if (root) {
        const rx = xOf(root), ty = yTop + 20;
        parts.push(`<g data-beat="6"><circle class="br-dot" cx="${x}" cy="${ty}" r="4"/><path class="br-trace" d="M${x - 6} ${ty}H${rx + 8}" marker-end="url(#br-arrow)"/><text class="br-kicker is-fail" x="${(x + rx) / 2}" y="${ty + 16}" text-anchor="middle">TRACE BACK</text>${brackets(rx + 6, yMid - 24, rx + 30 + root.label.length * 8.6, yMid + 22, "br-brackets")}</g>`);
      }
    }
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = `<title>Evidence bridge</title><defs><marker id="br-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.6"/></marker></defs>${parts.join("")}`;
    // The lane names sit level with their rails.
    const place = (lane, y) => { const el = panel.querySelector(`[data-lane="${lane}"]`); el.style.top = `${y}px`; };
    place("need", yTop); place("link", yMid); place("got", yBot);
    setBeat(beat);
  }
  function setBeat(b) {
    beat = b;
    $(".qg-bridge")?.querySelectorAll("[data-beat]").forEach(el => el.classList.toggle("is-on", Number(el.dataset.beat) <= b));
  }
  new ResizeObserver(drawBridge).observe($(".qg-board"));
  const ticks = [...root.querySelectorAll(".qg-tick")];
  const handlers = {};
  const inspect = $(".qg-inspect"), panelBody = $(".qg-inspect-body"), tip = $(".qg-tip");
  let current = 0, journeyRole = null, chain = null;
  // Both side panels start folded to a tab; the details panel also opens when the user picks
  // something on the stage and folds again when the pick is cleared.
  const setPane = (name, open) => {
    const pane = $(`.qg-${name}`);
    pane.classList.toggle("is-collapsed", !open);
    pane.querySelector(".qg-pane-tab").setAttribute("aria-expanded", String(open));
  };

  // The side panels stop above the footer, whose height changes with the caption's length.
  const foot = $(".qg-foot");
  new ResizeObserver(() => { root.style.setProperty("--qg-foot-h", `${foot.offsetHeight}px`); clampCaption(); }).observe(foot);
  // The caption text stops at two lines; when it is cut, hovering the caption shows the whole
  // text in a card above it, so the footer (and the stage framed against it) keeps its height.
  function clampCaption() {
    const text = $(".qg-cap-text");
    $(".qg-cap-full").textContent = text.textContent;
    $(".qg-caption").classList.toggle("is-clamped", text.scrollHeight > text.clientHeight + 1);
  }
  $(".qg-case")?.addEventListener("change", e => { if (e.target.value.startsWith("#/")) location.hash = e.target.value; });
  panelBody.addEventListener("mouseover", e => stage.highlightChainEdge(e.target.closest("[data-chain-edge]") ? Number(e.target.closest("[data-chain-edge]").dataset.chainEdge) : null));
  panelBody.addEventListener("mouseleave", () => stage.highlightChainEdge(null));
  root.addEventListener("click", e => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "speed") {
      const b = e.target.closest("button");
      const s = {"1×": 2, "2×": 0.5}[b.textContent] ?? 1;
      b.textContent = `${s}×`;
      handlers.speed?.(s);
    } else if (act === "prev" || act === "next") handlers.step?.(act === "prev" ? -1 : 1);
    else if (act) handlers[act]?.();
    const mode = e.target.closest("[data-mode]");
    if (mode) handlers.mode?.(mode.dataset.mode);
    const cam = e.target.closest("[data-view]");
    if (cam) handlers.view?.(cam.dataset.view);
    const fp = e.target.closest("[data-filter-phase]");
    if (fp) handlers.filterPhase?.(fp.dataset.filterPhase);
    const jr = e.target.closest("[data-journey-role]:not(:disabled)");
    if (jr) handlers.journeyRole?.(jr.dataset.journeyRole, !!jr.closest(".qg-roles"));
    const jf = e.target.closest("[data-journey-fact]");
    if (jf) handlers.journeyFact?.(Number(jf.dataset.journeyFact), jf.dataset.role);
    const stop = e.target.closest("[data-stop]");
    if (stop) { const k = Number(stop.dataset.stop); stage.focusStop(stage.journey?.focus === k ? null : k); }
    if (e.target.closest("[data-end-journey]")) stage.setJourney(null);
    if (e.target.closest("[data-end-phase]")) handlers.endPhase?.();
    const pick = e.target.closest("[data-select]");
    if (pick) stage.select(pick.dataset.select);
    if (e.target.closest("[data-chain-all]")) handlers.chainAll?.();
    const row = e.target.closest(".qg-sb-row, .qg-card-ev");
    if (row) handlers.row?.(Number(row.dataset.row));
    const tick = e.target.closest(".qg-tick");
    if (tick) handlers.seek?.(Number(tick.dataset.i));
    const ph = e.target.closest(".qg-phases [data-phase]");
    if (ph) handlers.phase?.(ph.dataset.phase);
    const jump = e.target.closest("[data-jump]");
    if (jump) handlers.jump?.(Number(jump.dataset.jump));
    const open = e.target.closest("[data-open]");
    if (open) stage.open(open.dataset.open, open.dataset.group != null ? Number(open.dataset.group) : null);
    const fact = e.target.closest("[data-fact]");
    if (fact) { stage.selectFact(Number(fact.dataset.fact)); showFact(Number(fact.dataset.fact), stage.view); }
    if (e.target.closest("[data-fold]")) stage.back();
    const tab = e.target.closest("[data-pane]");
    if (tab) setPane(tab.dataset.pane, tab.closest("aside").classList.contains("is-collapsed"));
  });

  function showEvent(i) {
    const ev = scene.events[i];
    const c = model.byId.get(ev.cluster || "core");
    if (!c) { inspect.hidden = true; return; }
    showCluster(c, ev);
  }

  function crumbs(c, gi, fid) {
    const tier = TIERS.find(t => t.tier === c.tier);
    const parts = [`<span>T${c.tier} ${esc(tier.label)}</span>`];
    parts.push(gi == null && fid == null ? `<b>${esc(clusterTitle(c))}</b>` : `<button data-open="${esc(c.id)}">${esc(clusterTitle(c))}</button>`);
    if (gi != null) parts.push(fid == null ? `<b>${esc(c.groups[gi].key)}</b>` : `<button data-open="${esc(c.id)}" data-group="${gi}">${esc(c.groups[gi].key)}</button>`);
    if (fid != null) parts.push(`<b>f${fid}</b>`);
    return `<nav class="qg-crumbs">${parts.join("<i>›</i>")}</nav>`;
  }

  function sharing(c) {
    const own = new Set(c.fids);
    return model.clusters.filter(o => o !== c && o.kind !== "core" && o.fids.length)
      .map(o => [o, o.fids.filter(f => own.has(f)).length]).filter(([, k]) => k).sort((a, b) => b[1] - a[1]).slice(0, 6);
  }

  function showCluster(c, ev = scene.events[c.event], opened = false) {
    inspect.hidden = false;
    let body = "";
    if (c.error) body = `<p class="qg-bad">${esc(c.error.code || "error")}: ${esc(c.error.message)}</p>`;
    else if (c.kind === "query" && ev.calls) body = `<pre class="qg-code">${esc(ev.calls.slice(0, 24).map(e => `${e.name}(f${e.call.arguments?.fid ?? "?"})${e.verdict ? ` → ${e.verdict}` : ""}${e.error ? " ✕" : ""}`).join("\n"))}${ev.calls.length > 24 ? `\n… ${ev.calls.length - 24} more` : ""}</pre>`;
    else if (c.kind === "query") body = `<pre class="qg-code">${esc(ev.raw || `${ev.name}(${JSON.stringify(ev.spec)})`)}</pre>`;
    else if (c.kind === "window") body = `<p>${esc(short(c.ref.hypothesis, 320))}</p>`;
    else if (c.kind === "lane") body = `<p>${esc(short(c.ref.question, 300))}</p>${c.fill !== c.event ? `<details><summary>SQL</summary><pre class="qg-code qg-sql">${esc(scene.events[c.fill].sql || "")}</pre></details>` : ""}`;
    else if (c.kind === "candidate" || c.kind === "root") body = `<p>${esc(c.ref.proposition)}</p>${c.ref.quote ? `<blockquote>${esc(c.ref.quote)}</blockquote>` : ""}`;
    else if (c.kind === "core") body = `<p class="qg-muted">The whole compiled trajectory. Every access above draws its facts from here.</p>`;
    if (c.raw && c.kind !== "query") body += `<pre class="qg-code">${esc(c.raw)}</pre>`;
    const groups = c.groups.length ? `<h4>Entities <small>${c.groups.length}</small></h4>
      <ul class="qg-rows">${c.groups.map((g, gi) => `<li><button data-open="${esc(c.id)}" data-group="${gi}"><span>${esc(g.key)}</span><em>${g.fids.length}</em></button></li>`).join("")}</ul>` : "";
    const shared = c.kind !== "core" ? sharing(c) : [];
    const sharedHtml = shared.length ? `<h4>Shares facts with</h4><ul class="qg-rows">${shared.map(([o, k]) => `<li><button data-open="${esc(o.id)}"><span>${esc(clusterTitle(o))}</span><em>${k}</em></button></li>`).join("")}</ul>` : "";
    const phase = ev?.phase || "PARSE";
    if (root.classList.contains("is-overview") && c.narrative) {
      panelBody.innerHTML = `${crumbs(c)}<p class="qg-kind">Evidence index · ${esc(c.kind)}</p>
        <h3>${esc(clusterTitle(c))}</h3><p class="qg-muted">${c.fids.length} mapped facts. Select an entity or fact to inspect its source.</p>
        <button class="ovn-more" data-ov-more>Explain this investigation</button>
        ${groups}${sharedHtml}<details><summary>Recorded request / assessment</summary>${body}</details>
        <p class="qg-hint">Association does not establish a bug or a causal relationship.</p>`;
      return;
    }
    panelBody.innerHTML = `${crumbs(c)}
      <p class="qg-kind"><span class="qg-chip" data-phase="${esc(phase)}">${esc(phase)}</span>${esc(c.kind)}${opened ? ` · <button class="qg-link" data-fold>fold</button>` : ""}</p>
      <h3>${esc(c.label)}</h3><p class="qg-muted">${esc(c.detail)}</p>
      ${!opened && c.groups.length ? `<button class="qg-open" data-open="${esc(c.id)}">Open by entity ›</button>` : ""}${!opened ? chainList(c) : ""}${body}${groups}${sharedHtml}
      <p class="qg-muted qg-run">Run <a href="${caseHref(ref)}" title="Open in the workbench">${esc(scene.meta.tid)}</a></p>`;
  }

  // The lit chain as a numbered list matching the badges on the stage: each step's two clouds,
  // what was asked along it and what came back. Parallel queries set aside are counted under
  // the step into their window, with a switch to show them all.
  function chainList(c) {
    if (!chain || chain.id !== c.id || !chain.edges.length) return "";
    const name = id => id === "core" ? "DSL record" : clusterTitle(model.byId.get(id));
    const extraNote = id => {
      const n = chain.extra.get(id)?.length;
      return n ? `<li class="qg-note">+${n} more quer${n === 1 ? "y" : "ies"} also returned these clues · <button class="qg-link" data-chain-all>show all</button></li>` : "";
    };
    return `<h4>Causal chain <small>${chain.edges.length} steps</small></h4>
      <ol class="qg-chain">${chain.edges.map((e, k) => `
        <li data-chain-edge="${k}" data-phase="${esc(model.byId.get(e.to).phaseKey)}"><b>${k + 1}</b><div>
          <strong>${esc(name(e.from))} → ${esc(name(e.to))}</strong>
          ${e.link.title ? `<span>${esc(e.link.title)}</span>` : ""}${e.link.detail ? `<em>${esc(e.link.detail)}</em>` : ""}</div></li>
        ${extraNote(e.to)}`).join("")}
      </ol>${chain.all ? `<p class="qg-muted">Showing every query that fed a window · <button class="qg-link" data-chain-all>fold</button></p>` : ""}`;
  }

  function showPhase(focus) {
    const ph = focus.phase, list = [...focus.members].map(id => model.byId.get(id));
    inspect.hidden = false;
    setPane("inspect", true);
    panelBody.innerHTML = `
      <p class="qg-kind"><span class="qg-chip" data-phase="${esc(ph)}">${esc(ph)}</span>phase · <button class="qg-link" data-end-phase>close</button></p>
      <h3>${esc(PHASE_NOTE[ph])}</h3><p class="qg-muted">${list.length} cloud${list.length === 1 ? "" : "s"} · ${focus.edges.length} links in or out</p>
      <ul class="qg-rows">${list.map(c => `<li><button data-select="${esc(c.id)}"><span>${esc(clusterTitle(c))}</span><em>${esc(c.detail)}</em></button></li>`).join("")}</ul>
      <p class="qg-hint">Click a cloud to light its whole chain; Esc or the phase button again lets go.</p>`;
  }

  function showGroup(c, gi) {
    const g = c.groups[gi];
    const ent = scene.entities.find(e => e.key === g.key);
    inspect.hidden = false;
    panelBody.innerHTML = `${crumbs(c, gi)}
      <p class="qg-kind">entity group</p><h3>${esc(g.key)}</h3>
      ${ent ? `<dl><dt>kind</dt><dd>${esc(ent.kind)}${ent.parent ? ` ⊑ ${esc(ent.parent)}` : ""}</dd><dt>in record</dt><dd>${ent.facts.length} facts</dd></dl>` : ""}
      <h4>Facts in this access <small>${g.fids.length}</small></h4>
      <ul class="qg-rows qg-facts">${g.fids.map(fid => { const f = scene.factById.get(fid); return `<li><button data-fact="${fid}"><span>${esc(factLabel(f))}</span><em>${f.slot}</em></button></li>`; }).join("")}</ul>`;
  }

  function showFact(fid, view) {
    const f = scene.factById.get(fid), c = model.byId.get(view.cluster);
    const sig = scene.meta.relations[f.rel];
    const seen = model.clusters.filter(o => o.kind !== "core" && o.fids.includes(fid));
    inspect.hidden = false;
    panelBody.innerHTML = `${c ? crumbs(c, view.group, fid) : ""}
      <p class="qg-kind">fact ${fid}</p><h3>${esc(`${f.rel}(${f.args.join(", ")})`)}</h3>
      <pre class="qg-code">${esc(factRepr(f))}</pre>
      <dl><dt>step</dt><dd>S${f.t} · ${SLOT_LABEL[f.slot]} · ${f.side}</dd>
      ${sig ? `<dt>relation</dt><dd>${esc(`${sig.name}(${(sig.arg_kinds || []).join(", ")})`)}${(sig.props || []).length ? ` <small>${esc(sig.props.join(" · "))}</small>` : ""}</dd>` : ""}
      ${f.basis.length ? `<dt>basis</dt><dd>${f.basis.map(b => `<code>f${b}</code>`).join(" ")}</dd>` : ""}</dl>
      ${f.span ? `<blockquote>${esc(root.classList.contains("is-overview") ? f.span : short(f.span, 420))}</blockquote>` : ""}
      ${root.classList.contains("is-overview") ? `<button class="ovn-inline" data-ov-step="${f.t}">Read original step S${f.t} ↗</button>` : ""}
      ${roles.reach(fid) ? `<button class="qg-open" data-journey-fact="${fid}">Follow its journey ›</button>` : ""}
      <h4>Returned by <small>${seen.length}</small></h4>
      <ul class="qg-rows">${seen.map(o => `<li><button data-open="${esc(o.id)}"><span>${esc(clusterTitle(o))}</span><em>T${o.tier}</em></button></li>`).join("")}</ul>`;
  }

  // The journey panel, in plain English for readers new to the project: the role's facts (the
  // agent's own words, how far each got), then the chosen fact's stops as numbered story cards
  // matching the badges on the stage. Each card's structured record folds into its Details.
  function renderJourney(j) {
    const role = journeyRole;
    const fids = roles.byRole.get(role) || [];
    const f = j.fact;
    const stopTitle = s => s.kind === "core" ? "DSL record" : clusterTitle(model.byId.get(s.id));
    // Re-rendering on every focus change keeps the Details the reader opened.
    const opened = new Set([...panelBody.querySelectorAll("details[open][data-k]")].map(d => d.dataset.k));
    const fold = (k, rows) => `<details data-k="${k}"${opened.has(String(k)) ? " open" : ""}><summary>Details</summary><dl>${rows.map(([dt, dd]) => `<dt>${dt}</dt><dd>${esc(dd)}</dd>`).join("")}</dl></details>`;
    inspect.hidden = false;
    panelBody.innerHTML = `
      <p class="qg-kind">evidence journey · <button class="qg-link" data-end-journey>close</button></p>
      <div class="qg-seg qg-roles" role="group" aria-label="Role">${ROLE_ROWS.map(r => `<button data-journey-role="${r.role}" aria-pressed="${r.role === role}"${roles.byRole.get(r.role).length ? "" : " disabled"}>${r.tab} <small>${roles.byRole.get(r.role).length}</small></button>`).join("")}</div>
      <ul class="qg-rows qg-jfacts">${fids.map(fid => { const x = scene.factById.get(fid), w = agentWords(x); return `<li><button data-journey-fact="${fid}" data-role="${role}" title="${esc(`f${fid} ${x.rel}(${x.args.join(", ")})`)}"${fid === f.fid ? ` aria-current="true"` : ""}><span><i>step ${x.t} ${SLOT_LABEL[x.slot]}</i>${w ? ` “${esc(w)}”` : ""}</span><em data-reach="${roles.reach(fid)}">→ ${REACH[roles.reach(fid)]}</em></button></li>`; }).join("")}</ul>
      <div class="qg-jhead">
        <h3>${esc(j.name)}</h3>
        ${j.words ? `<blockquote>“${esc(j.words)}”</blockquote>` : ""}
        ${fold("fact", [["fact", `f${f.fid} ${f.rel}(${f.args.join(", ")}) = ${short(typeof f.value === "string" ? f.value : JSON.stringify(f.value), 90)}`], ["recorded", `S${f.t} · ${SLOT_LABEL[f.slot]}`]])}
      </div>
      <ol class="qg-story">${j.stops.map((s, k) => `
        ${k === 1 && j.startSay ? `<li class="qg-note" title="${esc(j.start)}">↳ ${esc(j.startSay)}</li>` : ""}
        <li data-phase="${esc(s.phase)}"${s.branch ? ` data-branch="${s.branch}"` : ""}${j.focus === k ? ` class="on"` : ""}>
          <button data-stop="${k}"><b>${esc(s.num)}</b><span class="qg-chip" data-phase="${esc(s.phase)}">${esc(s.phase)}</span>
            <p>${esc(s.say)}</p>${s.quote ? `<blockquote>“${esc(s.quote)}”</blockquote>` : ""}</button>
          ${fold(k, [["where", stopTitle(s)], ...(s.kind === "core" ? [] : [["call", String(s.event).padStart(2, "0")]]), ["result", s.outcome]])}
        </li>
        ${k === j.endAt && j.endSay ? `<li class="qg-note qg-end" title="${esc(j.end)}">⊣ ${esc(j.endSay)}</li>` : ""}`).join("")}
      </ol>
      <p class="qg-hint">Click a card or a numbered badge to fly to that stop; ← → step along the way, Esc steps back.</p>`;
    panelBody.querySelector(".qg-story li.on")?.scrollIntoView({block: "nearest"});
  }

  return {
    on(name, fn) { handlers[name] = fn; },
    setEvent(i) {
      current = i;
      const ev = scene.events[i];
      ticks.forEach((t, k) => { t.classList.toggle("done", k < i); t.classList.toggle("now", k === i); });
      const cur = PHASES.indexOf(ev.phase);
      root.querySelectorAll(".qg-phases button").forEach(b => {
        const k = PHASES.indexOf(b.dataset.phase);
        b.classList.toggle("now", k === cur); b.classList.toggle("done", k < cur);
      });
      const chip = $(".qg-caption .qg-chip");
      chip.textContent = ev.phase;
      chip.dataset.phase = ev.phase;
      $(".qg-cap-title").textContent = ev.title || "";
      $(".qg-cap-text").textContent = ev.caption || "";
      const next = scene.events.slice(i + 1).find(e => !e.error);
      $(".qg-cap-next").textContent = next ? `Next · ${next.phase} · ${next.title || ""}` : "End of the tour · switch to Overview to explore every cloud";
      $(".qg-caption").dataset.phase = ev.phase;
      clampCaption();
      root.classList.toggle("at-end", !next);
      const played = scene.events.filter(e => !e.error);
      $(".qg-count").textContent = `${String(played.indexOf(ev) + 1).padStart(2, "0")} / ${played.length}`;
      if (!stage.view.cluster) showEvent(i);
      handlers.event?.(ev.phase, i);
    },
    setProgress(i, p) {
      const t = ticks[i];
      if (t) $(".qg-head-mark").style.left = `${t.offsetLeft + t.offsetWidth * p}px`;
    },
    setPlaying(on) { $(".qg-play").textContent = on ? "❚❚" : "▶"; },
    syncFollow() {
      const b = $(".qg-follow");
      b.hidden = !(stage.view.cluster || stage.manual || stage.journey || (root.classList.contains("is-overview") && stage.view.selected));
      b.textContent = root.classList.contains("is-overview") ? "Reset view" : "Follow tour";
    },
    setMode(mode, view) {
      root.classList.toggle("is-overview", mode === "overview");
      root.classList.toggle("is-story", mode === "story");
      root.querySelectorAll("[data-mode]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
      $(".qg-views").hidden = mode !== "overview";
      root.querySelectorAll("[data-view]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
      if (mode === "story") placeStory();
      else if (mode === "overview") {
        ticks.forEach(t => { t.classList.add("done"); t.classList.remove("now"); });
        root.querySelectorAll(".qg-phases button").forEach(b => { b.classList.add("done"); b.classList.remove("now"); });
        const chip = $(".qg-caption .qg-chip");
        chip.textContent = "OVERVIEW";
        chip.dataset.phase = "SUBMIT";
        $(".qg-cap-title").textContent = `${scene.events.length} calls · ${model.clusters.length} clouds`;
        $(".qg-cap-text").textContent = "Every access at once. Click a cloud to light the chain it belongs to, click it again to open it; the timeline and phase bar pick clouds too.";
        $(".qg-cap-next").textContent = "";
        $(".qg-caption").dataset.phase = "SUBMIT";
        clampCaption();
        $(".qg-count").textContent = `${scene.events.length} calls`;
        showCluster(model.byId.get("root") || model.byId.get("core"));
      } else this.setEvent(current);
      this.syncFollow();
    },
    setFilter(filter) {
      root.querySelectorAll("[data-filter-phase]").forEach(b => b.setAttribute("aria-pressed", String(!filter.phases.has(b.dataset.filterPhase))));
    },
    setRow(k) {
      root.querySelectorAll(".qg-sb-row").forEach((el, i) => {
        el.classList.toggle("is-hidden", i > k); el.classList.toggle("is-now", i === k);
        el.setAttribute("aria-current", String(i === k));
      });
      root.querySelectorAll(".qg-card-ev").forEach(el => {
        const i = Number(el.dataset.row);
        el.classList.toggle("is-hidden", i > k); el.classList.toggle("is-now", i === k);
      });
      const row = story.rows[k], say = $(".qg-sb-say");
      $(".qg-board").classList.toggle("is-zoomed", !!row.thread);
      root.classList.toggle("is-bridge", !!row.thread);
      shown = k;
      requestAnimationFrame(() => { drawSpine(); if (row.thread) { drawThread(); drawBridge(); } });
      say.dataset.phase = row.phase;
      say.querySelector(".qg-chip").textContent = row.phase;
      say.querySelector("strong").textContent = row.question;
      say.querySelector(".qg-sb-answer").textContent = row.answer;
      $(".qg-sb-count").textContent = k < story.rows.length - 1 ? `${k + 1} / ${story.rows.length} · → next` : `${k + 1} / ${story.rows.length}`;
      root.querySelector(".qg-card-ev.is-now")?.scrollIntoView({block: "nearest"});
    },
    // The steps a pointed-at tower cloud read, as lit columns over the board's rows.
    peekSteps(steps) {
      const layer = $(".qg-sb-peek");
      layer.innerHTML = (steps || []).map(s => { const [l, w] = story.axis.col(s); return `<i style="left:${pct(l)};width:${pct(w)}"></i>`; }).join("");
      layer.classList.toggle("is-on", !!steps?.length);
    },
    setBeat(b, line) {
      setBeat(b);
      if (line) $(".qg-sb-answer").textContent = line;
    },
    get journeyRole() { return journeyRole; },
    openPane(name) { setPane(name, true); },
    setChain(path) { chain = path; },
    // The phase in focus: its button pressed, and its clouds listed in the panel (quiet keeps the
    // panel as it is, for a pick that replaces it anyway).
    setPhase(focus, quiet = false) {
      root.querySelectorAll(".qg-phases button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.phase === focus?.phase)));
      if (quiet) return;
      if (focus) showPhase(focus);
      else this.inspect(null);
    },
    showJourney(role) {
      journeyRole = role;
      this.journey(stage.journey);
    },
    // The stage's journey changed (picked, focused or closed): mark the legend row and redraw
    // the panel, or hand the panel back when the journey closes.
    journey(j) {
      if (!j) journeyRole = null;
      root.querySelectorAll(".qg-legend [data-journey-role]").forEach(li => li.classList.toggle("only", li.dataset.journeyRole === journeyRole));
      this.syncFollow();
      if (!j) { this.inspect(null); return; }
      setPane("inspect", true);
      renderJourney(j);
    },
    // Hover help never floats over the stage's lines: a cloud's or entity's own tag says what a
    // click does, an edge's card lies over the details panel's place (folded or not, so the
    // stage does not reframe), and only facts and journey stops keep a small tip by the pointer.
    hover(h) {
      panelBody.querySelectorAll(".qg-chain li.hi").forEach(li => li.classList.remove("hi"));
      if (h?.kind === "edge" && h.k != null) panelBody.querySelector(`[data-chain-edge="${h.k}"]`)?.classList.add("hi");
      const card = $(".qg-hovercard");
      if (h?.kind === "edge") {
        if (card.dataset.link !== h.link.title + h.link.detail) {
          card.dataset.link = h.link.title + h.link.detail;
          card.innerHTML = `<p class="qg-kind">hovered edge</p><h3>${esc(h.link.title)}</h3>${h.link.detail ? `<p class="qg-muted">${esc(h.link.detail)}</p>` : ""}
            <dl class="qg-card">${h.link.card.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(short(v, 360))}</dd>`).join("")}</dl>`;
        }
        card.hidden = false;
      } else if (!card.hidden) {
        card.hidden = true;
        card.dataset.link = "";
      }
      let html = "";
      const act = t => `<i class="qg-tip-act">${t}</i>`;
      if (h?.kind === "stop") html = `<b>${h.k + 1} · ${esc(h.stop.kind === "core" ? "DSL record" : clusterTitle(model.byId.get(h.stop.id)))}</b><span>${esc(h.stop.outcome)}</span>${act("click to fly there")}`;
      else if (h?.kind === "fact") { const f = scene.factById.get(h.fid); html = `<b>fact ${f.fid} · S${f.t} ${SLOT_LABEL[f.slot]}</b><em>${esc(short(typeof f.value === "string" ? f.value : JSON.stringify(f.value), 160))}</em>`; }
      if (!html) { tip.hidden = true; return; }
      tip.hidden = false;
      tip.innerHTML = html;
      tip.style.left = `${Math.min(h.x + 16, root.clientWidth - 320)}px`;
      tip.style.top = `${Math.max(8, h.y + 14)}px`;
    },
    inspect(sel) {
      this.syncFollow();
      setPane("inspect", !!sel && !(root.classList.contains("is-overview") && sel.kind === "cluster" && !sel.opened));
      if (!sel) { showEvent(current); return; }
      if (sel.kind === "cluster") showCluster(sel.cluster, scene.events[sel.cluster.event], sel.opened);
      else if (sel.kind === "group") showGroup(sel.cluster, sel.gi);
      else if (sel.kind === "fact") showFact(sel.fid, stage.view);
    },
  };
}
