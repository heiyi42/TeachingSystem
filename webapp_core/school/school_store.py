from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from werkzeug.security import check_password_hash, generate_password_hash

from webapp_core.learning.learning_courses import COURSES
from webapp_core.learning.learning_exercises import EXERCISES
from webapp_core.learning.learning_authoring import validate_training_edit
from webapp_core.learning.learning_service import LearningConflict


class SchoolStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                role TEXT NOT NULL, password_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS login_sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS login_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS classes (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, course_id TEXT NOT NULL,
                teacher_id TEXT NOT NULL, join_code TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS members (class_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY(class_id, user_id));
            CREATE TABLE IF NOT EXISTS chat_owners (chat_id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS content_versions (
                item_key TEXT NOT NULL, version INTEGER NOT NULL, category TEXT NOT NULL,
                status TEXT NOT NULL, data TEXT NOT NULL, source TEXT NOT NULL,
                editor_id TEXT, reviewer_id TEXT, review_note TEXT NOT NULL,
                published_at REAL, PRIMARY KEY(item_key, version));
            CREATE TABLE IF NOT EXISTS school_audit (
                id INTEGER PRIMARY KEY, actor_id TEXT, action TEXT NOT NULL,
                target TEXT NOT NULL, created_at REAL NOT NULL, detail TEXT NOT NULL);
        """
        )
        return connection

    @staticmethod
    def public_user(row):
        return {key: row[key] for key in ("id", "username", "name", "role")}

    @staticmethod
    def audit(connection, actor, action, target, detail=""):
        connection.execute(
            "INSERT INTO school_audit VALUES (NULL,?,?,?,?,?)",
            (actor, action, target, time.time(), detail),
        )

    def setup_needed(self):
        with closing(self.connect()) as connection:
            return connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None

    def create_user(self, data: dict, *, role="student", initial=False, actor=None):
        username, name, password = (
            data.get("username"),
            data.get("name"),
            data.get("password"),
        )
        if not isinstance(username, str) or not re.fullmatch(
            r"[a-zA-Z0-9_]{3,32}", username
        ):
            raise ValueError("用户名须为 3 至 32 位字母、数字或下划线")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise ValueError("姓名须为 1 至 40 个字符")
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise ValueError("密码须为 8 至 128 个字符")
        user = dict(
            id=uuid4().hex, username=username.lower(), name=name.strip(), role=role
        )
        password_hash = generate_password_hash(password)
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if initial and connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise LearningConflict("初始化已经完成")
            try:
                connection.execute(
                    "INSERT INTO users VALUES (?,?,?,?,?)",
                    (*user.values(), password_hash),
                )
            except sqlite3.IntegrityError:
                raise LearningConflict("用户名已使用") from None
            self.audit(
                connection, actor or user["id"], "account_created", user["id"], role
            )
        return user

    def login(self, username, password, address):
        if (
            not isinstance(username, str)
            or not isinstance(password, str)
            or len(username) > 32
            or len(password) > 128
        ):
            raise ValueError("用户名或密码错误")
        key = hashlib.sha256(f"{address}/{username.lower()}".encode()).hexdigest()
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            limit = connection.execute(
                "SELECT * FROM login_limits WHERE key=?", (key,)
            ).fetchone()
            if limit and limit["until"] > time.time() and limit["count"] >= 5:
                raise LearningConflict("登录失败次数过多，请 10 分钟后再试")
            row = connection.execute(
                "SELECT * FROM users WHERE username=?", (username.lower(),)
            ).fetchone()
            valid = row is not None and check_password_hash(
                row["password_hash"], password
            )
            if not valid:
                count = (
                    limit["count"] + 1 if limit and limit["until"] > time.time() else 1
                )
                until = (
                    limit["until"]
                    if limit and limit["until"] > time.time()
                    else time.time() + 600
                )
                connection.execute(
                    "INSERT OR REPLACE INTO login_limits VALUES (?,?,?)",
                    (key, count, until),
                )
            else:
                connection.execute("DELETE FROM login_limits WHERE key=?", (key,))
                token = secrets.token_urlsafe(32)
                connection.execute(
                    "INSERT INTO login_sessions VALUES (?,?,?)",
                    (
                        hashlib.sha256(token.encode()).hexdigest(),
                        row["id"],
                        time.time() + 28800,
                    ),
                )
                self.audit(connection, row["id"], "logged_in", row["id"])
                return self.public_user(row), token
        raise ValueError("用户名或密码错误")

    def user_for_token(self, token):
        if not isinstance(token, str) or len(token) > 100:
            return None
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT u.* FROM users u JOIN login_sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
            return self.public_user(row) if row else None

    def logout(self, token):
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "DELETE FROM login_sessions WHERE token_hash=?",
                (hashlib.sha256((token or "").encode()).hexdigest(),),
            )

    def change_password(self, user, old, new):
        if (
            not isinstance(old, str)
            or not isinstance(new, str)
            or not 8 <= len(new) <= 128
        ):
            raise ValueError("新密码须为 8 至 128 个字符")
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id=?", (user["id"],)
            ).fetchone()
            if not row or not check_password_hash(row[0], old):
                raise ValueError("原密码错误")
            connection.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(new), user["id"]),
            )
            connection.execute(
                "DELETE FROM login_sessions WHERE user_id=?", (user["id"],)
            )
            self.audit(connection, user["id"], "password_changed", user["id"])

    def classes(self, user):
        with closing(self.connect()) as connection:
            if user["role"] in {"teacher", "admin"}:
                rows = connection.execute(
                    "SELECT * FROM classes WHERE teacher_id=?", (user["id"],)
                )
            else:
                rows = connection.execute(
                    "SELECT c.* FROM classes c JOIN members m ON c.id=m.class_id WHERE m.user_id=?",
                    (user["id"],),
                )
            result = [dict(row) for row in rows]
            if user["role"] == "student":
                for row in result:
                    row.pop("join_code")
            return result

    def class_access(self, class_id, user, *, teacher=False):
        if not isinstance(class_id, str):
            raise ValueError("班级编号格式错误")
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM classes WHERE id=?", (class_id,)
            ).fetchone()
            owned = bool(row and row["teacher_id"] == user["id"])
            member = bool(
                row
                and connection.execute(
                    "SELECT 1 FROM members WHERE class_id=? AND user_id=?",
                    (class_id, user["id"]),
                ).fetchone()
            )
            if not row or not (owned if teacher else owned or member):
                raise LookupError("班级不存在或无权访问")
            return dict(row)

    def create_class(self, user, title, course_id):
        if (
            not isinstance(title, str)
            or not 1 <= len(title.strip()) <= 80
            or not isinstance(course_id, str)
            or course_id not in COURSES
        ):
            raise ValueError("请填写有效班级名称并选择已有课程")
        item = dict(
            id=uuid4().hex,
            title=title.strip(),
            course_id=course_id,
            teacher_id=user["id"],
            join_code=secrets.token_urlsafe(9),
        )
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "INSERT INTO classes VALUES (?,?,?,?,?)", tuple(item.values())
            )
            self.audit(connection, user["id"], "class_created", item["id"])
        return item

    def join_class(self, user, code):
        if not isinstance(code, str) or not 1 <= len(code) <= 32:
            raise ValueError("请输入有效的班级加入码")
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM classes WHERE join_code=?", (code.strip(),)
            ).fetchone()
            if not row:
                raise LookupError("加入码不存在或已更新")
            connection.execute(
                "INSERT OR IGNORE INTO members VALUES (?,?)", (row["id"], user["id"])
            )
            self.audit(connection, user["id"], "class_joined", row["id"])

    def roster(self, class_id, user):
        self.class_access(class_id, user, teacher=True)
        with closing(self.connect()) as connection:
            return [
                self.public_user(row)
                for row in connection.execute(
                    "SELECT u.* FROM users u JOIN members m ON u.id=m.user_id WHERE m.class_id=? ORDER BY u.name",
                    (class_id,),
                )
            ]

    def remove_member(self, class_id, user_id, actor):
        self.class_access(class_id, actor, teacher=True)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "DELETE FROM members WHERE class_id=? AND user_id=?",
                (class_id, user_id),
            )
            self.audit(connection, actor["id"], "member_removed", class_id, user_id)

    def rotate_code(self, class_id, actor):
        self.class_access(class_id, actor, teacher=True)
        code = secrets.token_urlsafe(9)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE classes SET join_code=? WHERE id=?", (code, class_id)
            )
            self.audit(connection, actor["id"], "join_code_rotated", class_id)
        return code

    def bind_chat(self, chat_id, user_id):
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "INSERT INTO chat_owners VALUES (?,?)", (chat_id, user_id)
            )

    def can_read_chat(self, chat_id, user):
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT user_id FROM chat_owners WHERE chat_id=?", (chat_id,)
            ).fetchone()
        return row[0] == user["id"] if row else user["role"] == "admin"

    def seed_content(self, questions):
        items = [("training", item) for item in EXERCISES.values()] + [
            ("qa", item) for item in questions
        ]
        with closing(self.connect()) as connection, connection:
            for category, item in items:
                key = f"{category}:{item['id']}"
                connection.execute(
                    "INSERT OR IGNORE INTO content_versions VALUES (?,1,?,'draft',?,?,NULL,NULL,'',NULL)",
                    (
                        key,
                        category,
                        json.dumps(item, ensure_ascii=False),
                        (
                            "仓库训练定义"
                            if category == "training"
                            else "仓库规范 JSONL 题库"
                        ),
                    ),
                )

    @staticmethod
    def version_public(row):
        item = dict(row)
        item["data"] = json.loads(item["data"])
        return item

    def versions(self, key=None):
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM content_versions"
                + (" WHERE item_key=?" if key else "")
                + " ORDER BY item_key,version DESC",
                (key,) if key else (),
            )
            return [self.version_public(row) for row in rows]

    def catalog(self, category, *, preview=False):
        versions = self.versions()
        result = {}
        for item in versions:
            if item["category"] != category or (
                item["status"] != "published" and not preview
            ):
                continue
            key = item["data"]["id"]
            if key not in result:
                data = item["data"]
                data.update(
                    content_version=item["version"],
                    publication_status=item["status"],
                    source=item["source"],
                    grading_version="rules-v1",
                )
                result[key] = data
        return result

    def new_version(self, key, user):
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM content_versions WHERE item_key=? ORDER BY version DESC LIMIT 1",
                (key,),
            ).fetchone()
            if not row:
                raise LookupError("题目不存在")
            version = row["version"] + 1
            data = json.loads(row["data"])
            if row["category"] == "qa":
                data["review"] = {
                    **data.get("review", {}),
                    "teacher_status": "pending",
                    "reviewer_id": None,
                    "note": "",
                }
            connection.execute(
                "INSERT INTO content_versions VALUES (?,?,?,'draft',?,?,?,NULL,'',NULL)",
                (
                    key,
                    version,
                    row["category"],
                    json.dumps(data, ensure_ascii=False),
                    row["source"],
                    user["id"],
                ),
            )
            self.audit(connection, user["id"], "content_drafted", key, str(version))
        return version

    def edit_version(self, key, version, user, data, source):
        if not isinstance(source, str) or not 1 <= len(source.strip()) <= 1000:
            raise ValueError("请填写题目来源或核查材料，不超过 1000 字符")
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM content_versions WHERE item_key=? AND version=?",
                (key, version),
            ).fetchone()
            if not row:
                raise LookupError("题目版本不存在")
            if row["status"] != "draft":
                raise LearningConflict("仅草稿可以编辑；修改已审核内容请建立新版本")
            original = json.loads(row["data"])
            if (
                not isinstance(data, dict)
                or data.get("id") != original["id"]
                or data.get("subject_id") != original["subject_id"]
            ):
                raise ValueError("题目编号和课程归属不能修改")
            if row["category"] == "training":
                validate_training_edit(original, data)
            else:
                if any(
                    not isinstance(data.get(field), str)
                    or not 1 <= len(data[field].strip()) <= 10000
                    for field in ("question", "answer")
                ):
                    raise ValueError(
                        "问答题须填写题干和参考答案，每项不超过 10000 字符"
                    )
                for field in (
                    "solution_steps",
                    "common_mistakes",
                    "assumptions",
                    "knowledge_points",
                ):
                    if (
                        not isinstance(data.get(field, []), list)
                        or len(data.get(field, [])) > 100
                        or any(
                            not isinstance(value, str) or len(value) > 5000
                            for value in data.get(field, [])
                        )
                    ):
                        raise ValueError("问答题步骤、易错点和条件须为有限的文本列表")
                if any(
                    data.get(field) != original.get(field)
                    for field in original
                    if field
                    not in {
                        "question",
                        "answer",
                        "solution_steps",
                        "common_mistakes",
                        "assumptions",
                        "knowledge_points",
                    }
                ) or set(data) != set(original):
                    raise ValueError("请保留编号、课程、题型、别名和其他归属信息")
            connection.execute(
                "UPDATE content_versions SET data=?,source=?,editor_id=? WHERE item_key=? AND version=?",
                (
                    json.dumps(data, ensure_ascii=False),
                    source.strip(),
                    user["id"],
                    key,
                    version,
                ),
            )
            self.audit(connection, user["id"], "content_edited", key, str(version))

    def transition(self, key, version, user, action, note):
        if action not in {"approve", "reject", "publish", "withdraw"}:
            raise ValueError("审核操作无效")
        if not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
            raise ValueError("请填写审核或发布依据，不超过 2000 字符")
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM content_versions WHERE item_key=? AND version=?",
                (key, version),
            ).fetchone()
            if not row:
                raise LookupError("题目版本不存在")
            required = {
                "approve": "draft",
                "reject": "draft",
                "publish": "approved",
                "withdraw": "published",
            }[action]
            if row["status"] != required:
                raise LearningConflict(f"当前状态 {row['status']} 不允许此操作")
            status = {
                "approve": "approved",
                "reject": "rejected",
                "publish": "published",
                "withdraw": "withdrawn",
            }[action]
            if action == "approve" and row["category"] == "qa":
                data = json.loads(row["data"])
                data["review"] = {
                    **data.get("review", {}),
                    "teacher_status": "approved",
                    "reviewer_id": user["id"],
                    "note": note.strip(),
                }
                connection.execute(
                    "UPDATE content_versions SET data=? WHERE item_key=? AND version=?",
                    (json.dumps(data, ensure_ascii=False), key, version),
                )
            if action == "publish":
                newer = connection.execute(
                    "SELECT 1 FROM content_versions WHERE item_key=? AND version>? AND status='published'",
                    (key, version),
                ).fetchone()
                if newer:
                    raise LearningConflict("不能覆盖已发布的更新版本")
                connection.execute(
                    "UPDATE content_versions SET status='withdrawn' WHERE item_key=? AND status='published'",
                    (key,),
                )
            connection.execute(
                "UPDATE content_versions SET status=?,reviewer_id=?,review_note=?,published_at=? WHERE item_key=? AND version=?",
                (
                    status,
                    user["id"],
                    note.strip(),
                    time.time() if action == "publish" else row["published_at"],
                    key,
                    version,
                ),
            )
            self.audit(
                connection,
                user["id"],
                f"content_{action}",
                key,
                json.dumps(
                    {"version": version, "note": note.strip()}, ensure_ascii=False
                ),
            )

    def audit_log(self, key):
        with closing(self.connect()) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT a.*,COALESCE(u.name, CASE WHEN a.actor_id='system:codex' THEN 'Codex 自动化审核' END) AS actor_name FROM school_audit a LEFT JOIN users u ON u.id=a.actor_id WHERE a.target=? ORDER BY a.id",
                    (key,),
                )
            ]
