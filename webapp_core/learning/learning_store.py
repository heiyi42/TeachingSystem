from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable


class LearningStore:
    def __init__(
        self,
        path: str | Path,
        *,
        owner_id: str | None = None,
        class_id: str | None = None,
        include_legacy: bool = False,
    ) -> None:
        self.path = Path(path)
        self.owner_id = owner_id
        self.class_id = class_id
        self.include_legacy = include_legacy

    def _owned(self, state):
        return (
            self.owner_id is None
            or state.get("owner_id") == self.owner_id
            or (self.include_legacy and not state.get("owner_id"))
        ) and (self.class_id is None or state.get("class_id") == self.class_id)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS learning_events ("
            "id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL, kind TEXT NOT NULL, "
            "created_at REAL NOT NULL, data TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS school_tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lesson_plans (id TEXT PRIMARY KEY, class_id TEXT NOT NULL, data TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lesson_generations (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, class_id TEXT NOT NULL, status TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS task_submissions (task_id TEXT NOT NULL, user_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id,user_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS task_files (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, user_id TEXT NOT NULL, name TEXT NOT NULL, data BLOB NOT NULL, sha256 TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS chapter_reading (owner_id TEXT NOT NULL, chapter_id TEXT NOT NULL, read_at REAL NOT NULL, PRIMARY KEY(owner_id,chapter_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS learning_guidance (owner_id TEXT NOT NULL, point_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(owner_id,point_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS learning_recommendations (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, data TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS study_profiles (owner_id TEXT NOT NULL, subject_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(owner_id,subject_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS ai_study_plans (owner_id TEXT NOT NULL, subject_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(owner_id,subject_id))"
        )
        from webapp_core.assistant.assistant_store import schema

        schema(connection)
        connection.commit()
        return connection

    def ai_study_plan(self, subject, data=None):
        with closing(self._connect()) as connection, connection:
            if data is not None:
                connection.execute(
                    "INSERT INTO ai_study_plans VALUES (?,?,?) ON CONFLICT(owner_id,subject_id) DO UPDATE SET data=excluded.data",
                    (
                        self.owner_id or "demo",
                        subject,
                        json.dumps(data, ensure_ascii=False),
                    ),
                )
            row = connection.execute(
                "SELECT data FROM ai_study_plans WHERE owner_id=? AND subject_id=?",
                (self.owner_id or "demo", subject),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def study_profile(self, subject):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT data FROM study_profiles WHERE owner_id=? AND subject_id=?",
                (self.owner_id or "demo", subject),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def save_study_profile(self, subject, profile):
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO study_profiles VALUES (?,?,?) ON CONFLICT(owner_id,subject_id) DO UPDATE SET data=excluded.data",
                (
                    self.owner_id or "demo",
                    subject,
                    json.dumps(profile, ensure_ascii=False),
                ),
            )

    def reading(self) -> dict[str, float]:
        with closing(self._connect()) as connection:
            return dict(
                connection.execute(
                    "SELECT chapter_id,read_at FROM chapter_reading WHERE owner_id=?",
                    (self.owner_id or "demo",),
                )
            )

    def guidance(self):
        with closing(self._connect()) as connection:
            return {
                key: json.loads(data)
                for key, data in connection.execute(
                    "SELECT point_id,data FROM learning_guidance WHERE owner_id=?",
                    (self.owner_id or "demo",),
                )
            }

    def recommendations(self):
        with closing(self._connect()) as connection:
            return [
                json.loads(raw)
                for (raw,) in connection.execute(
                    "SELECT data FROM learning_recommendations WHERE owner_id=?",
                    (self.owner_id or "demo",),
                )
            ]

    def accept_recommendation(self, record):
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO learning_recommendations VALUES (?,?,?)",
                (
                    record["id"],
                    self.owner_id or "demo",
                    json.dumps(record, ensure_ascii=False),
                ),
            )
            return json.loads(
                connection.execute(
                    "SELECT data FROM learning_recommendations WHERE id=? AND owner_id=?",
                    (record["id"], self.owner_id or "demo"),
                ).fetchone()[0]
            )

    def _complete_recommendations(self, connection, kind, target, completed_at):
        for record_id, raw in connection.execute(
            "SELECT id,data FROM learning_recommendations WHERE owner_id=?",
            (self.owner_id or "demo",),
        ).fetchall():
            record = json.loads(raw)
            action = record["action"]
            field = "chapter_id" if kind == "material" else "point_id"
            if (
                action["kind"] == kind
                and action.get(field) == target
                and not record.get("completed_at")
            ):
                record["completed_at"] = completed_at
                connection.execute(
                    "UPDATE learning_recommendations SET data=? WHERE id=?",
                    (json.dumps(record, ensure_ascii=False), record_id),
                )

    def save_guidance(self, point_id, guidance, *, belongs_to_point, guard=None):
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            attempts = [
                state
                for (raw,) in connection.execute("SELECT data FROM attempts")
                if self._owned(state := json.loads(raw)) and belongs_to_point(state)
            ]
            # 交付讲解前再次核验测验权限，与辅助留证在同一事务中完成。
            for state in attempts:
                if guard:
                    guard(state, connection)
            for state in attempts:
                if state["status"] == "passed":
                    continue
                state["tutoring_viewed"] = True
                state["updated_at"] = time.time()
                connection.execute(
                    "UPDATE attempts SET data=? WHERE id=?",
                    (json.dumps(state, ensure_ascii=False), state["id"]),
                )
                self._event(
                    connection,
                    state["id"],
                    "personal_guidance_viewed",
                    {"point_id": point_id},
                )
            connection.execute(
                "INSERT INTO learning_guidance VALUES (?,?,?) ON CONFLICT(owner_id,point_id) DO UPDATE SET data=excluded.data",
                (
                    self.owner_id or "demo",
                    point_id,
                    json.dumps(guidance, ensure_ascii=False),
                ),
            )

            self._complete_recommendations(
                connection, "explain", point_id, guidance["created_at"]
            )

    def mark_reading(self, chapter_id: str, read: bool) -> None:
        with closing(self._connect()) as connection, connection:
            if read:
                now = time.time()
                connection.execute(
                    "INSERT INTO chapter_reading VALUES (?,?,?) ON CONFLICT(owner_id,chapter_id) DO UPDATE SET read_at=excluded.read_at",
                    (self.owner_id or "demo", chapter_id, now),
                )
                self._complete_recommendations(connection, "material", chapter_id, now)
            else:
                connection.execute(
                    "DELETE FROM chapter_reading WHERE owner_id=? AND chapter_id=?",
                    (self.owner_id or "demo", chapter_id),
                )

    def list_attempts(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [
                state
                for row in connection.execute("SELECT data FROM attempts")
                if self._owned(state := json.loads(row[0]))
            ]

    def get(self, attempt_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT data FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise LookupError("练习不存在")
        state = json.loads(row[0])
        if not self._owned(state):
            raise LookupError("练习不存在")
        return state

    def cancel(self, attempt_id: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT data FROM attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            state = json.loads(row[0]) if row else None
            if (
                state is None
                or not self._owned(state)
                or (
                    self.owner_id is not None and state.get("owner_id") != self.owner_id
                )
            ):
                raise LookupError("练习不存在")
            if state.get("assignment_id"):
                raise ValueError("班级任务的作答不能取消")
            if state["status"] != "in_progress" or state.get("submissions"):
                raise ValueError("只能取消尚未提交的作答")
            connection.execute(
                "DELETE FROM learning_events WHERE attempt_id=?", (attempt_id,)
            )
            connection.execute("DELETE FROM attempts WHERE id=?", (attempt_id,))

    def create(
        self,
        factory: Callable[[list[dict[str, Any]]], dict[str, Any]],
        *,
        guard=None,
        recommendation_id=None,
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = [
                state
                for row in connection.execute("SELECT data FROM attempts")
                if self._owned(state := json.loads(row[0]))
            ]
            recommendation = None
            if recommendation_id:
                row = connection.execute(
                    "SELECT data FROM learning_recommendations WHERE id=? AND owner_id=?",
                    (recommendation_id, self.owner_id or "demo"),
                ).fetchone()
                if row is None:
                    raise LookupError("推荐记录不存在")
                recommendation = json.loads(row[0])
                if recommendation.get("attempt_id"):
                    previous = next(
                        (
                            a
                            for a in existing
                            if a["id"] == recommendation["attempt_id"]
                        ),
                        None,
                    )
                    if previous is None:
                        raise LookupError("推荐练习不存在")
                    return previous
            state = factory(existing)
            if recommendation:
                action = recommendation["action"]
                matches = (
                    action["kind"] == "practice"
                    and state["exercise_id"] == action.get("exercise_id")
                ) or (
                    action["kind"] == "review"
                    and state.get("review_id") == action.get("attempt_id")
                )
                if not matches:
                    raise ValueError("练习与推荐任务不一致")
                recommendation["attempt_id"] = state["id"]
                connection.execute(
                    "UPDATE learning_recommendations SET data=? WHERE id=?",
                    (json.dumps(recommendation, ensure_ascii=False), recommendation_id),
                )
            if self.owner_id is not None:
                state["owner_id"] = self.owner_id
            if guard:
                guard(state, connection)
            if any(item["id"] == state["id"] for item in existing):
                return state
            connection.execute(
                "INSERT INTO attempts VALUES (?, ?)",
                (state["id"], json.dumps(state, ensure_ascii=False)),
            )
            self._event(
                connection,
                state["id"],
                "started",
                {"exercise_id": state["exercise_id"], "parent_id": state["parent_id"]},
            )
            return state

    def update(
        self,
        attempt_id: str,
        action: Callable[
            [dict[str, Any], list[dict[str, Any]]], tuple[str, dict[str, Any]]
        ],
        *,
        guard=None,
        after=None,
    ) -> dict[str, Any]:
        # 核验结果、作答快照和辅助事件在同一事务内保存，避免并发请求漏记提示。
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = [
                state
                for row in connection.execute("SELECT data FROM attempts")
                if self._owned(state := json.loads(row[0]))
            ]
            state = next((item for item in existing if item["id"] == attempt_id), None)
            if state is None:
                raise LookupError("练习不存在")
            if guard:
                guard(state, connection)
            kind, event = action(state, existing)
            state["updated_at"] = time.time()
            if after:
                after(state, connection)
            connection.execute(
                "UPDATE attempts SET data = ? WHERE id = ?",
                (json.dumps(state, ensure_ascii=False), attempt_id),
            )
            self._event(connection, attempt_id, kind, event)
            if (
                kind == "submitted"
                and state.get("owner_id")
                and not state.get("assignment_id")
            ):
                from webapp_core.assistant.assistant_store import enqueue

                last = state["submissions"][-1]
                first = state["submissions"][0]
                enqueue(
                    connection,
                    state["owner_id"],
                    f"practice:{attempt_id}:{len(state['submissions'])}",
                    json.dumps(
                        {
                            "来源": "系统核验的课外练习",
                            "题目": state["exercise_id"],
                            "结果": state["status"],
                            "首错": state.get("first_error"),
                            "首次独立通过": bool(
                                first["evaluation"]["passed"] and first["unassisted"]
                            ),
                            "本次提交通过": bool(last["evaluation"]["passed"]),
                        },
                        ensure_ascii=False,
                    ),
                )
            return state

    def freeze_legacy(self, exercises):
        with closing(self._connect()) as connection, connection:
            rows = connection.execute("SELECT id,data FROM attempts").fetchall()
            if not any("exercise_snapshot" not in json.loads(raw) for _, raw in rows):
                return
            backup = self.path.with_name(f"{self.path.stem}_pre_identity.sqlite3")
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as destination:
                    connection.backup(destination)
            connection.execute("BEGIN IMMEDIATE")
            for attempt_id, raw in connection.execute(
                "SELECT id,data FROM attempts"
            ).fetchall():
                state = json.loads(raw)
                if (
                    "exercise_snapshot" not in state
                    and state["exercise_id"] in exercises
                ):
                    state["exercise_snapshot"] = exercises[state["exercise_id"]]
                    state["content_version"] = "legacy"
                    state["grading_version"] = "rules-v1"
                    connection.execute(
                        "UPDATE attempts SET data=? WHERE id=?",
                        (json.dumps(state, ensure_ascii=False), attempt_id),
                    )

    @staticmethod
    def _event(
        connection: sqlite3.Connection, attempt_id: str, kind: str, data: dict[str, Any]
    ) -> None:
        connection.execute(
            "INSERT INTO learning_events (attempt_id, kind, created_at, data) VALUES (?, ?, ?, ?)",
            (attempt_id, kind, time.time(), json.dumps(data, ensure_ascii=False)),
        )
