// Lazily mounted case views (Trajectory / DSL Graph / Result) kept in sync with a CaseStore.
// Shared by the workbench and the read-only demo on the home page.
export const TABS = [
  {id: "trajectory", label: "Trajectory", load: () => import("../views/trajectory.js?v=b596342864")},
  {id: "graph", label: "DSL Graph", load: () => import("../views/graph.js?v=27bf1baa85")},
  {id: "result", label: "Result", load: () => import("../views/result.js?v=27bf1baa85"), runOnly: true},
];

// ctx: {store, ref, navigate(tab, ...extra), embedded?} is handed to every view.
export function createCaseViews(container, ctx) {
  const views = new Map();
  const pending = new Map();
  let latest = null;

  const tabFor = id => {
    const tab = TABS.find(t => t.id === id) || TABS[0];
    return tab.runOnly && ctx.store.kind !== "run" ? TABS[0] : tab;
  };

  async function mount(tab) {
    const el = document.createElement("section");
    el.className = `wb-view view-${tab.id}`;
    el.hidden = true;
    container.append(el);
    const module = await tab.load();
    const view = module.createView(el, ctx);
    views.set(tab.id, {el, view});
    if (ctx.store.model) view.update(ctx.store.model, {changed: new Set(["all"]), first: true});
  }

  return {
    tabFor,
    // route: {tab, extra}; resolves once the tab is mounted and shown.
    async show(route) {
      latest = route;
      const tab = tabFor(route.tab);
      if (!pending.has(tab.id)) pending.set(tab.id, mount(tab));
      await pending.get(tab.id);
      if (latest !== route) return; // a newer navigation won
      views.forEach((v, id) => { v.el.hidden = id !== tab.id; });
      views.get(tab.id).view.show?.(route);
    },
    update(model, info) { views.forEach(v => v.view.update(model, info)); },
    destroy() { views.forEach(v => v.view.destroy?.()); },
  };
}
