"""Serial, cancellable risk-review jobs. Original diagnosis jobs are untouched."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .diagnosis_jobs import DiagnosisJobs, TERMINAL, SOURCE_KEYS, model_configuration, now, read, write, sha, rows

ROOT = Path(__file__).resolve().parents[1]
# The original client reserves UTF-8 bytes conservatively, before provider usage
# is known. Real official exports carry multiple evidence copies; a bounded
# 1.8M byte reservation leaves room for final validated SUBMIT corrections.
# Turn/output/tool-call/wall-time caps remain enforced independently.
RISK_LIMITS = {"max_turns": 30, "trace_top_p": 4, "trace_top_k": 1,
               "max_input_tokens": 1800000, 'max_parse_calls': 6,
               'max_parse_input_tokens': 400000, 'max_parse_output_tokens': 24000,
               'parse_max_tokens': 4000}
REVIEW_PROTOCOL = "signoz-privacy-evidence-query-correction/v6"


def implementation_versions():
    paths = [Path(__file__), *(ROOT / "prodebugger" / name for name in
        ("risk_agent.py", "risk_trace.py", "risk_references.py", "risk_prompt_views.py", "risk_policy_contract.py", "privacy_evidence.py",
         "risk_query_provenance.py", "query_trace.py", "query_dsl.py", 'risk_scope.py',
         'record_bridge.py', 'openclaw_semantics.py', 'session_artifact.py',
         'session_sources.py', 'session_import.py', 'signoz_import.py', 'parse_audit.py',
         'risk_review.py', 'safety_audit.py', 'abstract_rules.py', 'template_parser.py')),
        *sorted((ROOT / "prodebugger/prompts").glob("risk_*.md"))]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def normal_context(policy, automatic):
    """Copy normal checks only. No titles, private experiment answers or diffs.

    Input source is separately restricted to the established SOURCE_KEYS contract.
    This deliberately does not read experiment directories or evaluation files.
    """
    selected = {}
    automatic = automatic or {}
    keys = {"id", "risk_type", "rule_id", "status", "explanation", "call_id", "span_id", "import_step",
            "fact_ids", "primary_fids", "evidence", "counterevidence", "missing", "effect", "source", "policy_refs",
            "policy_id", "step", "reason", "missing_information", "category", "checked", "applicable", "query_id"}
    for name in ("findings", "checks"):
        selected[name] = [{k: item[k] for k in keys if k in item} for item in automatic.get(name, []) if isinstance(item, dict)]
    selected["coverage"] = automatic.get("coverage", {})
    selected["limitations"] = automatic.get("limitations", [])
    selected["source_sha256"] = automatic.get("source_sha256")
    selected["policy_sha256"] = automatic.get("policy_sha256")
    if automatic.get("privacy"):
        selected["privacy"] = automatic["privacy"]
    return {"policy": policy or {}, "automatic": selected,
        "instruction": "Automatic findings are investigation leads only. Independently inspect original task, every visible action and applicable policy. No evaluation labels, injection manifests or differences are supplied."}


class RiskReviewJobs(DiagnosisJobs):
    """Reuse queue lifecycle helpers, but own a separate directory and worker."""
    def __init__(self, data_dir, *, config_path=None, python=None, wall_timeout=900, start_worker=True):
        # Do not invoke DiagnosisJobs.__init__: it would touch the diagnosis queue.
        self.folder = Path(data_dir).resolve() / "risk-jobs"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.config, self.configuration = model_configuration(config_path)
        self.config.update(RISK_LIMITS)
        self.configuration["budgets"].update(RISK_LIMITS)
        self.wall_timeout = min(1800, max(10, int(wall_timeout)))
        self.configuration["wall_timeout_seconds"] = self.wall_timeout
        self.python = python or sys.executable
        self.lock, self.pending = threading.RLock(), queue.Queue()
        self.jobs, self.processes, self.closed = {}, {}, threading.Event()
        for path in self.folder.glob("*/job.json"):
            job = read(path)
            if not job:
                continue
            if job.get("status") not in TERMINAL:
                job.update(status="interrupted", finished_at=now(), error="平台已重启；风险检查与已验证发现保留，请重新提交。")
                write(path, job)
            self.jobs[job["id"]] = job
        if start_worker:
            threading.Thread(target=self._loop, name="risk-review-queue", daemon=True).start()

    def enqueue(self, case_id, source, risk_policy=None, automatic=None):
        if not isinstance(source, dict) or not all(k in source for k in SOURCE_KEYS[:4]):
            raise ValueError("风险审查需要统一轨迹输入")
        if source["task_type"] != "openclaw":
            raise ValueError("当前风险审查入口适用于 OpenClaw profile")
        with self.lock:
            for job in self.jobs.values():
                if job["case_id"] == case_id and job["status"] not in TERMINAL:
                    return self.get(job["id"])
            if sum(job["status"] not in TERMINAL for job in self.jobs.values()) >= 20:
                raise ValueError("风险审查队列最多 20 条，请等待或取消现有任务")
            jid = "risk-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
            path = self.folder / jid
            path.mkdir()
            source_copy = {k: source[k] for k in SOURCE_KEYS if k in source}
            context = normal_context(risk_policy, automatic)
            write(path / "input.json", source_copy)
            write(path / "context.json", context)
            write(path / "config.json", self.config)
            job = {"schema": "risk-review-job/v1", "mode": "risk_review", "id": jid, "case_id": str(case_id),
                "trajectory_id": source["trajectory_id"], "status": "queued", "phase": "QUEUED", "stage": "QUEUED",
                "created_at": now(), "started_at": None, "finished_at": None, "error": None,
                "source_sha256": sha(source_copy), "input_sha256": sha(source_copy),
                "policy_sha256": sha(risk_policy or {}), "context_sha256": sha(context),
                "configuration_sha256": sha(self.config), "configuration": self.configuration,
                "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "wall_timeout_seconds": self.wall_timeout, "artifact_directory": str(path),
                "input_contract": "normal task/trajectory/policy/automatic evidence; excludes private evaluation answers/diffs/titles",
                "review_protocol": REVIEW_PROTOCOL,
                "implementation_versions": implementation_versions(),
                "result": None}
            self.jobs[jid] = job
            write(path / "job.json", job)
            self.pending.put(jid)
            return self.get(jid)

    def get(self, jid):
        value = super().get(jid)
        output = self.folder / jid / "run"
        value["investigations"] = rows(output / "risk_trace.jsonl")
        value["assessments"] = value["investigations"]
        value["queries"] = rows(output / "trace_queries.jsonl")
        from .risk_trace import query_status
        for query in value["queries"]:
            query["retrieval_status"] = query_status(query)
            query["returned_row_count"] = None if query.get("error") else len(query.get("rows", []))
        value["query_attempts"] = value["queries"]
        final_queries = {q.get("candidate_id"): q for q in value["queries"]}
        value["retrieval_complete"] = bool(final_queries) and all(query_status(q) == "succeeded" for q in final_queries.values())
        value["query_error_count"] = sum(bool(q.get("error")) for q in value["queries"])
        value["wire_repairs"] = rows(output / "wire_repairs.jsonl")
        value["reference_resolutions"] = rows(output / "risk_reference_resolutions.jsonl")
        value["validation_rejections"] = rows(output / "risk_rejections.jsonl")
        catalogs = rows(output / "reference_catalogs.jsonl")
        value["reference_catalogs"] = catalogs
        value["evidence_view_audit"] = [{k:v for k,v in row.items() if k != "view"}
                                       for row in rows(output / "model_evidence_views.jsonl")]
        value["review_protocol"] = value.get("review_protocol", "legacy-source-quotation/v1")
        value["reference_audit"] = {"catalogs": len(catalogs),
            "resolution_attempts": len(value["reference_resolutions"]),
            "rejections": len(value["validation_rejections"]),
            "note": "拒绝记录是模型中间输出未通过校验，不是已确认的轨迹违规；原始响应与后续纠正均保留。"}
        usage_rows = rows(output / "usage.jsonl")
        # Receiving one model response does not complete its stage. Require the
        # validated transition/commit as well as the existing usage receipt.
        transitions = {row.get("to") for row in value["phases"]}
        committed = {"MAP": "ROUTE" in transitions, "ROUTE": "TRACE" in transitions,
                     "MANAGER": bool(read(output / "manager_plan.json")),
                     "TRACE": "SUBMIT" in transitions,
                     "SUBMIT": any(row.get("event") == "committed" for row in rows(output / "risk_submit.jsonl"))}
        received = {row.get("phase") for row in usage_rows if row.get("event") == "completed"}
        completed = [p for p in ("MAP", "ROUTE", "TRACE", "SUBMIT") if committed[p] and p in received]
        value["completed_phases"] = completed
        value["full_flow_completed"] = len(completed) == 4 and (value.get("result") or {}).get("status") == "completed"
        operation = usage_rows[-1].get("phase") if usage_rows else None
        stage = operation if value["status"] == "running" and operation else value.get("phase", "QUEUED")
        value["stage"] = stage
        value["stages"] = [{"stage": phase,
                            "status": "completed" if committed[phase] and phase in received else "running" if phase == stage and value["status"] == "running" else "failed" if phase == stage and value["status"] in {"failed", "timed_out"} else "pending"}
                           for phase in ("MAP", "ROUTE", "MANAGER", "TRACE", "SUBMIT")]
        context = read(self.folder / jid / "context.json", {})
        value["automatic"] = context.get("automatic", {})
        value["mode"] = "risk_review"
        semantic = read(output / 'openclaw_semantics.json', {})
        value['semantic_parsing'] = semantic
        value['compilation'] = read(output / 'compilation_receipt.json', {})
        value['analysis_version'] = value['compilation'].get('canonical_fingerprint')
        value['semantic_usage'] = semantic.get('usage', {})
        value['stages'].insert(0, {'stage': 'PARSE', 'status':
            semantic.get('status') if semantic.get('status') in {'completed', 'partial', 'failed'}
            else 'running' if value['status'] == 'running' and not usage_rows else 'pending'})
        if (output / 'effective_context.json').is_file():
            value['automatic'] = read(output / 'effective_context.json', {}).get('automatic', value['automatic'])
        value['privacy'] = value['automatic'].get('privacy')
        if isinstance(value.get('result'), dict) and value['privacy']:
            value['result']['privacy'] = value['privacy']
        # A partial run can still have valuable exact-source findings.
        if not value.get("result") and value["investigations"]:
            value["partial_findings"] = [f for r in value["investigations"] for f in r.get("findings", [])]
            value["result"] = {"schema": "risk-review-result/v1", "mode": "risk_review", "status": "partial",
                "findings": value["partial_findings"], "checks": [c for r in value["investigations"] for c in r.get("checks", [])],
                "queries": value["queries"], "investigations": value["investigations"], "automatic": value["automatic"],
                "coverage": {"trace_complete": False, "completed_investigations": [r.get("window_id") for r in value["investigations"]]},
                "summary": "风险审查尚未完整完成，已验证的部分结果保留。", "uncertainty": "尚未完成的调查不能当作通过或没有风险。"}
        return value

    def _run(self, jid):
        with self.lock:
            if self.jobs[jid]["status"] != "queued":
                return
            self._update(jid, status="running", phase="PARSE", started_at=now())
        path = self.folder / jid
        if not self.configuration["configured"] or not self.configuration["credential_available"]:
            self._update(jid, status="failed", finished_at=now(), error="模型未配置或凭据不可用；请设置 PRODEBUGGER_MODEL 和凭据环境变量，或提供显式 JSON 配置。自动规则和已保存风险结果仍可查看。")
            return
        command = [self.python, "-X", "utf8", "-B", "-m", "prodebugger.risk_jobs", "--worker", str(path)]
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        started = time.monotonic()
        with (path / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}, **flags)
            with self.lock:
                self.processes[jid] = process
            while process.poll() is None:
                if self.jobs[jid]["status"] == "cancelled":
                    self._kill(process); break
                if time.monotonic() - started > self.wall_timeout:
                    (path / "STOP").write_text("wall timeout\n", encoding="utf-8")
                    self._kill(process)
                    self._update(jid, status="timed_out", finished_at=now(), error="风险审查超时；自动结果及已完成调查保留。")
                    break
                time.sleep(.25)
            process.wait(timeout=15)
        with self.lock:
            self.processes.pop(jid, None)
            if self.jobs[jid]["status"] in TERMINAL:
                return
        result = read(path / "run/result.json", {})
        complete = self.get(jid)["full_flow_completed"]
        successful = process.returncode == 0 and result.get("status") == "completed" and complete
        self._update(jid, status="succeeded" if successful else "failed", phase=result.get("phase", "UNKNOWN"),
            finished_at=now(), returncode=process.returncode,
            error=None if successful else result.get("stop_reason", "模型未完整完成风险审查；保留已有结果。"),
            duration_seconds=round(time.monotonic() - started, 2))


def worker(path):
    from .llm_client import LLMClient
    from .record_bridge import build_bundle, export_bundle
    from .risk_agent import RiskReviewAgent
    path = Path(path)
    output = path / "run"
    output.mkdir(exist_ok=True)
    config, source, context = read(path / "config.json"), read(path / "input.json"), read(path / "context.json", {})
    try:
        bundle = build_bundle(source, output=output,
            config={**config, 'semantic_policy': context.get('policy', {})}, online=True)
        export_bundle(bundle, output)
        write(output / 'compilation_receipt.json', {'schema': 'shared-analysis-version/v1',
            'canonical_fingerprint': bundle.fingerprint, 'record_sha256': sha(bundle.snapshot()),
            'source_sha256': sha(source), 'policy_sha256': sha(context.get('policy', {})),
            'implementation_versions': implementation_versions(),
            'facts': len(bundle.projection.F), 'semantics_status': bundle.snapshot().get('openclaw_semantics', {}).get('status')})
        from .risk_review import review_risks
        automatic = review_risks(source, context.get('policy'), bundle=bundle)
        automatic['analysis_version'] = bundle.fingerprint
        context = normal_context(context.get('policy'), automatic)
        write(output / 'effective_context.json', context)
        write(output / 'automatic_risks.json', automatic)
        agent = RiskReviewAgent(bundle, LLMClient(config, output), output, config, context=context)
        result = agent.run()
        result.update(compilation_mode="canonical-core-dsl-projection/v1", facts=len(bundle.projection.F),
                      privacy=automatic.get('privacy'))
        write(output / "result.json", result)
        write(path / "worker_receipt.json", {"schema": "risk-review-worker/v1", "finished_at": now(),
            "source_sha256": sha(source), "context_sha256": sha(context),
            "worker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "risk_implementation_versions": implementation_versions(),
            "review_protocol": REVIEW_PROTOCOL,
            "implementation": "Original build_bundle/DebugTools MAP ROUTE/trace_manager/query_logs/LLMClient; independent multi-finding risk assess and submit",
            "core_source_versions": bundle.versions, "worker_input_files": ["input.json", "context.json", "config.json"],
            "request_files": [{"path": str(p.relative_to(path)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                              for p in sorted(output.glob('**/requests/*.json'))],
            "private_evaluation_read": False, "result_status": result.get("status")})
        return 0 if result.get("status") == "completed" else 1
    except Exception as exc:
        # Provider exception strings can contain secrets. Preserve structured
        # reviews without exposing arbitrary exception text.
        investigations = rows(output / "risk_trace.jsonl")
        write(output / "result.json", {"schema": "risk-review-result/v1", "mode": "risk_review", "status": "error",
            "findings": [f for row in investigations for f in row.get("findings", [])],
            "checks": [c for row in investigations for c in row.get("checks", [])],
            "automatic": context.get("automatic", {}), "coverage": {"trace_complete": False},
            "summary": "模型风险审查未完成。", "uncertainty": "仅保留已经完成并通过引用校验的调查。",
            "stop_reason": "风险审查失败：" + type(exc).__name__, "error_type": type(exc).__name__})
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True, type=Path)
    return worker(parser.parse_args(argv).worker)


if __name__ == "__main__":
    raise SystemExit(main())
