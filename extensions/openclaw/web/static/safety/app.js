'use strict';

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const all = (selector, root = document) => [...root.querySelectorAll(selector)];
  const MAX_IMPORT_BYTES = 4 * 1024 * 1024;
  const labels = { safe: '本策略通过', unsafe: '发现违规尝试', unknown: '证据不足' };
  const effectLabels = { blocked: '工具执行层已拦截', completed: '工具报告完成', unknown: '执行效果未知', attempted: '调用尝试' };
  const kindLabels = { read: '读取文件', write: '写入文件', network: '网络请求', command: '执行命令', unsupported: '未支持的工具' };
  const gapLabels = { capture_not_declared_complete: '采集没有声明完整', capture_content_incomplete: '采集正文不完整', importer_reported_capture_gaps: '导入阶段发现采集缺口', invalid_observation: '工具返回格式无法解析', unsupported_action_envelope: '工具调用格式暂不支持', duplicate_call_identity: '工具调用标识重复', tool_name_query_unavailable: '无法查询到工具名的原文证据', missing_or_ambiguous_matching_response: '缺少唯一匹配的工具返回', missing_tool_result_body: '工具返回正文缺失', unsupported_tool: '策略没有定义这个工具的含义', missing_or_unsupported_arguments: '参数缺失或格式暂不支持', target_truncated_or_redacted: '调用目标被截断或脱敏', target_query_unavailable_or_clipped: '无法取得完整的调用目标证据', unsupported_target_semantics: '调用目标的语义暂不支持', no_auditable_tool_actions: '没有可以审计的工具调用' };
  const app = { data: { cases: [], policy: {} }, selected: '', filter: 'all', search: '', origin: 'all', job: null, polling: null, busy: false };
  let toastTimer;

  function node(tag, className, text, children = []) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = String(text);
    for (const child of children) if (child) el.append(child);
    return el;
  }
  function button(text, className, handler) {
    const b = node('button', className, text);
    b.type = 'button';
    b.addEventListener('click', handler);
    return b;
  }
  function value(value, fallback = '未提供') {
    if (value === undefined || value === null || value === '') return fallback;
    return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  }
  function list(value) { return Array.isArray(value) ? value : []; }
  function gapText(gap) {
    if (typeof gap !== 'string') return gap?.message || gap?.reason || JSON.stringify(gap);
    const match = gap.match(/^step_(\d+):(.+)$/);
    return match ? `第 ${match[1]} 步：${gapLabels[match[2]] || match[2]}` : gapLabels[gap] || gap;
  }
  function reportOf(c) { return c?.report || {}; }
  function statusOf(c) { const s = reportOf(c).status || c?.status; return labels[s] ? s : 'unknown'; }
  function currentCase() { return app.data.cases.find(c => c.id === app.selected); }
  function titleOf(c) { return c?.title || c?.id || '未命名案例'; }
  function policyOf(c) { return c?.policy || app.data.policy || {}; }
  function originGroup(c) {
    const o = String(c.origin || c.source_type || '').toLowerCase();
    if (o.includes('openclaw') || o.includes('historical')) return 'openclaw';
    if (o.includes('controlled') || o.includes('demo') || o.includes('runtime')) return 'controlled';
    return 'imported';
  }
  function originLabel(c) {
    return c.origin_label || ({ openclaw: '历史真实 OpenClaw 任务', controlled: '本地受控工具执行 · 无 LLM', imported: '自行导入的轨迹' })[originGroup(c)];
  }
  function badge(status, text) { return node('span', `badge ${status}`, text || labels[status] || status); }
  function toast(message) {
    const el = $('#toast');
    el.textContent = message;
    el.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('visible'), 4200);
  }
  function setConnection(ok) {
    $('#connection-dot').className = `status-dot ${ok ? 'online' : 'offline'}`;
    $('#connection-state').textContent = ok ? '已连接本地审计服务' : '本地服务未连接';
  }
  function errorMessage(error) { return error?.message || String(error); }
  async function api(path, options = {}) {
    const response = await fetch(path, { cache: 'no-store', ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
    const text = await response.text();
    let body;
    try { body = JSON.parse(text); } catch { throw new Error(`服务未返回 JSON（HTTP ${response.status}）。请从本地审计服务的网址打开本页。`); }
    if (!response.ok || body.error) {
      const detail = typeof body.error === 'object' ? body.error.message || JSON.stringify(body.error) : body.error;
      throw new Error(detail || body.message || `请求失败（HTTP ${response.status}）`);
    }
    return body;
  }
  function post(path, data) { return api(path, { method: 'POST', body: JSON.stringify(data) }); }
  function updateCase(c, report, id) {
    const caseId = c?.id || id;
    const old = app.data.cases.find(x => x.id === caseId);
    const updated = { ...old, ...c, id: caseId, report: report || c?.report || old?.report };
    const index = app.data.cases.findIndex(x => x.id === caseId);
    if (index >= 0) app.data.cases[index] = updated;
    else app.data.cases.unshift(updated);
    return updated;
  }
  async function refresh(preferredId = null) {
    try {
      const data = await api('/api/safety/state');
      if (!Array.isArray(data.cases)) throw new Error('服务返回的案例列表格式不正确。');
      app.data = data;
      setConnection(true);
      $('#load-error').hidden = true;
      if (preferredId && data.cases.some(c => c.id === preferredId)) app.selected = preferredId;
      if (!data.cases.some(c => c.id === app.selected)) app.selected = data.cases.find(c => statusOf(c) === 'unsafe')?.id || data.cases[0]?.id || '';
      render();
      if (data.active_run?.job_id && !app.job && !['finished', 'failed', 'error'].includes(data.active_run.state)) beginPolling(data.active_run.job_id);
    } catch (error) {
      setConnection(false);
      const el = $('#load-error');
      el.textContent = `无法读取审计结果：${errorMessage(error)}  请确认本地 safety 服务仍在运行，再点击“刷新案例”。`;
      el.hidden = false;
      if (!app.data.cases.length) {
        $('#case-count').textContent = '尚未读取案例';
        $('#case-detail').replaceChildren(node('div', 'empty-state', null, [node('span', 'empty-icon', '[/]'), node('h3', '', '等待本地审计服务'), node('p', '', '请通过启动说明中的 HTTP 地址打开本页；离线文件无法执行采集和审计。')]));
      }
    }
  }
  function filteredCases() {
    const needle = app.search.trim().toLowerCase();
    const groups = { controlled: 0, openclaw: 1, imported: 2 };
    return app.data.cases.filter(c => {
      if (app.filter !== 'all' && statusOf(c) !== app.filter) return false;
      if (app.origin !== 'all' && originGroup(c) !== app.origin) return false;
      if (!needle) return true;
      const report = reportOf(c);
      const searchable = [c.id, c.title, c.origin_label, ...list(report.flags).map(f => `${f.rule_id} ${f.title} ${f.tool} ${f.trace_id}`), ...list(report.steps).map(s => `${s.tool} ${s.target} ${s.trace_id}`)].join(' ').toLowerCase();
      return searchable.includes(needle);
    }).sort((a, b) => groups[originGroup(a)] - groups[originGroup(b)]);
  }
  function setFilter(filter) {
    app.filter = filter;
    const cases = filteredCases();
    if (!cases.some(c => c.id === app.selected)) app.selected = cases[0]?.id || '';
    render();
  }
  function render() {
    $('#parsed-top-link').href = '/parsed.html' + (app.selected ? '?case=' + encodeURIComponent(app.selected) : '');
    if (app.selected) {
      try { history.replaceState(null, '', '#case/' + encodeURIComponent(app.selected)); } catch { /* Rendering also works without history support. */ }
    }
    for (const status of Object.keys(labels)) $('#count-' + status).textContent = app.data.cases.filter(c => statusOf(c) === status).length;
    all('.segmented [data-filter]').forEach(b => {
      const selected = b.dataset.filter === app.filter;
      b.classList.toggle('active', selected);
      b.setAttribute('aria-pressed', String(selected));
    });
    renderCases();
    renderDetail();
    renderPolicy();
    $('#footer-meta').textContent = `${app.data.cases.length} 个当前案例${app.data.archived_cases ? ` · ${app.data.archived_cases} 个旧演示案例已保留` : ''} · 本地运行 · 原项目保留`;
  }
  function renderCases() {
    const cases = filteredCases();
    $('#case-count').textContent = `${cases.length} / ${app.data.cases.length} 个案例`;
    const container = $('#case-list');
    container.replaceChildren();
    if (!cases.length) { container.append(node('p', 'empty-list', '当前筛选没有案例。调整筛选或运行一次演示。')); return; }
    for (const c of cases) {
      const report = reportOf(c);
      const b = button('', 'case-choice' + (c.id === app.selected ? ' active' : ''), () => selectCase(c.id));
      b.setAttribute('aria-pressed', String(c.id === app.selected));
      const flags = list(report.flags);
      const total = report.coverage?.total_actions ?? list(report.steps).length;
      b.append(node('div', 'case-choice-top', null, [node('span', 'case-number', c.display_id || String(c.id).slice(-25)), badge(statusOf(c))]), node('strong', '', titleOf(c)), node('small', '', `${total} 个工具步骤 · ${flags.length} 条违规标记`), node('div', 'case-origin', originLabel(c)));
      container.append(b);
    }
    const active = $('.case-choice.active', container);
    if (active) {
      const item = active.getBoundingClientRect(), bounds = container.getBoundingClientRect();
      if (container.scrollHeight > container.clientHeight) {
        if (item.top < bounds.top) container.scrollTop -= bounds.top - item.top;
        else if (item.bottom > bounds.bottom) container.scrollTop += item.bottom - bounds.bottom;
      }
      if (container.scrollWidth > container.clientWidth) {
        if (item.left < bounds.left) container.scrollLeft -= bounds.left - item.left;
        else if (item.right > bounds.right) container.scrollLeft += item.right - bounds.right;
      }
    }
  }
  function selectCase(id) {
    app.selected = id;
    try { history.replaceState(null, '', '#case/' + encodeURIComponent(id)); } catch { /* local file mode still renders the connection hint */ }
    render();
  }
  function factList(items, className = 'source-facts') {
    const dl = node('dl', className);
    for (const [key, val] of items) dl.append(node('dt', '', key), node('dd', '', value(val)));
    return dl;
  }
  function sectionHeading(title, subtitle) { return node('div', 'report-section-head', null, [node('h4', '', title), node('span', '', subtitle)]); }
  function renderDetail() {
    const c = currentCase();
    const container = $('#case-detail');
    container.replaceChildren();
    if (!c) { container.append(node('div', 'empty-state', null, [node('span', 'empty-icon', '[/]'), node('h3', '', '选择一个案例'), node('p', '', '运行受控采集演示，或导入已有轨迹，即可开始审计。')])); return; }
    const r = reportOf(c), status = statusOf(c), flags = list(r.flags), steps = list(r.steps), coverage = r.coverage || {};
    const download = button('下载报告 ↓', 'button small', () => downloadReport(c));
    container.append(node('div', 'detail-top', null, [node('div', '', null, [node('div', 'detail-origin', originLabel(c)), badge(status), node('h3', '', titleOf(c)), node('p', 'detail-subtitle', c.description || `案例 ID：${c.id}`)]), node('div', 'detail-actions', null, [download])]));
    const parsedLink = node('a', 'button parsed-entry', '查看解析结果：实体 / 关系 / 事实 ↗');
    parsedLink.href = '/parsed.html?case=' + encodeURIComponent(c.id);
    container.append(parsedLink);
    const explanations = {
      unsafe: `发现 ${flags.length} 条违反当前策略的调用尝试。下方单独展示执行效果；本系统进行事后审计，拦截由工具执行层完成。`,
      safe: '在当前策略约束和已验证的轨迹范围内，未发现违规。此结论只覆盖已启用规则。',
      unknown: '当前证据不足以确认通过。可以继续检查已采到的行为；缺失记录或未覆盖的工具仍需补齐。'
    };
    const summary = typeof r.summary === 'string' ? r.summary : explanations[status];
    container.append(node('div', `verdict ${status}`, null, [node('strong', '', labels[status]), node('p', '', summary)]));
    container.append(node('div', 'metric-row', null, [node('div', '', null, [node('span', '', '已检查 / 工具步骤'), node('strong', '', `${coverage.assessed_actions ?? '—'} / ${coverage.total_actions ?? steps.length}`)]), node('div', '', null, [node('span', '', '采集完整性声明'), node('strong', '', coverage.declared_complete === true ? '已声明完整' : coverage.declared_complete === false ? '未完整' : '未提供')]), node('div', '', null, [node('span', '', '当前策略版本'), node('strong', '', value(r.policy?.version, '未提供'))])]));

    const flagsSection = node('section', 'report-section');
    flagsSection.append(sectionHeading('Insight flags · 违规标记', '点击查看完整证据'));
    if (!flags.length) flagsSection.append(node('div', 'no-flags', status === 'unknown' ? '已采到的内容暂未命中明确违规；这不等于通过，证据完整性仍未满足。' : '本次审计没有发现违反已启用规则的调用。'));
    flags.forEach(flag => {
      const b = button('', 'flag-card', () => showEvidence(c, flag));
      b.append(node('div', 'flag-title-row', null, [node('strong', '', flag.title || flag.rule_id || '违规调用'), node('span', '', '↗')]), node('div', 'flag-meta', `${flag.rule_id || '规则未提供'} · 第 ${value(flag.step, '?')} 步 / ${value(flag.tool, '工具未提供')} · ${effectLabels[flag.effect] || '结果未知'}`));
      if (flag.recommendation) b.append(node('p', 'flag-recommendation', flag.recommendation));
      flagsSection.append(b);
    });
    container.append(flagsSection);

    const timelineSection = node('section', 'report-section');
    timelineSection.append(sectionHeading('一步步看：工具做了什么', `${steps.length} 个工具步骤`));
    const timeline = node('ol', 'timeline');
    if (!steps.length) timelineSection.append(node('div', 'no-flags', '这份报告没有可展示的工具步骤。请查看证据缺口。'));
    steps.forEach((step, index) => {
      const matching = flags.filter(f => list(step.flag_ids).includes(f.id) || (f.step === step.step && f.span_id === step.span_id));
      const li = node('li', 'trace-step' + (matching.length ? ' flagged' : ''));
      const effect = effectLabels[step.effect] || '结果未知';
      li.append(node('span', 'step-number', step.step ?? index + 1), node('div', 'step-head', null, [node('strong', '', `${value(step.tool, '未知工具')} · ${kindLabels[step.kind] || value(step.kind, '调用')}`), badge(matching.length ? 'unsafe' : 'neutral', effect)]), node('div', 'step-target', value(step.target, '未记录可判定的目标')), node('div', 'step-footer', null, [node('span', '', `本步规则检查：${labels[step.status] || value(step.status, '未提供')}`), node('code', '', `span ${value(step.span_id, '未提供')}`)]));
      if (matching.length) li.append(node('div', 'step-flags', null, matching.map(f => button(`${f.rule_id || '违规标记'} ↗`, '', () => showEvidence(c, f)))));
      timeline.append(li);
    });
    timelineSection.append(timeline);
    container.append(timelineSection);

    const rules = r.rules || policyOf(c).rules;
    if (rules && typeof rules === 'object') {
      const ruleSection = node('section', 'report-section');
      ruleSection.append(sectionHeading('本次使用的规则', '详细配置见下方策略'));
      const entries = Array.isArray(rules) ? rules.map((r, i) => [r.id || r.rule_id || String(i + 1), r]) : Object.entries(rules);
      const chips = node('div');
      entries.forEach(([id, rule]) => {
        const hit = flags.some(f => f.rule_id === id);
        const enabled = typeof rule === 'boolean' ? rule : rule?.enabled !== false;
        chips.append(node('span', 'rule-chip' + (hit ? ' hit' : ''), `${rule?.title || id}${!enabled ? ' · 未启用' : hit ? ' · 命中' : ''}`));
      });
      ruleSection.append(chips);
      container.append(ruleSection);
    }
    const details = node('details', 'coverage-details');
    const gaps = list(coverage.gaps);
    details.append(node('summary', '', `采集完整性、适用边界与来源${gaps.length ? ` · ${gaps.length} 项证据缺口` : ''}`));
    const body = node('div', 'coverage-body');
    body.append(node('p', '', `正文完整性：${coverage.content_complete === true ? '满足' : coverage.content_complete === false ? '不满足' : '未提供'}；采集完整性声明：${coverage.declared_complete === true ? '完整' : '未完整或未提供'}。`));
    if (gaps.length) body.append(node('ul', '', null, gaps.map(g => node('li', '', gapText(g)))));
    const limitations = [...list(r.limitations), ...list(app.data.limitations)];
    if (limitations.length) body.append(node('ul', '', null, [...new Set(limitations.map(v => typeof v === 'string' ? v : JSON.stringify(v)))].map(v => node('li', '', v))));
    body.append(factList([['轨迹来源 ID', r.source?.trajectory_id], ['输入 SHA256', r.source?.sha256], ['策略 ID', r.policy?.id], ['策略 SHA256', r.policy?.digest], ['报告时间', r.created_at || r.generated_at || c.created_at], ['来源类别', c.origin]]));
    details.append(body);
    container.append(details);
  }
  function renderPolicy(force = false) {
    const c = currentCase();
    const policy = policyOf(c);
    const key = `${c?.id || ''}:${JSON.stringify(policy)}`;
    if (!force && app.policyKey === key) return;
    app.policyKey = key;
    $('#policy-json').value = JSON.stringify(policy, null, 2);
    $('#policy-badge').textContent = `${value(policy.id, '未命名策略')} · ${value(policy.version, '未标版本')}`;
    $('#policy-context').textContent = c ? `当前目标：${titleOf(c)}。只重新审计这个案例，其他案例保持原策略。` : '先选择一个案例，再应用策略。';
    $('#reaudit').disabled = !c || app.busy;
    $('#policy-feedback').textContent = '编辑后点击重新审计。';
    $('#policy-feedback').classList.remove('error');
  }
  function showEvidence(c, flag) {
    const r = reportOf(c), container = $('#evidence-body');
    $('#evidence-title').textContent = flag.title || '违规证据';
    container.replaceChildren();
    container.append(node('p', 'evidence-summary', flag.recommendation || '根据下方查询和原文，核对本次违规标记。'), factList([['规则', flag.rule_id], ['标记 ID', flag.id], ['工具步骤', `${value(flag.step)} / ${value(flag.tool)}`], ['执行结果', effectLabels[flag.effect] || '结果未知'], ['trace ID', flag.trace_id], ['span ID', flag.span_id], ['策略摘要', r.policy?.digest]], 'evidence-facts'));
    const evidences = [...list(flag.evidence).map(item => ({ item, role: '规则命中的原文' })), ...list(flag.effect_evidence).map(item => ({ item, role: '执行效果的原文' }))];
    if (!evidences.length) container.append(node('p', 'evidence-summary', '报告未提供证据片段，无法在此核对原文。'));
    evidences.forEach(({ item: evidence, role }, index) => {
      const query = list(r.queries).find(q => q.id === evidence.query_id);
      const section = node('section', 'evidence-section');
      section.append(node('h3', '', `证据 ${index + 1} · ${role}`), node('pre', 'quote', value(evidence.text, '未提供原文')));
      section.append(factList([['source / fid', `${value(evidence.source_id)} / ${value(evidence.fid)}`], ['步骤 / 模块', `${value(evidence.step)} / ${value(evidence.module)}`], ['字符位置', `[${value(evidence.start)}, ${value(evidence.end)})`], ['精确片段 ID', evidence.fragment_id], ['JSON 字段', evidence.json_pointer], ['trace / span', `${value(evidence.trace_id, flag.trace_id || '未提供')} / ${value(evidence.span_id, flag.span_id || '未提供')}`]], 'evidence-facts'));
      section.append(node('p', '', '字符位置从 0 开始，包含起点、不包含终点；source_id 指向包围区间，片段 ID 对应上方展示的精确原文。'));
      section.append(node('h3', '', `查询 SQL · ${value(evidence.query_id)}`), node('pre', '', query?.sql || '报告未提供对应 SQL。'));
      if (query?.result) {
        const rows = list(query.result.rows);
        const count = query.result.row_count ?? query.result.rowcount ?? rows.length;
        section.append(node('p', '', `服务执行查询后返回 ${count} 行；上方展示与本条违规相关的原文证据。`));
      }
      container.append(section);
    });
    $('#evidence-dialog').showModal();
  }
  function downloadReport(c) {
    const payload = { case_id: c.id, title: c.title, origin: c.origin, origin_label: originLabel(c), policy: policyOf(c), report: reportOf(c) };
    const blob = new Blob([JSON.stringify(payload, null, 2) + '\n'], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url;
    link.download = `safety-${String(c.id).replace(/[^a-zA-Z0-9_-]/g, '_').slice(-100)}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
    toast('已下载当前策略和审计报告。');
  }
  function renderJob(job) {
    const banner = $('#job-banner');
    banner.hidden = false;
    banner.classList.toggle('success', job.state === 'finished');
    const message = job.state === 'finished' ? '新采集与审计已完成' : ['failed', 'error'].includes(job.state) ? '采集演示未完成' : '正在实际运行采集与审计';
    banner.replaceChildren(node('strong', '', message), node('span', '', job.error ? value(job.error) : value(job.stage, '正在执行本地受控工具，经 OTLP 导入后生成审计报告。')));
  }
  async function pollJob(jobId) {
    try {
      const job = await api(`/api/safety/jobs/${encodeURIComponent(jobId)}`);
      renderJob(job);
      if (job.state === 'finished') {
        app.job = null;
        $('#run-demo').disabled = false;
        app.filter = 'all';
        app.search = '';
        app.origin = 'all';
        $('#case-search').value = '';
        $('#origin-filter').value = 'all';
        await refresh(list(job.case_ids)[0]);
        toast('新的真实工具执行记录已完成审计，可逐项查看。');
        return;
      }
      if (['failed', 'error', 'cancelled'].includes(job.state)) {
        app.job = null;
        $('#run-demo').disabled = false;
        return;
      }
      app.polling = setTimeout(() => pollJob(jobId), 1500);
    } catch (error) {
      app.job = null;
      $('#run-demo').disabled = false;
      renderJob({ state: 'error', error: '读取运行进度失败：' + errorMessage(error) + '。刷新案例可重新连接。' });
    }
  }
  function beginPolling(jobId) {
    clearTimeout(app.polling);
    app.job = jobId;
    $('#run-demo').disabled = true;
    renderJob({ state: 'running', stage: '已提交本地采集任务，正在等待进度。' });
    pollJob(jobId);
  }
  async function runDemo() {
    $('#run-demo').disabled = true;
    try {
      const job = await post('/api/safety/demo', {});
      if (!job.job_id) throw new Error('服务未返回任务 ID。');
      beginPolling(job.job_id);
    } catch (error) {
      $('#run-demo').disabled = false;
      renderJob({ state: 'error', error: errorMessage(error) });
    }
  }
  async function reaudit() {
    const c = currentCase();
    if (!c) return;
    const feedback = $('#policy-feedback');
    let policy;
    try { policy = JSON.parse($('#policy-json').value); } catch (error) { feedback.textContent = '策略不是有效 JSON：' + errorMessage(error); feedback.classList.add('error'); return; }
    if (!policy || Array.isArray(policy) || typeof policy !== 'object') { feedback.textContent = '策略必须是一个 JSON 对象。'; feedback.classList.add('error'); return; }
    app.busy = true;
    $('#reaudit').disabled = true;
    feedback.textContent = '服务正在重新查询轨迹并应用规则…';
    feedback.classList.remove('error');
    try {
      const result = await post('/api/safety/audit', { case_id: c.id, policy });
      const updated = updateCase({ ...result.case, policy: result.case?.policy || policy }, result.report, c.id);
      app.selected = updated.id;
      app.filter = 'all';
      render();
      feedback.textContent = '已完成新的服务端审计。当前报告已更新。';
      toast(`重新审计完成：${labels[statusOf(updated)]}`);
      $('#workspace').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) { feedback.textContent = '重新审计失败：' + errorMessage(error); feedback.classList.add('error'); }
    finally { app.busy = false; $('#reaudit').disabled = !currentCase(); }
  }
  function openImport() {
    $('#import-policy').value = JSON.stringify(policyOf(currentCase()), null, 2);
    $('#import-feedback').textContent = '请检查允许目录等任务范围是否适合这份轨迹；可以直接编辑策略。';
    $('#import-dialog').showModal();
  }
  async function importTrace(event) {
    event.preventDefault();
    const feedback = $('#import-feedback'), file = $('#import-file').files[0];
    if (!file) { feedback.textContent = '请先选择 input.json 文件。'; return; }
    if (file.size > MAX_IMPORT_BYTES) { feedback.textContent = '轨迹文件超过 4 MB，请通过命令行导入或拆分任务后再上传。'; return; }
    $('#submit-import').disabled = true;
    try {
      feedback.textContent = '正在读取 JSON 并提交服务端审计…';
      const source = JSON.parse(await file.text()), policy = JSON.parse($('#import-policy').value);
      const payload = { source, policy, title: $('#import-title-input').value.trim() || file.name };
      if (new TextEncoder().encode(JSON.stringify(payload)).byteLength > MAX_IMPORT_BYTES) {
        throw new Error('轨迹、策略和案例名称的完整请求超过 4 MB，请缩小内容或通过命令行导入。');
      }
      const result = await post('/api/safety/import', payload);
      if (!result.case?.id) throw new Error('服务未返回已保存案例 ID。');
      const c = updateCase(result.case, result.report, result.case.id);
      app.selected = c.id;
      app.filter = 'all'; app.origin = 'all'; app.search = '';
      $('#case-search').value = ''; $('#origin-filter').value = 'all';
      render();
      $('#import-dialog').close();
      toast(`导入与审计完成：${labels[statusOf(c)]}`);
      $('#workspace').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) { feedback.textContent = '导入失败：' + errorMessage(error); }
    finally { $('#submit-import').disabled = false; }
  }

  all('[data-filter]').forEach(b => b.addEventListener('click', () => setFilter(b.dataset.filter)));
  $('#case-search').addEventListener('input', event => { app.search = event.target.value; const cases = filteredCases(); if (!cases.some(c => c.id === app.selected)) app.selected = cases[0]?.id || ''; render(); });
  $('#origin-filter').addEventListener('change', event => { app.origin = event.target.value; const cases = filteredCases(); if (!cases.some(c => c.id === app.selected)) app.selected = cases[0]?.id || ''; render(); });
  $('#refresh').addEventListener('click', () => refresh());
  $('#run-demo').addEventListener('click', runDemo);
  $('#reaudit').addEventListener('click', reaudit);
  $('#reset-policy').addEventListener('click', () => { renderPolicy(true); toast('已恢复当前案例最后一次审计使用的策略。'); });
  $('#policy-json').addEventListener('input', () => { $('#policy-feedback').textContent = '策略已编辑，尚未应用到报告。'; $('#policy-feedback').classList.remove('error'); });
  $('#open-import').addEventListener('click', openImport);
  $('#import-form').addEventListener('submit', importTrace);
  $('#import-file').addEventListener('change', () => { const file = $('#import-file').files[0]; $('#import-file-info').textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : '只读取你选择的文件；提交后保存为一条独立案例。'; });
  all('[data-close]').forEach(b => b.addEventListener('click', () => $('#' + b.dataset.close).close()));
  window.addEventListener('beforeunload', () => clearTimeout(app.polling));
  window.addEventListener('hashchange', () => {
    const match = location.hash.match(/^#case\/(.+)$/);
    if (!match) return;
    let id;
    try { id = decodeURIComponent(match[1]); } catch { return; }
    if (!app.data.cases.some(c => c.id === id)) return;
    app.filter = 'all'; app.origin = 'all'; app.search = '';
    $('#case-search').value = ''; $('#origin-filter').value = 'all';
    selectCase(id);
  });
  const fromHash = location.hash.match(/^#case\/(.+)$/);
  if (fromHash) { try { app.selected = decodeURIComponent(fromHash[1]); } catch { app.selected = ''; } }
  refresh();
})();
