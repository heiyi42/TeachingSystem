"""Transactional outbox and account-scoped published STS snapshots."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS assistant_profiles (owner TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, epoch INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '{}')"
    )
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
        with closing(self.connect()) as db, db:
            schema(db)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def view(self, owner):
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
        snapshot = json.loads(profile.pop("snapshot"))
        return {
            **profile,
            "enabled": bool(profile["enabled"]),
            "messages": messages,
            "snapshot": snapshot,
            "memory_status": (
                "paused"
                if not profile["enabled"]
                else "failed"
                if job and job["attempts"] >= 5
                else "updating"
                if job
                else "ready"
            ),
            "memory_error": job["error"] if job else None,
        }

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
                if old["status"] == "pending" and time.time() - old["created"] < 120:
                    raise ValueError("这条消息正在处理，请稍后刷新")
                db.execute(
                    "UPDATE assistant_messages SET status='pending',created=? WHERE id=?",
                    (time.time(), request_id),
                )
                return True
            pending = db.execute(
                "SELECT 1 FROM assistant_messages WHERE owner=? AND status='pending' AND created>?",
                (owner, time.time() - 120),
            ).fetchone()
            if pending:
                raise ValueError("请等待上一条消息完成")
            db.execute(
                "INSERT INTO assistant_messages VALUES (?,?,?,?,?,?,NULL)",
                (request_id, owner, "user", content, time.time(), "pending"),
            )
            enqueue(db, owner, request_id, content)
            return True

    def finish_message(self, owner, request_id, reply=None):
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
            if reply:
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

    def claim(self):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT j.*,p.epoch FROM assistant_jobs j JOIN assistant_profiles p USING(owner) WHERE p.enabled=1 AND j.lease<? AND j.next_at<=? AND j.attempts<5 ORDER BY j.next_at LIMIT 1",
                (time.time(), time.time()),
            ).fetchone()
            if not row:
                return None
            job = dict(row)
            job["token"] = uuid4().hex
            db.execute(
                "UPDATE assistant_jobs SET token=?,lease=?,attempts=attempts+1 WHERE owner=?",
                (job["token"], time.time() + 600, job["owner"]),
            )
            job["events"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM assistant_events WHERE owner=? AND active=1 ORDER BY seq",
                    (job["owner"],),
                )
            ]
            job["through"] = max((r["seq"] for r in job["events"]), default=0)
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
            db.execute(
                "UPDATE assistant_profiles SET snapshot=?,version=version+1 WHERE owner=?",
                (encode(snapshot), job["owner"]),
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
