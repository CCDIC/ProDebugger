"""Deterministic, policy-scoped tool-attempt audit using ProDebugger SOURCE queries.

This module never executes tools, sends telemetry, calls an LLM, or diagnoses a
root cause. Candidate indexing is not evidence: every accepted decision requires
an unchanged query_dsl query returning the corresponding original source slice.
"""
from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import ntpath
import posixpath
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

from . import query_dsl
from .debug_tools import DebugTools
from .record_bridge import build_bundle, digest, read_json, write_json
from .otel_import import _truncated
from profiles.openclaw import envelope

POLICY_SCHEMA = "prodebugger-safety-policy/v1"
REPORT_SCHEMA = "prodebugger-safety-report/v1"
RULES = {
    "forbidden_path": ("禁止访问的路径", "移除敏感目录访问权限，并改用授权的数据来源。"),
    "outside_allowed_roots": ("超出允许目录", "将读写目标限制在策略允许的目录内。"),
    "network_not_allowed": ("网络目标不在允许列表", "核对目标主机及端口，只有经授权的目标才应加入允许列表。"),
    "denied_tool": ("调用被禁用的工具", "移除该工具的调用权限，或使用经授权的替代工具。"),
    "dangerous_command": ("调用策略禁止的命令", "移除该命令调用，使用权限更小的文件操作工具。"),
}
MAX_ACTION_CHARS = 65536
MAX_EVIDENCE_CHARS = 240


def _string(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(label + " must be a nonempty bounded string")
    if any(ord(c) < 32 for c in value):
        raise ValueError(label + " must not contain control characters")
    return value


def _canonical_path(value, style, cwd=None):
    value = _string(value, "path")
    if value.startswith("~") or "$" in value or "%" in value:
        raise ValueError("path expansion is unsupported")
    module = ntpath if style == "windows" else posixpath
    if style == "windows":
        value = value.replace("/", "\\")
        if value.startswith("\\\\"):
            raise ValueError("UNC/device path semantics are unsupported")
        drive, tail = ntpath.splitdrive(value)
        if ":" in tail or any(part not in ("", ".", "..") and part.endswith((" ", "."))
                              for part in tail.split("\\")):
            raise ValueError("Windows alternate streams/trailing-dot aliases are unsupported")
        if any(part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                              *(f"LPT{i}" for i in range(1, 10))} for part in tail.split("\\")):
            raise ValueError("Windows reserved device names are unsupported")
        if drive and not tail.startswith("\\"):
            raise ValueError("drive-relative paths are unsupported")
        if tail.startswith("\\") and not drive:
            raise ValueError("root-relative Windows path requires an explicit drive")
    elif "\\" in value:
        # POSIX accepts this as a filename, but some tools translate separators.
        raise ValueError("mixed path separator semantics are unsupported")
    if not module.isabs(value):
        if cwd is None:
            raise ValueError("policy paths must be absolute")
        value = module.join(cwd, value)
    return module.normcase(module.normpath(value)) if style == "windows" else module.normpath(value)


def _under(path, root, style):
    module = ntpath if style == "windows" else posixpath
    try:
        return module.commonpath((path, root)) == root
    except ValueError:
        return False


def _host(value):
    value = _string(value, "host").lower().rstrip(".")
    if not value or any(c in value for c in "/\\@%?#") or any(c.isspace() for c in value):
        raise ValueError("host must be an exact hostname or IP without a URL/userinfo")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if ":" in value:
            raise ValueError("invalid host") from None
        encoded = value.encode("idna").decode("ascii")
        if len(encoded) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                                     for part in encoded.split(".")):
            raise ValueError("invalid hostname")
        return encoded


def validate_policy(policy: dict) -> dict:
    """Return a normalized copy. Missing controls and unknown fields are errors.

    Host entries without a port allow only the scheme's default port (80/443).
    Paths are lexical policy boundaries, not a claim about resolved symlinks.
    """
    required = {"schema", "id", "version", "path_style", "working_directory", "allowed_roots",
                "forbidden_paths", "allowed_network_hosts", "denied_tools", "denied_commands", "tools"}
    if not isinstance(policy, dict) or set(policy) != required:
        raise ValueError("policy must explicitly supply exactly: " + ", ".join(sorted(required)))
    result = copy.deepcopy(policy)
    if result["schema"] != POLICY_SCHEMA or result["path_style"] not in ("windows", "posix"):
        raise ValueError("unsupported policy schema or path_style")
    for key in ("id", "version"):
        _string(result[key], key)
    style = result["path_style"]
    result["working_directory"] = _canonical_path(result["working_directory"], style)
    for key in ("allowed_roots", "forbidden_paths", "denied_tools", "denied_commands", "allowed_network_hosts"):
        if not isinstance(result[key], list) or len(result[key]) > 256:
            raise ValueError(key + " must be an array with at most 256 entries")
    for key in ("allowed_roots", "forbidden_paths"):
        result[key] = sorted(set(_canonical_path(p, style) for p in result[key]))
    result["denied_tools"] = sorted(set(_string(t, "denied tool") for t in result["denied_tools"]))
    hosts = []
    for entry in result["allowed_network_hosts"]:
        if not isinstance(entry, dict) or not {"host"} <= set(entry) <= {"host", "port"}:
            raise ValueError("network entry requires host and optional port")
        host = {"host": _host(entry["host"])}
        if "port" in entry:
            if type(entry["port"]) is not int or not 1 <= entry["port"] <= 65535:
                raise ValueError("network port must be an integer from 1 to 65535")
            host["port"] = entry["port"]
        hosts.append(host)
    result["allowed_network_hosts"] = sorted(hosts, key=lambda h: (h["host"], h.get("port", 0)))
    seen = set()
    for rule in result["denied_commands"]:
        if not isinstance(rule, dict) or set(rule) != {"id", "executable", "require_any_args"}:
            raise ValueError("command rules require id, executable, require_any_args")
        _string(rule["id"], "command rule id")
        if rule["id"] in seen:
            raise ValueError("command rule IDs must be unique")
        seen.add(rule["id"])
        executable = _string(rule["executable"], "executable")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", executable):
            raise ValueError("command executable must be a literal basename")
        if not isinstance(rule["require_any_args"], list) or any(
                not isinstance(v, str) or not v or re.search(r"\s", v) for v in rule["require_any_args"]):
            raise ValueError("require_any_args must contain literal individual argv tokens")
    if not isinstance(result["tools"], dict) or len(result["tools"]) > 256:
        raise ValueError("tools must explicitly bind supported tool names")
    for name, binding in result["tools"].items():
        _string(name, "tool name")
        if not isinstance(binding, dict):
            raise ValueError("tool binding must be an object")
        parameter = {"read": "path_arg", "write": "path_arg", "network": "url_arg", "command": "command_arg"}.get(binding.get("kind"))
        if parameter is None or set(binding) != {"kind", parameter}:
            raise ValueError("tool binding requires kind and its single argument field")
        _string(binding[parameter], "argument field")
    return result


def _locations(text):
    """JSON-pointer -> (value start,end,member start); no reserialized offsets."""
    decoder, found = json.JSONDecoder(), {}
    def space(i):
        while i < len(text) and text[i].isspace():
            i += 1
        return i
    def visit(i, path, member=None):
        i = space(i)
        start = i
        if text[i] == "{":
            i = space(i + 1)
            while text[i] != "}":
                key_start = i
                key, end = decoder.raw_decode(text, i)
                i = space(end)
                if text[i] != ":":
                    raise ValueError("missing colon")
                i = space(visit(i + 1, (*path, key), key_start))
                if text[i] == ",":
                    i = space(i + 1)
                elif text[i] != "}":
                    raise ValueError("missing object separator")
            i += 1
        elif text[i] == "[":
            i, number = space(i + 1), 0
            while text[i] != "]":
                i = space(visit(i, (*path, number)))
                number += 1
                if text[i] == ",":
                    i = space(i + 1)
                elif text[i] != "]":
                    raise ValueError("missing array separator")
            i += 1
        else:
            _, i = decoder.raw_decode(text, i)
        found[path] = (start, i, start if member is None else member)
        return i
    visit(0, ())
    return found


def _needle(text):
    # Do not relax the original DSL's literal restrictions. Split underscores,
    # wildcard characters and quote boundaries, then use a specific literal.
    candidates = re.findall(r"[^%_*\s\"'\\]{3,120}", text)
    for candidate in sorted(candidates, key=len, reverse=True):
        if len(re.sub(r"\W", "", candidate)) >= 3 and candidate.lower() not in {
                "the", "and", "you", "step", "plan", "memory", "reflection", "action", "task", "search"}:
            return candidate
    return None


def _sql_string(value):
    return "'" + value.replace("'", "''") + "'"


class _Evidence:
    def __init__(self, bundle, report):
        self.tools = DebugTools(bundle)
        self.report = report
        self.cache = {}

    def get(self, step, module, text, start, end, identity, pointer, lookup_needle=None):
        key = (step, module, start, end)
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None
        snippet = text[start:end]
        needle = lookup_needle or _needle(snippet)
        if not 0 <= start < end <= len(text) or len(snippet) > MAX_EVIDENCE_CHARS or not needle:
            return None
        # SOURCE returns a bounded excerpt around the first literal match. A
        # long JSON path's longest component can be too far after the member
        # start, clipping its key/prefix even though the full member is within
        # our evidence budget. Try a few early anchors from this same member;
        # an anchor is only a retrieval hint, never evidence by itself.
        needles = [needle]
        if len(snippet) > 90:
            needles = [_needle(snippet[:80]), needle, _needle(snippet[:40]),
                       _needle(snippet[40:80])]
        needles = list(dict.fromkeys(value for value in needles if value))
        relation, slot = ("tool_call", "act") if module == "action" else ("tool_result", "obs")
        facts = [f for f in self.tools.bundle.record.F if f.t == step and f.rel == relation and f.slot == slot]
        if len(facts) != 1:
            return None
        fid = facts[0].fid
        # Preserve SOURCE's enclosing source_id and its bounds, while projecting
        # only the minimal actual member. No nearby content/headers are returned.
        for candidate in needles:
            sql = ("-- Purpose: Verify one indexed tool field against original captured text.\n"
                   "SELECT source_id, fid, step, module, start AS excerpt_start, end AS excerpt_end, "
                   f"{start} AS start, {end} AS end, substr(text, {start} - start + 1, {end-start}) AS text, "
                   "source_kind, parse_status "
                   f"FROM SOURCE({fid}, {_sql_string(candidate)}) WHERE start <= {start} AND end >= {end}")
            result = query_dsl.execute(self.tools, sql)
            query_id = f"q{len(self.report['queries']) + 1:03d}"
            keys = ("schema", "query_digest", "rows", "partial", "truncated", "error", "function_errors",
                    "skipped_branches", "coverage", "returned_fids", "returned_steps", "query_has_limit")
            self.report["queries"].append({"id": query_id, "sql": sql,
                                            "result": {k: result[k] for k in keys if k in result}})
            if (result.get("error") or result.get("partial") or result.get("truncated")
                    or result.get("function_errors") or result.get("skipped_branches")
                    or len(result.get("rows", [])) != 1):
                continue
            row = result["rows"][0]
            if any(row.get(k) != v for k, v in {"step": step, "fid": fid, "module": module,
                                               "start": start, "end": end, "text": snippet}.items()):
                continue
            evidence = {**row, "query_id": query_id, "json_pointer": pointer,
                        "fragment_id": f"{module}:{step}:{start}:{end}",
                        "trace_id": identity[0], "span_id": identity[1]}
            self.cache[key] = evidence
            return evidence
        return None

    def member(self, step, module, text, locations, path, identity):
        value_start, end, start = locations[path]
        return self.get(step, module, text, start, end, identity,
                        "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in path),
                        _needle(text[value_start:end]) or _needle(text[start:value_start]))


def _command(value, policy):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("missing/oversized command string")
    if re.search(r"[;&|<>`$()\n\r]", value):
        raise ValueError("shell composition/expansion requires a separate parser")
    if "\\" in value:
        raise ValueError("shell backslash escape/Windows command-line semantics are unsupported")
    try:
        argv = shlex.split(value, posix=True)
    except ValueError:
        raise ValueError("unparseable command quoting") from None
    if not argv:
        raise ValueError("empty command")
    executable = posixpath.basename(argv[0].replace("\\", "/"))
    matches = [r["id"] for r in policy["denied_commands"] if executable == r["executable"]
               and (not r["require_any_args"] or any(a in argv[1:] for a in r["require_any_args"]))]
    if matches:
        return matches, argv
    command_name = re.sub(r"\.(exe|cmd|bat|com)$", "", executable.lower())
    if command_name in {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh", "node",
                        "env", "sudo", "eval", "perl", "ruby", "php", "lua", "busybox"} or re.fullmatch(r"python[\d.]*", command_name):
        raise ValueError("interpreter/wrapper command semantics are unsupported")
    if argv[0] not in {"echo", "printf", "pwd", "true", "false"}:
        raise ValueError("command filesystem/network side effects are outside the supported grammar")
    return matches, argv


def _url(value):
    value = _string(value, "URL")
    if "\\" in value or any(c.isspace() for c in value):
        raise ValueError("ambiguous URL whitespace or backslash")
    parts = urlsplit(value)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or not parts.hostname:
        raise ValueError("only absolute HTTP/HTTPS target URLs are supported")
    host = _host(parts.hostname)
    default = 443 if parts.scheme.lower() == "https" else 80
    port = default if parts.port is None else parts.port
    if not 1 <= port <= 65535:
        raise ValueError("invalid URL port")
    authority = parts.netloc.rsplit("@", 1)[-1]
    return parts, host, port, default, authority


def audit_source(source: dict, policy: dict, *, bundle=None) -> dict:
    policy = validate_policy(policy)
    if not isinstance(source, dict) or source.get("task_type") != "openclaw":
        raise ValueError("safety audit v1 requires an imported openclaw source")
    coverage = source.get("telemetry", {}).get("coverage", {})
    report = {"schema": REPORT_SCHEMA, "status": "unknown", "status_label": "证据不足",
              "policy": {"id": policy["id"], "version": policy["version"], "digest": digest(policy)},
              "source": {"trajectory_id": source.get("trajectory_id"), "sha256": digest(source),
                         "telemetry_input_digest": source.get("telemetry", {}).get("input_digest")},
              "flags": [], "queries": [], "steps": [], "rules": [],
              "coverage": {"declared_complete": coverage.get("declared_complete") is True,
                           "content_complete": coverage.get("content_complete") is True,
                           "total_actions": 0, "assessed_actions": 0, "gaps": []},
              "limitations": ["safe 仅表示本版本显式策略通过，不表示系统在所有意义上安全。",
                              "这是已记录工具调用的事后审计，不负责阻止执行，也不进行根因定位。",
                              "路径按输入字符串规范化；未验证符号链接、挂载点或实际文件系统解析。",
                              "网络策略检查记录的初始目标，不证明 DNS、重定向或后续连接安全。",
                              "命令仅支持显式禁止的直接调用及 echo/printf/pwd/true/false；其他命令保留 unknown。",
                              "采集完整性来自输入声明及导入检查；未知工具、缺失证据和未支持语义保留 unknown。",
                              "source_id 是 SOURCE 返回的包围区间，fragment_id/start/end 对应实际展示的精确子片段。"]}
    gaps = report["coverage"]["gaps"]
    if coverage.get("declared_complete") is not True:
        gaps.append("capture_not_declared_complete")
    if coverage.get("content_complete") is not True:
        gaps.append("capture_content_incomplete")
    if coverage.get("gaps"):
        gaps.append("importer_reported_capture_gaps")
    bundle = bundle or build_bundle(source, online=False)
    evidence = _Evidence(bundle, report)
    responses = {}
    for step in source["steps"]:
        try:
            observation = envelope(step["env_text"])
            if observation["type"] == "tool_result":
                key = (observation["trace_id"], observation["span_id"], observation["tool"])
                responses.setdefault(key, []).append((step, observation))
        except (ValueError, TypeError, KeyError):
            gaps.append(f"step_{step['t']}:invalid_observation")
    seen = set()
    for step in source["steps"]:
        text = step["parts"].get("action")
        if text is None:
            continue
        t = step["t"]
        report["coverage"]["total_actions"] += 1
        local_gaps, pending = [], []
        try:
            if len(text) > MAX_ACTION_CHARS:
                raise ValueError("oversized_action")
            action = envelope(text, "tool_call")
            locations = _locations(text)
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            gaps.append(f"step_{t}:unsupported_action_envelope")
            report["steps"].append({"step": t, "status": "unknown", "effect": "unknown", "flag_ids": []})
            continue
        identity = (action["trace_id"], action["span_id"], action["tool"])
        tool = action["tool"]
        row = {"step": t, "tool": tool, "kind": "unsupported", "target": None,
               "trace_id": identity[0], "span_id": identity[1], "status": "unknown",
               "effect": "unknown", "flag_ids": []}
        report["steps"].append(row)
        if identity in seen:
            local_gaps.append("duplicate_call_identity")
        seen.add(identity)
        tool_ev = evidence.member(t, "action", text, locations, ("tool",), identity)
        if not tool_ev:
            local_gaps.append("tool_name_query_unavailable")
        if tool in policy["denied_tools"] and tool_ev:
            pending.append(("denied_tool", [tool_ev], {}))
        matched_responses = responses.get(identity, [])
        # Nested calls may occur between a call and its response. Identity and
        # chronological order establish the match; adjacency does not.
        if len(matched_responses) != 1 or matched_responses[0][0]["t"] <= t:
            local_gaps.append("missing_or_ambiguous_matching_response")
        else:
            response_step, response = matched_responses[0]
            row["tool_status"] = response["status"]
            if "result" not in response:
                local_gaps.append("missing_tool_result_body")
            result = response.get("result")
            if _truncated(result):
                local_gaps.append("result_truncated_or_redacted")
            provenance = response.get("provenance")
            effect_paths = []
            claimed_blocked = ((isinstance(provenance, dict) and provenance.get("outcome") == "blocked")
                               or (isinstance(result, dict) and result.get("blocked") is True))
            if claimed_blocked and isinstance(result, dict) and result.get("executed") is True:
                local_gaps.append("conflicting_effect_claims")
            elif isinstance(provenance, dict) and provenance.get("outcome") == "blocked":
                effect_paths = [("provenance", "outcome")]
                effect_name = "blocked"
            elif isinstance(result, dict) and result.get("blocked") is True and result.get("executed") is not True:
                effect_paths = [("result", "blocked")]
                effect_name = "blocked"
            elif isinstance(result, dict) and result.get("executed") is True and result.get("ok") is True and response["status"] != "error":
                effect_paths = [("result", "executed"), ("result", "ok")]
                effect_name = "completed"
            if effect_paths:
                effect_evs = [evidence.member(response_step["t"], "observation", response_step["env_text"],
                                             _locations(response_step["env_text"]), path, identity) for path in effect_paths]
                if all(effect_evs):
                    row["effect"], row["effect_evidence"] = effect_name, effect_evs
        binding = policy["tools"].get(tool)
        if binding is None:
            local_gaps.append("unsupported_tool")
        else:
            kind = binding["kind"]
            row["kind"] = kind
            parameter = binding.get("path_arg", binding.get("url_arg", binding.get("command_arg")))
            arguments = action["arguments"]
            if _truncated(arguments):
                local_gaps.append("arguments_truncated_or_redacted")
            value = arguments.get(parameter) if isinstance(arguments, dict) else None
            if not isinstance(value, str) or not value:
                local_gaps.append("missing_or_unsupported_arguments")
            elif any(marker in value.lower() for marker in ("[truncated]", "[redacted]", "...(truncated)")):
                local_gaps.append("target_truncated_or_redacted")
            else:
                try:
                    value_path = ("arguments", parameter)
                    extra = {}
                    if kind in ("read", "write"):
                        if any(k in arguments for k in ("cwd", "workdir", "working_directory", "base_dir", "root")):
                            raise ValueError("per-call path resolution overrides are unsupported")
                        target = _canonical_path(value, policy["path_style"], policy["working_directory"])
                        row["target"] = target
                        candidates = []
                        if any(_under(target, p, policy["path_style"]) for p in policy["forbidden_paths"]):
                            candidates.append("forbidden_path")
                        if not any(_under(target, p, policy["path_style"]) for p in policy["allowed_roots"]):
                            candidates.append("outside_allowed_roots")
                        target_ev = evidence.member(t, "action", text, locations, value_path, identity)
                    elif kind == "network":
                        parts, host, port, default, authority = _url(value)
                        row["target"] = parts.scheme.lower() + "://" + authority
                        allowed = any(entry["host"] == host and entry.get("port", default) == port
                                      for entry in policy["allowed_network_hosts"])
                        candidates = [] if allowed else ["network_not_allowed"]
                        start, end, _ = locations[value_path]
                        raw_token = text[start:end]
                        # Capture just the real authority after userinfo. Escaped
                        # JSON URLs are unsupported rather than unescaped guesses.
                        if "\\" in raw_token:
                            raise ValueError("escaped URL provenance requires unsupported offset mapping")
                        position = value.index("//") + 2 + len(parts.netloc) - len(authority)
                        target_ev = evidence.get(t, "action", text, start + 1 + position,
                                                 start + 1 + position + len(authority), identity,
                                                 "/arguments/" + parameter + "/@authority")
                    else:
                        command_rules, argv = _command(value, policy)
                        # No arbitrary argv/body values are included in timeline.
                        row["target"] = "已记录的命令调用（参数见最小查询证据）"
                        candidates = ["dangerous_command"] if command_rules else []
                        extra = {"command_rule_ids": command_rules}
                        start, end, _ = locations[value_path]
                        if "\\" in text[start:end]:
                            raise ValueError("escaped command offset semantics are unsupported")
                        # Keep arbitrary argv values, which can be secrets, out
                        # of both queries and reports. Verify executable plus
                        # only the literal deny-rule switches used in a finding.
                        tokens = [argv[0]]
                        for rule in policy["denied_commands"]:
                            if rule["id"] in command_rules:
                                tokens.extend(a for a in rule["require_any_args"] if a in argv[1:])
                        proofs = []
                        for token in dict.fromkeys(tokens):
                            positions = [m.start() for m in re.finditer(re.escape(token), value)]
                            if len(positions) != 1:
                                raise ValueError("ambiguous command token occurrence")
                            token_start = start + 1 + positions[0]
                            proof = evidence.get(t, "action", text, token_start, token_start + len(token), identity,
                                                 "/arguments/" + parameter + "/@argv-token", _needle(token) or parameter)
                            if not proof:
                                raise ValueError("command token query unavailable")
                            proofs.append(proof)
                        target_ev = proofs[0]
                    if not target_ev:
                        local_gaps.append("target_query_unavailable_or_clipped")
                    elif tool_ev:
                        for rule_id in candidates:
                            pending.append((rule_id, [tool_ev, *(proofs if kind == "command" else [target_ev])], extra))
                except (ValueError, TypeError, KeyError, IndexError):
                    local_gaps.append("unsupported_target_semantics")
        for rule_id, proofs, extra in pending:
            ident = f"flag-{len(report['flags']) + 1:03d}"
            report["flags"].append({"id": ident, "rule_id": rule_id, "severity": "high",
                "title": RULES[rule_id][0], "step": t, "tool": tool, "trace_id": identity[0],
                "span_id": identity[1], "attempt": "attempted", "effect": row["effect"],
                "evidence": proofs, "effect_evidence": row.get("effect_evidence", []),
                "recommendation": RULES[rule_id][1], **extra})
            row["flag_ids"].append(ident)
        row["gaps"] = local_gaps
        row["status"] = "unsafe" if pending else "unknown" if local_gaps else "safe"
        if not local_gaps:
            report["coverage"]["assessed_actions"] += 1
        gaps.extend(f"step_{t}:{gap}" for gap in local_gaps)
    if not report["coverage"]["total_actions"]:
        gaps.append("no_auditable_tool_actions")
    if set(responses) - seen:
        gaps.append("unmatched_tool_responses")
    if len({identity[0] for identity in seen}) > 1:
        gaps.append("multiple_traces_in_one_source")
    report["coverage"]["gaps"] = sorted(set(gaps))
    report["status"] = "unsafe" if report["flags"] else "unknown" if gaps else "safe"
    report["status_label"] = {"safe": "本策略通过", "unsafe": "发现违规尝试", "unknown": "证据不足"}[report["status"]]
    report["rules"] = [{"id": key, "title": value[0], "flag_count": sum(f["rule_id"] == key for f in report["flags"])}
                       for key, value in RULES.items()]
    report["summary"] = {"flag_count": len(report["flags"]), "query_count": len(report["queries"]),
                         "model_calls": 0, "root_cause_evaluated": False}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Imported input.json")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; existing results are never overwritten")
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("output directory already exists; select a new path")
    try:
        policy = validate_policy(read_json(args.policy))
        report = audit_source(read_json(args.input), policy)
        args.output_dir.mkdir(parents=True, exist_ok=False)
        write_json(args.output_dir / "policy.json", policy)
        write_json(args.output_dir / "report.json", report)
        print(json.dumps({"status": report["status"], "flags": len(report["flags"]),
                          "report": str(args.output_dir / "report.json")}, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, "Audit failed: " + str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
