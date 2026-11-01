'use strict';
(() => {
  const $ = s => document.querySelector(s);
  const state = { cases: [], data: null, record: null, entity: '', fact: null, step: 'all', slot: 'all', rel: 'all', search: '', sequence: 0 };
  const slots = { obs: '观察', mem: '记忆', refl: '反思', plan: '计划', act: '行动', chk: '检查' };
  const modules = { obs: 'observation', mem: 'memory', refl: 'reflection', plan: 'plan', act: 'action' };
  const array = x => Array.isArray(x) ? x : [];
  const entityKey = e => e && typeof e === 'object' ? `${e.kind}:${e.index}` : e == null ? '' : String(e);
  const raw = v => typeof v === 'string' ? v : JSON.stringify(v, null, 2) ?? '字段未提供';
  const short = (v, n = 105) => { const t = raw(v); return t.length > n ? t.slice(0, n) + '…（摘要）' : t; };
  const el = (tag, cls, text, children = []) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); children.forEach(c => c && e.append(c)); return e; };
  const button = (text, cls, action) => { const e = el('button', cls, text); e.type = 'button'; e.addEventListener('click', action); return e; };
  const badge = (text, cls = 'neutral') => el('span', 'badge ' + cls, text);
  const factById = fid => array(state.record?.facts).find(f => String(f.fid) === String(fid));
  const flagsFor = fid => array(state.data?.safety_links).filter(f => array(f.fids).some(v => String(v) === String(fid)));
  const isPresent = v => v !== null && v !== undefined && v !== '';
  function sourceText(step, slot) { const s = state.record?.source_steps?.[String(step)]; return slot === 'obs' ? s?.observation : s?.modules?.[modules[slot]]; }
  function decode(v) { try { return typeof v === 'string' ? JSON.parse(v) : v; } catch { return null; } }
  function resultMissing(fact) { const v = decode(fact?.value); return fact?.rel === 'tool_result' && v && typeof v === 'object' && !Object.hasOwn(v, 'result'); }
  function factsForEntity(key) { return array(state.record?.facts).filter(f => array(f.args).some(a => entityKey(a) === key)); }
  function factFields(fields) { const dl = el('dl', 'fact-fields'); fields.forEach(([k, v]) => dl.append(el('dt', '', k), el('dd', '', raw(v)))); return dl; }
  function info(text, warn = false) { return el('p', 'parsed-info' + (warn ? ' warn' : ''), text); }
  async function get(path) { const r = await fetch(path, { cache: 'no-store' }); let data; try { data = await r.json(); } catch { throw new Error('本地服务没有返回 JSON，请从运行中的 8783 服务打开本页。'); } if (!r.ok || data.error) throw new Error(data.error || '请求失败：' + r.status); return data; }
  function toast(text) { $('#parsed-toast').textContent = text; $('#parsed-toast').classList.add('visible'); setTimeout(() => $('#parsed-toast').classList.remove('visible'), 2800); }
  function showSource(title, text, description) { $('#source-title').textContent = title; $('#source-description').textContent = description; $('#source-content').textContent = isPresent(text) ? raw(text) : '该字段未记录，保持缺失。'; $('#source-dialog').showModal(); }
  function auditHref() { return '/#case/' + encodeURIComponent(state.data.case_id); }
  async function loadCase(id) {
    const sequence = ++state.sequence; $('#download-record').disabled = true; $('#parsed-title').textContent = '正在编译并读取解析结果…'; $('#parsed-error').hidden = true;
    state.data = state.record = null;
    document.querySelectorAll('.parse-layers,.parse-counts,.parse-explanation,.parse-section,.parse-provenance').forEach(e => e.hidden = true);
    $('#back-audit').href = $('#footer-back').href = '/#case/' + encodeURIComponent(id);
    try {
      const data = await get('/api/safety/parsed/' + encodeURIComponent(id)); if (sequence !== state.sequence) return;
      state.data = data; state.record = data.record; state.entity = ''; state.step = state.slot = state.rel = 'all'; state.search = ''; $('#entity-search').value = '';
      state.fact = array(state.record.facts).find(f => f.rel === 'tool_call')?.fid ?? state.record.facts?.[0]?.fid ?? null;
      if (![...$('#parsed-case').options].some(o => o.value === id)) { const option = el('option', '', `${data.title} · ${data.origin_label || data.origin} · 指定案例`); option.value = id; $('#parsed-case').append(option); }
      $('#parsed-case').value = id; history.replaceState(null, '', '?case=' + encodeURIComponent(id));
      $('#parsed-title').textContent = data.title; $('#parsed-origin').textContent = data.origin_label || data.origin;
      $('#compiler-badge').textContent = `${data.compiler.entry} · ${data.compiler.online ? 'online' : 'offline'} · ${data.compiler.ir_version}`;
      $('#back-audit').href = $('#footer-back').href = auditHref();
      for (const key of ['entities', 'relations', 'facts', 'goals', 'steps']) $('#count-' + key).textContent = data.counts[key];
      options('#step-filter', [...new Set(array(state.record.facts).map(f => f.t))].sort((a,b) => a-b), '全部步骤', v => 'step ' + v);
      options('#slot-filter', [...new Set(array(state.record.facts).map(f => f.slot))], '全部模块', v => `${v} · ${slots[v] || '其他'}`);
      options('#relation-filter', Object.keys(state.record.relations || {}), '全部关系', v => v);
      renderEntities(); renderFacts(); renderRelations(); renderSources(); renderGoals(); renderSafety(); renderProvenance();
      document.querySelectorAll('.parse-layers,.parse-counts,.parse-explanation,.parse-section,.parse-provenance').forEach(e => e.hidden = false);
      $('#download-record').disabled = false;
    } catch (error) { if (sequence !== state.sequence) return; $('#parsed-error').hidden = false; $('#parsed-error').textContent = '读取解析结果失败：' + error.message; $('#parsed-title').textContent = '当前案例无法读取'; }
  }
  function options(selector, values, title, format) { const s = $(selector); s.replaceChildren(); const first = el('option', '', title); first.value = 'all'; s.append(first); values.forEach(v => { const opt = el('option', '', format(v)); opt.value = String(v); s.append(opt); }); }
  function setEntity(key) { state.entity = key; state.step = state.slot = state.rel = 'all'; ['#step-filter','#slot-filter','#relation-filter'].forEach(s => $(s).value = 'all'); renderEntities(); renderFacts(); }
  function renderEntities() {
    const box = $('#entity-list'); box.replaceChildren();
    Object.entries(state.record.entities || {}).filter(([key]) => key.toLowerCase().includes(state.search.toLowerCase())).forEach(([key, entity]) => {
      const b = button('', 'entity-option' + (key === state.entity ? ' active' : ''), () => setEntity(key)); b.dataset.entity = key; b.setAttribute('aria-pressed', String(key === state.entity)); b.append(el('span', 'kind-tag', entity.kind), el('code', '', entity.index)); box.append(b);
    });
    const detail = $('#entity-detail'); detail.replaceChildren();
    if (state.entity) { detail.append(el('strong', '', state.entity), el('p', '', `被 ${factsForEntity(state.entity).length} 条事实的 args 引用。purpose 等字段在事实详情中另列。`), el('pre', '', raw(state.record.entities[state.entity]))); if (/^(req:|int:|agent:self$)/.test(state.entity)) detail.append(el('p', '', '这是原编译器的结构项；其存在不代表采集到了对应的计划或推理内容。')); }
    else detail.append(el('p', '', '这里的每项都来自 record.entities。点击实体后，事实表只保留 args 实际引用它的记录。'));
  }
  function visibleFacts() { return array(state.record.facts).filter(f => (state.step === 'all' || String(f.t) === state.step) && (state.slot === 'all' || f.slot === state.slot) && (state.rel === 'all' || f.rel === state.rel) && (!state.entity || array(f.args).some(a => entityKey(a) === state.entity))); }
  function chooseFact(fid, reset = false, scroll = false) {
    if (reset) { state.entity = ''; state.step = state.slot = state.rel = 'all'; ['#step-filter','#slot-filter','#relation-filter'].forEach(s => $(s).value = 'all'); renderEntities(); }
    state.fact = fid; renderFacts(); if (scroll) $('#facts').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function renderFacts() {
    const facts = visibleFacts(); if (!facts.some(f => f.fid === state.fact)) state.fact = facts[0]?.fid ?? null;
    $('#fact-count').textContent = `${facts.length} / ${state.record.facts.length} 条事实`; $('#entity-filter-label').textContent = state.entity ? '实体筛选：' + state.entity : '';
    const rows = $('#fact-rows'); rows.replaceChildren(); $('#facts-empty').hidden = !!facts.length;
    facts.forEach(f => {
      const row = el('tr', f.fid === state.fact ? 'selected' : ''); row.dataset.fid = f.fid;
      const select = button('F' + f.fid, 'fact-row-button', () => chooseFact(f.fid)); select.setAttribute('aria-label', `查看事实 ${f.fid} ${f.rel}`);
      const args = array(f.args).map(entityKey).join(', ');
      row.append(el('td', '', null, [select, el('small', '', 'step ' + f.t)]), el('td', '', null, [el('code', '', f.slot), el('small', '', f.side)]), el('td', '', null, [el('strong', '', f.rel), el('div', 'fact-args', short(args, 82))]), el('td', '', null, [el('div', 'value-preview', short(f.value, 90)), flagsFor(f.fid).length ? el('small', '', '被安全 flag 引用') : null]));
      row.addEventListener('click', e => { if (!e.target.closest('button')) chooseFact(f.fid); }); rows.append(row);
    });
    renderGraph(); renderInspector();
  }
  function svgNode(tag, attrs, text) { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [k,v] of Object.entries(attrs || {})) e.setAttribute(k, v); if (text !== undefined) e.textContent = text; return e; }
  function renderGraph() {
    const fact = factById(state.fact), box = $('#fact-graph'), argsBox = $('#graph-arguments'); box.replaceChildren(); argsBox.replaceChildren();
    if (!fact) { $('#graph-title').textContent = '当前没有选中的事实'; box.append(el('p', 'small-note', '调整筛选后选择一条事实。')); return; }
    $('#graph-title').textContent = `F${fact.fid} · ${fact.rel} · step ${fact.t}`;
    const args = array(fact.args), height = Math.max(230, args.length * 95 + 30), center = height/2;
    const svg = svgNode('svg', { viewBox: `0 0 720 ${height}`, role: 'img', 'aria-label': `事实 ${fact.fid} ${fact.rel} 的 ${args.length} 个真实实体参数` });
    args.forEach((arg, index) => {
      const y = args.length === 1 ? center : 55 + index * (height-110)/(args.length-1), key = entityKey(arg);
      svg.append(svgNode('path', { d: `M 250 ${center} C 340 ${center}, 330 ${y}, 420 ${y}`, class: 'graph-edge' }), svgNode('text', { x: 320, y: (center+y)/2-8, class:'graph-label' }, `args[${index}]`));
      const group = svgNode('g', { class: 'graph-entity' + (key === state.entity ? ' active' : ''), tabindex:'0', role:'button', 'aria-label': '筛选实体 ' + key, 'data-entity': key });
      group.append(svgNode('title', {}, key), svgNode('rect', { x:420, y:y-29, width:278, height:58, rx:8 }), svgNode('text', { x:434,y:y-6,class:'graph-main' }, arg.kind), svgNode('text', { x:434,y:y+14,class:'graph-sub' }, arg.index.length > 39 ? arg.index.slice(0,36)+'…' : arg.index));
      group.addEventListener('click', () => setEntity(key)); group.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setEntity(key); } }); svg.append(group);
      argsBox.append(button(`args[${index}] = ${key}`, '', () => setEntity(key)));
    });
    const centerGroup = svgNode('g', { class:'graph-fact' }); centerGroup.append(svgNode('rect',{x:20,y:center-38,width:230,height:76,rx:10}),svgNode('text',{x:37,y:center-8,class:'graph-main'},`F${fact.fid} · ${fact.rel}`),svgNode('text',{x:37,y:center+16,class:'graph-sub'},`step ${fact.t} / slot ${fact.slot} / ${args.length} args`)); svg.append(centerGroup); box.append(svg);
  }
  function renderInspector() {
    const fact = factById(state.fact), box = $('#fact-inspector'); box.replaceChildren(); if (!fact) { box.append(el('p','','当前筛选没有可查看的事实。')); return; }
    box.append(el('h3', '', `F${fact.fid} · ${fact.rel}`), el('p', '', '以下字段来自原 canonical record，null 和空数组保持原样。'));
    const fields = factFields([['t / step',fact.t],['slot / side',`${fact.slot} / ${fact.side}`],['value_type',fact.value_type],['args',fact.args],['purpose',fact.purpose]]);
    const basis = el('dd'); if (array(fact.basis).length) fact.basis.forEach(fid => basis.append(button('F'+fid,'',()=>chooseFact(fid,true)))); else basis.textContent='[]'; fields.append(el('dt','','basis'),basis);
    const situation = el('dd'); if (fact.situation === null || fact.situation === undefined) situation.textContent=raw(fact.situation); else situation.append(button('F'+fact.situation,'',()=>chooseFact(fact.situation,true))); fields.append(el('dt','','situation'),situation); box.append(fields);
    if (fact.slot === 'chk') box.append(info('这条是原编译器检查记录；不应把它当作采集到的工具调用或 agent 思考。'));
    if (resultMissing(fact)) box.append(info('工具结果 envelope 已解析，但 result 正文没有记录。现有 status / error 摘要仍保留；存在 tool_result Fact 不等于返回正文完整。',true));
    else if (fact.rel === 'tool_result' && decode(fact.value)?.result === null) box.append(info('原文包含 result 字段，其采集值为显式 null；这与 result 字段未记录不同。这里按原值保留。'));
    box.append(el('h4','','value · 原始值'),el('pre','parsed-code',raw(fact.value)),el('h4','','span · 事实附带的原文'),el('pre','parsed-code light',raw(fact.span)));
    if (fact.t === 0) box.append(button('查看任务原文 ↗','button source-button',()=>showSource('任务原文 · step 0',state.record.task,'来自 canonical record.task；任务目标与工具轨迹分开记录。')));
    else if (modules[fact.slot]) box.append(button(`查看 step ${fact.t} / ${fact.slot} 完整来源 ↗`,'button source-button',()=>showSource(`step ${fact.t} / ${fact.slot} · ${slots[fact.slot]}`,sourceText(fact.t,fact.slot),'来源：record.source_steps；这里显示的是规范化输入原文。')));
    const links = flagsFor(fact.fid); if (links.length) { box.append(el('h4','','后续安全审计引用')); links.forEach(f => { const a=el('a','fact-flag-link',`${f.title} · ${f.rule_id} → 返回审计`);a.href=auditHref();box.append(a); }); }
  }
  function renderRelations() {
    const box=$('#relation-rows');box.replaceChildren();Object.entries(state.record.relations||{}).forEach(([key,r])=>{
      const count=state.record.facts.filter(f=>f.rel===key).length;
      const action=button(key,'',()=>{state.entity='';state.step=state.slot='all';state.rel=key;$('#step-filter').value=$('#slot-filter').value='all';$('#relation-filter').value=key;renderEntities();renderFacts();$('#facts').scrollIntoView({behavior:'smooth'});});
      box.append(el('tr','',null,[el('td','',null,[action]),el('td','',`${r.arity} args · (${array(r.arg_kinds).map(v=>v??'any').join(', ')})`),el('td','',array(r.value_types).join(' / ')),el('td','',`${array(r.props).join(', ') || '[]'} / key ${raw(r.key)}`),el('td','',count)]));
    });
  }
  function renderSources() {
    const box=$('#module-rows');box.replaceChildren();Object.entries(state.record.source_steps||{}).sort(([a],[b])=>Number(a)-Number(b)).forEach(([step,source])=>{
      const row=el('tr','',null,[el('td','','step '+step)]);
      for(const slot of ['obs','mem','refl','plan','act']) { const text=sourceText(step,slot), cell=el('td'); if(isPresent(text)){cell.append(button('已记录 · 查看','',()=>showSource(`step ${step} / ${slot} · ${slots[slot]}`,text,`来源 record.source_steps[${step}]${slot==='obs'?'.observation':'.modules.'+modules[slot]}。按原记录显示。`)),el('small','',String(text).length+' 字符'));}else cell.append(el('span','module-missing','未记录'));row.append(cell); }box.append(row);
    });
    const missing=array(state.record.facts).filter(resultMissing), missingBox=$('#missing-results');missingBox.replaceChildren();if(missing.length)missingBox.append(info(`有 ${missing.length} 条 tool_result Fact 的 envelope 存在，但 result 正文缺失：${missing.map(f=>'F'+f.fid+' / step '+f.t).join('，')}。这部分不补造内容。`,true));
    const audits=$('#parse-audit-list');audits.replaceChildren();array(state.record.parse_audits).forEach(a=>audits.append(el('div','audit-row',null,[el('strong','',`${a.source_id} · ${a.status}`),el('pre','',raw(a))])));if(!array(state.record.parse_audits).length)audits.append(el('p','small-note','原记录没有 parse_audits。'));
  }
  function renderGoals() { const box=$('#goal-list');box.replaceChildren();array(state.record.goals).forEach((g,i)=>{const card=el('article','goal-card');card.append(el('h3','',g.name||'目标 '+(i+1)),badge(g.status_mode,g.status_mode==='unmodeled'?'unknown':'neutral'),factFields([['count',g.count],['of_kind',g.of_kind],['rel',g.rel],['target',g.target],['attrs',g.attrs]]),el('p','',g.status_mode==='unmodeled'?'原目标保留，但当前 profile 尚不能确定性判定它是否完成。':'tracked 表示原目标参与任务检查；实际结果仍需看对应事实。'),el('pre','parsed-code light',raw(g.constraints)));box.append(card);});if(!state.record.goals?.length)box.append(el('p','no-flags','原记录没有任务目标。')); }
  function renderSafety() { const box=$('#safety-link-list');box.replaceChildren();const warnings=array(state.data.warnings);warnings.forEach(w=>box.append(info('安全报告关联提示：'+(typeof w==='string'?w:raw(w)),true)));array(state.data.safety_links).forEach(f=>{const card=el('article','safety-parse-card');card.append(el('h3','',f.title),el('p','',`${f.rule_id} · ${f.flag_id} · step ${f.step}`));array(f.fids).forEach(fid=>card.append(button('查看 F'+fid,'',()=>chooseFact(fid,true,true))));const back=el('a','', '查看安全规则和精确查询证据 ↗');back.href=auditHref();card.append(back);box.append(card);});if(!array(state.data.safety_links).length)box.append(el('p','no-flags',warnings.length?'当前没有可可靠关联的安全 flag，请先核对上方提示。原 E / R / F / G 解析结构仍可独立查看。':'当前已保存审计报告没有违规 flag。E / R / F / G 仍按原编译结果展示；没有 flag 也不自动代表证据完整或任务成功。')); }
  function renderProvenance() { const box=$('#parsed-provenance');box.className='fact-fields';box.replaceChildren();[['case_id',state.data.case_id],['trajectory_id',state.record.trajectory_id],['编译入口',state.data.compiler.entry],['online',state.data.compiler.online],['IR 版本',state.data.compiler.ir_version],['版本信息',state.data.compiler.versions],['输入 SHA256',state.data.source_sha256],['快照 SHA256',state.data.snapshot_sha256]].forEach(([k,v])=>box.append(el('dt','',k),el('dd','',raw(v)))); }
  function download() { const url=URL.createObjectURL(new Blob([JSON.stringify(state.record,null,2)+'\n'],{type:'application/json;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='canonical_record.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1500);toast('已下载原 canonical record，未增加展示字段。'); }
  $('#parsed-case').addEventListener('change',e=>loadCase(e.target.value));$('#entity-search').addEventListener('input',e=>{state.search=e.target.value;if(state.record)renderEntities();});$('#clear-entity').addEventListener('click',()=>{if(state.record)setEntity('');});
  for(const [selector,key] of [['#step-filter','step'],['#slot-filter','slot'],['#relation-filter','rel']])$(selector).addEventListener('change',e=>{state[key]=e.target.value;renderFacts();});
  $('#reset-filters').addEventListener('click',()=>{if(state.record)setEntity('');});$('#download-record').addEventListener('click',download);$('#close-source').addEventListener('click',()=>$('#source-dialog').close());$('#close-source-bottom').addEventListener('click',()=>$('#source-dialog').close());
  (async()=>{try{const data=await get('/api/safety/state');state.cases=data.cases;const select=$('#parsed-case');select.replaceChildren();data.cases.forEach(c=>{const o=el('option','',`${c.title} · ${c.origin_label||c.origin}`);o.value=c.id;select.append(o);});const wanted=new URLSearchParams(location.search).get('case');const id=wanted||state.cases.find(c=>c.id.includes('missing_output'))?.id||state.cases[0]?.id;if(!id)throw new Error('当前服务没有可读取的案例。');await loadCase(id);}catch(error){$('#parsed-error').hidden=false;$('#parsed-error').textContent='读取案例列表失败：'+error.message;$('#parsed-title').textContent='等待本地解析服务';}})();
})();
