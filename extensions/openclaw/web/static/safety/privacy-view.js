'use strict';
// Read-only view of policy-validated privacy artifacts. The browser never
// classifies data, infers a flow, or converts a reference answer into a finding.
(() => {
  const {el,button,arr,raw,details,code,fields}=AnalysisUI.helpers;
  const selections=new Map();
  const tr=(zh,en)=>window.I18n?.language==='zh'?zh:en;
  const p=text=>el('p','privacy-note',text);
  const badge=(text,kind='neutral')=>el('span','privacy-badge '+kind,text);
  const section=(title,children)=>el('section','privacy-section',null,[el('h3','',title),...children.filter(Boolean)]);
  const sourceText=(text,tag='span')=>el(tag,'i18n-raw',text);
  const originalFields=items=>{const node=fields(items);for(const value of node.querySelectorAll('dd'))value.classList.add('i18n-raw');return node;};
  const typeLabels={employment_grade:['职级','Employment grade'],job_grade:['职级','Job grade'],grade:['职级','Job grade'],salary:['工资','Salary'],bank_account:['银行账户','Bank account'],bank_card:['银行卡号','Bank card number'],personal_id:['个人证件号码','Personal ID'],name:['姓名','Name']};
  const category=value=>typeLabels[value]?tr(...typeLabels[value]):String(value||tr('未声明类别','Category not specified'));
  const status=value=>({confirmed:tr('有证据支持','Evidence supported'),suspected:tr('疑似，尚未证实','Suspected, unconfirmed'),unknown:tr('证据不足','Insufficient evidence'),insufficient_evidence:tr('证据不足','Insufficient evidence'),passed:tr('适用检查通过','Applicable check passed'),not_applicable:tr('不适用','Not applicable'),input_warning:tr('输入警告','Input warning')})[value]||value||tr('未声明','Not specified');
  function datasets(ctx){return [{id:'automatic',label:tr('自动检查','Automatic checks'),artifact:ctx.automatic?.privacy,findings:arr(ctx.automatic?.findings)},
    {id:'model',label:tr('模型审查','Model review'),artifact:ctx.model?.result?.privacy||ctx.model?.privacy,findings:arr(ctx.model?.result?.findings)}].filter(item=>item.artifact);}
  function current(ctx){const all=datasets(ctx),selected=selections.get(ctx.graph?.case_id);return all.find(item=>item.id===selected)||all.find(item=>item.id==='model'&&ctx.model?.status==='succeeded')||all[0];}
  function reviewButton(ctx){const active=ctx.model&&!RiskView.terminal.has(ctx.model.status),b=button(active?tr('审查进行中','Review in progress'):tr('完整风险审查','Run risk review'),'studio-button primary',()=>ctx.onReview?.([ctx.graph.case_id]));b.disabled=!!active||!ctx.policy;return b;}
  function refs(value){return Array.isArray(value)?value:value?[value]:[];}
  function location(ref){const parts=[];if(ref?.step!=null||ref?.import_step!=null)parts.push(tr('导入步骤 ','Import step ')+(ref.step??ref.import_step));if(ref?.module)parts.push(ref.module);if(ref?.call_id)parts.push(ref.call_id);else if(ref?.model_span_id)parts.push('Span '+ref.model_span_id);return parts.join(' · ')||tr('原始字段引用','Source field reference');}
  function reference(ref,ctx,caption){
    if(!ref)return null;const box=el('article','privacy-reference');
    box.append(el('strong','',caption||location(ref)));
    const locate=button(tr('定位记录','Locate record'),'studio-button subtle',()=>ctx.onLocate?.(ref));locate.disabled=!ctx.canLocate?.(ref);box.append(locate);
    if(ref.quote!=null||ref.text!=null)box.append(el('span','privacy-source-label',tr('证据原文','Original evidence')),code(ref.quote??ref.text));
    if(ref.value_clipped||ref.text_clipped)box.append(badge(tr('已保存片段经过截断','Saved excerpt is truncated'),'unknown'));
    box.append(fields([[tr('导入步骤','Import step'),ref.step??ref.import_step],[tr('事实 ID','Fact ID'),ref.fid],[tr('来源 ID','Source ID'),ref.source_id],[tr('调用 ID','Call ID'),ref.call_id],[tr('事件 ID','Event ID'),ref.event_id],[tr('模型 Span','Model Span'),ref.model_span_id],[tr('内容部分','Module'),ref.module],[tr('原文字符范围','Source character range'),ref.start!=null?ref.start+'–'+ref.end:undefined]]));
    box.append(details(tr('完整来源引用','Full source reference'),[code(ref)]));return box;
  }
  function flowStages(flow,source){
    const sources=arr(flow.source_candidates).flatMap(item=>refs(item.refs?.length?item.refs:item.source||item).map(ref=>({...item,...ref}))),inputs=arr(flow.input_occurrences),output=flow.output;
    const outputs=output?refs(output.refs?.length?output.refs:output).map(ref=>({...output,...ref})):[];
    const saved=flow.stage_status||{};
    const stages=[
      {id:'return',title:tr('工具返回','Tool return'),refs:sources,present:sources.length>0&&saved.return!=='missing',empty:tr('来源返回未确认','Source return not confirmed')},
      {id:'model_input',title:tr('模型实际输入','Actual model input'),refs:inputs,present:inputs.length>0&&saved.model_input!=='missing',empty:tr('未找到实际输入记录','No matching input record')},
      {id:'output',title:tr('报告输出','Report output'),refs:outputs,present:outputs.length>0&&saved.output!=='absent',empty:tr('未记录对应输出','Matching output not recorded')},
    ];
    const findings=source?relevantFindings(flow,source):[],types={return:'sensitive_data_acquisition',model_input:'sensitive_context_exposure',output:'sensitive_output_disclosure'};
    return stages.map(stage=>({...stage,findings:findings.filter(finding=>finding.risk_type===types[stage.id])}));
  }
  function stageCard(stage,ctx,compact=false){
    const box=el('article','privacy-stage '+(stage.present?'recorded':'missing'));box.dataset.privacyStage=stage.id;
    box.append(el('span','privacy-stage-index',({return:'1',model_input:'2',output:'3'})[stage.id]),el('h4','',stage.title),badge(stage.present?tr('已记录','Recorded'):tr('未确认','Unverified'),stage.present?'recorded':'unknown'));
    if(stage.findings.length){const confirmed=stage.findings.some(finding=>finding.status==='confirmed');box.append(badge(confirmed?tr('此环节发现风险','Risk found at this stage'):tr('此环节有待核实','This stage needs verification'),confirmed?'risk':'unknown'));}
    if(!stage.present)box.append(p(stage.empty));
    if(stage.refs.length){box.append(p(stage.refs.length+' '+tr('处引用','references')));const ref=stage.refs.find(item=>ctx.canLocate?.(item));if(ref){const b=button(location(ref),'privacy-locate',()=>ctx.onLocate?.(ref));b.title=tr('定位记录并展开原文','Locate the record and inspect its original content');box.append(b);}if(!compact)box.append(details(tr('查看证据原文','Inspect original evidence'),stage.refs.map((ref,i)=>reference(ref,ctx,tr('引用 ','Reference ')+(i+1)))));}
    return box;
  }
  function relevantFindings(flow,source){return source.findings.filter(finding=>arr(finding.privacy_flow_ids).includes(flow.id)||arr(finding.privacy_field_ids).some(id=>arr(flow.source_candidates).some(s=>s.field_id===id))||finding.privacy_rule_id&&finding.privacy_rule_id===flow.policy_id&&Number(finding.import_step??finding.step)===Number(flow.output?.step));}
  function accessAuditView(ctx){
    const issues=arr(ctx.graph?.analysis?.issues).filter(issue=>issue.source==='safety_audit'&&['forbidden_path','outside_allowed_roots'].includes(issue.rule_id)),groups=new Map();
    for(const issue of issues){const key=issue.call_id||issue.id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(issue);}
    if(!groups.size)return null;
    return section(tr('读取权限审计','Read-access audit'),[...groups.values()].map(items=>{
      const first=items[0],ref={step:arr(first.import_steps)[0],call_id:first.call_id,event_id:arr(first.original_locations)[0]?.action_event_id,module:'action'},supported=items.some(issue=>issue.certainty==='evidence_supported'&&['violation_attempt','confirmed','violated'].includes(issue.state)),box=el('article','privacy-finding '+(supported?'supported':'pending'));box.dataset.privacyAuditCall=first.call_id||first.id;
      box.append(el('div','privacy-inline',null,[el('strong','',tr('读取调用命中访问策略','Read call flagged by access policy')),badge(tr('安全策略审计','Safety-policy audit')),badge(supported?tr('违规尝试','Violation attempt'):tr('证据不足','Insufficient evidence'),supported?'risk':'unknown')]),fields([[tr('导入步骤','Import step'),ref.step],[tr('调用 ID','Call ID'),ref.call_id],[tr('命中的规则','Matched rules'),items.map(issue=>issue.rule_id).join(', ')]]));
      box.append(p(items.length+' '+tr('项已保存检查关联到同一次调用。','saved checks refer to this one call.')));
      const b=button(tr('定位读取调用','Locate read call'),'studio-button subtle',()=>ctx.onLocate?.(ref));b.disabled=!ctx.canLocate?.(ref);box.append(b);
      for(const issue of items)box.append(details(issue.title||issue.rule_id,[p(issue.explanation||''),...arr(issue.evidence).map(evidence=>reference({...ref,...evidence},ctx)),details(tr('原始审计记录','Original audit record'),[code(issue)])]));return box;
    }));
  }
  function findingView(finding,ctx){
    const box=el('article','privacy-finding '+(finding.status==='confirmed'?'supported':'pending'));box.dataset.privacyFinding=finding.id;
    const type=finding.risk_type==='sensitive_data_acquisition'?tr('工具返回包含禁止获取的信息','Tool return contains information prohibited from acquisition'):finding.risk_type==='sensitive_output_disclosure'?tr('报告包含受限信息','Restricted information in report'):finding.risk_type==='sensitive_context_exposure'?tr('受限信息进入上下文','Restricted information in context'):finding.risk_type==='read_scope'?tr('越界读取','Out-of-scope read'):finding.title||finding.risk_type;
    box.append(el('div','privacy-inline',null,[el('strong','',type),badge(status(finding.status),finding.status==='confirmed'?'risk':'unknown')]),p(finding.explanation||''));
    box.append(fields([[tr('策略','Policy'),arr(finding.policy_ids).join(', ')||finding.privacy_rule_id],[tr('导入步骤','Import step'),finding.import_step??finding.step],[tr('调用 ID','Call ID'),finding.call_id]]));
    const primary={step:finding.import_step??finding.step,call_id:finding.call_id,event_id:finding.event_id,module:finding.module};const b=button(tr('定位发现','Locate finding'),'studio-button subtle',()=>ctx.onLocate?.(primary));b.disabled=!ctx.canLocate?.(primary);box.append(b);
    box.append(details(tr('支持证据','Supporting evidence'),arr(finding.evidence).map(ref=>reference(ref,ctx)).filter(Boolean)),details(tr('原始发现与限制','Original finding and limitations'),[code(finding)]));return box;
  }
  function flowView(flow,ctx,source){
    const box=el('article','privacy-flow');box.dataset.privacyFlow=flow.id;
    box.append(el('div','privacy-inline',null,[el('h4','',category(flow.category)),badge(flow.status==='verified_observed_chain'?tr('三段记录已关联','Three records linked'):tr('来源关联不完整','Partial source association'),flow.status==='verified_observed_chain'?'recorded':'unknown')]));
    box.append(originalFields([[tr('人员（原文）','Person (original)'),flow.subject],[tr('策略','Policy'),flow.policy_id]]));
    if(flow.value!=null)box.append(el('span','privacy-source-label',tr('记录中的值','Recorded value')),code(flow.value));
    const stages=el('div','privacy-chain');for(const stage of flowStages(flow,source))stages.append(stageCard(stage,ctx));box.append(stages);
    if(arr(flow.source_candidates).length>1)box.append(p(tr('保留所有可匹配来源，尚未证明唯一来源。','All matching sources are retained; a unique source is not proven.')));
    const findings=relevantFindings(flow,source);if(findings.length)box.append(...findings.map(finding=>findingView(finding,ctx)));
    if(arr(flow.missing).length)box.append(details(tr('未确认的环节','Unverified links'),arr(flow.missing).map(item=>p(typeof item==='string'?item:raw(item))),true));
    if(arr(flow.limitations).length)box.append(details(tr('证据限制','Evidence limits'),arr(flow.limitations).map(item=>p(typeof item==='string'?item:raw(item)))));
    box.append(details(tr('完整关联记录','Full association record'),[code(flow)]));return box;
  }
  function policyView(policy){
    if(!policy)return p(tr('尚未配置隐私审查策略。','No privacy review policy configured.'));
    const rules=arr(policy.privacy_rules),body=[];
    if(policy.read_scope)body.push(fields([[tr('允许访问的路径','Allowed file paths'),arr(policy.read_scope.allowed_roots).join(', ')],[tr('禁止访问的路径','Forbidden file paths'),arr(policy.read_scope.forbidden_paths).join(', ')]]));
    for(const rule of rules)body.push(el('article','privacy-policy-rule',null,[el('strong','',rule.id||rule.policy_id||category(rule.category)),fields([[tr('信息类别','Data category'),category(rule.category)],[tr('允许获取字段内容','Allowed to acquire field content'),typeof rule.allow_acquire==='boolean'?tr(rule.allow_acquire?'允许':'禁止',rule.allow_acquire?'Allowed':'Forbidden'):tr('未配置','Not configured')],[tr('允许进入上下文','Allowed in context'),typeof rule.allow_in_context==='boolean'?tr(rule.allow_in_context?'允许':'禁止',rule.allow_in_context?'Allowed':'Forbidden'):tr('未配置','Not configured')],[tr('允许进入报告','Allowed in report'),typeof rule.allow_in_output==='boolean'?tr(rule.allow_in_output?'允许':'禁止',rule.allow_in_output?'Allowed':'Forbidden'):tr('未配置','Not configured')]]),details(tr('规则原文','Original policy rule'),[code(rule)])]));
    return details(tr('本次隐私与读取政策','Privacy and read policy'),body.length?body:[code(policy)]);
  }
  function fieldView(field,ctx){
    const box=el('article','privacy-field'),forwarded=arr(field.forwarded_from_field_ids).length>0;box.dataset.privacyField=field.id;box.append(el('div','privacy-inline',null,[el('strong','',category(field.category)),badge(forwarded?tr('转发副本','Forwarded copy'):field.kind==='output'?tr('报告中的记录','Report occurrence'):tr('工具返回中的记录','Tool-return occurrence')),badge(field.classification_method==='explicit_policy_label'?tr('按已配置字段标签分类','Configured field label'):field.classification_method||tr('来源见引用','See source reference'))]));
    box.append(originalFields([[tr('字段 ID','Field ID'),field.id],[tr('人员（原文）','Person (original)'),field.subject],[tr('策略','Policy'),field.policy_id],[tr('分类来源','Classification source'),field.classification_source||field.source_type]]));
    if(field.value!=null)box.append(el('span','privacy-source-label',tr('记录中的值','Recorded value')),code(field.value));
    if(forwarded)box.append(originalFields([[tr('转发自字段','Forwarded from fields'),arr(field.forwarded_from_field_ids).join(', ')]]));
    box.append(...[reference(field.source,ctx),field.subject_source?details(tr('人员关联依据','Person association evidence'),[reference(field.subject_source,ctx)]):null].filter(Boolean));return box;
  }
  function investigationView(ctx){
    const job=ctx.model;if(!job)return p(tr('尚未运行模型调查。','Model investigation has not run.'));
    const result=job.result||{},box=el('div','privacy-investigation');box.append(RiskView.jobView(job,ctx));
    if(result.risk_map)box.append(details(tr('MAP · 已保存的政策与调查范围','MAP · saved policies and investigation scope'),[code(result.risk_map)]));
    const routes=arr(result.route_history);if(routes.length)box.append(details(tr('ROUTE · 实际安排的调查','ROUTE · recorded investigation choices'),routes.map((route,index)=>section(tr('第 ','Round ')+(index+1),arr(route.windows).map(window=>el('article','privacy-reference',null,[fields([[tr('调查编号','Investigation ID'),window.candidate_id||window.window_id],[tr('关注步骤','Focus step'),window.issue_step],[tr('事实引用','Fact references'),arr(window.issue_fids).map(fid=>'F'+fid).join(', ')]]),badge(tr('待核查的问题 / 假设','Question / hypothesis to investigate'),'unknown'),p(window.hypothesis||window.question||''),details(tr('原始调查安排','Original investigation selection'),[code(window)])]))))));
    const queries=arr(job.queries||result.queries);
    if(queries.length)box.append(details(tr('TRACE · 实际证据查询','TRACE · recorded evidence queries'),queries.map((query,index)=>{
      const rows=arr(query.rows),failed=!!query.error||query.retrieval_status==='failed',partial=failed||query.retrieval_status==='partial'||query.partial||query.truncated||arr(query.function_errors).length||arr(query.skipped_branches).length;
      return details(tr('查询 ','Query ')+(index+1)+' · '+(failed?tr('查询失败','Failed query'):(query.returned_row_count??query.returned_rows??rows.length)+' '+tr('条结果','rows')),[badge(failed?tr('查询失败','Failed query'):partial?tr('查询有缺口','Query has coverage gaps'):tr('已保存查询结果','Query result saved'),partial?'unknown':'recorded'),...(query.query_attempt!=null?[badge(tr('尝试 ','Attempt ')+query.query_attempt)]:[]),...(Number(query.query_attempt)>1||query.query_retry===true?[badge(tr('纠正后重试','Corrective retry'))]:[]),...(failed?[p(tr('本次查询未成功执行，不能解释为没有匹配证据。','This query did not execute successfully; it does not establish that no evidence matched.')),el('span','privacy-source-label',tr('查询错误','Query error')),code(query.error||query.retrieval_status)]:partial?[p(tr('部分查询分支不可用；已返回的记录仅支持其实际覆盖范围。','Some query branches were unavailable; returned records support only their recorded scope.'))]:[]),...(partial?[details(tr('检索缺口记录','Retrieval gap records'),[code({function_errors:query.function_errors,skipped_branches:query.skipped_branches,partial:query.partial,truncated:query.truncated})])]:[]),el('span','privacy-source-label',tr('原始查询','Original query')),code(query.query||query.sql||query),...rows.map(row=>reference({...row,quote:row.text??row.span},ctx)),details(tr('完整查询结果与限制','Full query result and limits'),[code(query)])]);
    })));
    return box;
  }
  function summary(ctx){
    const source=current(ctx);if(!source)return null;const artifact=source.artifact,flow=arr(artifact.flows)[0],box=el('section','privacy-overview');
    box.append(el('div','privacy-inline',null,[el('strong','',tr('隐私来源','Privacy trail')),badge(source.label),button(tr('查看字段、风险和证据','Inspect fields, risks and evidence'),'semantic-link',ctx.onOpen)]));
    box.append(p(`${arr(artifact.fields).length} ${tr('条字段记录','field occurrences')} · ${new Set(arr(artifact.fields).map(field=>field.category)).size} ${tr('类信息','data categories')} · ${arr(artifact.flows).length} ${tr('条输出关联','output associations')}`));
    if(flow){const chain=el('div','privacy-chain compact');for(const stage of flowStages(flow,source))chain.append(stageCard(stage,ctx,true));box.append(chain);}return box;
  }
  function render(ctx){
    const box=el('div','privacy-view');box.dataset.detailView='privacy';if(!ctx.graph){box.append(p(tr('先选择一条轨迹。','Select a trace first.')));return box;}
    const source=current(ctx),all=datasets(ctx);
    box.append(section(tr('隐私来源与输出','Privacy sources and output'),[el('div','privacy-inline',null,[badge(ctx.constructed?tr('构造副本 · 未实际执行','Constructed copy · not executed'):ctx.sourceLabel||tr('来源见记录','See provenance')),reviewButton(ctx)]) ]));
    box.append(policyView(ctx.policy));
    if(!source){box.append(p(tr('当前分析尚无隐私字段或输出关联记录。请核对策略与采集内容。','The current analysis has no privacy-field or output-association artifact. Check the policy and captured content.')));if(ctx.model)box.append(RiskView.jobView(ctx.model,ctx));return box;}
    const selector=el('div','privacy-source-switch');for(const item of all){const b=button(item.label,'studio-button'+(source.id===item.id?' primary':''),()=>{selections.set(ctx.graph.case_id,item.id);ctx.onChange?.();});b.dataset.privacySource=item.id;b.setAttribute('aria-pressed',String(source.id===item.id));selector.append(b);}box.append(selector);
    box.append(p(tr('以下为已保存的记录关联；不证明内部思考或唯一因果。','These are saved record associations; they do not prove internal reasoning or a unique cause.')));
    const artifact=source.artifact,flows=arr(artifact.flows),privacyFindings=source.findings.filter(f=>f.privacy_rule_id||f.privacy_flow_ids||['read_scope','sensitive_data_acquisition','sensitive_context_exposure','sensitive_output_disclosure'].includes(f.risk_type));
    if(source.id==='automatic'){const access=accessAuditView(ctx);if(access)box.append(access);}
    box.append(section(tr('风险结论','Risk findings'),privacyFindings.length?privacyFindings.map(f=>findingView(f,ctx)):[p(tr('本来源未记录隐私风险发现；请结合检查覆盖范围。','This source recorded no privacy risk finding; see the check coverage.'))]));
    box.append(section(tr('来源 → 实际输入 → 报告','Source → actual input → report'),flows.length?flows.map(flow=>flowView(flow,ctx,source)):[p(tr('尚无记录支持的输出关联。','No recorded output association is available.'))]));
    box.append(section(tr('字段记录与出处','Field occurrences and sources'),arr(artifact.fields).length?arr(artifact.fields).map(field=>fieldView(field,ctx)):[p(tr('尚未提取到适用字段。','No applicable fields extracted.'))]));
    box.append(section(tr('模型调查过程','Model investigation'),[investigationView(ctx)]));
    box.append(details(tr('覆盖范围与完整记录','Coverage and full artifact'),[code(artifact.coverage||{}),code(artifact)]));
    if(ctx.onCompare)box.append(button(tr('独立答案与定位对照','Independent reference and location comparison'),'studio-button',ctx.onCompare));
    return box;
  }
  window.PrivacyView=Object.freeze({render,summary});
})();
