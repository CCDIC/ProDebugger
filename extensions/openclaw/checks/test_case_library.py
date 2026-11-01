"""Recoverable library deletion: original evidence and review jobs remain intact."""
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from checks.test_otel_import import exported, normalized, task_spec, tool
from prodebugger.otel_import import convert
from prodebugger.record_bridge import read_json, write_json
from prodebugger.safety_workbench import (CaseLibraryConflict, Handler, SafetyHTTPServer,
                                        SafetyStore, default_policy)


class CaseLibraryChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SafetyStore(Path(self.temp.name) / "data")
        self.policy = default_policy("/workspace")
        self.source, _ = convert(normalized(exported(tool(arguments={"path": "/workspace/public.txt"}))), task_spec())
        self.ids = [self.store.add_source(self.source, self.policy, title=title, case_id=title)["id"]
                    for title in ("base", "variant", "import-one", "import-two")]
        write_json(self.store.folder / "privacy-showcase.json", {
            "base_case_id": "base", "cases": [{"case_id": "base"}, {"case_id": "variant"}]})

    def tearDown(self):
        self.temp.cleanup()

    def snapshot_files(self):
        return {p.relative_to(self.store.folder).as_posix(): p.read_bytes()
                for p in self.store.folder.rglob("*") if p.is_file() and p != self.store.index_file}

    def test_delete_restart_and_restore_preserve_files_and_historical_job(self):
        job_path = self.store.folder / "risk-jobs" / "saved" / "job.json"
        write_json(job_path, {"id": "saved", "case_id": "import-one", "status": "succeeded",
                              "result": {"findings": [{"status": "suspected"}]}})
        files = self.snapshot_files()
        original = copy.deepcopy(self.store._find("import-one"))
        self.store._bundles["cached"] = object()
        self.store._risk_reports["cached"] = {}
        response = self.store.delete_cases({"case_ids": ["import-one", "import-two"]})
        self.assertEqual(response["deleted_count"], 2)
        self.assertEqual(self.snapshot_files(), files)
        self.assertFalse(self.store._bundles)
        self.assertFalse(self.store._risk_reports)
        reopened = SafetyStore(self.store.folder)
        self.assertIsNone(reopened._find("import-one"))
        with self.assertRaises(KeyError):
            reopened._material("import-one")
        self.assertNotIn("import-one", reopened.state()["workspace_case_ids"])
        self.assertEqual(reopened.deleted_cases()["count"], 2)
        self.assertIsNone(reopened._risk_jobs)
        self.assertIsNone(reopened._diagnoses)
        result = reopened.restore_cases({"case_ids": ["import-one", "import-two"]})
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(reopened._find("import-one"), original)
        self.assertEqual([c["id"] for c in reopened.index["cases"]], self.ids)
        self.assertIn("import-one", reopened.state()["workspace_case_ids"])
        self.assertEqual(self.snapshot_files(), files)

    def test_protected_batch_is_rejected_atomically(self):
        before = self.store.index_file.read_bytes()
        with self.assertRaises(CaseLibraryConflict) as caught:
            self.store.delete_cases({"case_ids": ["import-one", "base"]})
        self.assertEqual(caught.exception.code, "protected_case")
        self.assertEqual(self.store.index_file.read_bytes(), before)
        self.assertIsNotNone(self.store._find("import-one"))
        state = self.store.state()
        self.assertEqual(state["protected_case_ids"], ["base", "variant"])
        self.assertTrue(next(c for c in state["cases"] if c["id"] == "variant")["deletion_protected"])

    def test_invalid_unknown_and_repeated_delete_are_atomic(self):
        original = self.store.index_file.read_bytes()
        for ids in (None, [], ["import-one", "missing"], ["import-one", "import-one"], [3], [""]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.store.delete_cases({"case_ids": ids})
            self.assertEqual(self.store.index_file.read_bytes(), original)
        self.store.delete_cases({"case_ids": ["import-one"]})
        after = self.store.index_file.read_bytes()
        with self.assertRaises(ValueError):
            self.store.delete_cases({"case_ids": ["import-two", "import-one"]})
        self.assertEqual(self.store.index_file.read_bytes(), after)
        with self.assertRaises(ValueError):
            self.store.add_source(self.source, self.policy, title="reuse", case_id="import-one")

    def test_running_capture_rejects_delete_without_changing_job(self):
        self.store.jobs["capture"] = {"state": "running", "stage": "collecting"}
        before = copy.deepcopy(self.store.jobs)
        with self.assertRaises(CaseLibraryConflict) as caught:
            self.store.delete_cases({"case_ids": ["import-one"]})
        self.assertEqual(caught.exception.code, "active_capture")
        self.assertEqual(self.store.jobs, before)
        self.assertIsNotNone(self.store._find("import-one"))

    def test_running_or_queued_model_jobs_block_batch_but_unrelated_jobs_do_not(self):
        for attribute in ("_diagnoses", "_risk_jobs"):
            for status in ("queued", "running"):
                with self.subTest(attribute=attribute, status=status):
                    manager = SimpleNamespace(lock=threading.RLock(), jobs={
                        "job": {"case_id": "import-two", "status": status}})
                    setattr(self.store, attribute, manager)
                    with self.assertRaises(CaseLibraryConflict) as caught:
                        self.store.delete_cases({"case_ids": ["import-one", "import-two"]})
                    self.assertEqual(caught.exception.case_ids, ["import-two"])
                    self.assertIsNotNone(self.store._find("import-one"))
                    setattr(self.store, attribute, None)
        self.store._diagnoses = SimpleNamespace(lock=threading.RLock(), jobs={
            "unrelated": {"case_id": "base", "status": "running"}})
        self.store.delete_cases({"case_ids": ["import-one"]})

    def test_uninitialized_queues_are_checked_without_mutating_saved_jobs(self):
        for folder in ("diagnosis-jobs", "risk-jobs"):
            with self.subTest(folder=folder):
                path = self.store.folder / folder / "pending" / "job.json"
                write_json(path, {"case_id": "import-one", "status": "queued"})
                before = path.read_bytes()
                with self.assertRaises(CaseLibraryConflict):
                    self.store.delete_cases({"case_ids": ["import-one"]})
                self.assertEqual(path.read_bytes(), before)
                self.assertIsNone(self.store._diagnoses)
                self.assertIsNone(self.store._risk_jobs)
                write_json(path, {"case_id": "import-one", "status": "cancelled"})

    def test_restore_validates_whole_batch_and_missing_artifacts(self):
        case = self.store._find("import-one")
        report_path = self.store._resolve(case["report_path"])
        self.store.delete_cases({"case_ids": ["import-one", "import-two"]})
        before = self.store.index_file.read_bytes()
        with self.assertRaises(ValueError):
            self.store.restore_cases({"case_ids": ["import-one", "missing"]})
        report_path.unlink()
        with self.assertRaises(CaseLibraryConflict) as caught:
            self.store.restore_cases({"case_ids": ["import-two", "import-one"]})
        self.assertEqual(caught.exception.code, "missing_artifact")
        self.assertEqual(self.store.index_file.read_bytes(), before)
        self.assertIsNone(self.store._find("import-two"))

    def test_failed_commit_keeps_live_and_disk_library_unchanged(self):
        before = self.store.index_file.read_bytes()
        with patch("prodebugger.safety_workbench.write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.delete_cases({"case_ids": ["import-one"]})
        self.assertIsNotNone(self.store._find("import-one"))
        self.assertEqual(self.store.index_file.read_bytes(), before)

    def test_workspace_migration_hides_old_library_without_deleting_it(self):
        for case, date in zip(self.store.index["cases"],
                              ["2026-10-30T01:00:00+00:00", "2026-10-30T02:00:00+00:00",
                               "2026-10-28T01:00:00+00:00", "2026-10-30T08:00:00+00:00"]):
            case["created_at"] = date
        self.store.index.pop("workspace_case_ids")
        self.store._save()
        reopened = SafetyStore(self.store.folder)
        self.assertEqual(reopened.state()["workspace_case_ids"], ["base", "variant", "import-two"])
        self.assertEqual(len(reopened.state()["cases"]), 4)
        added = reopened.add_source(self.source, self.policy, title="new", case_id="new")
        self.assertTrue(added["workspace_visible"])
        self.assertEqual(SafetyStore(self.store.folder).state()["workspace_case_ids"],
                         ["base", "variant", "import-two", "new"])

    def test_enqueue_and_delete_share_atomic_library_lock(self):
        for method in ("start_diagnosis", "start_risk_review"):
            with self.subTest(method=method):
                entered, release, deleted_done = threading.Event(), threading.Event(), threading.Event()
                errors = []
                manager = SimpleNamespace(lock=threading.RLock(), jobs={}, configuration={})

                def enqueue(cid, *_):
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("test enqueue was not released")
                    manager.jobs["new"] = {"case_id": cid, "status": "queued"}
                    return manager.jobs["new"]

                def delete():
                    try:
                        self.store.delete_cases({"case_ids": ["import-one"]})
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        deleted_done.set()

                manager.enqueue = enqueue
                attribute = "_diagnoses" if method == "start_diagnosis" else "_risk_jobs"
                setattr(self.store, attribute, manager)
                self.store._find("import-one")["risk_policy"] = {"test": True}
                with patch.object(self.store, "analysis", return_value={}), patch.object(self.store, "risk_report", return_value={}):
                    thread = threading.Thread(target=getattr(self.store, method), args=({"case_ids": ["import-one"]},))
                    thread.start()
                    self.assertTrue(entered.wait(3))
                    deletion = threading.Thread(target=delete)
                    deletion.start()
                    self.assertFalse(deleted_done.wait(0.05))
                    release.set()
                    thread.join(3)
                    deletion.join(3)
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], CaseLibraryConflict)
                self.assertIsNotNone(self.store._find("import-one"))
                setattr(self.store, attribute, None)

    def test_http_delete_restore_and_protection_contract(self):
        server = SafetyHTTPServer(("127.0.0.1", 0), Handler)
        server.store = self.store
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(path, ids):
            request = Request(base + path, data=json.dumps({"case_ids": ids}).encode(),
                              headers={"Content-Type": "application/json", "Origin": base})
            with urlopen(request, timeout=10) as response:
                return json.load(response)

        try:
            with self.assertRaises(HTTPError) as caught:
                post("/api/safety/cases/delete", ["base", "import-one"])
            self.assertEqual(caught.exception.code, 409)
            self.assertEqual(json.load(caught.exception)["code"], "protected_case")
            self.assertEqual(post("/api/safety/cases/delete", ["import-one"])["deleted"], ["import-one"])
            with urlopen(base + "/api/safety/cases/deleted", timeout=10) as response:
                deleted = json.load(response)
            self.assertEqual(deleted["cases"][0]["case_id"], "import-one")
            self.assertNotIn("source_path", deleted["cases"][0])
            self.assertEqual(post("/api/safety/cases/restore", ["import-one"])["restored"], ["import-one"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__ == "__main__":
    unittest.main()
