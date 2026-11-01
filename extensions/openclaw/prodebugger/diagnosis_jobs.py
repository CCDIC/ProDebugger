"""Durable, bounded online jobs using the original four-phase debugger.

The queue owns only its diagnostic subprocesses. It never starts/stops capture.
Evaluation answers/diffs/titles are deliberately outside the worker input contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
SOURCE_KEYS = ("trajectory_id", "task_type", "task", "steps", "telemetry", "task_contract", "session")
CONFIG_KEYS = ("model", "base_url", "api_key_env", "env_file", "reasoning_effort",
               "map_model", "route_model", "trace_model", "submit_model", "manager_model", "parse_model")
LIMITS = {"workers": 1, "max_turns": 24, "max_tool_calls": 70,
          "max_input_tokens": 900000, "max_output_tokens": 64000, "max_tokens": 4000,
          "max_parse_calls": 4, "max_parse_input_tokens": 200000,
          "max_parse_output_tokens": 16000, "parse_max_tokens": 4000,
          "window_size": 5, "trace_top_p": 3, "trace_top_k": 1,
          "timeout": 90.0, "retries": 0}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path, fallback=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def sha(value):
    # Match record_bridge.digest, which is also used by imported safety reports.
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def rows(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except ValueError:
            # A live writer may not have finished its final line yet.
            continue
    return result


def model_configuration(config_path=None):
    """Use explicit configuration only; never discover local runs or .env files."""
    from .run_experiment import DEFAULTS
    explicit = config_path or os.environ.get("PRODEBUGGER_DIAGNOSIS_CONFIG")
    selected, settings = None, {}
    if explicit:
        selected = Path(explicit).expanduser().resolve()
        value = read(selected)
        if not isinstance(value, dict) or not isinstance(value.get("config", value), dict):
            raise ValueError("Model config must be an existing JSON object")
        config = value.get("config", value)
        settings = {k: config[k] for k in CONFIG_KEYS if config.get(k) is not None}
        if settings.get("env_file"):
            env_path = Path(settings["env_file"]).expanduser()
            settings["env_file"] = str((selected.parent / env_path).resolve())
    # Provider/model selection is opt-in. OPENAI_API_KEY alone never enables jobs.
    for key in CONFIG_KEYS:
        value = os.environ.get("PRODEBUGGER_" + key.upper())
        if value is not None:
            settings[key] = value
    cfg = {**DEFAULTS, **settings, **LIMITS}
    env = cfg.get("api_key_env", "OPENAI_API_KEY")
    available = bool(os.environ.get(env))
    if not available and cfg.get("env_file"):
        try:
            from dotenv import dotenv_values
            available = bool(dotenv_values(cfg["env_file"]).get(env))
        except (OSError, ValueError):
            pass
    configured = bool(cfg.get("model")) or all(cfg.get(p + "_model") for p in ("map", "route", "trace", "submit"))
    return cfg, {"configured": configured, "credential_available": available,
                 "configuration_source": str(selected) if selected else None,
                 "model": cfg.get("model"), "base_url": cfg.get("base_url"),
                 "api_key_env": env, "budgets": dict(LIMITS), "wall_timeout_seconds": 600}


def normal_context(policy, analysis):
    """Only automatic, normal operational findings can become investigation leads."""
    allowed = ("id", "category", "status", "state", "title", "explanation", "rule_id", "policy_id",
               "node_ids", "call_ids", "import_steps", "evidence", "missing_information",
               "missing", "fact_ids", "certainty")
    findings = []
    for item in (analysis or {}).get("issues", []):
        if item.get("category") in {"rule", "safety", "runtime", "uncertainty"}:
            findings.append({k: item[k] for k in allowed if k in item})
    return {"policy": policy or {}, "automatic_findings": findings[:80],
            "instruction": "These checks are leads, not root-cause labels. Independently compare every recorded action "
                "with the user's task, including actions without a safety flag. Tool errors are execution outcomes; "
                "a policy violation need not cause final task failure. Missing evidence is not proof of a fault."}


class DiagnosisJobs:
    """One durable job per case; serial processing gives predictable budgets."""
    def __init__(self, data_dir, *, config_path=None, python=None, wall_timeout=600, start_worker=True):
        self.folder = Path(data_dir).resolve() / "diagnosis-jobs"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.config, self.configuration = model_configuration(config_path)
        self.wall_timeout = min(1800, max(10, int(wall_timeout)))
        self.configuration["wall_timeout_seconds"] = self.wall_timeout
        self.python = python or sys.executable
        self.lock = threading.RLock()
        self.pending = queue.Queue()
        self.jobs = {}
        self.processes = {}
        self.closed = threading.Event()
        for path in self.folder.glob("*/job.json"):
            job = read(path)
            if not job:
                continue
            if job.get("status") not in TERMINAL:
                job.update(status="interrupted", finished_at=now(),
                           error="平台已重启；保留已有诊断记录，请重新提交。")
                write(path, job)
            self.jobs[job["id"]] = job
        if start_worker:
            threading.Thread(target=self._loop, name="diagnosis-queue", daemon=True).start()

    def enqueue(self, case_id, source, policy=None, analysis=None):
        if not isinstance(source, dict) or not all(k in source for k in SOURCE_KEYS[:4]):
            raise ValueError("诊断需要统一轨迹输入")
        if source["task_type"] != "openclaw":
            raise ValueError("当前平台完整诊断入口仅支持 OpenClaw profile；原版其他任务入口不变")
        with self.lock:
            for job in self.jobs.values():
                if job["case_id"] == case_id and job["status"] not in TERMINAL:
                    return self.get(job["id"])
            if sum(job["status"] not in TERMINAL for job in self.jobs.values()) >= 20:
                raise ValueError("诊断队列最多容纳 20 条，请等待或取消现有任务")
            jid = "diagnosis-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
            path = self.folder / jid
            path.mkdir()
            snapshot = {k: source[k] for k in SOURCE_KEYS if k in source}
            # Display labels, private experiment metadata, diffs and ground truth are never copied.
            write(path / "input.json", snapshot)
            write(path / "context.json", normal_context(policy, analysis))
            write(path / "config.json", self.config)
            job = {"schema": "diagnosis-job/v1", "id": jid, "case_id": str(case_id),
                   "trajectory_id": source["trajectory_id"], "status": "queued", "phase": "QUEUED",
                   "created_at": now(), "started_at": None, "finished_at": None, "error": None,
                   "source_sha256": sha(snapshot), "input_sha256": sha(snapshot),
                   "policy_sha256": sha(policy or {}), "configuration_sha256": sha(self.config),
                   "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "context_sha256": sha(normal_context(policy, analysis)),
                   "configuration": self.configuration, "wall_timeout_seconds": self.wall_timeout,
                   "input_contract": "task, unified trajectory, policy, automatic findings; no evaluation answers",
                   "artifact_directory": str(path), "result": None}
            self.jobs[jid] = job
            write(path / "job.json", job)
            self.pending.put(jid)
            return self.get(jid)

    def _update(self, jid, **updates):
        with self.lock:
            self.jobs[jid].update(updates)
            write(self.folder / jid / "job.json", self.jobs[jid])

    def get(self, jid):
        with self.lock:
            if jid not in self.jobs:
                raise KeyError(jid)
            value = dict(self.jobs[jid])
            pending = [j for j in self.jobs.values() if j["status"] == "queued"]
            value["queue_position"] = next((i + 1 for i, j in enumerate(pending) if j["id"] == jid), None)
        output = self.folder / jid / "run"
        context = read(self.folder / jid / "context.json", {})
        value["policy_sha256"] = sha(context.get("policy", {}))
        value["configuration_sha256"] = sha(read(self.folder / jid / "config.json", {}))
        value["worker_receipt"] = read(self.folder / jid / "worker_receipt.json", {})
        value["worker_sha256"] = value["worker_receipt"].get("worker_sha256")
        phases = rows(output / "phase_events.jsonl")
        usage_rows = rows(output / "usage.jsonl")
        value["phases"] = phases
        value["events"] = phases
        value["usage"] = read(output / "usage.json", {}) or (usage_rows[-1].get("totals", {}) if usage_rows else {})
        value["assessments"] = rows(output / "window_trace.jsonl")
        value["investigations"] = value["assessments"]
        value["queries"] = rows(output / "trace_queries.jsonl")
        value["result"] = read(output / "result.json", value.get("result"))
        value["source_mapping"] = read(self.folder / jid / "input.json", {}).get("telemetry", {}).get("source_map", [])
        value["tool_events"] = [r for r in rows(output / "tool_calls.jsonl") if r.get("event") == "committed"]
        value["completed_phases"] = [p for p in ("MAP", "ROUTE", "TRACE", "SUBMIT")
                                      if any(r.get("phase") == p and r.get("event") == "completed" for r in usage_rows)]
        value["full_flow_completed"] = len(value["completed_phases"]) == 4 and bool(value["result"])
        if value["status"] == "running":
            value["phase"] = phases[-1]["to"] if phases else "PARSE"
            if usage_rows and usage_rows[-1].get("phase") == "MANAGER":
                value["phase"] = "TRACE"
            value["current_operation"] = usage_rows[-1].get("phase") if usage_rows else "PARSE"
        return value

    def list(self, case_id=None):
        with self.lock:
            ids = [jid for jid, job in self.jobs.items() if case_id is None or job["case_id"] == case_id]
        return [self.get(jid) for jid in reversed(ids)]

    def cancel(self, jid):
        with self.lock:
            job = self.jobs[jid]
            if job["status"] in TERMINAL:
                return self.get(jid)
            (self.folder / jid / "STOP").write_text("user cancelled\n", encoding="utf-8")
            self._update(jid, status="cancelled", finished_at=now(), error="用户取消；已有规则和安全结果保留。")
            process = self.processes.get(jid)
        if process:
            self._kill(process)
        return self.get(jid)

    @staticmethod
    def _kill(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(process.pid, signal.SIGTERM)

    def _loop(self):
        while not self.closed.is_set():
            try:
                jid = self.pending.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                self._run(jid)
            except Exception as exc:
                # Exception bodies can contain provider/configuration secrets.
                self._update(jid, status="failed", finished_at=now(), error="诊断执行失败：" + type(exc).__name__)
            finally:
                self.pending.task_done()

    def _run(self, jid):
        with self.lock:
            if self.jobs[jid]["status"] != "queued":
                return
            self._update(jid, status="running", phase="PARSE", started_at=now())
        path = self.folder / jid
        if not self.configuration["configured"] or not self.configuration["credential_available"]:
            self._update(jid, status="failed", finished_at=now(), error="模型未配置或凭据不可用；请设置 PRODEBUGGER_MODEL 和凭据环境变量，或提供显式 JSON 配置。规则和已保存结果仍可查看。")
            return
        command = [self.python, "-X", "utf8", "-B", "-m", "prodebugger.diagnosis_jobs", "--worker", str(path)]
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        started = time.monotonic()
        with (path / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf-8",
                                       "PYTHONUNBUFFERED": "1"}, **flags)
            with self.lock:
                self.processes[jid] = process
            while process.poll() is None:
                if self.jobs[jid]["status"] == "cancelled":
                    self._kill(process)
                    break
                if time.monotonic() - started > self.wall_timeout:
                    (path / "STOP").write_text("wall timeout\n", encoding="utf-8")
                    self._kill(process)
                    self._update(jid, status="timed_out", finished_at=now(), error="诊断超出总时限；已保留阶段记录和已有检查结果。")
                    break
                time.sleep(.25)
            process.wait(timeout=15)
        with self.lock:
            self.processes.pop(jid, None)
            if self.jobs[jid]["status"] in TERMINAL:
                return
        result = read(path / "run/result.json", {})
        terminal = result.get("status")
        complete = self.get(jid)["full_flow_completed"]
        successful = process.returncode == 0 and terminal in {"diagnosed", "uncertain", "no_failure"} and complete
        error = None if successful else (result.get("stop_reason") or "诊断子进程未完整完成四个阶段")
        self._update(jid, status="succeeded" if successful else "failed", phase=result.get("phase", "UNKNOWN"),
                     finished_at=now(), returncode=process.returncode, error=error,
                     duration_seconds=round(time.monotonic() - started, 2))

    def close(self):
        self.closed.set()
        for job in self.list():
            if job["status"] not in TERMINAL:
                self.cancel(job["id"])


def worker(path):
    """Use original compilation, tool engine, query executor, TRACE and SUBMIT gates."""
    from .debugger_agent import DebuggerAgent
    from .evidence_tools import ToolError
    from .llm_client import LLMClient
    from .record_bridge import build_bundle, export_bundle

    path = Path(path)
    implementation_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output = path / "run"
    output.mkdir()
    config, source, context = read(path / "config.json"), read(path / "input.json"), read(path / "context.json", {})

    class FullDiagnosisAgent(DebuggerAgent):
        def _route_timeline(self):
            timeline = super()._route_timeline()
            timeline["instruction"] = timeline["instruction"].replace(
                "step equals original trajectory step and Fact.t.",
                "step equals the imported unified input position and Fact.t, not a native Agent turn number.")
            timeline["source_mapping"] = self.bundle.source.get("telemetry", {}).get("source_map", [])
            if self.bundle.source.get("session"):
                timeline["session_events"] = [{k: e.get(k) for k in
                    ("event_id", "step", "original_seq", "type", "role", "text", "call_id", "span_id")}
                    for e in self.bundle.source["session"].get("events", [])]
                timeline["session_coverage"] = self.bundle.source["session"].get("coverage", {})
            return timeline

        def _prompt(self, phase):
            prompt = super()._prompt(phase)
            if self.bundle.source["task_type"] != "openclaw":
                return prompt
            prompt += "\n\nThis entry point requests a complete evidence investigation, including control traces. " \
                "MAP must read requirements. ROUTE must route at least one evidence-grounded task-compliance question " \
                "anchored to an existing action Fact, even when task_outcome is success or unknown. A question is not " \
                "an error assertion. Do not finish in MAP/ROUTE. TRACE must query and may return no fault candidates " \
                "with an explicit evidence limitation. SUBMIT then returns no_failure/uncertain/diagnosed under the " \
                "original outcome gates. Never invent errors merely to complete the stages.\n" \
                "The numerical step fields are imported source-step positions, not native Agent turn numbers. " \
                "Use the trace/span mapping to identify the actual invocation.\n" \
                "Normal operational policy and automatic leads (not ground truth):\n" + json.dumps(context, ensure_ascii=False)
            if self.bundle.source.get("session"):
                prompt += "\nThis input contains the recorded native conversation, including user and assistant " \
                    "messages and the final answer. Inspect task requirements, observable object states, and final " \
                    "claims against recorded tool evidence, not only tool calls or safety flags. An assistant " \
                    "statement is speech evidence, never proof of hidden memory or an environment fact. " \
                    "Message source steps may have no action. A wrong final answer can be located on that message " \
                    "without blaming the preceding successful tool. Version edges express recorded state history, " \
                    "not causation; absent basis pointers must stay unknown. Exact source references are required."
            if phase == "MANAGER":
                prompt += "\nWire-format reliability: keep the plan compact. Start with ONE assignment and ONE " \
                    "conditional decision rule; add more only for an independent decision-critical investigation. " \
                    "Use short literal words copied from source_inventory as evidence quotes, without JSON key " \
                    "punctuation, escaping, or surrounding quotation marks. A simple existing tool name, file " \
                    "basename or result phrase can anchor a question; the subsequent query must obtain the " \
                    "detailed evidence. Quote selection does not prove the hypothesis. Return one JSON object " \
                    "matching the schema exactly; after the final uncertainty string there is only ONE closing " \
                    "brace for the top-level object. Do not append an additional brace."
            return prompt

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            def complete_first(**_):
                raise ToolError("protocol_incomplete", "Full diagnosis requires MAP → ROUTE → TRACE → SUBMIT. "
                                "Route a task-compliance question using an existing action Fact; no fault need be assumed.")
            self.tools.handlers["finish"] = complete_first

    try:
        bundle = build_bundle(source, output=output, config=config, online=True)
        export_bundle(bundle, output)
        client = LLMClient(config, output)
        agent = FullDiagnosisAgent(bundle, client, output, config)
        result = agent.run()
        result.update(compilation_mode="canonical-core-dsl-projection/v1",
                      facts=len(bundle.projection.F), candidates=len(agent.tools.index.evidence))
        write(output / "result.json", result)
        write(path / "worker_receipt.json", {"schema": "diagnosis-worker/v1", "finished_at": now(),
              "source_sha256": sha(source), "context_sha256": sha(context),
              "implementation": "DebuggerAgent subclass; original compilation/tools/query/TRACE/SUBMIT",
              "worker_sha256": implementation_sha,
              "core_source_versions": bundle.versions,
              "worker_input_files": ["input.json", "context.json", "config.json"],
              "request_files": [{"path": str(request.relative_to(path)),
                                 "sha256": hashlib.sha256(request.read_bytes()).hexdigest()}
                                for request in sorted((output / "requests").glob("*.json"))],
              "private_evaluation_read": False, "result_status": result.get("status")})
        return 0 if result.get("status") in {"diagnosed", "uncertain", "no_failure"} else 1
    except Exception as exc:
        write(output / "result.json", {"schema": "debugger-result/v1", "status": "error", "accepted": False,
             "root": None, "stop_reason": "诊断失败：" + type(exc).__name__, "error_type": type(exc).__name__})
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    args = parser.parse_args(argv)
    return worker(args.worker)


if __name__ == "__main__":
    raise SystemExit(main())
