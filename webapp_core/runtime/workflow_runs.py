from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import math
import os
import sqlite3
import time
import uuid
from threading import Condition, Event, Thread
from contextlib import closing, contextmanager
from pathlib import Path


class WorkflowConflict(ValueError):
    pass


class WorkflowRun:
    def __init__(self, store, row, lock, *, resume=False):
        self.store = store
        self.row = row
        self.lock = lock
        self.resume = resume
        self.started = False
        self.sequence = 0

    @property
    def id(self):
        return self.row["id"]

    @property
    def checkpoint_enabled(self):
        payload = self.row["payload"]
        return payload.get("mode") in {
            "auto",
            "deepsearch",
            "learning_dialogue",
            "learning_plan",
        } and not any(
            payload.get(key)
            for key in ("code_analysis", "problem_tutoring", "tutoring")
        )

    def emit(self, event, data):
        self.sequence += 1
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO workflow_events VALUES(?,?,?,?,?)",
                (
                    self.id,
                    self.row["execution_id"],
                    self.sequence,
                    event,
                    json.dumps(data, ensure_ascii=False),
                ),
            )
        with self.store.changed:
            self.store.changed.notify_all()

    def cancellation_requested(self):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT cancel_requested FROM workflow_runs WHERE id=? AND execution_id=?",
                (self.id, self.row["execution_id"]),
            ).fetchone()
        return bool(row and row[0])

    def save(self, **changes):
        self.store.save(self.id, **changes)
        self.row.update(changes)

    def close(self):
        if self.lock is not None:
            self.lock.close()
            self.lock = None


class WorkflowRuns:
    """Request metadata lives beside LangGraph checkpoints; grades stay in LearningStore."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_dir = self.path.with_suffix(".locks")
        self.lock_dir.mkdir(exist_ok=True)
        self.changed = Condition()
        self.completed_days = float(os.getenv("WEB_WORKFLOW_COMPLETED_DAYS", "7"))
        self.recoverable_days = float(os.getenv("WEB_WORKFLOW_RECOVERABLE_DAYS", "30"))
        if any(
            not math.isfinite(n) or n <= 0
            for n in (self.completed_days, self.recoverable_days)
        ):
            raise ValueError("工作流保留天数必须为正数")
        self._cleanup_stop = Event()
        self._cleanup_thread = None
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT NOT NULL,
                payload TEXT NOT NULL, context TEXT NOT NULL DEFAULT '{}',
                progress TEXT NOT NULL DEFAULT '{}', result TEXT,
                status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL
            )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS workflow_chat ON workflow_runs(chat_id, created)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(workflow_runs)")}
            for name, definition in (
                ("execution_id", "TEXT NOT NULL DEFAULT ''"),
                ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    db.execute(
                        f"ALTER TABLE workflow_runs ADD COLUMN {name} {definition}"
                    )
            db.execute(
                """CREATE TABLE IF NOT EXISTS workflow_events (
                run_id TEXT NOT NULL, execution_id TEXT NOT NULL, seq INTEGER NOT NULL,
                event TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(run_id, execution_id, seq)
            )"""
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _lock(self, chat_id):
        name = hashlib.sha256(chat_id.encode()).hexdigest()
        lock = (self.lock_dir / name).open("a+b")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise WorkflowConflict("当前任务仍在执行，请稍后刷新") from None
        return lock

    @staticmethod
    def _decode(row):
        data = dict(row)
        for key in ("payload", "context", "progress", "result"):
            data[key] = json.loads(data[key]) if data[key] else None
        return data

    def latest(self, owner, chat_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_runs WHERE owner=? AND chat_id=? ORDER BY created DESC LIMIT 1",
                (owner, chat_id),
            ).fetchone()
        return self._decode(row) if row else None

    def public(self, owner, chat_id):
        row = self.latest(owner, chat_id)
        if row is None or row["status"] in {"obsolete", "expired"}:
            return None
        status = row["status"]
        if status == "running":
            try:
                lock = self._lock(chat_id)
            except WorkflowConflict:
                if row["cancel_requested"]:
                    status = "stopping"
            else:
                lock.close()
                status = "interrupted"
        return {
            "id": row["id"],
            "execution_id": row["execution_id"],
            "status": status,
            "mode": row["payload"].get("mode"),
            "message": row["payload"].get("message"),
            "error": row["error"],
            "can_resume": status in {"failed", "interrupted", "cancelled"}
            and row["version"] == 3
            and WorkflowRun(self, row, None).checkpoint_enabled,
            "can_restart": status in {"failed", "interrupted", "cancelled"},
        }

    def claim(self, owner, chat_id, payload, resume_id=None):
        lock = self._lock(chat_id)
        try:
            latest = self.latest(owner, chat_id)
            if resume_id:
                if latest is None or latest["id"] != resume_id:
                    raise LookupError("可恢复任务不存在")
                if (
                    latest["status"] in {"obsolete", "expired"}
                    or latest["version"] != 3
                ):
                    raise WorkflowConflict("会话或工作流已更新，请重新提问")
                run = WorkflowRun(self, latest, lock, resume=True)
                if not run.checkpoint_enabled:
                    raise WorkflowConflict("此类回答请重新生成")
                run.save(
                    status="running",
                    error="",
                    execution_id=uuid.uuid4().hex,
                    cancel_requested=0,
                )
                return run
            now = time.time()
            row = {
                "id": uuid.uuid4().hex,
                "owner": owner,
                "chat_id": chat_id,
                "payload": payload,
                "context": {},
                "progress": {},
                "result": None,
                "status": "running",
                "error": "",
                "version": 3,
                "created": now,
                "updated": now,
                "execution_id": uuid.uuid4().hex,
                "cancel_requested": 0,
            }
            with self.connect() as db:
                db.execute(
                    "UPDATE workflow_runs SET status='obsolete' WHERE chat_id=?",
                    (chat_id,),
                )
                db.execute(
                    "INSERT INTO workflow_runs(id,owner,chat_id,payload,status,created,updated,execution_id,version) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        owner,
                        chat_id,
                        json.dumps(payload, ensure_ascii=False),
                        "running",
                        now,
                        now,
                        row["execution_id"],
                        row["version"],
                    ),
                )
            return WorkflowRun(self, row, lock)
        except BaseException:
            lock.close()
            raise

    def execution(self, owner, chat_id, run_id, execution_id):
        row = self.latest(owner, chat_id)
        if (
            row is None
            or row["id"] != run_id
            or row["execution_id"] != execution_id
            or row["status"] in {"obsolete", "expired"}
        ):
            raise LookupError("本次执行不存在或已更新")
        return row

    def cancel(self, owner, chat_id, run_id, execution_id):
        self.execution(owner, chat_id, run_id, execution_id)
        with self.connect() as db:
            db.execute(
                "UPDATE workflow_runs SET cancel_requested=1,updated=? WHERE id=? AND owner=? AND execution_id=? AND status='running'",
                (time.time(), run_id, owner, execution_id),
            )
        return self.public(owner, chat_id)

    def iter_events(
        self, owner, chat_id, run_id, execution_id, after=0, can_read=lambda: True
    ):
        heartbeat = time.monotonic()
        while can_read():
            try:
                self.execution(owner, chat_id, run_id, execution_id)
            except LookupError:
                break
            with self.connect() as db:
                events = db.execute(
                    "SELECT seq,event,data FROM workflow_events WHERE run_id=? AND execution_id=? AND seq>? ORDER BY seq LIMIT 128",
                    (run_id, execution_id, after),
                ).fetchall()
            for seq, event, data in events:
                after = seq
                yield f"id: {seq}\nevent: {event}\ndata: {data}\n\n"
                if event == "stream_end":
                    return
            if events:
                continue
            try:
                lock = self._lock(chat_id)
            except WorkflowConflict:
                pass
            else:
                lock.close()
                # The writer may have committed its last event between our read and lock check.
                with self.connect() as db:
                    pending = db.execute(
                        "SELECT 1 FROM workflow_events WHERE run_id=? AND execution_id=? AND seq>?",
                        (run_id, execution_id, after),
                    ).fetchone()
                if pending:
                    continue
                break
            if time.monotonic() - heartbeat >= 10:
                heartbeat = time.monotonic()
                yield ": keepalive\n\n"
            with self.changed:
                self.changed.wait(timeout=0.25)
        yield 'event: stream_end\ndata: {"reason":"unavailable"}\n\n'

    def _delete_checkpoints(self, ids, *, dry_run=False):
        from agenticRAG.workflow_checkpoint import checkpoint_database_path
        from langgraph.checkpoint.sqlite import SqliteSaver

        count = 0
        # Include legacy checkpoints stored in the request metadata database.
        for path in (self.path, checkpoint_database_path(self.path)):
            if not path.exists():
                continue
            with closing(sqlite3.connect(path, timeout=30)) as db:
                if not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'"
                ).fetchone():
                    continue
                saver = SqliteSaver(db)
                for run_id in ids:
                    prefix = run_id + ":"
                    threads = [
                        r[0]
                        for r in db.execute(
                            "SELECT DISTINCT thread_id FROM checkpoints WHERE substr(thread_id,1,?)=?",
                            (len(prefix), prefix),
                        )
                    ]
                    count += len(threads)
                    if not dry_run:
                        for thread in threads:
                            saver.delete_thread(thread)
        return count

    def delete_chat(self, chat_id, delete_session):
        with self._lock(chat_id):
            with self.connect() as db:
                ids = [
                    row[0]
                    for row in db.execute(
                        "SELECT id FROM workflow_runs WHERE chat_id=?", (chat_id,)
                    )
                ]
            self._delete_checkpoints(ids)
            with self.connect() as db:
                db.execute("DELETE FROM workflow_runs WHERE chat_id=?", (chat_id,))
                for run_id in ids:
                    db.execute("DELETE FROM workflow_events WHERE run_id=?", (run_id,))
                return delete_session(chat_id)

    def prune(self, *, dry_run=False, limit=100, now=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("每批清理数量须为 1 至 1000")
        now = time.time() if now is None else now
        completed_before = now - self.completed_days * 86400
        recoverable_before = now - self.recoverable_days * 86400
        condition = """status='expired'
            OR (status IN ('done','completed','obsolete') AND updated<=?)
            OR (status IN ('running','failed','cancelled') AND updated<=?)"""
        with self.connect() as db:
            candidates = db.execute(
                f"SELECT id,chat_id FROM workflow_runs WHERE {condition} ORDER BY updated LIMIT ?",
                (completed_before, recoverable_before, limit),
            ).fetchall()
        result = dict(
            dry_run=dry_run, runs=0, events=0, checkpoint_threads=0, skipped_active=0
        )
        for candidate in candidates:
            try:
                lock = self._lock(candidate["chat_id"])
            except WorkflowConflict:
                result["skipped_active"] += 1
                continue
            try:
                with self.connect() as db:
                    row = db.execute(
                        f"SELECT id FROM workflow_runs WHERE id=? AND ({condition})",
                        (candidate["id"], completed_before, recoverable_before),
                    ).fetchone()
                    if row is None:
                        continue
                    run_id = row["id"]
                    events = db.execute(
                        "SELECT count(*) FROM workflow_events WHERE run_id=?", (run_id,)
                    ).fetchone()[0]
                    if not dry_run:
                        # A crash between the two DB deletions must never make a partial run resumable.
                        db.execute(
                            "UPDATE workflow_runs SET status='expired' WHERE id=?",
                            (run_id,),
                        )
                result["checkpoint_threads"] += self._delete_checkpoints(
                    [run_id], dry_run=dry_run
                )
                if not dry_run:
                    with self.connect() as db:
                        db.execute(
                            "DELETE FROM workflow_events WHERE run_id=?", (run_id,)
                        )
                        db.execute("DELETE FROM workflow_runs WHERE id=?", (run_id,))
                result["runs"] += 1
                result["events"] += events
            finally:
                lock.close()
        return result

    def start_cleanup(self, interval=3600):
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            return
        self._cleanup_stop.clear()

        def clean():
            while not self._cleanup_stop.is_set():
                try:
                    self.prune()
                except Exception:
                    logging.getLogger(__name__).exception(
                        "工作流历史清理失败，下轮重试"
                    )
                self._cleanup_stop.wait(interval)

        self._cleanup_thread = Thread(
            target=clean, name="workflow-cleanup", daemon=True
        )
        self._cleanup_thread.start()

    def stop_cleanup(self):
        self._cleanup_stop.set()
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=1)

    def save(self, run_id, **changes):
        allowed = {
            "context",
            "progress",
            "result",
            "status",
            "error",
            "execution_id",
            "cancel_requested",
        }
        if not changes or not set(changes) <= allowed:
            raise ValueError("无效的任务字段")
        values = [
            (
                json.dumps(value, ensure_ascii=False)
                if key in {"context", "progress", "result"}
                else value
            )
            for key, value in changes.items()
        ]
        with self.connect() as db:
            db.execute(
                "UPDATE workflow_runs SET "
                + ",".join(f"{key}=?" for key in changes)
                + ",updated=? WHERE id=?",
                (*values, time.time(), run_id),
            )
