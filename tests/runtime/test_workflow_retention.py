import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from langgraph.graph import StateGraph, START, END
from agenticRAG.workflow_checkpoint import (
    checkpoint_run,
    checkpoint_database_path,
    invoke_workflow,
)
from webapp_core.runtime.workflow_runs import WorkflowRuns, WorkflowConflict

DAY = 86400


class WorkflowRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "runs.sqlite3"
        with patch.dict(
            "os.environ",
            {"WEB_WORKFLOW_COMPLETED_DAYS": "7", "WEB_WORKFLOW_RECOVERABLE_DAYS": "30"},
        ):
            self.runs = WorkflowRuns(self.path)
        self.now = 100 * DAY

    def run_record(self, name, status, age, *, active=False):
        run = self.runs.claim("alice", name, {"mode": "deepsearch", "message": "Q"})
        run.emit("delta", {"text": "answer"})
        run.save(status=status)
        with self.runs.connect() as db:
            db.execute(
                "UPDATE workflow_runs SET updated=? WHERE id=?",
                (self.now - age, run.id),
            )
        if not active:
            run.close()
        else:
            self.addCleanup(run.close)
        return run

    def checkpoints(self, run):
        async def generate():
            token = checkpoint_run.set((str(self.path), run.id))
            try:
                for name in ("child", "parent"):
                    graph = StateGraph(dict)
                    graph.add_node("work", lambda state: {"answer": "retained"})
                    graph.add_edge(START, "work")
                    graph.add_edge("work", END)
                    await invoke_workflow(graph, name, {})
            finally:
                checkpoint_run.reset(token)

        asyncio.run(generate())

    def test_expiry_boundaries_all_modes_and_unfinished_retention(self):
        deleted = []
        kept = []
        for status in ("completed", "done", "obsolete"):
            deleted.append(self.run_record(status, status, 7 * DAY))
            kept.append(self.run_record(status + "fresh", status, 7 * DAY - 1))
        for status in ("failed", "cancelled", "running"):
            deleted.append(self.run_record(status, status, 30 * DAY))
            kept.append(self.run_record(status + "fresh", status, 30 * DAY - 1))
        result = self.runs.prune(now=self.now)
        self.assertEqual(result["runs"], 6)
        self.assertEqual(result["events"], 6)
        for run in deleted:
            self.assertIsNone(self.runs.latest("alice", run.row["chat_id"]))
        for run in kept:
            self.assertIsNotNone(self.runs.latest("alice", run.row["chat_id"]))

    def test_preview_changes_nothing_and_apply_removes_nested_checkpoints(self):
        old = self.run_record("old", "completed", 10 * DAY)
        self.checkpoints(old)
        recent = self.run_record("recent", "failed", 10 * DAY)
        self.checkpoints(recent)
        with self.runs.connect() as db:
            before = list(db.iterdump())
        preview = self.runs.prune(now=self.now, dry_run=True)
        self.assertEqual(preview["checkpoint_threads"], 2)
        with self.runs.connect() as db:
            self.assertEqual(list(db.iterdump()), before)
        self.assertEqual(self.runs.prune(now=self.now)["runs"], 1)
        with sqlite3.connect(checkpoint_database_path(self.path)) as db:
            threads = [
                row[0]
                for row in db.execute("SELECT DISTINCT thread_id FROM checkpoints")
            ]
            self.assertTrue(threads)
            self.assertTrue(all(t.startswith(recent.id + ":") for t in threads))
            self.assertFalse(
                db.execute(
                    "SELECT 1 FROM writes WHERE thread_id LIKE ?", (old.id + ":%",)
                ).fetchone()
            )
        self.assertEqual(self.runs.prune(now=self.now)["runs"], 0)

    def test_active_lock_wins_even_when_timestamp_or_status_is_old(self):
        active = self.run_record("active", "running", 60 * DAY, active=True)
        finished_but_locked = self.run_record(
            "finishing", "completed", 60 * DAY, active=True
        )
        result = self.runs.prune(now=self.now)
        self.assertEqual(result["runs"], 0)
        self.assertEqual(result["skipped_active"], 2)
        active.close()
        finished_but_locked.close()
        self.assertEqual(self.runs.prune(now=self.now)["runs"], 2)

    def test_candidate_rechecked_after_lock_and_recent_resume_survives(self):
        run = self.run_record("racing", "failed", 60 * DAY)
        original = self.runs._lock

        def updated(key):
            lock = original(key)
            with self.runs.connect() as db:
                db.execute(
                    "UPDATE workflow_runs SET updated=? WHERE id=?", (self.now, run.id)
                )
            return lock

        with patch.object(self.runs, "_lock", side_effect=updated):
            self.assertEqual(self.runs.prune(now=self.now)["runs"], 0)
        resumed = self.runs.claim("alice", "racing", {}, run.id)
        resumed.close()

    def test_partial_cleanup_cannot_resume_and_next_pass_finishes(self):
        run = self.run_record("partial", "failed", 60 * DAY)
        self.checkpoints(run)
        with patch.object(
            self.runs, "_delete_checkpoints", side_effect=OSError("disk busy")
        ):
            with self.assertRaises(OSError):
                self.runs.prune(now=self.now)
        self.assertIsNone(self.runs.public("alice", "partial"))
        with self.assertRaises(WorkflowConflict):
            self.runs.claim("alice", "partial", {}, run.id)
        self.assertEqual(self.runs.prune(now=self.now)["runs"], 1)

    def test_legacy_checkpoint_store_and_batch_limit(self):
        run = self.run_record("legacy", "completed", 10 * DAY)
        self.checkpoints(run)
        # Move a complete legacy saver DB next to the request tables as in older releases.
        with sqlite3.connect(self.path) as db:
            db.execute(
                "ATTACH DATABASE ? AS cp", (str(checkpoint_database_path(self.path)),)
            )
            for table in ("checkpoints", "writes"):
                db.execute(f"CREATE TABLE {table} AS SELECT * FROM cp.{table}")
        self.run_record("next", "done", 10 * DAY)
        result = self.runs.prune(now=self.now, limit=1)
        self.assertEqual(result["runs"], 1)
        self.assertEqual(result["checkpoint_threads"], 4)
        with self.runs.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0
            )
        self.assertEqual(self.runs.prune(now=self.now)["runs"], 1)

    def test_cleanup_worker_starts_once_and_stops(self):
        called = Event()
        with patch.object(self.runs, "prune", side_effect=lambda: called.set()):
            self.runs.start_cleanup(interval=60)
            thread = self.runs._cleanup_thread
            self.runs.start_cleanup(interval=60)
            self.assertIs(thread, self.runs._cleanup_thread)
            self.assertTrue(called.wait(2))
            self.runs.stop_cleanup()
            self.assertFalse(thread.is_alive())

    def test_invalid_retention_and_limit_are_rejected(self):
        for value in ("0", "-1", "nan", "inf"):
            with (
                patch.dict("os.environ", {"WEB_WORKFLOW_COMPLETED_DAYS": value}),
                self.assertRaises(ValueError),
            ):
                WorkflowRuns(self.path)
        for limit in (0, 1001, True):
            with self.assertRaises(ValueError):
                self.runs.prune(limit=limit)
