"""Offline checks for native transcript selection, provenance, and read isolation."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from prodebugger import openclaw_transcript as native


class NativeTranscriptChecks(unittest.TestCase):
    def fixture(self, root, rows=None, cases=None):
        database = root / "agent.sqlite"
        connection = sqlite3.connect(database)
        connection.executescript("""
            CREATE TABLE transcript_events (
                session_id TEXT, seq INTEGER, created_at TEXT,
                event_json TEXT, event_zstd BLOB, event_utf8_bytes INTEGER
            );
            CREATE TABLE session_transcript_index_state (
                session_id TEXT, indexed_seq INTEGER, leaf_event_id TEXT,
                needs_rebuild INTEGER, active_event_count INTEGER,
                active_message_count INTEGER, updated_at TEXT
            );
            CREATE TABLE session_transcript_active_events (
                session_id TEXT, active_position INTEGER, event_seq INTEGER,
                message_position INTEGER, context_eligible INTEGER
            );
            CREATE TABLE auth_credentials (secret TEXT);
            INSERT INTO auth_credentials VALUES ('synthetic-secret-do-not-export');
        """)
        events = [
            {"type": "session", "id": "selected-session"},
            {"type": "message", "message": {"role": "user", "content": [
                {"type": "text", "text": "读取任务文件"}]}},
            {"type": "message", "message": {"role": "assistant", "content": [
                {"type": "toolCall", "id": "original-call", "name": "read",
                 "arguments": {"path": "fixture.txt"}}]}},
        ]
        raw_events = [json.dumps(event, ensure_ascii=False) for event in events]
        if rows is None:
            rows = [("selected-session", seq, "2026-10-21", raw, None, None)
                    for seq, raw in zip((0, 4, 9), raw_events)]
        connection.executemany("INSERT INTO transcript_events VALUES (?,?,?,?,?,?)", rows)
        connection.execute("INSERT INTO transcript_events VALUES (?,?,?,?,?,?)",
                           ("unrelated-session", 0, "2026-10-21",
                            '{"type":"message","secret":"unrelated-session-private-data"}', None, None))
        connection.execute("INSERT INTO session_transcript_index_state VALUES (?,?,?,?,?,?,?)",
                           ("selected-session", 9, "leaf-id", 0, 2, 2, "2026-10-21"))
        connection.executemany("INSERT INTO session_transcript_active_events VALUES (?,?,?,?,?)",
                               [("selected-session", 0, 4, 0, 1), ("selected-session", 1, 9, 1, 1)])
        connection.commit()
        connection.close()
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"cases": cases if cases is not None else [
            {"id": "case-01", "session_id": "selected-session"}]}), encoding="utf-8")
        prompts = root / "cases/case-01"
        prompts.mkdir(parents=True)
        (prompts / "prompt.txt").write_text("读取任务文件", encoding="utf-8")
        return manifest, database, events, raw_events

    def test_selects_only_requested_session_and_preserves_bytes_sequence_and_active_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, database, events, raw_events = self.fixture(root)
            before = database.read_bytes()  # This fixture contains synthetic data only.
            summary = native.export_transcripts(manifest, database)
            output = root / "native-transcripts"
            record = json.loads((output / "case-01/transcript.json").read_text(encoding="utf-8"))
            self.assertEqual(database.read_bytes(), before)
            self.assertEqual([row["event"] for row in record["events"]], events)
            self.assertEqual([row["seq"] for row in record["events"]], [0, 4, 9])
            self.assertEqual([row["active_position"] for row in record["events"]], [None, 0, 1])
            self.assertEqual(record["active_index"]["leaf_event_id"], "leaf-id")
            self.assertEqual(record["original_prompt_exact_match_seqs"], [4])
            self.assertFalse(record["capture_complete"])
            self.assertFalse(record["otlp"])
            self.assertTrue(record["source"]["read_only"])
            self.assertEqual(record["source"]["manifest_sha256"], hashlib.sha256(manifest.read_bytes()).hexdigest())
            for raw, row in zip(raw_events, record["events"]):
                digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                self.assertEqual(row["event_sha256"], digest)
                self.assertEqual(row["storage_sha256"], digest)
                self.assertEqual(row["event_utf8_bytes"], len(raw.encode("utf-8")))
            self.assertEqual((output / "case-01/events.jsonl").read_text(encoding="utf-8").splitlines(), raw_events)
            self.assertEqual(summary["cases"][0]["event_count"], 3)
            for path in output.rglob("*"):
                if path.is_file():
                    contents = path.read_text(encoding="utf-8")
                    self.assertNotIn("synthetic-secret-do-not-export", contents)
                    self.assertNotIn("unrelated-session-private-data", contents)

    def test_authorizer_allows_transcript_reads_and_blocks_credentials_and_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            _, database, _, _ = self.fixture(Path(directory))
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
            connection.set_authorizer(native._authorize)
            try:
                self.assertEqual(len(connection.execute(
                    "SELECT seq FROM transcript_events WHERE session_id=?", ("selected-session",)).fetchall()), 3)
                for statement in ("SELECT secret FROM auth_credentials", "DELETE FROM transcript_events",
                                  "PRAGMA query_only=OFF", "ATTACH DATABASE ':memory:' AS other"):
                    with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                        connection.execute(statement)
            finally:
                connection.close()

    def test_compressed_event_keeps_original_blob_and_both_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'{"type":"message","message":{"role":"toolResult","toolCallId":"original-call"}}'
            blob = b"synthetic-zstd-blob"
            manifest, database, _, _ = self.fixture(root, rows=[
                ("selected-session", 9, "2026-10-21", None, blob, len(raw))])
            with patch.object(native, "decompress_zstd", return_value=raw) as decoder:
                native.export_transcripts(manifest, database, node="node-test")
            decoder.assert_called_once_with(blob, len(raw), "node-test")
            folder = root / "native-transcripts/case-01"
            row = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))["events"][0]
            self.assertEqual((folder / "event-000009.zstd").read_bytes(), blob)
            self.assertEqual(row["encoding"], "zstd")
            self.assertEqual(row["event_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(row["storage_sha256"], hashlib.sha256(blob).hexdigest())

    def test_decoder_rejects_missing_runtime_oversize_and_mismatched_output(self):
        with patch.object(native.subprocess, "run") as run:
            for node, blob, size in ((None, b"blob", 4), ("node", b"blob", 0),
                                     ("node", b"blob", native.MAX_EVENT_BYTES + 1),
                                     ("node", b"x" * (native.MAX_EVENT_BYTES + 1), 4)):
                with self.subTest(node=node, size=size), self.assertRaises(ValueError):
                    native.decompress_zstd(blob, size, node)
            run.assert_not_called()
            for result in (Mock(returncode=1, stdout=b"data"), Mock(returncode=0, stdout=b"short")):
                run.return_value = result
                with self.assertRaises(ValueError):
                    native.decompress_zstd(b"blob", 4, "node")
            run.return_value = Mock(returncode=0, stdout=b"data")
            self.assertEqual(native.decompress_zstd(b"blob", 4, "node"), b"data")
            self.assertEqual(run.call_args.kwargs["input"], b"blob")
            self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_duplicate_identifiers_and_existing_destination_are_rejected(self):
        for duplicate in ("id", "session_id"):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                second = {"id": "case-02", "session_id": "second-session"}
                second[duplicate] = {"id": "case-01", "session_id": "selected-session"}[duplicate]
                manifest, database, _, _ = self.fixture(root, cases=[
                    {"id": "case-01", "session_id": "selected-session"}, second])
                with self.assertRaises(ValueError):
                    native.export_transcripts(manifest, database)
                self.assertFalse((root / "native-transcripts").exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, database, _, _ = self.fixture(root)
            destination = root / "native-transcripts"
            destination.mkdir()
            marker = destination / "existing-evidence.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                native.export_transcripts(manifest, database)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_conflicting_storage_encodings_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, database, _, _ = self.fixture(root, rows=[
                ("selected-session", 1, "2026-10-21", '{"type":"session"}', b"other", 18)])
            with self.assertRaisesRegex(ValueError, "conflicting encodings"):
                native.export_transcripts(manifest, database)


if __name__ == "__main__":
    unittest.main()
