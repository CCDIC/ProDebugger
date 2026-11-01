'use strict';
// Read-only projection of recorded events and core Fact versions. This module
// neither infers hidden cognition nor creates causal edges between events.
(() => {
  const {el, button, arr, raw, details, code, fields} = AnalysisUI.helpers;
  const kind = event => event.type === 'tool_call' ? '工具调用' : event.type === 'tool_result' ? '工具返回' : event.role === 'user' ? '用户要求' : event.stop_reason === 'stop' && event.role === 'assistant' ? 'Agent 最终答复' : event.role === 'assistant' ? 'Agent 公开消息' : event.role === 'tool' ? '工具消息' : event.role || '会话消息';
  const clip = (value, n=170) => {const text=typeof value==='string'?value:raw(value);return text.length>n?text.slice(0,n)+'…':text;};
  const artifact = graph => graph?.session_artifact;
  const available = graph => artifact(graph)?.available === true;
  const eventBy = (graph,id) => arr(artifact(graph)?.events).find(event=>event.event_id===id);
  const versionBy = (graph,fid) => arr(artifact(graph)?.rows).flatMap(row=>arr(row.versions).map(version=>({row,version}))).find(item=>String(item.version.fid)===String(fid));
  const chip=(text,cls='neutral')=>el('span','pill '+cls,text);
  const note=text=>el('p','session-note',text);
  const list=items=>el('ul','session-list',null,items.map(value=>el('li','',typeof value==='string'?value:raw(value))));
  const eventText=event=>event.type==='tool_call'?raw(event.arguments||{}):event.type==='tool_result'?raw(event.result??event.content??'返回正文未提供'):event.text||(Array.isArray(event.content)&&event.content.length?raw(event.content):'无文本正文');
  const statusLabel={observed:'直接记录',asserted:'Agent 公开表达',required:'任务要求',unknown:'证据不足'};
  const pointerNames={source:'出处',basis:'依据',correspondence:'对应',check:'检查'};
  const checkLabel={passed:'适用检查通过',violated:'规则违反',unknown:'证据不足',not_applicable:'不适用'};
  const semanticLabel={semantic:'已提取结构事实',preserved_only:'原始内容已保留',unsupported:'此内容类型尚未结构化'};
  const invocationNames={model_tool:'模型直接发起',nested_tool:'工具封装内部调用',runtime_tool_unclassified:'运行时调用 · 层级未确认'};
  const assessmentNames={passed:'检查通过',risk:'安全风险',missing_evidence:'证据不足',policy_field_unavailable:'策略字段未提供',unsupported:'检查未覆盖',unconfigured:'策略未配置',prerequisite_unavailable:'检查前提未满足',not_applicable:'检查不适用',not_evaluated:'尚未检查',conflict:'结论冲突',unvalidated:'证据待核验'};
  const checkSources={safety_audit:'安全策略',risk_rule:'风险规则',risk_model:'模型审查',model:'模型审查',reused_abstract_rules:'原版规则'};
  const coverageStatuses=new Set(['policy_field_unavailable','unsupported','unconfigured','prerequisite_unavailable','not_applicable','not_evaluated']);
  const uncertaintyStatuses=new Set(['missing_evidence','conflict','unvalidated']);
  const tr=(zh,en)=>window.I18n?.language==='zh'?zh:en;
  const scopeNames={path:['路径权限','Path permission'],acquire:['内容获取','Content acquisition'],context:['模型输入','Model-input exposure'],output:['报告输出','Output disclosure']};
  function checkScope(item){
    if(item.risk_type==='sensitive_data_acquisition'||item.rule_id==='RR-PRIVACY-ACQUIRE-001')return 'acquire';
    if(item.risk_type==='sensitive_context_exposure'||item.rule_id==='RR-PRIVACY-CONTEXT-001')return 'context';
    if(item.risk_type==='sensitive_output_disclosure'||item.rule_id==='RR-PRIVACY-OUTPUT-001')return 'output';
    if(item.risk_type==='read_scope'||item.policy_id==='read_scope'||['RR-READ-SCOPE-001','forbidden_path','outside_allowed_roots'].includes(item.rule_id))return 'path';
    return null;
  }
  function acquisitionPending(graph,event,items){
    const policy=graph.analysis?.risk_review?.policy;
    return ['tool_call','tool_result'].includes(event.type)&&policy?.tools?.[event.tool]?.kind==='read'&&arr(policy.privacy_rules).some(rule=>rule.allow_acquire===false)&&!items.some(item=>checkScope(item)==='acquire');
  }
  function assessmentFor(graph,event){
    return graph.analysis?.event_assessments?.[String(event.step)]||nodeFor(graph,event)?.check_summary||null;
  }
  function assessmentItems(summary){return arr(summary?.items);}
  function hasAssessmentStatus(summary,statuses){return assessmentItems(summary).some(item=>statuses.has(item.display_status));}
  function spanLabel(graph,event){
    let identity=event.provenance?.span_identity;
    if(!identity)for(const fact of arr(graph.record?.facts).filter(f=>f.rel==='tool_call')){try{const value=typeof fact.value==='string'?JSON.parse(fact.value):fact.value;if(value?.span_id===event.span_id||value?.provenance?.tool_call_id===event.call_id){identity=value.provenance?.span_identity;break;}}catch{}}
    return identity==='synthetic_local_analysis_id'||String(event.trace_id||'').startsWith('native-')?'本地分析标识（非云端 Span）':identity==='recorded_otel'?'云端 Span ID':'Span / 原始记录标识';
  }
  function nodeFor(graph,event){
    if(event.type==='session_message'){const key=(event.trace_id?event.trace_id+'/':'')+event.event_id;return arr(graph.nodes).find(node=>node.kind==='message'&&(node.index===key||node.entity_id==='message:'+key));}
    const canonical=event.trace_id&&event.span_id?event.trace_id+'/'+event.span_id:null;return arr(graph.nodes).find(node=>node.kind==='call'&&[canonical,event.call_id].filter(Boolean).some(id=>node.index===id||node.entity_id==='call:'+id));
  }
  const primaryFids=check=>arr(check.primary_fids??check.affected_fids);
  function eventIssues(graph,event){const node=nodeFor(graph,event);return arr(graph.analysis?.issues).filter(issue=>issue.source!=='capture_coverage'&&(arr(issue.affected_event_ids).includes(event.event_id)||(node&&arr(issue.node_ids).includes(node.id))));}
  function eventChecks(graph,event){return arr(artifact(graph)?.checks).filter(check=>arr(check.affected_event_ids).includes(event.event_id)||primaryFids(check).some(fid=>arr(event.fact_ids).some(id=>String(fid)===String(id))));}
  function visualFor(graph,event){
    const node=nodeFor(graph,event),issues=eventIssues(graph,event),checks=eventChecks(graph,event),summary=assessmentFor(graph,event);
    if(issues.some(issue=>['rule','model'].includes(issue.category)&&issue.state==='confirmed')||checks.some(check=>check.state==='violated'))return 'error';
    if(issues.some(issue=>issue.category==='safety'&&['confirmed','violation_attempt','violated'].includes(issue.state)))return 'risk';
    if(issues.some(issue=>issue.risk_status==='input_warning'))return 'warning';
    if(issues.some(issue=>issue.risk_status==='suspected'))return 'suspected';
    if(summary){
      if(hasAssessmentStatus(summary,new Set(['risk'])))return 'risk';
      if(checks.some(check=>check.state==='unknown')||hasAssessmentStatus(summary,uncertaintyStatuses))return 'unknown';
      if(summary.has_passed&&!summary.has_unresolved&&!summary.has_conflict&&!acquisitionPending(graph,event,assessmentItems(summary)))return 'passed';
      // Coverage limitations do not turn recorded content into missing evidence.
      return 'neutral';
    }
    if(checks.some(check=>check.state==='unknown')||issues.some(issue=>['unknown','suspected'].includes(issue.state)))return 'unknown';
    if(node&&['error','risk','passed'].includes(node.visual_state))return node.visual_state;
    if(checks.some(check=>check.state==='passed'))return 'passed';
    // A trace-level capture gap does not make every recorded event unknown.
    return 'neutral';
  }
  function selectButton(text,cls,selection,ctx){const node=button(text,cls,()=>ctx.onSelect?.(selection));node.dataset.sessionSelection=JSON.stringify(selection);return node;}
  function scope(graph){
    const data=artifact(graph),box=el('div','session-scope'+(available(graph)?'':' incomplete'));
    if(!available(graph)){box.append(details('记录范围',[note('仅包含工具调用与返回；会话消息未提供。')]));return box;}
    box.append(details('来源与覆盖范围',[list(arr(data.limitations))]));return box;
  }
  function semanticProgress(graph,event){
    const units=arr(graph.record?.openclaw_semantics?.units).filter(unit=>Number(unit.step)===Number(event.step));
    if(!units.length)return semanticLabel[event.semantic_status]||'原文已保留';
    const reviewed=units.filter(unit=>unit.status==='reviewed'),items=reviewed.reduce((n,unit)=>n+arr(unit.items).length,0);
    if(reviewed.length===units.length)return items?`语义 ${items} 项`:'语义已审';
    return reviewed.length?'语义部分完成':'待语义解析';
  }
  function assessmentBadges(graph,event,badges){
    const summary=assessmentFor(graph,event);if(!summary)return false;
    const items=assessmentItems(summary),issues=eventIssues(graph,event),passed=items.filter(item=>item.display_status==='passed'&&!checkScope(item));
    for(const [scope,names]of Object.entries(scopeNames)){
      const scoped=items.filter(item=>checkScope(item)===scope),findings=issues.filter(item=>checkScope(item)===scope);if(!scoped.length&&!findings.length)continue;
      const risk=scoped.some(item=>item.display_status==='risk')||findings.some(item=>['confirmed','violation_attempt','violated'].includes(item.state)),unresolved=scoped.some(item=>uncertaintyStatuses.has(item.display_status))||findings.some(item=>['unknown','suspected'].includes(item.state)),limited=scoped.some(item=>coverageStatuses.has(item.display_status)),pass=scoped.some(item=>item.display_status==='passed');
      const state=risk?tr('发现风险','Risk found'):unresolved?tr('证据不足','Insufficient evidence'):limited?tr('检查不完整','Incomplete checks'):pass?tr('通过','Passed'):tr('尚未检查','Not checked');
      const badge=chip(tr(...names)+' · '+state,risk?'risk':unresolved?'unknown':limited?'coverage':pass?'passed':'coverage');badge.dataset.checkScope=scope;badges.append(badge);
    }
    if(acquisitionPending(graph,event,items)){const badge=chip(tr('内容获取 · 尚未检查','Content acquisition · Not checked'),'coverage');badge.dataset.checkScope='acquire';badges.append(badge);}
    if(items.some(item=>item.display_status==='risk')&&!badges.querySelector('.risk,.error'))badges.append(chip('风险发现','risk'));
    if(passed.length)badges.append(chip(`✓ 检查通过 ${passed.length}`,'passed'));
    const statuses=[...new Set(items.map(item=>item.display_status))],coverage=Object.keys(assessmentNames).filter(status=>coverageStatuses.has(status)&&statuses.includes(status));
    for(const status of statuses){
      if(['passed','risk'].includes(status))continue;
      if(coverageStatuses.has(status))continue;
      badges.append(chip((uncertaintyStatuses.has(status)?'? ':'')+(assessmentNames[status]||status),uncertaintyStatuses.has(status)?'unknown':'coverage'));
    }
    if(coverage.length){const status=coverage[0],badge=chip(assessmentNames[status],'coverage');badge.title=coverage.map(value=>assessmentNames[value]).join(' · ');badges.append(badge);}
    if(!items.length)badges.append(chip(event.type==='session_message'?'已记录':'尚未检查'));
    return true;
  }
  function timeline(graph,ctx){
    const container=el('div','session-timeline');
    for(const event of arr(artifact(graph)?.events)){
      const selected=ctx.selection?.type==='event'&&ctx.selection.eventId===event.event_id;
      const card=selectButton('',`session-event ${visualFor(graph,event)}${selected?' selected':''}`,{type:'event',eventId:event.event_id},ctx);
      card.dataset.eventId=event.event_id;card.dataset.caseId=graph.case_id;const associated=nodeFor(graph,event);if(associated)card.dataset.nodeId=associated.id;
      const sourceLabel=ctx.constructed?'构造记录':event.original_seq!=null?'原始序号 '+event.original_seq:ctx.cloudVerified?'云端记录':event.source?.document?'导入记录':'原始记录';
      card.append(el('span','session-event-heading',null,[el('span','session-step','导入 '+event.step),el('strong','',kind(event))]),el('span','session-event-source',sourceLabel+(event.tool?' · '+event.tool:'')),el('span','session-event-preview',clip(eventText(event))),el('span','session-event-foot',`${arr(event.fact_ids).length} 条事实 · ${semanticProgress(graph,event)}`));
      if(event.invocation_layer==='nested_tool')card.append(el('span','session-invocation','内部调用'));
      const node=associated,badges=el('span','session-event-badges'),issues=eventIssues(graph,event),summary=assessmentFor(graph,event);
      for(const status of [...new Set(issues.map(issue=>issue.risk_status).filter(value=>['input_warning','suspected'].includes(value)))])badges.append(chip(status==='input_warning'?'△ 输入警告':'◇ 疑似风险',status==='input_warning'?'warning':'suspected'));
      for(const category of [...new Set([...arr(node?.badges).map(value=>value.category),...issues.map(issue=>issue.category)])]){if(category==='uncertainty'&&summary)continue;const confirmed=issues.some(issue=>issue.category===category&&issue.state==='confirmed');badges.append(chip(({rule:'规则问题',model:'模型判断',safety:'安全风险',runtime:'运行异常',uncertainty:'证据不足'})[category]||category,category==='safety'?'risk':['rule','model'].includes(category)?confirmed?'error':'unknown':'neutral'));}
      assessmentBadges(graph,event,badges);
      if(event.type==='tool_result'&&event.is_error)badges.append(chip('工具返回错误','neutral'));if(!badges.children.length){const visual=visualFor(graph,event);badges.append(chip(({error:'！错误判断',risk:'▲ 安全风险',unknown:'？证据不足',passed:'✓ 检查通过',neutral:event.type==='session_message'?'已记录':'尚未检查'})[visual]||'尚未检查',visual));}card.append(badges);container.append(card);
    }
    if(!container.children.length)container.append(note('没有可显示的原生会话事件。'));return container;
  }
  function states(graph,ctx){
    const data=artifact(graph),wrap=el('div','session-state-wrap'),table=el('table','session-state-table'),head=el('thead'),tr=el('tr');
    tr.append(el('th','session-row-title','对象 · 属性 / 状态'));
    for(const column of arr(data.columns)){
      const th=el('th');th.append(selectButton('', 'session-column', {type:'event',eventId:column.event_id},ctx));
      th.firstChild.append(el('strong','','导入 '+column.step),el('span','',kind(eventBy(graph,column.event_id)||column)),el('small','','原生序号 '+(column.original_seq??'未提供')));tr.append(th);
    }
    head.append(tr);table.append(head);const body=el('tbody');
    for(const row of arr(data.rows)){
      const line=el('tr');line.dataset.stateRow=row.id;
      line.append(el('th','session-row-title',null,[el('strong','',row.entity_label||row.entity_id),el('span','',(row.label||row.relation).replace((row.entity_label||'')+' · ','')),el('small','',({env:'环境 / 记录',agent:'Agent 公开表达',belief:'Agent 公开表达',mind:'Agent 公开表达',task:'任务要求'})[row.side]||row.side||'')]));
      const versions=[...arr(row.versions)].sort((a,b)=>a.step-b.step);
      for(const column of arr(data.columns)){
        const cell=el('td');const current=versions.filter(version=>version.step<=column.step&&(version.in_force_until==null||column.step<=version.in_force_until)).at(-1);
        if(!current){cell.append(el('span','session-no-state','— 尚无记录'));line.append(cell);continue;}
        const exact=current.step===column.step,violated=arr(data.checks).some(check=>check.state==='violated'&&primaryFids(check).some(fid=>String(fid)===String(current.fid)))||(row.relation==='session_final_answer'&&arr(graph.analysis?.issues).some(issue=>['rule','model'].includes(issue.category)&&issue.state==='confirmed'&&arr(issue.affected_event_ids).some(id=>arr(current.source_events).includes(id))));
        const selected=ctx.selection?.type==='fact'&&String(ctx.selection.fid)===String(current.fid);
        const item=selectButton('',`session-state-cell ${exact?'changed':'carried'}${current.status==='unknown'?' unknown':''}${violated?' error':''}${selected?' selected':''}`,{type:'fact',fid:current.fid,eventId:column.event_id},ctx);
        item.dataset.fid=current.fid;item.dataset.eventId=column.event_id;item.title=raw(current.value);
        item.append(el('span','session-value',clip(current.value,105)),el('small','',`${exact?'新记录':'沿用已知值'} · F${current.fid}`),el('small','',statusLabel[current.status]||current.status));cell.append(item);line.append(cell);
      }body.append(line);
    }
    table.append(body);wrap.append(table);
    if(!arr(data.rows).length)wrap.append(note('当前没有可展示的结构状态。原始消息仍可在完整会话中查看。'));return wrap;
  }
  function coverage(graph,ctx){
    const data=artifact(graph),c=data.coverage||{},source=c.source_coverage||{},box=el('div','session-coverage');
    const metrics=[['消息接入',`${source.represented_messages??'—'} / ${source.native_messages??'—'}`],['结构化事件',c.events_semantic??'—'],['仅保留原文',c.events_preserved_only??'—'],['未支持内容',c.events_unsupported??'—']];
    const cards=el('div','session-coverage-metrics');for(const[label,value]of metrics)cards.append(el('article','',null,[el('span','',label),el('strong','',String(value))]));box.append(cards);
    box.append(fields([['已保留事件',`${c.events_preserved??'—'} / ${c.events_total??'—'}`],['已保留元数据',`${source.preserved_metadata_events??'—'} / ${source.native_metadata_events??'—'}`]]));
    if(arr(data.missing_modules).length)box.append(details(`未观测内容 · ${arr(data.missing_modules).length}`,[list(arr(data.missing_modules).map(module=>`${module.label||module.module}：${module.reason}`))]));
    const table=el('table','session-coverage-table'),head=el('tr');['原始定位 / 导入步','内容','处理范围'].forEach(text=>head.append(el('th','',text)));table.append(el('thead','',null,[head]));const body=el('tbody');
    for(const event of arr(data.events)){const row=el('tr'),link=selectButton(`原生 ${event.original_seq??'?'} / 导入 ${event.step}`,'session-text-link',{type:'event',eventId:event.event_id},ctx);row.append(el('td','',null,[link]),el('td','',kind(event)),el('td','',null,[el('strong','',semanticLabel[event.semantic_status]||'范围未声明'),el('small','',event.semantic_note||'查看事件原文与事实。'),arr(event.unsupported_blocks).length?el('small','','含未支持内容块，原文仍保留'):null]));body.append(row);}table.append(body);box.append(table);
    if(arr(c.notes).length)box.append(details('采集与覆盖说明',[list(c.notes)]));if(c.source_coverage)box.append(details('原始接入核对记录',[code(c.source_coverage)]));if(arr(data.limitations).length)box.append(details('语义解析的限制',[list(data.limitations)]));return box;
  }
  function render(graph,mode,ctx){if(!available(graph))return el('div','session-unavailable',null,[el('h3','','这条轨迹还没有完整会话输入'),note('请切换“工具调用”或“工具实体”查看已有结果。要核对完整过程，需要导入带 native_transcript 的会话文件包。')]);return mode==='states'?states(graph,ctx):mode==='coverage'?coverage(graph,ctx):timeline(graph,ctx);}
  function pointers(graph,fid,eventId,ctx){
    const all=arr(artifact(graph)?.pointers),root=el('div','session-pointers');
    for(const[type,title]of Object.entries(pointerNames)){
      const matches=all.filter(pointer=>pointer.type===type&&[pointer.from,pointer.to].some(end=>end&&((fid!=null&&end.fid!=null&&String(end.fid)===String(fid))||(eventId&&end.event_id===eventId))));
      const group=el('section','session-pointer-group',null,[el('h4','',title+'指针')]);
      if(!matches.length)group.append(note('无'+title+'关联'));
      for(const pointer of matches){
        const card=el('article','session-pointer',null,[el('strong','',pointer.label||title),...(pointer.note?[note(pointer.note)]:[])]);
        for(const[label,end]of [['起点',pointer.from],['终点',pointer.to]])if(end){
          if(end.fid!=null)card.append(selectButton(`${label}：F${end.fid}`,'session-text-link',{type:'fact',fid:end.fid},ctx));
          else if(end.event_id)card.append(selectButton(`${label}：${end.event_id}`,'session-text-link',{type:'event',eventId:end.event_id},ctx));
          else if(end.check_id)card.append(el('span','session-pointer-check',`${label}：${end.check_id}`));
          else if(end.source)card.append(details(label+'：输入来源',[code(end.source)]));
        }card.append(el('small','',({recorded:'记录中直接关联',derived:'由确定性规则建立',unknown:'关联未确认'})[pointer.certainty]||pointer.certainty||''));group.append(card);
      }root.append(group);
    }return root;
  }
  function checks(graph,fid,eventId){
    const applicable=arr(artifact(graph)?.checks).filter(check=>(fid!=null&&arr(check.fids).some(id=>String(id)===String(fid)))||(eventId&&arr(check.event_ids).includes(eventId))),box=el('section','detail-section');box.append(el('h3','','会话状态检查'));
    if(!applicable.length)box.append(note('无适用的状态检查'));
    for(const check of applicable){const affected=(fid!=null&&primaryFids(check).some(id=>String(id)===String(fid)))||(eventId&&arr(check.affected_event_ids).includes(eventId));box.append(el('article','session-check',null,[chip(checkLabel[check.state]||check.state,check.state==='violated'&&affected?'error':check.state==='passed'?'passed':check.state==='unknown'?'unknown':'neutral'),el('strong','',check.title||check.rule_id),check.state==='violated'&&!affected?note('当前事件 / 事实是这项检查的对照证据，不是该检查定位的错误。'):null,note(check.explanation),fields([['规则',check.rule_id],['主要影响事实',primaryFids(check)],['主要影响事件',check.affected_event_ids],['关联调用',check.call_ids]]),arr(check.missing).length?list(check.missing):null,details('检查的原始证据',[code(check.evidence)])].filter(Boolean)));}return box;
  }
  function assessmentDetail(graph,event){
    const summary=assessmentFor(graph,event);if(!summary)return null;
    const section=el('section','session-assessment',null,[el('h4','','检查结果')]),badges=el('div','session-event-badges');assessmentBadges(graph,event,badges);section.append(badges);
    const rows=assessmentItems(summary).map(item=>{
      const status=item.display_status,cls=status==='passed'?'passed':status==='risk'?'risk':uncertaintyStatuses.has(status)?'unknown':'coverage';
      const scope=checkScope(item),box=el('article','session-assessment-item',null,[chip(assessmentNames[status]||item.label||status,cls),scope?el('strong','',tr(...scopeNames[scope])):null,el('strong','',item.policy_id||item.rule_id||item.id),el('small','',checkSources[item.source]||item.source||'')].filter(Boolean));
      if(item.reason)box.append(note(item.reason));
      box.append(details('证据与检查记录',[code(item)]));return box;
    });
    section.append(details(`检查明细 · ${rows.length}`,rows),details('检查版本与关联',[fields([['分析版本',summary.analysis_version],['调用 ID',summary.call_id],['关联导入步骤',summary.shared_steps]]),code(summary)]));return section;
  }
  function detail(graph,selection,ctx){
    if(!available(graph))return scope(graph);const data=artifact(graph),box=el('div','session-detail');
    if(!selection){box.append(scope(graph),note('选择一个事件或事实。'));return box;}
    if(selection.type==='fact'){
      const found=versionBy(graph,selection.fid);if(!found){box.append(note('当前会话没有这条事实版本；保留引用，无法展开。'));return box;}
      const{row,version}=found;box.append(el('h3','',row.label||row.relation),chip(statusLabel[version.status]||version.status),fields([['实体',row.entity_label||row.entity_id],['事实版本','F'+version.fid],['形成于导入步骤',version.step],['原始序号',version.source?.original_seq??'请核对出处事件'],['状态属性',row.relation]]),code(version.value));
      for(const id of arr(version.source_events))box.append(selectButton('查看出处事件 '+id,'session-text-link',{type:'event',eventId:id},ctx));
      box.append(details('事实版本与来源原文',[code(version)]),checks(graph,version.fid),pointers(graph,version.fid,null,ctx));return box;
    }
    const event=eventBy(graph,selection.eventId);if(!event){box.append(note('选中的原生事件不在当前输入中。'));return box;}
    box.append(el('h3','',kind(event)),fields([['原生事件序号',event.original_seq],['导入步骤编号',event.step],['事件 ID',event.event_id],['调用 ID',event.call_id],[spanLabel(graph,event),event.span_id],['调用层级',invocationNames[event.invocation_layer]||event.invocation_layer],['父调用 ID',event.parent_call_id||event.provenance?.parent_tool_call_id],['角色',event.role],['停止原因',event.stop_reason]]));
    const assessment=assessmentDetail(graph,event);if(assessment)box.append(assessment);
    box.append(el('h4','','完整记录正文'),code(eventText(event)),el('h4','','解析结果'),note(semanticLabel[event.semantic_status]||'未声明'));
    const facts=el('div','session-fact-links');for(const fid of arr(event.fact_ids))facts.append(selectButton('F'+fid,'session-text-link',{type:'fact',fid},ctx));box.append(facts);
    const node=nodeFor(graph,event),issues=eventIssues(graph,event);for(const issue of issues){
      if(assessment&&issue.category==='uncertainty'){box.append(details('原始未决检查 · '+(issue.rule_id||issue.source),[code(issue)]));continue;}
      const explanation=note(issue.explanation||issue.message);
      box.append(el('section','session-check',null,[chip(({rule:'规则问题',model:'模型判断',safety:'安全风险',runtime:'运行异常',uncertainty:'证据不足'})[issue.category]||issue.category,issue.state==='confirmed'&&['rule','model'].includes(issue.category)?'error':issue.category==='safety'?'risk':'unknown'),el('strong','',issue.title),explanation,fields([['来源',issue.source||'分析结果'],['状态',issue.state]]),details('发现的证据与不确定性',[code(issue)])]));
    }if(node)box.append(button('查看完整检查结果','studio-button',()=>ctx.onCall?.(node.id)));
    box.append(details('来源与解析范围',[fields([['原生会话文件',event.source?.transcript_path||event.source?.document],['JSON 定位',event.source?.json_pointer],['会话摘要',event.source?.transcript_sha256],['规范化事件摘要',event.source?.sha256],['原生记录附带摘要',event.source?.native_event_sha256]]),note(event.semantic_note||''),code(event)]),checks(graph,null,event.event_id),pointers(graph,null,event.event_id,ctx));return box;
  }
  function nodeAssessment(graph,node){
    const event=arr(artifact(graph)?.events).find(item=>nodeFor(graph,item)?.id===node.id);
    const summary=event?assessmentFor(graph,event):node.check_summary;
    if(!summary)return null;
    const target=event||{step:arr(node.action_steps)[0],type:'tool_call',trace_id:String(node.index||'').split('/')[0],span_id:String(node.index||'').split('/').slice(1).join('/')};
    const badges=el('span','session-event-badges');assessmentBadges(graph,target,badges);
    return {summary,visual:visualFor(graph,target),badges};
  }
  window.SessionView={available,render,detail,scope,eventBy,versionBy,nodeFor,nodeAssessment,assessmentFor,visualFor};
})();
