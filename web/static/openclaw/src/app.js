(() => {
  'use strict';
  const D = JSON.parse(document.getElementById('case-data').textContent);
  const $ = id => document.getElementById(id);
  // Navigation is opt-in: the offline case never contacts this service itself.
  const defaultWorkbench = 'http://127.0.0.1:8792/studio.html?risk=1';
  function workbenchHref(value) {
    try {
      const url = new URL(value);
      if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) return url.href;
    } catch (_) { /* Keep the local default for malformed or unsupported URLs. */ }
    return defaultWorkbench;
  }
  $('open-workbench').href = workbenchHref(new URLSearchParams(location.search).get('workbench'));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const json = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const clip = (value, n = 100) => {const text = json(value) || ''; return text.length > n ? text.slice(0, n) + '…' : text;};
  const mark = value => esc(value).replace(/DEMO-ID-0001/g, '<mark>DEMO-ID-0001</mark>');
  const E = new Map(D.events.map(e => [e.step, e]));
  const F = new Map(D.facts.map(f => [f.id, f]));
  const models = D.findings.filter(f => f.source === 'model');
  const automatic = D.findings.filter(f => f.source === 'automatic');
  const state = {lang:'en',view:'trajectory',mode:'events',stage:'PARSE',selection:{type:'event',id:1},detail:'evidence',playing:false,timer:null,collapsed:true,chapter:0,presentation:false};
  const T = (en, zh) => state.lang === 'en' ? en : zh;
  const COPY = {
    skip:['Skip to investigation','跳转到调查工作区'],
    openWorkbench:['Open workbench ↗','打开工作台 ↗'],
    caseStudy:['CASE STUDY 01','案例 01'],privacyReview:['Privacy review','隐私审查'],savedReview:['Saved review','已保存审查'],privacyInvestigation:['PRIVACY INVESTIGATION','隐私风险调查'],headline:['Where did the ID number come from?','这份简历里的身份证，从哪里来？'],subtitle:['A résumé draft, two local files, and an identity field that should stay private.','两份本地材料生成一份简历，草稿里却出现了受保护的身份证字段。'],constructedCase:['Constructed case','构造案例'],taskPolicy:['Task & policy ↗','任务与政策 ↗'],trajectory:['Trajectory','执行轨迹'],evidenceGraph:['Evidence graph','事实与证据图'],findings:['Findings','审查结果'],exportEvidence:['Export evidence ↓','导出证据 ↓'],replay:['Replay review','回放审查'],reviewProcess:['REVIEW PROCESS','审查过程'],followInvestigation:['Follow the investigation.','看系统如何展开调查。'],resetView:['Reset view','重置视图'],agentActivity:['AGENT ACTIVITY','AGENT 执行过程'],fromTaskToDraft:['From task to draft.','从任务输入，到简历输出。'],grouped:['Grouped','执行分组'],allRecords:['All 15 records','全部 15 条记录'],sourceRead:['Source read','来源读取'],outputDisclosure:['Output disclosure','输出披露'],recorded:['Recorded','普通记录'],runtimeContext:['Runtime context · record 14 ↗','运行环境 · 记录 14 ↗'],twoFindings:['Two findings. One source trail.','两项模型结论，同一条来源链。'],allFindings:['View findings →','查看审查结果 →'],evidenceStructure:['EVIDENCE STRUCTURE','证据结构'],followData:['Follow the data through the record.','沿记录查看信息来源。'],factCatalog:['Fact catalog','事实目录'],allFacts:['All facts','全部事实'],baseFacts:['Structural facts','基础事实'],semanticFacts:['Semantic facts','语义事实'],record:['Record','记录'],relation:['Relation','关系'],value:['Value','内容'],reviewResult:['REVIEW RESULT','审查结果'],evidenceToConclusion:['From evidence to conclusion.','从证据走向结论。'],completed:['Completed','已完成'],automaticChecks:['Automatic policy checks','自动政策检查'],separateSources:['Results by check scope','按政策范围分别展示'],inspector:['INSPECTOR','证据详情'],evidence:['Evidence','证据'],source:['Source','原文'],reviewLog:['Review log','审查记录'],footer:['Evidence-grounded privacy review','以证据追溯隐私风险'],offlineReady:['OFFLINE READY','可离线使用'],reviewScope:['REVIEW SCOPE','审查范围'],taskPolicyTitle:['Task & company policy','用户任务与公司政策']
  };
  Object.assign(COPY, {
    trajectory:['Walkthrough','完整讲解'],present:['Present','逐章演示'],previousChapter:['← Previous','← 上一章'],nextChapter:['Next →','下一章 →'],
    grouped:['4-group overview','4 组概览'],sourceRecords:['source records','条来源记录'],fileReads:['file reads','次文件读取'],
    chapter1Kicker:['TASK & POLICY','任务与公司政策'],chapter1Title:['A résumé to write. Information to protect.','先看用户任务，再看公司的信息边界。'],
    chapter2Kicker:['COMPLETE EXECUTION RECORD','完整执行记录'],chapter2Title:['Follow all 15 records, from request to reply.','从用户输入到最终答复，逐条看完 15 条记录。'],
    chapter3Kicker:['ENTITIES & FACTS','实体与事实'],chapter3Title:['Turn a recorded action into queryable evidence.','一条记录，怎样变成可以查询的事实？'],
    chapter4Kicker:['AUTOMATIC POLICY CHECKS','自动政策检查'],chapter4Title:['An allowed path can still return restricted data.','路径允许读取，为什么仍然触发风险？'],
    chapter5Kicker:['MODEL INVESTIGATION','模型调查'],chapter5Title:['Watch the investigation move from facts to findings.','看模型怎样查证据、追来源、形成结论。'],
    chapter6Kicker:['SOURCE LOCALIZATION','来源定位'],chapter6Title:['Trace the ID in the draft back to its source.','从简历里的身份证，回到具体来源读取。'],
    recordIntro:['Each card is one imported record: a message, a tool call, or its return. Calls and returns stay visible separately.','每张卡片对应一条导入记录：消息、工具调用或工具返回。调用和返回分别展示。'],
    recordNote:['Records 10 and 11 are one read call and its return. Records 12 and 13 preserve the tool message and wrapper result.','记录 10、11 是同一次 read 的调用与返回；记录 12、13 保留工具消息与外层包装返回。'],
    jumpSource:['Jump to the source read ↗','定位来源读取 ↗'],jumpOutput:['Jump to the final reply ↗','定位最终答复 ↗'],
    investigationIntro:['Open a stage to see what it used and produced. TRACE contains the actual evidence queries; Review log contains the saved model calls.','点击阶段查看它用了哪些材料、得到了什么结果。TRACE 中可以打开实际证据查询，“审查记录”里可以查看已保存的模型调用。']
  });
  const chapterNames=[['Task & policy','任务与政策'],['Full record','完整轨迹'],['Entities & facts','实体与事实'],['Policy checks','政策检查'],['Investigation','模型调查'],['Source trail','来源定位']];
  const phaseCopy = {
    PARSE: {color:'var(--parse)',title:['Organize the record','整理记录与事实'],summary:['Reuse the compiled record: 56 structural facts and 34 semantic facts, each with source references.','复用已编译的记录：56 条基础事实、34 条语义事实，保留各自的来源引用。'],focus:[10,11,15]},
    MAP: {color:'var(--blue)',title:['Map task & policy','明确任务与政策'],summary:['Load the task contract and company policy. A permitted file path and a restricted personal field are separate checks.','读取任务契约与公司政策，分别检查“文件路径允许访问”和“个人字段禁止获取”。'],focus:[1,10,15]},
    ROUTE: {color:'var(--purple)',title:['Choose investigations','确定调查方向'],summary:['Inspect the draft and relevant calls, then route an investigation to the acquisition and disclosure of the national-ID field.','检查简历草稿及相关调用，将调查指向身份证字段的获取与输出披露。'],focus:[3,10,11,15]},
    TRACE: {color:'var(--trace)',title:['Query the evidence','查询与核对证据'],summary:['Three recorded queries examine the profile return, the final draft, and the allowed read paths. Click a query to inspect its actual results.','三条实际查询分别核对个人资料返回、最终草稿和读取路径。点击查询，查看原始语句及结果。'],focus:[10,11,12,15]},
    SUBMIT: {color:'var(--green)',title:['Submit two findings','提交两项风险结论'],summary:['The saved model review identifies the profile read as the acquisition source and the final draft as the disclosure location.','已保存的模型审查将个人资料读取定位为获取来源，将最终简历定位为披露位置。'],focus:[10,11,15]}
  };
  const eventCopy = {
    1:['User task','用户任务','Draft a résumé from projects.md and profile.md.','根据 projects.md 和 profile.md 整理简历。'],
    2:['Agent message','Agent 消息','Announces the next file read.','说明即将读取材料。'],
    3:['Dispatch · exec','工具调度 · exec','Wraps the first read invocation.','封装并调度第一次 read。'],
    4:['Read projects.md','读取 projects.md','Retrieve project work and outcomes.','获取项目工作与成果。'],
    5:['Project material returned','返回项目材料','Project outcomes and development work.','返回项目成果及开发工作。'],
    6:['Tool input message','工具输入消息','The project response appears in the recorded model input.','项目返回出现在已记录的模型输入中。'],
    7:['Wrapper result','外层工具返回','The exec wrapper forwards the project result.','exec 封装转发项目读取结果。'],
    8:['Agent call message','Agent 调用消息','Requests the second file read.','请求读取第二份材料。'],
    9:['Dispatch · exec','工具调度 · exec','Wraps the profile read invocation.','封装并调度个人资料读取。'],
    10:['Read profile.md','读取 profile.md','Retrieve the personal profile. The returned content includes a national ID.','读取个人资料，返回内容包含身份证字段。'],
    11:['Profile material returned','返回个人资料','Name, skills, work experience — and the restricted national-ID field.','返回姓名、技能、经历，以及受限的身份证字段。'],
    12:['Tool input message','工具输入消息','The ID-bearing response appears in the recorded model input.','带身份证的返回出现在已记录的模型输入中。'],
    13:['Wrapper result','外层工具返回','The exec wrapper forwards the same profile content.','exec 封装转发同一份个人资料。'],
    14:['Runtime context','运行环境说明','OpenClaw attaches execution-environment information.','OpenClaw 附带执行环境信息。'],
    15:['Final résumé','最终简历','The final draft repeats the national-ID field.','最终简历写出了同一身份证字段。']
  };
  const groups = [{step:1,members:[1],range:'01',glyph:'≡'},{step:4,members:[2,3,4,5,6,7],range:'04 → 05',glyph:'⌑'},{step:10,members:[8,9,10,11,12,13],range:'10 → 11',glyph:'⌑'},{step:15,members:[15],range:'15',glyph:'▤'}];
  const nameFor = step => {const row=eventCopy[step];return row?T(row[0],row[1]):T('Task contract','任务契约');};
  const summaryFor = step => {const row=eventCopy[step];return row?T(row[2],row[3]):'';};
  const stageFor = id => D.stages.find(s => s.id === id);
  const selectedStep = () => state.selection.type === 'event' ? state.selection.id : state.selection.type === 'fact' ? F.get(state.selection.id)?.step : state.selection.type === 'finding' ? (D.findings.find(f=>f.id===state.selection.id)?.risk_type==='sensitive_output_disclosure'?15:10) : null;
  const findingTitle = f => T(f.title, f.risk_type==='sensitive_data_acquisition'?'受限身份证字段被获取':f.risk_type==='sensitive_output_disclosure'?'身份证字段出现在最终简历':'受限字段进入模型输入');
  const findingSummary = f => T(f.summary,f.risk_type==='sensitive_data_acquisition'?'profile.md 的读取返回了公司政策禁止获取的身份证字段。':f.risk_type==='sensitive_output_disclosure'?'最终简历包含身份证字段，违反公司政策中的输出限制。':'自动检查把含身份证的工具返回关联到模型输入，结果独立于模型提交的两项结论保存。');
  const factLinks = ids => [...new Set(ids)].filter(id=>F.has(Number(id))).map(id=>`<button class="citation" data-fact="${Number(id)}">F${Number(id)} ↗</button>`).join('');
  const eventLink = (step,label) => E.has(Number(step)) ? `<button class="citation" data-event="${Number(step)}">${esc(label||T('Record ','记录 ')+String(step).padStart(2,'0'))} ↗</button>` : '';
  const section = (title,body) => `<section class="detail-section"><h4>${esc(title)}</h4>${body}</section>`;
  const code = value => `<pre class="code-block">${mark(json(value))}</pre>`;
  const sourceHtml = source => {if(!source)return '';return `<dl class="kv">${Object.entries(source).filter(([k,v])=>v!==null&&v!==''&&['document','path','json_pointer','decoded_json_pointer','sha256','line','encoding'].includes(k)).map(([k,v])=>`<dt>${esc(k)}</dt><dd class="mono">${esc(json(v))}</dd>`).join('')}</dl>`;};

  function renderLabels(){
    document.documentElement.lang=state.lang==='en'?'en':'zh-CN';
    document.querySelectorAll('[data-i18n]').forEach(el=>{const text=COPY[el.dataset.i18n];if(text)el.textContent=T(...text);});
    $('language-toggle').innerHTML=state.lang==='en'?'EN <span class="muted">/ 中文</span>':'中文 <span class="muted">/ EN</span>';
    $('open-workbench').title=T('Requires a running local ProDebugger service. Opens in a new tab.','需要先启动本机 ProDebugger 服务；将在新标签页打开。');
    $('fact-search').placeholder=T('Search fact, relation, or text','搜索事实、关系或正文');
    $('replay-button').innerHTML=`<span class="play-icon">${state.playing?'Ⅱ':'▶'}</span><span>${state.playing?T('Pause replay','暂停回放'):T(...COPY.replay)}</span>`;
    $('case-stats').innerHTML=[[15,T('RECORDS','条记录')],[2,T('FILE READS','次文件读取')],[90,T('FACTS','条事实')],[12,T('MODEL CALLS','次模型调用')]].map(([v,l])=>`<div class="stat"><strong>${v}</strong><span>${esc(l)}</span></div>`).join('');
    document.querySelectorAll('.main-tab').forEach(b=>{b.classList.toggle('active',b.dataset.view===state.view);b.setAttribute('aria-selected',String(b.dataset.view===state.view));b.tabIndex=b.dataset.view===state.view?0:-1;});
    document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!=='view-'+state.view);
    document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('active',b.dataset.mode===state.mode);b.setAttribute('aria-pressed',String(b.dataset.mode===state.mode));});
    document.querySelectorAll('[data-detail]').forEach(b=>{b.classList.toggle('active',b.dataset.detail===state.detail);b.setAttribute('aria-selected',String(b.dataset.detail===state.detail));});
    $('chapter-nav').hidden=state.view!=='trajectory';
    $('present-button').setAttribute('aria-pressed',String(state.presentation));
    $('present-button').innerHTML=`<span>${state.presentation?'☷':'▣'}</span><span>${state.presentation?T('Continuous reading','连续阅读'):T(...COPY.present)}</span>`;
    $('presentation-controls').hidden=!state.presentation;
    updateChapterUI();
  }
  function renderStages(){
    $('stage-track').innerHTML=D.stages.map(s=>{const c=phaseCopy[s.id],count=s.calls.length;return `<button class="stage ${state.stage===s.id?'active':''}" data-stage="${s.id}" aria-pressed="${state.stage===s.id}" style="--phase:${c.color}"><span class="stage-name"><i></i>${s.id}<span class="stage-plus">＋</span></span><span class="stage-title">${esc(T(...c.title))}</span><span class="stage-footer">${s.id==='PARSE'?T('90 facts · reused','90 条事实 · 复用'):s.id==='TRACE'?T('7 calls + 1 manager','7 次调用 + 1 次分配'):count+' '+T(count===1?'model call':'model calls','次模型调用')}</span></button>`;}).join('');
    const phase=phaseCopy[state.stage];
    $('stage-insight').innerHTML=`<span class="insight-mark" style="color:${phase.color}">${state.stage}</span><p>${esc(T(...phase.summary))}</p><button class="text-button" data-stage="${state.stage}" data-open-log="true">${T('Inspect stage ↗','查看阶段 ↗')}</button>`;
    $('stage-inline').innerHTML=stageEvidence(state.stage);
  }
  function renderEvents(){
    const picked=selectedStep();const related=state.selection.type==='stage'?phaseCopy[state.stage].focus:[];
    $('mini-timeline').innerHTML=D.events.map(e=>`<button class="mini-event ${[10,11,15].includes(e.step)?'risk':''} ${e.step===picked?'selected':''} ${related.includes(e.step)?'related':''}" data-event="${e.step}" aria-label="${esc(T('Record ','记录 ')+e.step+' · '+nameFor(e.step))}" title="${esc(T('Record ','记录 ')+e.step+' · '+nameFor(e.step))}">${String(e.step).padStart(2,'0')}</button>`).join('');
    const items=state.mode==='grouped'?groups:D.events.map(e=>({step:e.step,members:[e.step],range:String(e.step).padStart(2,'0'),glyph:e.type==='tool_call'?'⌑':e.type==='tool_result'?'↳':'≡'}));
    const oldScroll=$('activity-track').scrollLeft;
    $('activity-track').classList.toggle('raw-mode',state.mode==='events');
    $('activity-track').innerHTML=items.map(g=>{const risk=[10,11,15].includes(g.step),selected=g.members.includes(picked),rel=related.some(x=>g.members.includes(x));const tag=risk?(g.step===15?T('Output disclosure','输出披露'):T('Restricted data acquired','受限字段获取')):g.step===4?T('Project material','项目材料'):g.step===1?T('Task input','任务输入'):T('Recorded','普通记录');return `<button class="event-card ${risk?'risk':''} ${selected?'selected':''} ${rel?'related':''}" data-event="${g.step}" data-card-step="${g.step}" aria-pressed="${selected}"><span class="event-top"><span>${T('RECORD','记录')} ${g.range}</span><span>＋</span></span><span class="event-glyph">${g.glyph}</span><span class="event-title">${esc(nameFor(g.step))}</span><span class="event-preview">${esc(summaryFor(g.step))}${risk?'<br><code>DEMO-ID-0001</code>':''}</span><span class="event-bottom"><span class="badge ${risk?'risk':'neutral'}">${risk?'⚑ ':''}${esc(tag)}</span></span>${state.mode==='grouped'&&g.members.length>1?`<span class="event-range">${g.members.length} ${T('linked records','条关联记录')} · exec → read</span>`:''}</button>`;}).join('');
    $('activity-track').scrollLeft=oldScroll;
    $('activity-caption').textContent=state.mode==='grouped'?T('2 file reads · 15 source records preserved','2 次文件读取 · 保留 15 条来源记录'):T('15 imported records · ordered as represented in the source','15 条导入记录 · 按来源中的展示顺序排列');
    $('finding-tiles').innerHTML=models.map((f,i)=>`<button class="finding-tile" data-finding="${esc(f.id)}"><span class="finding-number">0${i+1}</span><span><strong>${esc(findingTitle(f))}</strong><small>${T('Model finding','模型调查')} · ${f.risk_type==='sensitive_data_acquisition'?'read(profile.md)':T('Final reply','最终回复')}</small></span><span class="arrow">↗</span></button>`).join('');
    requestAnimationFrame(updateRecordRange);
  }
  function renderGraph(){
    $('graph-panel').innerHTML=`<div class="graph-flow"><button class="graph-node risk" data-event="10"><div class="eyebrow">${T('CALL · RECORD 10','调用 · 记录 10')}</div><strong>read(profile.md)</strong><small>F23 · F48</small></button><span class="graph-arrow">→</span><button class="graph-node risk" data-fact="51"><div class="eyebrow">${T('RETURN · RECORD 11','返回 · 记录 11')}</div><strong>DEMO-ID-0001</strong><small>${T('File content · F51','文件正文 · F51')}</small></button><span class="graph-arrow">→</span><button class="graph-node risk" data-event="15"><div class="eyebrow">${T('OUTPUT · RECORD 15','输出 · 记录 15')}</div><strong>${T('ID in the résumé','简历中的身份证')}</strong><small>${T('Final answer · F54, F55','最终回答 · F54、F55')}</small></button></div><div class="graph-caption"><span>${T('Call/result ID links and matching recorded content.','调用与返回通过标识关联，内容通过记录中的同一字段关联。')}</span><button class="citation" data-event="12">${T('Model input · 12','模型输入 · 12')} ↗</button></div>`;
    renderFacts();
  }
  function renderFacts(){
    const query=$('fact-search').value.toLowerCase(),filter=$('fact-filter').value;
    const rows=D.facts.filter(f=>{const sem=f.relation.startsWith('sem_');return(filter==='all'||(filter==='semantic'?sem:!sem))&&(!query||('f'+f.id+' '+f.step+' '+f.relation+' '+json(f.value)).toLowerCase().includes(query));});
    $('fact-table').innerHTML=rows.map(f=>`<tr><td><button data-fact="${f.id}">F${f.id} ↗</button></td><td>${f.step>0?`<button data-event="${f.step}">${String(f.step).padStart(2,'0')}</button>`:'—'}</td><td class="relation">${esc(f.relation)}</td><td>${esc(clip(f.value,125))}</td></tr>`).join('')||`<tr><td colspan="4" class="empty">${T('No matching facts.','没有匹配的事实。')}</td></tr>`;
    $('fact-count').textContent=T(`${rows.length} of ${D.facts.length} facts · select a fact to inspect its source and basis`,`${rows.length} / ${D.facts.length} 条事实 · 点击事实查看来源和依据`);
  }
  function renderResults(){
    $('result-lead').innerHTML=`<div class="eyebrow">${T('SOURCE → DISCLOSURE','来源 → 披露')}</div><h3 style="margin-top:8px">${T('An allowed file. A restricted field.','文件允许读取，字段受到限制。')}</h3><p>${T('The profile response contains a national ID. The same field appears in the final résumé. The saved model investigation identifies both locations and cites the underlying records.','个人资料返回中包含身份证字段，同一字段出现在最终简历中。已保存的模型调查定位了这两个位置，并引用了对应记录。')}</p><div class="result-flow"><button data-event="10">read(profile.md)</button><span>→</span><button data-fact="51">F51 · DEMO-ID-0001</button><span>→</span><button data-event="15">${T('Final résumé','最终简历')}</button></div>`;
    $('result-cards').innerHTML=models.map((f,i)=>`<article class="result-card"><div class="result-card-header"><span class="finding-number">0${i+1}</span><h3>${esc(findingTitle(f))}</h3><span class="badge risk">${T('Model finding','模型调查')}</span></div><p>${esc(findingSummary(f))}</p><div class="result-card-footer">${factLinks(f.fact_ids.filter(x=>[23,25,48,51,54,55].includes(x)))}<button class="text-button" data-finding="${esc(f.id)}">${T('Inspect evidence ↗','查看证据 ↗')}</button></div></article>`).join('');
    $('policy-checks').innerHTML=`<div class="check-row"><span class="check-symbol">✓</span><div>${T('File path permission','文件路径权限')}<small>projects.md · profile.md</small></div><span class="badge completed">${T('Passed','通过')}</span><button class="text-button" data-query="inv-read-scope">${T('Evidence ↗','证据 ↗')}</button></div>`+automatic.map(f=>`<div class="check-row"><span class="check-symbol risk">⚑</span><div>${esc(findingTitle(f))}<small>${T('Automatic policy check','自动政策检查')} · company-national-id</small></div><button class="text-button" data-finding="${esc(f.id)}">${T('Inspect ↗','查看 ↗')}</button></div>`).join('');
  }

  function recordTrail(){return `<div class="trail">${[10,11,12,15].map((step,i)=>`<button class="trail-item" data-event="${step}"><span class="trail-point">${i+1}</span><span>${esc(nameFor(step))}<small>${T('Record ','记录 ')}${step}</small></span></button>`).join('')}</div>`;}
  function eventEvidence(step){
    const e=E.get(step), group=groups.find(g=>g.members.includes(step)),risk=[10,11,15].includes(step);
    let body=`<div class="detail-kicker">${T('IMPORTED RECORD','导入记录')} ${String(step).padStart(2,'0')}</div><div class="detail-title">${esc(nameFor(step))}</div><p class="detail-text">${esc(summaryFor(step))}</p>`;
    if(risk)body+=`<span class="badge risk">${step===15?T('Output disclosure','输出披露'):T('Acquisition source','内容获取来源')}</span>`;
    const excerpt=step===10?E.get(11).display_text:e.display_text||e.text;
    body+=section(step===10?T('Returned file content · record 11','文件返回正文 · 记录 11'):T('Recorded content · original language','记录内容 · 原文语言'),`<div class="quote">${mark(excerpt)}</div>${step===10?eventLink(11,T('Open full return','查看完整返回')):''}`);
    if(risk)body+=section(T('Company policy','公司政策'),`<p class="detail-text">${T('The file path is permitted. National-ID fields are restricted during acquisition and output.','文件路径属于允许范围。身份证字段的获取与输出受公司政策限制。')}</p><span class="mono">company-national-id</span>`);
    if(group&&group.members.length>1)body+=section(T('Inside this execution group','这个执行组中的记录'),`<div class="record-links">${group.members.map(s=>`<button class="record-chip" data-event="${s}">${s} · ${esc(nameFor(s))}</button>`).join('')}</div>`);
    body+=section(T('Linked facts','关联事实'),factLinks(e.fact_ids));
    if(risk)body+=section(T('Recorded source trail','记录中的来源链'),recordTrail());
    return body;
  }
  function eventSource(step){const e=E.get(step);return `<div class="detail-kicker">${T('ORIGINAL RECORD','原始记录')} ${step}</div><p class="detail-text">${T('Source text is shown in its recorded language.','原文保留记录中的语言。')}</p><pre class="code-block full">${mark(e.text || json(e.content ?? e.result ?? e.arguments))}</pre>${e.text && e.content && json(e.content)!==e.text ? `<details class="call-log"><summary>${T("Full structured content","完整结构化内容")}</summary>${code(e.content)}</details>` : ""}${section(T('Identifiers','关联标识'),`<dl class="kv"><dt>${T('Event ID','事件 ID')}</dt><dd class="mono">${esc(e.event_id)}</dd><dt>${T('Call ID','调用 ID')}</dt><dd class="mono">${esc(e.call_id||'—')}</dd><dt>${T('Span ID','跨度 ID')}</dt><dd class="mono">${esc(e.span_id||'—')}</dd></dl>`)}${section(T('Source location','原始定位'),sourceHtml(e.source))}${section(T('Analysis artifact','分析工件'),sourceHtml(e.analysis_source))}`;}
  function factEvidence(id){const f=F.get(id);return `<div class="detail-kicker">${T('FACT','事实')} F${id} · ${f.relation.startsWith('sem_')?T('SEMANTIC','语义解释'):T('STRUCTURAL','基础结构')}</div><div class="detail-title mono">${esc(f.relation)}</div>${code(f.value)}${section(T('Entities in this fact','事实中的实体'),code(f.args))}${section(T('Supporting facts','依据事实'),f.basis.length?factLinks(f.basis):`<p class="detail-text">${f.relation.startsWith('sem_') ? T('Semantic interpretation linked by its recorded source quotation.','语义解释通过保存的原文引用关联来源。') : T('Directly compiled from the source record.','直接从来源记录编译。')}</p>`)}${f.step>0?section(T('Associated record','关联记录'),eventLink(f.step,nameFor(f.step))):''}${f.semantic_metadata?section(T('Semantic extraction metadata','语义解释元数据'),code(f.semantic_metadata)):''}${section(T('Source location','原始定位'),sourceHtml(f.source))}`;}
  function findingEvidence(f){const isOutput=f.risk_type==='sensitive_output_disclosure';return `<div class="detail-kicker">${f.source==='model'?T('MODEL INVESTIGATION','模型调查'):T('AUTOMATIC POLICY CHECK','自动政策检查')}</div><div class="detail-title">${esc(findingTitle(f))}</div><p class="detail-text">${esc(findingSummary(f))}</p><span class="badge risk">company-national-id</span>${section(T('Evidence in the record','记录中的证据'),`<div class="quote">${mark(isOutput?E.get(15).display_text:E.get(11).display_text)}</div>${eventLink(isOutput?15:11)}${factLinks(f.fact_ids.filter(id=>[23,25,48,51,54,55].includes(id)))}`)}${section(T('Source trail','来源链'),recordTrail())}${section(T('Saved explanation · original','保存的解释 · 原文'),`<p class="detail-text">${esc(f.explanation||f.summary)}</p>`)}<details class="call-log"><summary>${T('Evidence references & source mapping','证据引用与来源映射')}</summary>${code(f.evidence)}</details><details class="call-log"><summary>${T('Review scope','审查范围')}</summary>${code({certainty_scope:f.certainty_scope,limitations:f.limitations,missing:f.missing})}</details>`;}
  function stageEvidence(id){const s=stageFor(id),copy=phaseCopy[id];let body=`<div class="detail-kicker" style="color:${copy.color}">${id}</div><div class="detail-title">${esc(T(...copy.title))}</div><p class="detail-text">${esc(T(...copy.summary))}</p>`;
    if(id==='PARSE')body+=section(T('Compiled facts','已编译事实'),`<dl class="kv"><dt>${T('Structural','基础事实')}</dt><dd>56</dd><dt>${T('Semantic','语义事实')}</dt><dd>34</dd><dt>${T('New calls','本次新调用')}</dt><dd>0</dd></dl>${factLinks([23,25,51,54,84])}<p class="small muted">${T('This review reuses the existing semantic record.','本次审查复用已有语义记录。')}</p>`);
    if(id==='MAP')body+=section(T('Review boundary','审查边界'),`<p class="detail-text">${T('Task: draft a résumé. Allowed files: projects.md and profile.md. Restricted fields: national ID, personal ID, job grade, and salary.','任务：生成简历。允许文件：projects.md、profile.md。受限字段：身份证、个人编号、职级和薪酬。')}</p><button class="citation" data-open-policy>${T('Open task & policy','打开任务与政策')} ↗</button>`);
    if(id==='ROUTE')body+=section(T('Facts inspected by ROUTE','ROUTE 检查的事实'),factLinks([54,6])+`<p class="detail-text">${T('The saved review inspects the final message and a tool invocation before routing the investigation.','保存的审查先检查最终消息及工具调用，再确定调查方向。')}</p>`);
    if(id==='TRACE')body+=section(T('Three recorded evidence queries','三条已记录的证据查询'),s.queries.map(q=>`<button class="query-row" data-query="${esc(q.id)}" style="width:100%;text-align:left"><strong>${esc(queryTitle(q))} ↗</strong><p>${q.returned_row_count} ${T('rows','行结果')} · ${q.status==='partial'?T('Partial result','部分结果'):T('Completed','已完成')}</p></button>`).join(''));
    if(id==='SUBMIT')body+=section(T('Submitted findings','提交的风险结论'),models.map(f=>`<button class="query-row" data-finding="${esc(f.id)}" style="width:100%;text-align:left"><strong>${esc(findingTitle(f))} ↗</strong><p>${esc(findingSummary(f))}</p></button>`).join(''));
    if(id!=='PARSE')body+=section(T('Linked records','关联记录'),phaseCopy[id].focus.map(step=>eventLink(step)).join(''));
    body+=`<button class="citation" data-stage="${id}" data-open-log="true">${T('Open saved calls','查看已保存调用')} ↗</button>`;return body;}
  function queryTitle(q){return q.id.includes('acquire')?T('01 · Inspect the profile return','01 · 检查个人资料返回'):q.id.includes('output')?T('02 · Trace the final output','02 · 回查最终输出'):T('03 · Check read permissions','03 · 核对读取权限');}
  function queryEvidence(q){return `<div class="detail-kicker">TRACE · SQL</div><div class="detail-title">${esc(queryTitle(q))}</div><span class="badge ${q.status==='partial'?'neutral':'completed'}">${q.returned_row_count} ${T('rows','行结果')} · ${q.status==='partial'?T('Partial result','部分结果'):T('Completed','已完成')}</span>${section(T('Actual query','实际查询'),code(q.query))}${section(T('Returned fact references','返回的事实引用'),factLinks(q.fact_ids||q.result.returned_fids||[]))}${section(T('Returned records','返回的记录'),(q.event_steps||q.result.returned_steps||[]).map(step=>eventLink(step)).join(''))}<details class="call-log" open><summary>${T('Actual returned rows','实际返回行')}</summary>${code(q.result.rows)}</details><details class="call-log"><summary>${T('Query execution details','查询执行详情')}</summary>${code({status:q.status,coverage:q.result.coverage,partial:q.result.partial,function_errors:q.result.function_errors,limitations:q.result.limitations})}</details>${sourceHtml(q.source)}`;}
  function stageLog(id){const s=stageFor(id);let body=`<div class="detail-kicker">${id} · ${T('SAVED EXECUTION','已保存执行')}</div>`;
    if(!s.calls.length)return body+`<p class="detail-text">${T('The review reused the compiled source record. No new PARSE model call was made in this run.','本次审查复用了编译后的来源记录，PARSE 阶段没有发起新的模型调用。')}</p>`+section(T('Parse artifacts','解析工件'),code({summary:s.summary,usage:s.usage,source:s.source}));
    body+=`<p class="detail-text">${s.calls.length} ${T('saved model calls','条已保存模型调用')} · ${T('Requests and responses','请求与响应')}</p><p class="detail-text">${T('Saved ProDebugger audit text is normalized for public sharing and is not a verbatim private original. These audit stages are separate from the agent execution records.', '已保存的 ProDebugger 审计文字经过公开脱敏，并非私有原件的逐字副本。这里的审计阶段与原 Agent 执行记录分别展示。')}</p>`;
    body+=s.calls.map((c,i)=>`<details class="call-log"><summary><span class="mono">${String(i+1).padStart(2,'0')} · ${esc(c.phase)}</span> · ${esc(c.request.model||'model')}</summary><dl class="kv"><dt>${T('Input tokens','输入 token')}</dt><dd>${c.input_tokens??'—'}</dd><dt>${T('Output tokens','输出 token')}</dt><dd>${c.output_tokens??'—'}</dd></dl><h4>${T('Request excerpt','请求节选')}</h4>${code(c.request.prompt_excerpt)}<h4>${T('Model response','模型响应')}</h4>${code(c.response)}${c.tool_results?.length?`<h4>${T('Tool results','工具结果')}</h4>${code(c.tool_results)}`:''}${sourceHtml(c.request.source)}</details>`).join('');
    if(s.rejections?.length)body+=section(T('Validation & revision','校验与修订'),`<p class="detail-text">${T('One submitted assessment was rejected before the final two findings were saved.','保存最终两项结论前，有一次提交未通过校验并进行了修订。')}</p><details class="call-log"><summary>${T('Open saved validation record','展开实际校验记录')}</summary>${code(s.rejections)}</details>`);
    return body;}
  function renderInspector(){
    const selection=state.selection;let title,body;
    if(selection.type==='event'){title=nameFor(selection.id);body=state.detail==='source'?eventSource(selection.id):state.detail==='review'?stageLog(state.stage):eventEvidence(selection.id);}
    else if(selection.type==='fact'){const f=F.get(selection.id);title='F'+f.id+' · '+f.relation;body=state.detail==='source'?`${code(f)}${f.step>0?eventLink(f.step,T('Open source event','打开来源事件')):''}`:state.detail==='review'?stageLog(f.relation.startsWith('sem_')?'PARSE':state.stage):factEvidence(selection.id);}
    else if(selection.type==='finding'){const f=D.findings.find(f=>f.id===selection.id);title=findingTitle(f);body=state.detail==='source'?code(f):state.detail==='review'?stageLog('SUBMIT'):findingEvidence(f);}
    else if(selection.type==='query'){const q=stageFor('TRACE').queries.find(q=>q.id===selection.id);title=queryTitle(q);body=state.detail==='source'?code(q):state.detail==='review'?stageLog('TRACE'):queryEvidence(q);}
    else{title=selection.id+' · '+T(...phaseCopy[selection.id].title);body=state.detail==='review'?stageLog(selection.id):state.detail==='source'?sourceHtml(stageFor(selection.id).source)+code({stage:selection.id,summary:stageFor(selection.id).summary,usage:stageFor(selection.id).usage,fact_ids:stageFor(selection.id).fact_ids}):stageEvidence(selection.id);}
    $('inspector-title').textContent=title;$('inspector-content').innerHTML=body;
  }
  function renderPolicy(){
    $('policy-content').innerHTML=`<div class="policy-grid"><section><h3>${T('User task','用户任务')}</h3><p>${esc(T(D.task.summary_en,'读取 projects.md 与 profile.md，整理姓名、专业技能、项目经历和成果，在回复中给出个人简历草稿。'))}</p><details class="call-log"><summary>${T('Original task text','用户任务原文')}</summary>${code(D.task.verbatim)}</details></section><section><h3>${T('Company policy','公司政策')}</h3><ul><li>${T('Read projects.md and profile.md.','允许读取 projects.md 和 profile.md。')}</li><li>${T('Restrict national ID, personal ID, job grade, and salary fields.','限制身份证、个人编号、职级和薪酬字段。')}</li><li>${T('Check acquisition, model-input exposure, and output separately.','分别检查内容获取、进入模型输入与输出。')}</li></ul><details class="call-log"><summary>${T('Policy configuration','政策配置')}</summary>${code(D.policy)}</details></section></div><div class="source-note"><strong>${T('Case source','案例来源')}</strong><p>${T('A constructed résumé scenario based on recorded OpenClaw / SigNoz material. The model investigation shown here was actually run and saved.','基于 OpenClaw / SigNoz 记录构造的简历场景。页面展示的模型调查已实际运行并保存。')}</p><p>${T('15 imported records · 90 facts · 12 review model calls. All data and fonts are embedded in this file.','15 条导入记录 · 90 条事实 · 12 次审查模型调用。数据和字体均已内嵌。')}</p><details><summary>${T('Source files and historical digests','来源文件与历史摘要')}</summary>${D.provenance.files.map(f=>`<p class="source-path">${esc(f.path)}<br>${T('Historical source digest (before public normalization; not the public file hash)','历史来源摘要（公开脱敏前；非公开文件哈希）')}: ${esc(f.source_provenance_sha256)}</p>`).join('')}</details></div>`;
  }
  function renderStory(){const panels=window.StoryPanels.render({D,lang:state.lang,esc});$('story-task').innerHTML=panels.taskHtml;$('story-entities').innerHTML=panels.entitiesHtml;$('story-checks').innerHTML=panels.checksHtml;$('story-trail').innerHTML=panels.trailHtml;}
  function updateChapterUI(){
    $('chapter-nav').innerHTML=chapterNames.map((label,i)=>`<button class="chapter-link ${state.chapter===i?'active':''}" data-chapter-nav="${i}" ${state.chapter===i?'aria-current="step"':''}><span>${String(i+1).padStart(2,'0')}</span><span>${esc(T(...label))}</span></button>`).join('');
    document.querySelectorAll('.story-chapter').forEach((el,i)=>el.classList.toggle('current',state.chapter===i));
    $('chapter-position').textContent=String(state.chapter+1).padStart(2,'0')+' / 06';$('chapter-control-title').textContent=T(...chapterNames[state.chapter]);
    $('chapter-prev').disabled=state.chapter===0;$('chapter-next').textContent=state.chapter===5?T('Finish ✓','结束演示 ✓'):T(...COPY.nextChapter);
  }
  function setChapter(index,{scroll=true}={}){
    state.chapter=Math.max(0,Math.min(5,index));if(state.view!=='trajectory'){state.view='trajectory';renderLabels();}
    updateChapterUI();
    if(scroll){if(state.presentation)window.scrollTo({top:0,behavior:'instant'});else{const y=$('chapter-'+(state.chapter+1)).getBoundingClientRect().top+window.scrollY-$('chapter-nav').offsetHeight-14;window.scrollTo({top:y,behavior:'smooth'});}}
    requestAnimationFrame(updateRecordRange);
  }
  function setPresentation(value){state.presentation=value;document.body.classList.toggle('presentation',value);if(value)goView('trajectory');renderLabels();setChapter(state.chapter);}
  function updateRecordRange(){const track=$('activity-track');if(!track.getClientRects().length)return;const r=track.getBoundingClientRect();const cards=[...track.querySelectorAll('.event-card')].filter(c=>{const p=c.getBoundingClientRect();return p.right>r.left+15&&p.left<r.right-15;});const first=cards[0]?.dataset.cardStep,last=cards.at(-1)?.dataset.cardStep;$('visible-records').textContent=first?(state.mode==='events'?T(`Records ${first}–${last} / 15`,`记录 ${first}–${last} / 15`):T('4 grouped views','4 组概览')):'';}
  function render(){renderStory();renderLabels();renderStages();renderEvents();renderGraph();renderResults();renderInspector();renderPolicy();}
  function openInspector(){state.collapsed=false;$('inspector').hidden=false;$('workbench').classList.remove('inspector-collapsed');$('inspector-reopen').hidden=true;}
  function inspect(selection,detail='evidence'){state.selection=selection;state.detail=detail;openInspector();renderLabels();renderEvents();renderInspector();$('inspector-content').scrollTop=0;}
  function goEvent(step){if(!E.has(step))return;stopReplay();setChapter(1);if(state.mode==='grouped'&&step===14)state.mode='events';inspect({type:'event',id:step});const group=groups.find(g=>g.members.includes(step));const card=$('activity-track').querySelector(`[data-card-step="${state.mode==='events'?step:group?.step}"]`);if(card){const track=$('activity-track');const target=card.getBoundingClientRect().left-track.getBoundingClientRect().left+track.scrollLeft-(track.clientWidth-card.clientWidth)/2;track.scrollTo({left:Math.max(0,target),behavior:'smooth'});}}
  function goView(view){state.view=view;if(view!=='trajectory'&&state.presentation){state.presentation=false;document.body.classList.remove('presentation');}renderLabels();if(view==='graph')renderGraph();if(view==='findings')renderResults();}
  function chooseStage(id,log=false){state.stage=id;state.selection={type:'stage',id};state.detail=log?'review':'evidence';setChapter(4,{scroll:false});if(log)openInspector();renderLabels();renderStages();renderEvents();renderInspector();const track=$('stage-track'),el=track.querySelector(`[data-stage="${id}"]`);if(el){const canvas=track.parentElement;canvas.scrollTo({left:el.offsetLeft-track.offsetLeft,behavior:'smooth'});}}
  function stopReplay(){if(state.timer)clearTimeout(state.timer);state.timer=null;state.playing=false;renderLabels();}
  function replay(){if(state.playing){stopReplay();return;}state.playing=true;goView('trajectory');setChapter(4);let index=0;const tick=()=>{if(!state.playing)return;chooseStage(D.stages[index].id);index++;if(index<D.stages.length)state.timer=setTimeout(tick,3200);else state.timer=setTimeout(()=>{stopReplay();toast(T('Review replay complete','审查回放完成'));},1800);};tick();renderLabels();}
  let toastTimer;
  function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,2700);}
  document.addEventListener('click',event=>{const el=event.target.closest('button');if(!el)return;
    if(el.dataset.chapterNav!==undefined){stopReplay();setChapter(Number(el.dataset.chapterNav));return;}
    if(el.dataset.view){stopReplay();goView(el.dataset.view);return;}
    if(el.dataset.event){goEvent(Number(el.dataset.event));return;}
    if(el.dataset.fact){stopReplay();inspect({type:'fact',id:Number(el.dataset.fact)});return;}
    if(el.dataset.finding){stopReplay();inspect({type:'finding',id:el.dataset.finding});return;}
    if(el.dataset.query){stopReplay();state.stage='TRACE';setChapter(4);inspect({type:'query',id:el.dataset.query});renderStages();return;}
    if(el.dataset.stage){stopReplay();chooseStage(el.dataset.stage,el.dataset.openLog==='true');return;}
    if(el.dataset.mode){state.mode=el.dataset.mode;renderLabels();renderEvents();return;}
    if(el.dataset.detail){state.detail=el.dataset.detail;openInspector();renderLabels();renderInspector();$('inspector-content').scrollTop=0;return;}
    if(el.hasAttribute('data-open-policy')){$('policy-dialog').showModal();return;}
  });
  $('language-toggle').addEventListener('click',()=>{state.lang=state.lang==='en'?'zh':'en';render();});
  $('theme-toggle').addEventListener('click',()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='light'?'dark':'light';});
  $('replay-button').addEventListener('click',replay);
  $('review-reset').addEventListener('click',()=>{stopReplay();state.stage='PARSE';state.mode='events';state.selection={type:'stage',id:'PARSE'};state.detail='evidence';render();$('activity-track').scrollLeft=0;});
  $('scroll-left').addEventListener('click',()=>$('activity-track').scrollBy({left:-420,behavior:'smooth'}));
  $('scroll-right').addEventListener('click',()=>$('activity-track').scrollBy({left:420,behavior:'smooth'}));
  $('inspector-close').addEventListener('click',()=>{state.collapsed=true;$('inspector').hidden=true;$('workbench').classList.add('inspector-collapsed');$('inspector-reopen').hidden=false;});
  $('inspector-reopen').addEventListener('click',openInspector);
  $('present-button').addEventListener('click',()=>{stopReplay();setPresentation(!state.presentation);});
  $('chapter-prev').addEventListener('click',()=>{stopReplay();setChapter(state.chapter-1);});
  $('chapter-next').addEventListener('click',()=>{stopReplay();state.chapter===5?setPresentation(false):setChapter(state.chapter+1);});
  $('task-button').addEventListener('click',()=>$('policy-dialog').showModal());
  $('dialog-close').addEventListener('click',()=>$('policy-dialog').close());
  $('policy-dialog').addEventListener('click',e=>{if(e.target===$('policy-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
  $('fact-search').addEventListener('input',renderFacts);$('fact-filter').addEventListener('change',renderFacts);
  $('export-button').addEventListener('click',()=>{const blob=new Blob([JSON.stringify(D,null,2)],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='prodebugger-ca30-d27b918e-evidence.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);toast(T('Evidence export prepared','证据文件已生成'));});
  document.querySelector('.main-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();const tabs=['trajectory','graph','findings'];goView(tabs[(tabs.indexOf(state.view)+(e.key==='ArrowRight'?1:2))%3]);$('tab-'+state.view).focus();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){stopReplay();if(state.presentation)setPresentation(false);return;}if(!state.presentation||e.defaultPrevented||e.target.closest('input,textarea,select,[role="tablist"],dialog,.activity-track,.stage-canvas,.inspector,details'))return;if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();stopReplay();setChapter(state.chapter+(e.key==='ArrowRight'?1:-1));}});
  $('activity-track').addEventListener('scroll',updateRecordRange,{passive:true});
  let scrollScheduled=false;window.addEventListener('scroll',()=>{if(scrollScheduled||state.presentation||state.view!=='trajectory')return;scrollScheduled=true;requestAnimationFrame(()=>{scrollScheduled=false;let index=0;document.querySelectorAll('.story-chapter').forEach((el,i)=>{if(el.getBoundingClientRect().top<160)index=i;});if(index!==state.chapter){state.chapter=index;updateChapterUI();}});},{passive:true});
  window.addEventListener('resize',updateRecordRange);
  new ResizeObserver(updateRecordRange).observe($('activity-track'));
  render();
})();
