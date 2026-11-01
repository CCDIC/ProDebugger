'use strict';
// A single workspace over the existing compiler, audit and diagnosis APIs.
// Visual states and facts come from the server; no inference happens here.
(() => {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const {el, button, arr, raw, details, code, fields, name, terminal} = AnalysisUI.helpers;
  const MAX = 24, BYTES = 4 * 1024 * 1024;
  const categories = {rule:'规则问题', safety:'安全风险', runtime:'运行异常', model:'模型判断', uncertainty:'检查限制'};
  const visual = {error:'！规则 / 模型错误', risk:'▲ 安全风险', passed:'✓ 当前检查通过', unknown:'？证据不足', neutral:'— 结构 / 未评估'};
  const state = {pool:[], policy:{}, selected:new Set(), graphs:[], activeCase:null, nodeId:null, issue:null,
    tab:'session', mode:'session', sessionSelection:null, page:0, pageSize:6, scale:1, search:'', scope:'all', filter:'all', issueFilter:'all',
    jobs:[], suites:[], reference:null, configuration:null, loading:false, sequence:0, pollBusy:false,
    workspaceIds:new Set(), protectedIds:new Set(), caseMutation:false,
    riskJobs:[], riskCases:[], riskRecords:new Map(), riskReferences:new Map(), semanticRecords:new Map(), riskConfiguration:null,
    cloudManifest:null,cloudFocus:false,showcaseMode:'cloud',cloudEvaluations:new Map(),cloudReferences:new Map(),cloudLoading:new Set(),cloudReferenceErrors:new Map(),captureId:null, captureTimer:null, queue:[], importing:false, lastSample:null, offsets:new Map()};
  let timer, toastTimer, jobDialog;
  const short = (value, n=95) => String(value??'').length>n ? String(value).slice(0,n-1)+'…' : String(value??'');
  const baseName = path => String(path??'').replaceAll('\\','/').split('/').filter(Boolean).pop()||String(path??'');
  const jsonValue = value => {try{return typeof value==='string'?JSON.parse(value):value||{};}catch{return {};}};
  const poolCase = id => state.pool.find(c=>c.id===id);
  const graph = id => state.graphs.find(g=>g.case_id===id);
  const suiteFor = id => state.suites.find(s=>arr(s.cases).some(c=>c.case_id===id));
  const suiteCase = id => arr(suiteFor(id)?.cases).find(c=>c.case_id===id);
  const latest = id => state.jobs.filter(j=>j.case_id===id).sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at))||b.id.localeCompare(a.id))[0];
  const latestRisk = id => {
    const jobs=state.riskJobs.filter(j=>j.case_id===id).sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at))||String(b.id).localeCompare(String(a.id)));
    const active=jobs.find(job=>!RiskView.terminal.has(job.status));if(active)return active;
    const record=state.riskRecords.get(id)||graph(id)?.analysis?.risk_review;
    // The server selects the current input, policy and implementation version.
    // Historical jobs remain in the queue, not as the current model conclusion.
    if(record&&Object.hasOwn(record,'model'))return record.model||null;
    return jobs[0];
  };
  const riskCase = id => state.riskCases.find(c=>c.case_id===id);
  const cloudCase = id => arr(state.cloudManifest?.cases).find(c=>c.case_id===id);
  const inLibrary = id => !state.cloudFocus||(state.showcaseMode==='privacy'?state.workspaceIds.has(id):!!cloudCase(id));
  const protectedCase = id => state.protectedIds.has(id)||!!poolCase(id)?.deletion_protected||(state.showcaseMode==='privacy'&&!!cloudCase(id));
  const busyCase = id => state.jobs.some(job=>job.case_id===id&&!terminal.has(job.status))||state.riskJobs.some(job=>job.case_id===id&&!RiskView.terminal.has(job.status));
  const removableCase = id => !!poolCase(id)&&!protectedCase(id)&&!busyCase(id);
  const callNodes = g => arr(g.nodes).filter(n=>n.kind==='call').sort((a,b)=>(arr(a.action_steps)[0]??0)-(arr(b.action_steps)[0]??0));
  const callValue = (g,n) => jsonValue(arr(g.record?.facts).find(f=>f.rel==='tool_call'&&arr(n.fids).includes(f.fid))?.value);
  const caseTitle = id => {const cloud=cloudCase(id);if(cloud){const index=arr(state.cloudManifest?.cases).findIndex(c=>c.case_id===id);return window.I18n?.t(['轨迹一','轨迹二','轨迹三'][index]||('轨迹 '+(index+1)))||['轨迹一','轨迹二','轨迹三'][index];}const r=riskCase(id);if(r)return r.title||poolCase(id)?.title||id;const s=suiteCase(id);return s?`${s.label} · ${s.is_constructed?'构造变体':'原始对照'}`:poolCase(id)?.title||graph(id)?.title||id;};
  const originLabel = id => {const cloud=cloudCase(id);if(cloud)return cloud.constructed?'构造副本':'云端记录';const r=riskCase(id);return r?(r.origin_label||(r.is_constructed?'风险验收构造 · 未实际执行':'基础对照 · '+(r.source_category||'来源见详情'))):suiteCase(id)?(suiteCase(id).is_constructed?'基于真实轨迹构造':'真实 OpenClaw · 受控运行'):poolCase(id)?.origin_label||graph(id)?.origin_label||'来源待核对';};
  function pill(text, type='neutral') {return el('span','pill '+type,text);}
  function toast(text) {$('#toast').textContent=text;$('#toast').hidden=false;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('#toast').classList.remove('visible');$('#toast').hidden=true;},4500);}
  function message(text, error=false) {const box=$('#workspace-message');box.textContent=text;box.hidden=!text;box.classList.toggle('error',error);}
  async function api(path, body) {
    const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    let data;try{data=await response.json();}catch{throw Error('本地服务未返回有效结果，请确认当前地址的服务正在运行。');}
    const isJob=typeof data.id==='string'&&typeof data.case_id==='string'&&typeof data.status==='string';
    if(!response.ok||(data.error&&!isJob))throw Error(typeof data.error==='string'?data.error:'请求失败：HTTP '+response.status);
    return data;
  }
  function showPanel(panel) {document.body.dataset.panel=panel;if(panel==='detail')document.body.classList.remove('detail-collapsed');$$('[data-panel-target]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.panelTarget===panel)));}
  function filtered() {const q=state.search.trim().toLowerCase();return state.pool.filter(c=>inLibrary(c.id)&&(!q||`${c.id} ${c.title} ${caseTitle(c.id)}`.toLowerCase().includes(q))&&(state.scope==='all'||(state.scope==='session'?c.session_available:['risk_validation','risk_holdout'].includes(state.scope)?!!(c.risk_validation||riskCase(c.id))&&(c.validation_group||riskCase(c.id)?.validation_group||'development')===(state.scope==='risk_holdout'?'holdout':'development'):state.scope==='validation'?!!suiteCase(c.id):c.origin===state.scope))&&(state.filter==='all'||c.status===state.filter));}
  function renderPool() {
    const list=filtered(),box=$('#pool-list');box.replaceChildren();$('#pool-count').textContent=`${list.length} / ${state.pool.filter(c=>inLibrary(c.id)).length} 条`;
    const exampleOrder=new Map(state.suites.flatMap(s=>s.cases).map((c,i)=>[c.case_id,i]));
    const riskOrder=new Map(state.riskCases.map((c,i)=>[c.case_id,i]));
    const ordered=[...list].sort((a,b)=>{
      if(state.cloudFocus){const ids=arr(state.cloudManifest?.cases).map(c=>c.case_id),ai=ids.indexOf(a.id),bi=ids.indexOf(b.id);if(ai>=0||bi>=0)return (ai<0?ids.length:ai)-(bi<0?ids.length:bi);return String(b.created_at).localeCompare(String(a.created_at));}
      if(['risk_validation','risk_holdout'].includes(state.scope)&&riskOrder.has(a.id)&&riskOrder.has(b.id))return riskOrder.get(a.id)-riskOrder.get(b.id);
      if(!!a.session_available!==!!b.session_available)return Number(!!b.session_available)-Number(!!a.session_available);
      if(a.session_available&&b.session_available)return a.id.localeCompare(b.id);
      if(exampleOrder.has(a.id)&&exampleOrder.has(b.id))return exampleOrder.get(a.id)-exampleOrder.get(b.id);
      return Number(exampleOrder.has(b.id))-Number(exampleOrder.has(a.id))||String(b.created_at).localeCompare(String(a.created_at));
    });
    for(const c of ordered){
      const row=el('div','pool-row'+(c.id===state.activeCase?' active':'')+(graph(c.id)?' loaded':''));
      const check=el('input');check.type='checkbox';check.dataset.selectCase=c.id;check.checked=state.selected.has(c.id);check.setAttribute('aria-label','选择比较：'+caseTitle(c.id));
      check.addEventListener('change',()=>{if(check.checked&&state.selected.size>=MAX){check.checked=false;toast('一次最多比较 24 条轨迹。');return;}check.checked?state.selected.add(c.id):state.selected.delete(c.id);renderSelection();});
      const select=button('','pool-select',()=>openCase(c.id));select.dataset.caseId=c.id;select.title=caseTitle(c.id);select.append(el('span','pool-title',caseTitle(c.id)),el('span','pool-meta',originLabel(c.id)));
      select.append(el('span','pool-meta'+(c.session_available?' session-tag':''),c.session_available?'会话记录':'工具记录'));
      const g=graph(c.id),problem=g?.analysis?.issues?.some(i=>['rule','model'].includes(i.category)&&i.state==='confirmed');
      select.append(el('span','pool-meta',null,[pill(problem?'有规则 / 模型问题':c.status==='unsafe'?'基础策略违规尝试':c.status==='safe'?'基础检查通过':'查看检查详情',problem?'error':c.status==='unsafe'?'risk':c.status==='safe'?'passed':'neutral')]));
      if(c.risk_summary){const r=c.risk_summary;select.append(el('span','pool-meta risk-source'+(r.findings?' has-risk':''),r.findings||r.unknown?`${r.findings||0} 项风险${r.unknown?' · '+r.unknown+' 项检查限制':''}`:['not_evaluated','not_configured'].includes(r.status)?'尚未检查':'暂无风险发现'));}
      const remove=button('','pool-delete icon-button',()=>openDeleteCases([c.id]));remove.dataset.deleteCase=c.id;remove.disabled=!removableCase(c.id)||state.caseMutation||state.loading;
      remove.title=protectedCase(c.id)?'原始轨迹已保护，不能删除':busyCase(c.id)?'分析正在运行，完成或取消后再删除':'删除轨迹：'+caseTitle(c.id);remove.setAttribute('aria-label',remove.title);
      remove.innerHTML=protectedCase(c.id)?'<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="5" y="8" width="10" height="8" rx="1.5"/><path d="M7 8V5a3 3 0 0 1 6 0v3"/></svg>':'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h14M7 5V3h6v2M5 5l1 12h8l1-12M8 8v6M12 8v6"/></svg>';
      row.append(check,select,remove);box.append(row);
    }
    if(!list.length)box.append(el('div','empty','没有匹配的轨迹。试试清空搜索或调整筛选。'));renderSelection();
  }
  function renderSelection(){
    $('#selection-count').textContent=`已选 ${state.selected.size} 条`;
    $('#compare-selected').disabled=!state.selected.size||state.loading;
    $('#diagnose-selected').disabled=!state.selected.size||state.selected.size>20;
    $('#risk-selected').disabled=!state.selected.size||state.selected.size>20;
    $('#delete-selected').disabled=state.loading||state.caseMutation||![...state.selected].some(removableCase);
    $$('[data-delete-case]').forEach(control=>{control.disabled=state.loading||state.caseMutation||!removableCase(control.dataset.deleteCase);});
    $('#diagnose-selected').title=state.selected.size>20?'单次诊断最多提交 20 条':'对勾选的轨迹运行完整模型诊断';
    const list=filtered(),count=list.filter(c=>state.selected.has(c.id)).length;
    $('#select-visible').checked=!!list.length&&count===list.length;$('#select-visible').indeterminate=count>0&&count<list.length;
  }
  async function fetchGraphs(ids){const results=[];for(let i=0;i<ids.length;i+=4)results.push(...await Promise.allSettled(ids.slice(i,i+4).map(id=>api('/api/safety/graph/'+encodeURIComponent(id)))));return results;}
  async function loadCases(ids,{context='已载入',select=true}={}){
    ids=[...new Set(ids)].slice(0,MAX);if(!ids.length){toast('请先选择轨迹。');return;}
    if(state.cloudFocus&&ids.some(id=>!inLibrary(id))){state.cloudFocus=false;renderCloudIntro();}
    const seq=++state.sequence;state.loading=true;renderSelection();message(`正在载入 ${ids.length} 条轨迹…`);
    const results=await fetchGraphs(ids);if(seq!==state.sequence)return;
    state.graphs=results.filter(r=>r.status==='fulfilled').map(r=>r.value);state.page=0;state.offsets.clear();
    if(select)state.selected=new Set(ids);
    if(!state.graphs.some(g=>g.case_id===state.activeCase)){state.activeCase=state.graphs[0]?.case_id;state.nodeId=callNodes(state.graphs[0]||{})[0]?.id;state.issue=null;state.sessionSelection=null;}
    if(state.graphs.some(g=>SessionView.available(g))&&state.mode==='session'){state.tab='session';state.sessionSelection={type:'event',eventId:graph(state.activeCase)?.session_artifact?.events?.[0]?.event_id};}
    await Promise.all([loadRiskRecords(state.graphs.map(g=>g.case_id)),loadSemantics(state.graphs.map(g=>g.case_id))]);if(seq!==state.sequence)return;state.loading=false;
    const failed=results.filter(r=>r.status==='rejected');renderAll();showPanel('workspace');
    message(failed.length?`${failed.length} 条载入失败：${failed.map(r=>r.reason.message).join('；')}`:'',!!failed.length);
    if(!failed.length)toast(`已载入 ${state.graphs.length} 条轨迹`);
  }
  async function reloadGraphs(){
    if(state.caseMutation)return;
    const seq=state.sequence,ids=state.graphs.map(g=>g.case_id);if(!ids.length)return;
    const results=await fetchGraphs(ids);if(seq!==state.sequence)return;
    state.graphs=results.map((r,i)=>r.status==='fulfilled'?r.value:state.graphs.find(g=>g.case_id===ids[i])).filter(Boolean);
    if(state.issue){const found=graph(state.activeCase)?.analysis?.issues?.find(i=>i.id===state.issue.id);state.issue=found||null;}
    await Promise.all([loadRiskRecords(ids),loadSemantics(ids)]);if(seq!==state.sequence)return;renderAll();
  }
  async function openCase(id){
    if(!graph(id))await loadCases([id]);const g=graph(id);
    if(g&&SessionView.available(g)){if(['calls','entities'].includes(state.mode))state.mode='session';selectSession(id,{type:'event',eventId:g.session_artifact.events?.[0]?.event_id},false);}
    else if(g){state.mode='calls';state.tab='overview';renderLanes();selectNode(id,callNodes(g)[0]?.id||g.nodes?.[0]?.id);}if(riskCase(id)||poolCase(id)?.risk_validation)setTab('risk');showPanel('workspace');
  }
  function svg(tag,attrs={},text){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;}
  function badges(n,g){
    const assessment=window.SessionView?.nodeAssessment?.(g,n);
    if(assessment){const box=el('div','node-badges');for(const kind of [...new Set(arr(n.badges).map(b=>b.category).filter(c=>['rule','model','runtime'].includes(c)))])box.append(el('span','badge '+kind,categories[kind]));const riskStatuses=[...new Set(arr(g?.analysis?.issues).filter(issue=>arr(issue.node_ids).includes(n.id)).map(issue=>riskState(g,issue)).filter(status=>['suspected','input_warning'].includes(status)))];for(const status of riskStatuses)box.append(el('span','badge '+(status==='suspected'?'suspected':'warning'),status==='suspected'?'◇ 疑似风险':'△ 输入警告'));box.append(...assessment.badges.childNodes);return box;}
    const box=el('div','node-badges');const types=[...new Set(arr(n.badges).map(b=>b.category))];
    const riskStatuses=[...new Set(arr(g?.analysis?.issues).filter(issue=>arr(issue.node_ids).includes(n.id)).map(issue=>riskState(g,issue)).filter(Boolean))];
    for(const status of riskStatuses)box.append(el('span','badge '+(status==='confirmed'?'safety':status==='input_warning'?'warning':status==='suspected'?'suspected':'unknown'),({confirmed:'▲ 有证据的风险',input_warning:'△ 输入警告',suspected:'◇ 疑似风险',unknown:'？证据不足'})[status]||status));
    for(const type of types){if(type==='safety'&&riskStatuses.length||type==='uncertainty'&&riskStatuses.includes('unknown'))continue;box.append(el('span','badge '+type,type==='safety'?'▲ 安全风险':categories[type]||type));}
    if(!box.children.length)box.append(el('span','badge '+(n.visual_state||'neutral'),visual[n.visual_state]||visual.neutral));return box;
  }
  function callsView(g){
    const strip=el('div','call-strip'),nodes=callNodes(g);
    nodes.forEach((n,i)=>{const payload=callValue(g,n),target=payload.arguments?.path||payload.arguments?.url||payload.arguments?.command||payload.arguments?.target;
      const presented=window.SessionView?.nodeAssessment?.(g,n),nodeVisual=presented?.visual||n.visual_state||'neutral';
      const card=button('','call-card '+nodeVisual+(state.nodeId===n.id?' selected':''),()=>{selectNode(g.case_id,n.id);showPanel('detail');});
      card.dataset.nodeId=n.id;card.dataset.caseId=g.case_id;card.dataset.visualState=nodeVisual;card.setAttribute('aria-label',`${payload.tool||'工具调用'}，${target||'参数见详情'}，${n.assessment?.label||visual[nodeVisual]||visual.neutral}`);
      card.append(el('div','call-top',null,[el('span','call-number',String(i+1).padStart(2,'0')),el('strong','',payload.tool||'工具调用')]),el('span','call-target',target?short(baseName(target),40):'查看调用参数'),el('span','call-sub',`导入步骤 ${arr(n.action_steps).join('、')||'未提供'} · ${arr(n.fids).length} 条关联事实`),badges(n,g));
      card.title=target?String(target):n.entity_id;strip.append(card);
      if(i<nodes.length-1)strip.append(el('span','sequence-arrow','→'));
    });
    if(!nodes.length)strip.append(el('p','empty','当前记录没有已解析的调用节点。切换“实体关系”查看结构与事实。'));
    return strip;
  }
  function entitiesView(g){
    const order={trace:0,call:1,tool:2,agent:3,int:4,req:5,file:6,message:7};
    const nodes=[...arr(g.nodes)].sort((a,b)=>(order[a.kind]??9)-(order[b.kind]??9)||a.index.localeCompare(b.index));
    const width=Math.max(420,nodes.length*186+30),height=178,positions=new Map();
    const drawing=svg('svg',{class:'entity-svg',width:width*state.scale,height:height*state.scale,viewBox:`0 0 ${width} ${height}`,role:'group','aria-label':'来自事实参数的实体关系；连线不表示因果'});
    nodes.forEach((n,i)=>positions.set(n.id,{x:16+i*186,y:60}));
    for(const edge of arr(g.edges)){
      const a=positions.get(edge.source),b=positions.get(edge.target);if(!a||!b)continue;
      const x=a.x+80,y=b.x+80,line=svg('path',{d:`M ${x} 60 Q ${(x+y)/2} 6 ${y} 60`,class:'entity-edge'+([edge.source,edge.target].includes(state.nodeId)?' highlighted':'')});
      line.append(svg('title',{},`${edge.relation} · F${edge.fid} · 导入步骤 ${edge.step} · 事实参数关系`));drawing.append(line,svg('text',{x:(x+y)/2,y:28,'text-anchor':'middle',class:'edge-label'},short(edge.relation,22)+' · F'+edge.fid));
    }
    for(const n of nodes){const presented=window.SessionView?.nodeAssessment?.(g,n),nodeVisual=presented?.visual||n.visual_state||'neutral';const p=positions.get(n.id),group=svg('g',{class:'graph-node '+nodeVisual+(state.nodeId===n.id?' selected':''),'data-node-id':n.id,'data-case-id':g.case_id,'data-visual-state':nodeVisual,tabindex:0,role:'button','aria-label':n.entity_id+'，'+(n.assessment?.label||visual[nodeVisual]||visual.neutral)});
      const payload=n.kind==='call'?callValue(g,n):{},target=payload.arguments?.path||payload.arguments?.url;
      const unknownIntent=n.kind==='int'&&arr(g.record?.facts).filter(f=>arr(n.fids).includes(f.fid)).some(f=>f.value==='unknown'||f.value==='unknown_intent'||String(f.value).includes('unknown'));
      const names={trace:'轨迹',tool:'工具',agent:'执行者',int:'意图（核对出处）',req:'任务要求',file:'文件对象',message:'公开消息'},entityLabel=arr(g.session_artifact?.rows).find(row=>row.entity_id===n.entity_id)?.entity_label;
      group.append(svg('title',{},n.entity_id+'\n'+(window.I18n?.t(n.assessment?.label||n.reason||'')||n.assessment?.label||n.reason||'')+(unknownIntent?'\n未知意图占位，不是真实采集到的认知':'')),svg('rect',{x:p.x,y:p.y,width:166,height:83,rx:10}),svg('text',{x:p.x+12,y:p.y+25,class:'node-kind'},n.kind==='call'?short('call · '+(payload.tool||''),19):names[n.kind]||n.kind),svg('text',{x:p.x+12,y:p.y+48,class:'node-value'},short(unknownIntent?'未知占位':target?baseName(target):entityLabel||n.index,23)),svg('text',{x:p.x+12,y:p.y+69,class:'node-status'},short(window.I18n?.t(n.assessment?.label||visual[nodeVisual]||visual.neutral)||n.assessment?.label||visual[nodeVisual]||visual.neutral,24)));
      if(arr(n.badges).some(b=>b.category==='safety'))group.append(svg('text',{x:p.x+149,y:p.y+25,'text-anchor':'end',class:'risk-mark'},'▲'));
      const activate=()=>{selectNode(g.case_id,n.id);showPanel('detail');};group.addEventListener('click',activate);group.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();activate();}});drawing.append(group);
    }
    return drawing;
  }
  function renderLanes(){
    const box=$('#lanes');box.replaceChildren();$('#empty-state').hidden=!!state.graphs.length;
    const pages=Math.ceil(state.graphs.length/state.pageSize);state.page=Math.max(0,Math.min(state.page,Math.max(0,pages-1)));
    const shown=state.graphs.slice(state.page*state.pageSize,(state.page+1)*state.pageSize);
    shown.forEach(g=>{
      const card=el('article','trace-card'+(g.case_id===state.activeCase?' active':''));card.dataset.caseId=g.case_id;
      const header=el('div','trace-head'),title=button(caseTitle(g.case_id),'trace-title',()=>openCase(g.case_id));
      const issues=arr(g.analysis?.issues),error=issues.some(i=>['rule','model'].includes(i.category)&&i.state==='confirmed'),risk=issues.some(i=>i.category==='safety');
      header.append(el('div','trace-heading',null,[title,el('span','trace-origin',originLabel(g.case_id))]),el('div','trace-tags',null,[...(error?[pill('有错误判断','error')]:[]),...(risk?[pill('有安全风险','risk')]:[]),...(SessionView.available(g)?[pill(`${g.session_artifact.events.length} 个导入事件`)]:[pill('仅工具输入','unknown')]),pill(`${callNodes(g).length} 次调用`)]));
      const task=details('',[el('p','trace-task-full',g.record?.task||'未记录')]);task.classList.add('trace-task');task.querySelector('summary').replaceChildren(el('span','','用户任务原文'),document.createTextNode(': '),el('span',g.record?.task?'i18n-raw':'',g.record?.task?short(g.record.task,76):'原记录未提供任务'));
      const pane=el('div','lane-scroll');pane.tabIndex=0;pane.dataset.caseId=g.case_id;pane.setAttribute('aria-label',caseTitle(g.case_id)+'，左右滚动查看'+({session:'完整会话',states:'状态表',coverage:'覆盖核对',calls:'工具调用',entities:'实体关系'})[state.mode]);
      pane.append(state.mode==='calls'?callsView(g):state.mode==='entities'?entitiesView(g):SessionView.render(g,state.mode,{cloudVerified:!!cloudCase(g.case_id)&&!cloudCase(g.case_id).constructed,constructed:!!(cloudCase(g.case_id)?.constructed||riskCase(g.case_id)?.is_constructed||suiteCase(g.case_id)?.is_constructed),selection:g.case_id===state.activeCase?state.sessionSelection:null,onSelect:selection=>selectSession(g.case_id,selection)}));
      const footer=el('div','trace-foot');
      const scrollControls=el('div','lane-controls');for(const [text,dir]of [['←',-1],['→',1]]){const b=button(text,'lane-arrow',()=>pane.scrollBy({left:dir*Math.max(180,pane.clientWidth*.7),behavior:'smooth'}));b.setAttribute('aria-label',(dir<0?'向左':'向右')+'滚动 '+caseTitle(g.case_id));scrollControls.append(b);}footer.append(scrollControls);
      card.append(header,task,pane,footer);box.append(card);pane.scrollLeft=state.offsets.get(g.case_id+'|'+state.mode)||0;
      const updateArrows=()=>{const overflow=pane.scrollWidth>pane.clientWidth+1;scrollControls.hidden=!overflow;const arrows=scrollControls.querySelectorAll('button');arrows[0].disabled=pane.scrollLeft<=1;arrows[1].disabled=pane.scrollLeft+pane.clientWidth>=pane.scrollWidth-1;};
      pane.addEventListener('scroll',()=>{state.offsets.set(g.case_id+'|'+state.mode,pane.scrollLeft);updateArrows();});requestAnimationFrame(updateArrows);
    });
    $('#page-label').textContent=pages?`${state.page+1} / ${pages}`:'0 / 0';$('#prev-page').disabled=state.page<=0;$('#next-page').disabled=state.page>=pages-1;$('#page-size').value=state.pageSize;
    $('#loaded-count').textContent=`已载入 ${state.graphs.length} 条 · ${state.graphs.reduce((sum,g)=>sum+callNodes(g).length,0)} 次调用`;
    $$('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===state.mode);b.setAttribute('aria-pressed',String(b.dataset.view===state.mode));});
    for(const id of ['zoom-in','zoom-out','zoom-reset'])if($('#'+id))$('#'+id).disabled=state.mode!=='entities';
    if($('#zoom-reset'))$('#zoom-reset').textContent=Math.round(state.scale*100)+'%';
    $('#session-scope').replaceChildren(...(graph(state.activeCase)?[SessionView.scope(graph(state.activeCase))]:[]));
    $('#session-mode-note').textContent=({session:'从用户要求到最终答复：这里展示已保存的公开事件，不补造内部思考。',states:'对象是什么、状态在哪一步改变、证据从哪里来；点击格子查看四类指针。',coverage:'分别核对原始记录接入、结构事实提取、未支持语义和未观测认知。',calls:'此视图仅看工具调用。完整消息和状态变化请用前面的三个视图。',entities:'这里展示编译出的实体和事实关系，随事件变化的版本请切换“状态表”。'})[state.mode];
  }
  function selectSession(caseId,selection,openDetail=true){
    const g=graph(caseId);if(!g)return;state.activeCase=caseId;state.sessionSelection=selection;state.issue=null;state.tab='session';state.page=Math.floor(state.graphs.indexOf(g)/state.pageSize);
    const event=selection?.type==='event'?SessionView.eventBy(g,selection.eventId):null;
    if(event)state.nodeId=SessionView.nodeFor(g,event)?.id||null;else state.nodeId=null;
    renderAll();revealSelection();if(openDetail)showPanel('detail');
  }
  function selectNode(caseId,nodeId,issue=null){
    const g=graph(caseId);if(!g)return;state.activeCase=caseId;state.nodeId=nodeId||null;state.issue=issue;
    state.sessionSelection=null;if(state.tab==='session')state.tab='overview';
    const selectedNode=arr(g.nodes).find(node=>node.id===nodeId),selectedEvent=selectedNode?.kind==='message'?arr(g.session_artifact?.events).find(event=>SessionView.nodeFor(g,event)?.id===nodeId):null;
    if(selectedEvent){state.mode='session';state.sessionSelection={type:'event',eventId:selectedEvent.event_id};renderLanes();}
    else if(issue&&['states','coverage','session'].includes(state.mode)){state.mode='calls';renderLanes();}
    const index=state.graphs.indexOf(g),page=Math.floor(index/state.pageSize),changed=page!==state.page;state.page=page;
    if(issue)state.tab='overview';if(changed)renderLanes();
    $$('.call-card,.graph-node,.session-event').forEach(n=>n.classList.toggle('selected',n.dataset.nodeId===state.nodeId));
    $$('.trace-card').forEach(c=>c.classList.toggle('active',c.dataset.caseId===caseId));renderPool();renderContext();renderDetail();
    revealSelection(!!issue);
    if(issue)showPanel('detail');
  }
  function revealSelection(force=false){
    const card=$$('.trace-card').find(c=>c.dataset.caseId===state.activeCase),area=$('#lanes-scroll');
    if(card){
      const cardRect=card.getBoundingClientRect(),areaRect=area.getBoundingClientRect();
      if(force||cardRect.top<areaRect.top||cardRect.bottom>areaRect.bottom)area.scrollTop+=cardRect.top-areaRect.top-12;
      const node=state.tab==='session'&&state.sessionSelection?[...card.querySelectorAll('[data-session-selection]')].find(n=>{const s=JSON.parse(n.dataset.sessionSelection);return s.type===state.sessionSelection.type&&(s.type==='event'?s.eventId===state.sessionSelection.eventId:String(s.fid)===String(state.sessionSelection.fid)&&(!state.sessionSelection.eventId||s.eventId===state.sessionSelection.eventId));}):[...card.querySelectorAll('[data-node-id]')].find(n=>n.dataset.nodeId===state.nodeId),pane=card.querySelector('.lane-scroll');
      if(node&&pane){const r=node.getBoundingClientRect(),p=pane.getBoundingClientRect();if(r.left<p.left||r.right>p.right)pane.scrollLeft+=r.left-p.left-pane.clientWidth/2+r.width/2;const a=area.getBoundingClientRect();if(r.top<a.top||r.bottom>a.bottom)area.scrollTop+=r.top-a.top-20;}
    }
  }
  function renderContext(){
    $('#context-title').textContent=state.graphs.length>1?'多轨迹风险比较':'轨迹风险工作区';$('#context-subtitle').textContent=state.activeCase?caseTitle(state.activeCase):'';
    $('#session-scope').replaceChildren(...(graph(state.activeCase)?[SessionView.scope(graph(state.activeCase))]:[]));
    const job=latest(state.activeCase);$('#diagnose-active').disabled=!state.activeCase||(job&&!terminal.has(job.status));$('#diagnose-active').textContent=job&&!terminal.has(job.status)?(job.status==='queued'?'任务诊断排队中':'任务诊断中'):'任务诊断';
    const rjob=latestRisk(state.activeCase),riskPolicy=state.riskRecords.get(state.activeCase)?.policy||graph(state.activeCase)?.analysis?.risk_review?.policy;$('#risk-active').disabled=!state.activeCase||!riskPolicy||(rjob&&!RiskView.terminal.has(rjob.status));$('#risk-active').title=riskPolicy?'依据任务、轨迹、策略与证据调查风险行为':'本条未配置独立 risk_policy；可载入风险验收案例或导入带策略的文件包';$('#risk-active').textContent=rjob&&!RiskView.terminal.has(rjob.status)?(rjob.status==='queued'?'风险审查排队中':'风险审查 · '+(rjob.stage||rjob.phase||'进行中')):'完整风险审查';
    $('#export-open').disabled=!state.graphs.length;const pending=state.jobs.filter(j=>!terminal.has(j.status)).length+state.riskJobs.filter(j=>!RiskView.terminal.has(j.status)).length;$('#jobs-open').textContent=pending?`审查队列 · ${pending}`:'审查记录';
    window.Showcase?.sync();
    renderSemanticsSummary();
    const privacyHolder=$('#privacy-workflow');if(privacyHolder){const overview=window.PrivacyView&&(state.showcaseMode==='privacy'||state.tab==='privacy')?PrivacyView.summary(privacyContext()):null;privacyHolder.replaceChildren(...(overview?[overview]:[]));}
  }
  function allIssues(){return state.graphs.flatMap(g=>arr(g.analysis?.issues).map(i=>({g,i})));}
  function riskState(g,i){
    if(!['risk_rule','risk_model'].includes(i.source))return null;
    const records=state.riskRecords.get(g.case_id)||g.analysis?.risk_review||{},report=i.source==='risk_rule'?records.automatic:(latestRisk(g.case_id)||records.model)?.result;
    const finding=arr(report?.findings).find(f=>f.id===i.risk_finding_id);
    const status=i.risk_status||finding?.status||(['confirmed','violation_attempt'].includes(i.state)?'confirmed':i.state);
    return i.state==='unknown'&&status==='confirmed'?'unknown':status;
  }
  function matchesIssue({g,i},key){const status=riskState(g,i);return key==='all'||key==='risk_confirmed'&&status==='confirmed'||key==='input_warning'&&status==='input_warning'||key==='suspected'&&status==='suspected'||i.category===key||(key==='uncertainty'&&i.state==='unknown');}
  function renderIssues(){
    const issues=allIssues(),findings=issues.filter(({g,i})=>i.category!=='uncertainty'||['input_warning','suspected'].includes(riskState(g,i))),unique=new Set(issues.map(({g,i})=>g.case_id+'|'+i.id));
    $('#issue-summary').textContent=`${findings.length} 项发现 · ${new Set(findings.map(v=>v.g.case_id)).size} 条受影响 · ${issues.length-findings.length} 项检查限制`;
    $('#issue-summary').title=`全部载入轨迹共 ${unique.size} 个唯一问题；按问题计数，不按关联实体重复计数。`;
    const filters=$('#issue-filters');filters.replaceChildren();
    for(const [key,label]of Object.entries({all:'全部',risk_confirmed:'有证据的风险',input_warning:'输入警告',suspected:'疑似',...categories})){const count=issues.filter(item=>matchesIssue(item,key)).length;
      const b=button(`${label} ${count}`,'issue-filter'+(state.issueFilter===key?' active':''),()=>{state.issueFilter=key;renderIssues();});b.dataset.issueFilter=key;b.setAttribute('aria-pressed',String(state.issueFilter===key));filters.append(b);}
    const box=$('#issue-list');box.replaceChildren();
    for(const {g,i}of issues.filter(item=>matchesIssue(item,state.issueFilter))){
      const row=button('','issue-row '+i.category,()=>{const id=arr(i.node_ids).find(id=>g.nodes.some(n=>n.id===id&&n.kind==='call'))||arr(i.node_ids)[0];selectNode(g.case_id,id||null,i);if(['risk_rule','risk_model'].includes(i.source))setTab('risk');});row.dataset.issueId=i.id;row.dataset.caseId=g.case_id;
      const rs=riskState(g,i),displayLabel=i.display_label||null,coverageLimit=['policy_field_unavailable','unsupported','unconfigured','prerequisite_unavailable','not_applicable','not_evaluated','unvalidated'].includes(i.display_status),badgeText=coverageLimit?'检查限制':rs==='input_warning'?'输入警告':rs==='suspected'?'疑似风险':rs==='confirmed'?'有证据的风险':rs==='unknown'?'证据不足':categories[i.category]||i.category;
      row.append(pill(badgeText,coverageLimit?'neutral':rs==='input_warning'?'warning':rs==='suspected'?'suspected':i.category==='safety'?'risk':['rule','model'].includes(i.category)&&i.state==='confirmed'?'error':i.category==='uncertainty'||i.state==='unknown'?'unknown':'neutral'),el('span','issue-copy',null,[el('strong','',displayLabel||i.title),el('small','muted',`${caseTitle(g.case_id)} · ${arr(i.import_steps).length?'导入步骤 '+i.import_steps.join('、'):'轨迹级发现，无具体调用定位'}${i.source==='risk_rule'?' · 自动风险规则':i.source==='risk_model'?' · 模型风险调查':''}`)]),el('span','issue-arrow','↗'));box.append(row);
    }
    if(!box.children.length)box.append(el('p','empty',issues.length?'当前分类无发现':'当前检查无风险发现'));
  }
  function renderDetail(){
    const g=graph(state.activeCase),n=arr(g?.nodes).find(n=>n.id===state.nodeId);
    $('#detail-title').textContent=state.tab==='privacy'?'隐私来源与输出':state.tab==='cloud'?'数据来源':state.tab==='semantics'?'语义解析与来源':state.tab==='risk'?'风险行为审查':state.tab==='session'?(state.sessionSelection?.type==='fact'?'状态与证据指针':'原始事件与提取结果'):state.issue?'问题详情':n?.kind==='call'?'调用详情':n?'实体详情':'查看详情';$('#detail-caption').textContent=state.activeCase?caseTitle(state.activeCase):'点击事件、状态或问题，信息会显示在这里';
    $('[data-detail-tab="cloud"]').hidden=!cloudCase(state.activeCase);
    $$('[data-detail-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.detailTab===state.tab);b.setAttribute('aria-selected',String(b.dataset.detailTab===state.tab));});
    const content=$('#detail-content');const open=[...content.querySelectorAll('details[open]')].map(d=>d.querySelector('summary')?.textContent);const scroll=content.scrollTop;
    const risk=state.riskRecords.get(state.activeCase)||g?.analysis?.risk_review||{};
    if(state.tab==='privacy'&&window.PrivacyView){content.replaceChildren(PrivacyView.render(privacyContext()));for(const d of content.querySelectorAll('details'))if(open.includes(d.querySelector('summary')?.textContent))d.open=true;content.scrollTop=scroll;return;}
    if(state.tab==='semantics'){
      content.replaceChildren(SemanticsView.detail(semanticContext()));
      for(const d of content.querySelectorAll('details'))if(open.includes(d.querySelector('summary')?.textContent))d.open=true;content.scrollTop=scroll;return;
    }
    if(state.tab==='cloud'){
      const id=state.activeCase,value=state.cloudEvaluations.get(id),active=latestRisk(id),inProgress=active&&!RiskView.terminal.has(active.status);
      content.replaceChildren(CloudView.render({caseInfo:cloudCase(id),displayTitle:caseTitle(id),evaluation:inProgress?{case_id:id,ready:false,job_status:active.status}:value,error:value?.error,loading:state.cloudLoading.has('evaluation:'+id),reference:inProgress?null:state.cloudReferences.get(id),referenceLoading:state.cloudLoading.has('reference:'+id),referenceError:state.cloudReferenceErrors.get(id),onReload:()=>loadCloudEvaluation(id),onReveal:()=>revealCloudReference(id)}));
      for(const d of content.querySelectorAll('details'))if(open.includes(d.querySelector('summary')?.textContent))d.open=true;content.scrollTop=scroll;return;
    }
    const showcaseTab=window.Showcase?.renderTab(state.tab);
    content.replaceChildren(showcaseTab||(state.tab==='risk'?RiskView.render({graph:g,...risk,model:latestRisk(state.activeCase)||risk.model,validation:riskCase(state.activeCase),reference:state.riskReferences.get(state.activeCase),onReview:startRiskReview,onCancel:cancelRiskReview,onReveal:revealRiskReference,onLocate:(finding,source)=>locateRiskFinding(g.case_id,finding,source),onEvidence:(finding,source)=>{locateRiskFinding(g.case_id,finding,source);setTab('evidence');}}):state.tab==='session'?SessionView.detail(g,state.sessionSelection,{onSelect:selection=>selectSession(g?.case_id,selection),onCall:id=>{state.mode='calls';renderLanes();selectNode(g.case_id,id);}}):StudioDetails.render({graph:g,node:n,issue:state.issue,tab:state.tab,job:latest(state.activeCase)||g?.analysis?.diagnosis,jobs:state.jobs.filter(j=>j.case_id===state.activeCase),suite:suiteFor(state.activeCase),reference:state.reference,configuration:state.configuration,onLocate:selectNode,onDiagnose:startDiagnosis,onCancel:cancelDiagnosis,onReveal:revealReference,onLoadValidation:loadValidation,onSetTab:setTab})));
    for(const d of content.querySelectorAll('details'))if(open.includes(d.querySelector('summary')?.textContent))d.open=true;content.scrollTop=scroll;
    if(state.tab==='risk'&&cloudCase(state.activeCase))content.append(button('数据来源','studio-button',()=>setTab('cloud')));
  }
  function setTab(tab){if(tab==='cloud'&&!cloudCase(state.activeCase)){toast('请先选择本组的真实云端轨迹或两个构造对照。');return;}state.tab=tab;$('#detail-content').scrollTop=0;renderDetail();renderContext();showPanel('detail');if(tab==='cloud'&&!state.cloudEvaluations.has(state.activeCase))loadCloudEvaluation(state.activeCase);}
  function renderAll(){renderPool();renderLanes();renderContext();renderIssues();renderDetail();}
  async function refresh({reload=true}={}){
    const results=await Promise.allSettled([api('/api/safety/state'),api('/api/safety/diagnosis/jobs'),api('/api/safety/validation'),api('/api/safety/risk/jobs'),api('/api/safety/risk/validation')]);
    if(results[0].status==='rejected')throw results[0].reason;
    state.pool=results[0].value.cases;state.policy=results[0].value.policy;
    state.workspaceIds=new Set(arr(results[0].value.workspace_case_ids));state.protectedIds=new Set(arr(results[0].value.protected_case_ids));
    const available=new Set(state.pool.map(c=>c.id));state.selected=new Set([...state.selected].filter(id=>available.has(id)));state.graphs=state.graphs.filter(g=>available.has(g.case_id));
    if(state.activeCase&&!available.has(state.activeCase)){state.activeCase=state.graphs[0]?.case_id||null;state.nodeId=null;state.issue=null;state.sessionSelection=null;}
    if(results[1].status==='fulfilled'){state.jobs=arr(results[1].value.jobs);state.configuration=results[1].value.configuration;}
    if(results[2].status==='fulfilled')state.suites=arr(results[2].value.suites);
    if(results[3].status==='fulfilled'){state.riskJobs=arr(results[3].value.jobs);state.riskConfiguration=results[3].value.configuration;}
    if(results[4].status==='fulfilled')state.riskCases=arr(results[4].value.cases);
    if(state.reference&&!state.suites.some(s=>s.id===state.reference.comparison?.suite_id&&s.reference_available))state.reference=null;
    $('#service-status').textContent='本地服务已连接';$('#service-status').classList.add('connected');
    if(reload&&state.graphs.length&&!state.caseMutation)await reloadGraphs();else renderAll();renderCloudIntro();
    const failures=results.slice(1).filter(r=>r.status==='rejected');if(failures.length)message('轨迹已载入，但诊断或验证状态暂不可用：'+failures.map(r=>r.reason.message).join('；'),true);
  }
  async function loadValidation(){
    if(!state.suites.length){toast('当前没有已注册的定位验证样例。');return;}
    state.scope='all';state.filter='all';state.search='';$('#pool-scope').value=$('#pool-filter').value='all';$('#pool-search').value='';
    await loadCases(state.suites[0].cases.map(c=>c.case_id),{context:'已载入验证样例'});setTab('validation');
  }
  async function loadRiskRecords(ids){
    for(let i=0;i<ids.length;i+=4){const chunk=ids.slice(i,i+4),results=await Promise.allSettled(chunk.map(id=>api('/api/safety/risk/'+encodeURIComponent(id))));
      results.forEach((result,index)=>{const id=chunk[index];if(result.status==='fulfilled')state.riskRecords.set(id,result.value);else state.riskRecords.set(id,{...(state.riskRecords.get(id)||graph(id)?.analysis?.risk_review||{}),error:'风险报告暂不可用：'+result.reason.message});});
    }
  }
  async function loadSemantics(ids){
    for(let i=0;i<ids.length;i+=4){const chunk=ids.slice(i,i+4),results=await Promise.allSettled(chunk.map(id=>api('/api/safety/semantics/'+encodeURIComponent(id))));
      results.forEach((result,index)=>{const id=chunk[index];state.semanticRecords.set(id,result.status==='fulfilled'?result.value:{...(state.semanticRecords.get(id)||{}),error:result.reason.message});});
    }
  }
  function semanticContext(){const id=state.activeCase;return {graph:graph(id),semantic:state.semanticRecords.get(id),job:latestRisk(id),policy:state.riskRecords.get(id)?.policy||graph(id)?.analysis?.risk_review?.policy,onDetails:()=>setTab('semantics'),onRisk:()=>setTab('risk'),onPolicy:openRiskPolicy,onLocate:item=>{
    const g=graph(id),event=arr(g?.session_artifact?.events).find(event=>Number(event.step)===Number(item.step));
    if(event){state.mode='session';selectSession(id,{type:'event',eventId:event.event_id});revealSelection(true);}
    else{const node=arr(g?.nodes).find(node=>arr(node.action_steps).includes(item.step));if(node){state.mode='calls';renderLanes();selectNode(id,node.id);showPanel('detail');}else toast('这条提取结果引用任务或原始字段，请展开来源核对。');}
  }};}
  function renderSemanticsSummary(){const holder=$('#semantic-workflow');if(holder)holder.replaceChildren(SemanticsView.summary(semanticContext()));}
  function privacyReferenceEvent(g,ref){
    if(!g||!ref)return null;const events=arr(g.session_artifact?.events);
    const callMatches=event=>{if(!ref.call_id)return true;const node=SessionView.nodeFor(g,event);return [event.call_id,node?.index,node?.entity_id,node?.entity_id?.replace(/^call:/,'')].includes(ref.call_id);};
    if(ref.event_id){const exact=events.find(event=>event.event_id===ref.event_id);return exact&&callMatches(exact)?exact:null;}
    const step=ref.step??ref.import_step;if(step!=null){const exact=events.filter(event=>Number(event.step)===Number(step)&&callMatches(event));return exact.length===1?exact[0]:null;}
    if(ref.call_id){const matches=events.filter(callMatches);const preferred=matches.filter(event=>ref.module==='action'?event.type==='tool_call':event.type==='tool_result');if(preferred.length===1)return preferred[0];if(matches.length===1)return matches[0];}
    return null;
  }
  function privacyContext(){
    const id=state.activeCase,g=graph(id),risk=state.riskRecords.get(id)||g?.analysis?.risk_review||{},info=cloudCase(id);
    return {graph:g,...risk,model:latestRisk(id)||risk.model,constructed:!!(info?.constructed||riskCase(id)?.is_constructed||suiteCase(id)?.is_constructed),sourceLabel:originLabel(id),onReview:startRiskReview,onCancel:cancelRiskReview,onChange:renderAll,onOpen:()=>setTab('privacy'),onCompare:info?()=>setTab('cloud'):null,canLocate:ref=>!!privacyReferenceEvent(g,ref),onLocate:ref=>{const event=privacyReferenceEvent(g,ref);if(!event)return;state.mode='session';selectSession(id,{type:'event',eventId:event.event_id});revealSelection(true);}};
  }
  function renderCloudIntro(){
    if(!state.cloudManifest)return;let box=$('#cloud-demo-intro');if(!box){box=el('section','cloud-demo-intro');box.id='cloud-demo-intro';$('.library-panel').prepend(box);}
    const ids=arr(state.cloudManifest.cases).map(item=>item.case_id);
    const toggle=button(state.cloudFocus?'查看全部历史轨迹':state.showcaseMode==='privacy'?'返回本次工作区':'精选轨迹','semantic-link',()=>{
      state.cloudFocus=!state.cloudFocus;state.scope='all';state.filter='all';state.search='';$('#pool-scope').value='all';$('#pool-filter').value='all';$('#pool-search').value='';
      const url=new URL(location.href);if(state.cloudFocus)url.searchParams.delete('library');else url.searchParams.set('library','all');history.replaceState(null,'',url);renderCloudIntro();renderPool();
    });toggle.id='library-toggle';
    box.replaceChildren(el('div','cloud-library-heading',null,[el('h2','',state.cloudFocus?(state.showcaseMode==='privacy'?'隐私审查':'OpenClaw'):'全部历史轨迹'),el('span','count-label',String(state.pool.filter(c=>inLibrary(c.id)).length))]),button(state.showcaseMode==='privacy'?'比较轨迹一和轨迹二':'比较三条轨迹','button full-width',()=>loadCases(ids)),toggle);
    document.body.classList.toggle('cloud-demo-focus',state.cloudFocus);
  }
  async function startCloudDemo(){
    state.cloudManifest=await api('/api/safety/cloud-showcase');state.cloudFocus=true;renderCloudIntro();
    const requested=new URLSearchParams(location.search).getAll('case').filter(id=>poolCase(id));const ids=requested.length?requested:arr(state.cloudManifest.cases).slice(0,1).map(item=>item.case_id);
    await loadCases(ids,{context:'已载入真实云端验收记录'});setTab('semantics');showPanel('workspace');
  }
  async function startPrivacyDemo(manifest){
    state.showcaseMode='privacy';state.cloudManifest=manifest||await api('/api/safety/privacy-showcase');for(const item of arr(state.cloudManifest.cases)){state.workspaceIds.add(item.case_id);state.protectedIds.add(item.case_id);}state.cloudFocus=new URLSearchParams(location.search).get('library')!=='all';renderCloudIntro();
    const requested=new URLSearchParams(location.search).getAll('case').filter(id=>poolCase(id));const ids=requested.length?requested:arr(state.cloudManifest.cases).slice(0,1).map(item=>item.case_id);
    await loadCases(ids);setTab('privacy');showPanel('workspace');
  }
  async function loadCloudEvaluation(id){
    if(!cloudCase(id)||state.cloudLoading.has('evaluation:'+id))return;state.cloudLoading.add('evaluation:'+id);renderDetail();
    const jobId=latestRisk(id)?.id;
    try{const value=await api('/api/safety/'+(state.showcaseMode==='privacy'?'privacy-showcase':'cloud-showcase')+'/'+encodeURIComponent(id)+'/evaluation');if(latestRisk(id)?.id!==jobId)return;state.cloudEvaluations.set(id,value);if(!value.ready)state.cloudReferences.delete(id);}
    catch(e){state.cloudEvaluations.set(id,{error:e.message});}
    finally{state.cloudLoading.delete('evaluation:'+id);if(state.activeCase===id&&state.tab==='cloud')renderDetail();}
  }
  async function revealCloudReference(id){
    if(!cloudCase(id)||!state.cloudEvaluations.get(id)?.ready||state.cloudLoading.has('reference:'+id))return;
    state.cloudLoading.add('reference:'+id);state.cloudReferenceErrors.delete(id);renderDetail();
    const jobId=latestRisk(id)?.id;
    try{const value=await api('/api/safety/'+(state.showcaseMode==='privacy'?'privacy-showcase':'cloud-showcase')+'/'+encodeURIComponent(id)+'/reference');if(latestRisk(id)?.id===jobId)state.cloudReferences.set(id,value);}
    catch(e){state.cloudReferences.delete(id);state.cloudReferenceErrors.set(id,e.message);}
    finally{state.cloudLoading.delete('reference:'+id);if(state.activeCase===id&&state.tab==='cloud')renderDetail();}
  }
  function openRiskPolicy(){
    const id=state.activeCase;if(!id)return;const current=state.riskRecords.get(id)?.policy||graph(id)?.analysis?.risk_review?.policy;
    const editor=el('textarea','semantic-policy-editor');editor.id='risk-policy-editor';editor.spellcheck=false;editor.setAttribute('aria-label','本条轨迹的风险策略 JSON');editor.value=JSON.stringify(current||{},null,2);
    const feedback=el('p','muted','编辑只影响本条轨迹。保存后重新进行自动检查；完整模型审查需要点击按钮另行运行。');feedback.id='risk-policy-feedback';
    const editing=details('高级编辑 · 风险策略 JSON',[el('p','muted','请使用真实任务允许范围，不能把模型猜测或 Agent 自称的批准写成确定授权。'),editor]);editing.open=!current;
    const save=button('保存策略并重新检查','primary',async()=>{
      try{const value=JSON.parse(editor.value);if(!value||typeof value!=='object'||Array.isArray(value))throw Error('策略需要是 JSON 对象。');save.disabled=true;feedback.textContent='正在校验策略并重新检查…';await api('/api/safety/risk-policy',{case_id:id,risk_policy:value});state.cloudEvaluations.delete(id);state.cloudReferences.delete(id);state.cloudReferenceErrors.delete(id);await refresh();feedback.textContent='策略已保存，自动检查已更新。原模型结果仍保留，请重新运行完整风险审查以使用新策略。';toast('策略已保存；可重新运行完整风险审查。');d.close();}
      catch(e){feedback.textContent='未保存：'+e.message;feedback.classList.add('import-errors');}finally{save.disabled=false;}
    });save.id='risk-policy-save';
    const d=dialog('本条轨迹的安全边界',[SemanticsView.policyDetail(current),el('p','semantic-policy-warning','没有规则告警不等于全部安全。只有明确适用并已执行的检查，才显示通过。'),editing,feedback],[save]);d.classList.add('semantic-policy-dialog');
  }
  async function loadRiskValidation(group='development'){
    group=group==='holdout'?'holdout':'development';
    const members=state.riskCases.filter(c=>(c.validation_group||'development')===group);
    if(!members.length){toast('当前没有已注册的这组风险验收案例。');return;}
    state.scope=group==='holdout'?'risk_holdout':'risk_validation';state.filter='all';state.search='';$('#pool-scope').value=state.scope;$('#pool-filter').value='all';$('#pool-search').value='';state.mode='calls';
    await loadCases(members.map(c=>c.case_id),{context:group==='holdout'?'已载入独立保留测试集':'已载入开发验收案例'});setTab('risk');showPanel('workspace');
  }
  function locateRiskFinding(caseId,finding,source){
    const g=graph(caseId),node=RiskView.nodeFor(g,finding);if(!node){toast('报告未能精确对应到调用或公开消息，保留轨迹级发现。');return;}
    const issue=arr(g.analysis?.issues).find(i=>i.source===source&&(i.risk_finding_id===finding.id||i.finding_id===finding.id||i.id===finding.id||i.id?.endsWith(':'+finding.id)))||null;
    state.mode=node.kind==='message'?'session':'calls';renderLanes();selectNode(caseId,node.id,issue);setTab('risk');revealSelection(true);
    requestAnimationFrame(()=>{const target=[...$('#detail-content').querySelectorAll('[data-finding-id]')].find(card=>card.dataset.findingId===finding.id);if(target)$('#detail-content').scrollTop+=target.getBoundingClientRect().top-$('#detail-content').getBoundingClientRect().top-12;});
  }
  async function startRiskReview(ids){
    ids=[...new Set(arr(ids).filter(Boolean))];if(!ids.length)return;if(ids.length>20){toast('一次完整风险审查最多选择 20 条。');return;}
    try{const data=await api('/api/safety/risk/review',{case_ids:ids});for(const job of arr(data.jobs))state.riskJobs=[...state.riskJobs.filter(j=>j.id!==job.id),job];
      if(data.configuration)state.riskConfiguration=data.configuration;for(const id of ids){state.riskReferences.delete(id);state.cloudEvaluations.delete(id);state.cloudReferences.delete(id);state.cloudReferenceErrors.delete(id);}window.Showcase?.invalidate(ids);
      state.tab='risk';renderAll();showPanel('detail');const errors=arr(data.errors);message(errors.length?'部分风险审查未启动：'+errors.map(error=>typeof error==='string'?error:(error.case_id||'')+' '+(error.error||error.message||raw(error))).join('；'):'',!!errors.length);
      toast(`已提交 ${arr(data.jobs).length} 条完整风险审查。自动规则结果继续保留。`);schedulePoll(300);
    }catch(e){message('风险审查未启动：'+e.message,true);}
  }
  async function cancelRiskReview(id){
    try{const job=await api('/api/safety/risk/jobs/'+encodeURIComponent(id)+'/cancel',{});state.riskJobs=state.riskJobs.map(j=>j.id===id?job:j);renderContext();renderDetail();renderJobDialog();toast('已请求取消风险审查，保留自动规则和已保存结果。');schedulePoll(300);}catch(e){message(e.message,true);}
  }
  async function revealRiskReference(id){
    try{state.riskReferences.set(id,await api('/api/safety/risk/validation/'+encodeURIComponent(id)+'/reference'));renderDetail();}catch(e){message('独立答案尚不能打开：'+e.message,true);}
  }
  function dialog(title,body,actions=[]){
    const d=el('dialog','studio-dialog');const close=button('×','dialog-close',()=>d.close());close.setAttribute('aria-label','关闭'+title);
    const heading=el('h2','',title);heading.id='modal-title-'+Date.now();d.setAttribute('aria-labelledby',heading.id);
    d.append(el('div','dialog-head',null,[heading,close]),el('div','dialog-body',null,arr(body).length?body:[body]),el('div','dialog-actions',null,actions));
    d.addEventListener('close',()=>d.remove());d.addEventListener('click',e=>{if(e.target===d){const rect=d.getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)d.close();}});document.body.append(d);d.showModal();return d;
  }
  function resetLibraryFilters(){state.scope='all';state.filter='all';state.search='';$('#pool-scope').value=$('#pool-filter').value='all';$('#pool-search').value='';}
  function clearCaseCaches(ids){
    for(const id of ids){state.selected.delete(id);for(const cache of [state.riskRecords,state.riskReferences,state.semanticRecords,state.cloudEvaluations,state.cloudReferences])cache.delete(id);}
    state.graphs=state.graphs.filter(g=>!ids.includes(g.case_id));state.jobs=state.jobs.filter(job=>!ids.includes(job.case_id));state.riskJobs=state.riskJobs.filter(job=>!ids.includes(job.case_id));
    if(ids.includes(state.activeCase)){state.activeCase=state.graphs[0]?.case_id||null;state.nodeId=null;state.issue=null;state.sessionSelection=null;}
    state.page=Math.min(state.page,Math.max(0,Math.ceil(state.graphs.length/state.pageSize)-1));
  }
  function openDeleteCases(requested){
    if(state.caseMutation||state.loading)return;const ids=[...new Set(requested)].filter(removableCase),skipped=[...new Set(requested)].filter(id=>!removableCase(id));
    if(!ids.length){toast('所选轨迹受保护或正在分析，不能删除。');return;}
    const list=el('ul','case-change-list');for(const id of ids)list.append(el('li','',null,[el('strong','',caseTitle(id)),el('span','mono',id)]));
    const feedback=el('p','muted','轨迹移入回收站，原始文件与分析结果保留，可以恢复。');
    const body=[el('p','',`将删除 ${ids.length} 条轨迹：`),list,feedback];if(skipped.length)body.push(el('p','muted',`保留 ${skipped.length} 条受保护或正在分析的轨迹。`));
    const cancel=button('取消','button',()=>d.close());cancel.id='delete-cancel';
    const confirm=button('确认删除','button danger',async()=>{
      if(state.caseMutation)return;state.caseMutation=true;++state.sequence;confirm.disabled=true;cancel.disabled=true;d.querySelector('.dialog-close').disabled=true;renderSelection();
      try{const result=await api('/api/safety/cases/delete',{case_ids:ids}),removed=arr(result.deleted);clearCaseCaches(removed);await refresh({reload:false});renderCloudIntro();
        if(!state.graphs.length){const next=filtered()[0];if(next)await loadCases([next.id]);}
        d.close();toast(`已删除 ${removed.length} 条轨迹，可在回收站恢复。`);
      }catch(e){feedback.textContent=e.message;feedback.classList.add('import-errors');}
      finally{state.caseMutation=false;confirm.disabled=false;cancel.disabled=false;d.querySelector('.dialog-close').disabled=false;renderAll();}
    });confirm.id='delete-confirm';
    const d=dialog('删除轨迹',body,[cancel,confirm]);d.id='delete-dialog';d.addEventListener('cancel',e=>{if(state.caseMutation)e.preventDefault();});d.addEventListener('click',e=>{if(state.caseMutation&&e.target===d)e.stopImmediatePropagation();},true);cancel.focus();
  }
  async function openDeletedCases(){
    const box=el('div','deleted-case-list'),feedback=el('p','muted','正在读取回收站…');const d=dialog('回收站',[feedback,box]);d.id='deleted-dialog';let running=false;
    async function populate(){
      try{const data=await api('/api/safety/cases/deleted');if(!d.isConnected)return;box.replaceChildren();feedback.textContent=arr(data.cases).length?'选择轨迹恢复到工作区。':'回收站为空。';
        for(const item of arr(data.cases)){const id=item.case_id||item.id,row=el('div','deleted-case-row'),restore=button('恢复','button',async()=>{
          if(running)return;running=true;state.caseMutation=true;for(const b of box.querySelectorAll('button'))b.disabled=true;
          try{const result=await api('/api/safety/cases/restore',{case_ids:[id]});await refresh({reload:false});resetLibraryFilters();renderCloudIntro();if(arr(result.restored).includes(id))await loadCases([id]);await populate();toast('轨迹已恢复。');}
          catch(e){feedback.textContent=e.message;feedback.classList.add('import-errors');}
          finally{running=false;state.caseMutation=false;for(const b of box.querySelectorAll('button'))b.disabled=false;renderAll();}
        });restore.dataset.restoreCase=id;row.append(el('div','deleted-case-copy',null,[el('strong','',item.title||id),el('span','mono',id),el('small','muted',item.deleted_at||'')]),restore);box.append(row);}
      }catch(e){feedback.textContent=e.message;feedback.classList.add('import-errors');}
    }
    await populate();
  }
  function field(label,input){const wrap=el('label','form-field',null,[el('span','',label),input]);return wrap;}
  function openSample(){
    const count=el('input');count.type='number';count.min=1;count.max=MAX;count.value=4;count.id='sample-count';
    const seed=el('input');seed.placeholder='可留空；相同种子可复现抽样';seed.id='sample-seed';seed.maxLength=64;
    const receipt=el('p','muted',`在当前筛选的 ${filtered().length} 条轨迹中随机抽取；不会执行 Agent。`);receipt.id='sample-receipt';
    const start=button('抽取并查看','primary',async()=>{
      const n=Number(count.value);if(!Number.isInteger(n)||n<1||n>MAX){receipt.textContent='请输入 1–24 的整数。';return;}
      start.disabled=true;try{
        // Server sampling receives the same textual/origin/status filters.
        const eligibleIds=new Set(filtered().map(c=>c.id)),payload={count:n,case_ids:[...eligibleIds]};if(seed.value.trim())payload.seed=seed.value.trim();if(state.search.trim())payload.query=state.search.trim();if(state.filter!=='all')payload.statuses=[state.filter];
        if(!['all','validation','session','risk_validation','risk_holdout'].includes(state.scope))payload.origins=[state.scope];
        // All registered examples fit in one backend sample. Filter a full
        // randomized ordering to preserve uniform selection within this UI subset.
        if(state.scope==='validation'){payload.query='定位验证 ·';payload.count=24;}
        if(['risk_validation','risk_holdout'].includes(state.scope)){payload.risk_validation_only=true;payload.risk_validation_group=state.scope==='risk_holdout'?'holdout':'development';payload.count=24;}
        if(state.scope==='session')payload.session_only=true;
        const result=await api('/api/safety/sample',payload);
        if(['validation','risk_validation','risk_holdout'].includes(state.scope)){
          if(result.eligible_count>24)throw Error('这个范围超过一次可完整抽取的集合，请使用“全部来源”配合名称搜索来抽样。');
          result.case_ids=result.case_ids.filter(id=>eligibleIds.has(id)).slice(0,n);
          result.backend_filters={...result.filters};result.filters={...result.filters,ui_scope:state.scope,ui_query:state.search};
          result.requested_count=n;result.eligible_count=eligibleIds.size;result.selected_count=result.case_ids.length;result.pool_exhausted=n>eligibleIds.size;
        }
        state.lastSample=result;seed.value=result.seed;receipt.textContent=`从 ${result.eligible_count} 条中抽取 ${result.selected_count} 条；种子 ${result.seed}`;
        if(!result.case_ids.length){receipt.textContent='当前筛选没有可抽取的轨迹。';return;}
        await loadCases(result.case_ids,{context:'已随机抽取'});d.close();toast(`抽取 ${result.selected_count} 条；种子 ${result.seed}（导出时保留）`);
      }catch(e){receipt.textContent=e.message;}finally{start.disabled=false;}
    });start.id='sample-submit';const d=dialog('随机抽取已有轨迹',[field('抽取数量',count),field('随机种子（选填）',seed),receipt],[start]);
  }
  function importPayload(item,policyText){
    if(item.parseError)throw Error(item.parseError);const data=item.data;if(!data||typeof data!=='object'||Array.isArray(data))throw Error('JSON 顶层需要是对象。');
    const wrapped=Object.hasOwn(data,'source'),source=wrapped?data.source:data,policy=wrapped?data.policy:JSON.parse(policyText),title=wrapped?(data.title||item.name):item.name;
    if(source?.task_type!=='openclaw'||!Array.isArray(source.steps)||source.steps.length<1||source.steps.length>500)throw Error('请使用已转换的 OpenClaw input.json，包含 1–500 个步骤。');
    if(!policy||typeof policy!=='object'||Array.isArray(policy))throw Error('请提供匹配此轨迹的安全策略。');
    if(typeof title!=='string')throw Error('轨迹名称需要是文本。');const payload={source,policy,title};if(wrapped&&data.native_transcript)payload.native_transcript=data.native_transcript;if(wrapped&&data.risk_policy){if(typeof data.risk_policy!=='object'||Array.isArray(data.risk_policy))throw Error('risk_policy 需要是独立策略对象。');payload.risk_policy=data.risk_policy;}if(new TextEncoder().encode(JSON.stringify(payload)).byteLength>BYTES)throw Error('轨迹与策略合计超过 4 MB。');return payload;
  }
  function openImport(){
    let items=[],running=false;const MAX_RAW_FILE=8*1024*1024,MAX_RAW_REQUEST=16*1024*1024;
    const input=el('input');input.type='file';input.multiple=true;input.accept='.json,.jsonl,.ndjson,.txt,application/json,application/x-ndjson,text/plain';input.id='import-files';
    const drop=el('label','import-drop',null,[el('strong','','选择或拖入 SigNoz / 轨迹文件'),el('span','muted','支持 JSON、JSONL 和文本文件；同一轨迹的多个 Span 可以一起导入'),input]);
    const policy=el('textarea');policy.value=JSON.stringify(state.policy,null,2);policy.rows=7;policy.spellcheck=false;policy.id='import-policy';policy.setAttribute('aria-label','独立 input.json 的共同基础安全策略');
    const attachPolicy=el('input');attachPolicy.type='checkbox';attachPolicy.id='import-attach-policy';
    const riskPolicy=el('textarea','semantic-policy-editor');riskPolicy.rows=6;riskPolicy.style.minHeight='120px';riskPolicy.id='import-risk-policy';riskPolicy.spellcheck=false;riskPolicy.placeholder='可选：粘贴这批轨迹适用的风险策略 JSON';
    const task=el('textarea','import-task');task.id='import-task';task.placeholder='可选：仅当导出没有记录用户任务时，补充真实任务原文';
    const advanced=details('任务与安全边界 · 可选补充',[field('缺少任务记录时补充',task),field('风险审查策略（留空表示尚未配置）',riskPolicy),details('兼容旧 input.json 的基础安全策略',[el('p','muted','独立规范输入使用下方策略；带 source + policy 的文件包使用自身策略。云端原始记录默认不套用其他案例的权限。'),policy,el('label','',null,[attachPolicy,document.createTextNode(' 我已核对，将此基础策略也应用于本批云端记录')])])]);
    const previews=el('div','import-preview');previews.id='import-preview';
    const feedback=el('p','muted','先保存原文并完成结构解析及自动检查；点击“完整风险审查”后，才调用模型进行语义解析和风险调查。');feedback.id='import-feedback';
    const auditBox=el('div','import-audits');
    function riskValue(){if(!riskPolicy.value.trim())return undefined;const value=JSON.parse(riskPolicy.value);if(!value||typeof value!=='object'||Array.isArray(value))throw Error('风险策略需要是 JSON 对象。');return value;}
    const submit=button('导入并查看','primary',async()=>{
      if(running)return;running=true;for(const control of [input,policy,riskPolicy,task,attachPolicy])control.disabled=true;const ids=[];render();
      try{
        const extraRisk=riskValue();
        for(const item of items.filter(i=>i.type==='canonical'&&i.valid&&!['success','partial'].includes(i.status))){
          item.status='submitting';render();try{const payload=importPayload(item,policy.value);if(extraRisk&&!payload.risk_policy)payload.risk_policy=extraRisk;const result=await api('/api/safety/import',payload);item.status='success';item.caseId=result.case.id;ids.push(result.case.id);item.error=null;}catch(e){item.status='failed';item.error=e.message;}render();
        }
        const cloud=items.filter(i=>i.type==='signoz'&&i.valid&&!['success','partial'].includes(i.status));
        if(cloud.length){
          cloud.forEach(item=>{item.status='submitting';item.error=null;});render();
          try{
            const payload={documents:cloud.map(item=>({name:item.name,text:item.text}))};if(task.value.trim())payload.task=task.value.trim();if(extraRisk)payload.risk_policy=extraRisk;if(attachPolicy.checked)payload.policy=JSON.parse(policy.value);
            if(new TextEncoder().encode(JSON.stringify(payload)).byteLength>MAX_RAW_REQUEST)throw Error('本批云端请求超过 16 MB，请减少文件或缩小导出时间范围。');
            const result=await api('/api/safety/import/signoz',payload),created=arr(result.cases).map(item=>typeof item==='string'?item:item.id||item.case_id||item.case?.id).filter(Boolean),errors=arr(result.errors);
            ids.push(...created);for(const item of cloud){item.status=created.length?(errors.length?'partial':'success'):'failed';item.error=errors.length?'本批有未导入记录，请核对下方说明':created.length?null:'未生成可分析轨迹，请查看导入核对。';}
            auditBox.replaceChildren(el('p','muted',`本批生成 ${created.length} 条轨迹；${errors.length} 项导入问题。原始来源与转换记录由服务保存。`));
            if(errors.length)auditBox.append(el('p','import-errors',errors.map(value=>typeof value==='string'?value:value.message||value.error||raw(value)).join('\n')));
            if(result.audits)auditBox.append(details('导入覆盖、来源及未支持记录',[code(result.audits)]));
          }catch(e){for(const item of cloud){item.status='failed';item.error=e.message;}}
          render();
        }
        if(ids.length){await refresh({reload:false});for(const id of ids)state.workspaceIds.add(id);resetLibraryFilters();renderCloudIntro();await loadCases([...new Set(ids)],{context:'已导入'});}
        feedback.textContent=ids.length?`已保存 ${new Set(ids).size} 条轨迹。关闭此窗口查看执行过程，再核对策略并运行完整风险审查。`:'尚未导入成功，请查看各文件提示。';
      }catch(e){feedback.textContent='未完成导入：'+e.message;}finally{running=false;for(const control of [input,policy,riskPolicy,task,attachPolicy])control.disabled=false;render();}
    });submit.id='import-submit';submit.disabled=true;
    function render(){
      previews.replaceChildren();let sharedError=null;try{riskValue();if(attachPolicy.checked){const value=JSON.parse(policy.value);if(!value||typeof value!=='object'||Array.isArray(value))throw Error('基础策略需要是 JSON 对象。');}}catch(e){sharedError=e.message;}
      for(const item of items){if(!['success','partial','submitting'].includes(item.status)){try{if(item.parseError)throw Error(item.parseError);if(sharedError)throw Error(sharedError);if(item.type==='canonical')item.payload=importPayload(item,policy.value);item.valid=true;item.localError=null;}catch(e){item.valid=false;item.localError=e.message;}}
        const status=item.status==='success'?'已导入':item.status==='partial'?'部分导入':item.status==='submitting'?'正在导入':item.error||item.localError?'待核对':'待导入';
        const row=el('div','import-file');row.dataset.state=item.status==='success'?'success':item.valid?'ready':'invalid';row.append(el('div','',null,[el('strong','',item.name),el('span','import-type',item.type==='canonical'?'规范轨迹':'SigNoz 原始记录'),el('p','muted',item.localError||item.error||(item.status==='success'?'已保存；解析覆盖与来源可在轨迹中核对':item.type==='canonical'?`${item.payload?.source.steps.length||0} 个导入步骤`:'服务将按真实 Trace 整理，保留原始字段'))]),pill(status,item.status==='success'?'passed':item.status==='partial'?'unknown':item.error||item.localError?'unknown':'neutral'));previews.append(row);
      }
      submit.disabled=running||!items.some(item=>item.valid&&!['success','partial'].includes(item.status));submit.textContent=running?'正在导入…':'导入并查看';
    }
    async function receive(files){
      if(running)return;items=[];auditBox.replaceChildren();if(files.length>MAX){feedback.textContent='本次最多导入 24 个文件，尚未提交任何文件。';render();return;}
      for(const file of [...files]){
        const item={name:file.name,status:'ready',type:'signoz'};
        try{
          if(file.size>MAX_RAW_FILE)throw Error('单文件超过 8 MB，请缩小导出范围。');item.text=await file.text();if(!item.text.trim())throw Error('文件为空。');
          try{item.data=JSON.parse(item.text);}catch{const rows=item.text.split(/\r?\n/).filter(line=>line.trim());for(const [index,line]of rows.entries()){try{JSON.parse(line);}catch{throw Error(`不是有效 JSON / JSONL：第 ${index+1} 行无法解析。`);}}}
          const source=item.data?.source||item.data;if(source?.task_type==='openclaw'&&Array.isArray(source.steps)){item.type='canonical';if(file.size>BYTES)throw Error('规范轨迹文件超过 4 MB。');}
        }catch(e){item.parseError=e.message;}items.push(item);
      }
      render();feedback.textContent=`已读取 ${items.length} 个文件。云端记录按 Trace 合并，原文保留；标准轨迹走原有导入流程。`;
    }
    input.addEventListener('change',()=>receive(input.files));for(const control of [policy,riskPolicy,attachPolicy])control.addEventListener('input',render);drop.addEventListener('dragover',e=>e.preventDefault());drop.addEventListener('drop',e=>{e.preventDefault();receive(e.dataTransfer.files);});
    const d=dialog('导入 SigNoz 或已有轨迹',[drop,previews,advanced,feedback,auditBox],[submit]);d.id='import-dialog';d.addEventListener('cancel',e=>{if(running)e.preventDefault();});
  }
  function openCapture(){
    const feedback=el('p','muted',state.captureId?'已有采集正在进行，可关闭窗口继续查看轨迹。':'执行本地受控工具，保存新的轨迹并自动审计。不会启动 OpenClaw Agent。');
    const start=button('开始受控采集','primary',async()=>{start.disabled=true;try{const job=await api('/api/safety/demo',{});state.captureId=job.job_id;d.close();toast('采集已启动，完成后会显示新轨迹。');pollCapture();}catch(e){feedback.textContent=e.message;start.disabled=false;}});start.id='capture-submit';start.disabled=!!state.captureId;
    const d=dialog('采集一组工具演示',[el('p','','这会实际运行项目已有的本地工具演示，用于观察通过、违规尝试及证据不足等情况。'),feedback,details('与真实 Agent 接入的区别',[el('p','','真实 OpenClaw 轨迹使用已保存输入导入。实时 Agent 接入仍是预留接口，当前页面不会把工具演示标成 OpenClaw Agent 运行。')])],[start]);
  }
  async function pollCapture(){
    if(!state.captureId)return;try{const job=await api('/api/safety/jobs/'+encodeURIComponent(state.captureId));message('采集进度：'+job.stage);
      if(job.state==='finished'){state.captureId=null;await refresh({reload:false});await loadCases(arr(job.case_ids),{context:'已采集并自动检查'});return;}
      if(['failed','error','cancelled'].includes(job.state)){state.captureId=null;message('采集未完成：'+raw(job.error||job.stage),true);return;}
      state.captureTimer=setTimeout(pollCapture,1800);
    }catch(e){message('暂时无法读取采集进度：'+e.message,true);state.captureTimer=setTimeout(pollCapture,5000);}
  }
  async function startDiagnosis(ids){
    if(!Array.isArray(ids)||!ids.length)return;if(ids.length>20){toast('一次完整诊断最多选择 20 条。');return;}
    try{const data=await api('/api/safety/diagnosis',{case_ids:ids});for(const job of data.jobs)state.jobs=[...state.jobs.filter(j=>j.id!==job.id),job];state.configuration=data.configuration;
      state.reference=null;for(const suite of state.suites)if(suite.cases.some(c=>ids.includes(c.case_id)))suite.reference_available=false;
      state.tab='diagnosis';renderAll();showPanel('detail');toast(`已提交 ${ids.length} 条完整诊断；规则及安全结果继续保留。`);await reloadGraphs();schedulePoll(300);
    }catch(e){message('诊断未启动：'+e.message,true);}
  }
  async function cancelDiagnosis(id){try{const job=await api('/api/safety/diagnosis/jobs/'+encodeURIComponent(id)+'/cancel',{});state.jobs=state.jobs.map(j=>j.id===id?job:j);renderContext();renderDetail();if(jobDialog?.open)renderJobDialog();toast('已取消诊断，保留已有分析结果。');schedulePoll(300);}catch(e){message(e.message,true);}}
  async function revealReference(suiteId){
    try{state.reference=await api('/api/safety/validation/'+encodeURIComponent(suiteId)+'/reference');renderDetail();}
    catch(e){state.reference=null;renderDetail();message('独立答案暂不能打开：'+e.message,true);}
  }
  function renderJobDialog(){
    if(!jobDialog?.open)return;const body=jobDialog.querySelector('.dialog-body'),open=[...body.querySelectorAll('details[open]')].map(d=>d.dataset.jobId);body.replaceChildren();
    
    const riskGroup=el('section','risk-queue-group',null,[el('h3','','风险审查队列与历史')]);
    if(state.riskConfiguration)riskGroup.append(details('风险审查预算',[fields([['模型',state.riskConfiguration.model],['凭据',state.riskConfiguration.credential_available?'已有配置可用（不展示密钥）':'未配置'],['总时限',state.riskConfiguration.wall_timeout_seconds?state.riskConfiguration.wall_timeout_seconds+' 秒':undefined]]),code(state.riskConfiguration.budgets||{})]));
    for(const job of [...state.riskJobs].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)))){
      const panel=details(`${caseTitle(job.case_id)} · ${RiskView.label(job.status)} · ${job.stage||job.phase||'等待阶段记录'}`,[RiskView.jobView(job,{onCancel:cancelRiskReview}),button('打开本轨迹的最新审查','studio-button subtle',async()=>{jobDialog.close();await openCase(job.case_id);setTab('risk');})]);panel.dataset.jobId=job.id;panel.open=open.includes(job.id);riskGroup.append(panel);
    }
    if(!state.riskJobs.length)riskGroup.append(el('p','empty small','尚无模型风险审查。可先查看自动规则，再点击“完整风险审查”。'));body.append(riskGroup,el('h3','','任务诊断记录'));
    const config=state.configuration||{};body.append(details('模型配置与预算',[fields([['模型',config.model],['凭据是否可用',config.credential_available?'可用（不展示密钥）':'未配置'],['总时限',config.wall_timeout_seconds+' 秒']]),code(config.budgets||{})]));
    for(const job of [...state.jobs].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)))){
      const panel=details(`${caseTitle(job.case_id)} · ${name(job.status)}${job.result?' · '+name(job.result.status):' · '+job.phase}`,[AnalysisUI.renderDiagnosis(job)]);panel.dataset.jobId=job.id;panel.open=open.includes(job.id);
      if(!terminal.has(job.status))panel.append(button('取消这条诊断','danger',()=>cancelDiagnosis(job.id)));body.append(panel);
    }
    if(!state.jobs.length)body.append(el('p','empty','尚无模型诊断记录。可在工作区诊断当前轨迹。'));
  }
  function openJobs(){jobDialog=dialog('审查队列与历史',[el('p','','读取审查记录…')]);jobDialog.id='jobs-dialog';renderJobDialog();}
  function schedulePoll(delay){clearTimeout(timer);timer=setTimeout(pollJobs,delay);}
  async function pollJobs(){
    if(state.pollBusy||state.caseMutation||document.hidden){schedulePoll(8000);return;}state.pollBusy=true;
    try{
      const responses=await Promise.allSettled([api('/api/safety/diagnosis/jobs'),api('/api/safety/risk/jobs')]);const stamp=jobs=>JSON.stringify(jobs.map(j=>[j.id,j.status,j.stage||j.phase,j.usage?.model_calls,arr(j.assessments).length,arr(j.stages).length,arr(j.queries).length]));
      if(state.caseMutation)return;
      let changed=false,terminalChanged=false;
      for(const [index,result]of responses.entries())if(result.status==='fulfilled'){
        const data=result.value,key=index?'riskJobs':'jobs',old=state[key],previous=new Map(old.map(j=>[j.id,j.status])),closed=index?RiskView.terminal:terminal;
        changed=changed||stamp(data.jobs)!==stamp(old);state[key]=arr(data.jobs);if(index)state.riskConfiguration=data.configuration;else state.configuration=data.configuration;
        terminalChanged=terminalChanged||state[key].some(j=>closed.has(j.status)&&previous.get(j.id)!==j.status);
      }
      if(changed){await loadSemantics(state.graphs.map(g=>g.case_id));renderContext();renderDetail();renderJobDialog();if(terminalChanged){await refresh();if(state.tab==='cloud')await loadCloudEvaluation(state.activeCase);}}
      if(responses.some(result=>result.status==='rejected'))$('#service-status').textContent='部分审查状态暂不可用';
    }catch(e){$('#service-status').textContent='审查状态暂不可用';}
    finally{state.pollBusy=false;schedulePoll(state.jobs.some(j=>!terminal.has(j.status))||state.riskJobs.some(j=>!RiskView.terminal.has(j.status))?2500:15000);}
  }
  function exportGraphs(){
    if(!state.graphs.length)return;const payload={schema:'prodebugger-studio-export/v1',exported_at:new Date().toISOString(),sample:state.lastSample,graphs:state.graphs,semantic_reports:state.graphs.map(g=>({case_id:g.case_id,...state.semanticRecords.get(g.case_id)})),risk_reviews:state.graphs.map(g=>({case_id:g.case_id,...state.riskRecords.get(g.case_id)}))};
    const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)+'\n'],{type:'application/json;charset=utf-8'})),a=el('a');a.href=url;a.download='prodebugger-workspace.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出当前轨迹、原事实与分析结果。');
  }
  function openHelp(){
    const steps=el('ol','help-steps');for(const text of ['导入 SigNoz JSON / JSONL，或从左侧选择验收记录；构造对照会明确标注未实际执行。','先看完整事件和当前安全边界，再打开“语义解析”，查看模型提取的约束、陈述及原文引用。','橙色表示有风险；灰色虚线表示缺证或待核实。工具成功不等于合规，绿色只代表适用检查通过。','点“完整风险审查”，依次执行 PARSE 和 MAP → ROUTE → TRACE → SUBMIT；无告警的轨迹也能审查。','点击某项发现的“定位这一次调用”，精确跳转到节点；模型结束后再展开独立标准答案与差异。','完整会话、状态表、覆盖核对、实体关系、原始事实及任务诊断仍保留，可按需要切换。'])steps.append(el('li','',text));
    const legacy=el('a','','打开保留的旧界面');legacy.href='/explorer.html';legacy.target='_blank';legacy.rel='noopener';
    const d=dialog('一分钟上手',[steps,el('p','','读入轨迹会自动检查。只有点击完整风险审查或任务诊断才会调用模型；查看已有结果不会调用。'),el('div','help-legend',null,Object.entries(visual).map(([type,label])=>pill(label,type))),el('p','muted','风险审查保留多个发现；任务诊断沿用原版定位失败原因，两者不能混成唯一根因。'),details('版本与能力范围',[el('p','','新旧代码和数据分开保留。完整消息接入不等于自然语言含义全部解析；原文公开表达不等于内部思考。实时接入尚未实现；受控采集运行工具演示。'),legacy])],[button('载入风险验收案例','primary',()=>{d.close();loadRiskValidation();}),button('载入完整会话','',()=>{d.close();loadCases(state.pool.filter(c=>c.session_available&&c.id.startsWith('session-run02-')).map(c=>c.id));})]);
  }
  $('#pool-search').addEventListener('input',e=>{state.search=e.target.value;renderPool();});$('#pool-scope').addEventListener('change',e=>{state.scope=e.target.value;renderPool();});$('#pool-filter').addEventListener('change',e=>{state.filter=e.target.value;renderPool();});
  $('#select-visible').addEventListener('change',e=>{if(e.target.checked){for(const c of filtered()){if(state.selected.size>=MAX)break;state.selected.add(c.id);}}else for(const c of filtered())state.selected.delete(c.id);renderPool();});
  $('#compare-selected').addEventListener('click',()=>loadCases([...state.selected]));$('#diagnose-selected').addEventListener('click',()=>startDiagnosis([...state.selected]));$('#diagnose-active').addEventListener('click',()=>startDiagnosis([state.activeCase]));
  $('#risk-selected').addEventListener('click',()=>startRiskReview([...state.selected]));$('#risk-active').addEventListener('click',()=>startRiskReview([state.activeCase]));$('#risk-examples-open').addEventListener('click',()=>loadRiskValidation());$('#risk-holdout-open').addEventListener('click',()=>loadRiskValidation('holdout'));
  $('#delete-selected').addEventListener('click',()=>openDeleteCases([...state.selected]));$('#deleted-open').addEventListener('click',openDeletedCases);
  $('#import-open').addEventListener('click',openImport);$('#sample-open').addEventListener('click',openSample);$('#capture-open').addEventListener('click',openCapture);$('#help-open').addEventListener('click',openHelp);$('#jobs-open').addEventListener('click',openJobs);$('#export-open').addEventListener('click',exportGraphs);
  $('#refresh-all').addEventListener('click',async()=>{const b=$('#refresh-all');b.disabled=true;try{await refresh();toast('已刷新轨迹与分析。');}catch(e){message(e.message,true);}finally{b.disabled=false;}});
  $('#clear-workspace').addEventListener('click',()=>{++state.sequence;state.graphs=[];state.selected.clear();state.activeCase=state.nodeId=state.issue=null;state.loading=false;renderAll();toast('已清空工作区，保存的轨迹没有删除。');});
  $$('[data-view]').forEach(b=>b.addEventListener('click',()=>{state.mode=b.dataset.view;if(['session','states','coverage'].includes(state.mode)){state.tab='session';const g=graph(state.activeCase);if(!state.sessionSelection&&g?.session_artifact?.events?.length)state.sessionSelection={type:'event',eventId:g.session_artifact.events[0].event_id};if(state.mode==='states'&&state.sessionSelection?.type==='event'){const eventId=state.sessionSelection.eventId,check=arr(g?.session_artifact?.checks).find(c=>c.state==='violated'&&arr(c.affected_event_ids).includes(eventId)),fid=arr(check?.primary_fids??check?.affected_fids)[0];if(fid!=null&&SessionView.versionBy(g,fid))state.sessionSelection={type:'fact',fid,eventId};}}renderLanes();renderDetail();revealSelection();}));
  const privacyTab=button('隐私来源','',()=>setTab('privacy'));privacyTab.dataset.detailTab='privacy';privacyTab.setAttribute('role','tab');privacyTab.setAttribute('aria-selected','false');privacyTab.setAttribute('aria-controls','detail-content');$('#detail-tabs').insertBefore(privacyTab,$('[data-detail-tab="risk"]'));
  $$('[data-detail-tab]').filter(b=>b!==privacyTab).forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.detailTab)));
  $('#page-size').addEventListener('change',e=>{state.pageSize=Number(e.target.value);state.page=0;renderLanes();});
  $('#prev-page').addEventListener('click',()=>{state.page--;renderLanes();$('#lanes-scroll').scrollTop=0;});$('#next-page').addEventListener('click',()=>{state.page++;renderLanes();$('#lanes-scroll').scrollTop=0;});
  for(const [id,delta]of [['zoom-in',.2],['zoom-out',-.2],['zoom-reset',0]])$('#'+id)?.addEventListener('click',()=>{state.scale=delta?Math.max(.8,Math.min(1.8,state.scale+delta)):1;renderLanes();});
  $$('[data-panel-target]').forEach(b=>b.addEventListener('click',()=>showPanel(b.dataset.panelTarget)));
  document.addEventListener('click',e=>{const b=e.target.closest('[data-toolbar-action]');if(b){const menu=b.closest('details');if(menu)menu.open=false;$('#'+b.dataset.toolbarAction)?.click();}});
  $('#toggle-detail')?.addEventListener('click',()=>{document.body.classList.toggle('detail-collapsed');if(innerWidth<=1180)showPanel(document.body.dataset.panel==='detail'?'workspace':'detail');});
  window.addEventListener('beforeunload',()=>{clearTimeout(timer);clearTimeout(state.captureTimer);});
  const mobileWorkspace=window.matchMedia('(max-width:700px)');if(mobileWorkspace.matches)$('#issues-panel').open=false;mobileWorkspace.addEventListener('change',event=>{$('#issues-panel').open=!event.matches;});
  const languageSelect=$('#language-select');
  if(languageSelect&&window.I18n){languageSelect.value=I18n.language;languageSelect.addEventListener('change',event=>I18n.setLanguage(event.target.value));window.addEventListener('languagechange',()=>{languageSelect.value=I18n.language;renderAll();renderCloudIntro();I18n.refresh();});}
  window.Studio={state,startCloudDemo,startPrivacyDemo,openRiskPolicy,loadCases,openCase,selectNode,selectSession,refresh,setTab,loadValidation,loadRiskValidation,startRiskReview,openJobs,openDeleteCases,openDeletedCases,api,renderAll,showPanel,locateRiskFinding};
  (async()=>{
    try{
      await refresh({reload:false});const params=new URLSearchParams(location.search);
      if(params.get('privacy')==='1'){await startPrivacyDemo();schedulePoll(15000);return;}
      if(params.get('cloud')==='1'){await startCloudDemo();schedulePoll(15000);return;}
      if(params.get('showcase')==='1'&&window.Showcase){await Showcase.start();schedulePoll(15000);return;}
      if(params.get('risk')==='1'&&state.riskCases.length){await loadRiskValidation(params.get('suite')==='holdout'?'holdout':'development');schedulePoll(15000);return;}
      const requested=params.getAll('case').filter(id=>poolCase(id));
      // This service defaults to its own privacy workspace. Other copied services
      // keep their existing entry points when no local privacy manifest is present.
      let privacy=null;try{const candidate=await api('/api/safety/privacy-showcase');if(arr(candidate.cases).some(c=>poolCase(c.case_id)))privacy=candidate;}catch(_){}
      if(privacy&&!params.has('case')&&!params.has('cloud')&&!params.has('showcase')&&!params.has('risk')){await startPrivacyDemo(privacy);schedulePoll(15000);return;}
      if(privacy){state.showcaseMode='privacy';state.cloudManifest=privacy;state.cloudFocus=params.get('library')!=='all'&&requested.every(id=>state.workspaceIds.has(id));renderCloudIntro();}
      const preferred=state.pool.find(c=>c.id==='session-run02-case-03')||state.pool.find(c=>c.session_available),ids=requested.length?requested:preferred?[preferred.id]:arr(state.suites[0]?.cases).map(c=>c.case_id);
      await loadCases(ids.length?ids:state.pool.slice(0,3).map(c=>c.id),{context:requested.length?'已载入指定轨迹':preferred?'已载入完整会话示例':'已载入工具验证样例'});
      if(requested.some(id=>riskCase(id)))setTab('risk');schedulePoll(15000);
    }catch(e){message('工作台加载失败：'+e.message,true);$('#service-status').textContent='本地服务连接失败';}
  })();
})();
