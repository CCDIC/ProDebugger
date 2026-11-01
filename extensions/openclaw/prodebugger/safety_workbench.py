"""Local safety-audit workbench: capture/import -> compile/query -> policy report.

Run with the existing project virtual environment. All new evidence is written
below --data-dir; original captures and previous reports are never overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit
import uuid

from .record_bridge import build_bundle, digest, export_bundle, read_json, write_json

STATIC = Path(__file__).resolve().parents[1] / "web" / "static" / "safety"
MAX_BODY = 32 * 1024 * 1024
LIMITATIONS = [
    "规则、安全审计、工具运行状态、模型诊断与根因候选分别保存；候选不等于已证明根因。",
    "本策略通过只覆盖配置规则和可见轨迹；不保证未采集行为、隐藏推理或系统整体安全。",
    "演示实际执行本地受控工具并采集 OTLP，使用假数据，不是新的 OpenClaw agent 运行。",
]


def now():
    return datetime.now(timezone.utc).isoformat()


def unique(prefix):
    return prefix + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


class CaseLibraryConflict(ValueError):
    def __init__(self, message, code, case_ids=()):
        super().__init__(message)
        self.code, self.case_ids = code, list(case_ids)


def default_policy(root):
    import ntpath
    path_style = "windows" if ntpath.splitdrive(str(root))[0] else "posix"
    separator = "\\" if path_style == "windows" else "/"
    root = str(root).rstrip("/\\")
    return {
        "schema": "prodebugger-safety-policy/v1", "id": "local-tool-safety", "version": "1",
        "path_style": path_style, "working_directory": root, "allowed_roots": [root],
        "forbidden_paths": [root + separator + ".env", root + separator + ".ssh"],
        "allowed_network_hosts": [], "denied_tools": ["credential_dump"],
        "denied_commands": [{"id": "recursive-delete", "executable": "rm",
                             "require_any_args": ["-rf", "-fr", "-r", "--recursive"]}],
        "tools": {
            "read": {"kind": "read", "path_arg": "path"},
            "read_file": {"kind": "read", "path_arg": "path"},
            "write": {"kind": "write", "path_arg": "path"},
            "write_file": {"kind": "write", "path_arg": "path"},
            "http_request": {"kind": "network", "url_arg": "url"},
            "web_fetch": {"kind": "network", "url_arg": "url"},
            "exec": {"kind": "command", "command_arg": "command"},
        },
    }


class SafetyStore:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs = {}
        self._bundles = {}
        self._diagnoses = None
        self._risk_jobs = None
        self._risk_reports = {}
        import hashlib
        self._risk_engine_sha = hashlib.sha256(b"".join(
            (Path(__file__).parent / name).read_bytes() for name in
            ("risk_review.py", "risk_evidence.py", "privacy_evidence.py", "risk_policy_contract.py", "openclaw_semantics.py", "safety_audit.py", "query_dsl.py", "session_sources.py"))).hexdigest()
        self.index_file = self.folder / "index.json"
        self.index = read_json(self.index_file) if self.index_file.is_file() else {
            "schema": "safety-workbench-index/v1", "cases": [], "active_run": None,
            "policy": default_policy(self.folder / "workspace"),
        }
        if "workspace_case_ids" not in self.index:
            protected = self._protected_case_ids()
            cases = self.index["cases"]
            baseline = max((c.get("created_at", "") for c in cases if c["id"] in protected), default="")
            self.index["workspace_case_ids"] = [c["id"] for c in cases if not protected or
                c["id"] in protected or (c.get("origin") == "user_import" and c.get("created_at", "") > baseline)]
            self._save()

    def _relative(self, path):
        return Path(path).resolve().relative_to(self.folder).as_posix()

    def _resolve(self, path):
        candidate = (self.folder / path).resolve()
        if not candidate.is_relative_to(self.folder):
            raise ValueError("saved source is outside the workbench data directory")
        return candidate

    def _save(self):
        write_json(self.index_file, self.index)

    def _protected_case_ids(self):
        """The two original privacy cases are retained even when imports are removed."""
        path = self.folder / "privacy-showcase.json"
        if not path.is_file():
            return set()
        manifest = read_json(path)
        protected = {item["case_id"] for item in manifest.get("cases", []) if item.get("case_id")}
        if manifest.get("base_case_id"):
            protected.add(manifest["base_case_id"])
        return protected

    @staticmethod
    def _case_ids(data):
        ids = data.get("case_ids")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 10000 or
                any(not isinstance(cid, str) or not cid for cid in ids) or len(set(ids)) != len(ids)):
            raise ValueError("case_ids must contain 1–10000 distinct saved trace IDs.")
        return ids

    def _check_library_idle(self, case_ids):
        """Called with store.lock, also held by capture and model queue submission."""
        from .diagnosis_jobs import TERMINAL
        if any(job.get("state") in {"running", "queued"} for job in self.jobs.values()):
            raise CaseLibraryConflict("A capture is in progress. Try again when it finishes.", "active_capture")
        active = set()
        for attribute, folder in (("_diagnoses", "diagnosis-jobs"), ("_risk_jobs", "risk-jobs")):
            manager = getattr(self, attribute)
            if manager is not None:
                with manager.lock:
                    jobs = [dict(job) for job in manager.jobs.values()]
            else:
                # Do not initialize a queue just to delete: initialization changes
                # historical nonterminal jobs to interrupted and starts a worker.
                jobs = [read_json(path) for path in (self.folder / folder).glob("*/job.json")]
            active.update(job.get("case_id") for job in jobs if job.get("case_id") in case_ids
                          and job.get("status") not in TERMINAL)
        if active:
            raise CaseLibraryConflict("Selected traces have queued or running analysis. Wait or cancel it first.",
                                      "active_analysis", sorted(active))

    def deleted_cases(self):
        with self.lock:
            cases = [{key: item.get(key) for key in
                      ("id", "title", "origin", "origin_label", "created_at", "deleted_at")}
                     for item in self.index.get("deleted_cases", [])]
            for case in cases:
                case["case_id"] = case["id"]
            return {"cases": sorted(cases, key=lambda c: c["deleted_at"], reverse=True), "count": len(cases)}

    def delete_cases(self, data):
        ids = self._case_ids(data)
        selected = set(ids)
        with self.lock:
            missing = selected - {case["id"] for case in self.index["cases"]}
            if missing:
                raise ValueError("Unknown or already deleted trace IDs: " + ", ".join(sorted(missing)))
            protected = selected & self._protected_case_ids()
            if protected:
                raise CaseLibraryConflict("The original showcase traces are protected and cannot be deleted.",
                                          "protected_case", sorted(protected))
            self._check_library_idle(selected)
            timestamp, operation_id = now(), unique("delete")
            moved = [dict(case, deleted_at=timestamp, deleted_operation_id=operation_id, deleted_from_index=i)
                     for i, case in enumerate(self.index["cases"]) if case["id"] in selected]
            updated = dict(self.index,
                cases=[case for case in self.index["cases"] if case["id"] not in selected],
                deleted_cases=[*self.index.get("deleted_cases", []), *moved],
                workspace_case_ids=[cid for cid in self.index["workspace_case_ids"] if cid not in selected],
                case_library_events=[*self.index.get("case_library_events", []),
                    {"id": operation_id, "operation": "delete", "case_ids": ids, "at": timestamp}])
            # Atomic replace first; a failed disk write must not change the live library.
            write_json(self.index_file, updated)
            self.index = updated
            self._bundles.clear()
            self._risk_reports.clear()
            return {"deleted": ids, "operation_id": operation_id, "remaining_count": len(updated["cases"]),
                    "deleted_count": len(updated["deleted_cases"])}

    def restore_cases(self, data):
        ids = self._case_ids(data)
        selected = set(ids)
        with self.lock:
            deleted = {case["id"]: case for case in self.index.get("deleted_cases", [])}
            if selected - deleted.keys():
                raise ValueError("Unknown deleted trace IDs: " + ", ".join(sorted(selected - deleted.keys())))
            if selected & {case["id"] for case in self.index["cases"]}:
                raise CaseLibraryConflict("A trace with the same ID already exists.", "duplicate_case", ids)
            self._check_library_idle(selected)
            for cid in ids:
                # Restoring a damaged archive must not create a broken visible case.
                for key in ("source_path", "report_path"):
                    if not self._resolve(deleted[cid][key]).is_file():
                        raise CaseLibraryConflict("Saved source or report is missing; restore was not applied.",
                                                  "missing_artifact", [cid])
            cases = list(self.index["cases"])
            for old in sorted((deleted[cid] for cid in ids), key=lambda c: c["deleted_from_index"]):
                restored = {k: v for k, v in old.items()
                            if k not in {"deleted_at", "deleted_operation_id", "deleted_from_index"}}
                cases.insert(min(old["deleted_from_index"], len(cases)), restored)
            operation_id = unique("restore")
            updated = dict(self.index, cases=cases,
                deleted_cases=[case for case in self.index.get("deleted_cases", []) if case["id"] not in selected],
                workspace_case_ids=list(dict.fromkeys([*self.index["workspace_case_ids"], *ids])),
                case_library_events=[*self.index.get("case_library_events", []),
                    {"id": operation_id, "operation": "restore", "case_ids": ids, "at": now()}])
            write_json(self.index_file, updated)
            self.index = updated
            self._bundles.clear()
            self._risk_reports.clear()
            return {"restored": ids, "operation_id": operation_id, "remaining_count": len(cases),
                    "deleted_count": len(updated["deleted_cases"])}

    def _public_case(self, case):
        result = {key: case[key] for key in ("id", "title", "origin", "origin_label", "created_at", "policy")}
        result["report"] = read_json(self._resolve(case["report_path"]))
        result["status"] = result["report"]["status"]
        result["report_url"] = "/api/safety/reports/" + case["id"]
        result["deletion_protected"] = case["id"] in self._protected_case_ids()
        result["deletion_protection_reason"] = "original_showcase" if result["deletion_protected"] else None
        result["workspace_visible"] = case["id"] in self.index["workspace_case_ids"]
        for key in ("source_category", "is_constructed", "validation_suite", "session_available", "risk_validation", "validation_group"):
            if key in case:
                result[key] = case[key]
        if result.get("source_category") == "openclaw_session_import":
            result["origin_label"] = "OpenClaw 会话格式导入 · 真实性见来源核对"
        if case.get("risk_policy"):
            risk = self.risk_report(case["id"])
            latest = self.latest_risk_review(case["id"])
            result["risk_summary"] = {"status": risk.get("status"),
                # Risk checks reuse existing safety flags instead of making a
                # duplicate finding. Keep those reused discoveries in the UI
                # count, otherwise an actual forbidden read displays "0".
                "findings": sum(f.get("status") == "confirmed" for f in risk.get("findings", []))
                    + len({f['id'] for f in result['report'].get('flags', [])}),
                "unknown": sum(f.get("status") in {"unknown", "suspected"} for f in risk.get("findings", [])),
                "model_status": latest.get("status") if latest else None}
        return result

    def _bundle(self, source, *, case_id=None):
        from .analysis_version import selected_compilation
        selection = selected_compilation(self, case_id) if case_id else None
        key = digest([source, selection['receipt'] if selection else None])
        with self.lock:
            if key not in self._bundles:
                if len(self._bundles) >= 64:
                    self._bundles.pop(next(iter(self._bundles)))
                bundle = build_bundle(source, online=False,
                    audited=selection['audited'] if selection else None,
                    semantics=selection['semantics'] if selection else None)
                if selection:
                    expected = selection['receipt']
                    if digest(bundle.snapshot()) != expected.get('record_sha256'):
                        raise ValueError('已保存的模型解析与当前重放不一致，未混用模型结果。')
                    # Fingerprint also includes the whole source tree, including
                    # unrelated view helpers. Retain the worker version after
                    # exact canonical-record and scoped implementation checks.
                    bundle.fingerprint = expected['canonical_fingerprint']
                self._bundles[key] = bundle
            return self._bundles[key]

    def _material(self, case_id):
        with self.lock:
            saved = self._find(case_id)
            if saved is None:
                raise KeyError("没有找到这条轨迹。")
            case = dict(saved)
            source = read_json(self._resolve(case["source_path"]))
            report = read_json(self._resolve(case["report_path"]))
        return case, source, report

    def diagnoses(self):
        from .diagnosis_jobs import DiagnosisJobs
        with self.lock:
            if self._diagnoses is None:
                self._diagnoses = DiagnosisJobs(self.folder)
            return self._diagnoses

    def latest_diagnosis(self, case_id):
        jobs = self.diagnoses().list(case_id)
        return max(jobs, key=lambda job: (job["created_at"], job["id"])) if jobs else None

    def analysis(self, case_id, *, include_diagnosis=True):
        from .trace_analysis import analyze_source
        case, source, report = self._material(case_id)
        job = self.latest_diagnosis(case_id) if include_diagnosis else None
        risk = {"automatic": self.risk_report(case_id),
                "model": self.latest_risk_review(case_id) if include_diagnosis else None,
                "policy": case.get("risk_policy")}
        bundle = self._bundle(source, case_id=case_id)
        analysis = analyze_source(source, report, case, bundle=bundle, diagnosis=job, risk_review=risk)
        analysis['analysis_version'] = bundle.fingerprint
        return analysis

    def semantics(self, case_id):
        from .analysis_version import describe
        return describe(self, case_id)

    def risk_report(self, case_id):
        from .risk_review import review_risks
        case, source, report = self._material(case_id)
        policy = case.get("risk_policy")
        bundle = self._bundle(source, case_id=case_id)
        key = digest([source, policy, report, self._risk_engine_sha, bundle.fingerprint])
        with self.lock:
            if key not in self._risk_reports:
                if len(self._risk_reports) >= 128:
                    self._risk_reports.pop(next(iter(self._risk_reports)))
                self._risk_reports[key] = review_risks(source, policy,
                    bundle=bundle if policy else None, safety_report=report)
                self._risk_reports[key]['analysis_version'] = bundle.fingerprint
                if policy:
                    self._risk_reports[key]["engine_sha256"] = self._risk_engine_sha
                    target = self.folder / "risk-audits" / case_id / key / "report.json"
                    write_json(target, self._risk_reports[key])
                    saved = self._find(case_id)
                    saved["risk_report_path"] = self._relative(target)
                    self._save()
            return self._risk_reports[key]

    def risk_jobs(self):
        from .risk_jobs import RiskReviewJobs
        with self.lock:
            if self._risk_jobs is None:
                self._risk_jobs = RiskReviewJobs(self.folder)
            return self._risk_jobs

    def latest_risk_review(self, case_id):
        if self._risk_jobs is None and not (self.folder / "risk-jobs").exists():
            return None
        from .analysis_version import matching_jobs
        jobs = matching_jobs(self, case_id)
        return self.risk_jobs().get(jobs[0]['id']) if jobs else None

    def risk_view(self, case_id):
        case, _, _ = self._material(case_id)
        from .risk_validation import case_evaluation
        return {"automatic": self.risk_report(case_id), "model": self.latest_risk_review(case_id),
                "policy": case.get("risk_policy"), "evaluation": case_evaluation(self, case_id)}

    def start_risk_review(self, data):
        ids = data.get("case_ids")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 20
                or any(not isinstance(cid, str) for cid in ids) or len(set(ids)) != len(ids)):
            raise ValueError("完整风险审查需要 1–20 个互不重复的已保存案例 ID。")
        with self.lock:
            materials = [(cid, self._material(cid)) for cid in ids]
            if any(not case.get("risk_policy") for _, (case, _, _) in materials):
                raise ValueError("请先导入包含 risk_policy 的轨迹包；缺少风险策略时不会默认授权或安全。")
            manager = self.risk_jobs()
            with manager.lock:
                pending = [j for j in manager.jobs.values() if j["status"] not in
                           {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}]
                if len(pending) + len(set(ids) - {j["case_id"] for j in pending}) > 20:
                    raise ValueError("风险审查队列最多容纳 20 条。")
                jobs = [manager.enqueue(cid, source, case["risk_policy"], self.risk_report(cid))
                        for cid, (case, source, _) in materials]
        return {"jobs": jobs, "errors": [], "configuration": manager.configuration}

    def start_diagnosis(self, data):
        ids = data.get("case_ids")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 20
                or any(not isinstance(cid, str) for cid in ids) or len(set(ids)) != len(ids)):
            raise ValueError("完整诊断需要 1–20 个互不重复的已保存案例 ID。")
        # Validate the entire batch before submitting any model job.
        with self.lock:
            materials = [(cid, self._material(cid)) for cid in ids]
            manager = self.diagnoses()
            with manager.lock:
                pending = [j for j in manager.jobs.values() if j["status"] not in
                           {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}]
                already = {j["case_id"] for j in pending}
                if len(pending) + len(set(ids) - already) > 20:
                    raise ValueError("诊断队列最多容纳 20 条，请等待或取消现有任务。")
                jobs = [manager.enqueue(cid, source, case["policy"],
                        self.analysis(cid, include_diagnosis=False))
                        for cid, (case, source, _) in materials]
        return {"jobs": jobs, "configuration": manager.configuration}

    def state(self):
        with self.lock:
            active_run = self.index["active_run"]
            visible = [item for item in self.index["cases"]
                       if item["origin"] != "controlled_tool_runtime" or
                       (active_run and item["id"].startswith(active_run + "-"))]
            visible.sort(key=lambda item: (item["origin"] == "historical_openclaw", item["created_at"]))
            cases = [self._public_case(item) for item in visible]
            counts = Counter(item["status"] for item in cases)
            flag_count = sum(len(item["report"]["flags"]) for item in cases)
            rules = Counter(flag["rule_id"] for item in cases for flag in item["report"]["flags"])
            return {
                "schema": "safety-workbench-state/v1", "policy": self.index["policy"],
                "cases": cases, "active_run": self.index["active_run"],
                "workspace_case_ids": list(self.index["workspace_case_ids"]),
                "protected_case_ids": sorted(self._protected_case_ids()),
                "deleted_count": len(self.index.get("deleted_cases", [])),
                "archived_cases": len(self.index["cases"]) - len(visible),
                "summary": {"total": len(cases), "unsafe": counts["unsafe"], "safe": counts["safe"],
                            "unknown": counts["unknown"], "flags": flag_count},
                "insights": [
                    {"title": "已记录的策略违规", "detail": f"{counts['unsafe']} 条轨迹产生 {flag_count} 个证据 flag。受阻尝试会与执行效果分开显示。", "severity": "high" if flag_count else "info"},
                    {"title": "待补充的采集证据", "detail": f"{counts['unknown']} 条轨迹暂不能通过策略审计；具体缺口可在各条轨迹中查看。", "severity": "warning" if counts["unknown"] else "info"},
                    {"title": "规则命中统计", "detail": "；".join(f"{key}: {count}" for key, count in sorted(rules.items())) or "当前无已确认规则命中。", "severity": "info"},
                ],
                "limitations": LIMITATIONS,
            }

    def _find(self, case_id):
        return next((item for item in self.index["cases"] if item["id"] == case_id), None)

    def parsed(self, case_id):
        """Compile only a registered saved source; do not accept caller paths."""
        from .parsed_view import parsed_source
        with self.lock:
            case = self._find(case_id)
            if case is None:
                raise KeyError("没有找到这条轨迹。")
            case = dict(case)
            source = read_json(self._resolve(case["source_path"]))
            report = read_json(self._resolve(case["report_path"]))
        bundle = self._bundle(source, case_id=case_id)
        result = parsed_source(source, report, case, bundle=bundle)
        result['analysis_version'] = bundle.fingerprint
        result['openclaw_semantics'] = bundle.snapshot().get('openclaw_semantics')
        result['semantic_fact_metadata'] = bundle.snapshot().get('semantic_fact_metadata', {})
        return result

    def graph(self, case_id):
        from .trace_explorer import graph_source
        with self.lock:
            case = self._find(case_id)
            if case is None:
                raise KeyError("没有找到这条轨迹。")
            case = dict(case)
            source = read_json(self._resolve(case["source_path"]))
            report = read_json(self._resolve(case["report_path"]))
        bundle = self._bundle(source, case_id=case_id)
        result = graph_source(source, report, case, bundle=bundle, analysis=self.analysis(case_id))
        result['analysis_version'] = bundle.fingerprint
        return result

    def sample(self, request):
        from .trace_explorer import sample_cases
        cases = self.state()["cases"]
        if 'case_ids' in request:
            eligible = request['case_ids']
            if (not isinstance(eligible, list) or len(eligible) > 10000
                    or any(not isinstance(cid, str) for cid in eligible)
                    or not set(eligible) <= {case['id'] for case in cases}):
                raise ValueError('抽样范围需要已保存案例的 ID 列表。')
            cases = [case for case in cases if case['id'] in set(eligible)]
        if request.get("risk_validation_only") is True:
            cases = [case for case in cases if case.get("risk_validation")]
        group = request.get("risk_validation_group")
        if group is not None:
            if group not in {"development", "holdout"}:
                raise ValueError("未知风险评测集")
            cases = [case for case in cases if case.get("risk_validation")
                     and case.get("validation_group", "development") == group]
        result = sample_cases(cases, {key: value for key, value in request.items()
                                     if key not in {"risk_validation_only", "risk_validation_group", 'case_ids'}})
        if request.get("risk_validation_only") is True:
            result["filters"]["risk_validation_only"] = True
        if group is not None:
            result["filters"]["risk_validation_group"] = group
        return result

    def add_source(self, source, policy, *, title, origin="user_import", folder=None, case_id=None, risk_policy=None):
        from .safety_audit import audit_source, validate_policy
        if not isinstance(source, dict) or source.get("task_type") != "openclaw":
            raise ValueError("请上传已导入的 OpenClaw input.json（task_type 必须为 openclaw）。")
        if not isinstance(source.get("steps"), list) or not 1 <= len(source["steps"]) <= 500:
            raise ValueError("一次导入需要 1–500 个规范化步骤；较长轨迹请先按 trace 切分。")
        policy = validate_policy(policy)
        if risk_policy is not None:
            from .risk_review import validate_risk_policy
            risk_policy = validate_risk_policy(risk_policy)
        case_id = case_id or unique("import")
        from .record_bridge import valid_id
        valid_id(case_id)
        with self.lock:
            if self._find(case_id) or any(c["id"] == case_id for c in self.index.get("deleted_cases", [])):
                raise ValueError("案例 ID 已存在；不会覆盖旧轨迹。")
        bundle = self._bundle(source)
        report = audit_source(source, policy, bundle=bundle)
        target = Path(folder) if folder else self.folder / "imports" / case_id
        target.mkdir(parents=True, exist_ok=False)
        write_json(target / "input.json", source)
        write_json(target / "policy.json", policy)
        write_json(target / "report.json", report)
        labels = {"controlled_tool_runtime": "新采集 · 受控工具运行", "historical_openclaw": "历史真实 OpenClaw", "user_import": "用户导入 · 完整性依赖输入声明"}
        case = {
            "id": case_id, "title": str(title or source["trajectory_id"])[:180], "origin": origin,
            "origin_label": labels[origin], "created_at": now(), "policy": policy,
            "source_path": self._relative(target / "input.json"),
            "report_path": self._relative(target / "report.json"),
            "history": [self._relative(target / "report.json")],
        }
        if source.get("session"):
            case.update(session_available=True, source_category="openclaw_session_import",
                        origin_label="OpenClaw 会话格式导入 · 真实性见来源核对")
        if risk_policy is not None:
            from .risk_review import review_risks
            case["risk_policy"] = risk_policy
            write_json(target / "risk-policy.json", risk_policy)
            risk = review_risks(source, risk_policy, bundle=bundle, safety_report=report)
            write_json(target / "risk-report.json", risk)
        else:
            risk = None
        from .trace_analysis import analyze_source
        automatic = analyze_source(source, report, case, bundle=bundle,
            risk_review={"automatic": risk, "model": None, "policy": risk_policy} if risk else None)
        write_json(target / "analysis.json", automatic)
        export_bundle(bundle, target / "compiled")
        case["analysis_path"] = self._relative(target / "analysis.json")
        with self.lock:
            self.index["cases"].append(case)
            self.index["workspace_case_ids"].append(case_id)
            self._save()
            return self._public_case(case)

    def reaudit(self, case_id, policy):
        from .safety_audit import audit_source, validate_policy
        with self.lock:
            case = self._find(case_id)
            if case is None:
                raise KeyError("没有找到这条轨迹。")
            source = read_json(self._resolve(case["source_path"]))
        policy = validate_policy(policy)
        report = audit_source(source, policy, bundle=self._bundle(source, case_id=case_id))
        target = self.folder / "reaudits" / unique("audit")
        target.mkdir(parents=True, exist_ok=False)
        write_json(target / "policy.json", policy)
        write_json(target / "report.json", report)
        with self.lock:
            case["policy"] = policy
            case["report_path"] = self._relative(target / "report.json")
            case["history"].append(case["report_path"])
            self._save()
            result = {"case": self._public_case(case), "report": report}
        result["analysis"] = self.analysis(case_id)
        write_json(target / "analysis.json", self.analysis(case_id, include_diagnosis=False))
        return result

    def run_demo(self, stage=None):
        from .safety_demo_capture import capture_demo
        notify = stage or (lambda message: None)
        run_id = unique("capture")
        run_dir = self.folder / "captures" / run_id
        notify("执行本地受控工具，并经 OTLP 接收器保存轨迹")
        manifest = capture_demo(run_dir)

        def manifest_path(value):
            value = Path(value)
            return value if value.is_absolute() else run_dir / value

        policy = read_json(manifest_path(manifest["policy_path"]))
        summaries = []
        notify("导入轨迹，编译事实，查询原文并执行策略规则")
        for item in manifest["cases"]:
            source = read_json(manifest_path(item["source_path"]))
            case = self.add_source(source, policy, title=item["title"],
                origin="controlled_tool_runtime", folder=run_dir / "audited" / item["id"],
                case_id=run_id + "-" + item["id"])
            summaries.append({"id": case["id"], "scenario": item.get("scenario"),
                "status": case["status"], "expected_verdict": item.get("expected_verdict"),
                "flags": len(case["report"]["flags"]),
                "matched_expected": case["status"] == item.get("expected_verdict")})
        receipt = {"schema": "safety-demo-acceptance/v1", "run_id": run_id,
            "data_origin": "controlled_tool_runtime", "not_openclaw_agent_run": True,
            "model_calls": 0, "cases": summaries,
            "passed": all(item["matched_expected"] for item in summaries),
            "note": "预期结果仅用于验收，不传给审计引擎；不是安全检测准确率评测。"}
        write_json(run_dir / "safety-acceptance.json", receipt)
        with self.lock:
            self.index["active_run"] = run_id
            self.index["policy"] = policy
            self._save()
        notify("采集、查询和策略审计已完成")
        return receipt

    def start_demo(self):
        with self.lock:
            if any(job["state"] == "running" for job in self.jobs.values()):
                raise ValueError("已有一次采集正在进行，请等待完成后再运行。")
            job = {"job_id": unique("job"), "state": "running", "stage": "准备本地受控采集", "created_at": now()}
            self.jobs[job["job_id"]] = job

        def stage(message):
            with self.lock:
                job["stage"] = message

        def run():
            try:
                receipt = self.run_demo(stage)
                with self.lock:
                    job.update(state="finished", receipt=receipt,
                               case_ids=[item["id"] for item in receipt["cases"]])
            except Exception as exc:
                with self.lock:
                    job.update(state="failed", error=str(exc), stage="采集或审计未完成，已有结果保留")
        threading.Thread(target=run, daemon=True).start()
        return dict(job)

    def seed_historical(self, folder):
        from .otel_import import convert, load_export, load_task, spans_from_export
        folder = Path(folder)
        for case_dir in sorted(folder.glob("case-*")):
            cid = "historical-" + case_dir.name
            with self.lock:
                if self._find(cid):
                    continue
            if not (case_dir / "trace.json").is_file() or not (case_dir / "task.json").is_file():
                continue
            spans = spans_from_export(load_export(case_dir / "trace.json"))
            task = load_task(case_dir / "task.json", None)
            source, _ = convert(spans, task, trajectory_id=cid)
            action = json.loads(source["steps"][0]["parts"]["action"])
            path = action.get("arguments", {}).get("path", "")
            import ntpath
            import posixpath
            library = ntpath if ntpath.splitdrive(path)[0] else posixpath
            root = library.dirname(library.dirname(path))
            if not root:
                raise ValueError("历史轨迹缺少可配置的明确工作目录。")
            policy = default_policy(root)
            policy["id"] = "historical-workspace-policy"
            self.add_source(source, policy, title="原 OpenClaw " + case_dir.name,
                origin="historical_openclaw", case_id=cid,
                folder=self.folder / "historical" / cid)


class SafetyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "ProDebuggerSafety/1"

    def log_message(self, *_):
        pass

    def _send(self, value, status=200, content_type="application/json; charset=utf-8"):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        store = self.server.store
        try:
            if path == '/api/safety/privacy-showcase':
                from .privacy_showcase import manifest
                return self._send(manifest(store))
            if path.startswith('/api/safety/privacy-showcase/'):
                from .privacy_showcase import evaluation, reference
                parts = path.split('/')
                if len(parts) == 6 and parts[-1] in {'evaluation', 'reference'}:
                    try:
                        return self._send((evaluation if parts[-1] == 'evaluation' else reference)(store, parts[-2]))
                    except ValueError as exc:
                        return self._send({'error': str(exc)}, 409)
            if path == '/api/safety/cloud-showcase':
                return self._send(read_json(store.folder / 'cloud-showcase.json'))
            if path.startswith('/api/safety/cloud-showcase/'):
                from .cloud_evaluation import evaluation, reference
                parts = path.split('/')
                if len(parts) == 6 and parts[-1] in {'evaluation', 'reference'}:
                    try:
                        return self._send((evaluation if parts[-1] == 'evaluation' else reference)(store, parts[-2]))
                    except ValueError as exc:
                        return self._send({'error': str(exc)}, 409)
            if path.startswith('/api/safety/semantics/'):
                return self._send(store.semantics(path.removeprefix('/api/safety/semantics/')))
            if path.startswith('/api/safety/source/'):
                return self._send(store._material(path.removeprefix('/api/safety/source/'))[1])
            if path == "/api/safety/showcase":
                from .showcase import manifest
                return self._send(manifest(store))
            if path.startswith("/api/safety/showcase/cases/"):
                from .showcase import source_map
                parts = path.split("/")
                if len(parts) == 7 and parts[-1] == "source-map":
                    return self._send(source_map(store, parts[-2]))
                if len(parts) == 7 and parts[-1] == "source":
                    return self._send(store._material(parts[-2])[1])
            if path.startswith("/api/safety/showcase/"):
                from .showcase import evaluation, reference
                parts = path.split("/")
                if len(parts) == 6 and parts[-1] in {"evaluation", "reference"}:
                    try:
                        return self._send((reference if parts[-1] == "reference" else evaluation)(store, parts[-2]))
                    except ValueError as exc:
                        return self._send({"error": str(exc)}, 409)
            if path == "/api/safety/state":
                return self._send(store.state())
            if path == "/api/safety/cases/deleted":
                return self._send(store.deleted_cases())
            if path == "/api/safety/health":
                import os
                return self._send({"schema": "prodebugger-service/v1", "pid": os.getpid(),
                    "source": str(Path(__file__).resolve()), "data_dir": str(store.folder),
                    "capture_jobs": list(store.jobs.values()), "static_dir": str(STATIC)})
            if path == "/api/safety/risk/jobs":
                manager = store.risk_jobs()
                return self._send({"jobs": manager.list(), "configuration": manager.configuration})
            if path.startswith("/api/safety/risk/jobs/"):
                return self._send(store.risk_jobs().get(path.rsplit("/", 1)[-1]))
            if path == "/api/safety/risk/validation":
                from .risk_validation import suite_view
                return self._send(suite_view(store))
            if path.startswith("/api/safety/risk/validation/") and path.endswith("/reference"):
                from .risk_validation import reference_view
                try:
                    return self._send(reference_view(store, path.split("/")[-2]))
                except ValueError as exc:
                    return self._send({"error": str(exc)}, 409)
            if path.startswith("/api/safety/risk/"):
                return self._send(store.risk_view(path.removeprefix("/api/safety/risk/")))
            if path == "/api/safety/diagnosis/jobs":
                manager = store.diagnoses()
                return self._send({"jobs": manager.list(), "configuration": manager.configuration})
            if path.startswith("/api/safety/diagnosis/jobs/"):
                return self._send(store.diagnoses().get(path.rsplit("/", 1)[-1]))
            if path.startswith("/api/safety/analysis/"):
                return self._send(store.analysis(path.removeprefix("/api/safety/analysis/")))
            if path == "/api/safety/validation":
                from .validation_evaluation import suites_view
                return self._send(suites_view(store))
            if path.startswith("/api/safety/validation/") and path.endswith("/reference"):
                from .validation_evaluation import reference_view
                try:
                    return self._send(reference_view(store, path.split("/")[-2]))
                except ValueError as exc:
                    return self._send({"error": str(exc)}, 409)
            if path == "/api/safety/policy":
                return self._send(store.index["policy"])
            if path == "/api/safety/integrations":
                from .trace_explorer import integrations
                return self._send(integrations())
            if path.startswith("/api/safety/graph/"):
                case_id = path.removeprefix("/api/safety/graph/")
                try:
                    return self._send(store.graph(case_id))
                except KeyError:
                    return self._send({"error": "没有找到这条轨迹。"}, 404)
            if path.startswith("/api/safety/parsed/"):
                case_id = path.removeprefix("/api/safety/parsed/")
                try:
                    return self._send(store.parsed(case_id))
                except KeyError:
                    return self._send({"error": "没有找到这条轨迹。"}, 404)
            if path.startswith("/api/safety/jobs/"):
                with store.lock:
                    job = store.jobs.get(path.rsplit("/", 1)[-1])
                    return self._send(job or {"error": "没有找到任务。"}, 200 if job else 404)
            if path.startswith("/api/safety/reports/"):
                with store.lock:
                    case = store._find(path.rsplit("/", 1)[-1])
                    if case:
                        return self._send(read_json(store._resolve(case["report_path"])))
                return self._send({"error": "没有找到报告。"}, 404)
            relative = path.removeprefix("/safety/") if path.startswith("/safety/") else path.lstrip("/")
            if not relative:
                relative = "index.html"
            file = (STATIC / relative).resolve()
            if not file.is_relative_to(STATIC.resolve()) or not file.is_file():
                return self._send({"error": "资源不存在。"}, 404)
            mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}.get(file.suffix, mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            return self._send(file.read_bytes(), content_type=mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        except KeyError:
            return self._send({"error": "没有找到这条已保存的记录。"}, 404)
        except ConnectionError:
            return
        except Exception:
            return self._send({"error": "无法读取这份已保存的结果，请查看本地数据目录。"}, 500)

    def _body(self):
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise ValueError("只接受本地工作台请求。")
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            raise ValueError("拒绝跨来源写入。")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("请使用固定长度 JSON 请求。")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ValueError("请求必须是 application/json。")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= MAX_BODY:
            raise ValueError("JSON 请求大小必须在 1 字节至 32 MB 之间。")
        self.connection.settimeout(20)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("JSON 请求不完整。")
        data = json.loads(raw, parse_constant=lambda v: (_ for _ in ()).throw(ValueError("非有限 JSON 数值")))
        if not isinstance(data, dict):
            raise ValueError("请求必须是 JSON 对象。")
        return data

    def do_POST(self):
        try:
            data = self._body()
            path = urlsplit(self.path).path
            if path == "/api/safety/cases/delete":
                return self._send(self.server.store.delete_cases(data))
            if path == "/api/safety/cases/restore":
                return self._send(self.server.store.restore_cases(data))
            if path == '/api/safety/import/signoz':
                from .signoz_workbench import import_request
                return self._send(import_request(self.server.store, data), 201)
            if path == '/api/safety/risk-policy':
                from .signoz_workbench import change_policy
                return self._send(change_policy(self.server.store, data))
            if path == "/api/safety/risk/review":
                return self._send(self.server.store.start_risk_review(data), 202)
            if path.startswith("/api/safety/risk/jobs/") and path.endswith("/cancel"):
                return self._send(self.server.store.risk_jobs().cancel(path.split("/")[-2]))
            if path == "/api/safety/diagnosis":
                return self._send(self.server.store.start_diagnosis(data), 202)
            if path.startswith("/api/safety/diagnosis/jobs/") and path.endswith("/cancel"):
                return self._send(self.server.store.diagnoses().cancel(path.split("/")[-2]))
            if path == "/api/safety/sample":
                return self._send(self.server.store.sample(data))
            if path == "/api/safety/live/events":
                from .trace_explorer import live_stub
                return self._send(live_stub(), 501)
            if path == "/api/safety/demo":
                return self._send(self.server.store.start_demo(), 202)
            if path == "/api/safety/audit":
                return self._send(self.server.store.reaudit(data["case_id"], data["policy"]))
            if path == "/api/safety/import":
                source = data["source"]
                if "native_transcript" in data:
                    if not isinstance(data["native_transcript"], dict):
                        raise ValueError("native_transcript 必须是已导出的 JSON 对象，不能传本地文件路径。")
                    from .session_import import convert_session
                    source, _ = convert_session(source, data["native_transcript"])
                case = self.server.store.add_source(source, data["policy"], title=data.get("title"),
                    risk_policy=data.get("risk_policy"))
                return self._send({"case": case, "report": case["report"],
                                   "analysis": self.server.store.analysis(case["id"], include_diagnosis=False)}, 201)
            return self._send({"error": "接口不存在。"}, 404)
        except ConnectionError:
            return
        except CaseLibraryConflict as exc:
            return self._send({"error": str(exc), "code": exc.code, "case_ids": exc.case_ids}, 409)
        except (ValueError, TypeError, KeyError) as exc:
            return self._send({"error": str(exc)}, 400)
        except Exception as exc:
            return self._send({"error": "审计未完成：" + str(exc)}, 500)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8783)
    parser.add_argument("--validation-suite", type=Path, action="append", default=[])
    parser.add_argument("--historical-dir", type=Path)
    parser.add_argument("--seed-demo", action="store_true")
    parser.add_argument("--demo-only", action="store_true", help="capture, import and audit once, then exit")
    args = parser.parse_args(argv)
    store = SafetyStore(args.data_dir)
    if args.validation_suite:
        from .validation_evaluation import register_suite
        for path in args.validation_suite:
            register_suite(store, path)
    if args.historical_dir:
        store.seed_historical(args.historical_dir)
    if args.demo_only or (args.seed_demo and not store.index["active_run"]):
        receipt = store.run_demo(lambda stage: print(stage, flush=True))
        print(json.dumps(receipt, ensure_ascii=False), flush=True)
        if args.demo_only:
            return 0 if receipt["passed"] else 1
    server = SafetyHTTPServer(("127.0.0.1", args.port), Handler)
    server.store = store
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_address[1]}/", "data_dir": str(store.folder)}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if store._diagnoses is not None:
            store._diagnoses.close()
        if store._risk_jobs is not None:
            store._risk_jobs.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
