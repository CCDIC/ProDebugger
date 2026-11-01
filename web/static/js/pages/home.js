// Home page: a light, business-style overview with its own header. In-page links scroll
// instead of changing the hash, which belongs to the router.
import {CaseStore} from "../case/store.js";
import {createCaseViews, TABS} from "../case/views.js";
import {caseHref} from "../routes.js";
import {domainIcon} from "./runs.js";
import {esc, loader} from "../util.js";

// The showcase case: a fully correct reference run kept under artifacts/.
export const DEMO_REF = ["runs", "dsl-route-causal-timeline-high-unlimited-alf18-20261007-live", "GPT-4o_018_alfworld_task_018"];

const icon = paths => `<svg class="h-icon" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
const ARROW = icon('<path d="M5 12h14M13 6l6 6-6 6"/>');

const PAINS = [
  ["Hundreds of steps to read", "A single run mixes observations, memory, plans and actions; the decisive moment is buried."],
  ["Symptoms show up late", "The failing step is rarely the faulty one. The real mistake happened several steps earlier."],
  ["Verdicts without evidence", "A model saying “step 7 was wrong” is hard to trust when you cannot check why."],
];

const CAPABILITIES = [
  ["Compare failure hypotheses", '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
   "Investigates candidate steps, modules and fault types, with evidence that can support or challenge each hypothesis."],
  ["Evidence you can verify", '<path d="M12 3 5 6v6c0 4.2 3 7.6 7 9 4-1.4 7-4.8 7-9V6z"/><path d="m9 12 2 2 4-4"/>',
   "Inspect source quotations and compiled facts behind each candidate. SUBMIT selects an available candidate and records evidence limits in its uncertainty explanation."],
  ["Watch it think, live", '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
   "Follow each stage and every window review as the run writes it, or replay a finished diagnosis step by step."],
  ["See the structure", '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="8" cy="18" r="2.5"/><circle cx="18" cy="17" r="2.5"/><path d="M8.3 7 15.6 7M7 8.4l.7 7.1M10.4 17.8l5.1-.6M17.9 9.5l.1 5"/>',
   "Trajectories compile into fact, entity and goal graphs, shown side by side with the debugger's own decisions."],
];

const USE_CASES = [
  ["alfworld", "Embodied & household agents", "ALFWorld",
   "Agents that move through rooms, open receptacles and handle objects. ProDebugger checks each action against the goal and the world state the agent had already observed.",
   ["Repeated or ineffective actions", "Plans that drift from the goal", "Misread observations"]],
  ["webshop", "Web shopping agents", "WebShop",
   "Inspect search, product options and purchase actions against recorded shopping requirements, and compare candidate explanations for unmet constraints.",
   ["Constraints lost during search", "Wrong option or attribute chosen", "Premature purchase"]],
];

const STAGES = [
  ["map", "MAP", "Map the goal", "Builds a goal map from task requirements, retaining unresolved parsing as evidence gaps.", "Goal map"],
  ["route", "ROUTE", "Route to suspects", "Reads the full action–feedback timeline and the original modules, tests parser gaps as rival explanations, and declares a few evidence-backed anchor cases.", "Anchor cases"],
  ["trace", "TRACE", "Trace the evidence", "A manager allocates investigations. Each investigator makes one targeted query across the trajectory and reports a step-level candidate with source evidence.", "Evidence-backed candidates"],
  ["submit", "SUBMIT", "Submit the verdict", "Compares TRACE candidates using supplied evidence and selects one existing candidate with an uncertainty explanation. If TRACE supplies no candidate, SUBMIT is skipped.", "Selected hypothesis"],
];

// Hero screenshots of the workbench (web/capture_shots.py); each opens the live view.
const SHOTS = [
  ["result", "Result", "Selected hypothesis, linked evidence", caseHref(DEMO_REF, "result")],
  ["trajectory", "Trajectory", "Debugger decisions linked to agent steps", caseHref(DEMO_REF, "trajectory", "step", 4)],
  ["graph", "DSL Graph", "World state compiled from the trajectory", caseHref(DEMO_REF, "graph", "entities")],
  ["chat", "Chat", "Launch or replay a diagnosis", "#/chat"],
];

function heroShots() {
  return `<div class="shot-frame">
      <div class="shot-bar"><span class="demo-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="shot-caption"></span></div>
      <div class="shot-stage">
        ${SHOTS.map(([id, label, caption, href]) => `<a class="shot" href="${href}" data-shot="${id}">
          <img src="./img/shots/${id}.webp" width="1280" height="800" alt="${label} view: ${caption}"></a>`).join("")}
      </div>
    </div>
    <div class="shot-tabs" role="tablist" aria-label="Product views">
      ${SHOTS.map(([id, label]) => `<button type="button" role="tab" data-shot-tab="${id}">${label}<span class="shot-progress"></span></button>`).join("")}
    </div>`;
}

// Cross-fades the hero screenshots. The active tab's progress bar times each slide, so
// hovering (which pauses the bar) also pauses the rotation; no autoplay for reduced motion.
function rotateShots(box) {
  const auto = !matchMedia("(prefers-reduced-motion: reduce)").matches;
  let index = 0;
  function go(i) {
    index = (i + SHOTS.length) % SHOTS.length;
    const [id, label, caption] = SHOTS[index];
    box.querySelectorAll("[data-shot]").forEach(a => {
      const on = a.dataset.shot === id;
      a.classList.toggle("is-on", on);
      a.tabIndex = on ? 0 : -1;
      a.setAttribute("aria-hidden", String(!on));
    });
    box.querySelectorAll("[data-shot-tab]").forEach(b => {
      b.setAttribute("aria-selected", String(b.dataset.shotTab === id));
      b.classList.remove("is-timing");
    });
    if (auto) {
      const tab = box.querySelector(`[data-shot-tab="${id}"]`);
      void tab.offsetWidth; // restart the bar's animation
      tab.classList.add("is-timing");
    }
    box.querySelector(".shot-caption").textContent = `${label} · ${caption}`;
  }
  box.addEventListener("animationend", event => { if (event.target.classList.contains("shot-progress")) go(index + 1); });
  box.querySelector(".shot-tabs").addEventListener("click", event => {
    const b = event.target.closest("[data-shot-tab]");
    if (b) go(SHOTS.findIndex(s => s[0] === b.dataset.shotTab));
  });
  go(0);
}

// Read-only workbench views for DEMO_REF inside a dark app window. Tab changes and
// in-view links stay inside the window; "Open in workbench" carries the current view.
function mountDemo(section) {
  const body = section.querySelector(".demo-body");
  const open = section.querySelector(".demo-open");
  const store = new CaseStore(DEMO_REF);
  const views = createCaseViews(body, {store, ref: DEMO_REF, embedded: true, navigate: (tab, ...extra) => show({tab, extra})});
  let route = {tab: "trajectory", extra: []};

  function show(next) {
    route = next;
    const tab = views.tabFor(next.tab).id;
    section.querySelectorAll("[data-demo-tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.demoTab === tab)));
    open.href = caseHref(DEMO_REF, tab, ...next.extra);
    return views.show(next);
  }

  function unavailable(message) {
    store.stop();
    body.innerHTML = `<div class="demo-status"><strong>The demo case is not available here.</strong>
      <span>It reads <code>artifacts/dsl_debugger/${esc(DEMO_REF[1])}/${esc(DEMO_REF[2])}</code>${message ? ` (${esc(message)})` : ""}.</span></div>`;
  }

  store.on("change", ({changed, first}) => {
    if (first && store.error) { unavailable(store.error); return; }
    const model = store.model;
    section.querySelector(".demo-caption").textContent = [model.task && `“${model.task}”`, model.taskType].filter(Boolean).join(" · ");
    views.update(model, {changed, first});
    if (first) { body.querySelector(".demo-status")?.remove(); show(route); }
  });
  store.start().catch(error => unavailable(error.message));
  section.querySelector(".demo-tabs").addEventListener("click", event => {
    const b = event.target.closest("[data-demo-tab]");
    if (b) show({tab: b.dataset.demoTab, extra: []});
  });
  return {destroy() { store.stop(); views.destroy(); }};
}

export function mountHome(root) {
  root.innerHTML = `
    <div class="home">
      <header class="home-header">
        <a class="home-brand" href="#/" aria-label="ProDebugger home">
          <span class="home-mark" aria-hidden="true">[/]</span><span><strong>PRO</strong>DEBUGGER</span>
        </a>
        <nav class="home-nav" aria-label="Sections">
          <button type="button" data-scroll="capabilities">Capabilities</button>
          <button type="button" data-scroll="how">How it works</button>
          <button type="button" data-scroll="demo">Demo</button>
          <button type="button" data-scroll="use-cases">Use cases</button>
        </nav>
        <nav class="home-actions" aria-label="Pages">
          <a href="./openclaw/index.html">OpenClaw</a>
          <a href="#/runs">Runs</a>
          <a href="#/chat">Chat</a>
          <a class="h-btn primary small" href="#/runs">Open workbench</a>
        </nav>
      </header>

      <section class="home-hero">
        <div class="hero-copy" data-reveal-group>
          <h1 class="hero-title">Pro<em>Debugger</em></h1>
          <p class="hero-subtitle">Evidence-Grounded Debugging of LLM Agent Trajectories</p>
          <p class="hero-sub">Investigate why an agent failed. Inspect candidate explanations and the recorded evidence behind them.</p>
          <div class="hero-ctas">
            <a class="h-btn primary" href="#/chat">Try a live diagnosis ${ARROW}</a>
            <a class="h-btn" href="#/runs">Explore runs</a>
            <a class="h-btn ghost" href="${caseHref(DEMO_REF, "result")}">See a case ${ARROW}</a>
          </div>
        </div>
        <div class="hero-visual" data-reveal>${heroShots()}</div>
        <button type="button" class="hero-scroll" data-scroll="capabilities" aria-label="Scroll to the problem">${icon('<path d="m6 9 6 6 6-6"/>')}</button>
      </section>

      <section class="home-section" id="capabilities">
        <div class="section-head" data-reveal>
          <div class="h-eyebrow">The problem</div>
          <h2 class="section-title">Agent failures surface far from their cause.</h2>
          <p class="section-lede">When an agent fails at the last step, the decision that doomed it usually happened much earlier. Finding it by hand is slow, and an unexplained verdict is hard to act on.</p>
        </div>
        <div class="pain-row" data-reveal-group>
          ${PAINS.map(([title, text]) => `<div class="pain"><h3>${title}</h3><p>${text}</p></div>`).join("")}
        </div>
        <h3 class="solution-title" data-reveal><span class="h-eyebrow">The solution</span>ProDebugger turns a failed run into a verified diagnosis.</h3>
        <div class="cap-grid" data-reveal-group>
          ${CAPABILITIES.map(([title, paths, text]) => `<article class="cap-card">
            <span class="cap-icon">${icon(paths)}</span><h3>${title}</h3><p>${text}</p></article>`).join("")}
        </div>
      </section>

      <section class="home-section alt" id="how">
        <div class="section-head" data-reveal>
          <div class="h-eyebrow">How it works</div>
          <h2 class="section-title">Four stages, one verified verdict.</h2>
          <p class="section-lede">Each stage runs in its own isolated model context and hands a structured result to the next, so every conclusion can be traced back to the source.</p>
        </div>
        <ol class="stage-row" data-reveal-group>
          ${STAGES.map(([id, name, title, text, output], i) => `<li class="stage stage-${id}">
            <div class="stage-top"><span class="stage-num">0${i + 1}</span><span class="stage-name">${name}</span></div>
            <h3>${title}</h3><p>${text}</p>
            <div class="stage-out"><span>Output</span>${output}</div></li>`).join("")}
        </ol>
        <div class="how-more" data-reveal>
          <button type="button" class="h-btn" data-open-diagram>${icon('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>')}View the technical diagram</button>
        </div>
      </section>

      <section class="home-section" id="demo">
        <div class="section-head" data-reveal>
          <div class="h-eyebrow">Product</div>
          <h2 class="section-title">See it on a real case.</h2>
          <p class="section-lede">A household agent failed its task. Explore the diagnosis the way an engineer would: the trajectory beside the debugger's decisions, the compiled facts, and the final verdict.</p>
        </div>
        <div class="demo-window" data-reveal>
          <div class="demo-bar">
            <span class="demo-dots" aria-hidden="true"><i></i><i></i><i></i></span>
            <div class="demo-tabs" role="tablist" aria-label="Demo views">
              ${TABS.map(t => `<button type="button" role="tab" data-demo-tab="${t.id}" aria-selected="${t.id === "trajectory"}">${t.label}</button>`).join("")}
            </div>
            <span class="demo-caption"></span>
            <a class="demo-open" href="${caseHref(DEMO_REF)}">Open in workbench ${ARROW}</a>
          </div>
          <div class="demo-body"><div class="demo-status">${loader("Loading the case…")}</div></div>
        </div>
      </section>

      <section class="home-section alt" id="use-cases">
        <div class="section-head" data-reveal>
          <div class="h-eyebrow">Use cases</div>
          <h2 class="section-title">Built for agents that act.</h2>
          <p class="section-lede">ProDebugger reads the agent's own trajectory — observations, memory, reflection, plan and action — so it works wherever an agent takes steps in an environment.</p>
        </div>
        <div class="case-grid" data-reveal-group>
          ${USE_CASES.map(([domain, title, bench, text, issues]) => `<article class="case-card">
            <div class="case-top"><span class="case-icon">${domainIcon(domain)}</span><span class="case-bench">${bench}</span></div>
            <h3>${title}</h3><p>${text}</p>
            <ul class="case-issues">${issues.map(i => `<li>${i}</li>`).join("")}</ul></article>`).join("")}
        </div>
      </section>

      <footer class="home-footer">
        <span class="home-brand"><span class="home-mark" aria-hidden="true">[/]</span><span><strong>PRO</strong>DEBUGGER</span></span>
        <nav aria-label="Footer">
          <a href="#/runs">Runs</a><a href="#/chat">Chat</a><button type="button" data-scroll="how">How it works</button>
        </nav>
        <span class="home-note">Internal preview</span>
      </footer>

      <dialog class="diagram-dialog" aria-label="Technical diagram">
        <div class="diagram-bar"><strong>MAP → ROUTE → TRACE → SUBMIT · technical diagram</strong>
          <button type="button" class="h-btn small" data-close-diagram>Close</button></div>
        <img src="./img/methodology.png" alt="Pipeline from raw trajectory and canonical DSL compilation through MAP, ROUTE, TRACE and SUBMIT, with validation loops and post-run evaluation" decoding="async">
      </dialog>
    </div>`;

  rotateShots(root.querySelector(".hero-visual"));
  const dialog = root.querySelector(".diagram-dialog");
  const onClick = event => {
    const target = event.target.closest("[data-scroll]");
    if (target) root.querySelector("#" + target.dataset.scroll)?.scrollIntoView({behavior: "smooth", block: "start"});
    if (event.target.closest("[data-open-diagram]")) dialog.showModal();
    // A click on the backdrop lands on the dialog element itself.
    if (event.target.closest("[data-close-diagram]") || event.target === dialog) dialog.close();
  };
  root.addEventListener("click", onClick);
  // Blocks fade and rise into view once; children of a group follow one another.
  root.querySelectorAll("[data-reveal-group] > *").forEach(el => {
    el.dataset.reveal = "";
    el.style.setProperty("--i", [...el.parentElement.children].indexOf(el));
  });
  const reveal = new IntersectionObserver(entries => entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    entry.target.classList.add("is-in");
    reveal.unobserve(entry.target);
  }), {threshold: 0.15, rootMargin: "0px 0px -40px 0px"});
  root.querySelectorAll("[data-reveal]").forEach(el => reveal.observe(el));

  // The demo loads a whole case, so it starts only when its section comes near.
  let demo = null;
  const demoSection = root.querySelector("#demo");
  const nearDemo = new IntersectionObserver(entries => {
    if (!entries.some(e => e.isIntersecting)) return;
    nearDemo.disconnect();
    demo = mountDemo(demoSection);
  }, {rootMargin: "600px 0px"});
  nearDemo.observe(demoSection);

  window.scrollTo(0, 0);
  return {unmount() { root.removeEventListener("click", onClick); nearDemo.disconnect(); reveal.disconnect(); demo?.destroy(); }};
}
