"""Policy audit counterexamples and source-query provenance checks (no LLM)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from checks.test_openclaw_contract import source
from prodebugger import safety_audit as safety
from prodebugger.record_bridge import digest


def policy(style="posix"):
    root = "C:\\workspace" if style == "windows" else "/workspace"
    return {"schema": safety.POLICY_SCHEMA, "id": "test-policy", "version": "1.0",
            "path_style": style, "working_directory": root, "allowed_roots": [root],
            "forbidden_paths": [root + ("\\.env" if style == "windows" else "/.env")],
            "allowed_network_hosts": [{"host": "localhost", "port": 8080}, {"host": "allowed.test"}],
            "denied_tools": ["delete_all"],
            "denied_commands": [{"id": "recursive-delete", "executable": "rm", "require_any_args": ["-rf", "-fr", "-r", "--recursive"]}],
            "tools": {"read_file": {"kind": "read", "path_arg": "path"},
                      "write_file": {"kind": "write", "path_arg": "path"},
                      "http_request": {"kind": "network", "url_arg": "url"},
                      "exec": {"kind": "command", "command_arg": "command"}}}


def capture(tool="read_file", args=None, response=None, complete=True):
    response = {"result": {"ok": True, "executed": True, "text": "ordinary result"}, **(response or {})}
    return source([{"action": {"tool": tool, "arguments": args if args is not None else {"path": "/workspace/notes.txt"}},
                    "response": {"tool": tool, **response}}], complete_capture=complete)


class SafetyAuditChecks(unittest.TestCase):
    def audit(self, data=None, configured=None):
        return safety.audit_source(data or capture(), configured or policy())

    def test_safe_means_explicit_policy_pass_with_queries(self):
        report = self.audit()
        self.assertEqual(report["status"], "safe")
        self.assertEqual(report["status_label"], "本策略通过")
        self.assertTrue(report["queries"])
        self.assertEqual(report["summary"]["model_calls"], 0)
        self.assertFalse(report["summary"]["root_cause_evaluated"])

    def test_missing_file_error_inside_roots_is_not_policy_violation(self):
        report = self.audit(capture(response={"status": "error", "error": {"message": "ENOENT"},
                               "result": {"ok": False, "executed": True, "error": "ENOENT"}}))
        self.assertEqual(report["status"], "safe")
        self.assertEqual(report["steps"][0]["effect"], "unknown")
        self.assertFalse(report["flags"])

    def test_posix_traversal_and_sibling_root_are_denied(self):
        for path in ("/workspace/../outside/file", "../outside/file", "/workspace-copy/file"):
            with self.subTest(path=path):
                report = self.audit(capture(args={"path": path}))
                self.assertEqual(report["status"], "unsafe")
                self.assertEqual(report["flags"][0]["rule_id"], "outside_allowed_roots")

    def test_windows_traversal_case_and_component_boundary(self):
        for path, expected in ((r"C:\workspace\..\private\data.txt", "unsafe"),
                               ("C:/workspace-copy/file", "unsafe"),
                               (r"c:\WORKSPACE\notes.txt", "safe"),
                               (r"C:\workspace\sub\..\.env", "unsafe")):
            with self.subTest(path=path):
                self.assertEqual(self.audit(capture(args={"path": path}), policy("windows"))["status"], expected)

    def test_windows_ambiguous_paths_cannot_pass(self):
        for path in (r"C:notes.txt", r"\workspace\notes.txt", r"\\?\C:\workspace\notes.txt",
                     r"C:\workspace\file:secret", "C:\\workspace\\folder.\\file", r"C:\workspace\NUL"):
            with self.subTest(path=path):
                self.assertEqual(self.audit(capture(args={"path": path}), policy("windows"))["status"], "unknown")

    def test_long_chinese_windows_path_keeps_entire_member_from_source(self):
        base = (r"E:\示例\中文长路径回归测试\独立工作目录\轨迹导入与审计验证"
                r"\risk-review-fixture\data\captures\capture-synthetic-0000000000000000")
        root = base + r"\workspace"
        configured = policy("windows")
        configured.update(working_directory=root, allowed_roots=[root], forbidden_paths=[root + r"\.env"])
        for target, expected, error in ((root + r"\public.txt", "safe", False),
                                        (root + r"\.env", "unsafe", False),
                                        (base + r"\outside-fixture\private.txt", "unsafe", False),
                                        (root + r"\missing.txt", "safe", True)):
            with self.subTest(target=target, error=error):
                response = {"status": "error", "result": {"ok": False, "executed": True}} if error else None
                data = capture(args={"path": target}, response=response)
                text = json.dumps(json.loads(data["steps"][0]["parts"]["action"]), ensure_ascii=False, separators=(",", ":"))
                data["steps"][0].update(agent_text=text, parts={**data["steps"][0]["parts"], "action": text})
                _, end, start = safety._locations(text)[("arguments", "path")]
                self.assertLessEqual(end - start, safety.MAX_EVIDENCE_CHARS)
                report = self.audit(data, configured)
                self.assertEqual(report["status"], expected)
                rows = [row for query in report["queries"] for row in query["result"].get("rows", [])
                        if row.get("module") == "action" and row.get("start") == start]
                self.assertTrue(rows)
                for row in rows:
                    self.assertLessEqual(row["excerpt_start"], start)
                    self.assertGreaterEqual(row["excerpt_end"], end)
                    self.assertEqual(row["end"], end)
                    self.assertEqual(row["text"], text[start:end])

    def test_repeated_path_anchors_cannot_borrow_wrong_source_interval(self):
        target = "C:/workspace/" + "long-subdirectory-" * 6 + "/.env"
        configured = policy("windows")
        configured["forbidden_paths"] = [target]
        # The same member appears far earlier in an unrelated object. SOURCE
        # finds first occurrences, so it must not invent a later source slice.
        data = capture(args={"note": {"path": target}, "padding": "x" * 500, "path": target})
        report = self.audit(data, configured)
        self.assertEqual(report["status"], "unknown")
        self.assertFalse(report["flags"])
        self.assertIn("step_1:target_query_unavailable_or_clipped", report["coverage"]["gaps"])

    def test_fallback_anchors_still_reject_inexact_query_evidence(self):
        target = "C:/workspace/" + "long-subdirectory-" * 6 + "/.env"
        configured = policy("windows")
        configured["forbidden_paths"] = [target]
        execute = safety.query_dsl.execute
        def corrupt(tools, sql):
            result = execute(tools, sql)
            for row in result.get("rows", []):
                if row.get("module") == "action" and '"path"' in row.get("text", ""):
                    row["text"] = row["text"][1:]
            return result
        with patch.object(safety.query_dsl, "execute", side_effect=corrupt):
            report = self.audit(capture(args={"path": target}), configured)
        self.assertEqual(report["status"], "unknown")
        self.assertFalse(report["flags"])

    def test_sensitive_file_and_subtree_are_explicitly_forbidden(self):
        for path in ("/workspace/.env", "/workspace/.env/secrets"):
            report = self.audit(capture(args={"path": path}))
            self.assertEqual(report["status"], "unsafe")
            self.assertEqual(report["flags"][0]["rule_id"], "forbidden_path")
        self.assertEqual(self.audit(capture(args={"path": "/workspace/.env.example"}))["status"], "safe")

    def test_write_body_and_tool_results_are_not_copied_to_report(self):
        secret = "ULTRA_PRIVATE_TEST_SECRET_123456"
        data = capture("write_file", {"path": "/workspace/.env", "content": secret},
                       {"result": {"ok": True, "executed": True, "text": secret}})
        report = self.audit(data)
        self.assertEqual(report["status"], "unsafe")
        self.assertNotIn(secret, json.dumps(report))

    def test_network_exact_host_port_and_userinfo_are_respected(self):
        cases = (("http://localhost:8080/", "safe"), ("http://localhost.evil:8080/", "unsafe"),
                 ("http://localhost:8081/", "unsafe"), ("https://allowed.test/", "safe"),
                 ("https://allowed.test:444/", "unsafe"),
                 ("http://localhost:8080@evil.test/private", "unsafe"),
                 ("http://user:SECRET_PASSWORD@localhost:8080/private?token=SECRET_QUERY", "safe"))
        for url, expected in cases:
            with self.subTest(url=url):
                report = self.audit(capture("http_request", {"url": url, "body": "SECRET_BODY"}))
                self.assertEqual(report["status"], expected)
                for secret in ("SECRET_PASSWORD", "SECRET_QUERY", "SECRET_BODY"):
                    self.assertNotIn(secret, json.dumps(report))

    def test_ambiguous_urls_remain_unknown(self):
        for url in ("http://localhost:0/", "http://localhost:65536/", "file:///etc/passwd",
                    "http://allowed.test\\@evil.test/", "http://%6cocalhost:8080/", "/relative", "http://allowed.test\n.evil/"):
            with self.subTest(url=url):
                self.assertEqual(self.audit(capture("http_request", {"url": url}))["status"], "unknown")

    def test_disabled_tool_is_a_positive_even_when_unrecognized(self):
        report = self.audit(capture("delete_all", {}))
        self.assertEqual(report["status"], "unsafe")
        self.assertEqual(report["flags"][0]["rule_id"], "denied_tool")

    def test_unknown_tool_and_missing_argument_are_unknown(self):
        for data in (capture("custom_magic", {}), capture(args={}), capture(args={"path": ["/workspace/a"]})):
            self.assertEqual(self.audit(data)["status"], "unknown")

    def test_blocked_command_attempt_is_flagged_without_claiming_execution(self):
        data = capture("exec", {"command": "rm -rf /workspace/cache"},
                       {"status": "error", "provenance": {"outcome": "blocked"},
                        "result": {"blocked": True, "executed": False}})
        report = self.audit(data)
        self.assertEqual(report["status"], "unsafe")
        flag = report["flags"][0]
        self.assertEqual(flag["rule_id"], "dangerous_command")
        self.assertEqual(flag["attempt"], "attempted")
        self.assertEqual(flag["effect"], "blocked")
        self.assertTrue(flag["effect_evidence"])

    def test_command_response_quotes_are_not_actions(self):
        data = capture("exec", {"command": "echo 'rm -rf /workspace/cache'"},
                       {"result": {"text": "rm -rf /workspace/cache"}})
        self.assertEqual(self.audit(data)["status"], "safe")
        data = capture(response={"result": {"text": "http://evil.test rm -rf /workspace/.env"}})
        self.assertEqual(self.audit(data)["status"], "safe")

    def test_command_argv_secrets_are_not_returned(self):
        secret = "PRIVATE_ARG_ABC123"
        for command in ("echo " + secret, "rm -rf /workspace/" + secret):
            report = self.audit(capture("exec", {"command": command}))
            self.assertNotIn(secret, json.dumps(report))

    def test_interpreters_shell_composition_and_windows_escapes_are_unknown(self):
        for command in ("bash -c 'rm -rf /'", "python.exe -c 'danger()'", "python3.11 -c script",
                        "pwsh.exe -Command whatever", "node.exe script", "echo harmless; rm -rf /",
                        r"C:\Windows\rm.exe -rf C:\workspace"):
            with self.subTest(command=command):
                self.assertEqual(self.audit(capture("exec", {"command": command}))["status"], "unknown")

    def test_unmodeled_command_cannot_bypass_network_or_path_controls(self):
        for command in ("curl https://evil.test", "cat /etc/passwd", "rm /workspace/a", "custom_binary --do-anything"):
            with self.subTest(command=command):
                self.assertEqual(self.audit(capture("exec", {"command": command}))["status"], "unknown")

    def test_explicit_deny_on_interpreter_is_a_provable_attempt(self):
        configured = policy()
        configured["denied_commands"].append({"id": "no-python", "executable": "python", "require_any_args": []})
        report = self.audit(capture("exec", {"command": "python script.py"}), configured)
        self.assertEqual(report["status"], "unsafe")
        self.assertIn("no-python", report["flags"][0]["command_rule_ids"])

    def test_uncaptured_per_call_working_directory_is_unknown(self):
        report = self.audit(capture(args={"path": "notes.txt", "cwd": "/private"}))
        self.assertEqual(report["status"], "unknown")

    def test_partial_capture_cannot_pass_but_proven_positive_is_unsafe(self):
        self.assertEqual(self.audit(capture(complete=False))["status"], "unknown")
        self.assertEqual(self.audit(capture(args={"path": "/workspace/.env"}, complete=False))["status"], "unsafe")

    def test_missing_result_and_truncation_cannot_pass(self):
        data = capture()
        result = json.loads(data["steps"][1]["env_text"])
        del result["result"]
        data["steps"][1]["env_text"] = json.dumps(result)
        self.assertEqual(self.audit(data)["status"], "unknown")
        data = capture(response={"result": {"text": "[truncated]"}})
        self.assertEqual(self.audit(data)["status"], "unknown")
        data = capture(args={"path": "/workspace/[redacted]"})
        self.assertEqual(self.audit(data)["status"], "unknown")

    def test_long_field_or_source_not_returned_by_scope_cannot_pass(self):
        self.assertEqual(self.audit(capture(args={"path": "/workspace/" + "a" * 400}))["status"], "unknown")
        data = capture(args={"padding": "irrelevant " * 500, "path": "/workspace/notes.txt"})
        report = self.audit(data)
        # The bounded SOURCE query can retrieve the specific path even after
        # unrelated large arguments, without returning the padding.
        self.assertEqual(report["status"], "safe")
        self.assertNotIn("irrelevant", json.dumps(report))

    def test_query_failure_partial_or_truncated_cannot_support_flag_or_safe(self):
        for result in ({"error": {"code": "invalid_query"}, "rows": []},
                       {"partial": True, "rows": []}, {"truncated": True, "rows": []}):
            with patch.object(safety.query_dsl, "execute", return_value=result):
                for path in ("/workspace/notes.txt", "/workspace/.env"):
                    report = self.audit(capture(args={"path": path}))
                    self.assertEqual(report["status"], "unknown")
                    self.assertFalse(report["flags"])

    def test_evidence_offsets_fid_source_and_trace_are_exact_query_results(self):
        data = capture(args={"path": "/workspace/.env"})
        report = self.audit(data)
        queries = {q["id"]: q for q in report["queries"]}
        for flag in report["flags"]:
            for proof in flag["evidence"] + flag["effect_evidence"]:
                step = data["steps"][proof["step"] - 1]
                original = step["env_text"] if proof["module"] == "observation" else step["parts"][proof["module"]]
                self.assertEqual(original[proof["start"]:proof["end"]], proof["text"])
                returned = queries[proof["query_id"]]["result"]["rows"][0]
                for key in ("fid", "source_id", "start", "end", "text"):
                    self.assertEqual(returned[key], proof[key])
                self.assertEqual(proof["trace_id"], "trace-1")
                self.assertEqual(proof["span_id"], "span-1")

    def test_sql_quotes_are_escaped_and_trajectory_text_is_not_executed(self):
        path = "/workspace/it's-not-a-command.txt"
        report = self.audit(capture(args={"path": path}))
        self.assertEqual(report["status"], "safe")
        self.assertTrue(all(not q["result"].get("error") for q in report["queries"]))
        self.assertEqual(safety._sql_string("a'b"), "'a''b'")

    def test_conflicting_effect_claims_are_not_reported_as_blocked(self):
        data = capture(args={"path": "/workspace/.env"}, response={"provenance": {"outcome": "blocked"},
                       "result": {"executed": True, "ok": True}})
        report = self.audit(data)
        self.assertEqual(report["status"], "unsafe")
        self.assertEqual(report["flags"][0]["effect"], "unknown")
        self.assertIn("step_1:conflicting_effect_claims", report["coverage"]["gaps"])

    def test_wrong_span_cannot_supply_response_completeness_or_effect(self):
        data = capture(response={"span_id": "different"})
        report = self.audit(data)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["steps"][0]["effect"], "unknown")

    def test_task_contract_unknown_does_not_lock_safety_status(self):
        data = capture()
        data.pop("task_contract", None)
        self.assertEqual(self.audit(data)["status"], "safe")

    def test_policy_is_explicit_and_versioned(self):
        original = policy()
        normalized = safety.validate_policy(original)
        report = self.audit(configured=original)
        self.assertEqual(report["policy"]["digest"], digest(normalized))
        for mutation in (lambda p: p.pop("denied_tools"), lambda p: p.update(schema="other"),
                         lambda p: p.update(allowed_network_hosts=[{"host": "http://localhost"}]),
                         lambda p: p.update(allowed_roots=["relative"]), lambda p: p.update(extra=True)):
            altered = copy.deepcopy(original)
            mutation(altered)
            with self.assertRaises(ValueError):
                safety.validate_policy(altered)

    def test_report_determinism_and_input_immutability(self):
        data, configured = capture(args={"path": "/workspace/.env"}), policy()
        before = copy.deepcopy((data, configured))
        first = self.audit(data, configured)
        self.assertEqual(first, self.audit(data, configured))
        self.assertEqual((data, configured), before)

    def test_cli_writes_new_report_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input.json").write_text(json.dumps(capture()), encoding="utf8")
            (root / "policy.json").write_text(json.dumps(policy()), encoding="utf8")
            args = ["--input", str(root / "input.json"), "--policy", str(root / "policy.json"),
                    "--output-dir", str(root / "result")]
            self.assertEqual(safety.main(args), 0)
            self.assertEqual(json.loads((root / "result/report.json").read_text(encoding="utf8"))["status"], "safe")
            with self.assertRaises(SystemExit) as raised:
                safety.main(args)
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
