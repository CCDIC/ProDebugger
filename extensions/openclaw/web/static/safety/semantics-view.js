'use strict';
// Displays persisted parsing evidence. This view never invents a parse result.
(() => {
  const {el, button, arr, raw, details, code, fields} = AnalysisUI.helpers;
  const names = {deferred:'待模型解析',pending:'待模型解析',queued:'排队中',running:'解析中',completed:'本轮提取完成',partial:'部分完成',failed:'解析未完成',template:'模板解析',model:'模型解析',reviewed:'已完成本片段提取',model_interpretation:'模型语义解释',unresolved:'尚未解析',unmodeled:'语义未建模',not_evaluated:'未评估'};
  const kinds = {constraint:'任务／权限约束',statement:'已记录的陈述',external_instruction:'外部内容中的指令',behavior_interpretation:'行为含义解释',authorization:'授权陈述',task_constraint:'任务约束'};
  const roles = {user:'用户',assistant:'Agent',tool:'工具返回',system:'系统',external:'外部内容'};
  const authorities = {user_task:'用户任务要求',recorded_user_input:'记录中的用户输入',agent_statement:'Agent 自身陈述',recorded_message:'已记录消息',recorded_content_blocks:'已记录内容块',external_observation:'外部返回内容',recorded_action:'已记录操作'};
  const p = text => el('p','semantic-note',text);
  const badge = (text,kind='neutral') => el('span','pill '+kind,text);
  const list = value => Array.isArray(value)?value:value&&typeof value==='object'?Object.entries(value).map(([id,item])=>typeof item==='object'?{id,...item}:{id,meaning:item}):[];
  const label = value => names[value]||value||'尚未记录';
  const reportLabel = report => report?.status==='partial'&&report?.coverage?.all_units_reviewed?'已提取 · 含未确定内容':label(report?.status);
  const reportOf = ctx => ctx.semantic?.report||ctx.graph?.openclaw_semantics||ctx.graph?.semantics?.report||ctx.graph?.record?.openclaw_semantics||null;
  function calls(report){const usage=report?.usage||{};return usage.by_phase?.PARSE?.model_calls??usage.model_calls??null;}
  function policySummary(policy){
    if(!policy)return '策略未配置';
    const parts=[];
    if(policy.read_scope)parts.push('读取范围已配置');
    if(arr(policy.sensitive_sources).length)parts.push(arr(policy.sensitive_sources).length+' 类敏感来源');
    if(arr(policy.approvals).length)parts.push(arr(policy.approvals).length+' 条授权记录');
    if(arr(policy.protected_config).length||arr(policy.protected_fields).length||policy.config)parts.push('保护配置已配置');
    return parts.join(' · ')||'策略已配置';
  }
  function summary(ctx){
    const box=el('section','semantic-workflow');box.setAttribute('aria-label','解析与审查');
    if(!ctx.graph)return box;
    const report=reportOf(ctx),job=ctx.job,active=job&&!RiskView.terminal.has(job.status),parseStage=active&&(job.stage||job.phase)==='PARSE';
    const row=el('div','semantic-flow');
    row.append(el('span','semantic-step recorded',null,[el('b','','1'),el('span','','结构已导入')]));
    row.append(el('span','semantic-arrow','→'));
    row.append(button('', 'semantic-step'+(parseStage?' active':''),ctx.onDetails));
    row.lastChild.append(el('b','','2'),el('span','','语义解析 · '+(parseStage?'进行中':report?reportLabel(report):'待解析')));
    row.append(el('span','semantic-arrow','→'));
    row.append(button('', 'semantic-step'+(active&&!parseStage?' active':''),ctx.onRisk));
    row.lastChild.append(el('b','','3'),el('span','','风险审查 · '+(job?RiskView.label(job.status):'尚未运行')));
    row.append(button('解析详情','semantic-link',ctx.onDetails));
    box.append(row);
    const reportCoverage=report?.coverage||{},foot=el('div','semantic-workflow-foot',null,[button('审查策略','semantic-link',ctx.onPolicy)]);
    if(reportCoverage.total_units!=null)foot.prepend(el('span','semantic-policy-summary',`语义片段 ${reportCoverage.reviewed_units??0} / ${reportCoverage.total_units}`));
    if(active&&ctx.semantic?.job_id&&ctx.semantic.job_id!==job.id)foot.append(badge('审查中 · 显示上次结果'));
    box.append(foot);return box;
  }
  function sourceFields(item){return fields([
    ['来源角色',roles[item.role]||item.role],['来源身份',authorities[item.authority]||item.authority],['来源编号',item.source_id],
    ['导入步骤',item.step??item.import_step],['内容部分',item.module],['原始字段',typeof item.source_pointer==='object'?raw(item.source_pointer):item.source_pointer||item.json_pointer],
    ['原文字符范围',item.start!=null?`${item.start}–${item.end??'未提供'}`:undefined],['语气 / 确定性',item.modality],
  ]);}
  function itemView(item,ctx){
    if(typeof item!=='object'||!item)return p(String(item));
    const box=el('article','semantic-item');
    box.append(el('div','analysis-chips',null,[badge(kinds[item.kind]||item.kind||'语义提取'),badge(item.source==='template'?'模板来源':item.status==='model_interpretation'?'模型提取 · 待对照事实':'来源见引用')]));
    box.append(el('h4','',item.meaning||item.description||item.text||item.title||item.id||'提取结果'));
    if(item.quote)box.append(el('span','detail-eyebrow','证据原文'),el('blockquote','semantic-quote',item.quote));
    const source=details('原文与来源',[sourceFields(item),...(item.role==='assistant'?[p('Agent 陈述，未作为已验证事实。')]:[]),...(item.kind==='external_instruction'?[p('外部指令，执行情况需另行核对。')]:[]),code(item)]);box.append(source);
    const locate=button('定位到记录','semantic-link',()=>ctx.onLocate?.(item));locate.disabled=!ctx.onLocate||item.step==null;box.append(locate);return box;
  }
  function detail(ctx){
    const box=el('div','semantic-detail'),report=reportOf(ctx);
    box.append(el('h3','','语义解析'));
    if(!ctx.graph){box.append(p('先从左侧选择一条轨迹。'));return box;}
    const version=ctx.semantic?.job_id||ctx.semantic?.version||ctx.semantic?.analysis_version||ctx.graph?.analysis_version;
    box.append(fields([['案例',ctx.graph.case_id],['当前解析版本',version||'尚无已保存模型解析'],['版本来源',ctx.semantic?.version_kind==='model_compiled'?'本次模型解析产物':ctx.semantic?.version_kind==='structural_offline'?'已导入结构 · 待模型提取':undefined],['当前作业阶段',ctx.semantic?.phase||ctx.job?.stage||ctx.job?.phase]]));
    if(ctx.semantic?.import_audit&&Object.keys(ctx.semantic.import_audit).length)box.append(details('导入覆盖',[code(ctx.semantic.import_audit)]));
    if(ctx.semantic?.error)box.append(p('解析报告读取失败：'+ctx.semantic.error));
    if(!report){box.append(p('尚无语义报告。运行完整风险审查后查看。'));return box;}
    box.append(el('div','analysis-chips',null,[badge(reportLabel(report),['failed','partial'].includes(report.status)?'unknown':'neutral'),badge(calls(report)==null?'模型解析调用数未记录':`真实解析模型请求 ${calls(report)} 次`)]));
    const c=report.coverage||{};
    box.append(fields([['待审语义片段',c.total_units],['已审片段',c.reviewed_units],['尚未审查片段',c.unreviewed_units],['覆盖导入步骤',arr(c.reviewed_steps).join('、')||undefined],['未审查步骤',arr(c.unreviewed_steps).join('、')||undefined]]));
    const constraints=list(report.constraints),statements=list(report.statements),units=list(report.units);
    for(const [title,items]of [['任务与权限约束',constraints],['已记录陈述及行为解释',statements]]){
      const section=el('section','semantic-section',null,[el('h3','',`${title} · ${items.length}`)]);
      section.append(...(items.length?items.map(item=>itemView(item,ctx)):[p('无提取结果')]));box.append(section);
    }
    const gaps=list(report.unresolved);
    box.append(details(`尚未解析或无法确认 · ${gaps.length}`,gaps.length?gaps.map(item=>typeof item==='object'?el('article','semantic-item',null,[p(item.reason||item.meaning||item.message||raw(item)),sourceFields(item),details('完整缺口记录',[code(item)])]):p(item)):[p('未记录解析缺口')]));
    box.append(details(`逐片段核对 · ${units.length}`,units.map(unit=>details(`${unit.step==null?'任务':'导入 '+unit.step} · ${roles[unit.role]||unit.role||unit.module||''} · ${label(unit.status)}`,[sourceFields(unit),...list(unit.items).map(item=>itemView(item,ctx)),...list(unit.unresolved).map(item=>p(typeof item==='string'?item:item.reason||raw(item))),details('保存的片段记录',[code(unit)])]))));
    box.append(details('完整解析报告与预算记录',[code(report)]));return box;
  }
  function policyDetail(policy){
    if(!policy)return p('策略未配置');
    const box=el('div','semantic-policy-detail');box.append(p(policySummary(policy)));
    if(policy.read_scope)box.append(fields([['允许读取',arr(policy.read_scope.allowed_roots).join('、')||'未指定'],['禁止读取',arr(policy.read_scope.forbidden_paths).join('、')||'未指定']]));
    for(const value of arr(policy.sensitive_sources))box.append(details('敏感来源：'+(value.id||value.label||'未命名'),[code(value)]));
    return box;
  }
  window.SemanticsView=Object.freeze({summary,detail,policyDetail,policySummary});
})();
