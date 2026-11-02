// Read-only narrative layer. All claims and evidence stay bound to this run's records.
// Missing fields stay unavailable; a retrieved record is never promoted to an error.
import {esc, short} from "../util.js?v=27bf1baa85";
import {factRepr, SLOT_LABEL, SIGNAL_LABEL} from "./model.js?v=ad59c1cadf";

const PHASES = ["PARSE", "MAP", "ROUTE", "MANAGER", "TRACE", "SUBMIT"];
const DEST = {PARSE:"To DSL record", MAP:"To task requirements", ROUTE:"To investigation leads", MANAGER:"To investigation branches", TRACE:"To queried evidence", SUBMIT:"To the recorded diagnosis"};
const sentence = (value, n = 150) => short(String(value || "").replace(/\s+/g," "), n);
const pretty = x => typeof x === "string" ? x : JSON.stringify(x, null, 2);
const code = x => `<pre class="ovn-code">${esc(pretty(x) ?? "Not recorded")}</pre>`;
const fold = (title, body) => `<details class="ovn-fold"><summary>${esc(title)}</summary>${body}</details>`;
const box = (title, body) => `<section class="ovn-section"><h4>${esc(title)}</h4>${body}</section>`;
const note = t => `<p class="ovn-note">${esc(t)}</p>`;
const text = t => `<p>${esc(t || "Not recorded in this run.")}</p>`;
const field = (name, value) => value ? box(name, text(value)) : "";
const factButton = f => `<button class="ovn-fact" data-ov-fact="${f.fid}"><span>S${f.t} · ${esc(SLOT_LABEL[f.slot] || f.slot)}</span>${esc(factRepr(f))}<b>Source ↗</b></button>`;
const stepButton = (t, label = `S${t}`) => `<button class="ovn-inline" data-ov-step="${Number(t)}">${esc(label)} ↗</button>`;

export function buildNarratives(scene, model) {
  const task = scene.meta.task || "the recorded task";
  // Prefer task-linked entities, not a hard-coded benchmark vocabulary.
  const taskWords = new Set(task.toLowerCase().match(/[a-z]{3,}/g) || []);
  const entitiesFor = c => [...new Set(c.fids.flatMap(id => scene.factById.get(id)?.args || []))]
    .filter(a => !/^(agent|req|attribute|value|int|source|statement|topic):/.test(a) && score(a)>0)
    .sort((a,b) => score(b)-score(a)).slice(0,2).map(a => a.replace(/^(portable|place):/,"").replace(/[:_]/g," "));
  const score = a => a.toLowerCase().split(/[:_\W]+/).filter(w=>taskWords.has(w)).length;
  const range = c => { const ts=c.fids.map(id=>scene.factById.get(id)?.t).filter(Number.isFinite); return ts.length ? `S${Math.min(...ts)}–${Math.max(...ts)}` : "No mapped steps"; };
  for (const c of model.clusters) {
    const ev = scene.events[c.fill ?? c.event] || {};
    let title, summary, status;
    if(c.kind === "core") {
      title="From agent logs to connected facts";
      summary=`${scene.meta.steps} steps parsed into ${scene.facts.length} facts and ${scene.entities.length} entities.`;
      status="Structured record";
    } else if(c.kind === "query") {
      const names=entitiesFor(c).join(" & ");
      title=c.phase === "MAP" ? "What does this task require?" : ev.error ? "This evidence request was rejected" : ev.spec?.startsWith("entity:req:") ? "Check the task requirement" : names ? `Review decisions about ${names}` : "Inspect the task's recorded trajectory";
      const signals=(ev.signals||[]).slice(0,2).map(([s])=>SIGNAL_LABEL[s]||s).join("; ");
      summary=ev.error ? sentence(ev.error.message) : signals ? `Recorded signals: ${signals}. These are leads, not confirmed errors.` : ev.goal ? `Compiled requirement status: ${ev.goal.state}; inspect the original evidence.` : `Read ${range(c)} for facts relevant to “${sentence(task,62)}”.`;
      status=ev.error ? "Request rejected" : "Retrieved records";
    } else if(c.kind === "window") {
      title=`Why investigate S${c.ref.step}?`;
      summary=sentence(c.ref.hypothesis,170); status="Investigation lead";
    } else if(c.kind === "lane") {
      title=`Trace ${c.ref.agent.replace(/_/g," ")}${entitiesFor(c).length ? ` · ${entitiesFor(c)[0]}` : ""}`;
      summary=c.ref.rows.length ? `${c.ref.rows.length} SQL rows across ${c.ref.steps.length} returned steps; ${c.fids.length} mapped facts.` : "Assigned question; no returned SQL rows are available.";
      status="Queried evidence";
    } else if(c.kind === "candidate") {
      title=`Suspected ${c.ref.module} error · S${c.ref.step}`;
      summary=sentence(c.ref.proposition,170); status=c.ref.status === "uncertain" ? "Unresolved" : `TRACE assessment: ${c.ref.status || "not recorded"}`;
    } else {
      title=`Selected diagnosis · S${c.ref.step} ${c.ref.module}`;
      summary=sentence(c.ref.proposition,170); status="Selected diagnosis";
    }
    c.narrative={title,summary,status,meta:`${range(c)} · ${c.fids.length} mapped facts`, phase:c.phaseKey || "PARSE"};
    // Short labels identify a cloud; the single fixed reader explains the selection.
    const subject=entitiesFor(c)[0];
    const scoped = String(ev.spec || "").match(/steps:(\d+):(\d+)/);
    const scopeLabel = scoped ? ` · S${scoped[1]}–${scoped[2]}` : "";
    const queryLabel = ev.spec?.startsWith("local_audits") ? "Check task decisions"
      : ev.spec?.startsWith("episodes") ? "Review task progress"
      : ev.spec?.startsWith("scan") ? "Scan the trajectory"
      : ev.name==="inspect" ? "Inspect source records" : subject ? `Review ${subject}` : "Inspect recorded evidence";
    c.narrative.shortTitle = c.kind==="core" ? "Structured logs"
      : c.kind==="query" ? (c.phase==="MAP" || ev.spec?.startsWith("entity:req:") ? "Task requirements" : ev.error ? "Rejected request" : queryLabel+scopeLabel)
      : c.kind==="window" ? `Investigation lead · S${c.ref.step}`
      : c.kind==="lane" ? c.ref.agent.replace(/_/g," ").replace(/^./,s=>s.toUpperCase())
      : c.kind==="candidate" ? `Candidate · S${c.ref.step} ${c.ref.module}`
      : `Selected · S${c.ref.step} ${c.ref.module}`;
  }
}

export function mountOverviewNarrative(root, scene, model, stage) {
  buildNarratives(scene, model);
  const source=scene.source || {}, result=source.result || {}, manager=source.managerPlan || {};
  const steps=source.input?.steps || [];
  const requirements=source.goalMap?.requirements || [];
  const byStep=new Map(steps.map(s=>[s.t,s]));
  const summary=document.createElement("section");
  summary.className="ovn-summary"; summary.hidden=true; summary.setAttribute("aria-label","Current investigation narrative");
  const panel=document.createElement("section");
  panel.className="ovn-panel"; panel.hidden=true; panel.setAttribute("role","region"); panel.setAttribute("aria-label","Stage details");
  const preview=document.createElement("div");
  preview.className="ovn-hover-preview";preview.hidden=true;preview.setAttribute("role","tooltip");
  const instruction=document.createElement("p");instruction.className="ovn-hover-help";instruction.textContent="Hover to preview · Click to investigate";instruction.hidden=true;
  root.append(summary,panel,preview,instruction);
  const annotation=document.createElement("p");annotation.className="ovn-annotation-key";annotation.textContent="Point colors mark recorded roles: investigation clue → TRACE candidate → selected diagnosis. They do not mark confirmed bugs.";
  root.querySelector(".qg-legend-body").append(annotation);
  let active=false, intro=true, phase="PARSE", cluster=null, event=null, selectedStep=steps[0]?.t ?? 1, returnFocus=null;
  const inspector=root.querySelector(".qg-inspect"), body=root.querySelector(".qg-inspect-body");
  const members=ph=>model.clusters.filter(c=>ph==="MANAGER" ? c.kind==="lane" : c.phaseKey===ph);
  const factList=fids=>fids.length ? `<div class="ovn-facts">${fids.map(id=>scene.factById.get(id)).filter(Boolean).map(factButton).join("")}</div>` : note("No mapped facts recorded for this selection.");
  const entityButtons=facts=>[...new Set(facts.flatMap(f=>f.args))].filter(a=>!a.startsWith("req:")).map(a=>`<button class="ovn-entity" data-ov-entity="${esc(a)}">${esc(a)}</button>`).join("");
  function linkedSource(value, facts) {
    const names=new Map();
    for(const key of new Set(facts.flatMap(f=>f.args))) {
      if(/^(agent|req|attribute|value|int|source|statement|topic):/.test(key)) continue;
      const name=key.replace(/^(portable|place):/,"").replace(/[:_]/g," ");
      if(name.length>2) names.set(name.toLowerCase(),key);
    }
    if(!names.size) return esc(value);
    const escapeRe=s=>s.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
    const regex=new RegExp(`\\b(${[...names.keys()].sort((a,b)=>b.length-a.length).map(escapeRe).join("|")})\\b`,"gi");
    const raw=String(value), out=[];let at=0;
    for(const m of raw.matchAll(regex)) {out.push(esc(raw.slice(at,m.index)),`<button class="ovn-source-entity" data-ov-entity="${esc(names.get(m[0].toLowerCase()))}">${esc(m[0])}</button>`);at=m.index+m[0].length;}
    return out.join("")+esc(raw.slice(at));
  }
  const clusterButton=c=>`<button class="ovn-branch" data-ov-cluster="${esc(c.id)}"><strong>${esc(c.narrative.shortTitle)}</strong><small>${esc(c.narrative.status)} · ${esc(c.narrative.meta)} <b>→</b></small></button>`;
  function phaseStory() {
    const cases=scene.windows.map(w=>`S${w.step}`).join(", ");
    const map={
      PARSE:["From raw logs to a searchable record",`ProDebugger parsed ${scene.meta.steps} steps into ${scene.facts.length} facts and ${scene.entities.length} entities for this task.`],
      MAP:["What must the agent accomplish?",`ProDebugger read ${requirements.length || scene.goals.length} structured requirement(s) compiled from “${scene.meta.task}”.`],
      ROUTE:["Where should the investigation begin?", cases ? `Recorded leads start at ${cases}. They are investigation anchors, not confirmed errors or retrieval boundaries.` : "Review the recorded requests and any task-related leads; no investigation anchor is available."],
      MANAGER:["Give each branch a question to test",`${scene.lanes.length} recorded assignments test alternative explanations against the task and original logs.`],
      TRACE:["Test the hypotheses against recorded evidence",`${scene.lanes.length} branches retrieve evidence across the trajectory. Returned steps are not automatically faulty steps.`],
      SUBMIT:[scene.root ? `Why select S${scene.root.step} ${scene.root.module}?` : "Was a diagnosis selected?",scene.root ? sentence(result.root?.proposition || result.reasoning,210) : "No selected diagnostic step is available in this run."]
    };
    return map[phase];
  }
  function renderSummary() {
    const [title,desc]=intro ? ["Follow the investigation", "Select a cloud or stage to understand what was checked, why it matters to the task, and what the records show."] : cluster ? [cluster.narrative.title,cluster.narrative.summary] : phaseStory();
    summary.dataset.phase=phase;
    summary.innerHTML=`<div class="ovn-eyebrow">${intro ? "OVERVIEW" : esc(phase)}${cluster ? ` · ${esc(cluster.narrative.status)}` : ""}</div><h2>${esc(title)}</h2><div class="ovn-summary-line"><p>${esc(desc)}</p><button class="ovn-more" data-ov-more>More details</button></div>`;
    summary.hidden=true;
  }
  function parseBody() {
    const list=steps.length ? steps.map(s=>`<button data-ov-parse-step="${s.t}" aria-current="${s.t===selectedStep}"><b>S${s.t}</b><span>${esc(sentence(s.parts?.action || s.env_text,56))}</span><small>${scene.facts.filter(f=>f.t===s.t).length} facts</small></button>`).join("") : note("Original input steps are unavailable. The compiled facts remain inspectable.");
    const s=byStep.get(selectedStep), facts=scene.facts.filter(f=>f.t===selectedStep);
    const records=s ? [["observation (before action)",s.env_text],...["memory","reflection","plan","action"].map(k=>[k,s.parts?.[k]]),[s.next_env_text ? "feedback" : `next observation (S${selectedStep+1})`,s.next_env_text || byStep.get(selectedStep+1)?.env_text]].filter(([,v])=>v) : [];
    const warnings=(source.goalMap?.parse_audit?.unresolved||[]).filter(w=>w.step===selectedStep);
    return `<div class="ovn-parse"><nav class="ovn-step-list" aria-label="Parsed steps">${list}</nav><div class="ovn-step-record"><h3>Step S${selectedStep}</h3>${text(s?.parts?.action || "Select a source record below.")}<p class="ovn-note">${facts.length} extracted facts. Open a source or entity to check the mapping.</p>${warnings.length ? fold("Extraction limitations",code(warnings)+note("A parsing gap is not evidence of agent failure.")) : ""}<h4>Original records</h4>${records.map(([k,v])=>fold(k,`<blockquote>${linkedSource(v,facts)}</blockquote>`)).join("")}${!s ? note("Original step text is unavailable.") : ""}${fold("Linked entities",`<div class="ovn-entities">${entityButtons(facts)}</div>`)}${fold(`Extracted facts · ${facts.length}`,factList(facts.map(f=>f.fid)))}</div></div>`;
  }
  function mapBody() {
    const reqs=requirements.length ? requirements : scene.goals.map(g=>({entity:{id:g.key},definition:{relation:g.rel,target:g.target,constraints:g.constraints},status:{state:"not recorded"}}));
    return note("MAP reads compiled requirements. A missing extracted state is unknown, not false; the verbatim task remains authoritative.")+reqs.map(r=>box(r.entity?.id || "Task requirement",`<div class="ovn-status">Compiled status: ${esc(r.status?.state || "unknown")}</div>${(r.definition?.constraints||[]).map(c=>`<article class="ovn-mapping"><blockquote>${esc(c.source_span || c.span || scene.meta.task)}</blockquote><span>↓ maps to</span><strong>${esc(c.attribute || c.property?.index || c.property || "condition")} ${esc(c.operator || "=")} ${esc(pretty(c.expected))}</strong><button class="ovn-inline" data-ov-requirement="${esc(r.entity?.id || "")}">View linked requirement evidence ↗</button></article>`).join("")}${fold("Actual DSL requirement",code(r))}`)).join("")+field("Coverage limitation",source.goalMap?.task_contract?.limitation);
  }
  function routeBody() {
    if(cluster?.kind==="query") {
      const ev=scene.events[cluster.event];
      return text(cluster.narrative.summary)+fold("Recorded evidence request",code({tool:ev.name,spec:ev.spec || ev.call?.arguments}))+fold(`Retrieved facts · ${cluster.fids.length}`,factList(cluster.fids))+note("Retrieved records and routing signals are not confirmed errors.");
    }
    const ws=cluster?.kind==="window" ? [cluster.ref] : scene.windows;
    return note("Investigation anchors are leads, not confirmed errors. The diagnostic step may be elsewhere.")+ws.map(w=>box(`Investigation lead · S${w.step}`,`${text(sentence(w.hypothesis,180))}${stepButton(w.step,"Read anchor step")}${fold("Full hypothesis and task links",text(w.hypothesis)+text(w.goals.join(", ") || "No explicit goal link recorded"))}${fold(`Anchor facts · ${w.fids.length}`,factList(w.fids))}`)).join("")+(ws.length ? "" : note("No declared investigation anchors are recorded."))+fold("All ROUTE requests",members("ROUTE").filter(c=>c.kind==="query").map(clusterButton).join(""));
  }
  function managerBody() {
    const assignments=cluster?.kind==="lane" ? (manager.assignments||[]).filter(a=>a.agent_id===cluster.ref.agent) : manager.assignments || [];
    return note("Assigned questions, not query findings.")+assignments.map(a=>box(a.agent_id.replace(/_/g," "),`${text(sentence(a.question,180))}${fold("Full question and search targets",text(a.question)+text((a.search_targets||[]).join("\n")))}${fold("Support / refutation / unknown criteria",code(a.criteria || {}))}${fold("Assigned source quotations",(a.evidence||[]).map(e=>`<blockquote>${esc(e.quote)}<small>${esc(e.source_id)}</small></blockquote>`).join(""))}<button class="ovn-more" data-ov-trace="l:${esc(a.agent_id)}">View this branch's actual query →</button>`)).join("")+(assignments.length ? "" : note("No matching manager assignment is recorded."))+fold("Recorded decision rules",code(manager.decision_rules || []));
  }
  function traceBody() {
    if(!cluster || !["lane","candidate"].includes(cluster.kind)) return note("Choose a branch to inspect its own question, query and returned records; results from separate queries are not merged.")+members("TRACE").filter(c=>c.kind==="lane").map(clusterButton).join("");
    const lane=cluster.kind==="lane" ? cluster : model.byId.get(cluster.ref.lane);
    if(!lane) return note("No corresponding investigation branch is recorded.");
    const ev=scene.events[lane.fill], rows=lane.ref.rows || [];
    const assessments=scene.candidates.filter(c=>c.lane===lane.id);
    const rowList=rows.map((r,k)=>`<article class="ovn-row" data-row-step="${esc(r.step ?? "")}"><strong>${Number.isFinite(r.step) ? stepButton(r.step) : "Step not specified"} · ${esc(r.module || "query row")}</strong><small class="ovn-row-kind">${esc(r.source_kind || "Source kind not recorded")}</small><p>${esc(sentence(r.excerpt || r.text || r.source_text || r.span || r.value || pretty(r),230))}</p><button class="ovn-inline" data-ov-row="${k}" data-ov-lane="${esc(lane.id)}">Inspect returned row ↗</button></article>`).join("");
    const filter=`<label class="ovn-filter">Step <select data-ov-row-filter><option value="">All steps</option>${[...new Set(rows.map(r=>r.step).filter(Number.isFinite))].sort((a,b)=>a-b).map(t=>`<option value="${t}">S${t}</option>`).join("")}</select></label>`;
    const outcome=assessments.length ? assessments.map(c=>`S${c.step} ${c.module}: ${c.status || "not recorded"}`).join("; ") : "No selectable candidate recorded.";
    return box("What was checked",text(sentence(lane.ref.question,190)))
      +box("What came back",text(`${rows.length} SQL rows across ${lane.ref.steps.length} returned steps. ${lane.fids.length} facts were mapped to the graph.`))
      +box("Recorded assessment",text(outcome)+note("Returned records are not automatically faulty steps."))
      +fold("Full investigation question",text(lane.ref.question))
      +fold("Actual SQL / DSL query",code(ev?.sql || "No SQL query recorded"))
      +fold(`Returned evidence · ${rows.length} rows`,filter+`<div class="ovn-returned">${rowList || note("No rows are available; this is not proof of absence.")}</div>`)
      +fold("Assessment reasoning and quotations",assessments.map(c=>`<article class="ovn-assessment"><h4>S${c.step} · ${esc(c.module)}</h4>${text(c.proposition)}<blockquote>${esc(c.quote)}</blockquote>${stepButton(c.step,"Read assessed step")}</article>`).join(""))
      +fold("Query metadata and limitations",code(ev?.call?.result?.data ? Object.fromEntries(Object.entries(ev.call.result.data).filter(([k])=>k!=="rows" && k!=="query")) : {}));
  }
  function submitBody() {
    const r=result.root;
    const comparisons=result.candidate_comparisons || result.comparisons || [];
    return (r ? box(`Selected · S${r.step} ${r.module}`,text(sentence(r.proposition,220))+stepButton(r.step,"Read selected step")) : note("No selected diagnostic step is recorded."))
      +note("A recorded selection is not proof of a unique cause. Suggested repairs have not been executed or validated here.")
      +fold("Source quotation and selection reasoning",`<blockquote>${esc(r?.output_quote || "")}</blockquote>`+text(r?.reasoning || result.reasoning))
      +fold("Compare the candidates",scene.candidates.map(c=>clusterButton(model.byId.get(c.id))).join("")+comparisons.map(c=>box(c.candidate_id || c.root_id || "Comparison",text(c.reasoning || pretty(c)))).join(""))
      +fold("Suggested correction",text(result.repair))+fold("Evidence limits / uncertainty",text(result.uncertainty))+fold("Full recorded result",code(result));
  }
  const bodies={PARSE:parseBody,MAP:mapBody,ROUTE:routeBody,MANAGER:managerBody,TRACE:traceBody,SUBMIT:submitBody};
  function renderPanel() {
    panel.dataset.phase=phase;
    panel.innerHTML=`<header class="ovn-panel-head"><div><span class="ovn-destination">${DEST[phase]}</span><h2>${esc(cluster?.narrative.title || phaseStory()[0])}</h2><p class="ovn-task"><b>Agent task</b> ${esc(scene.meta.task)}</p><small>${phase} ${event!=null ? `· Event ${event+1}` : "· Stage overview"}${cluster ? ` · ${esc(cluster.id)}` : ""}</small></div><button class="ovn-close" data-ov-close aria-label="Close stage details">×</button></header><div class="ovn-content">${bodies[phase]()}</div>`;
  }
  function openPanel() { preview.hidden=true;returnFocus=document.activeElement; renderPanel(); panel.hidden=false; root.classList.add("ovn-details-open"); panel.querySelector("[data-ov-close]").focus({preventScroll:true}); }
  function closePanel() { panel.hidden=true; root.classList.remove("ovn-details-open"); if(!root.classList.contains("ovn-source-open"))stage.highlightEvidence([]);returnFocus?.focus?.({preventScroll:true}); }
  function openEvidence(title,html) {
    inspector.hidden=false; inspector.classList.remove("is-collapsed"); inspector.querySelector("[data-pane]").setAttribute("aria-expanded","true");
    body.innerHTML=`<div class="ovn-source-context">Opened from ${esc(phase)}${event!=null ? ` · Event ${event+1}` : ""}<button class="ovn-more" data-ov-return>Back to stage details</button></div><h3>${esc(title)}</h3>${html}${note("Mapped records are not automatically errors. Recorded links are not causal proof.")}`;
    body.scrollTop=0; root.classList.add("ovn-source-open");
  }
  function closeSource() {
    if(!root.classList.contains("ovn-source-open")) return;
    root.classList.remove("ovn-source-open");inspector.classList.add("is-collapsed");inspector.querySelector("[data-pane]").setAttribute("aria-expanded","false");stage.highlightEvidence([]);
  }
  function openStep(t) {
    const s=byStep.get(t); stage.highlightEvidence(scene.facts.filter(f=>f.t===t).map(f=>f.fid));
    root.querySelectorAll("[data-ov-step]").forEach(b=>b.classList.toggle("is-viewing",Number(b.dataset.ovStep)===t));
    openEvidence(`Step S${t} · original agent record`,s ? [["Environment observation · before action",s.env_text],...["memory","reflection","plan","action"].map(k=>[k,s.parts?.[k]]),[s.next_env_text ? "Recorded feedback" : `Next observation · S${t+1} (after this action)`,s.next_env_text || byStep.get(t+1)?.env_text]].filter(([,v])=>v).map(([k,v])=>box(k,`<blockquote>${esc(v)}</blockquote>`)).join("")+(s.next_env_text || byStep.has(t+1) ? "" : note("No next observation is recorded. The action's final effect is unknown."))+factList(scene.facts.filter(f=>f.t===t).map(f=>f.fid)) : note("Original step text unavailable; inspect the mapped facts instead.")+factList(scene.facts.filter(f=>f.t===t).map(f=>f.fid)));
  }
  function openFact(fid) {
    const f=scene.factById.get(fid); if(!f) return;
    stage.highlightEvidence([fid]);
    openEvidence(`Fact ${fid} · S${f.t} ${SLOT_LABEL[f.slot] || f.slot}`,code(factRepr(f))+(f.span ? box("Source quotation",`<blockquote>${esc(f.span)}</blockquote>`) : note("No source quotation is stored on this fact."))+stepButton(f.t,"Read full original step")+box("Linked entities",entityButtons([f])));
  }
  function selectCluster(id,ph) {
    const c=model.byId.get(id); if(!c) return;
    closeSource();
    stage.select(id,{preserveCamera:true}); intro=false;cluster=c; phase=ph || c.phaseKey || "PARSE"; event=c.fill ?? c.event; renderSummary(); if(!panel.hidden) renderPanel();
  }
  function onClick(e) {
    if(!active) return;
    const hit=s=>e.target.closest(s);
    if(hit("[data-ov-more]")) openPanel();
    if(hit("[data-ov-close]")) closePanel();
    if(hit('[data-pane="inspect"]') && inspector.classList.contains("is-collapsed")) closeSource();
    if(hit("[data-ov-return]")) { inspector.classList.add("is-collapsed"); inspector.querySelector("[data-pane]").setAttribute("aria-expanded","false"); root.classList.remove("ovn-source-open"); stage.highlightEvidence([]); if(panel.hidden) openPanel(); }
    const ps=hit("[data-ov-parse-step]");
    if(ps) {selectedStep=Number(ps.dataset.ovParseStep); const listTop=panel.querySelector(".ovn-step-list")?.scrollTop || 0; renderPanel(); panel.querySelector(".ovn-step-list").scrollTop=listTop; stage.highlightEvidence(scene.facts.filter(f=>f.t===selectedStep).map(f=>f.fid));}
    const st=hit("[data-ov-step]"); if(st) openStep(Number(st.dataset.ovStep));
    const f=hit("[data-ov-fact]"); if(f) openFact(Number(f.dataset.ovFact));
    const en=hit("[data-ov-entity]"); if(en) { const fs=scene.facts.filter(f=>f.args.includes(en.dataset.ovEntity)); stage.highlightEvidence(fs.map(f=>f.fid)); openEvidence(`Entity · ${en.dataset.ovEntity}`,factList(fs.map(f=>f.fid))); }
    const req=hit("[data-ov-requirement]"); if(req) {const fs=scene.facts.filter(f=>f.args.includes(req.dataset.ovRequirement));stage.highlightEvidence(fs.map(f=>f.fid));openEvidence(req.dataset.ovRequirement,factList(fs.map(f=>f.fid)));}
    const cb=hit("[data-ov-cluster]"); if(cb) selectCluster(cb.dataset.ovCluster);
    const tr=hit("[data-ov-trace]"); if(tr) selectCluster(tr.dataset.ovTrace,"TRACE");
    const row=hit("[data-ov-row]"); if(row) {const lane=model.byId.get(row.dataset.ovLane), r=lane.ref.rows[Number(row.dataset.ovRow)]; stage.highlightEvidence(scene.facts.filter(f=>f.t===r.step).map(f=>f.fid)); openEvidence(`Query row · ${lane.ref.agent}`,code(r)+(Number.isFinite(r.step)?stepButton(r.step,"Read original step"):""));}
  }
  function onChange(e) {if(e.target.matches("[data-ov-row-filter]")) panel.querySelectorAll("[data-row-step]").forEach(r=>r.hidden=!!e.target.value && r.dataset.rowStep!==e.target.value);}
  function onKey(e) {if(active && e.key==="Escape" && !panel.hidden) {e.preventDefault(); e.stopImmediatePropagation(); closePanel();}}
  root.addEventListener("click",onClick);root.addEventListener("change",onChange);root.addEventListener("keydown",onKey,true);
  return {
    mode(mode) {active=mode==="overview";summary.hidden=true;instruction.hidden=!active;preview.hidden=true;if(!active){closePanel();closeSource();}if(active)renderSummary();},
    phase(ph) {closeSource();intro=!ph;phase=ph || "PARSE";cluster=null;event=null;if(active){renderSummary();if(ph)openPanel();else if(!panel.hidden)renderPanel();}},
    event(i) {closeSource();intro=false;const ev=scene.events[i];phase=ev.phase;event=i;cluster=["plan_trace","route_cases"].includes(ev.name) ? null : model.byId.get(ev.cluster || "core");if(active){renderSummary();openPanel();}},
    selection(sel) {if(!active)return;if(sel?.kind==="cluster"){closeSource();intro=false;cluster=sel.cluster;phase=cluster.phaseKey || "PARSE";event=cluster.fill ?? cluster.event;renderSummary();if(!panel.hidden)renderPanel();}else if(!sel && panel.hidden){intro=true;cluster=null;renderSummary();}},
    details(id) {selectCluster(id);openPanel();},
    hover(h) {
      preview.hidden=true;
      if(!active || !panel.hidden || !h || h.kind!=="cluster") return;
      const n=h.cluster?.narrative;if(!n)return;
      preview.dataset.phase=n.phase;
      let title=n.shortTitle, description=h.cluster.kind==="lane" ? h.cluster.ref.question : n.summary;
      // Phrase only the recorded question, never infer a query finding from its vocabulary.
      if(h.cluster.kind==="lane" && /cool/i.test(description) && /clean|wash/i.test(description)) {
        title="Check the cooling assumption";
        description="Checks whether cleaning was supported as a way to achieve cooling.";
      } else if(h.cluster.kind==="lane" && /complet/i.test(description) && /later|subsequent|downstream/i.test(description)) {
        title="Check the completion claim";
        description="Checks whether the completion claim shaped later decisions or was reconsidered.";
      }
      preview.innerHTML=`<span>${esc(n.phase)} · ${esc(n.status)}</span><strong>${esc(title)}</strong><p>${esc(sentence(description,145))}</p><small>Click to investigate →</small>`;
      preview.hidden=false;
      const w=preview.offsetWidth,ht=preview.offsetHeight;
      preview.style.left=`${Math.max(12,Math.min(h.x+22,root.clientWidth-w-12))}px`;
      preview.style.top=`${Math.max(12,Math.min(h.y-ht-14,root.clientHeight-ht-75))}px`;
    },
    destroy(){root.removeEventListener("click",onClick);root.removeEventListener("change",onChange);root.removeEventListener("keydown",onKey,true);summary.remove();panel.remove();preview.remove();instruction.remove();annotation.remove();stage.highlightEvidence([]);}
  };
}
