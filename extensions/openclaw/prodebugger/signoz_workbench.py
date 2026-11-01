"""HTTP-facing imports and explicit policy revisions; no Agent execution."""
from .record_bridge import digest, write_json


def unconfigured_policy(root):
    from .safety_workbench import default_policy
    safety = default_policy(root)
    safety.update(id='unconfigured-import-scope', tools={}, allowed_roots=[],
                  forbidden_paths=[], denied_tools=[], denied_commands=[])
    risk = {'schema': 'prodebugger-risk-policy/v1', 'id': 'unconfigured-import-scope',
            'version': '1', 'path_style': safety['path_style'],
            'working_directory': safety['working_directory'], 'tools': {},
            'read_scope': None, 'approvals': [], 'sensitive_sources': [],
            'protected_writes': [], 'untrusted_sources': []}
    return safety, risk


def import_request(store, data):
    from .signoz_import import import_documents
    docs = data.get('files', data.get('documents'))
    if 'files' in data and 'documents' in data and data['files'] != data['documents']:
        raise ValueError('files 与 documents 的内容不一致。')
    if not isinstance(docs, list) or not 1 <= len(docs) <= 24:
        raise ValueError('请选择 1–24 个 SigNoz JSON/JSONL 文件。')
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get('name'), str) or not isinstance(doc.get('text'), str):
            raise ValueError('文件需要 name 和 text，不接受本地路径代替内容。')
        if len(doc['text'].encode('utf-8')) > 8 * 1024 * 1024:
            raise ValueError('单个 SigNoz 文件上限为 8 MB。')
    imported = import_documents(docs, task=data.get('task') or None)
    safety, risk = unconfigured_policy(store.folder / 'workspace')
    safety, risk = data.get('policy') or safety, data.get('risk_policy') or risk
    cases, audits, errors = [], [], []
    for item in imported:
        try:
            case = store.add_source(item['source'], safety, title=item['title'], risk_policy=risk)
            audits.append(item['audit'])
            with store.lock:
                saved = store._find(case['id'])
                saved.update(source_category='signoz_cloud_import',
                    origin_label='SigNoz 原始导出导入 · 来源凭据见原文',
                    import_audit=item['audit'])
                store._save()
                case = store._public_case(saved)
            write_json(store.folder / 'imports' / case['id'] / 'signoz-import-audit.json', item['audit'])
            cases.append(case)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append({'trace_id': item.get('source', {}).get('trajectory_id'), 'error': str(exc)})
    return {'cases': cases, 'audits': audits, 'errors': errors,
            'policy_notice': '未提交策略时仅保存未配置模板；不会从出现过的路径推断授权。请在“解析与审查”中配置策略后审查。'}


def change_policy(store, data):
    from .risk_review import validate_risk_policy
    from .safety_workbench import unique
    cid = data['case_id']
    policy = validate_risk_policy(data['risk_policy'])
    case, _, _ = store._material(cid)
    target = store.folder / 'risk-policy-history' / cid / unique('policy')
    write_json(target / 'before.json', case.get('risk_policy'))
    write_json(target / 'after.json', policy)
    with store.lock:
        saved = store._find(cid)
        saved['risk_policy'] = policy
        saved.setdefault('risk_policy_history', []).append(store._relative(target / 'after.json'))
        store._save()
    # Old jobs remain on disk but cannot supply findings or semantic facts for
    # the new policy version. Automatic checks are recomputed immediately.
    return {'case': store._public_case(store._find(cid)), 'report': store.risk_view(cid),
            'semantics': store.semantics(cid), 'policy_sha256': digest(policy),
            'notice': '策略已保存。当前自动检查已更新；旧模型记录保留在历史中，请重新运行完整风险审查。'}
