"""Export only manifest-selected native OpenClaw SQLite transcripts read-only.

This preserves session evidence and active-branch indices. It does not turn a
transcript into OTLP, infer missing spans, or assert capture completeness.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

from .otel_import import loads
from .record_bridge import write_json, valid_id


MAX_EVENT_BYTES = 4 * 1024 * 1024
TABLES = {"transcript_events", "session_transcript_index_state", "session_transcript_active_events"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def decompress_zstd(blob, expected_bytes, node):
    if not node:
        raise ValueError("compressed transcript requires --node with a Node runtime supporting zstd")
    if not 1 <= expected_bytes <= MAX_EVENT_BYTES or not 1 <= len(blob) <= MAX_EVENT_BYTES:
        raise ValueError("compressed transcript exceeds the native event size limit")
    script = ('const z=require("node:zlib");const chunks=[];process.stdin.on("data",x=>chunks.push(x));'
              'process.stdin.on("end",()=>{process.stdout.write(z.zstdDecompressSync(Buffer.concat(chunks),'
              '{maxOutputLength:4194304}));});')
    result = subprocess.run([str(node), "-e", script], input=blob, capture_output=True, timeout=15)
    if result.returncode or len(result.stdout) != expected_bytes:
        raise ValueError("native zstd transcript decoding failed or byte count mismatched")
    return result.stdout


def _authorize(action, table, column, database, source):
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if table in TABLES else sqlite3.SQLITE_DENY
    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _message_text(event):
    message = event.get("message", {})
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(p, dict) and p.get("type") == "text"
                                         and isinstance(p.get("text"), str) for p in content):
        return "\n".join(p["text"] for p in content)
    return None


def export_transcripts(manifest_path, database, output_dir=None, node=None):
    manifest_path, database = Path(manifest_path).resolve(), Path(database).resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest = loads(manifest_bytes.decode("utf-8-sig"))
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest requires a nonempty cases list")
    identities = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("session_id"), str) or not case["session_id"].strip():
            raise ValueError("every case needs an explicit native session_id")
        identities.append((valid_id(case["id"]), case["session_id"]))
    if len({i for i, _ in identities}) != len(identities) or len({s for _, s in identities}) != len(identities):
        raise ValueError("case IDs and native session IDs must be unique")
    destination = Path(output_dir).resolve() if output_dir else manifest_path.parent / "native-transcripts"
    destination.mkdir(parents=True, exist_ok=False)
    summary = {"schema": "openclaw-native-transcript-export/v1", "data_origin": "native_sqlite_transcript",
               "otlp": False, "created_at": datetime.now(timezone.utc).isoformat(), "cases": []}
    # The URI explicitly forbids writes; authorizer also blocks reads of auth or
    # credential tables even if a future query accidentally mentions one.
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.set_authorizer(_authorize)
    try:
        connection.execute("BEGIN")
        for case, (case_id, session_id) in zip(cases, identities):
            rows = connection.execute(
                "SELECT seq,created_at,event_json,event_zstd,event_utf8_bytes FROM transcript_events "
                "WHERE session_id=? ORDER BY seq", (session_id,)).fetchall()
            index_row = connection.execute(
                "SELECT indexed_seq,leaf_event_id,needs_rebuild,active_event_count,active_message_count,updated_at "
                "FROM session_transcript_index_state WHERE session_id=?", (session_id,)).fetchone()
            active = [dict(row) for row in connection.execute(
                "SELECT active_position,event_seq,message_position,context_eligible FROM session_transcript_active_events "
                "WHERE session_id=? ORDER BY active_position", (session_id,)).fetchall()]
            active_by_seq = {row["event_seq"]: row for row in active}
            folder = destination / case_id
            folder.mkdir()
            record = {"schema": "openclaw-native-transcript/v1", "data_origin": "native_sqlite_transcript", "otlp": False,
                      "case_id": case_id, "session_id": session_id, "capture_complete": False,
                      "source": {"database": str(database), "table": "transcript_events", "read_only": True,
                                 "selection": "session_id equals manifest case session_id",
                                 "manifest": str(manifest_path), "manifest_sha256": sha256(manifest_bytes)},
                      "active_index": dict(index_row) if index_row else None, "active_events": active, "events": []}
            raw_lines = []
            for row in rows:
                if row["event_json"] is not None:
                    if row["event_zstd"] is not None:
                        raise ValueError("native row has conflicting encodings")
                    raw = row["event_json"].encode("utf-8")
                    storage = raw
                    encoding = "identity"
                    if len(raw) > MAX_EVENT_BYTES:
                        raise ValueError("native transcript event exceeds size limit")
                else:
                    storage = row["event_zstd"]
                    if not isinstance(storage, bytes) or type(row["event_utf8_bytes"]) is not int:
                        raise ValueError("compressed native row lacks bounded byte length")
                    raw = decompress_zstd(storage, row["event_utf8_bytes"], node)
                    encoding = "zstd"
                    (folder / f"event-{row['seq']:06d}.zstd").write_bytes(storage)
                text = raw.decode("utf-8")
                event = loads(text)
                if not isinstance(event, dict):
                    raise ValueError("native transcript event must be an object")
                raw_lines.append(text)
                record["events"].append({"seq": row["seq"], "created_at": row["created_at"], "encoding": encoding,
                    "storage_sha256": sha256(storage), "event_sha256": sha256(raw), "event_utf8_bytes": len(raw),
                    "active_position": active_by_seq.get(row["seq"], {}).get("active_position"), "event": event})
            prompt_path = manifest_path.parent / "cases" / case_id / "prompt.txt"
            prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else None
            record["original_prompt_exact_match_seqs"] = [row["seq"] for row in record["events"]
                if prompt is not None and _message_text(row["event"]) == prompt]
            (folder / "events.jsonl").write_text("\n".join(raw_lines) + ("\n" if raw_lines else ""), encoding="utf-8")
            write_json(folder / "transcript.json", record)
            summary["cases"].append({"id": case_id, "session_id": session_id, "event_count": len(rows),
                "transcript_file": case_id + "/transcript.json", "events_file": case_id + "/events.jsonl",
                "original_prompt_exact_match_seqs": record["original_prompt_exact_match_seqs"],
                "active_index_needs_rebuild": index_row["needs_rebuild"] if index_row else None})
            write_json(destination / "manifest.json", summary)
        connection.rollback()
    finally:
        connection.close()
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--node", type=Path, help="Node runtime for bounded native zstd decompression")
    args = parser.parse_args(argv)
    try:
        result = export_transcripts(args.manifest, args.database, args.output_dir, args.node)
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        parser.exit(2, "Transcript export failed: " + type(exc).__name__ + "\n")
    print(json.dumps({"cases": len(result["cases"]), "event_counts": {c["id"]: c["event_count"] for c in result["cases"]},
                      "data_origin": result["data_origin"], "otlp": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
