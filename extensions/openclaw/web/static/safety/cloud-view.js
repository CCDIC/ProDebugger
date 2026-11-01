'use strict';
// References are fetched only when the user explicitly reveals them.
(() => {
  const {el,button,arr,raw,details,code,fields}=AnalysisUI.helpers;
  const p=text=>el('p','semantic-note',text);
  const outcome={hit:'精确命中',exact_hit:'精确命中',matched:'匹配',missed:'未发现',miss:'未发现',mislocalized:'位置不同',wrong_location:'位置不同',uncertain:'尚不确定',partial:'流程部分完成',unknown:'证据不足',not_evaluated:'尚未评测',no_confirmed_false_positive:'已知范围无确定误报',passed:'本项验收通过'};
  const riskTypes={forbidden_path:'访问策略明确禁止的路径',read_scope:'超出任务授权范围读取',sensitive_context_exposure:'受限明文进入上下文',sensitive_output_disclosure:'报告包含受限信息',sensitive_data_transfer:'受限信息外发',unapproved_side_effect:'未经批准的外部操作',untrusted_instruction:'不可信内容中的指令',protected_config_change:'保护配置被修改'};
  const text=value=>outcome[value]||RiskView.label(value);
  function comparison(title,result){
    const box=el('section','semantic-section');box.append(el('h3','',title));
    if(!result){box.append(p('尚无独立对照结果。'));return box;}
    if(result.status||result.outcome)box.append(p(text(result.outcome||result.status)));
    const rows=arr(result.results||result.checks);
    if(!rows.length)box.append(p('无预设故障位置'));
    for(const row of rows){
      const card=el('article','semantic-item');card.append(el('h4','',riskTypes[row.risk_type]||row.risk_type||row.type||'风险检查'),el('span','pill '+(['hit','exact_hit','matched','passed'].includes(row.outcome)?'passed':['uncertain','unknown'].includes(row.outcome)?'unknown':'neutral'),text(row.outcome||row.status)));
      card.append(fields([['预期调用 / Span',row.expected_span_id||row.expected_call_id],['预期消息事件',row.expected_event_id],['信息类别',row.privacy_category],['预期导入步骤',row.expected_step],['判定理由',row.reason||row.explanation]]));
      for(const actual of arr(row.actual)){
        card.append(fields([['实际调用',actual.call_id||arr(actual.call_ids).join('、')],['实际消息事件',actual.event_id||actual.actual_event_id],['实际 Span',actual.span_id||arr(actual.span_ids).join('、')],['实际导入步骤',actual.import_step??actual.step??arr(actual.import_steps).join('、')],['实际状态',RiskView.label(actual.status||actual.state)],['证据引用数',Array.isArray(actual.evidence)?actual.evidence.length:undefined]]));
        if(actual.explanation)card.append(p(actual.explanation));
        if(arr(actual.evidence).length)card.append(details('本项判断引用的证据',actual.evidence.map(item=>el('article','risk-evidence',null,[fields([['导入步骤',item.step],['来源编号',item.source_id],['引用编号',item.reference_id],['原始字段',item.json_pointer]]),code(item.quote??item.text??item)]))));
      }
      card.append(details('本项独立比较记录',[code(row)]));box.append(card);
    }
    box.append(details('完整评测结果',[code(result)]));return box;
  }
  function render(ctx){
    const box=el('div','cloud-evaluation');box.append(el('h3','',ctx.displayTitle||'数据来源'));if(ctx.caseInfo)box.append(fields([['来源',ctx.caseInfo.constructed?'构造副本 · 未实际执行':'SigNoz 云端记录'],['Trace ID',ctx.caseInfo.trace_id]]),details('来源记录',[code(ctx.caseInfo)]));
    if(ctx.loading){box.append(p('正在读取…'));return box;}
    if(ctx.error){box.append(p('读取失败：'+ctx.error),button('重新读取','studio-button',ctx.onReload));return box;}
    const value=ctx.evaluation;if(!value){box.append(button('读取结果','studio-button primary',ctx.onReload));return box;}
    box.append(fields([['案例',value.case_id],['模型作业状态',RiskView.label(value.job_status)],['完整流程',value.full_flow_completed===true?'已完成':value.full_flow_completed===false?'未完成':'尚无记录']]));
    if(!value.ready){box.append(p('审查尚未完成'),button('刷新审查状态','studio-button',ctx.onReload));return box;}
    box.append(details('结果比较',[comparison('自动规则与安全审计',value.automatic),comparison('完整模型风险审查',value.model)]));
    if(value.full_flow_completed===false)box.append(p('以下模型输出属于未完成流程中的已保存结果，不能当作完整审查成功。'));
    if(value.limits)box.append(details('评测范围',[code(value.limits)]));
    if(ctx.reference){
      const ref=el('section','cloud-reference semantic-section');ref.append(el('h3','','参考答案与修改记录'),details('标准答案',[code(ctx.reference.ground_truth)]),details('修改前后差异',[code(ctx.reference.diff)]),details('固定时间与完整性记录',[code(ctx.reference.freeze)]));box.append(ref);
    }else{
      const reveal=button(ctx.referenceLoading?'正在读取…':'查看参考答案与差异','studio-button',ctx.onReveal);reveal.id='cloud-reference-open';reveal.disabled=!!ctx.referenceLoading;box.append(reveal);
      if(ctx.referenceError)box.append(p('答案尚未展开：'+ctx.referenceError));
    }
    box.append(button('刷新结果','semantic-link',ctx.onReload));return box;
  }
  window.CloudView=Object.freeze({render});
})();
