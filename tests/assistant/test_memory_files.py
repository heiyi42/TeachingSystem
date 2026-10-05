import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from webapp_core.assistant.assistant_store import (
    AssistantStore,
    MemorySnapshotError,
    enqueue,
    schema,
)


class MemoryFileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "learning.sqlite3"
        self.store = AssistantStore(self.path)

    def add(self, event_id, owner="alice"):
        with closing(self.store.connect()) as db, db:
            enqueue(db, owner, event_id, "每天学习半小时")

    def publish(self, event_id, owner="alice"):
        self.add(event_id, owner)
        job = self.store.claim()
        self.assertEqual(job["owner"], owner)
        snapshot = {
            "scopes": {"study": {"title": "学习时间"}},
            "claims": {event_id: {"content": "每天半小时"}},
            "vectors": {event_id: [0.5, 0.25]},
            "state_results": [{"scope_id": "study"}],
        }
        self.assertTrue(self.store.publish(job, snapshot))
        return {**snapshot, "processed_through": job["through"]}

    def test_snapshot_roundtrip_restart_and_replacement(self):
        expected = self.publish("first")
        files = list(self.store.memory_dir.rglob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text()), expected)
        with closing(self.store.connect()) as db:
            reference = json.loads(
                db.execute(
                    "SELECT snapshot FROM assistant_profiles WHERE owner='alice'"
                ).fetchone()[0]
            )
        self.assertEqual(reference, {"snapshot_file": files[0].name})
        restarted = AssistantStore(self.path)
        self.assertEqual(restarted.view("alice")["snapshot"], expected)
        self.add("second")
        job = restarted.claim()
        self.assertEqual(job["snapshot"], expected)
        self.assertEqual([e["id"] for e in job["events"]], ["second"])
        self.assertTrue(restarted.publish(job, {"claims": {"new": {}}}))
        self.assertFalse(files[0].exists())
        self.assertEqual(len(list(restarted.memory_dir.rglob("*.json"))), 1)
        self.assertEqual(restarted.view("alice")["version"], 2)

    def test_legacy_migration_preserves_version_epoch_and_progress(self):
        legacy_path = self.path.with_name("legacy.sqlite3")
        snapshot = {"claims": {"old": {"content": "偏好例题"}}, "processed_through": 7}
        with closing(sqlite3.connect(legacy_path)) as db, db:
            schema(db)
            db.execute(
                "INSERT INTO assistant_profiles(owner,epoch,version,snapshot) VALUES (?,?,?,?)",
                ("alice", 3, 4, json.dumps(snapshot)),
            )
        migrated = AssistantStore(legacy_path)
        migrated.initialize_memory()
        profile = migrated.view("alice")
        self.assertEqual(profile["snapshot"], snapshot)
        self.assertEqual((profile["epoch"], profile["version"]), (3, 4))
        with closing(migrated.connect()) as db:
            reference = json.loads(
                db.execute("SELECT snapshot FROM assistant_profiles").fetchone()[0]
            )
        self.assertEqual(set(reference), {"snapshot_file"})
        files = list(migrated.memory_dir.rglob("*.json"))
        self.assertEqual(len(files), 1)
        restarted = AssistantStore(legacy_path)
        restarted.initialize_memory()
        self.assertEqual(restarted.view("alice")["snapshot"], snapshot)
        self.assertEqual(list(restarted.memory_dir.rglob("*.json")), files)

    def test_file_write_failure_keeps_published_snapshot_and_job(self):
        expected = self.publish("first")
        files = list(self.store.memory_dir.rglob("*.json"))
        self.add("second")
        job = self.store.claim()
        with patch(
            "webapp_core.assistant.assistant_store.os.fsync",
            side_effect=OSError("disk failure"),
        ):
            with self.assertRaises(OSError):
                self.store.publish(job, {"claims": {"new": {}}})
        self.assertEqual(self.store.view("alice")["snapshot"], expected)
        self.assertEqual(self.store.view("alice")["version"], 1)
        self.assertEqual(list(self.store.memory_dir.rglob("*.json")), files)
        self.assertTrue(self.store.publish(job, {"claims": {"retried": {}}}))

    def test_db_failure_leaves_old_snapshot_and_restart_cleans_orphan(self):
        expected = self.publish("first")
        original_file = next(self.store.memory_dir.rglob("*.json"))
        self.add("second")
        job = self.store.claim()
        with closing(self.store.connect()) as db, db:
            db.execute(
                "CREATE TRIGGER reject_snapshot BEFORE UPDATE OF snapshot ON assistant_profiles BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.publish(job, {"claims": {"uncommitted": {}}})
        self.assertEqual(self.store.view("alice")["snapshot"], expected)
        self.assertEqual(self.store.view("alice")["version"], 1)
        self.assertEqual(len(list(self.store.memory_dir.rglob("*.json"))), 2)
        with closing(self.store.connect()) as db, db:
            db.execute("DROP TRIGGER reject_snapshot")
        restarted = AssistantStore(self.path)
        restarted.initialize_memory()
        self.assertEqual(list(restarted.memory_dir.rglob("*.json")), [original_file])
        self.assertEqual(restarted.view("alice")["snapshot"], expected)
        self.assertTrue(restarted.publish(job, {"claims": {"retried": {}}}))

    def test_forgetting_removes_files_and_fences_old_worker(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                alice = "alice-" + str(partial)
                bob = "bob-" + str(partial)
                self.publish("private-" + str(partial), alice)
                bob_snapshot = self.publish("other-" + str(partial), bob)
                self.add("pending-" + str(partial), alice)
                stale_job = self.store.claim()
                with closing(self.store.connect()) as db:
                    raw = json.loads(
                        db.execute(
                            "SELECT snapshot FROM assistant_profiles WHERE owner=?",
                            (alice,),
                        ).fetchone()[0]
                    )
                alice_file = self.store._snapshot_path(alice, raw["snapshot_file"])
                if partial:
                    self.store.settings(alice, event_ids=["private-" + str(partial)])
                else:
                    self.store.settings(alice, forget=True)
                self.assertFalse(alice_file.parent.exists())
                self.assertFalse(self.store.publish(stale_job, {"secret": True}))
                self.assertEqual(self.store.view(alice)["snapshot"], {})
                self.assertEqual(self.store.view(bob)["snapshot"], bob_snapshot)
                self.add("private-" + str(partial), alice)
                remaining = self.store.claim()
                if partial:
                    self.assertEqual(
                        [e["id"] for e in remaining["events"]], ["pending-True"]
                    )
                    self.assertEqual(remaining["snapshot"], {})
                else:
                    self.assertIsNone(remaining)

    def test_custom_databases_and_unsafe_owner_names_are_isolated(self):
        owner = "../../outside"
        expected = self.publish("one", owner)
        path = next(self.store.memory_dir.rglob("*.json"))
        self.assertTrue(path.is_relative_to(self.store.memory_dir))
        self.assertEqual(len(path.relative_to(self.store.memory_dir).parts), 2)
        other = AssistantStore(self.path.with_suffix(".db"))
        self.assertNotEqual(other.memory_dir, self.store.memory_dir)
        self.assertEqual(other.view(owner)["snapshot"], {})
        other.settings(owner, forget=True)
        self.assertEqual(self.store.view(owner)["snapshot"], expected)

    def test_missing_or_invalid_reference_is_not_silently_empty(self):
        self.publish("one")
        next(self.store.memory_dir.rglob("*.json")).unlink()
        with self.assertRaises(MemorySnapshotError):
            self.store.view("alice")
        with closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE assistant_profiles SET snapshot=? WHERE owner='alice'",
                (json.dumps({"snapshot_file": "../../outside.json"}),),
            )
        with self.assertRaises(ValueError):
            self.store.view("alice")

    def test_bad_snapshot_blocks_only_its_owner_and_never_rebuilds(self):
        self.publish("one")
        path = next(self.store.memory_dir.rglob("*.json"))
        for broken in (
            "{invalid",
            "[]",
            '{"claims": []}',
            '{"processed_through": "1"}',
        ):
            with self.subTest(broken=broken):
                path.write_text(broken)
                self.add("pending-alice")
                self.add("pending-bob", "bob")
                job = self.store.claim()
                self.assertEqual(job["owner"], "bob")
                self.assertTrue(self.store.publish(job, {}))
                state = self.store.view("alice", allow_unavailable=True)
                self.assertEqual(state["memory_status"], "failed")
                self.assertEqual(state["snapshot"], {})
                self.assertIn("恢复", state["memory_error"])
                self.assertIsNone(self.store.claim())
                with self.assertRaises(MemorySnapshotError):
                    self.store.view("alice")
                self.assertEqual(
                    self.store.view("alice", allow_unavailable=True)["epoch"],
                    state["epoch"],
                )
                self.assertTrue(path.exists())
                # Requeue both accounts for the next independent corrupt payload.
                self.store.settings("alice", enabled=True)
                self.store.settings("bob", forget=True)
                with closing(self.store.connect()) as db, db:
                    db.execute("DELETE FROM assistant_forgotten WHERE owner='bob'")

    def test_restore_and_retry_preserve_progress_and_fence_old_job(self):
        expected = self.publish("one")
        path = next(self.store.memory_dir.rglob("*.json"))
        original = path.read_bytes()
        self.add("two")
        stale = self.store.claim()
        path.unlink()
        state = self.store.view("alice", allow_unavailable=True)
        self.assertEqual(state["memory_status"], "failed")
        self.assertFalse(self.store.publish(stale, {}))
        path.write_bytes(original)
        self.store.settings("alice", enabled=True)
        job = self.store.claim()
        self.assertEqual(job["snapshot"], expected)
        self.assertEqual([e["id"] for e in job["events"]], ["two"])
        self.assertTrue(self.store.publish(job, expected))
        self.assertEqual(self.store.view("alice")["memory_status"], "ready")

    def test_disabled_memory_does_not_block_learning_on_file_error(self):
        self.publish("one")
        next(self.store.memory_dir.rglob("*.json")).unlink()
        self.store.settings("alice", enabled=False)
        state = self.store.view("alice")
        self.assertFalse(state["enabled"])
        self.assertEqual(state["memory_status"], "paused")
        self.assertIsNone(self.store.claim())

    def test_request_construction_does_not_scan_or_prune_other_owners(self):
        expected = self.publish("one")
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT INTO assistant_profiles(owner,snapshot) VALUES ('broken','not-json')"
            )
        with patch.object(
            AssistantStore,
            "_prune_snapshots",
            side_effect=AssertionError("request must not prune"),
        ):
            request_store = AssistantStore(self.path)
            self.assertEqual(request_store.view("alice")["snapshot"], expected)
        self.store.initialize_memory()
        self.assertEqual(
            self.store.view("broken", allow_unavailable=True)["memory_status"], "failed"
        )
        self.assertEqual(self.store.view("alice")["snapshot"], expected)

    def test_cleanup_preserves_unrelated_files(self):
        self.publish("one")
        folder = next(self.store.memory_dir.rglob("*.json")).parent
        note = folder / "note.json"
        note.write_text('{"note": "manual"}')
        self.publish("two")
        self.assertTrue(note.exists())
        self.store.settings("alice", forget=True)
        self.assertEqual(list(folder.iterdir()), [note])


if __name__ == "__main__":
    unittest.main()
