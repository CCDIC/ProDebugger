// Case workbench: summary header, phase strip and lazily mounted views.
import {CaseStore} from "../case/store.js?v=27bf1baa85";
import {CALL_PHASES} from "../case/calls.js?v=27bf1baa85";
import {createCaseViews, TABS} from "../case/views.js?v=links-61e40abb21";
import {caseHref} from "../routes.js?v=27bf1baa85";
import {esc, loader, number, statusBadge} from "../util.js?v=27bf1baa85";

export function mountWorkbench(root, route) {
  const store = new CaseStore(route.ref);
  let currentRoute = route;

  root.innerHTML = `
    <section class="wb-head">
      <div class="wb-title">
        <div class="eyebrow" id="wb-kind">${store.kind === "run" ? "Debugger run" : "Compiled session"}</div>
        <h1 id="wb-task" class="wb-task">&nbsp;</h1>
        <div class="wb-badges" id="wb-badges"></div>
      </div>
      <dl class="wb-metrics" id="wb-metrics"></dl>
    </section>
    <ol class="phase-strip" id="phase-strip" aria-label="Debugger phases"></ol>
    <nav class="wb-tabs" role="tablist" aria-label="Workbench views">
      ${TABS.map(t => `<a role="tab" class="wb-tab" data-tab="${t.id}" href="${caseHref(route.ref, t.id)}">${t.label}</a>`).join("")}
      <span class="wb-tabs-fill"></span>
      <span class="wb-sync" id="wb-sync"></span>
    </nav>
    <div id="wb-views" class="wb-views">${loader("Loading case…")}</div>`;
  const $ = id => root.querySelector("#" + id);

  function renderHeader(model) {
    const d = model.debugger;
    const result = d?.result;
    $("wb-task").textContent = model.task || model.trajectoryId || "—";
    $("wb-kind").textContent = `${store.kind === "run" ? "Debugger run" : "Compiled session"} · ${model.taskType || "—"}`;
    const status = store.live ? "running" : store.kind === "session" ? "compiled" : result?.status || "incomplete";
    $("wb-badges").innerHTML = [
      statusBadge(status, !store.live && result?.accepted),
      d?.manifest?.config?.model ? `<span class="badge">${esc(d.manifest.config.model)}</span>` : "",
      d?.manifest?.mode ? `<span class="badge">${esc(d.manifest.mode)}</span>` : "",
      `<span class="badge mono" title="trajectory id">${esc(model.trajectoryId || "")}</span>`,
    ].join("");
    const usage = d?.usage || {};
    const tokens = d ? (usage.input_tokens != null ? usage.input_tokens + (usage.output_tokens || 0)
      : (d.calls.reduce((s, c) => s + (c.usage?.input_tokens || 0) + (c.usage?.output_tokens || 0), 0))) : null;
    const metrics = [
      ["Steps", model.steps.length], ["Facts", model.dsl?.facts.length ?? 0], ["Entities", Object.keys(model.dsl?.entities || {}).length],
      ...(d ? [["Model calls", d.calls.length], ["Tokens", number(tokens)]] : [["Evaluations", model.evaluations.length]]),
    ];
    $("wb-metrics").innerHTML = metrics.map(([k, v]) => `<div><dt class="eyebrow">${k}</dt><dd>${v}</dd></div>`).join("");
    const strip = $("phase-strip");
    if (!d) { strip.hidden = true; return; }
    const reached = new Set(d.calls.map(c => c.phase));
    const last = d.calls[d.calls.length - 1]?.phase;
    strip.innerHTML = CALL_PHASES.filter(p => p !== "PARSE").map(p => {
      const count = d.calls.filter(c => c.phase === p).length;
      const state = store.live && p === last ? "is-current" : reached.has(p) ? "is-done" : "";
      return `<li class="phase-pill phase-${p} ${state}"><span class="phase-dot"></span><strong>${p}</strong><span class="phase-count">${count} call${count === 1 ? "" : "s"}</span></li>`;
    }).join('<li class="phase-arrow" aria-hidden="true">→</li>');
  }

  function renderSync() {
    $("wb-sync").innerHTML = store.error ? `<span class="badge bad">${esc(store.error)}</span>`
      : store.live ? `<span class="badge live"><span class="live-dot"></span>Live · following writes</span>`
      : `<span class="sync-idle">Snapshot · ${new Date().toLocaleTimeString("en-GB")}</span>`;
  }

  const views = createCaseViews($("wb-views"), {store, ref: route.ref,
    navigate: (t, ...extra) => { location.hash = caseHref(route.ref, t, ...extra); }});

  function show(routeNow) {
    currentRoute = routeNow;
    const tab = views.tabFor(routeNow.tab);
    root.querySelectorAll(".wb-tab").forEach(a => {
      const on = a.dataset.tab === tab.id;
      a.setAttribute("aria-selected", String(on));
      a.classList.toggle("is-active", on);
      a.hidden = Boolean(TABS.find(x => x.id === a.dataset.tab).runOnly && store.kind !== "run");
    });
    return views.show(routeNow);
  }

  store.on("change", ({changed, first}) => {
    renderHeader(store.model);
    renderSync();
    views.update(store.model, {changed, first});
    if (first) { $("wb-views").querySelector(":scope > .loader")?.remove(); show(currentRoute); }
  });
  store.start().catch(error => {
    $("wb-views").innerHTML = `<div class="error-box">Could not load this case: ${esc(error.message)}</div>`;
  });

  return {
    update: show,
    unmount() { store.stop(); views.destroy(); },
  };
}
