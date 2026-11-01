'use strict';
// Presentation only. All findings, scope and evidence come from saved reports.
// Validation references are fetched separately and never create findings.
(() => {
  const {el, button, arr, raw, details, code, fields} = AnalysisUI.helpers;
  const terminal = new Set(['succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted', 'completed', 'partial', 'error', 'budget_exhausted']);
  const statusNames = {confirmed:'有证据支持的风险', suspected:'风险线索 · 尚未证实', unknown:'证据不足', insufficient_evidence:'证据不足', input_warning:'输入证据警告', passed:'适用检查通过', pass:'本项调查通过', risk:'本项发现风险', satisfied:'适用检查通过', safe:'当前范围通过', not_applicable:'不适用', unsupported:'未支持', not_evaluated:'未评估', not_configured:'未配置风险策略', queued:'排队中', running:'审查中', succeeded:'审查流程已完成', completed:'审查流程已完成', partial:'部分完成', failed:'审查失败', error:'审查失败', cancelled:'已取消', timed_out:'超时', interrupted:'已中断', budget_exhausted:'预算耗尽', violated:'违反当前策略', violation_attempt:'违规尝试', attempt:'有违规尝试', no_findings:'当前检查未发现风险'};
  const types = {unauthorized_action:'未授权操作', unauthorized_access:'越权访问', data_exfiltration:'信息外传', privacy_leak:'隐私泄漏', prompt_injection:'提示注入', prompt_injection_following:'执行了不可信指令', protected_write:'受保护内容修改', unauthorized_send:'未经批准发送', out_of_scope_access:'超出任务范围访问', protected_config_change:'受保护配置修改',read_scope:'超出任务授权范围读取',unapproved_side_effect:'未经批准产生外部操作',sensitive_data_transfer:'受限信息外传',sensitive_data_acquisition:'工具返回包含禁止获取的信息',sensitive_context_exposure:'受限信息出现在可见上下文',sensitive_output_disclosure:'报告包含受限信息',untrusted_instruction:'不可信内容中的指令'};
  const label = value => statusNames[value] || value || '未提供';
  const paragraph = text => el('p', 'detail-summary', text);
  const originalText = text => el('p', 'detail-summary i18n-raw', text);
  const chip = (text, cls='neutral') => el('span', 'analysis-chip '+cls, text);
  const section = (title, children) => el('section', 'detail-section risk-section', null, [el('h3', 'detail-title', title), ...children.filter(Boolean)]);
  const parse = value => {try {return typeof value==='string'?JSON.parse(value):value||{};} catch {return {};}};
  const knownRisk = finding => finding.status==='confirmed';
  function policyBoundaries(policy) {
    if(!policy)return null;
    const scope=policy.read_scope,items=[paragraph('路径权限、内容获取、模型输入、报告输出和对外发送分别检查。'),
      fields([['读取范围',scope?'允许：'+arr(scope.allowed_roots).join('、')+'；禁止：'+(arr(scope.forbidden_paths).join('、')||'未指定'):'未配置；不据此判越权'],['外部操作批准',arr(policy.approvals).length+' 条显式授权 / 拒绝记录；缺失批准记录仍需核实']])];
    for(const spec of arr(policy.sensitive_sources))items.push(el('article','risk-check',null,[el('strong','',spec.id),fields([
      ['数据来源',spec.tool+' · '+spec.result_pointer],
      ['当前对话可见',spec.allow_in_context===false?'明确禁止明文进入上下文':spec.allow_in_context===true?'明确允许当前上下文使用':'未配置禁止；不能仅凭外发限制判违规'],
      ['允许外发目标',arr(spec.allowed_destinations).length?spec.allowed_destinations.join('、'):'无；该字段限制外发']]) ]));
    for(const spec of arr(policy.privacy_rules))items.push(el('article','risk-check',null,[el('strong','',spec.id),fields([
      ['信息类别',spec.category],['允许获取字段内容',spec.allow_acquire===false?'禁止':spec.allow_acquire===true?'允许':'未配置'],['允许进入上下文',spec.allow_in_context===false?'禁止':spec.allow_in_context===true?'允许':'未配置'],['允许进入报告',spec.allow_in_output===false?'禁止':spec.allow_in_output===true?'允许':'未配置']]) ]));
    items.push(paragraph('这些是当前配置的检查边界。任务原文仍参与调查；尚未结构化的任务限制会标为待核实，不能靠一句引文冒充已验证政策。'));
    const block=details('权限边界 · 本条到底允许什么',items);block.classList.add('risk-policy-boundaries');return block;
  }
  function nodeFor(graph, finding) {
    if (!graph || !finding) return null;
    const calls=arr(graph.nodes).filter(node=>node.kind==='call');
    // Match the exact call first. A tool entity shared by several calls is
    // deliberately never used as a substitute for an absent call location.
    const exact = calls.find(node => finding.call_id && ([node.id,node.index,node.entity_id].includes(finding.call_id)||node.entity_id==='call:'+finding.call_id));
    if (exact) return exact;
    const byPayload = calls.filter(node => arr(graph.record?.facts).filter(f=>f.rel==='tool_call'&&arr(node.fids).includes(f.fid)).some(f=>{const value=parse(f.value);return finding.call_id ? [value.call_id,value.provenance?.tool_call_id].includes(finding.call_id) : finding.span_id && value.span_id===finding.span_id;}));
    if (byPayload.length===1) return byPayload[0];
    if (finding.call_id || finding.span_id) return null;
    const savedIssue=arr(graph.analysis?.issues).find(issue=>issue.risk_finding_id===finding.id);
    if(savedIssue){const nodes=arr(graph.nodes).filter(node=>['call','message'].includes(node.kind)&&arr(savedIssue.node_ids).includes(node.id));if(nodes.length===1)return nodes[0];}
    const matchingFacts=arr(graph.record?.facts).filter(f=>arr(finding.primary_fids).some(id=>String(id)===String(f.fid)));
    const callFact=matchingFacts.find(f=>f.rel==='tool_call');
    if (callFact) return calls.find(node=>arr(node.fids).some(id=>String(id)===String(callFact.fid)))||null;
    return arr(graph.nodes).find(node=>node.kind==='message'&&arr(node.fids).some(id=>arr(finding.primary_fids).some(fid=>String(fid)===String(id))))||null;
  }
  function effectiveFindings(report,ctx,source){
    return arr(report?.findings).map(finding=>{const issue=arr(ctx.graph?.analysis?.issues).find(item=>item.source===source&&item.risk_finding_id===finding.id);
      if(issue?.state==='unknown'&&finding.status==='confirmed')return {...finding,status:'unknown',missing:issue.missing,explanation:issue.explanation+' 当前来源核验未通过，已按证据不足展示。',original_status:finding.status};
      const integrityKey=source==='risk_rule'?'matching_source_and_risk_policy':'matching_risk_job';
      const invalid=arr(ctx.graph?.analysis?.issues).find(item=>item.source==='risk_integrity'&&arr(item.missing).includes(integrityKey));
      return invalid?{...finding,status:'unknown',missing:[...arr(finding.missing),invalid.explanation],original_status:finding.status}:{...finding,display_status:issue?.display_status,display_label:issue?.display_label};
    });
  }
  function evidence(entries) {
    return arr(entries).length ? arr(entries).map((item,index)=>el('article','risk-evidence',null,[
      el('strong','','证据 '+(index+1)),
      fields([['证据编号',item.reference_id],['导入步骤',item.step??item.import_step],['字段',item.module],['原始记录定位',item.source_id??item.source_ref??item.json_pointer],['事实引用',arr(item.fact_ids).map(id=>'F'+id).join('、')||item.fid]]),
      item.quote!=null?code(item.quote):item.text!=null?code(item.text):null,
      details('完整引用',[code(item)]),
    ].filter(Boolean))) : [paragraph('未记录此类证据，不能据此排除其他解释。')];
  }
  function findingCard(finding, ctx, source) {
    const confirmed=knownRisk(finding), node=nodeFor(ctx.graph,finding),coverageLimit=['policy_field_unavailable','unsupported','unconfigured','prerequisite_unavailable','not_applicable','not_evaluated','unvalidated'].includes(finding.display_status);
    const card=el('article','risk-finding '+(confirmed?'risk-confirmed':coverageLimit?'risk-coverage':finding.status==='input_warning'?'risk-input-warning':finding.status==='suspected'?'risk-suspected':'risk-uncertain'));
    card.dataset.findingId=finding.id||'';
    card.append(el('div','analysis-chips',null,[chip(source==='risk_model'?'模型调查':'自动风险规则'),chip(finding.display_label||label(finding.status),confirmed?'safety':coverageLimit?'neutral':finding.status==='input_warning'?'warning':finding.status==='suspected'?'suspected':'unknown')]),
      el('h4','',types[finding.risk_type]||finding.title||finding.risk_type||'风险发现'),paragraph(finding.explanation||'报告没有提供解释。'),details('原始分析记录',[originalText(finding.explanation||'报告没有提供解释。')]));
    if(finding.risk_type==='other')card.append(paragraph('任务语义候选：模型认为值得核查，但尚无结构化政策验证；不计作已确认风险。'));
    if(finding.effect)card.append(paragraph(finding.effect==='confirmed'?'报告记录了可支持该效果的证据。':finding.effect==='attempt'?'当前证据支持违规尝试；是否实际生效需结合返回和后续记录。':'后续影响尚不能确认。'));
    card.append(fields([['规则 / 策略',finding.rule_id||arr(finding.policy_ids).join('、')],['调用 ID',finding.call_id],['调用 / Span 标识',finding.span_id],['导入步骤',finding.import_step??finding.step],['关联事实',arr(finding.fact_ids).map(id=>'F'+id).join('、')]]));
    const locate=button(node?.kind==='message'?'定位公开消息':'定位这一次调用','studio-button subtle',()=>ctx.onLocate?.(finding,source));
    locate.disabled=!node;locate.title=node?'定位已保存的实体，不据此添加因果关系':'报告没有可精确匹配的调用或消息，保留为轨迹级发现';const original=button('查看事实与原文','studio-button subtle',()=>ctx.onEvidence?.(finding,source));original.disabled=!node;card.append(el('div','detail-actions',null,[locate,original]));
    card.append(details('支持证据 · '+arr(finding.evidence).length,evidence(finding.evidence)),details('反证 · '+arr(finding.counterevidence).length,evidence(finding.counterevidence)));
    if(arr(finding.missing).length)card.append(details('还缺哪些信息',arr(finding.missing).map(item=>paragraph(raw(item)))));
    if(arr(finding.limitations).length)card.append(details('本项结论之外的限制',arr(finding.limitations).map(item=>paragraph(raw(item)))));
    if(arr(finding.cause_hypotheses).length)card.append(details('可能的原因 · 候选，不是唯一根因',arr(finding.cause_hypotheses).map(candidate=>el('div','risk-hypothesis',null,[paragraph(candidate.explanation||raw(candidate)),details('依据及限制',[code(candidate)])]))));
    card.append(details('完整发现记录',[code(finding)]));return card;
  }
  function findingsSection(title,report,ctx,source) {
    const items=effectiveFindings(report,ctx,source),confirmed=items.filter(knownRisk),warnings=items.filter(item=>item.status==='input_warning'),suspected=items.filter(item=>item.status==='suspected'),unknown=items.filter(item=>!knownRisk(item)&&!['input_warning','suspected'].includes(item.status));
    return section(title,[paragraph(report?`${confirmed.length} 项有证据的风险 · ${warnings.length} 项输入警告 · ${suspected.length} 项疑似 · ${unknown.length} 项检查限制`:'尚无此项审查结果。'),
      ...items.map(item=>findingCard(item,ctx,source)),
      report&&!items.length?paragraph(report.status==='not_configured'?'未配置风险策略':'当前检查未发现风险'):null,
    ]);
  }
  function checkSection(report,title) {
    if(!report)return null;
    const checks=arr(report.checks);
    return details(title+' · '+checks.length+' 项',[
      ...checks.map(check=>el('article','risk-check',null,[el('strong','',check.title||check.rule_id||check.policy_id||check.id||'检查'),chip(label(check.status||check.state),(check.applied===true||check.executed===true)&&['passed','safe','satisfied'].includes(check.status||check.state)?'passed':'neutral'),check.explanation?paragraph(check.explanation):null,check.reason?paragraph(check.reason):null,details('检查依据',[code(check)])].filter(Boolean))),
      !checks.length?paragraph('此报告没有逐项检查记录。'):null,
      details('覆盖与证据限制',[fields([['调用总数',report.coverage?.total_calls],['已开展适用检查的调用',report.coverage?.assessed_calls],['适用检查',report.coverage?.applicable_checks],['已完成检查',report.coverage?.completed_checks],['可观察的行为 / 消息步骤',report.coverage?.observable_action_message_steps],['模型已调查步骤',report.coverage?.assessed_steps],['模型未调查步骤',report.coverage?.unassessed_steps]]),code(report.coverage||{}),...arr(report.limitations).map(value=>paragraph(raw(value))),report.uncertainty?paragraph(raw(report.uncertainty)):null].filter(Boolean)),
    ].filter(Boolean));
  }
  function obligationCoverage(report){
    const coverage=report?.coverage?.obligation_coverage;if(!coverage)return null;
    const statuses={pass:'本项通过',risk:'发现风险',unknown:'证据不足',not_applicable:'不适用',not_reviewed:'尚未检查'};
    return section('实际检查了哪些行为',[paragraph(`模型已检查 ${coverage.model_evaluated??0} / ${coverage.expected??0} 项；${arr(coverage.unassessed).length} 项尚未检查，${arr(coverage.uncertain).length} 项仍不确定。`),details('展开行为与适用策略',arr(coverage.obligations).map(item=>el('article','risk-check',null,[el('strong','',item.question||types[item.risk_type]||item.risk_type||item.id),fields([['导入步骤',item.step],['调用',item.call_id],['策略',item.policy_id],['自动检查',statuses[item.automatic_status]||item.automatic_status||'尚未检查'],['模型检查',statuses[item.model_status]||item.model_status||'尚未检查']]),details('检查范围的原始记录',[code(item)])])))]);
  }
  function jobView(job, ctx={}) {
    if(!job)return paragraph('尚未运行模型风险审查。自动规则仍可先查看。');
    const active=!terminal.has(job.status),stage=job.stage||job.phase;
    const block=el('div','risk-job');block.append(el('div','analysis-chips',null,[chip(label(job.status),active?'neutral':['failed','error','timed_out'].includes(job.status)?'unknown':'neutral'),stage?chip(stage):null].filter(Boolean)));
    const stages=arr(job.stages),strip=el('div','stage-strip');
    for(const [key,title] of [['PARSE','语义解析'],['MAP','审查范围'],['ROUTE','安排调查'],['TRACE','逐项核实'],['SUBMIT','汇总风险']]) {
      const saved=stages.find(item=>item===key||item.stage===key||item.name===key||item.phase===key),done=saved&&(typeof saved==='string'||['completed','succeeded','done'].includes(saved.status)),current=active&&stage===key;
      strip.append(el('span','stage-chip'+(done?' done':current?' current':''),(done?'✓ ':current?'◌ ':'')+title));
    }
    block.append(strip);
    const retrieval=job.result?.coverage;if(typeof retrieval?.retrieval_complete==='boolean')block.append(fields([['证据检索',retrieval.retrieval_complete?'已请求的证据查询已完成':'证据检索仍有缺口'],['失败的查询尝试',retrieval.query_error_count]]));
    const modern=['visible-references-policy-contract/v1','visible-references-policy-contract/v2','visible-references-policy-contract/v3','signoz-semantic-policy-coverage/v4'].includes(job.review_protocol),current=job.review_protocol==='signoz-semantic-policy-coverage/v4';
    if(!current)block.append(details('历史运行',[fields([['审查协议',job.review_protocol||'legacy']])]));
    if(modern)block.append(details('引用与政策校验记录',[paragraph('拒绝表示某次模型中间输出没有通过校验，不表示被调查轨迹发生了违规。纠正成功和最终失败都会保留记录。'),
      fields([['可见证据目录',job.reference_audit?.catalogs],['引用解析次数',job.reference_audit?.resolution_attempts],['被拒绝的中间输出',job.reference_audit?.rejections]]),
      details('被拒绝的输出及原因',[code(job.validation_rejections||[])]),details('选择的编号与还原结果',[code(job.reference_resolutions||[])]),details('各调查实际可见的证据目录',[code(job.reference_catalogs||[])]),details('减少重复上下文的收据',[paragraph('只减少重复原文和查询结束后不再使用的SQL说明；结构化结果、检索错误与证据限制继续保留。完整视图另存在本次运行的 model_evidence_views.jsonl。'),code(job.evidence_view_audit||[])])]));
    if(job.error)block.append(details('运行未完成的原因',[code(job.error)]));
    if(job.result?.summary)block.append(paragraph(raw(job.result.summary)),details('原始分析记录',[originalText(raw(job.result.summary))]));
    if(active&&ctx.onCancel)block.append(button('取消本条风险审查','studio-button subtle',()=>ctx.onCancel(job.id)));
    block.append(details('已记录的阶段与证据查询',[code({stages:job.stages,queries:job.queries||job.result?.queries,usage:job.usage,result_status:job.result?.status})]));
    if(job.result)block.append(details('本次运行保存的发现与完整结果',[code(job.result)]));
    return block;
  }
  function evaluationView(value,title) {
    if(!value)return paragraph(title+'：尚未完成独立评测。');
    if(value.status==='awaiting_analysis')return paragraph(title+'：模型流程结束后开放，不提前显示预设数量与位置。');
    const text=value.outcome||value.status||value.verdict;
    return details(title+(value.label?' · '+value.label:text?' · '+label(text):''),[value.explanation?paragraph(value.explanation):null,
      fields([['已知风险精确命中',value.expected_count?`${value.exact_hits??0} / ${value.expected_count} 项`:'本对照没有预设确定风险'],['范围外的风险判断',arr(value.extra_findings).length],['把缺证判成确定',value.overconfident_count],['是否发现目标问题',value.detected??value.detection??value.found],['精确调用匹配',value.exact_call_match??value.exact_location_match],['问题类型匹配',value.risk_type_match??value.issue_type_match],['相关证据引用',value.evidence_cited]]),
      value.scope?paragraph(value.scope):null,details('独立评测原始记录',[code(value)])].filter(Boolean));
  }
  function validationView(ctx) {
    const item=ctx.validation;if(!item)return null;
    const allowed=!!ctx.model&&terminal.has(ctx.model.status);
    const reference=allowed?ctx.reference:null;
    const holdout=item.validation_group==='holdout';
    const children=[paragraph(holdout?'这条属于独立编写的固定测试集：输入和答案预先固定。本轮是冻结v3后的复测；早期v2任务也曾执行并保留，不能称首次盲测。来源修补未依据该组诊断内容或答案调参。':'这条属于开发验收集，曾用于改进实现，不作为独立能力证明。'),paragraph(item.is_constructed?'这是构造测试数据，不是修改后实际执行的记录。已知位置用于独立核对，不进入模型诊断输入。':'这条是未修改的基础对照。来源类别及采集记录用于核验真实性；不默认基础轨迹没有问题。'),
      fields([['案例 ID',item.case_id],['来源类别',item.source_category],['是否构造',item.is_constructed?'是':'按来源记录核对']]),
      evaluationView(item.automatic_evaluation||ctx.evaluation?.automatic_evaluation||ctx.evaluation?.automatic,'自动检查评测'),evaluationView(item.model_evaluation||ctx.evaluation?.model_evaluation||ctx.evaluation?.model,'模型调查评测'),
    ];
    if(reference){
      const truth=reference.ground_truth||reference.reference||{},expected=arr(truth.expected),actual=reference.evaluation?.model_evaluation||item.model_evaluation;
      children.push(fields([['标准答案固定时间',reference.fixed_at],['基础案例',truth.base_case_id||item.base_case_id]]));
      if(item.case_id==='rvt-d845b52d'&&item.is_constructed===false){
        children.push(el('div','risk-reference-errata',null,[el('strong','','来源资料勘误说明'),paragraph('样本 12 是原始行为对照。独立核对确认：它与原 session-run02-case-01 的 task、steps、session 完全相同，只有 trajectory_id 重编号；来源收据中的四项文件哈希均一致。'),paragraph('固定标准答案的 provenance 误与样本 13 共用了描述，其中 changed_field、before、after、derived_changes 属于样本 13 的构造修改，不是样本 12 的行为差异。原标准答案保持不变，此处追加说明；评测不把这些共享描述当作样本 12 的注入故障。')]));
      }
      for(const [index,wanted]of expected.entries()){
        const matched=arr(actual?.checks)[index],pair=el('div','comparison-pair',null,[el('div','comparison-cell',null,[el('span','detail-eyebrow','预先固定的位置'),fields([['风险类型',types[wanted.risk_type]||wanted.risk_type],['调用',wanted.call_id],['Span',wanted.span_id],['导入步骤',wanted.import_step],['标准状态',label(wanted.status)]])]),el('div','comparison-cell',null,[el('span','detail-eyebrow','模型实际定位'),paragraph(matched?({hit:'精确命中',missed:'未发现',mislocalized:'调用位置不同',uncertain:'尚不确定',uncertainty_preserved:'正确保留不确定',overconfident:'把缺证误判成确定'}[matched.status]||matched.status):'尚无完整模型结果'),...arr(matched?.actual).map(found=>fields([['调用',found.call_id],['Span',found.span_id],['导入步骤',found.import_step],['模型状态',label(found.status)]]))])]);
        children.push(pair);
      }
      if(!expected.length)children.push(paragraph('此对照没有预先注入的确定风险。只评价已知范围内是否误报，不能认定整个轨迹绝对安全。'));
      const diff=reference.diff||reference.differences||reference.changes;
      children.push(details('构造修改与差异',Array.isArray(diff)&&diff.length?diff.map(change=>el('article','detail-change',null,[el('strong','',change.field||change.path||'修改字段'),el('div','comparison-pair',null,[el('div','comparison-cell',null,[el('span','detail-eyebrow','修改前'),code(change.before)]),el('div','comparison-cell',null,[el('span','detail-eyebrow','修改后'),code(change.after)])]),details('完整修改记录',[code(change)])])):[code(diff||'没有单独的差异记录')]),details('完整的预先固定标准答案',[code(truth)]),paragraph(holdout?'本组是固定版本的小规模复测；样本数量有限且已有早期运行，不能据此宣称首次盲测或整体定位准确率。':'本组仅用于开发验收，不能据此宣称泛化定位准确率。'));
    } else {
      const reveal=button(allowed?'展开独立答案与修改差异':'模型流程结束后可核对答案','studio-button subtle',()=>ctx.onReveal?.(item.case_id));reveal.disabled=!allowed;
      children.push(reveal,paragraph('先看系统结果，再按需打开答案；失败、取消或不确定都如实保留。'));
    }
    return section('风险定位验收',children);
  }
  function render(ctx={}) {
    const body=el('div','studio-detail risk-review');body.dataset.detailTab='risk';
    if(!ctx.graph){body.append(paragraph('从左侧选择轨迹，或载入“风险验收案例”开始查看。'));return body;}
    const active=!!ctx.model&&!terminal.has(ctx.model.status),start=button(active?'本条风险审查进行中':!ctx.policy?'本条未配置风险策略':'完整风险审查本条','studio-button primary',()=>ctx.onReview?.([ctx.graph.case_id]));start.disabled=active||!ctx.policy;
    body.append(section('风险项、原因与证据',[el('div','detail-actions',null,[start])]));
    if(ctx.loading)body.append(paragraph('正在读取本条已保存的风险报告…'));
    if(!ctx.policy)body.append(paragraph('请点击主工作区的“查看 / 编辑策略”，明确允许范围与授权要求。也可以在导入文件包中提供风险策略。'));
    if(ctx.error)body.append(el('p','risk-load-error',ctx.error+'；已有规则与任务诊断结果仍保留。'));
    const boundaries=policyBoundaries(ctx.policy);if(boundaries)body.append(boundaries);
    body.append(findingsSection('① 自动风险检查',ctx.automatic,ctx,'risk_rule'));
    const checks=checkSection(ctx.automatic,'自动检查及适用范围');if(checks)body.append(checks);
    body.append(section('② 完整模型风险审查',[jobView(ctx.model,ctx)]));
    const coverage=obligationCoverage(ctx.model?.result);if(coverage)body.append(coverage);
    if(ctx.model?.result){const complete=ctx.model.status==='succeeded'&&ctx.model.full_flow_completed===true;if(!complete)body.append(paragraph('下列为已保存的阶段发现，保留当时状态；完整流程未完成，不计作定位成功。'));body.append(findingsSection(complete?'模型调查发现':'模型阶段发现 · 完整流程未完成',ctx.model.result,ctx,'risk_model'));const modelChecks=checkSection(ctx.model.result,'模型检查与覆盖');if(modelChecks)body.append(modelChecks);}
    else if(arr(ctx.model?.partial_findings).length){body.append(paragraph('下方是已保存的阶段线索，尚未完成汇总核验，统一保留为待核实。'),findingsSection('阶段性调查线索',{findings:ctx.model.partial_findings.map(finding=>({...finding,original_status:finding.status,status:'suspected'}))},ctx,'risk_model'));}
    body.append(details('本条风险策略 · 与旧版基础安全策略独立',[ctx.policy?code(ctx.policy):paragraph('未提供风险策略原文，不能假定其他轨迹使用相同策略。')]),details('来源与报告核验',[fields([['轨迹 ID',ctx.graph.case_id],['输入 SHA-256',ctx.automatic?.source_sha256],['策略 SHA-256',ctx.automatic?.policy_sha256]]),paragraph('导入步骤是统一输入里的编号；原生事件顺序与 Agent 内部认知步骤另行区分。'),code({coverage:ctx.automatic?.coverage,limitations:ctx.automatic?.limitations})]));
    const validation=validationView(ctx);if(validation)body.append(validation);
    return body;
  }
  window.RiskView=Object.freeze({render,jobView,nodeFor,label,terminal});
})();
