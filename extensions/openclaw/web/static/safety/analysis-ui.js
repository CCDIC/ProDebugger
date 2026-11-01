'use strict';
// Presents saved compiler, rule, audit and debugger outputs. No rule evaluation
// or causal inference is performed in the browser.
(() => {
  const $ = s => document.querySelector(s), all = s => [...document.querySelectorAll(s)];
  const arr = v => Array.isArray(v) ? v : [];
  const raw = v => typeof v === 'string' ? v : JSON.stringify(v, null, 2) ?? '未提供';
  const el = (tag, cls, text, children = []) => {const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=String(text);children.forEach(c=>c&&n.append(c));return n;};
  const button = (text, cls, action) => {const n=el('button',cls,text);n.type='button';n.addEventListener('click',action);return n;};
  const categoryNames={rule:'规则问题',safety:'安全风险',runtime:'运行异常',model:'模型诊断',uncertainty:'证据不足'};
  const visualNames={error:'！有证据的规则 / 模型问题',risk:'▲ 安全风险',passed:'✓ 适用检查通过',unknown:'？证据不足',neutral:'— 结构 / 未评估'};
  const stateNames={queued:'排队',running:'运行中',succeeded:'流程完成',failed:'执行失败',cancelled:'已取消',timed_out:'已超时',interrupted:'已中断',not_run:'尚未诊断',diagnosed:'已选出根因候选',uncertain:'不确定 / 未能定位根因',no_failure:'未发现任务失败',violated:'违反',violation_attempt:'违规尝试',satisfied:'通过',not_applicable:'不适用',unsupported:'不支持',unknown:'未知',confirmed:'有证据支持',suspected:'仅怀疑',candidate:'候选',safe:'本策略通过',unsafe:'违规尝试',error:'错误状态',observed:'客观记录',passed:'通过'};
  const terminal=new Set(['succeeded','failed','cancelled','timed_out','interrupted']);
  const name=v=>stateNames[v]||v||'未提供';
  const details=(title,children=[],open=false)=>{const d=el('details','analysis-details');d.open=open;d.append(el('summary','',title),el('div','analysis-details-body',null,children));return d;};
  const fields=items=>{const dl=el('dl','analysis-fields');items.filter(([,v])=>v!==undefined&&v!==null&&v!=='').forEach(([k,v])=>dl.append(el('dt','',k),el('dd','',raw(v))));return dl;};
  const code=v=>el('pre','analysis-code',raw(v));
  const chip=(text,type='neutral')=>el('span','analysis-chip '+type,text);
  const category=i=>i.category==='insufficient'?'uncertainty':i.category;
  const matches=(issue,c)=>c==='all'||category(issue)===c||(c==='uncertainty'&&['unknown','suspected'].includes(issue.state));
  const callValue=(g,n)=>{const f=arr(g?.record?.facts).find(f=>f.rel==='tool_call'&&arr(n.fids).includes(f.fid));try{return typeof f?.value==='string'?JSON.parse(f.value):f?.value||{};}catch{return {};}};
  let bridge=null, filter='all', jobs=[], timer=null, polling=false, currentIssue=null;
  const issueList=g=>arr(g?.analysis?.issues);
  const graphFor=id=>bridge?.app.graphs.find(g=>g.case_id===id);
  const getNode=(g,issue)=>arr(g?.nodes).find(n=>n.kind==='call'&&arr(issue.node_ids).includes(n.id))||arr(g?.nodes).find(n=>n.kind==='call'&&issue.call_id&&(n.entity_id===issue.call_id||n.index===issue.call_id||n.entity_id==='call:'+issue.call_id))||arr(g?.nodes).find(n=>arr(issue.node_ids).includes(n.id));
  const jobFor=id=>jobs.filter(j=>j.case_id===id).sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)))[0];

  function renderIssue(issue,g,{compact=false,locate=true}={}) {
    const section=el('article','analysis-issue '+category(issue)+' state-'+issue.state);section.dataset.issueId=issue.id;
    const meta=el('div','analysis-chips',null,[chip(categoryNames[category(issue)]||category(issue),category(issue)),chip(name(issue.state)),chip(issue.source||'未标注来源')]);
    section.append(meta,el('h4',issue.title?'i18n-raw':'',issue.title||'未命名发现'),el('p',issue.explanation||issue.message?'i18n-raw':'',issue.explanation||issue.message||'报告未提供解释。'));
    section.append(fields([['规则 / 策略',issue.rule_id||issue.policy_id],['确定性',issue.certainty],['调用',issue.call_id||issue.call_ids],['Span ID',issue.span_id],['Trace ID',issue.trace_id],['导入步骤',arr(issue.import_steps).join(', ')],['原始记录位置',issue.original_locations]]));
    if(locate&&getNode(g,issue))section.append(button('定位此调用 ↗','analysis-link',()=>locateIssue(g,issue)));
    if(!compact){
      const ids=arr(issue.fact_ids),factPanel=el('div','analysis-fact-links');
      ids.forEach(fid=>{const f=arr(g?.record?.facts).find(f=>String(f.fid)===String(fid));factPanel.append(details(`F${fid}${f?' · '+f.rel+' · 导入步骤 '+f.t:''}`,f?[code(f.value),el('p','analysis-muted','引用原事实与原文，未添加因果边。'),code(f.span),details('完整事实字段',[code(f)])]:[el('p','','该事实不在当前返回的 record 中。')]));});
      if(ids.length)section.append(details('关联事实 · '+ids.length,[factPanel]));
      section.append(details('支持证据 · '+arr(issue.evidence).length,[code(issue.evidence||[])]),details('反证 · '+arr(issue.counterevidence).length,[code(issue.counterevidence||[])]));
      const missing=arr(issue.missing);section.append(details('缺少的信息 / 尚未验证',missing.length?missing.map(v=>el('p','',raw(v))):[el('p','','报告未列出额外缺失项；这不代表已验证所有前提或已证明唯一根因。')]));
    }
    return section;
  }

  function renderDiagnosis(data={}) {
    const job=data.job||data, result=job.result||data.result, root=el('section','diagnosis-result');
    root.append(el('div','analysis-chips',null,[chip(name(job.status||'not_run')),chip(job.phase||'尚未开始'),...(result?[chip('模型结论：'+name(result.status),'model')]:[])]));
    if(job.error)root.append(el('p','analysis-error','诊断未完成：'+raw(job.error)));
    const partialQueries=arr(job.queries).filter(q=>q.partial||q.error||arr(q.function_errors).length||arr(q.skipped_branches).length);
    if(partialQueries.length)root.append(el('p','evidence-notice',`有 ${partialQueries.length} 次查询存在错误或部分分支不可用；缺失的分支不能作为“未发生”的证据。原始错误见下方“真实证据查询”。完整流程结束不代表所有检索都通过。`));
    if(result){
      root.append(fields([['结论',result.status==='error'?'诊断执行错误':name(result.status)],['停止原因',result.stop_reason]]));
      const source=fields([['模型原因说明',result.reasoning],['不确定性',result.uncertainty],['语义判定',result.semantic_verdict],['模型修复建议',result.repair]]);source.querySelectorAll('dd').forEach(node=>node.dataset.i18nSkip='');root.append(source);
      if(result.root)root.append(details('最终选中候选（模型判断，非已证明根因）',[code(result.root)],true));
      root.append(details('支持事实 / 反证引用',[fields([['支持事实',result.supporting_evidence||[]],['反证',result.counterevidence||[]]])]));
      if(arr(result.candidate_comparisons).length)root.append(details('其他候选比较',[code(result.candidate_comparisons)]));
    }else root.append(el('p','','尚未产生 SUBMIT 结果；规则检查和安全审计仍然有效。'));
    const phases=arr(job.phases||job.phase_events||data.events),flow=el('div','diagnosis-phases');
    ['MAP','ROUTE','TRACE','SUBMIT'].forEach(p=>{const complete=arr(job.completed_phases).includes(p),entered=phases.some(e=>String(e.to||e.phase||e.stage||'').toUpperCase()===p);flow.append(el('span','diagnosis-phase'+(job.phase===p&&!complete?' current':'')+(complete?' reached':''),p+(complete?' ✓':entered?' · 已进入':job.phase===p?' …':'')));});root.append(flow);
    root.append(details('记录的阶段事件 · '+phases.length,[code(phases)]),details('全部调查结果 · '+arr(job.assessments||job.investigations).length,[code(job.assessments||job.investigations||[])]),details('真实证据查询 · '+arr(job.queries).length,[code(job.queries||[])]));
    root.append(fields([['已记录完整流程',job.full_flow_completed===true?'MAP / ROUTE / TRACE / SUBMIT 均完成':job.full_flow_completed===false?'流程尚未完整完成':undefined],['已完成阶段',job.completed_phases]]));
    if(job.usage)root.append(details('模型用量',[code(job.usage)]));
    if(job.configuration||job.config_public)root.append(details('本次预算与配置（无密钥）',[code(job.configuration||job.config_public)]));
    if(job.source_mapping||job.source_sha256)root.append(details('输入快照与来源映射',[fields([['输入 SHA-256',job.source_sha256]]),code(job.source_mapping||{})]));
    if(result)root.append(details('完整诊断结果原文',[code(result)]));
    return root;
  }

  function init(b) {
    bridge=b;
    const panel=el('section','analysis-workflow');panel.id='analysis-workflow';
    panel.append(el('div','analysis-workflow-copy',null,[el('span','eyebrow','ANALYZE · INVESTIGATE · VERIFY'),el('h2','','先看发现，再查原因。'),el('p','','自动：实体 / 事实 → 原版规则 + 安全审计。按需：完整模型诊断 → 带证据的候选。')]),el('div','analysis-workflow-actions',null,[button('完整诊断 · 选中轨迹','button primary',()=>startDiagnosis([...bridge.app.selected])),button('刷新分析结果','button',refreshGraphs)]));
    panel.querySelector('.primary').id='diagnose-selected';
    const counts=el('div','analysis-counts');counts.id='analysis-counts';panel.append(counts);
    const queue=el('details','diagnosis-queue');queue.id='diagnosis-queue';const config=details('运行模型与预算（复用已有配置）');config.id='diagnosis-configuration';queue.append(el('summary','','模型诊断队列'),config,el('div','diagnosis-queue-list'));panel.append(queue);
    $('.explorer-layout').before(panel);
    const dialog=el('dialog','analysis-dialog');dialog.id='diagnosis-dialog';dialog.append(el('div','dialog-head',null,[el('h2','','完整诊断记录'),button('×','close-button',()=>dialog.close())]),el('div','diagnosis-dialog-content'));document.body.append(dialog);
    all('[data-inspector-tab="analysis"]').forEach(e=>e.addEventListener('click',()=>{currentIssue=null;}));
    selectionChanged();pollJobs();
    window.addEventListener('beforeunload',()=>clearTimeout(timer));
  }

  function selectionChanged(){if(!bridge)return;const b=$('#diagnose-selected');if(b){b.textContent=`完整诊断 · 选中 ${bridge.app.selected.size} 条`;b.disabled=!bridge.app.selected.size;}}

  function graphRendered(){
    if(!bridge)return;
    all('.graph-node').forEach(dom=>{const g=graphFor(dom.dataset.caseId),n=arr(g?.nodes).find(n=>n.id===dom.dataset.nodeId);if(!n)return;const state=n.visual_state||'neutral';
      ['unsafe','safe','unknown','neutral','error','risk','passed'].forEach(c=>dom.classList.remove(c));dom.classList.add(state);dom.dataset.visualState=state;
      dom.setAttribute('aria-label',`${g.title}；${n.entity_id}；${visualNames[state]||state}；${arr(n.badges).map(b=>b.label).join('；')}`);
      const title=dom.querySelector('title');if(title)title.textContent=`${n.entity_id}\n${visualNames[state]}\n${n.reason||''}`;
      if(n.kind==='call'){const v=callValue(g,n),target=v.arguments?.path||v.arguments?.url||v.arguments?.target;if(v.tool){const label='call · '+String(v.tool),limit=arr(n.badges).some(b=>b.category==='runtime')&&arr(n.badges).some(b=>b.category==='safety')?10:14;dom.querySelector('.node-kind').textContent=label.length>limit?label.slice(0,limit-1)+'…':label;}if(typeof target==='string'){const label=target.replaceAll('\\','/').split('/').filter(Boolean).pop()||target;dom.querySelector('.node-index').textContent=label.length>19?label.slice(0,18)+'…':label;if(title)title.textContent+='\n调用目标：'+target;}}
      dom.querySelectorAll('.analysis-svg-badge').forEach(e=>e.remove());const rect=dom.querySelector('rect');if(!rect)return;
      const x=Number(rect.getAttribute('x'))+Number(rect.getAttribute('width'))-10,y=Number(rect.getAttribute('y'))+20;
      const texts=[state==='unknown'?'?':state==='error'?'!':state==='passed'?'✓':state==='risk'?'▲':'',...arr(n.badges).some(b=>b.category==='safety')&&state!=='risk'?['▲']:[],...arr(n.badges).some(b=>b.category==='runtime')?['↯']:[]].filter(Boolean);
      texts.forEach((text,i)=>{const t=document.createElementNS('http://www.w3.org/2000/svg','text');t.setAttribute('x',String(x-i*17));t.setAttribute('y',String(y));t.setAttribute('text-anchor','end');t.setAttribute('class','analysis-svg-badge'+(text==='▲'?' safety':''));t.textContent=text;dom.append(t);});
    });
    all('.trace-lane-card').forEach(card=>{const g=graphFor(card.dataset.caseId);if(!g)return;card.querySelector('.lane-analysis-actions')?.remove();
      const actions=el('div','lane-analysis-actions',null,[button('完整诊断','analysis-link',()=>startDiagnosis([g.case_id])),button('诊断记录','analysis-link',()=>openDiagnosis(g.case_id))]);
      const job=jobFor(g.case_id)||g.analysis?.diagnosis;actions.append(chip(job?`${name(job.status)} · ${job.phase||'—'}`:'尚未完整诊断'));
      if(g.analysis)actions.append(el('span','analysis-muted',`${issueList(g).length} 项发现 · 点击下方问题查看证据`));
      card.querySelector('.lane-footer').before(actions);
    });
    renderIssues();
  }

  function renderIssues(){
    if(!bridge)return;const graphs=bridge.app.graphs, unique=new Map();graphs.forEach(g=>issueList(g).forEach(i=>unique.set(g.case_id+'::'+i.id,{g,i})));const findings=[...unique.values()];
    const categories=['all',...Object.keys(categoryNames)],filters=$('#issue-filters');if(filters){filters.replaceChildren();categories.forEach(c=>{const count=findings.filter(v=>matches(v.i,c)).length,b=button(`${c==='all'?'全部':categoryNames[c]} ${count}`,'issue-filter'+(filter===c?' active':''),()=>{filter=c;renderIssues();});b.dataset.issueFilter=c;b.setAttribute('aria-pressed',String(c===filter));filters.append(b);});}
    const box=$('#warning-list'),scrollTop=box.scrollTop;box.replaceChildren();const visible=findings.filter(v=>matches(v.i,filter));
    visible.forEach(({g,i})=>{const b=button('','warning-item analysis-warning '+category(i)+' state-'+i.state,()=>locateIssue(g,i));b.dataset.issueId=i.id;b.dataset.warningId=i.id;b.dataset.caseId=g.case_id;
      b.append(el('i'),el('div','',null,[el('div','analysis-chips',null,[chip(categoryNames[category(i)]||category(i),category(i)),chip(name(i.state))]),el('strong','',i.title||i.id),el('small','',`${g.title} · ${i.explanation||''}`),el('small','',`${i.rule_id||i.source||''} · ${i.call_id?'call '+i.call_id:arr(i.call_ids).length?'关联 '+i.call_ids.length+' 次调用':'无可核实调用定位'}${arr(i.import_steps).length?' · 导入步骤 '+i.import_steps.join(', '):''}`)]),el('span','','↗'));box.append(b);});
    if(!visible.length)box.append(el('p','empty-list',graphs.some(g=>!g.analysis)?'部分轨迹分析尚未返回；不能据此判断无问题。':'当前筛选没有发现项。未评估或不适用的检查不会计为通过。'));
    box.scrollTop=scrollTop;$('#warning-count').textContent=`${visible.length} 项发现 / ${new Set(visible.map(v=>v.g.case_id)).size} 条受影响轨迹`;
    const counts=$('#analysis-counts');if(counts){counts.replaceChildren();counts.append(el('div','',null,[el('strong','',findings.length),el('span','','项独立发现')]),el('div','',null,[el('strong','',new Set(findings.map(v=>v.g.case_id)).size),el('span','','条受影响轨迹')]),el('div','',null,[el('strong','',graphs.length),el('span','','条已载入')]),el('p','','发现按每条轨迹的唯一问题 ID 统计；关联多个实体也只计一次。导入步骤是转换后的序号，不等于原始 Agent 步骤。'));}
  }

  function locateIssue(g,i){
    currentIssue={caseId:g.case_id,id:i.id};bridge.app.tab='analysis';const node=getNode(g,i);
    if(node){bridge.selectNode(g.case_id,node.id,true);$('#node-inspector').scrollIntoView({block:'nearest',behavior:'smooth'});}
    else{const d=$('#diagnosis-dialog');d.querySelector('.dialog-head h2').textContent='未关联到具体调用的发现';d.querySelector('.diagnosis-dialog-content').replaceChildren(renderIssue(i,g,{locate:false}));d.showModal();}
  }

  function inspectorSummary(g,n){
    const state=n.visual_state||'neutral',status=$('#inspector-status');status.className='badge analysis-status '+state;status.textContent=visualNames[state]||state;
    const reason=$('#node-summary .node-reason');if(reason)reason.textContent='颜色依据：'+(n.reason||'本节点没有已执行且适用的行为检查。');
    const badges=el('div','analysis-chips');arr(n.badges).forEach(b=>badges.append(chip(b.label||categoryNames[b.category],b.category)));$('#node-summary').append(badges);
    $('#node-summary').append(el('p','analysis-muted','同一个工具的不同 call 分别判断；共享 tool 节点仅表示结构。'),button('本条完整诊断','button small',()=>startDiagnosis([g.case_id])));
  }

  function renderInspector(g,n,container){
    const analysis=g.analysis;if(!analysis){container.append(el('p','analysis-error','此轨迹没有返回统一分析结果，请刷新分析；不据此标为通过。'));return;}
    const linked=issueList(g).filter(i=>arr(n.issue_ids).includes(i.id)||arr(i.node_ids).includes(n.id)),selected=linked.find(i=>currentIssue?.caseId===g.case_id&&i.id===currentIssue.id);
    container.append(el('p','analysis-muted',`${linked.length} 项发现关联当前实体。导入步骤编号用于定位转换后的 action / observation；原始位置由来源映射单独给出。`));
    [...(selected?[selected]:[]),...linked.filter(i=>i!==selected)].forEach(i=>container.append(renderIssue(i,g,{locate:false})));
    if(!linked.length)container.append(el('p','','此实体无已关联发现；请看下面检查的适用性与结果。'));
    const checks=arr(analysis.checks).filter(c=>arr(c.node_ids).includes(n.id)||arr(c.call_ids).includes(n.index)||c.call_id===n.index||arr(c.import_steps).some(t=>arr(n.action_steps).includes(t)));
    container.append(details('当前调用的检查记录 · '+checks.length,[code(checks)]));
    const evals=arr(analysis.rule_evaluations).filter(e=>arr(n.fids).some(fid=>arr(e.refs).includes(fid)));
    container.append(details('原版规则评估 · '+evals.length,evals.length?evals.map(e=>details(`${e.rule} · ${name(e.state)} · 导入步骤 ${e.t}`,[el('p','',e.details?.reason||''),code(e)])):[el('p','','没有直接绑定此节点事实的原版规则评估。')]));
    container.append(details('规则适用性（整条轨迹）',[code(analysis.rule_applicability||[])]),details('根因候选（保留不确定性）',[code(analysis.root_candidates||[])]));
    const job=jobFor(g.case_id)||analysis.diagnosis||{};container.append(details('完整模型诊断',[renderDiagnosis(job)],false),button('展开完整诊断记录 ↗','analysis-link',()=>openDiagnosis(g.case_id)));
  }

  async function refreshGraphs(){
    if(!bridge)return;const snapshot=[...bridge.app.graphs],results=await Promise.allSettled(snapshot.map(g=>bridge.api('/api/safety/graph/'+encodeURIComponent(g.case_id))));
    const errors=[];results.forEach((r,i)=>{if(r.status==='fulfilled'){const index=bridge.app.graphs.findIndex(g=>g.case_id===snapshot[i].case_id);if(index>=0)bridge.app.graphs[index]=r.value;}else errors.push(r.reason.message);});
    const top=$('#graph-canvas').scrollTop;bridge.renderGraph();$('#graph-canvas').scrollTop=top;bridge.renderStats();bridge.renderInspector();
    if(errors.length)bridge.message('部分分析刷新失败：'+errors.join('；'),true);else bridge.toast('已读取保存的规则、安全审计及模型诊断结果。');
  }

  async function startDiagnosis(ids){
    if(!ids.length){bridge.toast('请在轨迹池勾选至少一条轨迹。');return;}if(ids.length>20){bridge.message('完整诊断每批最多 20 条，请减少选择；图谱仍支持 24 条。',true);return;}
    try{const data=await bridge.post('/api/safety/diagnosis',{case_ids:ids});arr(data.jobs||[data.job].filter(Boolean)).forEach(j=>{jobs=jobs.filter(old=>old.id!==j.id);jobs.push(j);});renderJobs();$('#diagnosis-queue').open=true;bridge.message(`已提交 ${ids.length} 条完整诊断，按队列运行。原版规则与安全结果保留。`);clearTimeout(timer);pollJobs();}catch(e){bridge.message('诊断未启动：'+e.message,true);}
  }

  function renderJobs(){
    const panel=$('#diagnosis-queue');if(!panel)return;const relevant=[...jobs].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at))).slice(0,40),active=relevant.filter(j=>!terminal.has(j.status));panel.querySelector('summary').textContent=`模型诊断队列 · ${active.length} 条排队 / 运行 · ${relevant.length} 条最近记录`;
    const box=panel.querySelector('.diagnosis-queue-list');box.replaceChildren();relevant.forEach(job=>{const row=el('div','diagnosis-job');row.dataset.jobId=job.id;row.dataset.jobStatus=job.status;row.append(el('div','',null,[el('strong','',bridge.app.state.cases.find(c=>c.id===job.case_id)?.title||job.case_id),el('small','',`${name(job.status)} · ${job.phase||'QUEUED'}${job.queue_position?' · 队列位置 '+job.queue_position:''}${job.error?' · '+raw(job.error):''}`)]),button('记录','analysis-link',()=>openDiagnosis(job.case_id,job.id)));if(!terminal.has(job.status))row.append(button('取消','analysis-link cancel',()=>cancelJob(job.id)));box.append(row);});
    if(!relevant.length)box.append(el('p','analysis-muted','还没有模型诊断记录。点击“完整诊断”才会调用已配置的模型。'));
  }

  async function cancelJob(id){try{await bridge.post('/api/safety/diagnosis/jobs/'+encodeURIComponent(id)+'/cancel',{});bridge.toast('已请求取消，已保存的规则和安全结果仍可查看。');clearTimeout(timer);pollJobs();}catch(e){bridge.message('取消失败：'+e.message,true);}}

  async function pollJobs(){
    if(!bridge||polling)return;polling=true;
    try{const data=await bridge.api('/api/safety/diagnosis/jobs'),next=arr(data.jobs),previous=new Map(jobs.map(j=>[j.id,j.status]));const stamp=list=>JSON.stringify(list.map(j=>[j.id,j.status,j.phase,j.error]));const progressed=stamp(jobs)!==stamp(next);jobs=next;const cfg=$('#diagnosis-configuration .analysis-details-body');if(cfg&&!cfg.children.length&&data.configuration)cfg.append(fields([['模型',data.configuration.model],['已配置凭据',data.configuration.credential_available===true?'可用（不展示密钥）':'不可用；诊断会报失败，规则和安全结果保留'],['总时限（秒）',data.configuration.wall_timeout_seconds]]),code(data.configuration.budgets||{}));if(progressed||!$('#diagnosis-queue .diagnosis-queue-list')?.children.length)renderJobs();const changed=jobs.some(j=>terminal.has(j.status)&&previous.has(j.id)&&previous.get(j.id)!==j.status);if(changed)await refreshGraphs();else if(progressed)graphRendered();}
    catch(e){const list=$('#diagnosis-queue .diagnosis-queue-list');if(list&&!jobs.length)list.replaceChildren(el('p','analysis-error','诊断队列暂不可用：'+e.message));}
    finally{polling=false;clearTimeout(timer);timer=setTimeout(pollJobs,3000);}
  }

  async function openDiagnosis(caseId,id){
    const dialog=$('#diagnosis-dialog'),content=dialog.querySelector('.diagnosis-dialog-content');dialog.querySelector('.dialog-head h2').textContent='完整诊断 · '+(graphFor(caseId)?.title||caseId);content.replaceChildren(el('p','','正在读取已保存的诊断记录…'));if(!dialog.open)dialog.showModal();
    try{const cached=id?jobs.find(j=>j.id===id):jobFor(caseId);const job=cached?.id?await bridge.api('/api/safety/diagnosis/jobs/'+encodeURIComponent(cached.id)):graphFor(caseId)?.analysis?.diagnosis||{};content.replaceChildren(renderDiagnosis(job));}catch(e){content.replaceChildren(el('p','analysis-error',e.message));}
  }

  window.AnalysisUI={init,selectionChanged,graphRendered,renderIssues,inspectorSummary,renderInspector,refreshGraphs,startDiagnosis,renderDiagnosis,renderIssue,helpers:{el,button,arr,raw,details,fields,code,chip,name,categoryNames,terminal}};
})();
