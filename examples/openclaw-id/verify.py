"""Verify the single public input and saved audit snapshot without model calls.

Run from the repository root: python examples/openclaw-id/verify.py
Only aggregate checks are printed; potential credential values are never printed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "extensions/openclaw"))

from prodebugger.record_bridge import digest
from prodebugger.signoz_import import validate_signoz_source


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def pointer(value, path):
    if not path:
        return value
    for key in path.lstrip("/").split("/"):
        key = key.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def verify():
    source = load(HERE / "trajectory.json")
    policy = load(HERE / "policy.json")
    risk_policy = load(HERE / "risk-policy.json")
    envelope = load(HERE / "import.json")
    assert set(envelope) == {"title", "source", "policy", "risk_policy"}
    assert envelope["source"] == source
    assert envelope["policy"] == policy
    assert envelope["risk_policy"] == risk_policy
    snapshot_path = ROOT / "web/static/openclaw/assets/case-data.json"
    snapshot = load(snapshot_path)
    replay = validate_signoz_source(source)
    assert replay["validation"] == "canonical_signoz_document_replay"
    assert source["trajectory_id"] == snapshot["meta"]["case_id"] == "ca30-d27b918e"
    assert snapshot["meta"]["constructed"] is True
    assert snapshot["meta"]["executed"] is False
    assert len(source["steps"]) == len(source["session"]["events"]) == len(snapshot["events"]) == 15
    assert len(snapshot["facts"]) == snapshot["meta"]["canonical_fact_count"] == 90
    calls = [call for stage in snapshot["stages"] for call in stage["calls"]]
    assert len(calls) == snapshot["meta"]["model_call_count"] == 12
    assert [stage["id"] for stage in snapshot["stages"]] == ["PARSE", "MAP", "ROUTE", "TRACE", "SUBMIT"]
    assert all(call.get("request") and call.get("response") for call in calls)
    assert sum(bool(stage["queries"]) for stage in snapshot["stages"]) > 0
    assert source["task"] == snapshot["task"]["request"]
    assert risk_policy == snapshot["policy"]
    assert source["session"]["source_kind"] == "constructed_variant_from_signoz_export"
    assert all(declaration["executed"] is False for declaration in source["session"]["source_declarations"])

    for original, displayed in zip(source["session"]["events"], snapshot["events"]):
        assert original["event_id"] == displayed["event_id"]
        assert original["step"] == displayed["step"]
        assert original.get("text", "") == displayed.get("text", "")

    facts = {fact["fid"]: fact for fact in snapshot["facts"]}
    assert set(facts) == set(range(90))
    assert all(fact["id"] == fid for fid, fact in facts.items())
    assert sum(fact["relation"] == "session_read_target" for fact in facts.values()) == 2
    for fact in facts.values():
        if fact["relation"] == "session_file_path":
            expected_id = hashlib.sha256(fact["value"].encode("utf-8")).hexdigest()[:20]
            assert fact["args"][0]["index"] == expected_id
        for arg, entity_id in zip(fact["args"], fact["entity_ids"]):
            assert entity_id == arg["kind"] + ":" + arg["index"]
            assert entity_id in snapshot["entities"]
    assert snapshot["provenance"]["published_source_sha256"] == digest(source)
    assert "source_provenance_sha256" in snapshot["provenance"]
    assert all(item["hash_scope"] == "historical_private_source_before_public_normalization"
               for item in snapshot["provenance"]["files"])
    assert "compilation_receipt" not in snapshot["provenance"]

    documents = {doc["name"]: doc for doc in source["session"]["raw_documents"]}
    assert len(documents) == 8
    for document in documents.values():
        assert hashlib.sha256(document["text"].encode("utf-8")).hexdigest() == document["sha256"]
        resource = json.loads(document["text"]).get("resource", {})
        assert resource.get("host.name") == "DEMO-HOST"
        assert resource.get("process.owner") == "demo-user"
        assert resource.get("signoz.workspace.key.id") == "public-demo-workspace"

    # The same normalized Windows file paths must be used by tool parameters
    # and both policies. These are fictional labels, never files to execute.
    def windows_path(path):
        return re.sub(r"\\+", r"\\", path).lower().rstrip("\\")
    allowed = {windows_path(path) for path in policy["allowed_roots"]}
    assert allowed == {windows_path(path) for path in risk_policy["read_scope"]["allowed_roots"]}
    read_paths = {windows_path(fact["value"]) for fact in facts.values()
                  if fact["relation"] == "session_read_target"}
    assert read_paths == allowed

    counts = {"document_references": 0, "exact_quote_ranges": 0, "decoded_json_containers": 0,
              "fact_reference_lists": 0, "secret_pattern_matches": 0, "private_path_matches": 0}
    secret_patterns = [
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
        re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{16,}"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ]
    private_path = re.compile(r"(?i)(?:\b[D-Z]:[\\/]|\bC:[\\/]+Users[\\/]|/Users/|/home/|LAPTOP-[A-Z0-9]{5,})")

    def walk(value):
        if isinstance(value, dict):
            name = value.get("document")
            if name in documents:
                counts["document_references"] += 1
                for key in ("document_sha256", "sha256"):
                    if key in value:
                        assert value[key] == documents[name]["sha256"], "document digest mismatch"
            if isinstance(value.get("source_pointer"), str):
                path = value["source_pointer"]
                if path.startswith(("/task", "/steps/", "/session/")):
                    original_text = pointer(source, path)
                    if isinstance(original_text, str):
                        for prefix in ("", "subject_"):
                            keys = [prefix + key for key in ("quote", "start", "end")]
                            if all(key in value for key in keys):
                                quote, start, end = [value[key] for key in keys]
                                if isinstance(quote, str) and isinstance(start, int) and isinstance(end, int):
                                    assert original_text[start:end] == quote, "published quote/range mismatch"
                                    counts["exact_quote_ranges"] += 1
                        if "source_text_sha256" in value:
                            assert digest(original_text) == value["source_text_sha256"]
            for key, item in value.items():
                if key in {"fact_ids", "basis"} and isinstance(item, list) and all(type(i) is int for i in item):
                    assert all(fid in facts for fid in item), "dangling fact reference"
                    counts["fact_reference_lists"] += 1
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            counts["secret_pattern_matches"] += sum(len(pattern.findall(value)) for pattern in secret_patterns)
            counts["private_path_matches"] += len(private_path.findall(value))
            try:
                decoded = json.loads(value)
            except ValueError:
                return
            if isinstance(decoded, (dict, list)):
                counts["decoded_json_containers"] += 1
                walk(decoded)

    for value in (source, policy, risk_policy, snapshot):
        walk(value)
    assert counts["secret_pattern_matches"] == counts["private_path_matches"] == 0, "privacy scan found prohibited patterns"
    assert "DEMO-ID-0001" in json.dumps(snapshot, ensure_ascii=False)
    manifest = load(HERE / "publication.json")
    assert any(entry["path"] == "examples/openclaw-id/import.json" for entry in manifest["published_files"])
    for entry in manifest["published_files"]:
        path = ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], "published file digest mismatch"
    result = {"status": "verified", "case_id": "ca30-d27b918e", "events": 15, "reads": 2,
              "saved_facts": 90, "saved_model_calls": 12, "model_calls_during_verification": 0,
              "strict_raw_document_replay": True, "single_file_import_envelope": True, **counts}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    verify()


