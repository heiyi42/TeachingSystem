"""Transactional outbox and account-scoped published STS snapshots."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4


class MemorySnapshotError(ValueError):
    def __init__(self):
        super().__init__(
            "记忆快照无法读取，请恢复对应记忆文件后重试；也可以清空记忆后重新记录。"
        )


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS assistant_teacher_results (request_id TEXT PRIMARY KEY, owner TEXT NOT NULL, data TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS assistant_profiles (owner TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, epoch INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '{}')"
    )
    if "name" not in {row[1] for row in db.execute("PRAGMA table_info(assistant_profiles)")}:
        db.execute("ALTER TABLE assistant_profiles ADD COLUMN name TEXT NOT NULL DEFAULT '个人助理'")
    db.execute(
        "CREATE TABLE IF NOT EXISTS assistant_messages (id TEXT PRIMARY KEY, owner TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL, reply_to TEXT)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS assistant_message_owner ON assistant_messages(owner,created)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS assistant_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, owner TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS assistant_event_owner ON assistant_events(owner,seq)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS assistant_forgotten (owner TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY(owner,event_id))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS assistant_jobs (owner TEXT PRIMARY KEY, token TEXT, lease REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0, error TEXT)"
    )


def enqueue(db, owner, event_id, content):
    """Called inside the same transaction as the source business write."""
    if db.execute(
        "SELECT 1 FROM assistant_forgotten WHERE owner=? AND event_id=?",
        (owner, event_id),
    ).fetchone():
        return
    db.execute("INSERT OR IGNORE INTO assistant_profiles(owner) VALUES (?)", (owner,))
    if not db.execute(
        "SELECT enabled FROM assistant_profiles WHERE owner=?", (owner,)
    ).fetchone()[0]:
        return
    cursor = db.execute(
        "INSERT OR IGNORE INTO assistant_events(id,owner,content,created) VALUES (?,?,?,?)",
        (event_id, owner, content, time.time()),
    )
    if cursor.rowcount:
        db.execute("INSERT OR IGNORE INTO assistant_jobs(owner) VALUES (?)", (owner,))


class AssistantStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        root = Path(__file__).resolve().parents[2]
        self.memory_dir = (
            root / "memory"
            if self.path.resolve() == root / "data" / "learning.sqlite3"
            else self.path.resolve().parent / "memory" / self.path.name
        )
        self.memory_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self.connect()) as db, db:
            schema(db)

    def initialize_memory(self):
        """Migrate and prune once at process startup, not on request paths."""
        with closing(self.connect()) as db:
            owners = [
                row[0] for row in db.execute("SELECT owner FROM assistant_profiles")
            ]
        for owner in owners:
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    value = json.loads(
                        db.execute(
                            "SELECT snapshot FROM assistant_profiles WHERE owner=?",
                            (owner,),
                        ).fetchone()[0]
                    )
                    if not isinstance(value, dict):
                        raise MemorySnapshotError()
                    if value and "snapshot_file" not in value:
                        reference = self._write_snapshot(owner, value)
                        db.execute(
                            "UPDATE assistant_profiles SET snapshot=? WHERE owner=?",
                            (encode(reference), owner),
                        )
                except (OSError, ValueError):
                    self._block_snapshot(db, owner)
                    continue
            # Only prune after the new reference has committed. A failed commit
            # leaves an orphan file, never a reference to a partial snapshot.
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    self._prune_snapshots(db, owner)
                except (OSError, ValueError, KeyError, TypeError):
                    self._block_snapshot(db, owner)

    def _block_snapshot(self, db, owner):
        error = str(MemorySnapshotError())
        previous = db.execute(
            "SELECT error FROM assistant_jobs WHERE owner=?", (owner,)
        ).fetchone()
        if not previous or previous[0] != error:
            # Invalidate in-flight personalized replies and cached plans once.
            db.execute(
                "UPDATE assistant_profiles SET epoch=epoch+1 WHERE owner=?", (owner,)
            )
        db.execute(
            "INSERT INTO assistant_jobs(owner,attempts,error) VALUES (?,5,?) "
            "ON CONFLICT(owner) DO UPDATE SET attempts=5,error=excluded.error,token=NULL,lease=0",
            (owner, error),
        )

    def _snapshot_path(self, owner, filename):
        if not isinstance(filename, str) or not re.fullmatch(
            r"[a-f0-9]{32}\.json", filename
        ):
            raise ValueError("Invalid memory snapshot filename")
        return self.memory_dir / hashlib.sha256(owner.encode()).hexdigest() / filename

    def _write_snapshot(self, owner, snapshot):
        filename = uuid4().hex + ".json"
        path = self._snapshot_path(owner, filename)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(snapshot, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                for folder in (path.parent, self.memory_dir):
                    directory = os.open(folder, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"snapshot_file": filename}

    def _read_snapshot(self, owner, raw):
        try:
            reference = json.loads(raw)
            if reference == {}:
                return {}
            path = self._snapshot_path(owner, reference["snapshot_file"])
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(snapshot, dict):
                raise ValueError("Snapshot must be an object")
            for key in ("events", "scopes", "claims", "vectors", "scope_vectors"):
                if key in snapshot and not isinstance(snapshot[key], dict):
                    raise ValueError("Invalid snapshot collection")
            cursor = snapshot.get("processed_through", 0)
            if type(cursor) is not int or cursor < 0:
                raise ValueError("Invalid snapshot progress")
            return snapshot
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise MemorySnapshotError() from error

    def _prune_snapshots(self, db, owner):
        reference = json.loads(
            db.execute(
                "SELECT snapshot FROM assistant_profiles WHERE owner=?", (owner,)
            ).fetchone()[0]
        )
        if reference != {}:
            self._snapshot_path(owner, reference["snapshot_file"])
        folder = self.memory_dir / hashlib.sha256(owner.encode()).hexdigest()
        if folder.exists():
            for path in folder.glob("*.json"):
                if re.fullmatch(
                    r"[a-f0-9]{32}\.json", path.name
                ) and path.name != reference.get("snapshot_file"):
                    path.unlink()
            if not any(folder.iterdir()):
                folder.rmdir()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def view(self, owner, *, allow_unavailable=False):
        unavailable = False
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO assistant_profiles(owner) VALUES (?)", (owner,)
            )
            profile = dict(
                db.execute(
                    "SELECT * FROM assistant_profiles WHERE owner=?", (owner,)
                ).fetchone()
            )
            job = db.execute(
                "SELECT * FROM assistant_jobs WHERE owner=?", (owner,)
            ).fetchone()
            messages = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM (SELECT *,rowid AS ordinal FROM assistant_messages WHERE owner=? ORDER BY created DESC,rowid DESC LIMIT 60) ORDER BY created,ordinal",
                    (owner,),
                )
            ]
            try:
                snapshot = self._read_snapshot(owner, profile.pop("snapshot"))
            except MemorySnapshotError:
                self._block_snapshot(db, owner)
                unavailable = True
                snapshot = {}
                profile["epoch"] = db.execute(
                    "SELECT epoch FROM assistant_profiles WHERE owner=?", (owner,)
                ).fetchone()[0]
                job = db.execute(
                    "SELECT * FROM assistant_jobs WHERE owner=?", (owner,)
                ).fetchone()
        if unavailable and profile["enabled"] and not allow_unavailable:
            raise MemorySnapshotError()
        return {
            **profile,
            "enabled": bool(profile["enabled"]),
            "messages": messages,
            "snapshot": snapshot,
            "memory_status": (
                "paused"
                if not profile["enabled"]
                else (
                    "failed"
                    if job and job["attempts"] >= 5
                    else "updating" if job else "ready"
                )
            ),
            "memory_error": job["error"] if job else None,
        }

    def rename(self, owner, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24 or not name.strip().isprintable():
            raise ValueError("助理名字须为1至24个字符，不能包含换行或控制字符")
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO assistant_profiles(owner) VALUES (?)", (owner,))
            db.execute("UPDATE assistant_profiles SET name=? WHERE owner=?", (name.strip(), owner))

    def begin_message(self, owner, request_id, content):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM assistant_messages WHERE id=?", (request_id,)
            ).fetchone()
            if old:
                if old["owner"] != owner or old["content"] != content:
                    raise ValueError("请求编号冲突")
                if old["status"] == "complete":
                    return False
                if old["status"] == "pending" and time.time() - old["created"] < 180:
                    raise ValueError("这条消息正在处理，请稍后刷新")
                db.execute(
                    "UPDATE assistant_messages SET status='pending',created=? WHERE id=?",
                    (time.time(), request_id),
                )
                return True
            pending = db.execute(
                "SELECT 1 FROM assistant_messages WHERE owner=? AND status='pending' AND created>?",
                (owner, time.time() - 180),
            ).fetchone()
            if pending:
                raise ValueError("请等待上一条消息完成")
            db.execute(
                "INSERT INTO assistant_messages VALUES (?,?,?,?,?,?,NULL)",
                (request_id, owner, "user", content, time.time(), "pending"),
            )
            enqueue(db, owner, request_id, content)
            return True

    def teacher_results(self, owner):
        with closing(self.connect()) as db:
            return [
                dict(request_id=row[0], **json.loads(row[1]))
                for row in db.execute(
                    "SELECT request_id,data FROM assistant_teacher_results WHERE owner=? ORDER BY rowid",
                    (owner,),
                )
            ]

    def start_teacher_task(self, owner, request_id, task):
        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT 1 FROM assistant_messages WHERE owner=? AND id=? AND status='pending'",
                (owner, request_id),
            ).fetchone()
            if row:
                db.execute(
                    "INSERT INTO assistant_teacher_results VALUES (?,?,?) "
                    "ON CONFLICT(request_id) DO UPDATE SET data=excluded.data",
                    (request_id, owner, encode(task)),
                )

    def finish_message(self, owner, request_id, reply=None, teacher_result=None):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT 1 FROM assistant_messages WHERE owner=? AND id=? AND status='pending'",
                (owner, request_id),
            ).fetchone()
            if not row:
                return
            db.execute(
                "UPDATE assistant_messages SET status=? WHERE owner=? AND id=?",
                ("complete" if reply else "failed", owner, request_id),
            )
            if not teacher_result:
                task = db.execute(
                    "SELECT data FROM assistant_teacher_results WHERE owner=? AND request_id=?",
                    (owner, request_id),
                ).fetchone()
                if task:
                    failed = json.loads(task[0])
                    failed.update(
                        status="failed", error=reply or "生成未完成，请重试。"
                    )
                    db.execute(
                        "UPDATE assistant_teacher_results SET data=? WHERE owner=? AND request_id=?",
                        (encode(failed), owner, request_id),
                    )
            if reply:
                if teacher_result:
                    db.execute(
                        "INSERT INTO assistant_teacher_results VALUES (?,?,?) ON CONFLICT(request_id) DO UPDATE SET data=excluded.data",
                        (request_id, owner, encode(teacher_result)),
                    )
                db.execute(
                    "INSERT INTO assistant_messages VALUES (?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        owner,
                        "assistant",
                        reply,
                        time.time(),
                        "complete",
                        request_id,
                    ),
                )

    def settings(self, owner, *, enabled=None, forget=False, event_ids=None):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO assistant_profiles(owner) VALUES (?)", (owner,)
            )
            db.execute(
                "UPDATE assistant_profiles SET epoch=epoch+1 WHERE owner=?", (owner,)
            )
            if enabled is not None:
                db.execute(
                    "UPDATE assistant_profiles SET enabled=? WHERE owner=?",
                    (int(enabled), owner),
                )
            if forget or event_ids:
                # Source removal prevents regeneration. Erase conversation context too,
                # including assistant replies that could repeat removed information.
                if forget:
                    db.execute(
                        "INSERT OR IGNORE INTO assistant_forgotten SELECT owner,id FROM assistant_events WHERE owner=?",
                        (owner,),
                    )
                    db.execute("DELETE FROM assistant_events WHERE owner=?", (owner,))
                else:
                    for event_id in event_ids:
                        db.execute(
                            "INSERT OR IGNORE INTO assistant_forgotten VALUES (?,?)",
                            (owner, event_id),
                        )
                        db.execute(
                            "DELETE FROM assistant_events WHERE owner=? AND id=?",
                            (owner, event_id),
                        )
                db.execute("DELETE FROM assistant_messages WHERE owner=?", (owner,))
                db.execute(
                    "DELETE FROM assistant_teacher_results WHERE owner=?", (owner,)
                )
                db.execute(
                    "UPDATE assistant_profiles SET snapshot='{}' WHERE owner=?",
                    (owner,),
                )
            db.execute("DELETE FROM assistant_jobs WHERE owner=?", (owner,))
            active = db.execute(
                "SELECT enabled FROM assistant_profiles WHERE owner=?", (owner,)
            ).fetchone()[0]
            if (
                active
                and db.execute(
                    "SELECT 1 FROM assistant_events WHERE owner=?", (owner,)
                ).fetchone()
            ):
                db.execute("INSERT INTO assistant_jobs(owner) VALUES (?)", (owner,))
        if forget or event_ids:
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                self._prune_snapshots(db, owner)

    def claim(self):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            while True:
                row = db.execute(
                    "SELECT j.*,p.epoch,p.snapshot FROM assistant_jobs j JOIN assistant_profiles p USING(owner) WHERE p.enabled=1 AND j.lease<? AND j.next_at<=? AND j.attempts<5 ORDER BY j.next_at LIMIT 1",
                    (time.time(), time.time()),
                ).fetchone()
                if not row:
                    return None
                job = dict(row)
                try:
                    job["snapshot"] = self._read_snapshot(job["owner"], job["snapshot"])
                    break
                except MemorySnapshotError:
                    self._block_snapshot(db, job["owner"])
            through = job["snapshot"].get("processed_through", 0)
            job["token"] = uuid4().hex
            db.execute(
                "UPDATE assistant_jobs SET token=?,lease=?,attempts=attempts+1 WHERE owner=?",
                (job["token"], time.time() + 600, job["owner"]),
            )
            job["events"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM assistant_events WHERE owner=? AND active=1 AND seq>? ORDER BY seq",
                    (job["owner"], through),
                )
            ]
            for event in job["events"]:
                # These namespaces are reserved for transactional business writes;
                # message request IDs cannot contain a colon.
                event["role"] = (
                    "system"
                    if event["id"].startswith(("practice:", "exam:"))
                    else "user"
                )
                event["context"] = []
                if event["role"] == "user":
                    size = 0
                    for message in db.execute(
                        "SELECT m.id,m.role,m.content,"
                        "CASE WHEN m.role='user' THEN e.created ELSE m.created END AS created "
                        "FROM assistant_messages m JOIN assistant_events e "
                        "ON e.id=CASE WHEN m.role='user' THEN m.id ELSE m.reply_to END "
                        "AND e.owner=m.owner "
                        "WHERE m.owner=? AND e.active=1 AND e.seq<? AND m.rowid<"
                        "(SELECT rowid FROM assistant_messages WHERE owner=? AND id=?) "
                        "ORDER BY m.rowid DESC LIMIT 8",
                        (job["owner"], event["seq"], job["owner"], event["id"]),
                    ):
                        size += len(message["content"])
                        if size > 12000:
                            break
                        event["context"].append(dict(message))
                    event["context"].reverse()
            job["through"] = max((r["seq"] for r in job["events"]), default=through)
            return job

    def publish(self, job, snapshot):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            valid = db.execute(
                "SELECT 1 FROM assistant_jobs j JOIN assistant_profiles p USING(owner) WHERE j.owner=? AND j.token=? AND p.epoch=? AND p.enabled=1",
                (job["owner"], job["token"], job["epoch"]),
            ).fetchone()
            if not valid:
                return False
            reference = self._write_snapshot(
                job["owner"], {**snapshot, "processed_through": job["through"]}
            )
            db.execute(
                "UPDATE assistant_profiles SET snapshot=?,version=version+1 WHERE owner=?",
                (encode(reference), job["owner"]),
            )
            more = db.execute(
                "SELECT 1 FROM assistant_events WHERE owner=? AND seq>?",
                (job["owner"], job["through"]),
            ).fetchone()
            if more:
                db.execute(
                    "UPDATE assistant_jobs SET token=NULL,lease=0,attempts=0,next_at=0,error=NULL WHERE owner=?",
                    (job["owner"],),
                )
            else:
                db.execute("DELETE FROM assistant_jobs WHERE owner=?", (job["owner"],))
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._prune_snapshots(db, job["owner"])
        return True

    def fail(self, job):
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE assistant_jobs SET lease=0,next_at=?,error=? WHERE owner=? AND token=?",
                (
                    time.time() + min(300, 10 * 2 ** job["attempts"]),
                    "记忆更新失败；后台将重试，连续失败五次后可手动重试。",
                    job["owner"],
                    job["token"],
                ),
            )
