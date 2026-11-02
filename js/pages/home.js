// Home page: a light, business-style overview with its own header. In-page links scroll
// instead of changing the hash, which belongs to the router.
import {CaseStore} from "../case/store.js?v=27bf1baa85";
import {createCaseViews, TABS} from "../case/views.js?v=links-61e40abb21";
import {caseHref} from "../routes.js?v=27bf1baa85";
import {domainIcon} from "./runs.js?v=27bf1baa85";
import {esc, loader} from "../util.js?v=27bf1baa85";
import {STATIC} from "../api.js?v=27bf1baa85";
import {PROJECT} from "../site.js?v=18001567c2";

// The showcase case: a fully correct reference run kept under artifacts/.
export const DEMO_REF = ["runs", "dsl-route-causal-timeline-high-unlimited-alf18-20261007-live", "GPT-4o_018_alfworld_task_018"];

const icon = paths => `<svg class="h-icon" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
const ARROW = icon('<path d="M5 12h14M13 6l6 6-6 6"/>');

const CAPABILITY_ICONS = {
  logs: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 7h6M9 12h6M9 17h4"/>',
  dsl: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 4l-4 16"/>',
  checks: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m7 12 3 3 7-7"/>',
  tracing: '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h8a4 4 0 0 1 0 8H9a3 3 0 0 0 0 6h8"/>',
};

const HERO_CAPABILITIES = [
  ["Structured Log Representation", CAPABILITY_ICONS.logs],
  ["Interactive DSL Queries", CAPABILITY_ICONS.dsl],
  ["Programmatic Checks", CAPABILITY_ICONS.checks],
  ["Workflow-Driven Tracing", CAPABILITY_ICONS.tracing],
];

const CLOUD_CASE_HREF = "#/query/evidence-query-claims-n30-20261013/GPT-4o_024_alfworld_task_024";

function heroCapabilities() {
  return `<ul class="hero-capabilities" aria-label="Core capabilities">
    ${HERO_CAPABILITIES.map(([title, paths]) => `<li class="hero-capability">${icon(paths)}<span>${title}</span></li>`).join("")}
  </ul>`;
}

// Prospective research directions, distinct from the demonstrated workbench capabilities.
const RESEARCH_DIRECTIONS = [
  ["rsi", "Recursive Self-Improvement (RSI)", '<path d="M20 7a8 8 0 0 0-14-2L3 8M3 3v5h5M4 17a8 8 0 0 0 14 2l3-3M21 21v-5h-5"/>',
   ["Failure-driven refinement", "Skill evolution", "Closed-loop evaluation"]],
  ["gov", "Agent Governance", '<path d="m12 3-7 3v6c0 4 3 7 7 9 4-2 7-5 7-9V6z"/><path d="m9 12 2 2 4-4"/>',
   ["Policy compliance", "Decision accountability", "Auditable evidence"]],
  ["serving", "Efficient Serving", '<path d="m13 2-9 12h7l-1 8L21 9h-8z"/>',
   ["Selective evidence retrieval", "Budget-aware orchestration", "Lower-latency diagnosis"]],
  ["privacy", "Privacy Preservation", '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
   ["Sensitive-data tracing", "Disclosure detection", "Privacy-aware diagnosis"]],
];

const MECHANISMS = [
  ["logs", "Log Representation", ["Entity-level facts", "Symbolic predicates", "Source-linked evidence"]],
  ["dsl", "Interactive DSL Queries", ["Read-only queries", "Cross-step retrieval", "Evidence provenance"]],
  ["checks", "Programmatic Checks", ["Claim consistency", "Action applicability", "Task completion"]],
  ["tracing", "Workflow-Driven Tracing", ["Hypothesis generation", "Targeted investigation", "Candidate comparison"]],
];

// Project-site links (static export only): paper, code and a video that plays in a dialog.
const LINK_ICONS = {
  paper: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
  code: '<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>',
  video: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
};

function videoSources() {
  const {youtube, bilibili, mp4} = PROJECT.video;
  const yt = youtube && (youtube.match(/(?:youtu\.be\/|v=|embed\/)([\w-]{11})/)?.[1] || youtube);
  const bv = bilibili && (bilibili.match(/BV\w{10}/)?.[0] || bilibili);
  return [
    mp4 && ["mp4", "Video", `<video controls preload="metadata" playsinline src="${esc(mp4)}"></video>`],
    yt && ["youtube", "YouTube", `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(yt)}" title="ProDebugger video on YouTube" allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen loading="lazy"></iframe>`],
    bv && ["bilibili", "Bilibili", `<iframe src="https://player.bilibili.com/player.html?bvid=${encodeURIComponent(bv)}&autoplay=0&high_quality=1" title="ProDebugger video on Bilibili" allow="fullscreen" allowfullscreen loading="lazy"></iframe>`],
  ].filter(Boolean);
}

function projectLinks() {
  const link = (id, label, href, external = true) => href
    ? `<a class="paper-link" href="${esc(href)}"${external ? ' target="_blank" rel="noopener"' : ""}>${icon(LINK_ICONS[id])}${label}</a>`
    : `<span class="paper-link is-soon" aria-disabled="true">${icon(LINK_ICONS[id])}${label}<small>soon</small></span>`;
  return `<div class="paper-links">${link("paper", "Paper", PROJECT.paper)}${link("code", "Code", PROJECT.code)}${videoSources().length
    ? `<button type="button" class="paper-link" data-open-video>${icon(LINK_ICONS.video)}Video</button>` : link("video", "Video", "")}</div>`;
}

// The video stays folded behind the Video buttons and plays in a dialog over the page.
function videoDialog() {
  const sources = videoSources();
  if (!sources.length) return "";
  return `<dialog class="video-dialog" aria-label="ProDebugger video">
    <div class="video-dialog-bar"><strong>ProDebugger in action</strong>
      ${sources.length > 1 ? `<div class="video-tabs" role="tablist" aria-label="Video source">${sources.map(([id, label], i) =>
        `<button type="button" role="tab" data-video="${id}" aria-selected="${i === 0}">${label}</button>`).join("")}</div>` : ""}
      <button type="button" class="h-btn small" data-close-video>Close</button></div>
    <div class="video-frame"></div>
  </dialog>`;
}

// Hero screenshots of the workbench (web/capture_shots.py); each opens the live view. Each one is
// cropped to a single feature and letterboxed on its own background colour, so it fits whole at
// any stage size.
const SHOTS = [
  ["trajectory", "Trajectory", "Debugger decisions linked to agent steps", caseHref(DEMO_REF, "trajectory", "step", 4)],
  ["facts", "DSL Facts", "Agent log compiled into facts, S04 lineage traced", caseHref(DEMO_REF, "graph", "facts", 48), "#0d131a"],
  ["rules", "Rule Checks", "Rule violations grounded in cited facts", caseHref(DEMO_REF, "graph", "rules"), "#0d131a"],
  ["root-cause", "Root Cause", "Selected hypothesis, evidence and repair", caseHref(DEMO_REF, "result"), "#080b0f"],
];

// The original GIF composition, packaged as a same-origin static interactive view.
const CLOUD_PREVIEW = "./home-cloud.html?v=20261030-compact2";

function heroShots() {
  return `<div class="shot-frame">
      <div class="shot-bar"><span class="demo-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="shot-caption"></span></div>
      <div class="shot-stage">
        ${SHOTS.map(([id, label, caption, href, bg]) => id === "trajectory"
          ? `<div class="shot" data-shot="${id}"><iframe src="${CLOUD_PREVIEW}" title="Interactive evidence cloud" style="display:block;width:100%;height:100%;border:0" loading="eager"></iframe></div>`
          : `<a class="shot" href="${href}" data-shot="${id}" style="background:${bg}">
          <img src="./img/shots/${id}.webp?v=20261102" width="1400" height="960" alt="${label} view: ${caption}" loading="lazy"></a>`).join("")}
      </div>
    </div>
    <div class="shot-tabs" role="tablist" aria-label="Product views">
      ${SHOTS.map(([id, label]) => `<button type="button" role="tab" data-shot-tab="${id}">${label}<span class="shot-progress"></span></button>`).join("")}
    </div>`;
}

// Pages through the hero screenshots: the current one slides out as the next slides in,
// leftward when moving forward (auto-advance wraps forward too) and rightward when a tab
// further back is picked. The active tab's progress bar times each slide, so hovering
// (which pauses the bar) also pauses the rotation; no autoplay for reduced motion.
function rotateShots(box) {
  // Keep the cloud loop visible; the existing four tabs remain manually selectable.
  const auto = false;
  const shots = [...box.querySelectorAll("[data-shot]")];
  let index = -1;
  function go(i, dir = 1) {
    const next = (i + SHOTS.length) % SHOTS.length;
    if (next === index) return;
    const [id, label, caption] = SHOTS[next];
    const from = shots[index], to = shots[next];
    if (from) {
      // Park the incoming page on the side it enters from, then let both slide.
      to.style.transition = "none";
      to.style.transform = `translateX(${dir * 100}%)`;
      void to.offsetWidth;
      to.style.transition = to.style.transform = "";
      from.style.transform = `translateX(${-dir * 100}%)`;
    }
    index = next;
    shots.forEach(a => {
      const on = a === to;
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
    const i = b ? SHOTS.findIndex(s => s[0] === b.dataset.shotTab) : -1;
    if (i >= 0) go(i, i < index ? -1 : 1);
  });
  go(SHOTS.findIndex(([id]) => id === "trajectory"));
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
          ${STATIC && videoSources().length ? '<button type="button" data-open-video>Video</button>' : ""}
          <button type="button" data-scroll="how">How it works</button>
          <button type="button" data-scroll="demo">Demo</button>
          <button type="button" data-scroll="next">What's next</button>
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
          <div class="hero-eyebrow"><span></span> Evidence-grounded agent debugging</div>
          <h1 class="hero-title"><img class="hero-title-mark" src="./img/prodebugger-mark-animated.svg" width="96" height="96" alt="" aria-hidden="true"><span class="hero-title-word">Pro<em>Debugger</em></span></h1>
          <p class="hero-subtitle">Evidence-Grounded Debugging of LLM Agent Trajectories</p>
          <p class="hero-sub">Investigate why an agent failed. Inspect candidate explanations and the recorded evidence behind them.</p>
          ${STATIC ? projectLinks() : ""}
          ${heroCapabilities()}
          <div class="hero-ctas">
            <a class="h-btn primary" href="${CLOUD_CASE_HREF}">Explore ProDebugger ${ARROW}</a>
            <a class="h-btn hero-openclaw-cta" href="./openclaw/index.html">A case from OpenClaw ${ARROW}</a>
          </div>
        </div>
        <div class="hero-visual" data-reveal>${heroShots()}</div>
        <button type="button" class="hero-scroll" data-scroll="how" aria-label="Scroll down">${icon('<path d="m6 9 6 6 6-6"/>')}</button>
      </section>

      <section class="home-section alt" id="how">
        <div class="section-head how-heading" data-reveal>
          <h2 class="section-title">How it works</h2>
          <button type="button" class="how-diagram" data-open-diagram>Technical diagram ${ARROW}</button>
        </div>
        <ol class="stage-row" aria-label="Technical mechanisms" data-reveal-group>
          ${MECHANISMS.map(([id, title, points], i) => `<li class="stage">
            <div class="stage-heading">${icon(CAPABILITY_ICONS[id])}<span class="stage-num" aria-hidden="true">0${i + 1}</span></div>
            <h3>${title}</h3>
            <ul class="stage-points">${points.map(point => `<li>${point}</li>`).join("")}</ul>
          </li>`).join("")}
        </ol>
      </section>

      <section class="home-section" id="demo">
        <div class="section-head" data-reveal>
          <div class="h-eyebrow">Product</div>
          <h2 class="section-title">See it on a real case.</h2>
          <p class="section-lede">Inspect an agent failure through its trajectory, evidence graph, and diagnosis.</p>
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

      <section class="home-finale" id="next">
        <svg class="finale-trail" viewBox="0 0 1200 160" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0 120 C 120 120 160 40 280 48 S 440 130 560 104 S 740 20 860 36 S 1060 120 1200 70"/>
          ${[[280, 48], [560, 104], [860, 36], [1200, 70]].map(([x, y], i) => `<circle cx="${x}" cy="${y}" r="5" style="--i:${i}"/>`).join("")}
        </svg>
        <div class="finale-inner">
          <div class="section-head" data-reveal>
            <div class="h-eyebrow">Ongoing research</div>
            <h2 class="section-title">Research Frontiers in<br>Agent Debugging</h2>
          </div>
          <div class="scene-grid" data-reveal-group>
            ${RESEARCH_DIRECTIONS.map(([id, title, paths, points]) => `<article class="scene scene-${id}">
              <div class="scene-heading">${icon(paths)}<h3>${title}</h3></div>
              <ul class="scene-points">${points.map(point => `<li>${point}</li>`).join("")}</ul>
            </article>`).join("")}
          </div>
          <div class="finale-bottom">
            <div class="related" data-reveal>
              <span class="h-eyebrow">Related work from our group</span>
              <ul class="related-list">
                ${(PROJECT.related || []).map(({name, tagline, venue, href}) => `<li>${href
                  ? `<a class="related-item" href="${esc(href)}" target="_blank" rel="noopener">`
                  : '<div class="related-item">'}
                  <span class="related-glyph" aria-hidden="true">${esc(name.slice(0, 1))}</span>
                  <span class="related-copy"><strong>${esc(name)}</strong>${tagline ? `<span>${esc(tagline)}</span>` : ""}</span>
                  ${venue ? `<small>${esc(venue)}</small>` : ""}${href ? ARROW + "</a>" : "</div>"}</li>`).join("")}
              </ul>
            </div>
            <div class="qr-card" data-reveal>
              <a class="qr-frame" href="${esc(PROJECT.paper)}" target="_blank" rel="noopener" aria-label="Read the ProDebugger paper"><img src="./img/qr-paper.svg" alt="QR code linking to the ProDebugger paper" width="168" height="168"></a>
              <div class="qr-copy">
                <span class="h-eyebrow">Scan to read</span>
                <strong>The ProDebugger paper</strong>
                <p class="qr-contact">Contact: <a href="mailto:haiyan_yin@a-star.edu.sg">haiyan_yin@a-star.edu.sg</a></p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <footer class="home-footer">
        <span class="home-brand"><span class="home-mark" aria-hidden="true">[/]</span><span><strong>PRO</strong>DEBUGGER</span></span>
        <nav aria-label="Footer">
          <a href="#/runs">Runs</a><a href="#/chat">Chat</a><button type="button" data-scroll="how">How it works</button>
        </nav>
        ${STATIC ? '<span class="home-note">Recorded examples · no live model calls</span>' : '<span class="home-note">Internal preview</span>'}
      </footer>

      ${STATIC ? videoDialog() : ""}
      <dialog class="diagram-dialog" aria-label="Technical diagram">
        <div class="diagram-bar"><strong>MAP → ROUTE → TRACE → SUBMIT · technical diagram</strong>
          <button type="button" class="h-btn small" data-close-diagram>Close</button></div>
        <img src="./img/methodology.png" alt="Pipeline from raw trajectory and canonical DSL compilation through MAP, ROUTE, TRACE and SUBMIT, with validation loops and post-run evaluation" decoding="async">
      </dialog>
    </div>`;

  rotateShots(root.querySelector(".hero-visual"));
  const dialog = root.querySelector(".diagram-dialog");
  const videoBox = root.querySelector(".video-dialog");
  const loadVideo = id => {
    const sources = videoSources();
    const [, , player] = sources.find(([key]) => key === id) || sources[0];
    videoBox.querySelector(".video-frame").innerHTML = player;
    videoBox.querySelectorAll("[data-video]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.video === (id || sources[0][0]))));
    // The opening click allows playback with sound.
    videoBox.querySelector("video")?.play().catch(() => {});
  };
  // Closing empties the frame so nothing keeps playing. Esc arrives as "cancel" and is
  // handled the same way, synchronously (the async "close" event can be coalesced).
  const closeVideo = () => { videoBox.close(); videoBox.querySelector(".video-frame").innerHTML = ""; };
  videoBox?.addEventListener("cancel", event => { event.preventDefault(); closeVideo(); });
  const onClick = event => {
    const target = event.target.closest("[data-scroll]");
    if (target) root.querySelector("#" + target.dataset.scroll)?.scrollIntoView({behavior: "smooth", block: "start"});
    if (videoBox && event.target.closest("[data-open-video]")) { videoBox.showModal(); loadVideo(); }
    if (event.target.closest("[data-open-diagram]")) dialog.showModal();
    const source = event.target.closest("[data-video]");
    if (source) loadVideo(source.dataset.video);
    if (event.target.closest("[data-close-video]") || (videoBox && event.target === videoBox)) closeVideo();
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
