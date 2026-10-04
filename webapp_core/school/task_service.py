from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import time
from contextlib import closing
from pathlib import PurePath
from uuid import uuid4

from webapp_core.learning.learning_service import ERROR_LABELS, LearningConflict, LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.learning.learning_courses import COURSES


class TaskService:
    def __init__(self, school, store, solver, user):
        self.school, self.store, self.solver, self.user = school, store, solver, user

    @staticmethod
    def _task(connection, task_id):
        row = connection.execute(
            "SELECT data FROM school_tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not row:
            raise LookupError("任务不存在")
        return json.loads(row[0])

    def _access(self, task, *, teacher=False):
        self.school.class_access(task["class_id"], self.user, teacher=teacher)
        if self.user["role"] == "student" and task["status"] == "draft":
            raise LookupError("任务不存在")

    @staticmethod
    def _submission(connection, task_id, user_id):
        row = connection.execute(
            "SELECT data FROM task_submissions WHERE task_id=? AND user_id=?",
            (task_id, user_id),
        ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {"status": "draft", "report": "", "reviews": []}
        )

    @staticmethod
    def _save_submission(connection, task_id, user_id, data):
        connection.execute(
            "INSERT INTO task_submissions VALUES (?,?,?) ON CONFLICT(task_id,user_id) DO UPDATE SET data=excluded.data",
            (task_id, user_id, json.dumps(data, ensure_ascii=False)),
        )

    @staticmethod
    def _attempts(connection, task_id, user_id):
        return [
            state
            for row in connection.execute("SELECT data FROM attempts")
            if (state := json.loads(row[0])).get("assignment_id") == task_id
            and state.get("owner_id") == user_id
        ]

    @staticmethod
    def _window(task, submission):
        now = time.time()
        if (
            task["status"] != "published"
            or now > (task.get("late_until") or task["due_at"])
            or submission["status"] == "finalized"
        ):
            raise LearningConflict("任务已结束或已交卷，不能再修改")

    @staticmethod
    def _hidden(task):
        return (
            task["kind"] == "quiz"
            and time.time() < task["due_at"]
            and task["status"] != "closed"
        )

    def list(self, class_id):
        self.school.class_access(class_id, self.user)
        with closing(self.store._connect()) as c:
            tasks = sorted(
                [
                    task
                    for row in c.execute("SELECT data FROM school_tasks")
                    if (task := json.loads(row[0]))["class_id"] == class_id
                    and (self.user["role"] != "student" or task["status"] != "draft")
                ],
                key=lambda t: t["created_at"],
                reverse=True,
            )

            if self.user["role"] == "student":
                uid = self.user["id"]
                submissions = {
                    row[0]: json.loads(row[1]) for row in c.execute(
                        "SELECT task_id,data FROM task_submissions WHERE user_id=?", (uid,)
                    )
                }
                attempts = {}
                for row in c.execute("SELECT data FROM attempts"):
                    state = json.loads(row[0])
                    if state.get("owner_id") == uid and state.get("assignment_id"):
                        attempts.setdefault(state["assignment_id"], []).append(state)
                files = {row[0] for row in c.execute(
                    "SELECT DISTINCT task_id FROM task_files WHERE user_id=?", (uid,)
                )}
                now = time.time()
                for task in tasks:
                    submission = submissions.get(task["id"], {})
                    states = submission.get("attempts") or attempts.get(task["id"], [])
                    finalized = submission.get("status") == "finalized"
                    started = bool(states or submission.get("report") or task["id"] in files)
                    ended = task["status"] == "closed" or now > (task.get("late_until") or task["due_at"])
                    task["student_progress"] = dict(
                        status="finalized" if finalized else "ended" if ended else "in_progress" if started else "not_started",
                        submitted=len({a["exercise_id"] for a in states if a.get("submissions")}),
                        total=len(task["exercises"]),
                        late=not ended and not finalized and now > task["due_at"],
                    )
            return tasks

    def write(self, data, task_id=None):
        if not isinstance(data, dict):
            raise ValueError("请求须为对象")
        with closing(self.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            old = self._task(c, task_id) if task_id else None
            class_id = old["class_id"] if old else data.get("class_id")
            classroom = self.school.class_access(class_id, self.user, teacher=True)
            if old and old["status"] != "draft":
                raise LearningConflict("已发布任务不能修改，请另建任务")
            kind = data.get("kind")
            if not isinstance(kind, str) or kind not in {
                "homework",
                "quiz",
                "experiment",
            }:
                raise ValueError("请选择作业、测验或实验")

            def text(key, maximum, required=False):
                value = data.get(key, "")
                if (
                    not isinstance(value, str)
                    or len(value) > maximum
                    or (required and not value.strip())
                ):
                    raise ValueError("任务名称、说明或评分标准内容为空或过长")
                return value.strip()

            title, instructions, rubric = (
                text("title", 120, True),
                text("instructions", 6000),
                text("rubric", 3000, kind == "experiment"),
            )
            due = data.get("due_at")
            late = data.get("late_until")
            if (
                type(due) not in {int, float}
                or not math.isfinite(due)
                or due <= time.time()
            ):
                raise ValueError("截止时间须为未来时间")
            if late is not None and (
                kind == "quiz"
                or type(late) not in {int, float}
                or not math.isfinite(late)
                or not due < late <= due + 30 * 86400
            ):
                raise ValueError("补交时间须在截止后 30 天内，测验不支持补交")
            limit = data.get("max_submissions", 1 if kind == "quiz" else 10)
            if type(limit) is not int or not 1 <= limit <= (
                5 if kind == "quiz" else 20
            ):
                raise ValueError("每题提交次数超出范围")
            ids = data.get("exercise_ids", [])
            if (
                not isinstance(ids, list)
                or any(not isinstance(i, str) for i in ids)
                or len(ids) > 20
                or len(set(ids)) != len(ids)
                or (kind != "experiment" and not ids)
            ):
                raise ValueError("作业和测验请选择 1 至 20 道题；实验可只提交材料")
            catalog = self.school.catalog("training")
            if any(
                i not in catalog or catalog[i]["subject_id"] != classroom["course_id"]
                for i in ids
            ):
                raise ValueError("只能布置本课程已发布的训练题")
            now = time.time()
            plan_id = old.get("lesson_plan_id") if old else data.get("lesson_plan_id")
            plan_revision = None
            if plan_id is not None:
                if not isinstance(plan_id, str):
                    raise ValueError("备课安排编号格式不正确")
                row = c.execute("SELECT data FROM lesson_plans WHERE id=?", (plan_id,)).fetchone()
                if not row:
                    raise LookupError("备课安排不存在")
                plan = json.loads(row[0])
                if plan["class_id"] != class_id:
                    raise PermissionError("备课安排不属于本班")
                if not old and data.get("lesson_plan_revision") != plan["revision"]:
                    raise LearningConflict("备课安排已更新，请重新打开后布置补练")
                if any(catalog[i]["chapter_id"] != plan["chapter_id"] for i in ids):
                    raise ValueError("关联补练只能选择备课章节的题目")
                plan_revision = old["lesson_plan_revision"] if old else plan["revision"]
            task = dict(
                id=task_id or uuid4().hex,
                class_id=class_id,
                kind=kind,
                title=title,
                instructions=instructions,
                rubric=rubric,
                due_at=due,
                late_until=late,
                max_submissions=limit,
                feedback="after_due" if kind == "quiz" else "immediate",
                exercises=[catalog[i] for i in ids],
                status="draft",
                creator_id=self.user["id"],
                created_at=old["created_at"] if old else now,
                updated_at=now,
            )
            if plan_id:
                task.update(lesson_plan_id=plan_id, lesson_plan_revision=plan_revision)
            c.execute(
                "INSERT INTO school_tasks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (task["id"], json.dumps(task, ensure_ascii=False)),
            )
            return task

    def transition(self, task_id, action):
        with closing(self.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            task = self._task(c, task_id)
            self._access(task, teacher=True)
            if action == "publish" and task["status"] == "draft":
                current = self.school.catalog("training")
                if task["due_at"] <= time.time() or any(
                    current.get(e["id"]) != e for e in task["exercises"]
                ):
                    raise LearningConflict(
                        "截止时间或题目发布版本已变化，请重新编辑任务"
                    )
                task["status"] = "published"
            elif action == "close" and task["status"] == "published":
                task["status"] = "closed"
            else:
                raise LearningConflict("任务状态不支持此操作")
            task["updated_at"] = time.time()
            c.execute(
                "UPDATE school_tasks SET data=? WHERE id=?",
                (json.dumps(task, ensure_ascii=False), task_id),
            )
        return task

    def start(self, task_id, exercise_id):
        if self.user["role"] != "student":
            raise PermissionError("任务作答仅供班级学生使用")
        with closing(self.store._connect()) as c:
            task = self._task(c, task_id)
            self._access(task)
        service = LearningService(
            LearningStore(self.store.path, owner_id=self.user["id"]),
            self.solver,
            catalog={e["id"]: e for e in task["exercises"]},
            class_id=task["class_id"],
            tasks=self,
            assignment_kind=task["kind"],
        )
        return service.start(exercise_id, assignment_id=task_id)

    def guard(self, state, connection, operation):
        task_id = state.get("assignment_id")
        if not task_id:
            return
        task = self._task(connection, task_id)
        self._access(task)
        submission = self._submission(connection, task_id, state["owner_id"])
        if operation in {"solution", "tutoring"}:
            if self._hidden(task):
                raise LearningConflict("测验结束后才开放解析")
            if task["kind"] == "quiz":
                self._archive(connection, task)
                fresh = json.loads(
                    connection.execute(
                        "SELECT data FROM attempts WHERE id=?", (state["id"],)
                    ).fetchone()[0]
                )
                state.clear()
                state.update(fresh)
            return
        self._window(task, submission)
        if operation == "hint" and task["kind"] == "quiz":
            raise LearningConflict("测验不提供提示")
        if len(state["submissions"]) >= task["max_submissions"]:
            raise LearningConflict("已达到每题提交次数上限")

    def policy(self, state):
        with closing(self.store._connect()) as c:
            task = self._task(c, state["assignment_id"])
            submission = self._submission(c, task["id"], state["owner_id"])
        can_edit = True
        try:
            self._window(task, submission)
        except LearningConflict:
            can_edit = False
        can_edit = can_edit and len(state["submissions"]) < task["max_submissions"]
        hidden = self._hidden(task)
        return dict(
            id=task["id"],
            title=task["title"],
            kind=task["kind"],
            due_at=task["due_at"],
            late_until=task["late_until"],
            max_submissions=task["max_submissions"],
            feedback_hidden=hidden,
            can_edit=can_edit,
            can_hint=can_edit and task["kind"] != "quiz",
            can_solution=not hidden,
        )

    def _finalize(self, c, task, user_id, *, automatic=False):
        submission = self._submission(c, task["id"], user_id)
        if submission["status"] == "finalized":
            return submission
        attempts = self._attempts(c, task["id"], user_id)
        if task["kind"] == "quiz":
            if not attempts:
                raise LearningConflict("请先开始测验作答")
            service = LearningService(
                LearningStore(self.store.path, owner_id=user_id), self.solver
            )
            existing = [
                json.loads(row[0])
                for row in c.execute("SELECT data FROM attempts")
                if json.loads(row[0]).get("owner_id") == user_id
            ]
            for state in attempts:
                if len(state["submissions"]) < task["max_submissions"] and (
                    not state["submissions"]
                    or state["draft"] != state["submissions"][-1]["rows"]
                ):
                    kind, event = service._apply_submission(
                        state, existing, state["draft"], allow_invalid=True
                    )
                    state["updated_at"] = time.time()
                    c.execute(
                        "UPDATE attempts SET data=? WHERE id=?",
                        (json.dumps(state, ensure_ascii=False), state["id"]),
                    )
                    LearningStore._event(c, state["id"], kind, event)
        elif len(attempts) != len(task["exercises"]) or any(
            not a["submissions"] or a["draft"] != a["submissions"][-1]["rows"]
            for a in attempts
        ):
            raise LearningConflict("请先提交所有布置题目的作答")
        files = self._files(c, task["id"], user_id)
        if (
            task["kind"] == "experiment"
            and not submission["report"].strip()
            and not files
        ):
            raise LearningConflict("请填写实验报告或上传材料")
        passed = sum(a["status"] == "passed" for a in attempts)
        submission.update(
            status="finalized",
            finalized_at=time.time(),
            automatic=automatic,
            late=not automatic and time.time() > task["due_at"],
            attempts=attempts,
            file_ids=[f["id"] for f in files],
            training_score=(
                round(100 * passed / len(task["exercises"]), 2)
                if task["exercises"]
                else None
            ),
            score=(
                None
                if task["kind"] == "experiment"
                else round(100 * passed / len(task["exercises"]), 2)
            ),
        )
        self._save_submission(c, task["id"], user_id, submission)
        return submission

    def finalize(self, task_id):
        if self.user["role"] != "student":
            raise PermissionError("此操作仅供学生使用")
        with closing(self.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            task = self._task(c, task_id)
            self._access(task)
            self._window(task, self._submission(c, task_id, self.user["id"]))
            self._finalize(c, task, self.user["id"])
        return self.detail(task_id)

    def _archive(self, c, task):
        if task["kind"] != "quiz" or (
            task["status"] != "closed" and time.time() < task["due_at"]
        ):
            return
        for member in self.school.roster(
            task["class_id"], {"id": task["creator_id"], "role": "teacher"}
        ):
            if self._attempts(c, task["id"], member["id"]):
                self._finalize(c, task, member["id"], automatic=True)

    def archive_task(self, task_id):
        with closing(self.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            self._archive(c, self._task(c, task_id))

    @staticmethod
    def _files(c, task_id, user_id):
        return [
            dict(id=row[0], name=row[1], size=row[2], sha256=row[3], created_at=row[4])
            for row in c.execute(
                "SELECT id,name,length(data),sha256,created_at FROM task_files WHERE task_id=? AND user_id=? ORDER BY created_at",
                (task_id, user_id),
            )
        ]

    def detail(self, task_id, student_id=None):
        with closing(self.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            task = self._task(c, task_id)
            self._access(task)
            if student_id:
                self._access(task, teacher=True)
                if not any(
                    m["id"] == student_id
                    for m in self.school.roster(task["class_id"], self.user)
                ):
                    raise LookupError("学生不属于该班级")
            self._archive(c, task)
            uid = student_id or (
                self.user["id"] if self.user["role"] == "student" else None
            )
            if not uid:
                return dict(task=task, submission=None, attempts=[], files=[])
            submission = self._submission(c, task_id, uid)
            states = submission.get("attempts") or self._attempts(c, task_id, uid)
            files = self._files(c, task_id, uid)
        service = LearningService(
            LearningStore(self.store.path, owner_id=uid),
            self.solver,
            tasks=None if student_id else self,
        )
        public = [service._public(s) for s in states]
        if student_id:
            for result, state in zip(public, states):
                result["submissions"] = state["submissions"]
        visible = {k: v for k, v in submission.items() if k != "attempts"}
        hidden = not student_id and self._hidden(task)
        if hidden:
            visible["score"] = visible["training_score"] = None
            visible["reviews"] = []
        return dict(
            task=task,
            submission=visible,
            attempts=public,
            files=files,
            feedback_hidden=hidden,
        )

    def report_text(self, task_id, report):
        if not isinstance(report, str) or len(report) > 12000:
            raise ValueError("报告须为不超过 12000 字的文本")
        with closing(self.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            task = self._task(c, task_id)
            self._material_access(c, task)
            sub = self._submission(c, task_id, self.user["id"])
            sub["report"] = report
            self._save_submission(c, task_id, self.user["id"], sub)
        return self.detail(task_id)

    def _material_access(self, c, task):
        self._access(task)
        if self.user["role"] != "student" or task["kind"] != "experiment":
            raise PermissionError("材料提交仅供实验任务的学生使用")
        self._window(task, self._submission(c, task["id"], self.user["id"]))

    def upload(self, task_id, file):
        if file is None:
            raise ValueError("请选择材料文件")
        name = file.filename or ""
        raw = file.read(1048577)
        suffix = PurePath(name).suffix.lower()
        if (
            not name
            or len(name) > 120
            or any(ord(ch) < 32 for ch in name)
            or "/" in name
            or "\\" in name
            or suffix not in {".txt", ".md", ".c", ".pdf"}
        ):
            raise ValueError("仅支持 txt、md、c、pdf 文件，文件名须完整且无路径")
        if not raw or len(raw) > 1048576:
            raise ValueError("单个材料须为 1 字节至 1 MB")
        if suffix == ".pdf":
            if not raw.startswith(b"%PDF-") or b"%%EOF" not in raw[-2048:]:
                raise ValueError("PDF 文件格式错误")
        else:
            try:
                raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ValueError("文本和代码材料须使用 UTF-8 编码")
            if b"\0" in raw:
                raise ValueError("不接受二进制代码文件")
        with closing(self.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            task = self._task(c, task_id)
            self._material_access(c, task)
            if len(self._files(c, task_id, self.user["id"])) >= 3:
                raise LearningConflict("每次实验最多上传 3 个材料")
            c.execute(
                "INSERT INTO task_files VALUES (?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    task_id,
                    self.user["id"],
                    name,
                    raw,
                    hashlib.sha256(raw).hexdigest(),
                    time.time(),
                ),
            )
        return self.detail(task_id)

    def file(self, task_id, file_id, *, delete=False):
        with closing(self.store._connect()) as c, c:
            if delete:
                c.execute("BEGIN IMMEDIATE")
            task = self._task(c, task_id)
            self._access(task)
            row = c.execute(
                "SELECT user_id,name,data FROM task_files WHERE id=? AND task_id=?",
                (file_id, task_id),
            ).fetchone()
            if not row:
                raise LookupError("材料不存在")
            if self.user["role"] == "student" and row[0] != self.user["id"]:
                raise LookupError("材料不存在")
            if self.user["role"] != "student" and not any(
                m["id"] == row[0]
                for m in self.school.roster(task["class_id"], self.user)
            ):
                raise LookupError("学生不属于该班级")
            if delete:
                self._material_access(c, task)
                c.execute("DELETE FROM task_files WHERE id=?", (file_id,))
            return row[1], row[2]

    def review(self, task_id, student_id, data):
        self.detail(task_id, student_id)
        comment = data.get("comment")
        if not isinstance(comment, str) or not comment.strip() or len(comment) > 3000:
            raise ValueError("请填写不超过 3000 字的评语")
        with closing(self.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            task = self._task(c, task_id)
            self._access(task, teacher=True)
            sub = self._submission(c, task_id, student_id)
            if sub["status"] != "finalized":
                raise LearningConflict("学生交卷后才能评分或评语")
            if task["kind"] == "experiment":
                score = data.get("score")
                if (
                    type(score) not in {int, float}
                    or not math.isfinite(score)
                    or not 0 <= score <= 100
                ):
                    raise ValueError("实验评分须为 0 至 100")
                sub["score"] = score
            elif data.get("score") is not None:
                raise ValueError("作业与测验得分由核验结果计算")
            sub["reviews"].append(
                dict(
                    teacher_id=self.user["id"],
                    teacher_name=self.user["name"],
                    created_at=time.time(),
                    score=sub["score"],
                    comment=comment.strip(),
                )
            )
            self._save_submission(c, task_id, student_id, sub)
        return self.detail(task_id, student_id)

    @staticmethod
    def _pass_kind(state):
        if state["status"] != "passed":
            return None
        for index, submission in enumerate(state["submissions"]):
            if not submission["evaluation"]["passed"]:
                continue
            if index > 0:
                return "corrected"
            if submission.get("unassisted"):
                return "independent"
            assistance = submission.get("assistance", {})
            if any(assistance.get(key) for key in (
                "hint_count", "solution_viewed", "tutoring_viewed"
            )):
                return "assisted"
            if assistance.get("previously_seen"):
                return "repeated"
            return "unclassified_pass"
        return "unclassified_pass"

    def report(self, class_id, chapter_id=None):
        classroom = self.school.class_access(class_id, self.user, teacher=True)
        roster = self.school.roster(class_id, self.user)
        tasks = [t for t in self.list(class_id) if t["status"] != "draft"]
        chapter_options = {
            e["chapter_id"]: e["chapter_title"]
            for task in tasks for e in task["exercises"]
        }
        if chapter_id and chapter_id not in chapter_options:
            raise ValueError("所选章节没有本班已发布任务")
        assigned_ids = {e["id"] for task in tasks for e in task["exercises"]}
        if chapter_id:
            tasks = [t for t in tasks if any(e["chapter_id"] == chapter_id for e in t["exercises"])]
        pass_kinds = ("independent", "assisted", "corrected", "repeated", "unclassified_pass")
        metric_keys = ("started", "submitted", "passed", *pass_kinds)
        rows, questions, error_groups = [], {}, {}
        with closing(self.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            for task in tasks:
                self._archive(c, task)
                exercises = [e for e in task["exercises"] if not chapter_id or e["chapter_id"] == chapter_id]
                for exercise in exercises:
                    questions[(task["id"], exercise["id"])] = dict(
                        task_id=task["id"],
                        task_title=task["title"],
                        exercise_id=exercise["id"],
                        title=exercise["title"],
                        chapter_id=exercise["chapter_id"],
                        chapter_title=exercise["chapter_title"],
                        content_version=exercise["content_version"],
                        expected_answers=len(roster),
                        **dict.fromkeys(metric_keys, 0),
                    )
                for student in roster:
                    sub = self._submission(c, task["id"], student["id"])
                    # Finalized work is an immutable snapshot, including empty work.
                    states = sub["attempts"] if "attempts" in sub else self._attempts(
                        c, task["id"], student["id"]
                    )
                    selected_ids = {e["id"] for e in exercises}
                    states = [state for state in states if state["exercise_id"] in selected_ids]
                    errors = {}
                    metrics = dict.fromkeys(metric_keys, 0)
                    for state in states:
                        key = (task["id"], state["exercise_id"])
                        if key not in questions:
                            continue
                        q = questions[key]
                        counts = dict.fromkeys(metric_keys, 0)
                        counts["started"] = 1
                        counts["submitted"] = int(bool(state["submissions"]))
                        counts["passed"] = int(state["status"] == "passed")
                        pass_kind = self._pass_kind(state)
                        if pass_kind:
                            counts[pass_kind] = 1
                        for name, value in counts.items():
                            q[name] += value
                            metrics[name] += value
                        for number, submission in enumerate(state["submissions"], 1):
                            error = submission["evaluation"]["first_error"]
                            if error:
                                label = ERROR_LABELS.get(error["error_code"], "其他核验错误")
                                errors[label] = errors.get(label, 0) + 1
                                exercise = state["exercise_snapshot"]
                                family = exercise.get("family_id", exercise["algorithm"])
                                group_key = (exercise["chapter_id"], exercise["kind"], family, error["error_code"])
                                group = error_groups.setdefault(group_key, dict(
                                    id=hashlib.sha256(json.dumps(group_key).encode()).hexdigest()[:20],
                                    chapter_id=exercise["chapter_id"],
                                    chapter_title=exercise["chapter_title"],
                                    kind=exercise["kind"], family=family,
                                    code=error["error_code"], label=label,
                                    students=set(), current_students=set(), evidence=[],
                                ))
                                group["students"].add(student["id"])
                                if number == len(state["submissions"]):
                                    group["current_students"].add(student["id"])
                                group["evidence"].append(dict(
                                    task_id=task["id"], task_title=task["title"],
                                    student_id=student["id"], student_name=student["name"],
                                    attempt_id=state["id"], exercise_id=exercise["id"],
                                    exercise_title=exercise["title"], content_version=state["content_version"],
                                    submission_number=number, created_at=submission["created_at"],
                                    step=error.get("step"), field=error.get("field"),
                                    message=error["message"],
                                ))
                    rows.append(
                        dict(
                            task_id=task["id"],
                            task_title=task["title"],
                            kind=task["kind"],
                            student_id=student["id"],
                            student_name=student["name"],
                            status=(
                                sub["status"]
                                if sub["status"] == "finalized"
                                else (
                                    "started"
                                    if states
                                    or sub["report"]
                                    or self._files(c, task["id"], student["id"])
                                    else "not_started"
                                )
                            ),
                            score=sub.get("score"),
                            late=sub.get("late", False),
                            expected_answers=len(exercises),
                            **metrics,
                            hints=sum(a["hint_count"] for a in states),
                            solutions=sum(a["solution_viewed"] for a in states),
                            corrections=sum(
                                max(0, len(a["submissions"]) - 1) for a in states
                            ),
                            errors=errors,
                            pending_review=task["kind"] == "experiment"
                            and sub["status"] == "finalized"
                            and sub.get("score") is None,
                        )
                    )
        chapters = {}
        for question in questions.values():
            chapter = chapters.setdefault(
                question["chapter_id"],
                dict(
                    id=question["chapter_id"],
                    title=question["chapter_title"],
                    assigned_questions=0,
                    expected_answers=0,
                    **dict.fromkeys(metric_keys, 0),
                ),
            )
            chapter["assigned_questions"] += 1
            for key in (*metric_keys, "expected_answers"):
                chapter[key] += question[key]
        totals = {
            key: sum(q[key] for q in questions.values())
            for key in (*metric_keys, "expected_answers")
        }
        for item in [*rows, *questions.values(), *chapters.values(), totals]:
            item["unsubmitted"] = item["expected_answers"] - item["submitted"]
            item["unpassed"] = item["submitted"] - item["passed"]
        catalog = self.school.catalog("training")
        common_errors = []
        for group in error_groups.values():
            group["student_count"] = len(group.pop("students"))
            group["current_student_count"] = len(group.pop("current_students"))
            group["occurrences"] = len(group["evidence"])
            group["evidence"].sort(key=lambda e: (e["created_at"], e["attempt_id"], e["submission_number"]), reverse=True)
            candidates = [
                e for e in catalog.values()
                if e["subject_id"] == classroom["course_id"]
                and e["chapter_id"] == group["chapter_id"]
                and e["kind"] == group["kind"]
                and e.get("family_id", e["algorithm"]) == group["family"]
                and group["code"] in e.get("training_tags", [])
                and e["id"] not in assigned_ids
            ]
            group["exercises"] = [
                dict(id=e["id"], title=e["title"], content_version=e["content_version"])
                for e in sorted(candidates, key=lambda e: e["id"])[:5]
            ]
            group["teaching_advice"] = (
                f"围绕“{group['label']}”讲评：选取下方首错步骤，让学生说明判断依据，再对照题目条件核验。"
                f"已有{group['student_count']}名学生出现此首错，其中{group['current_student_count']}名在对应作答的最新提交仍出现；"
                "核验错误不等于已确认的理解原因，应先追问。"
            )
            group["practice_advice"] = (
                "使用下列同章节、同题型、同算法族且训练标签匹配的已发布题，先独立作答，再订正讲评。"
                "这些题尚未在本班已发布任务中布置，不代表学生从未私下做过；独立性以实际提交记录为准。"
                if candidates else
                "暂无未在本班布置且标签匹配的已发布补练题；请先补充并审核题目，不能把原题订正当成新题独立通过。"
            )
            common_errors.append(group)
        common_errors.sort(key=lambda g: (-g["current_student_count"], -g["student_count"], -g["occurrences"], g["id"]))
        return dict(
            generated_at=time.time(),
            class_id=class_id,
            course_name=COURSES[classroom["course_id"]]["name"],
            chapter_id=chapter_id,
            chapter_options=[dict(id=key, title=value) for key, value in chapter_options.items()],
            common_errors=common_errors,
            course_id=classroom["course_id"],
            member_count=len(roster),
            task_count=len(tasks),
            expected_submissions=len(roster) * len(tasks),
            finalized=sum(r["status"] == "finalized" for r in rows),
            totals=totals,
            rows=rows,
            questions=list(questions.values()),
            chapters=list(chapters.values()),
        )

    def csv(self, class_id, chapter_id=None):
        report = self.report(class_id, chapter_id)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(
            [
                "任务",
                "类型",
                "学生",
                "状态",
                "得分",
                "补交",
                "已核验题数",
                "通过题数",
                "首次独立通过",
                "辅助通过",
                "订正通过",
                "重复作答通过",
                "通过但辅助信息缺失",
                "已提交未通过",
                "未提交题次",
                "提示次数",
                "查看解析题数",
                "订正次数",
                "待评分",
            ]
        )

        def safe(value):
            text = str(value if value is not None else "")
            return (
                "'" + text
                if text.lstrip().startswith(("=", "+", "-", "@"))
                or text.startswith(("\t", "\r", "\n"))
                else text
            )

        kinds = {"homework": "作业", "quiz": "测验", "experiment": "实验"}
        statuses = {"finalized": "已交卷", "started": "进行中", "not_started": "未开始"}
        for r in report["rows"]:
            writer.writerow(
                [
                    safe(v)
                    for v in (
                        r["task_title"],
                        kinds[r["kind"]],
                        r["student_name"],
                        statuses[r["status"]],
                        r["score"],
                        "是" if r["late"] else "否",
                        r["submitted"],
                        r["passed"],
                        r["independent"],
                        r["assisted"],
                        r["corrected"],
                        r["repeated"],
                        r["unclassified_pass"],
                        r["unpassed"],
                        r["unsubmitted"],
                        r["hints"],
                        r["solutions"],
                        r["corrections"],
                        "是" if r["pending_review"] else "否",
                    )
                ]
            )
        return ("\ufeff" + stream.getvalue()).encode("utf-8")
