// Hash router: #/ is the home page; #/runs lists runs; #/case/<source>/<group>/<trajectory>/<tab>
// opens the workbench; #/chat[/<group>/<trajectory>] opens the chat page.
import {esc} from "./util.js?v=27bf1baa85";
import {parseRoute, runsHref} from "./routes.js?v=27bf1baa85";
import {mountHome} from "./pages/home.js?v=links-61e40abb21";
import {mountRuns} from "./pages/runs.js?v=27bf1baa85";
import {mountChat} from "./pages/chat.js?v=27bf1baa85";
import {mountWorkbench} from "./pages/workbench.js?v=links-61e40abb21";
import {mountQuery} from "./pages/query.js?v=23f9bfd6b4";
import {initTheme, setThemePage} from "./theme.js?v=27bf1baa85";

const app = document.getElementById("app");
const crumbs = document.getElementById("crumbs");
const nav = document.getElementById("site-nav");
let current = null;

function setCrumbs(route) {
  nav.querySelectorAll("a").forEach(a => a.setAttribute("aria-current", String(a.dataset.page === (route.page === "case" ? "runs" : route.page))));
  if (route.page === "query") {
    crumbs.innerHTML = route.ref ? `<span class="sep">/</span><span class="current" title="${esc(route.ref[1])}">${esc(route.ref[1])}</span>` : "";
    return;
  }
  if (route.page === "chat") {
    crumbs.innerHTML = route.ref ? `<span class="sep">/</span><span class="current" title="${esc(route.ref[2])}">${esc(route.ref[2])}</span>` : "";
    return;
  }
  if (route.page !== "case") { crumbs.innerHTML = ""; return; }
  const [source, group, trajectory] = route.ref;
  crumbs.innerHTML = `<span class="sep">/</span><a href="${runsHref(group)}">${source === "sessions" ? "session" : "run"} · ${esc(group)}</a><span class="sep">/</span><span class="current" title="${esc(trajectory)}">${esc(trajectory)}</span>`;
}

function render() {
  // The runs list used to live at #/?group=…; keep those links working.
  if (/^#\/?\?/.test(location.hash)) { location.replace("#/runs" + location.hash.replace(/^#\/?/, "")); return; }
  const route = parseRoute();
  // The home page brings its own light header and full-bleed layout.
  document.body.classList.toggle("page-home", route.page === "home");
  document.body.classList.toggle("page-query", route.page === "query");
  setThemePage(route.page === "home", route.page === "query" ? "dark" : null);
  setCrumbs(route);
  // Switching tabs inside the same case keeps the loaded store alive.
  // The chat page keeps its sidebar and composer while switching conversations.
  const key = route.page === "case" ? route.ref.join("/") : route.page;
  if (current && current.key === key && current.update) { current.update(route); return; }
  current?.unmount?.();
  app.innerHTML = "";
  const mount = {case: mountWorkbench, query: mountQuery, chat: mountChat, runs: mountRuns, home: mountHome}[route.page];
  current = {key, ...mount(app, route)};
}

window.addEventListener("hashchange", render);
initTheme();
render();
