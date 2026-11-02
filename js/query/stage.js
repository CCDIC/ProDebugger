// Zero-dependency 3D stage for the cluster tower: a perspective orbit camera over a 2D canvas.
// Flat square points, 1px lines and HUD brackets; the only motion is camera easing and points
// moving to their place when a cloud appears, expands or folds.
import {ballPoint, cloudRadius, PITCH, TIERS} from "./layout.js?v=e6d93658f9";
import {factLabel, factRoles, ROLE_ORDER} from "./clusters.js?v=e6d93658f9";

const TOKENS = ["void", "ink", "ink-hi", "ink-dim", "line", "line-hi", "focus", "label", "label-dim", "issue", "cand"];
const PHASE_TOKENS = {PARSE: "parse", MAP: "map", ROUTE: "route", MANAGER: "manager", TRACE: "trace", SUBMIT: "submit"};
const readColors = () => {
  const css = getComputedStyle(document.documentElement);
  const read = name => css.getPropertyValue(name).trim() || "#888";
  return {
    ...Object.fromEntries(TOKENS.map(t => [t, read(`--qg-${t}`)])),
    phase: Object.fromEntries(Object.entries(PHASE_TOKENS).map(([k, t]) => [k, read(`--${t}`)])),
  };
};
const MAX_FACTS = 26;     // facts listed when a group opens; the inspector has the rest
const FACT_GAP = 0.5;     // world units between fact rows
const POINT_DIM = 0.32;   // brightness of plain points in a cloud nobody is looking at
const POINT_LIFT = 0.65;  // ... in the focused or selected cloud
const SIDE_SCALE = 0.45;  // size of a cloud off the main chain while nobody looks at it
const TOUR_NEAR = 20;     // closest a tour shot comes when it fits the clouds shown so far
const CORE_POINTS = 140;  // points the folded record cloud keeps (plus every role point)
const LABEL_ROOM = 380;   // px kept free each side of an upright journey for its stop labels
const LABEL_ROW = 140;    // px kept free above and below a journey lying across
// Canvas type: names read in Plex Sans, counts and the DSL's own text in Plex Mono.
const SANS = "'IBM Plex Sans', sans-serif", MONO = "'IBM Plex Mono', monospace";
const pxOf = font => Number(font.match(/([\d.]+)px/)[1]);
const lineH = font => Math.round(pxOf(font) * 1.3);
// A detail line is a count ("85 facts") in Mono, a phrase ("ignored a task constraint") in Sans.
const detailFont = t => `15px ${/^\d/.test(t) ? MONO : SANS}`;

export function createStage(canvas, scene, model, layout) {
  const ctx = canvas.getContext("2d");
  const colors = readColors();
  const {clusters, byId} = model;
  const cam = {target: [0, 7, 0], yaw: 0.5, pitch: 0.32, dist: 48, fov: 0.8};
  let goal = {...cam, target: [...cam.target]};
  let tourGoal = goal, manual = false;
  let frame = {visible: new Set(["core"]), filled: new Set(["core"]), focus: new Set(), appear: new Map(), chain: null, labelAll: false, filter: null};
  const view = {cluster: null, group: null, basis: null, fact: null, selected: null};
  let hover = null;
  let targets = [];
  // A fact's journey (journeyOf) drawn as one numbered line through the clouds that returned
  // it; focus is the stop the camera is on, or null for the whole line.
  let journey = null;
  // Facts the Story board is talking about: each mark recolours its points wherever they are
  // drawn and puts its number once, beside the copy in a focused cloud if there is one.
  let markOf = new Map(), marks = [], badgeAt = [];
  let rot = false;
  let W = 0, H = 0, dpr = 1, raf = 0, last = 0, onFrame = null, B = null;
  const listeners = {hover: [], select: [], manual: [], view: [], journey: [], back: [], details: []};
  let evidenceHighlight = new Set();
  const emit = (k, v) => listeners[k].forEach(fn => fn(v));

  // Point instances per cloud; a cloud's points start at the core and travel out to it.
  const inst = new Map(clusters.map(c => [c.id, c.fids.map((fid, k) => ({fid, k, cur: null}))]));
  // A fact's evidence role, counted only from clouds already on screen.
  const roles = factRoles(clusters);
  const roleColor = {issue: colors.issue, candidate: colors.cand, root: colors.focus};
  function roleOf(fid) {
    let best = -1;
    for (const r of roles.get(fid) || []) if (frame.visible.has(r.id)) best = Math.max(best, ROLE_ORDER.indexOf(r.role));
    return best < 0 ? null : ROLE_ORDER[best];
  }
  const phaseColor = c => colors.phase[c.phaseKey] || colors.label;
  const groupOf = new Map(clusters.map(c => [c.id, new Map(c.groups.flatMap((g, gi) => g.fids.map(fid => [fid, gi])))]));
  // The main chain the overview lights: record → query → window → lane → candidate → root.
  // Walk down from the root through its first link; at the window take the query that gave it
  // most of its clues.
  const backbone = new Set();
  for (let c = byId.get("root"); c; ) {
    backbone.add(c.id);
    const links = c.links.filter(l => byId.has(l.from));
    if (c.kind === "window") { const q = [...links].sort((a, b) => b.weight - a.weight)[0]; if (q) backbone.add(q.from); break; }
    c = links[0] && byId.get(links[0].from);
    if (c && (c.kind === "core" || c.kind === "query")) { backbone.add(c.id); break; }
  }
  if (backbone.size) backbone.add("core");
  // Depth fog: clouds behind the tower's middle fade, those in front stay full.
  const fog = z => 1 - 0.55 * Math.max(0, Math.min(1, (z - cam.dist + 9) / 18));
  // Entity hubs travel like points; opened, a hub sits on its entity group (or the "+more" one).
  const hubInst = new Map(clusters.map(c => [c.id, c.structure.hubs.map(() => ({cur: null}))]));
  const hubGroup = new Map(clusters.map(c => {
    const other = c.groups.findIndex(g => g.other);
    return [c.id, c.structure.hubs.map(h => { const gi = c.groups.findIndex(g => g.key === h.key); return gi >= 0 ? gi : other; })];
  }));

  function resize() {
    dpr = Math.min(2, window.devicePixelRatio || 1);
    const r = canvas.getBoundingClientRect();
    W = r.width; H = r.height;
    // A landscape canvas lays the tower on its side: the funnel reads left to right.
    rot = W > H * 1.05;
    const pitch = rot ? PITCH.lying : PITCH.upright;
    if (layout.pitch !== pitch) layout.relayout(pitch);
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  }
  function basis() {
    const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const eye = [cam.target[0] + cam.dist * cp * Math.sin(cam.yaw), cam.target[1] + cam.dist * sp, cam.target[2] + cam.dist * cp * Math.cos(cam.yaw)];
    const f = norm(sub(cam.target, eye)), r = norm(cross(f, [0, 1, 0]));
    return {eye, f, r, u: cross(r, f), focal: H / (2 * Math.tan(cam.fov / 2))};
  }
  function project(p) {
    const d = sub(p, B.eye), z = dot(d, B.f);
    if (z < 0.5) return null;
    const a = B.focal * dot(d, B.r) / z, b = B.focal * dot(d, B.u) / z;
    // On its side the picture turns a quarter clockwise: the camera's up points right.
    return rot ? {x: W / 2 + b, y: H / 2 + a, z, s: B.focal / z} : {x: W / 2 + a, y: H / 2 - b, z, s: B.focal / z};
  }
  // World directions that read as screen right and screen down for a camera frame {r, u}.
  const screenRight = ({r, u}) => rot ? u : r;
  const screenDown = ({r, u}) => rot ? r : scale(u, -1);

  // Expanded layout: entity groups on a ring in the camera plane around the cloud, the open
  // group's facts as a column beside it.
  const groupRing = c => layout.radius.get(c.id) + 1.6 + 0.12 * c.groups.length;
  // Inside an open cloud each entity is a hub with its facts on spokes around it, in step order,
  // laid out like a loose sunflower: golden-angle turns, spokes growing with the square root of
  // the order, each angle and length nudged by a hash of the fact, so a star reads as one
  // entity's facts without looking stamped.
  const SPOKE_R0 = 0.3, SPOKE_GROW = 0.13;
  const radialExtent = n => n ? SPOKE_R0 + SPOKE_GROW * Math.sqrt(n - 1) * 1.15 + 0.05 : 0;
  function radialPoint(c, gi, k, n, fid) {
    const h1 = hash01(fid * 7 + 3), h2 = hash01(fid * 13 + 11), tilt = hash01(gi * 31 + c.groups.length) * Math.PI * 2;
    const a = tilt + k * 2.39996 + (h1 - 0.5) * 0.5;
    const R = (SPOKE_R0 + SPOKE_GROW * Math.sqrt(k)) * (0.85 + 0.3 * h2);
    const {r, u} = view.basis;
    return add(groupCenter(c, gi), add(scale(r, R * Math.cos(a)), scale(u, R * Math.sin(a))));
  }
  // Ties between two entity groups of a cloud: how many of one group's facts also name the
  // other's entity. Drawn as one line per pair instead of a line per fact.
  const groupTies = new Map();
  function tiesOf(c) {
    if (groupTies.has(c.id)) return groupTies.get(c.id);
    const own = groupOf.get(c.id), hubs = hubGroup.get(c.id), other = c.groups.findIndex(g => g.other);
    const pairs = new Map();
    for (const [fid, j] of c.structure.spokes) {
      const a = own.get(fid), b = hubs[j];
      if (a == null || b == null || b < 0 || a === b || a === other || b === other) continue;
      const key = a < b ? `${a}:${b}` : `${b}:${a}`;
      if (!pairs.has(key)) pairs.set(key, new Set());
      pairs.get(key).add(fid);
    }
    const list = [...pairs].map(([key, set]) => [...key.split(":").map(Number), set.size]);
    groupTies.set(c.id, list);
    return list;
  }
  function groupCenter(c, gi) {
    const [x, y] = groupPlan(c)[gi], {r, u} = view.basis;
    return add(layout.pos.get(c.id), add(scale(r, x), scale(u, y)));
  }
  // Where an open cloud's entity groups sit in the camera plane: seeded on a ring, then a short
  // deterministic force pass, as the DSL Graph's entity view does: groups that share facts pull
  // together, every pair keeps room for its star and name, and none falls into the cloud's
  // centre. Computed once per cloud.
  const groupPlans = new Map();
  function groupPlan(c) {
    if (groupPlans.has(c.id)) return groupPlans.get(c.id);
    const n = c.groups.length, R = groupRing(c), inner = layout.radius.get(c.id) + 0.6;
    const pts = c.groups.map((g, gi) => {
      const a = Math.PI / 2 - gi / n * Math.PI * 2 + (hash01(gi + 5) - 0.5) * 0.35;
      const d = R * (0.9 + 0.2 * hash01(gi + 17));
      return [d * Math.cos(a), d * Math.sin(a)];
    });
    const ext = c.groups.map(g => radialExtent(g.fids.length));
    const ties = tiesOf(c), most = Math.max(1, ...ties.map(t => t[2]));
    for (let it = 0; it < 260; it++) {
      const step = 0.5 * (1 - it / 260) + 0.05;
      const f = pts.map(() => [0, 0]);
      for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) {
        const dx = pts[b][0] - pts[a][0], dy = pts[b][1] - pts[a][1], d = Math.hypot(dx, dy) || 0.01;
        const room = ext[a] + ext[b] + 1.1;
        const push = d < room ? (room - d) * 0.5 : 0.04 / (d * d);
        f[a][0] -= dx / d * push; f[a][1] -= dy / d * push; f[b][0] += dx / d * push; f[b][1] += dy / d * push;
      }
      for (const [a, b, k] of ties) {
        const dx = pts[b][0] - pts[a][0], dy = pts[b][1] - pts[a][1], d = Math.hypot(dx, dy) || 0.01;
        const pull = Math.max(0, d - (ext[a] + ext[b] + 1.3)) * 0.04 * (0.4 + k / most);
        f[a][0] += dx / d * pull; f[a][1] += dy / d * pull; f[b][0] -= dx / d * pull; f[b][1] -= dy / d * pull;
      }
      pts.forEach((p, i) => {
        const d = Math.hypot(...p) || 0.01;
        // A soft spring to the seed ring keeps the whole open cloud round-ish.
        const spring = (R - d) * 0.03;
        p[0] += (f[i][0] + p[0] / d * spring) * step;
        p[1] += (f[i][1] + p[1] / d * spring) * step;
        const e = Math.hypot(...p) || 0.01, min = inner + ext[i];
        if (e < min) { p[0] *= min / e; p[1] *= min / e; }
      });
    }
    groupPlans.set(c.id, pts);
    return pts;
  }
  function factSlot(c, gi, k, n) {
    const gc = groupCenter(c, gi), right = screenRight(view.basis), down = screenDown(view.basis);
    const top = (Math.min(n, MAX_FACTS) - 1) / 2 * FACT_GAP;
    return add(gc, add(scale(right, cloudRadius(n) * 0.7 + 0.8), scale(down, k * FACT_GAP - top)));
  }
  // A cloud off the main chain shrinks to a small knot until it is focused, selected, hovered,
  // on the lit chain or on the journey; the main chain keeps its size so the eye follows it.
  function sizeOf(c) {
    if (c.kind === "core" || c.kind === "candidate" || c.kind === "root" || !backbone.size) return 1;
    const looked = backbone.has(c.id) || frame.focus.has(c.id) || view.selected === c.id || view.cluster === c.id
      || hover?.cluster?.id === c.id || frame.chain?.has(c.id) || frame.phase?.near.has(c.id) || journey?.stops.some(st => st.id === c.id);
    return looked ? 1 : SIDE_SCALE;
  }
  function targetOf(c, p) {
    const center = layout.pos.get(c.id), R = layout.radius.get(c.id) * sizeOf(c), shape = layout.shape.get(c.id);
    if (view.cluster !== c.id) return add(center, scale(shape ? shape.fact.get(p.fid) : ballPoint(p.k), R));
    const gi = groupOf.get(c.id).get(p.fid), g = c.groups[gi];
    const k = g.fids.indexOf(p.fid);
    if (view.group === gi && k < MAX_FACTS) return factSlot(c, gi, k, g.fids.length);
    return radialPoint(c, gi, k, g.fids.length, p.fid);
  }
  function hubTarget(c, j) {
    const gi = hubGroup.get(c.id)[j];
    if (view.cluster === c.id && gi >= 0) return groupCenter(c, gi);
    return add(layout.pos.get(c.id), scale(layout.shape.get(c.id).hub[j], layout.radius.get(c.id) * sizeOf(c)));
  }

  // The DSL inside a cloud: hairline spokes from each fact to the entities it names, threads in
  // the phase colour along each state's timeline, and the hubs as small rings. With a group
  // open, ties to its facts stay lit and the rest drop back.
  function drawStructure(c, alpha, isOpen, m) {
    const st = c.structure, hubs = hubInst.get(c.id);
    if (!layout.shape.get(c.id)) return;
    st.hubs.forEach((h, j) => {
      const t = hubTarget(c, j), q = hubs[j];
      if (!q.cur) q.cur = c.kind === "core" ? t : [...layout.pos.get("core")];
      for (let i = 0; i < 3; i++) q.cur[i] += (t[i] - q.cur[i]) * m;
    });
    if (isOpen) return drawRadial(c, alpha);
    const cur = new Map(inst.get(c.id).map(p => [p.fid, p.cur]));
    const lit = isOpen && view.group != null ? new Set(c.groups[view.group].fids) : null;
    const path = (pairs, color, a) => {
      if (!pairs.length || a < 0.01) return;
      stroke(color, a, 1);
      ctx.beginPath();
      for (const [p, q] of pairs) {
        const pa = project(p), pb = project(q);
        if (pa && pb) { ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); }
      }
      ctx.stroke();
    };
    const split = (list, touches) => lit ? [list.filter(e => !touches(e)), list.filter(touches)] : [list, []];
    const [spokeDim, spokeLit] = split(st.spokes, ([fid]) => lit.has(fid));
    const [threadDim, threadLit] = split(st.threads, ([a, b]) => lit.has(a) || lit.has(b));
    const spokeSeg = ([fid, j]) => [cur.get(fid), hubs[j].cur];
    const threadSeg = ([a, b]) => [cur.get(a), cur.get(b)];
    const base = lit ? 0.3 : 1;
    path(spokeDim.map(spokeSeg), colors["line-hi"], alpha * (isOpen ? 0.3 : 0.22) * base);
    path(threadDim.map(threadSeg), phaseColor(c), alpha * (isOpen ? 0.6 : 0.5) * base);
    path(spokeLit.map(spokeSeg), colors["line-hi"], alpha * 0.7);
    path(threadLit.map(threadSeg), phaseColor(c), alpha * 0.95);
    ctx.setLineDash([]);
    // Rings only for the entities the inspector lists; the rest still anchor their spokes.
    const other = c.groups.findIndex(g => g.other);
    hubs.forEach((q, j) => {
      const s = hubGroup.get(c.id)[j] !== other && project(q.cur);
      if (!s) return;
      const agent = st.hubs[j].key.startsWith("agent:");
      stroke(colors.label, alpha * (agent ? 0.9 : 0.7), 1);
      ctx.beginPath(); ctx.arc(s.x, s.y, Math.max(2, Math.min(agent ? 5 : 3.5, s.s * (agent ? 0.14 : 0.09))), 0, Math.PI * 2); ctx.stroke();
    });
  }
  // An open cloud: every fact on a straight spoke from its own entity's hub (the column of the
  // open entity fans out from it the same way), and one line per pair of entities that share
  // facts, thicker for more, with the count at its middle. No per-fact lines cross the ring.
  function drawRadial(c, alpha) {
    const own = groupOf.get(c.id), color = phaseColor(c);
    const centers = c.groups.map((g, gi) => groupCenter(c, gi));
    const ties = tiesOf(c), most = Math.max(1, ...ties.map(t => t[2]));
    ctx.setLineDash([]);
    for (const [a, b, n] of ties) {
      const lit = view.group == null || view.group === a || view.group === b;
      const pa = project(centers[a]), pb = project(centers[b]);
      if (!pa || !pb) continue;
      stroke(colors["line-hi"], alpha * (lit ? 0.55 : 0.12), 1 + 3 * n / most);
      ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
      if (!lit) continue;
      const mx = (pa.x + pb.x) / 2, my = (pa.y + pb.y) / 2, t = String(n);
      ctx.font = `13px ${MONO}`;
      const w = ctx.measureText(t).width + 10;
      ctx.globalAlpha = 0.9 * alpha;
      ctx.fillStyle = colors.void;
      ctx.fillRect(mx - w / 2, my - 8, w, 16);
      ctx.globalAlpha = alpha;
      ctx.fillStyle = colors.label;
      ctx.textAlign = "center";
      ctx.fillText(t, mx, my + 4);
    }
    for (const p of inst.get(c.id)) {
      const gi = own.get(p.fid);
      if (gi == null || !p.cur) continue;
      const lit = view.group == null || view.group === gi;
      const pa = project(centers[gi]), pb = project(p.cur);
      if (!pa || !pb) continue;
      stroke(lit ? color : colors["line-hi"], alpha * (lit ? 0.6 : 0.15), 1);
      ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
    }
  }
  // A selected, folded cloud names its biggest hubs.
  function hubLabels(c, alpha) {
    const hubs = hubInst.get(c.id);
    ctx.textAlign = "left";
    ctx.font = `14px ${MONO}`;
    ctx.fillStyle = colors["label-dim"];
    ctx.globalAlpha = alpha;
    c.structure.hubs.slice(0, 4).forEach((h, j) => {
      const s = hubs[j].cur && project(hubs[j].cur);
      if (!s) return;
      // A hub's name gives way to any name or badge already there.
      const bx = [s.x + 6, s.y - 17, ctx.measureText(h.key).width, 17];
      if (clash(bx, 1)) return;
      placed.push(bx);
      ctx.fillText(h.key, s.x + 6, s.y - 4);
    });
  }

  function expansionGoal() {
    const c = byId.get(view.cluster);
    if (view.group != null) {
      const g = c.groups[view.group];
      const t = add(groupCenter(c, view.group), scale(screenRight(view.basis), 2.6));
      return {target: t, yaw: cam.yaw, pitch: cam.pitch, dist: Math.max(10, Math.min(g.fids.length, MAX_FACTS) * FACT_GAP * 1.9 + 4)};
    }
    return {target: layout.pos.get(c.id), yaw: cam.yaw, pitch: cam.pitch, dist: Math.max(12, groupRing(c) * 4.2)};
  }

  // How much of the canvas the HUD covers on each side: header, footer, the open panels, and
  // the phase ruler (left when upright, along the bottom when on its side).
  function hudInsets() {
    const box = canvas.getBoundingClientRect(), page = canvas.closest(".qg");
    const rect = sel => { const r = page?.querySelector(sel)?.getBoundingClientRect(); return r && r.width ? r : null; };
    const panel = rect(".qg-inspect:not([hidden]):not(.is-collapsed)"), legend = rect(".qg-legend:not(.is-collapsed)");
    const head = rect(".qg > header"), foot = rect(".qg-foot");
    const ruler = rot || frame.mini ? 0 : 170;
    const left = Math.max(rot ? 16 : ruler, legend ? legend.right - box.left + 12 : 0);
    return {
      left,
      // Only an open details panel takes room; folded, the tower spreads under where it was.
      right: Math.max(16, panel ? box.right - panel.left + 12 : 0),
      top: head ? Math.max(0, head.bottom - box.top) : 0,
      bottom: (foot ? Math.max(0, box.bottom - foot.top + 8) : 0) + (rot && !frame.mini ? 44 : 0),
    };
  }
  // A preset marked fit frames the clouds on screen (not the plates under them, which are much
  // wider) in the canvas area the HUD leaves free, so a wide window fills with the tower.
  function fitGoal(g) {
    // Lying down, a low camera sees the cone from the side instead of down its length.
    if (rot && g.pitch < 0.6) g = {...g, pitch: g.pitch * 0.35};
    return fitClouds(g, clusters.filter(c => frame.visible.has(c.id)));
  }
  // A tour shot keeps its angle; where the clouds shown so far fit closer than its preset
  // distance (a wide window, a folded panel), the camera comes in and centres them in the free
  // area, but never closer than TOUR_NEAR so a lone cloud does not swell to fill the screen.
  function tourShot(g) {
    const fit = fitClouds({...g, yaw: g.yaw ?? cam.yaw}, clusters.filter(c => frame.visible.has(c.id)));
    if (fit.dist >= g.dist) return g;
    return {...g, dist: Math.max(fit.dist, Math.min(g.dist, TOUR_NEAR)), target: fit.target};
  }
  // Clouds fit by their rims across and down the screen, with room kept for their names (which
  // lead out to the right) and the plates allowed to run past the edge.
  function fitClouds(g, shown) {
    if (!shown.length) return g;
    const rims = f => {
      const R = screenRight(f), D = screenDown(f);
      return shown.flatMap(c => {
        const p = layout.pos.get(c.id), r = layout.radius.get(c.id) * sizeOf(c);
        return p ? [add(p, scale(R, r)), add(p, scale(R, -r)), add(p, scale(D, r)), add(p, scale(D, -r))] : [];
      });
    };
    const ins = hudInsets();
    const room = {left: ins.left + 40, right: ins.right + 120, top: ins.top + 28, bottom: ins.bottom + 28};
    return perspectiveFit(fitPoints(g, rims(camFrame(g)), () => [160, 56]), room, rims);
  }
  // The first estimate is orthographic; in perspective the near clouds draw larger and can leave
  // the free area. Project their rims through the goal camera and correct the distance and the
  // centre from what they really cover, three times.
  function perspectiveFit(g, {left, right, top, bottom}, rimsOf) {
    const keep = {...cam, target: [...cam.target]}, keepB = B;
    const rims = rimsOf(camFrame(g));
    const freeW = Math.max(240, W - left - right), freeH = Math.max(200, H - top - bottom);
    for (let pass = 0; pass < 3; pass++) {
      Object.assign(cam, g, {target: [...g.target]});
      B = basis();
      const ps = rims.map(project).filter(Boolean);
      if (!ps.length) break;
      const xs = ps.map(q => q.x), ys = ps.map(q => q.y);
      const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
      const k = 1.04 * Math.max((x1 - x0) / freeW, (y1 - y0) / freeH);
      const px = g.dist / B.focal, frame = camFrame(g);
      const dx = (x0 + x1) / 2 - (left + W - right) / 2, dy = (y0 + y1) / 2 - (top + H - bottom) / 2;
      g = {...g, dist: g.dist * k, target: add(g.target, add(scale(screenRight(frame), dx * px), scale(screenDown(frame), dy * px)))};
    }
    Object.assign(cam, keep);
    B = keepB;
    return g;
  }

  // A journey is framed from the angle the overview (or tour) camera has: all its stops fit the
  // free canvas with room for their labels on the right; a focused stop is seen up close.
  function journeyGoal() {
    const base = tourGoal.fit ? fitGoal(tourGoal) : tourGoal;
    const g = {yaw: base.yaw ?? cam.yaw, pitch: base.pitch ?? cam.pitch};
    if (journey.focus != null) {
      const s = journey.stops[journey.focus];
      return {...g, target: journeyPoint(s), dist: Math.max(14, (layout.radius.get(s.id) || 1) * 7)};
    }
    return fitPoints(g, journey.stops.map(journeyPoint), wide => wide ? [60, 2 * LABEL_ROW] : [2 * LABEL_ROOM, 60]);
  }
  // A lit chain is framed the same way: its clouds fill the free canvas, room left for names.
  function chainGoal() {
    const base = tourGoal.fit ? fitGoal(tourGoal) : tourGoal;
    const g = {yaw: base.yaw ?? cam.yaw, pitch: base.pitch ?? cam.pitch};
    // Cloud names lead out to the right, so the right side keeps more room.
    return fitPoints(g, [...frame.chain].map(id => layout.pos.get(id)).filter(Boolean), wide => wide ? [60, 120] : [120, 60], 3, 180);
  }
  // A phase in focus frames only its own clouds; the clouds one link away may run off the edge.
  function phaseGoal() {
    const base = tourGoal.fit ? fitGoal(tourGoal) : tourGoal;
    const g = {yaw: base.yaw ?? cam.yaw, pitch: base.pitch ?? cam.pitch};
    return fitPoints(g, [...frame.phase.members].map(id => layout.pos.get(id)).filter(Boolean), wide => wide ? [60, 120] : [120, 60], 3, 180);
  }
  // The camera that fits a set of world points into the canvas the HUD leaves free, seen from
  // the angle g; room(wide) says how many pixels to keep for labels across and down.
  function fitPoints(g, pts, room, pad = 2, rightRoom = 0) {
    const frame = camFrame(g), R = screenRight(frame), D = screenDown(frame);
    const xs = pts.map(p => dot(p, R)), ys = pts.map(p => dot(p, D));
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const {left, right, top, bottom} = hudInsets();
    const [rw, rh] = room(x1 - x0 >= y1 - y0);
    const freeW = Math.max(200, W - left - right - rw - rightRoom), freeH = Math.max(160, H - top - bottom - rh);
    const focal = H / (2 * Math.tan(cam.fov / 2));
    const dist = Math.max(16, 1.2 * focal * Math.max((x1 - x0 + pad) / freeW, (y1 - y0 + pad) / freeH));
    const px = dist / focal;
    // Centre the points in the free area; the labels flank them.
    const mid = scale(pts.reduce(add, [0, 0, 0]), 1 / pts.length);
    const at = add(mid, add(scale(R, (x0 + x1) / 2 - dot(mid, R)), scale(D, (y0 + y1) / 2 - dot(mid, D))));
    const ox = (left - right - rightRoom) / 2 * px, oy = (top - bottom) / 2 * px;
    return {...g, dist, target: add(at, add(scale(R, -ox), scale(D, -oy)))};
  }
  function journeyPoint(s) {
    return inst.get(s.id)?.find(q => q.fid === journey.fid)?.cur || layout.pos.get(s.id);
  }

  // The journey: segments in the phase colour of the stop they reach, a chevron on each, the
  // first one dashed when the fact enters above ROUTE; a numbered badge per stop with a callout
  // saying what that step concluded; a bar where the journey stops.
  function drawJourney() {
    const J = journey, pts = J.stops.map(s => project(journeyPoint(s)));
    const lit = k => J.focus == null || J.focus === k;
    const edges = J.stops.flatMap((s, k) => s.from.map(j => [j, k]));
    for (const [j, k] of edges) {
      const a = pts[j], b = pts[k];
      if (!a || !b) continue;
      const color = colors.phase[J.stops[k].phase] || colors.focus;
      const on = J.focus == null || J.focus === k || J.focus === j;
      stroke(color, on ? 0.95 : 0.35, 1.6, j === 0 && J.start ? [5, 4] : null);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      const len = Math.hypot(b.x - a.x, b.y - a.y);
      if (len > 40) {
        const ux = (b.x - a.x) / len, uy = (b.y - a.y) / len, mx = a.x + (b.x - a.x) * 0.55, my = a.y + (b.y - a.y) * 0.55;
        ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(mx - 6 * ux - 4 * uy, my - 6 * uy + 4 * ux); ctx.lineTo(mx, my); ctx.lineTo(mx - 6 * ux + 4 * uy, my - 6 * uy - 4 * ux); ctx.stroke();
      }
    }
    // A bar past every stop the way does not go on from: each dead branch, and where it ends.
    J.stops.forEach((s, k) => {
      const end = pts[k], prev = pts[s.from[0]];
      if (!s.deadEnd || !end || !prev) return;
      const len = Math.hypot(end.x - prev.x, end.y - prev.y) || 1, ux = (end.x - prev.x) / len, uy = (end.y - prev.y) / len;
      const bx = end.x + ux * 14, by = end.y + uy * 14;
      stroke(colors.label, lit(k) ? 0.9 : 0.4, 1.6);
      ctx.beginPath(); ctx.moveTo(end.x + ux * 8, end.y + uy * 8); ctx.lineTo(bx, by);
      ctx.moveTo(bx - uy * 7, by + ux * 7); ctx.lineTo(bx + uy * 7, by - ux * 7); ctx.stroke();
    });

    // Labels as callouts, subway-map style: along a line that runs across, two rows above and
    // below it; along a line that runs up, two columns beside it. Stops alternate between the
    // two in their order along the line, each row is swept so no two labels overlap, and a
    // leader ties every label to its numbered badge.
    const shown = pts.map((p, k) => p && {k, p}).filter(Boolean);
    if (!shown.length) return;
    const bx = shown.map(i => i.p.x), by = shown.map(i => i.p.y);
    const [minX, maxX, minY, maxY] = [Math.min(...bx), Math.max(...bx), Math.min(...by), Math.max(...by)];
    const wide = maxX - minX >= maxY - minY;
    for (const it of shown) {
      const s = J.stops[it.k], on = lit(it.k);
      it.color = colors.phase[s.phase] || colors.focus;
      it.lines = [[clip(stopTitle(byId.get(s.id)), 36), `500 17px ${SANS}`, on ? it.color : colors["label-dim"]], [clip(s.outcome, 44), `15px ${SANS}`, on ? colors.label : colors["label-dim"]]];
      if (it.k === 1 && J.start) it.lines.push([clip(`↳ ${J.start}`, 54), `15px ${SANS}`, colors.label]);
      if (it.k === J.endAt && J.end) it.lines.push([clip(`⊣ stops: ${J.end}`, 60), `500 15px ${SANS}`, colors["ink-hi"]]);
      it.w = Math.max(...it.lines.map(([t, f]) => { ctx.font = f; return ctx.measureText(t).width; })) + 18;
      it.h = it.lines.reduce((n, [, f]) => n + lineH(f), 0) + 8;
    }
    const order = [...shown].sort((a, b) => wide ? a.p.x - b.p.x : a.p.y - b.p.y);
    const ins = hudInsets(), GAP = wide ? 10 : 6;
    const lo = wide ? ins.left + 8 : ins.top + 8, hi = wide ? W - ins.right - 8 : H - ins.bottom - 8;
    const size = it => wide ? it.w : it.h;
    // A long journey whose labels outgrow one row per side stacks further rows outward.
    const levels = Math.max(1, Math.ceil(order.reduce((n, it) => n + size(it) + GAP, 0) / 2 / Math.max(1, hi - lo)));
    order.forEach((it, i) => { it.side = i % 2 ? 1 : -1; it.level = Math.floor(i / 2) % levels; });
    const depth = Math.max(...order.map(it => wide ? it.h : it.w)) + 6;
    for (const side of [-1, 1]) for (let level = 0; level < levels; level++) {
      const row = order.filter(it => it.side === side && it.level === level);
      let end = lo - GAP;
      for (const it of row) { it.a = Math.max(wide ? it.p.x - 12 : it.p.y - it.h / 2, end + GAP); end = it.a + size(it); }
      let limit = hi + GAP;
      for (const it of [...row].reverse()) { it.a = Math.min(it.a, limit - GAP - size(it)); limit = it.a; }
      const out = level * depth;
      for (const it of row) {
        it.box = wide ? [it.a, side < 0 ? minY - 30 - out - it.h : maxY + 30 + out, it.w, it.h]
                      : [side < 0 ? minX - 34 - out - it.w : maxX + 34 + out, it.a, it.w, it.h];
        // Never past the canvas edge or under a panel: on a narrow canvas a label may cover the
        // line rather than leave the screen.
        it.box[0] = Math.max(ins.left + 4, Math.min(W - ins.right - 4 - it.w, it.box[0]));
        it.box[1] = Math.max(ins.top + 4, Math.min(H - ins.bottom - 4 - it.h, it.box[1]));
        it.box = it.box.map(Math.round);
      }
    }
    for (const it of shown) {
      const [x, y, w, h] = it.box, on = lit(it.k), {p} = it;
      const [lx, ly] = wide ? [Math.max(x + 6, Math.min(x + w - 6, p.x)), it.side < 0 ? y + h : y]
                            : [it.side < 0 ? x + w : x, Math.max(y + 6, Math.min(y + h - 6, p.y))];
      stroke(it.color, on ? 0.6 : 0.25, 1);
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(lx, ly); ctx.stroke();
      ctx.globalAlpha = 0.92;
      ctx.fillStyle = colors.void;
      ctx.fillRect(x, y, w, h);
      stroke(it.color, on ? (J.focus === it.k ? 0.9 : 0.35) : 0.15, 1);
      ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
      ctx.globalAlpha = on ? 1 : 0.6;
      ctx.fillStyle = it.color;
      ctx.fillRect(x, y, 2, h);
      ctx.textAlign = "left";
      let base = y + 4;
      it.lines.forEach(([t, f, col]) => {
        base += lineH(f);
        ctx.font = f;
        ctx.fillStyle = col;
        ctx.fillText(t, x + 10, base - Math.round(pxOf(f) * 0.32));
      });
      targets.push({kind: "stop", k: it.k, stop: J.stops[it.k], x: x + w / 2, y: y + h / 2, r: 0, rect: [x, y, w, h]});
    }
    // Badges last, on top of every leader.
    for (const {k, p, color} of shown) {
      const on = lit(k), num = J.stops[k].num;
      ctx.font = `600 15px ${MONO}`;
      const w = Math.max(24, Math.ceil(ctx.measureText(num).width) + 10);
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.fillStyle = J.focus === k ? color : colors.void;
      ctx.fillRect(p.x - w / 2, p.y - 12, w, 24);
      stroke(color, on ? 1 : 0.5, 1);
      ctx.strokeRect(p.x - w / 2 + 0.5, p.y - 11.5, w - 1, 23);
      ctx.fillStyle = J.focus === k ? colors.void : on ? colors["ink-hi"] : colors["label-dim"];
      ctx.textAlign = "center";
      ctx.fillText(num, p.x, p.y + 5);
      targets.push({kind: "stop", k, stop: J.stops[k], x: p.x, y: p.y, r: 14});
    }
    ctx.globalAlpha = 1;
  }

  function setView(cluster, group = null) {
    if (cluster && cluster !== view.cluster) view.basis = {r: [...B.r], u: [...B.u]};
    view.cluster = cluster; view.group = group;
    manual = false; emit("manual", false);
    emit("view", {cluster: cluster ? byId.get(cluster) : null, group});
  }

  // ---- drawing helpers
  const stroke = (color, alpha = 1, width = 1, dash = null) => {
    ctx.strokeStyle = color; ctx.globalAlpha = alpha; ctx.lineWidth = width; ctx.setLineDash(dash || []);
  };
  function line3(a, b) {
    const pa = project(a), pb = project(b);
    if (!pa || !pb) return;
    ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
  }
  function ring3(center, R, seg = 120) {
    ctx.beginPath();
    let open = false;
    for (let i = 0; i <= seg; i++) {
      const a = i / seg * Math.PI * 2, p = project([center[0] + R * Math.cos(a), center[1], center[2] + R * Math.sin(a)]);
      if (!p) { open = false; continue; }
      open ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
      open = true;
    }
    ctx.stroke();
  }
  // The ROUTE floor's guide: one turn of a helix through the query clouds, rising with call order.
  function helix3(t, n) {
    if (n < 2) return;
    ctx.beginPath();
    let open = false;
    for (let i = 0; i <= 120; i++) {
      const f = i / 120 * (n - 1) / n, a = Math.PI / 2 - f * Math.PI * 2;
      const p = project([t.ring * Math.cos(a), t.y + t.rise * f * n / (n - 1), t.ring * Math.sin(a)]);
      if (!p) { open = false; continue; }
      open ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
      open = true;
    }
    ctx.stroke();
  }
  // A tier's floor: only its rim in the phase colour, the near half drawn and the far half
  // barely there, so six floors read as one cone instead of a stack of discs. The current
  // floor alone gets a faint fill.
  function plate(center, R, color, alpha, active) {
    const N = 96, pts = [];
    for (let i = 0; i <= N; i++) {
      const a = i / N * Math.PI * 2, d = [R * Math.cos(a), 0, R * Math.sin(a)];
      pts.push({p: project(add(center, d)), near: dot(d, B.f) < 0});
    }
    ctx.setLineDash([]);
    if (active) {
      ctx.beginPath();
      pts.forEach(({p}, i) => p && (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      ctx.globalAlpha = 0.07 * alpha;
      ctx.fillStyle = color;
      ctx.fill();
    }
    for (const near of [false, true]) {
      stroke(color, (near ? (active ? 0.6 : 0.18) : (active ? 0.2 : 0.05)) * alpha, 1);
      ctx.beginPath();
      for (let i = 1; i <= N; i++) {
        const a = pts[i - 1], b = pts[i];
        if (!a.p || !b.p || b.near !== near) continue;
        ctx.moveTo(a.p.x, a.p.y); ctx.lineTo(b.p.x, b.p.y);
      }
      ctx.stroke();
    }
  }

  // Phase ruler: a fixed HUD scale on the left whose ticks sit at each tier's projected height,
  // one segment per phase, the current tier lit.
  function ruler(shown, current) {
    if (rot) return rulerAcross(shown, current);
    const x = 18, top = 96, bottom = H - 150;
    const tiers = TIERS.filter(t => shown.some(c => c.tier === t.tier))
      .map(t => ({t, y: project([0, t.y, 0])?.y})).filter(r => r.y != null)
      .map(r => ({...r, y: Math.max(top, Math.min(bottom, r.y))}));
    if (!tiers.length) return;
    // Clamped ticks keep at least a label's height between them, pushing upward.
    for (let k = 1; k < tiers.length; k++) tiers[k].y = Math.min(tiers[k].y, tiers[k - 1].y - 42);
    stroke(colors.line, 1, 1);
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
    tiers.forEach((r, k) => {
      const color = colors.phase[r.t.phase];
      const next = tiers[k + 1];
      const on = current === r.t.tier;
      if (next) {
        stroke(color, on ? 1 : 0.55, on ? 3 : 2);
        ctx.beginPath(); ctx.moveTo(x, r.y); ctx.lineTo(x, next.y + 3); ctx.stroke();
      }
      stroke(color, 1, 1);
      ctx.beginPath(); ctx.moveTo(x - 5, r.y); ctx.lineTo(x + 6, r.y); ctx.stroke();
      const n = shown.filter(c => c.tier === r.t.tier).length;
      ctx.textAlign = "left";
      ctx.globalAlpha = on ? 1 : 0.75;
      ctx.fillStyle = color;
      ctx.font = `${on ? 600 : 500} 15px ${SANS}`;
      ctx.fillText(`${on ? "▸ " : ""}${r.t.phase}`, x + 10, r.y - 4);
      placed.push([x - 6, r.y - 19, 170, 40]);
      ctx.fillStyle = colors["label-dim"];
      ctx.font = `14px ${SANS}`;
      ctx.fillText(`${r.t.label.toLowerCase()}${n > 1 ? ` · ${n}` : ""}`, x + 10, r.y + 15);
    });
    ctx.globalAlpha = 1;
  }
  // On its side the same ruler runs along the bottom, ticks under each floor, labels above.
  function rulerAcross(shown, current) {
    const {left, right, bottom} = hudInsets();
    const y = H - bottom + 22, x0 = left + 8, x1 = W - right - 8;
    const tiers = TIERS.filter(t => shown.some(c => c.tier === t.tier))
      .map(t => ({t, x: project([0, t.y, 0])?.x})).filter(r => r.x != null)
      .map(r => ({...r, x: Math.max(x0, Math.min(x1 - 140, r.x))}));
    if (!tiers.length) return;
    // Clamped ticks keep at least a label's width between them, pushing right.
    for (let k = 1; k < tiers.length; k++) tiers[k].x = Math.max(tiers[k].x, tiers[k - 1].x + 140);
    stroke(colors.line, 1, 1);
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    tiers.forEach((r, k) => {
      const color = colors.phase[r.t.phase], next = tiers[k + 1], on = current === r.t.tier;
      if (next) {
        stroke(color, on ? 1 : 0.55, on ? 3 : 2);
        ctx.beginPath(); ctx.moveTo(r.x, y); ctx.lineTo(next.x - 3, y); ctx.stroke();
      }
      stroke(color, 1, 1);
      ctx.beginPath(); ctx.moveTo(r.x, y - 5); ctx.lineTo(r.x, y + 6); ctx.stroke();
      const n = shown.filter(c => c.tier === r.t.tier).length;
      ctx.textAlign = "left";
      ctx.globalAlpha = on ? 1 : 0.75;
      ctx.fillStyle = color;
      ctx.font = `${on ? 600 : 500} 15px ${SANS}`;
      ctx.fillText(`${on ? "▸ " : ""}${r.t.phase}`, r.x + 4, y - 25);
      placed.push([r.x - 2, y - 40, Math.min(200, next ? next.x - r.x : 200), 46]);
      // The floor's name only where the next tick leaves room for it.
      if (!next || next.x - r.x > 200) {
        ctx.fillStyle = colors["label-dim"];
        ctx.font = `14px ${SANS}`;
        ctx.fillText(`${r.t.label.toLowerCase()}${n > 1 ? ` · ${n}` : ""}`, r.x + 4, y - 8);
      }
    });
    ctx.globalAlpha = 1;
  }

  // What a click on a cloud does, as its hovered tag says it.
  function clickSays(c) {
    if (journey) return journey.stops.some(s => s.id === c.id) ? "click to fly to this stop" : "click to leave the journey";
    return view.selected === c.id ? "click again to open by entity" : c.kind === "core" ? "click to select, again to open" : "click to light its causal chain";
  }
  function brackets(x, y, r, color, alpha = 1) {
    const L = Math.max(5, Math.min(10, r * 0.4));
    stroke(color, alpha, 1);
    ctx.beginPath();
    for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const cx = x + sx * r, cy = y + sy * r;
      ctx.moveTo(cx, cy - sy * L); ctx.lineTo(cx, cy); ctx.lineTo(cx - sx * L, cy);
    }
    ctx.stroke();
  }
  // Boxes already drawn on the HUD this frame (badges, clouds, names): later names keep clear.
  let placed = [];
  const clash = (bx, m = 3) => placed.some(q => bx[0] < q[0] + q[2] + m && q[0] < bx[0] + bx[2] + m && bx[1] < q[1] + q[3] + m && q[1] < bx[1] + bx[3] + m);
  const offCanvas = bx => bx[0] < 4 || bx[1] < 4 || bx[0] + bx[2] > W - 4 || bx[1] + bx[3] > H - 4;
  // A cloud's name, led out from a bracket corner on a short diagonal. Upright, it prefers the
  // side away from the tower's axis and the upper corner; on its side, the right and the side of
  // the axis it is on. A fixed side (+1 right / -1 left upright, +1 down / -1 up on its side)
  // goes first. The first corner whose text box is clear of everything placed and inside the
  // canvas wins. A hovered cloud adds a line in the accent colour: what a
  // click will do there.
  function tag(x, y, r, title, detail, color, alpha = 1, side = 0, act = null, big = false) {
    const axis = project([0, cam.target[1], 0]);
    const lines = [[title, `500 ${big ? 20 : 16}px ${SANS}`, color], ...(detail ? [[detail, detailFont(detail), colors["label-dim"]]] : []), ...(act ? [[act, `14px ${SANS}`, colors.focus]] : [])];
    const w = Math.max(...lines.map(([t, f]) => { ctx.font = f; return ctx.measureText(t).width; }));
    const heights = lines.map(([, f]) => lineH(f)), h = heights.reduce((a, b) => a + b, 0);
    const px0 = pxOf(lines[0][1]), lift = h - heights[heights.length - 1];
    let cands;
    if (rot) {
      const v = -side || (axis && y < axis.y - 4 ? -1 : 1);
      cands = [[1, v], [1, -v], [-1, v], [-1, -v]];
    } else {
      const u = side || (axis && x < axis.x - 4 ? -1 : 1);
      cands = [[u, -1], [-u, -1], [u, 1], [-u, 1]];
    }
    const layoutAt = ([sx, sy]) => {
      const x0 = x + sx * r, y0 = y + sy * r, x1 = x0 + sx * 10, y1 = y0 + sy * 10, tx = x1 + sx * 10;
      // On its side, above the axis the block grows upward from the leader; else it hangs from it.
      const by = (sy < 0 && rot ? y1 - lift : y1) + Math.round(px0 * 0.36);
      return {sx, x0, y0, x1, y1, tx, by, box: [sx > 0 ? tx : tx - w, by - Math.round(px0 * 0.95), w, h]};
    };
    const all = cands.map(layoutAt);
    // Clear and on the canvas; else the on-canvas corner that overlaps least (a slight overlap
    // reads, a cut-off name does not).
    const overlap = bx => placed.reduce((n, q) => n + Math.max(0, Math.min(bx[0] + bx[2], q[0] + q[2]) - Math.max(bx[0], q[0])) * Math.max(0, Math.min(bx[1] + bx[3], q[1] + q[3]) - Math.max(bx[1], q[1])), 0);
    const inside = all.filter(o => !offCanvas(o.box));
    const pick = inside.find(o => !clash(o.box)) || [...inside].sort((p, q) => overlap(p.box) - overlap(q.box))[0] || all[0];
    placed.push(pick.box);
    const {sx, x0, y0, x1, y1, tx, by} = pick;
    stroke(color, alpha * 0.8, 1);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.lineTo(x1 + sx * 6, y1); ctx.stroke();
    ctx.globalAlpha = alpha;
    ctx.textAlign = sx > 0 ? "left" : "right";
    let at = by;
    lines.forEach(([t, f, c], i) => { ctx.font = f; ctx.fillStyle = c; ctx.fillText(t, tx, at); at += heights[i + 1] ?? 0; });
  }
  // An edge that has something to say carries a small square at its middle instead of a text
  // plate over the line: the square is the hover target, and its card shows over the details
  // panel's place (the tour's caption says the same in words).
  function edgeLabel({a, b, link, cluster, color, hot}) {
    const pa = project(a), pb = project(b);
    if (!pa || !pb) return;
    const x = Math.round(pa.x + (pb.x - pa.x) * 0.5), y = Math.round(pa.y + (pb.y - pa.y) * 0.5);
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    ctx.fillStyle = hot ? color : colors.void;
    ctx.fillRect(x - 5, y - 5, 10, 10);
    stroke(color, 1, 1);
    ctx.strokeRect(x - 4.5, y - 4.5, 9, 9);
    if (!hot) { ctx.fillStyle = color; ctx.fillRect(x - 1.5, y - 1.5, 3, 3); }
    placed.push([x - 6, y - 6, 12, 12]);
    targets.push({kind: "edge", link, cluster, x, y, rect: [x - 9, y - 9, 18, 18]});
  }
  // A lit chain's edges, numbered from the record up: a small square at each edge's middle, its
  // number matching the panel's list; hovering either side lights the other.
  let chainHi = null;
  function chainBadges(edges) {
    edges.forEach((e, k) => {
      const pa = project(layout.pos.get(e.from)), pb = project(layout.pos.get(e.to));
      if (!pa || !pb) return;
      const c = byId.get(e.to), color = phaseColor(c), hi = chainHi === k || (hover?.kind === "edge" && hover.link === e.link);
      const x = pa.x + (pb.x - pa.x) * 0.5, y = pa.y + (pb.y - pa.y) * 0.5;
      if (hi) { stroke(color, 1, 2.5); ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke(); }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.fillStyle = hi ? color : colors.void;
      ctx.fillRect(x - 12, y - 12, 24, 24);
      stroke(color, 1, 1);
      ctx.strokeRect(x - 11.5, y - 11.5, 23, 23);
      ctx.fillStyle = hi ? colors.void : colors["ink-hi"];
      ctx.font = `600 15px ${MONO}`;
      ctx.textAlign = "center";
      ctx.fillText(String(k + 1), x, y + 5);
      placed.push([x - 12, y - 12, 24, 24]);
      targets.push({kind: "edge", link: e.link, cluster: c, k, x, y, rect: [x - 13, y - 13, 26, 26]});
    });
  }
  // One numbered square per mark, beside a point of it: in a focused cloud first, else the nearest.
  function markBadges() {
    for (const mk of marks) {
      if (!mk.label) continue;
      const at = badgeAt.filter(b => b.mk === mk).sort((a, b) => (b.focus - a.focus) || a.z - b.z)[0];
      if (!at) continue;
      // The first corner whose square is clear of the badges and names already placed.
      const [dx, dy] = [[1, -1], [-1, -1], [1, 1], [-1, 1], [2, -1], [-2, -1]].find(([u, v]) => !clash([at.x + 13 * u - 11, at.y + 13 * v - 11, 22, 22], 2)) || [1, -1];
      const x = at.x + 13 * dx, y = at.y + 13 * dy;
      stroke(mk.color, 0.9, 1);
      ctx.beginPath(); ctx.moveTo(at.x + 3 * Math.sign(dx), at.y + 3 * dy); ctx.lineTo(x - 9 * Math.sign(dx), y - 9 * dy); ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = colors.void;
      ctx.fillRect(x - 10, y - 10, 20, 20);
      stroke(mk.color, 1, 1.5);
      ctx.strokeRect(x - 9.5, y - 9.5, 19, 19);
      ctx.fillStyle = mk.color;
      ctx.font = `600 13px ${MONO}`;
      ctx.textAlign = "center";
      ctx.fillText(mk.label, x, y + 4.5);
      placed.push([x - 11, y - 11, 22, 22]);
    }
    badgeAt = [];
  }
  function diamond(x, y, r, fill) {
    ctx.beginPath(); ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y); ctx.closePath();
    fill ? ctx.fill() : ctx.stroke();
  }

  function draw(now) {
    const dt = Math.min(0.1, (now - (last || now)) / 1000);
    last = now;
    if (onFrame) {
      const next = onFrame(dt);
      if (next) { frame = next.frame; tourGoal = next.camera; }
    }
    B = basis();
    if (!manual) {
      goal = view.cluster ? expansionGoal() : journey ? journeyGoal() : frame.phase?.members.size ? phaseGoal() : frame.chain?.size > 1 ? chainGoal() : tourGoal.fit ? fitGoal(tourGoal) : tourShot(tourGoal);
      const k = 1 - Math.exp(-dt * 2.4);
      if (goal.yaw != null) cam.yaw += angleDelta(cam.yaw, goal.yaw) * k;
      if (goal.pitch != null) cam.pitch += (goal.pitch - cam.pitch) * k;
      cam.dist += (goal.dist - cam.dist) * k;
      for (let i = 0; i < 3; i++) cam.target[i] += (goal.target[i] - cam.target[i]) * k;
      B = basis();
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = 1;
    ctx.fillStyle = colors.void;
    ctx.fillRect(0, 0, W, H);
    targets = [];
    const open = view.cluster ? byId.get(view.cluster) : null;
    const fade = open ? 0.12 : 1;

    // Frame: the central axis, then one plate per populated tier in its phase colour, so each
    // phase stands on its own floor. Legend filters can hide phases.
    const hiddenPhase = frame.filter?.phases;
    const shown = clusters.filter(c => frame.visible.has(c.id) && !hiddenPhase?.has(c.phaseKey));
    const tierUsed = new Set(shown.map(c => c.tier));
    const floors = TIERS.filter(t => tierUsed.has(t.tier));
    // The axis in segments, each floor's phase colour up to the next floor.
    floors.forEach((t, k) => {
      const next = floors[k + 1];
      stroke(colors.phase[t.phase], 0.3 * fade, 1);
      line3([0, t.y, 0], [0, next ? next.y : t.y + 1.2, 0]);
    });
    // The cone's outline: the two silhouette edges between neighbouring plates, taken along the
    // camera's horizontal right vector so they hug the rims from any angle.
    const side = norm([B.r[0], 0, B.r[2]]);
    stroke(colors["line-hi"], 0.2 * fade, 1, [3, 5]);
    for (let k = 1; k < floors.length; k++) {
      const a = floors[k - 1], b = floors[k];
      for (const s of [-1, 1]) line3([s * side[0] * a.plate, a.y, s * side[2] * a.plate], [s * side[0] * b.plate, b.y, s * side[2] * b.plate]);
    }
    // The current floor: the selected cloud's, else the hovered one's, else the tour's focus.
    const tierOf = id => id ? byId.get(id)?.tier : undefined;
    const current = (journey?.focus != null ? tierOf(journey.stops[journey.focus].id) : null) ?? tierOf(view.selected) ?? tierOf(hover?.cluster?.id) ?? (frame.phase?.members.size ? tierOf([...frame.phase.members][0]) : null) ?? (frame.focus.size ? tierOf([...frame.focus][0]) : null);
    for (const t of floors) {
      const members = shown.filter(c => c.tier === t.tier);
      const active = current === t.tier;
      plate([0, t.y, 0], t.plate, colors.phase[t.phase], fade, active);
      stroke(colors.line, (active ? 0.6 : 0.2) * fade, 1, [2, 5]);
      if (t.rise) helix3(t, members.length);
      else if (members.length > 1) ring3([0, t.y, 0], t.ring);
    }

    // Links between clouds, in the phase colour of the cloud they lead to. The edges of the
    // focused, open or hovered cloud carry their short label.
    const edgeLabels = [];
    const hoverId = hover?.kind === "cluster" ? hover.cluster.id : null;
    for (const c of shown) for (const l of c.links) {
      if (!frame.visible.has(l.from)) continue;
      if (hiddenPhase?.has(byId.get(l.from)?.phaseKey)) continue;
      // Under a journey the links only hint at the tower; the journey line carries the story.
      if (journey) { if (l.from !== "core") { stroke(colors["line-hi"], 0.14, 1); line3(layout.pos.get(l.from), layout.pos.get(c.id)); } continue; }
      // A phase in focus keeps only the links into or out of its clouds, each in full colour.
      if (frame.phase) {
        const ph = frame.phase, on = ph.members.has(c.id) || ph.members.has(l.from), over = hoverId === c.id || hoverId === l.from;
        if (!on || (l.minor && !over)) continue;
        stroke(phaseColor(c), l.from === "core" ? 0.5 : 0.85, 1.2, l.from === "core" ? [1, 4] : l.minor ? [3, 4] : null);
        line3(layout.pos.get(l.from), layout.pos.get(c.id));
        if ((over || hover?.kind === "edge" && hover.link === l) && l.title) edgeLabels.push({a: layout.pos.get(l.from), b: layout.pos.get(c.id), link: l, cluster: c, color: phaseColor(c), hot: hover?.link === l});
        continue;
      }
      const inChain = frame.chain && frame.chain.has(c.id) && frame.chain.has(l.from);
      const hot = inChain || (!frame.chain && frame.focus.has(c.id)) || (open && (open.id === c.id || (open.id === l.from && l.from !== "core")));
      const hovered = hoverId === c.id || (hoverId === l.from && l.from !== "core");
      if (l.minor && !hot && !hovered && view.selected !== c.id && view.selected !== l.from) continue;
      const spoke = l.from === "core";
      const color = phaseColor(c);
      const quiet = frame.chain && !inChain ? 0.25 : 1;
      const main = frame.labelAll && !frame.chain && !open && backbone.has(c.id) && backbone.has(l.from);
      if (spoke && !(main || hot || hovered || view.selected === c.id || (!backbone.size && !frame.labelAll))) continue;
      if (main) stroke(color, 0.9, 1.6);
      else stroke(hot || hovered ? color : colors["line-hi"], hot || hovered ? 0.85 : (spoke ? 0.45 : 0.35 + 0.5 * l.weight) * fade * quiet * (frame.labelAll && !frame.chain ? 0.7 : 1), 1, spoke ? [1, 4] : l.minor ? [3, 4] : null);
      line3(layout.pos.get(l.from), layout.pos.get(c.id));
      const edgeHot = hover?.kind === "edge" && hover.link === l;
      // The overview's main chain keeps its badges, so the pointer can reach them from anywhere.
      if (!frame.chain && (hot || hovered || edgeHot || main) && l.title) edgeLabels.push({a: layout.pos.get(l.from), b: layout.pos.get(c.id), link: l, cluster: c, color, hot: edgeHot});
    }

    // Points.
    ctx.setLineDash([]);
    const labelled = [];
    for (const c of shown) {
      const appear = frame.appear.get(c.id) ?? 1;
      const center = layout.pos.get(c.id), pc = project(center);
      const R = layout.radius.get(c.id);
      const focus = frame.focus.has(c.id);
      const isOpen = open?.id === c.id;
      const outside = (frame.chain && !frame.chain.has(c.id)) || (frame.phase && !frame.phase.near.has(c.id));
      const inPhase = frame.phase?.members.has(c.id);
      const lead = frame.labelAll && !frame.chain && !frame.phase && backbone.has(c.id) && c.kind !== "core";
      const onWay = journey && journey.stops.some(s => s.id === c.id);
      const alpha = (open && !isOpen ? 0.07 : journey && !isOpen ? (onWay ? 0.45 : 0.08) : outside ? 0.13 : focus || isOpen || frame.chain || lead || inPhase ? 1 : c.kind === "core" ? 0.5 : c.error ? 0.3 : 0.6)
        * appear * (pc && !isOpen ? fog(pc.z) : 1);
      if (frame.filled.has(c.id)) {
        if (inst.get(c.id).length !== c.fids.length) inst.set(c.id, c.fids.map((fid, k) => ({fid, k, cur: null})));
        const gmap = groupOf.get(c.id);
        const m = 1 - Math.exp(-dt * 5);
        for (const p of inst.get(c.id)) {
          const t = targetOf(c, p);
          if (!p.cur) p.cur = c.kind === "core" ? t : [...layout.pos.get("core")];
          for (let i = 0; i < 3; i++) p.cur[i] += (t[i] - p.cur[i]) * m;
        }
        // Plain points (and the ties between them) sit low by default so the clouds read as
        // shapes, not noise; a focused, selected or main-chain cloud lifts a little, an open one fully.
        const plain = isOpen ? 1 : focus || view.selected === c.id || lead ? POINT_LIFT : POINT_DIM;
        const sparse = c.kind === "core" && !isOpen && !focus ? Math.max(1, Math.ceil(c.fids.length / CORE_POINTS)) : 1;
        if ((!journey || isOpen) && sparse === 1) drawStructure(c, alpha * plain, isOpen, m);
        if (sparse > 1 && pc) {
          stroke(colors["line-hi"], 0.35 * fade, 1, [2, 4]);
          ctx.beginPath(); ctx.arc(pc.x, pc.y, R * pc.s * 1.05, 0, Math.PI * 2); ctx.stroke();
          ctx.setLineDash([]);
        }
        for (const p of inst.get(c.id)) {
          if (sparse > 1 && p.k % sparse && !roleOf(p.fid) && journey?.fid !== p.fid && !markOf.has(p.fid) && !(frame.labelAll && evidenceHighlight.has(p.fid))) continue;
          const s = project(p.cur);
          if (!s) continue;
          const inGroup = isOpen && view.group === gmap.get(p.fid);
          const role = roleOf(p.fid);
          // The travelling fact stands out in every cloud it passes; other role facts step back.
          const traveller = journey?.fid === p.fid;
          const mk = markOf.get(p.fid);
          const inspected = frame.labelAll && evidenceHighlight.has(p.fid);
          const size = mk ? 5 : traveller ? 6 : Math.max(role ? 3 : 1.3, Math.min(inGroup || role ? 4 : 2.6, s.s * (role ? 0.12 : 0.085)));
          ctx.globalAlpha = (isOpen && view.group != null && !inGroup ? alpha * 0.35 : alpha) * (role || mk ? 1 : plain);
          if (mk) { ctx.globalAlpha = Math.max(ctx.globalAlpha, 0.95 * appear); if (!open) badgeAt.push({mk, x: s.x, y: s.y, z: s.z, focus}); }
          if (role && !(open && !isOpen)) ctx.globalAlpha = Math.max(ctx.globalAlpha, (journey ? 0.5 : 0.95) * appear);
          if (traveller || inspected) ctx.globalAlpha = 1;
          ctx.fillStyle = mk ? mk.color : role ? roleColor[role] : focus || isOpen ? colors["ink-hi"] : c.kind === "core" ? colors["ink-dim"] : colors.ink;
          ctx.fillRect(s.x - size / 2, s.y - size / 2, size, size);
          if (inspected) {
            stroke(colors.focus, .95, 1);
            ctx.strokeRect(s.x - size / 2 - 2, s.y - size / 2 - 2, size + 4, size + 4);
          }
          if (inGroup && c.groups[view.group].fids.indexOf(p.fid) < MAX_FACTS) {
            targets.push({kind: "fact", cluster: c, fid: p.fid, x: s.x, y: s.y, r: 7});
            ctx.globalAlpha = 1;
            ctx.fillStyle = view.fact === p.fid || hover?.fid === p.fid || inspected ? colors["ink-hi"] : colors.label;
            ctx.font = `13.5px ${MONO}`;
            ctx.textAlign = "left";
            ctx.fillText(factLabel(scene.factById.get(p.fid)), s.x + 9, s.y + 4);
          }
        }
      } else if (pc) {
        // A lane before its SQL result: an empty dashed slot.
        stroke(colors["line-hi"], alpha, 1, [2, 3]);
        ctx.beginPath(); ctx.arc(pc.x, pc.y, Math.max(6, 0.6 * pc.s), 0, Math.PI * 2); ctx.stroke();
        ctx.setLineDash([]);
      }
      if (!pc) continue;
      const sr = Math.max(7, R * sizeOf(c) * pc.s * 1.08);
      if (c.kind === "candidate" || c.kind === "root") {
        ctx.globalAlpha = alpha;
        ctx.fillStyle = colors.focus;
        ctx.strokeStyle = c.kind === "root" ? colors.focus : colors.cand;
        ctx.lineWidth = 1.2;
        diamond(pc.x, pc.y, c.kind === "root" ? 9 : 7, c.kind === "root");
      }
      if (c.error) {
        stroke(colors["ink-dim"], alpha, 1);
        ctx.beginPath(); ctx.moveTo(pc.x - 4, pc.y - 4); ctx.lineTo(pc.x + 4, pc.y + 4); ctx.moveTo(pc.x + 4, pc.y - 4); ctx.lineTo(pc.x - 4, pc.y + 4); ctx.stroke();
      }
      if (!isOpen) targets.push({kind: "cluster", cluster: c, x: pc.x, y: pc.y, r: frame.labelAll ? Math.max(16,sr+3) : sr+3});
      labelled.push({c, pc, sr, focus, alpha, isOpen});
    }

    // Groups of the open cloud: a bracket and a name each.
    if (open && frame.filled.has(open.id)) {
      open.groups.forEach((g, gi) => {
        const p = project(groupCenter(open, gi));
        if (!p) return;
        const r = Math.max(6, (radialExtent(g.fids.length) + 0.12) * p.s + 3);
        const on = view.group === gi, hot = hover?.kind === "group" && hover.gi === gi;
        brackets(p.x, p.y, r, on || hot ? phaseColor(open) : colors["line-hi"], 1);
        if (!on) {
          ctx.globalAlpha = view.group != null ? 0.5 : 1;
          ctx.textAlign = "left";
          ctx.fillStyle = hot ? phaseColor(open) : colors.label;
          ctx.font = `15px ${MONO}`;
          ctx.fillText(g.key, p.x + r + 5, p.y - 1);
          ctx.fillStyle = colors["label-dim"];
          ctx.font = `13px ${MONO}`;
          ctx.fillText(`${g.fids.length} fact${g.fids.length === 1 ? "" : "s"}`, p.x + r + 5, p.y + 15);
          if (hot) { ctx.fillStyle = colors.focus; ctx.font = `14px ${SANS}`; ctx.fillText("click to list its facts", p.x + r + 5, p.y + 32); }
        } else if (g.fids.length > MAX_FACTS) {
          const q = project(factSlot(open, gi, MAX_FACTS, g.fids.length));
          if (q) { ctx.globalAlpha = 1; ctx.textAlign = "left"; ctx.fillStyle = colors["label-dim"]; ctx.font = `13px ${MONO}`; ctx.fillText(`+${g.fids.length - MAX_FACTS} more in the inspector`, q.x + 9, q.y + 4); }
        }
        targets.push({kind: "group", cluster: open, gi, x: p.x, y: p.y, r: r + 4});
      });
    }

    // HUD. The phase ruler and badges go down first and every cloud's core is an obstacle, then names are placed in
    // order of importance (open, hovered, selected, focused, main chain, verdicts), each keeping
    // clear of what is already there; hub names come last and give way.
    placed = labelled.map(({pc, sr}) => { const k = Math.min(sr, 14); return [pc.x - k, pc.y - k, 2 * k, 2 * k]; });
    // A minimap (the Story mode's tower) has no ruler and names only the clouds in focus.
    if (!frame.mini && !frame.labelAll) ruler(shown, current);
    markBadges();
    for (const e of edgeLabels) edgeLabel(e);
    if (frame.chain && frame.chainEdges && !journey && !open) chainBadges(frame.chainEdges);
    const rank = ({c, focus, isOpen}) => isOpen ? 0 : hover?.kind === "cluster" && hover.cluster === c ? 1 : view.selected === c.id ? 2 : focus ? 3
      : backbone.has(c.id) ? 4 : c.kind === "root" || c.kind === "candidate" ? 5 : 6;
    const hubsLater = [];
    for (const {c, pc, sr, focus, alpha, isOpen} of [...labelled].sort((p, q) => rank(p) - rank(q))) {
      const hot = hover?.kind === "cluster" && hover.cluster === c;
      if (isOpen) {
        const r = layout.radius.get(c.id) * pc.s + 6;
        brackets(pc.x, pc.y, r, phaseColor(c));
        tag(pc.x, pc.y, r, clusterTitle(c), c.detail, phaseColor(c), 1, 0, null, true);
        continue;
      }
      const selected = view.selected === c.id;
      if (journey && !hot) continue;
      if ((!frame.labelAll || open) && (selected || hot) && frame.filled.has(c.id) && layout.shape.get(c.id)) hubsLater.push(c);
      const outside = (frame.chain && !frame.chain.has(c.id)) || (frame.phase && !frame.phase.near.has(c.id));
      const inPhase = frame.phase?.members.has(c.id);
      // The hovered cloud answers the pointer: a faint disc in its phase colour, brackets pushed out.
      if (hot) {
        ctx.globalAlpha = 0.12;
        ctx.fillStyle = phaseColor(c);
        ctx.beginPath(); ctx.arc(pc.x, pc.y, sr + 6, 0, Math.PI * 2); ctx.fill();
        brackets(pc.x, pc.y, sr + 7, phaseColor(c), 1);
      } else if (focus || selected) brackets(pc.x, pc.y, sr + 4, phaseColor(c), fade);
      // Overview is a quiet cloud until hover. Its HTML preview owns all narrative text.
      if (frame.labelAll && !journey) continue;
      if (outside && !hot) continue;
      const lead = frame.labelAll && !frame.chain && !frame.phase && backbone.has(c.id);
      const quietAll = frame.labelAll && !frame.chain && !frame.phase && backbone.size;
      const named = frame.mini ? focus || hot || selected : quietAll ? lead || focus || hot || selected : c.kind !== "query" || focus || hot || selected || lead || inPhase || (frame.chain && frame.chain.has(c.id));
      if (!named && quietAll) continue;
      if (named) {
        // In the overview the main chain reads down the right; its tier-mates take the left.
        const side = !frame.labelAll || frame.chain || frame.phase ? 0 : lead ? 1 : c.kind !== "query" ? -1 : 0;
        tag(pc.x, pc.y, sr + 4, clusterTitle(c), c.detail, focus || hot || lead || inPhase || c.kind === "root" ? phaseColor(c) : colors.label, hot ? 1 : fade * Math.min(1, alpha / 0.6), side, hot ? clickSays(c) : null, focus && !frame.labelAll);
      }
    }
    for (const c of hubsLater) hubLabels(c, fade);
    if (journey) drawJourney();
    ctx.globalAlpha = 1;
    raf = requestAnimationFrame(draw);
  }

  function pick(x, y) {
    let best = null, bd = Infinity;
    for (const t of targets) {
      const d = Math.hypot(t.x - x, t.y - y);
      // A fact's label sits to the right of its point and is clickable too; edge labels are boxes.
      const hit = t.kind === "edge" || t.rect ? x >= t.rect[0] && x <= t.rect[0] + t.rect[2] && y >= t.rect[1] && y <= t.rect[1] + t.rect[3]
        : t.kind === "fact" ? d < t.r || (x > t.x && x < t.x + 380 && Math.abs(y - t.y) < 7) : d < t.r;
      const score = t.kind === "details" ? -4000 : t.kind === "stop" ? d - 3000 : t.kind === "edge" ? -2000 : t.kind === "fact" ? Math.abs(y - t.y) - 1000 : t.kind === "group" ? d - 500 : d;
      if (hit && score < bd) { best = t; bd = score; }
    }
    return best;
  }

  // ---- input
  let drag = null;
  const takeOver = () => { if (!manual) { manual = true; emit("manual", true); } };
  const onDown = e => { drag = {x: e.clientX, y: e.clientY, yaw: cam.yaw, pitch: cam.pitch, target: [...cam.target], pan: e.button === 2 || e.shiftKey, moved: false}; canvas.setPointerCapture(e.pointerId); };
  const onMove = e => {
    const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    if (drag) {
      const sx = e.clientX - drag.x, sy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(sx) + Math.abs(sy) > 3) { drag.moved = true; takeOver(); }
      // Work in the camera's own frame: on its side, screen down is the camera's right.
      const [dx, dy] = rot ? [sy, -sx] : [sx, sy];
      if (!drag.moved) return;
      if (drag.pan) { const s = cam.dist / B.focal; for (let i = 0; i < 3; i++) cam.target[i] = drag.target[i] - (B.r[i] * dx - B.u[i] * dy) * s; }
      else { cam.yaw = drag.yaw - dx * 0.006; cam.pitch = Math.max(-0.3, Math.min(1.45, drag.pitch + dy * 0.005)); }
      return;
    }
    hover = pick(x, y);
    canvas.style.cursor = hover ? "pointer" : "grab";
    emit("hover", hover ? {...hover, x, y} : null);
  };
  const onUp = e => {
    if (drag && !drag.moved) {
      const r = canvas.getBoundingClientRect();
      const t = pick(e.clientX - r.left, e.clientY - r.top);
      // Under a journey a badge or a cloud on the way focuses that stop; another cloud ends it.
      const stopAt = t && journey ? (t.kind === "stop" ? t.k : t.kind === "cluster" ? journey.stops.findIndex(s => s.id === t.cluster.id) : -1) : -1;
      if (t?.kind === "details") emit("details", t.cluster.id);
      else if (stopAt >= 0) focusStop(journey.focus === stopAt ? null : stopAt);
      else if (!t) back();
      else if (journey && t.kind === "cluster") { setJourney(null); select(t.cluster.id); }
      else if (frame.labelAll && t.kind === "cluster") emit("details", t.cluster.id);
      else if (t.kind === "cluster" || t.kind === "edge") view.selected === t.cluster.id ? open(t.cluster.id) : select(t.cluster.id);
      else if (t.kind === "group") open(t.cluster.id, view.group === t.gi ? null : t.gi);
      else if (t.kind === "fact") { view.fact = t.fid; emit("select", {kind: "fact", cluster: t.cluster, gi: view.group, fid: t.fid}); }
    }
    drag = null;
  };
  // First click selects a cloud (the page lights its causal chain); a second click opens it.
  function select(id, {preserveCamera = false} = {}) {
    view.selected = id;
    if (view.cluster && view.cluster !== id) setView(null);
    // Picking a cloud hands the camera back, so it can frame the chain the pick lights, even
    // after the reader has dragged or zoomed.
    if (preserveCamera) { manual = true; emit("manual",true); }
    else if (id && manual) { manual = false; emit("manual", false); }
    emit("select", id ? {kind: "cluster", cluster: byId.get(id), opened: false} : null);
  }
  function open(id, group = null) {
    view.selected = id;
    view.fact = null;
    setView(id, group);
    const c = byId.get(id);
    emit("select", group == null ? {kind: "cluster", cluster: c, opened: true} : {kind: "group", cluster: c, gi: group});
  }
  function setJourney(j) {
    journey = j ? {...j, focus: null} : null;
    view.selected = null; view.fact = null;
    if (view.cluster) setView(null);
    manual = false; emit("manual", false);
    emit("journey", journey);
  }
  function focusStop(k) {
    if (!journey) return;
    journey.focus = k;
    manual = false; emit("manual", false);
    emit("journey", journey);
  }
  function back() {
    if (journey && !view.cluster) { journey.focus != null ? focusStop(null) : setJourney(null); return; }
    if (view.fact != null) { view.fact = null; emit("select", {kind: "group", cluster: byId.get(view.cluster), gi: view.group}); }
    else if (view.group != null) open(view.cluster);
    else if (view.cluster) { setView(null); emit("select", {kind: "cluster", cluster: byId.get(view.selected), opened: false}); }
    else if (view.selected) { view.selected = null; emit("select", null); }
    else emit("back");
  }
  const onWheel = e => { e.preventDefault(); takeOver(); cam.dist = Math.max(5, Math.min(110, cam.dist * Math.exp(e.deltaY * 0.001))); };
  const onLeave = () => { hover = null; emit("hover", null); };
  const noMenu = e => e.preventDefault();
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointerleave", onLeave);
  canvas.addEventListener("wheel", onWheel, {passive: false});
  canvas.addEventListener("contextmenu", noMenu);
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  resize();
  B = basis();

  return {
    start(fn) { onFrame = fn; raf = requestAnimationFrame(draw); },
    // Fold everything and hand the camera back to the tour.
    follow() { if (journey) setJourney(null); view.fact = null; view.selected = null; setView(null); emit("select", null); },
    // Hand the camera back to whoever drives it (tour or overview presets) without folding.
    release() { manual = false; emit("manual", false); },
    // The tour moves the camera under a still pointer; drop what it was over until it moves.
    clearHover() { if (hover) { hover = null; canvas.style.cursor = "grab"; emit("hover", null); } },
    open,
    select,
    back,
    setJourney,
    focusStop,
    highlightChainEdge(k) { chainHi = k; },
    get journey() { return journey; },
    selectFact(fid) { view.fact = fid; },
    // [{fids, color, label}]: later marks win where they share a fact.
    markFacts(list) { marks = list || []; markOf = new Map(marks.flatMap(m => m.fids.map(fid => [fid, m]))); },
    highlightEvidence(fids) { evidenceHighlight = new Set(fids); },
    get view() { return view; },
    get manual() { return manual; },
    colors: () => colors,
    on(k, fn) { listeners[k].push(fn); },
    destroy() { cancelAnimationFrame(raf); ro.disconnect(); },
  };
}

const clip = (t, n) => t.length > n ? t.slice(0, n - 1) + "…" : t;
const stopTitle = c => c.kind === "core" ? "DSL record" : clusterTitle(c);
const camFrame = g => ({r: [Math.cos(g.yaw), 0, -Math.sin(g.yaw)],
                        u: [-Math.sin(g.yaw) * Math.sin(g.pitch), Math.cos(g.pitch), -Math.cos(g.yaw) * Math.sin(g.pitch)]});
const hash01 = n => { let x = Math.imul((n | 0) ^ 0x9e3779b9, 0x85ebca6b); x ^= x >>> 13; x = Math.imul(x, 0xc2b2ae35); x ^= x >>> 16; return (x >>> 0) / 4294967296; };
export const clusterTitle = c => c.kind === "query" ? shortSpec(c.label) : c.label;
const shortSpec = s => { const t = String(s).replace(/\s+/g, " "); return t.length > 40 ? t.slice(0, 39) + "…" : t; };
const angleDelta = (a, b) => { let d = (b - a) % (Math.PI * 2); if (d > Math.PI) d -= Math.PI * 2; if (d < -Math.PI) d += Math.PI * 2; return d; };
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = a => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
