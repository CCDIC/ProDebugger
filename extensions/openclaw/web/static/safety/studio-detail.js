'use strict';
// Read-only presentation of saved analysis. No checks, inference, requests or
// ground-truth-driven findings are performed here; the studio owns all actions.
(() => {
  const arr = value => Array.isArray(value) ? value : [];
  const raw = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '未提供';
  const el = (tag, cls, text, children = []) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = String(text);
    children.forEach(child => child && node.append(child));
    return node;
  };
  const button = (label, action, cls = 'studio-button') => {
    const node = el('button', cls, label);
    node.type = 'button';
    node.addEventListener('click', () => action?.());
    return node;
  };
  const paragraph = text => el('p', 'detail-summary', text);
  const originalText = text => el('p', 'detail-summary i18n-raw', text);
  const code = value => el('pre', 'detail-code', raw(value));
  const section = (title, children = [], eyebrow) => el('section', 'detail-section', null, [
    eyebrow && el('span', 'detail-eyebrow', eyebrow),
    title && el('h3', 'detail-title', title), ...children,
  ]);
  const disclosure = (title, children = [], open = false) => {
    const node = el('details', 'detail-disclosure');
    node.open = open;
    node.append(el('summary', '', title), el('div', 'detail-disclosure-body', null, children));
    return node;
  };
  const fields = items => {
    const list = el('dl', 'detail-kv');
    items.filter(([, value]) => value !== undefined && value !== null && value !== '')
      .forEach(([key, value]) => list.append(el('dt', '', key), el('dd', '', raw(value))));
    return list;
  };
  const badge = (text, cls = 'neutral') => el('span', 'analysis-chip ' + cls, text);
  const badges = children => el('div', 'analysis-chips', null, children);
  const actions = children => el('div', 'detail-actions', null, children);
  const parsed = value => {
    if (typeof value !== 'string') return value || {};
    try { return JSON.parse(value); } catch { return {}; }
  };
  const short = (value, limit = 180) => {
    const text = raw(value);
    return text.length > limit ? text.slice(0, limit) + '…' : text;
  };
  const basename = value => typeof value === 'string'
    ? value.replaceAll('\\', '/').split('/').filter(Boolean).pop() || value : '';
  const stateLabels = {
    queued: '等待运行', running: '正在分析', succeeded: '流程已完成', failed: '运行失败',
    cancelled: '已取消', timed_out: '已超时', interrupted: '已中断', not_run: '尚未诊断',
    no_failure: '当前证据未发现任务失败', diagnosed: '选出了错误候选', uncertain: '不确定，未能定位根因',
    safe: '当前安全策略通过', unsafe: '发现违规尝试', unknown: '证据不足', neutral: '结构 / 未评估',
    error: '规则或模型判断有错', risk: '存在安全风险', passed: '适用检查通过',
    confirmed: '有证据支持', suspected: '仅怀疑', observed: '已记录',
    satisfied: '通过', violated: '违反', violation_attempt: '违规尝试',
    not_applicable: '不适用', unsupported: '暂不支持', unset: '未声明工具状态',
    success: '当前任务契约满足', failure: '当前任务契约未满足', candidate: '候选',
  };
  const label = value => stateLabels[value] || value || '未提供';
  const categories = {rule: '规则问题', safety: '安全风险', runtime: '工具运行异常', model: '模型诊断', uncertainty: '证据不足'};
  const sources = {reused_abstract_rules: '复用原版规则', openclaw_extension: 'OpenClaw 任务规则', safety_audit: '基础安全策略审计', risk_rule: '自动风险行为规则', risk_model: '模型风险调查', model_TRACE: '模型 TRACE 调查', tool_runtime: '工具运行记录'};
  const relationNames = {tool_call: '工具调用', tool_result: '工具返回', tool_status: '工具状态', session_context: '会话上下文', admissibility: '适用性检查', satisfied: '任务检查'};
  const checkReasons = {
    unsupported_contract_or_no_tag: '缺少此规则支持的前提', missing_expected_effect_model: '缺少预期效果模型',
    missing_previous_action: '没有上一条调用可供比较', explicit_task_target_mismatch: '调用目标与明确任务不一致',
    explicit_task_target_matches: '调用目标与明确任务一致',
  };
  const terminal = new Set(['succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted']);
  const kindNames = {call: '工具调用', tool: '工具', trace: '轨迹', req: '任务要求', int: '行动意图', agent: '执行者', file:'文件对象', message:'公开消息'};
  const nodeFacts = (graph, node) => arr(graph?.record?.facts).filter(fact => arr(node?.fids).some(id => String(id) === String(fact.fid)));
  const issueList = (graph, node, chosen) => {
    const issues = arr(graph?.analysis?.issues).filter(issue => !node || arr(node.issue_ids).includes(issue.id) || arr(issue.node_ids).includes(node.id));
    if (chosen && !issues.some(issue => issue.id === chosen.id)) issues.unshift(chosen);
    return chosen ? [...issues.filter(issue => issue.id === chosen.id), ...issues.filter(issue => issue.id !== chosen.id)] : issues;
  };
  const callFacts = (graph, node) => {
    const facts = nodeFacts(graph, node);
    const callFact = facts.find(fact => fact.rel === 'tool_call');
    const resultFact = facts.find(fact => fact.rel === 'tool_result');
    return {facts, callFact, resultFact, call: parsed(callFact?.value), result: parsed(resultFact?.value)};
  };
  const outcomeText = outcome => {
    if (!outcome || !outcome.status) return '未提供可验证的任务契约';
    return label(outcome.status);
  };
  const matchingChecks = (graph, node) => !node ? arr(graph?.analysis?.checks) : arr(node.checks).length ? arr(node.checks)
    : arr(graph?.analysis?.checks).filter(check => arr(check.node_ids).includes(node.id) || arr(check.call_ids).includes(node.index) || check.call_id === node.index);

  function issueCard(issue, ctx) {
    const card = el('article', 'detail-issue ' + (issue.category || 'uncertainty'));
    card.dataset.issueId = issue.id;
    card.append(badges([badge(categories[issue.category] || issue.category, issue.category), badge(issue.display_label || label(issue.state))]),
      el('h4', '', issue.display_label || issue.title || '未命名发现'));
    const explanation = issue.explanation || issue.message || '报告尚未提供解释。';
    card.append(paragraph(explanation));
    if(issue.proposition)card.append(paragraph(issue.proposition));
    card.append(disclosure('原始分析记录', [originalText(explanation), issue.proposition && originalText(issue.proposition)].filter(Boolean)));
    card.append(el('p', 'detail-eyebrow', (sources[issue.source] || issue.source || '来源未标注') + (issue.rule_id ? ' · ' + issue.rule_id : '')));
    if (arr(issue.missing).length || issue.uncertainty) card.append(disclosure('判断的限制与缺失信息', [
      ...arr(issue.missing).map(value => paragraph(raw(value))), issue.uncertainty && paragraph(raw(issue.uncertainty)),
      ...arr(issue.limitations).map(value => paragraph(raw(value))),
    ]));
    const locator = arr(ctx.graph?.nodes).find(node => node.kind === 'call' && arr(issue.node_ids).includes(node.id)) || arr(ctx.graph?.nodes).find(node => arr(issue.node_ids).includes(node.id));
    card.append(actions([
      button('查看证据', () => {ctx.onLocate?.(ctx.graph?.case_id, locator?.id || ctx.node?.id, issue); ctx.onSetTab?.('evidence');}, 'studio-button subtle'),
      locator && (!ctx.node || ctx.node.id !== locator.id) && button(locator.kind==='call'?'定位调用':'定位事件', () => ctx.onLocate?.(ctx.graph.case_id, locator.id, issue), 'studio-button subtle'),
    ]));
    return card;
  }

  function checksPanel(checks) {
    if (!checks.length) return paragraph('没有可关联到当前实体的检查记录，不能据此判为通过。');
    return el('div', 'detail-check-list', null, checks.map(check => el('article', 'detail-check', null, [
      badges([badge(label(check.state), check.applied && ['safe', 'satisfied', 'passed'].includes(check.state) ? 'passed' : 'neutral')]),
      el('strong', '', check.rule_id || check.id || '检查'),
      paragraph((sources[check.source] || check.source || '检查来源') + ' · ' + (check.applied ? '本次适用并已执行' : '本次前提不足或不适用')),
      check.reason && paragraph(checkReasons[check.reason] || check.reason),
      disclosure('检查原始记录', [code(check)]),
    ])));
  }

  function overview(ctx) {
    const {graph, node} = ctx;
    if (!graph) return empty('先在左侧选择一条轨迹。', '选择轨迹后，这里会说明它做了什么、发现了什么。');
    const body = el('div');
    const {call, result, callFact, resultFact} = callFacts(graph, node);
    const target = call.arguments?.path ?? call.arguments?.url ?? call.arguments?.target;
    const summary = node?.kind === 'call' ? (call.tool === 'read' && target ? '读取 ' + basename(target) : (call.tool || '工具') + ' · 一次具体调用')
      : node ? (kindNames[node.kind] || node.kind) + '实体' : graph.title || '轨迹概况';
    const intro = section(summary, [
      node && badges([badge(node.assessment?.label || label(node.visual_state || 'neutral'), window.SessionView?.nodeAssessment?.(graph,node)?.visual || node.visual_state || 'neutral'), ...arr(node.badges).filter(value => !node.check_summary || !['uncertainty','passed','safety'].includes(value.category)).map(value => badge(value.label || categories[value.category], value.category))]),
    ], node ? '当前选中实体' : '当前轨迹');
    if (node?.kind === 'call') {
      intro.append(fields([['做了什么', call.tool ? '调用 ' + call.tool + ' 工具' : '调用信息不完整'], ['目标', target ? basename(target) : '未记录路径 / 目标'], ['导入调用步骤', callFact?.t]]));
      if (call.arguments && Object.keys(call.arguments).length) intro.append(disclosure('完整调用参数', [code(call.arguments)]));
      const resultBody = result.result;
      const returnedText = typeof resultBody === 'string' ? resultBody : arr(resultBody?.content).map(item => item.text).filter(Boolean).join('\n') || resultBody?.details?.content;
      intro.append(el('h4', '', '工具返回'));
      if (resultFact) {
        intro.append(returnedText != null ? el('pre', 'detail-code detail-result-preview', short(returnedText, 240)) : paragraph(resultBody == null ? '已记录返回事件，但返回正文缺失。' : '已记录结构化结果，展开查看正文。'));
        intro.append(disclosure('完整返回与工具状态', [fields([['工具状态', label(result.status)], ['导入返回步骤', resultFact.t]]), code(resultBody ?? '返回正文缺失')]));
      } else intro.append(paragraph('没有关联到工具返回，无法从当前记录确认执行结果。'));
    } else if (node) intro.append(paragraph(node.kind==='int'?'这是原工具解析器的意图槽位。值为 unknown 时只是占位，不能当成采集到了 Agent 记忆、计划或真实意图。':'这个节点表示编译后的结构实体。随事件变化的事实版本请看状态表；某一次调用的风险和错误请点击对应的 call 节点查看。'), fields([['实体', node.entity_id], ['关联事实', nodeFacts(graph, node).length]]));
    else intro.append(fields([['来源', graph.origin_label || graph.origin], ['工具调用', arr(graph.nodes).filter(value => value.kind === 'call').length], ['解析实体', arr(graph.nodes).length]]));
    body.append(intro);
    const results = section('先分清两件事', [
      el('div', 'comparison-pair', null, [
        el('div', 'comparison-cell', null, [el('span', 'detail-eyebrow', '任务要求'), el('strong', '', outcomeText(graph.analysis?.task_outcome)), paragraph('依据已声明的任务契约；工具成功不等于任务完成。')]),
        el('div', 'comparison-cell', null, [el('span', 'detail-eyebrow', '安全策略'), el('strong', '', label(graph.report?.status)), paragraph('只检查当前策略覆盖的范围，不代表整体绝对安全。')]),
      ]),
      disclosure('记录的用户任务与任务检查', [originalText(raw(graph.record?.task || '未记录任务')), code(graph.analysis?.task_outcome || {})]),
    ]);
    body.append(results);
    const issues = issueList(graph, node, ctx.issue);
    body.append(section(node ? '这个节点有哪些发现' : '这条轨迹有哪些发现', issues.length ? issues.map(issue => issueCard(issue, ctx)) : [paragraph('当前没有关联的发现。是否已评估、哪些规则适用，请查看检查记录。')]));
    body.append(disclosure('检查记录与适用范围 · ' + matchingChecks(graph, node).length, [checksPanel(matchingChecks(graph, node)), disclosure('原版规则适用性', [code(graph.analysis?.rule_applicability || [])])]));
    return body;
  }

  function evidenceEntry(value, ordinal) {
    const item = typeof value === 'object' && value !== null ? value : {text: value};
    return el('article', 'detail-evidence', null, [
      el('strong', '', '证据 ' + ordinal + (item.fid != null ? ' · F' + item.fid : '')),
      item.text != null ? code(item.text) : code(item),
      fields([['来源', item.source_kind || item.source], ['导入步骤', item.step], ['记录字段', item.module], ['原文定位', item.source_ref || item.fragment_id || item.source_id], ['参数位置', item.json_pointer]]),
      disclosure('完整引用信息', [code(item)]),
    ]);
  }

  function evidence(ctx) {
    const {graph, node} = ctx;
    if (!graph) return empty('尚未选择轨迹', '选择轨迹，再点击调用或问题，查看它的证据。');
    const issues = issueList(graph, node, ctx.issue);
    const body = el('div');
    const facts = node ? nodeFacts(graph, node) : arr(graph.record?.facts);
    const {call, callFact, resultFact} = callFacts(graph, node);
    const locations = issues.flatMap(issue => arr(issue.original_locations));
    const native = locations.find(value => value.original_native_call_sequence != null || value.native_call_sequence != null);
    body.append(section('对应哪一次调用', [fields([
      ['轨迹', graph.title || graph.case_id], ['调用实体', node?.entity_id], ['Trace ID', call.trace_id || issues[0]?.trace_id],
      ['Span ID', call.span_id || issues[0]?.span_id], ['原生 Tool Call ID', call.provenance?.tool_call_id],
      ['导入调用步骤', callFact?.t ?? node?.action_steps], ['导入返回步骤', resultFact?.t],
      ['原生调用事件序号', native?.original_native_call_sequence ?? native?.native_call_sequence ?? '当前分析映射未提供'],
    ]), paragraph('导入步骤是转换后的调用 / 返回编号，不等于原始 Agent 的执行步骤。原文只展示实际保存的内容。'),
      disclosure('完整来源映射', [code(locations), code(call.provenance || {})]),
    ]));
    if (issues.length) issues.forEach(issue => {
      const part = section(issue.title || categories[issue.category], [
        badges([badge(categories[issue.category] || issue.category, issue.category), badge(sources[issue.source] || issue.source), badge(label(issue.state))]),
        fields([['规则 / 策略', issue.rule_id || issue.policy_id], ['关联事实', arr(issue.fact_ids).map(id => 'F' + id).join('、')]]),
        disclosure('结论与解释', [paragraph(issue.explanation || '未提供'), issue.proposition && paragraph(issue.proposition)]),
      ]);
      part.append(disclosure('支持证据 · ' + arr(issue.evidence).length, arr(issue.evidence).length ? arr(issue.evidence).map((value, index) => evidenceEntry(value, index + 1)) : [paragraph('没有保存支持证据。')], issue === ctx.issue));
      part.append(disclosure('反证 · ' + arr(issue.counterevidence).length, arr(issue.counterevidence).length ? arr(issue.counterevidence).map((value, index) => evidenceEntry(value, index + 1)) : [paragraph('没有记录反证；这不等于已排除其他解释。')]));
      part.append(disclosure('缺失信息与尚未验证', [
        ...arr(issue.missing).map(value => paragraph(raw(value))), issue.uncertainty && paragraph(raw(issue.uncertainty)),
        ...arr(issue.limitations).map(value => paragraph(raw(value))),
        !arr(issue.missing).length && !issue.uncertainty && paragraph('报告未列出额外缺失项，但没有证明唯一根因或未记录的内部思考。'),
      ]));
      body.append(part);
    });
    const factDetails = facts.map(fact => disclosure('F' + fact.fid + ' · ' + (relationNames[fact.rel] || fact.rel) + ' · 导入步骤 ' + fact.t, [
      fields([['关系', fact.rel], ['字段', fact.slot], ['引用其他事实', arr(fact.basis).map(id => 'F' + id).join('、') || '无记录']]),
      code(parsed(fact.value)?.type ? parsed(fact.value) : fact.value),
      disclosure('对应原文', [code(fact.span)]), disclosure('完整事实字段', [code(fact)]),
    ]));
    body.append(section('解析后的事实', [paragraph(facts.length + ' 条关联事实；引用现有编译结果，没有另造因果关系。'), ...factDetails]));
    const steps = [...new Set(facts.map(fact => fact.t).filter(step => step != null && step !== 0))].sort((a, b) => a - b);
    body.append(disclosure('原始导入记录 · ' + steps.length + ' 步', steps.map(step => disclosure('导入步骤 ' + step, [code(graph.record?.source_steps?.[String(step)] ?? '未提供原记录')]))));
    body.append(disclosure('解析审计与证据范围', [code(graph.record?.parse_audits || []), code(graph.analysis?.notes || []), code(graph.report?.coverage || {})]));
    return body;
  }

  function diagnosis(ctx) {
    const {graph} = ctx;
    if (!graph) return empty('先选择一条轨迹', '完整诊断会依据任务、调用和证据逐步分析。');
    const job = ctx.job || graph.analysis?.diagnosis || {};
    const result = job.result;
    const active = !!job.id && !terminal.has(job.status);
    const body = el('div');
    const start = button(active ? '本条诊断进行中' : job.id ? '重新诊断本条' : '完整诊断本条', () => ctx.onDiagnose?.([graph.case_id]), 'studio-button primary');
    start.disabled = active;
    body.append(section(result ? result.status === 'error' ? '诊断执行错误，尚无可靠结论' : label(result.status) : label(job.status || 'not_run'), [
      paragraph(result?.status === 'diagnosed' ? '模型选中的候选是需要核对的解释，不能当作已证明的唯一根因。'
        : result?.status === 'no_failure' ? '在已提供的任务契约和证据范围内，模型没有找到支持任务失败的错误。'
          : result?.status === 'uncertain' ? '当前证据或调查结果不足，系统保留不确定结论。'
            : '运行后会补充错误位置、原因和证据。自动规则及安全审计结果始终保留。'),
      actions([start, active && button('取消本条诊断', () => ctx.onCancel?.(job.id), 'studio-button subtle')]),
    ], '模型分析'));
    const phases = el('div', 'stage-strip');
    const completed = arr(job.completed_phases);
    [['MAP', '整理证据'], ['ROUTE', '选择线索'], ['TRACE', '调查步骤'], ['SUBMIT', '形成结论']].forEach(([phase, title]) => {
      const done = completed.includes(phase), current = active && job.phase === phase;
      const chip = el('span', 'stage-chip' + (done ? ' done' : current ? ' current' : ''), (done ? '✓ ' : current ? '◌ ' : '') + title);
      chip.title = phase + ' · ' + (done ? '已记录完成' : current ? '正在进行' : '尚未记录完成');
      phases.append(chip);
    });
    body.append(section('完整诊断进度', [phases, paragraph(job.id ? label(job.status) + (job.phase ? ' · 当前阶段 ' + job.phase : '') : '点击上方按钮才会调用模型。'),
      job.queue_position && paragraph('队列位置：' + job.queue_position),
      job.error && disclosure('本次运行失败原因', [code(job.error)], true),
    ]));
    if (result) {
      const modelIssues = arr(graph.analysis?.issues).filter(issue => issue.category === 'model');
      if (modelIssues.length) body.append(section('模型定位的步骤', modelIssues.map(issue => issueCard(issue, ctx))));
      body.append(disclosure('模型提交的原始原因与不确定性', [
        paragraph(result.reasoning || '未提供原因说明'), result.uncertainty && paragraph(result.uncertainty),
        result.repair && disclosure('模型修复建议', [code(result.repair)]),
        result.root && disclosure('最终选中的候选', [code(result.root)]),
      ]));
    }
    const incompleteQueries = arr(job.queries).filter(query => query.partial || query.error || arr(query.function_errors).length || arr(query.skipped_branches).length);
    if (incompleteQueries.length) body.append(paragraph('有 ' + incompleteQueries.length + ' 次查询存在错误或不可用分支，相关范围仍有限制。完整流程结束不代表每次检索都成功。'));
    if (job.id || result) body.append(disclosure('完整诊断记录：阶段、查询与原始结果', [window.AnalysisUI?.renderDiagnosis ? window.AnalysisUI.renderDiagnosis(job) : code(job)]));
    const history = arr(ctx.jobs).filter(value => value.case_id === graph.case_id && value.id !== job.id).sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    if (history.length) body.append(disclosure('历史运行 · ' + history.length, history.map(value => disclosure(label(value.status) + ' · ' + (value.created_at || value.id), [
      value.error && code(value.error), window.AnalysisUI?.renderDiagnosis ? window.AnalysisUI.renderDiagnosis(value) : code(value),
    ]))));
    const config = job.configuration || ctx.configuration;
    if (config) body.append(disclosure('模型与运行预算', [fields([
      ['模型', config.model], ['凭据', config.credential_available === true ? '已有配置可用（不展示密钥）' : config.credential_available === false ? '当前不可用' : '由已有配置提供'],
      ['总时限', config.wall_timeout_seconds ? config.wall_timeout_seconds + ' 秒' : undefined],
      ['最多模型轮数', config.budgets?.max_turns], ['最多工具查询', config.budgets?.max_tool_calls],
    ])]));
    return body;
  }

  function comparisonLocation(title, location, actual = false) {
    if (actual) {
      const values = arr(location);
      const calls = [...new Set(values.flatMap(value => arr(value.call_ids)))];
      const spans = [...new Set(values.flatMap(value => value.span_id ? [value.span_id] : arr(value.call_ids).map(id => String(id).split('/').pop())))];
      return el('div', 'comparison-cell', null, [el('span', 'detail-eyebrow', title), fields([
        ['调用 / Span', spans.length ? spans.join('\n') : '未定位到具体调用'],
        ['导入调用步骤', [...new Set(values.flatMap(value => arr(value.import_steps)))].join('、') || '未提供'],
      ]), calls.length > 0 && disclosure('完整调用 ID', [code(calls)])]);
    }
    return el('div', 'comparison-cell', null, [el('span', 'detail-eyebrow', title), fields([
      ['调用 / Span', location?.span_id || '对照样本，无注入故障位置'], ['导入调用步骤', location?.import_action_step],
      ['原生调用事件序号', location?.original_native_call_sequence],
    ]), location?.call_id && disclosure('完整位置与原始记录', [code(location)])]);
  }

  function validation(ctx) {
    const {graph, suite} = ctx;
    const selected = arr(suite?.cases).find(value => value.case_id === graph?.case_id);
    if (!suite || !selected) return section('用已知修改验证定位', [
      paragraph('这里把基础轨迹与独立构造的错误变体放在同一工作台对照。先分析，再查看预先固定的答案。'),
      actions([button('载入定位验证样本', () => ctx.onLoadValidation?.(), 'studio-button primary')]),
    ]);
    const body = el('div');
    body.append(section(selected.label || '当前验证样本', [
      badges([badge(selected.is_constructed ? '构造数据 · 未实际执行' : '真实 Agent 受控执行', selected.is_constructed ? 'safety' : 'neutral')]),
      paragraph(selected.is_constructed ? '基于已核验的 OpenClaw 轨迹修改独立副本，用于测试系统能否找到已知故障。' : suite.source?.note || '基础轨迹的来源见下方采集记录。'),
      disclosure('来源与采集核验', [fields([['案例 ID', selected.case_id], ['来源类别', selected.source_category]]), code(suite.source || {})]),
      actions([
        button('查看本条模型诊断', () => ctx.onSetTab?.('diagnosis'), 'studio-button subtle'),
        button('全组完整诊断', () => ctx.onDiagnose?.(arr(suite.cases).map(value => value.case_id)), 'studio-button subtle'),
      ]),
    ], '定位能力验证'));
    // The parent explicitly fetches references only after the server opens the
    // gate. Never use reference content to populate graph findings or diagnosis.
    const reference = suite.reference_available === true ? ctx.reference : null;
    if (!reference) {
      const reveal = button(suite.reference_available ? '展开答案与修改差异' : '全组分析结束后开放答案', () => ctx.onReveal?.(suite.id), 'studio-button primary');
      reveal.disabled = suite.reference_available !== true;
      body.append(section('先分析，再核对答案', [
        paragraph(suite.reference_available ? '本组分析已结束。点击后，独立查看标准答案与系统实际定位的对照。' : '等待全组在相同配置下结束诊断。标准答案与修改位置不会参与模型分析。'),
        actions([reveal]),
      ]));
    } else {
      const comparison = arr(reference.comparison?.cases || reference.comparison?.results || reference.comparison?.comparisons).find(value => value.case_id === selected.case_id);
      const truth = arr(reference.ground_truth?.cases).find(value => value.case_id === selected.case_id || value.id === selected.id);
      const diff = reference.diff?.[selected.id] || reference.diff?.[selected.case_id] || {};
      body.append(section('系统是否定位正确', [
        badges([badge('独立评测 · 不是模型输出'), badge(comparison?.outcome || '尚未提供对照结果')]),
        comparison?.explanation && paragraph(comparison.explanation),
        el('div', 'comparison-pair', null, [comparisonLocation(selected.is_constructed ? '预先固定的注入位置' : '基础对照 · 没有注入故障', comparison?.expected_location || (selected.is_constructed ? truth?.injected_location : null)), comparisonLocation('模型实际定位', comparison?.actual_locations, true)]),
        fields([
          ['发现注入问题', comparison?.detection === true ? '是' : comparison?.detection === false ? '否' : '此项不适用 / 尚未验证'],
          ['定位到同一调用', comparison?.exact_call_match === true ? '是' : comparison?.exact_call_match === false ? '否' : '此项不适用 / 尚未验证'],
          ['问题类型正确', comparison?.issue_type_match === true ? '是' : comparison?.issue_type_match === false ? '否' : '此项不适用 / 尚未验证'],
          ['引用相关证据', comparison?.evidence_cited === true ? '是' : comparison?.evidence_cited === false ? '否' : '此项不适用 / 尚未验证'],
        ]),
      ]));
      const changes = arr(diff.semantic_changes).length ? arr(diff.semantic_changes) : arr(truth?.semantic_changes);
      body.append(section('这个样本修改了什么', changes.length ? changes.map(change => el('article', 'detail-change', null, [
        el('strong', '', change.field || '修改字段'),
        el('div', 'comparison-pair', null, [
          el('div', 'comparison-cell', null, [el('span', 'detail-eyebrow', '修改前'), code(change.before)]),
          el('div', 'comparison-cell', null, [el('span', 'detail-eyebrow', '修改后'), code(change.after)]),
        ]),
      ])) : [paragraph('基础对照样本没有注入目标替换故障；仍按真实任务和证据检查。')]));
      body.append(disclosure('配套返回与字段修改 · ' + arr(diff.derived_changes).length, arr(diff.derived_changes).map(change => disclosure(change.field || '派生修改', [code(change)]))));
      body.append(disclosure('本条预先固定的标准答案', [code(truth || '未提供')]), disclosure('本条完整修改差异', [code(diff)]), disclosure('本条独立评测记录', [code(comparison || {})]));
      const totals = reference.comparison?.summary;
      if (totals) body.append(paragraph('本组变体精确命中 ' + (totals.precise_hits ?? '未统计') + ' / ' + (totals.variant_count ?? '未统计') + ' 条，仅作为开发验收结果。'));
    }
    body.append(disclosure('验证范围与限制', [
      ...arr(suite.limitations).map(value => paragraph(raw(value))),
      paragraph('已知故障位置不等于已知 Agent 的内部思考或唯一根因。这组开发验收样本不能证明对新任务的整体准确率。'),
    ]));
    return body;
  }

  function empty(title, text) { return el('div', 'detail-empty', null, [el('h3', 'detail-title', title), paragraph(text)]); }

  function render(ctx = {}) {
    const root = el('div', 'studio-detail');
    root.dataset.detailTab = ctx.tab || 'overview';
    const builders = {overview, evidence, diagnosis, validation};
    root.append((builders[ctx.tab] || overview)(ctx));
    return root;
  }

  window.StudioDetails = Object.freeze({render});
})();
