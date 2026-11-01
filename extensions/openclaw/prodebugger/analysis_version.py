"""Select one replayable compiled version for UI, rules and risk review."""
from __future__ import annotations
from .record_bridge import digest, read_json


def matching_jobs(store, case_id, *, compiled=False):
    from .risk_jobs import implementation_versions, REVIEW_PROTOCOL
    case, source, _ = store._material(case_id)
    if store._risk_jobs is None and not (store.folder / 'risk-jobs').exists():
        return []
    manager = store.risk_jobs()
    versions = implementation_versions()
    jobs = [dict(j) for j in manager.jobs.values() if j['case_id'] == case_id
            and j.get('source_sha256') == digest(source)
            and j.get('policy_sha256') == digest(case.get('risk_policy') or {})
            and j.get('review_protocol') == REVIEW_PROTOCOL
            and j.get('implementation_versions') == versions]
    if compiled:
        jobs = [j for j in jobs if all((manager.folder / j['id'] / 'run' / p).is_file()
            for p in ('compilation_receipt.json', 'canonical_record.json', 'audited_parse.json', 'openclaw_semantics.json'))]
    return sorted(jobs, key=lambda j: (j['created_at'], j['id']), reverse=True)


def selected_compilation(store, case_id):
    jobs = matching_jobs(store, case_id, compiled=True)
    if not jobs:
        return None
    job = jobs[0]
    output = store.risk_jobs().folder / job['id'] / 'run'
    receipt = read_json(output / 'compilation_receipt.json')
    return {'job_id': job['id'], 'output': output, 'receipt': receipt,
            'audited': read_json(output / 'audited_parse.json'),
            'semantics': read_json(output / 'openclaw_semantics.json')}


def describe(store, case_id, bundle=None):
    case, source, _ = store._material(case_id)
    bundle = bundle or store._bundle(source, case_id=case_id)
    selection = selected_compilation(store, case_id)
    report = bundle.snapshot().get('openclaw_semantics') or {}
    return {'report': report, 'source': {'category': case.get('source_category'),
            'is_constructed': case.get('is_constructed', False), 'source_sha256': digest(source)},
        'import_audit': case.get('import_audit') or source.get('session', {}).get('coverage', {}),
        'job_id': selection['job_id'] if selection else None,
        'analysis_version': bundle.fingerprint,
        'version_kind': 'model_compiled' if selection else 'structural_offline',
        'same_version_for': ['parsed_entities', 'rules', 'risk_automatic', 'graph', 'model_evidence'],
        'policy_sha256': digest(case.get('risk_policy') or {}),
        'fact_count': len(bundle.projection.F),
        'semantic_fact_metadata': bundle.snapshot().get('semantic_fact_metadata', {}),
        'note': '语义解释保留原文引用；不是已验证的世界事实，也不包含未记录的内部思考。'}
