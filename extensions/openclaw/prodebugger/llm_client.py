"""Provider-neutral tool calling, durable usage accounting, and offline replay."""
from __future__ import annotations

import json
import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .evidence_tools import BudgetExceeded, ToolError
from .record_bridge import append_json, digest, plain, read_json, write_json

PHASES = ("MAP", "ROUTE", "TRACE", "SUBMIT")
ACCOUNTING_PHASES = (*PHASES, "MANAGER", "PARSE")


def phase_usage():
    return {p: {"model_calls": 0, "replay_calls": 0, "input_tokens": 0,
                "output_tokens": 0, "latency_seconds": 0.0,
                "cache_hit_input_tokens": None, "cache_miss_input_tokens": None} for p in ACCOUNTING_PHASES}


def save_request(output, ordinal, phase, model, messages, schemas):
    relative = f"requests/{ordinal:05d}.json"
    write_json(Path(output) / relative, {"schema": "debugger-request/v1", "phase": phase,
               "requested_model": model, "messages": plain(messages), "tools": plain(schemas)})
    return relative


def json_lines(path):
    """Only an interrupted final line may be ignored; earlier corruption fails."""
    if not Path(path).is_file():
        return []
    content = Path(path).read_text(encoding="utf-8-sig")
    lines = content.splitlines()
    rows = []
    for i, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines) - 1 or content.endswith("\n"):
                raise ToolError("source_mismatch", "corrupt event log")
    return rows


def replay_messages(path):
    path = Path(path)
    rows = json_lines(path) if path.suffix == ".jsonl" else read_json(path)
    if isinstance(rows, dict):
        rows = rows.get("messages", [])
    messages = []
    for row in rows:
        row = row.get("message", row)
        if row.get("role") == "assistant":
            messages.append(row)
    if not messages:
        raise ValueError("replay needs at least one recorded assistant message")
    return messages


class ReplayClient:
    def __init__(self, path, output, max_turns=80):
        self.messages = replay_messages(path)
        self.path, self.output, self.max_turns = Path(path), Path(output), max_turns
        self.cursor = 0
        self.recovered_message = None
        self.recovered_phase = None
        self.by_phase = phase_usage()
        self.fingerprint = digest(self.messages)

    def restore(self, state):
        if state and state.get("replay_fingerprint") != self.fingerprint:
            raise ToolError("source_mismatch", "replay script changed")
        checkpoint_cursor = (state or {}).get("replay_calls", 0)
        rows = json_lines(self.output / "usage.jsonl")
        latest = rows[-1] if rows else (state or {})
        if latest and latest.get("replay_fingerprint") != self.fingerprint:
            raise ToolError("source_mismatch", "replay ledger changed")
        self.cursor = latest.get("replay_calls", checkpoint_cursor)
        self.by_phase = latest.get("by_phase", phase_usage())
        if self.cursor > checkpoint_cursor:
            self.recovered_message = self.messages[self.cursor - 1]
            self.recovered_phase = latest.get("phase")

    def snapshot(self):
        return {"mode": "replay", "model_calls": 0, "replay_calls": self.cursor,
                "input_tokens": 0, "output_tokens": 0, "by_phase": plain(self.by_phase),
                "replay_fingerprint": self.fingerprint}

    def complete(self, messages, schemas, *, phase="ROUTE"):
        if self.cursor >= self.max_turns:
            raise BudgetExceeded("replay turn limit reached")
        if self.cursor >= len(self.messages):
            raise ToolError("model_error", "replay exhausted before a terminal result")
        message = plain(self.messages[self.cursor])
        self.cursor += 1
        self.by_phase[phase]["replay_calls"] += 1
        request_file = save_request(self.output, self.cursor, phase, "offline-replay", messages, schemas)
        append_json(self.output / "usage.jsonl", {**self.snapshot(), "phase": phase,
                    "event": "completed", "request_file": request_file, "message": message})
        return message

    async def complete_async(self, messages, schemas, *, phase="ROUTE"):
        return self.complete(messages, schemas, phase=phase)


class LLMClient:
    def __init__(self, config, output, transport=None):
        self.config, self.output = dict(config), Path(output)
        self.recovered_message = None
        self.recovered_phase = None
        self.last_completion = {}
        self.state = {"mode": "online", "model_calls": 0, "replay_calls": 0,
                      "input_tokens": 0, "output_tokens": 0,
                      "accounted_input": 0, "accounted_output": 0,
                      "unknown_usage_calls": 0, "latency_seconds": 0.0,
                      "cache_hit_input_tokens": None, "cache_miss_input_tokens": None,
                      "by_phase": phase_usage()}
        self.models = {p: config.get(p.lower() + "_model") or config.get("model") for p in PHASES}
        self.models["MANAGER"] = config.get("manager_model") or self.models["TRACE"]
        self.models["PARSE"] = config.get("parse_model") or config.get("model") or self.models["MAP"]
        if not all(self.models.values()):
            raise ValueError("online execution requires --model or all four phase models")
        if transport is None:
            if config.get("env_file"):
                from dotenv import load_dotenv
                load_dotenv(config["env_file"], override=False)
            env = config.get("api_key_env", "OPENAI_API_KEY")
            key = os.environ.get(env)
            if not key:
                raise ValueError(f"credential environment variable {env} is not set")
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=config.get("base_url") or None,
                            timeout=config["timeout"], max_retries=0)
            self.transport = client.chat.completions.create
        else:
            self.transport = transport

    def restore(self, state):
        # The ledger may be newer than the checkpoint after a completed API call.
        rows = json_lines(self.output / "usage.jsonl")
        self.state.update(rows[-1]["totals"] if rows else (state or {}))
        if rows and self.state["model_calls"] > (state or {}).get("model_calls", 0):
            if rows[-1]["event"] != "completed":
                raise ToolError("model_error", "interrupted model request has no durable response; start a new run")
            self.recovered_message = rows[-1]["message"]
            self.recovered_phase = rows[-1]["phase"]

    def snapshot(self):
        return plain(self.state)

    def _reserve(self, messages, schemas, phase):
        cfg, state = self.config, self.state
        if any((p / "STOP").is_file() for p in (self.output, self.output.parent, self.output.parent.parent)):
            raise BudgetExceeded("run STOP marker found; no further model requests sent")
        estimate = len(json.dumps([messages, schemas], ensure_ascii=False).encode("utf-8")) + 512
        output = cfg["max_tokens"]
        if state["model_calls"] >= cfg["max_turns"]:
            raise BudgetExceeded("model request limit reached")
        if state["accounted_input"] + estimate > cfg["max_input_tokens"]:
            raise BudgetExceeded("input token budget reached (conservative byte estimate)")
        if cfg["max_output_tokens"] and state["accounted_output"] + output > cfg["max_output_tokens"]:
            raise BudgetExceeded("output token budget cannot cover next request")
        state["model_calls"] += 1
        state["by_phase"][phase]["model_calls"] += 1
        state["accounted_input"] += estimate
        state["accounted_output"] += output
        self.request_phase = phase
        self.request_file = save_request(self.output, state["model_calls"], phase,
                                         self.models[phase], messages, schemas)
        # Persist before sending: interruption must not reset a potentially billed request.
        self._log({"event": "pending", "input_estimate": estimate, "output_reserved": output})
        return estimate, output

    def _log(self, event):
        append_json(self.output / "usage.jsonl", {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
                                               "phase": self.request_phase,
                                               "requested_model": self.models[self.request_phase],
                                               "request_file": self.request_file,
                                               **event, "totals": self.snapshot()})

    def complete(self, messages, schemas, *, phase="ROUTE"):
        return asyncio.run(self.complete_async(messages, schemas, phase=phase))

    async def complete_async(self, messages, schemas, *, phase="ROUTE"):
        if phase not in ACCOUNTING_PHASES:
            raise ValueError("unknown debugger phase")
        for attempt in range(self.config["retries"] + 1):
            estimate, reserved = self._reserve(messages, schemas, phase)
            request_file = self.request_file
            start = time.monotonic()
            try:
                extra = {"extra_body": {"reasoning_effort": self.config["reasoning_effort"]}} if self.config.get("reasoning_effort") else {}
                mode = ({"tools": schemas, "tool_choice": "auto"} if schemas
                        else {"response_format": {"type": "json_object"}})
                # DeepSeek thinking mode rejects forcing a specific tool.
                deepseek_thinking = (
                    urlparse(self.config.get("base_url") or "").hostname == "api.deepseek.com"
                    and self.config.get("reasoning_effort") in {"low", "high", "max"}
                )
                if phase in {"TRACE", "MANAGER"} and schemas and not deepseek_thinking:
                    mode["tool_choice"] = {"type": "function", "function": {"name": schemas[0]["function"]["name"]}}
                if reserved:
                    mode["max_tokens"] = reserved
                response = await asyncio.to_thread(self.transport, model=self.models[phase], messages=messages,
                                          **mode, **extra)
            except Exception as exc:
                self.request_phase, self.request_file = phase, request_file
                latency = time.monotonic() - start
                self.state["latency_seconds"] += latency
                self.state["by_phase"][phase]["latency_seconds"] += latency
                self.state["unknown_usage_calls"] += 1
                retryable = getattr(exc, "status_code", None) in {429, 500, 502, 503, 504}
                self._log({"event": "error", "error_type": type(exc).__name__, "retryable": retryable})
                if retryable and attempt < self.config["retries"]:
                    continue
                # Exception text from providers can echo credentials or request headers.
                raise ToolError("model_error", f"model request failed: {type(exc).__name__}") from None
            self.request_phase, self.request_file = phase, request_file
            latency = time.monotonic() - start
            self.state["latency_seconds"] += latency
            self.state["by_phase"][phase]["latency_seconds"] += latency
            usage = getattr(response, "usage", None)
            if usage is None or any(type(getattr(usage, k, None)) is not int or getattr(usage, k) < 0
                                    for k in ("prompt_tokens", "completion_tokens")):
                self.state["unknown_usage_calls"] += 1
                self._log({"event": "unknown_usage"})
                raise ToolError("model_error", "provider omitted token usage; request conservatively charged")
            inp, out = usage.prompt_tokens, usage.completion_tokens
            self.state["input_tokens"] += inp
            self.state["output_tokens"] += out
            self.state["by_phase"][phase]["input_tokens"] += inp
            self.state["by_phase"][phase]["output_tokens"] += out
            for key, field in (("cache_hit_input_tokens", "prompt_cache_hit_tokens"),
                               ("cache_miss_input_tokens", "prompt_cache_miss_tokens")):
                value = getattr(usage, field, None)
                if type(value) is int and value >= 0:
                    self.state[key] = (self.state[key] or 0) + value
                    stage = self.state["by_phase"][phase]
                    stage[key] = (stage[key] or 0) + value
            self.state["accounted_input"] += max(0, inp - estimate)
            self.state["accounted_output"] += out - reserved
            message = plain(response.choices[0].message.model_dump(exclude_none=True))
            # Keep only fields accepted in a subsequent chat request.
            message = {k: v for k, v in message.items() if k in {"role", "content", "tool_calls", "reasoning_content"}}
            # Keep response diagnostics outside the assistant message: these
            # fields are not valid chat-message input on a subsequent request.
            choice = response.choices[0]
            details = getattr(usage, "completion_tokens_details", None)
            self.last_completion = {
                "finish_reason": getattr(choice, "finish_reason", None),
                "native_finish_reason": getattr(choice, "native_finish_reason", None),
                "response_id": getattr(response, "id", None),
                "reasoning_tokens": getattr(details, "reasoning_tokens", None),
            }
            self._log({"event": "completed", "input_tokens": inp, "output_tokens": out,
                       "response_model": getattr(response, "model", None),
                       **self.last_completion,
                       "message": message})
            return message
        raise ToolError("model_error", "model retry limit reached")
