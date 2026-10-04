"""Exam goals and executable daily tasks backed by actual learning evidence."""

from contextlib import closing
from datetime import date, datetime, timedelta
import json
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from .assistant_store import AssistantStore, enqueue, encode
from .learning_courses import COURSES, course_chapters
from .learning_path import LearningPathService
from .learning_service import LearningConflict, LearningService


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def family(exercise):
    return (
        exercise["subject_id"],
        exercise["kind"],
        exercise.get("family_id", exercise["algorithm"]),
    )


def independent(attempt):
    rows = attempt["submissions"]
    return bool(rows and rows[0]["evaluation"]["passed"] and rows[0]["unassisted"])


class ExamPlans:
    def __init__(self, learning):
        self.learning = learning
        self.owner = learning.store.owner_id
        if not self.owner:
            raise PermissionError("请先登录")
        self.store = AssistantStore(learning.store.path)
        with closing(self.store.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS assistant_exam_plans (id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS assistant_exam_active ON assistant_exam_plans(owner) WHERE status='active'"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS assistant_exam_owner ON assistant_exam_plans(owner)"
            )

    def _scoped(self, plan):
        return LearningService(
            self.learning.store,
            self.learning.solver,
            catalog={
                key: e
                for key, e in self.learning.catalog.items()
                if e["subject_id"] == plan["subject_id"]
                and e["chapter_id"] in plan["chapters"]
            },
        )

    def _load(self, db, plan_id):
        row = db.execute(
            "SELECT * FROM assistant_exam_plans WHERE id=? AND owner=?",
            (plan_id, self.owner),
        ).fetchone()
        if not row:
            raise LookupError("复习计划不存在")
        return {
            **json.loads(row["data"]),
            "status": row["status"],
            "revision": row["revision"],
        }

    def _save(self, db, plan, kind):
        plan["revision"] += 1
        db.execute(
            "UPDATE assistant_exam_plans SET status=?,revision=?,data=? WHERE id=? AND owner=?",
            (plan["status"], plan["revision"], encode(plan), plan["id"], self.owner),
        )
        enqueue(
            db,
            self.owner,
            f"exam:{plan['id']}:{plan['revision']}",
            encode(
                {
                    "来源": "用户确认的复习计划",
                    "操作": kind,
                    "课程": COURSES[plan["subject_id"]]["name"],
                    "考试日期": plan["exam_date"],
                    "范围": plan["chapters"],
                    "每日分钟": plan["minutes"],
                    "单日调整": plan.get("overrides", {}),
                }
            ),
        )

    def _check(self, plan, revision):
        if type(revision) is not int or revision != plan["revision"]:
            raise LearningConflict("计划已更新，请刷新后重试")

    def draft(self, data):
        subject = data.get("subject_id")
        if not isinstance(subject, str) or subject not in COURSES:
            raise ValueError("请选择课程")
        minutes = data.get("minutes")
        if type(minutes) is not int or not 15 <= minutes <= 120:
            raise ValueError("每日学习时间须为15—120分钟")
        try:
            exam = date.fromisoformat(data.get("exam_date", ""))
        except (TypeError, ValueError):
            raise ValueError("请填写明确的考试日期") from None
        if not 1 <= (exam - today()).days <= 90:
            raise ValueError("考试日期须在明天至90天内；计划安排在考试日前完成")
        chapters = data.get("chapters")
        allowed = {c["id"]: c for c in course_chapters(subject)}
        if (
            not isinstance(chapters, list)
            or not chapters
            or any(not isinstance(c, str) or c not in allowed for c in chapters)
        ):
            raise ValueError("请选择本课程的考试章节")
        chapters = list(dict.fromkeys(chapters))
        attempts = [
            a for a in self.learning.store.list_attempts() if not a.get("assignment_id")
        ]
        seen = {a["exercise_id"] for a in attempts}
        grouped = {}
        for exercise in self.learning.catalog.values():
            if exercise["subject_id"] == subject and exercise["chapter_id"] in chapters:
                grouped.setdefault(family(exercise), []).append(exercise)
        tasks, gaps = [], []
        path = LearningPathService(self.learning)
        for chapter_id in chapters:
            c = allowed[chapter_id]
            if path.material_path({**c, "subject_id": subject}):
                tasks.append(
                    {
                        "id": uuid4().hex,
                        "kind": "reading",
                        "chapter_id": chapter_id,
                        "title": c["title"]
                        if "title" in c
                        else c.get("name", chapter_id),
                        "minutes": 5,
                        "priority": 1,
                        "reason": "回顾本章资料；阅读标记不代表独立掌握。",
                    }
                )
            else:
                gaps.append(
                    f"{c.get('title', c.get('name', chapter_id))}暂无可用章节资料"
                )
            if not any(
                e["chapter_id"] == chapter_id
                for items in grouped.values()
                for e in items
            ):
                gaps.append(
                    f"{c.get('title', c.get('name', chapter_id))}暂无已发布训练，无法自动验证掌握"
                )
        covered = []
        for key, items in grouped.items():
            evidence = sorted(
                [a for a in attempts if family(self.learning._exercise(a)) == key],
                key=lambda a: a["updated_at"],
            )
            submitted = [a for a in evidence if a["submissions"]]
            if submitted and independent(submitted[-1]):
                covered.append(
                    {
                        "title": items[0]["algorithm"],
                        "attempt_id": submitted[-1]["id"],
                        "reason": "最近作答已有首次独立通过证据，减少重复练习",
                    }
                )
                continue
            unused = [e for e in items if e["id"] not in seen]
            active = next(
                (
                    a
                    for a in reversed(evidence)
                    if a["status"] != "passed"
                    and a["exercise_id"] in self.learning.catalog
                ),
                None,
            )
            exercise = (
                self.learning.catalog[active["exercise_id"]]
                if active
                else next(
                    iter(
                        sorted(
                            unused, key=lambda e: (e["difficulty"] != "基础", e["id"])
                        )
                    ),
                    None,
                )
            )
            if not exercise:
                gaps.append(
                    f"{items[0]['algorithm']}暂无未练习的新题，暂时无法独立验证"
                )
                continue
            wrong = bool(submitted and not independent(submitted[-1]))
            tasks.append(
                {
                    "id": uuid4().hex,
                    "kind": "practice",
                    "chapter_id": exercise["chapter_id"],
                    "exercise_id": exercise["id"],
                    "family": list(key),
                    "title": exercise["title"],
                    "minutes": 15,
                    "priority": 0 if wrong else 2,
                    "attempt_id": active["id"] if active else None,
                    "reason": "已有错误或辅助作答，优先订正并用新题复测"
                    if wrong
                    else "暂无充分作答证据，先做一道摸底题",
                }
            )
        tasks.sort(
            key=lambda t: (t["priority"], chapters.index(t["chapter_id"]), t["title"])
        )
        plan = {
            "id": uuid4().hex,
            "status": "draft",
            "revision": 0,
            "subject_id": subject,
            "exam_date": exam.isoformat(),
            "minutes": minutes,
            "chapters": chapters,
            "tasks": tasks,
            "gaps": gaps,
            "covered": covered,
            "created_at": time.time(),
            "adopted_at": None,
            "overrides": {},
            "budget_proposal": None,
        }
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE assistant_exam_plans SET status='superseded' WHERE owner=? AND status='draft'",
                (self.owner,),
            )
            db.execute(
                "INSERT INTO assistant_exam_plans VALUES (?,?,?,?,?)",
                (plan["id"], self.owner, "draft", 0, encode(plan)),
            )
        return self.view(plan)

    def overview(self):
        with closing(self.store.connect()) as db:
            rows = db.execute(
                "SELECT id FROM assistant_exam_plans WHERE owner=? AND status IN ('draft','active')",
                (self.owner,),
            ).fetchall()
            plans = [self.view(self._load(db, row["id"])) for row in rows]
        return {
            "plans": plans,
            "today": today().isoformat(),
            "courses": [
                {"id": key, "name": value["name"], "chapters": course_chapters(key)}
                for key, value in COURSES.items()
            ],
        }

    def adopt(self, plan_id, revision):
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            plan = self._load(db, plan_id)
            if plan["status"] == "active":
                return self.view(plan)
            self._check(plan, revision)
            if (
                plan["status"] != "draft"
                or date.fromisoformat(plan["exam_date"]) <= today()
            ):
                raise LearningConflict("草稿已失效，请重新生成")
            db.execute(
                "UPDATE assistant_exam_plans SET status='archived' WHERE owner=? AND status='active'",
                (self.owner,),
            )
            plan.update(status="active", adopted_at=time.time())
            self._save(db, plan, "采用计划")
        return self.view(plan)

    def budget(self, plan_id, revision, minutes, propose=False):
        if type(minutes) is not int or minutes not in {0, *range(15, 121)}:
            raise ValueError("今日时间须为0（休息）或15—120分钟")
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            plan = self._load(db, plan_id)
            self._check(plan, revision)
            if (
                plan["status"] != "active"
                or date.fromisoformat(plan["exam_date"]) <= today()
            ):
                raise LearningConflict("此计划不可调整")
            if propose:
                plan["budget_proposal"] = {
                    "date": today().isoformat(),
                    "minutes": minutes,
                }
                # A proposal is not a confirmed user fact.
                plan["revision"] += 1
                db.execute(
                    "UPDATE assistant_exam_plans SET revision=?,data=? WHERE id=? AND owner=?",
                    (plan["revision"], encode(plan), plan_id, self.owner),
                )
            else:
                plan["overrides"][today().isoformat()] = minutes
                plan["budget_proposal"] = None
                self._save(db, plan, "调整今天时间，不改变其他日期")
        return self.view(plan)

    def view(self, plan):
        plan = json.loads(encode(plan))
        attempts = {
            a["id"]: a
            for a in self.learning.store.list_attempts()
            if not a.get("assignment_id")
        }
        reading = self.learning.store.reading()
        start = today()
        deadline = date.fromisoformat(plan["exam_date"])
        days = [
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "budget": plan["overrides"].get(
                    (start + timedelta(days=i)).isoformat(), plan["minutes"]
                ),
                "used": 0,
                "tasks": [],
            }
            for i in range(max(0, (deadline - start).days))
        ]
        done, blocked, overflow, pending = [], [], [], []
        for task in plan["tasks"]:
            task["status"] = "pending"
            a = attempts.get(task.get("attempt_id"))
            if task["kind"] == "reading":
                if (
                    plan["adopted_at"]
                    and reading.get(task["chapter_id"], 0) >= plan["adopted_at"]
                ):
                    task["status"] = "completed"
                    task["completed_at"] = reading[task["chapter_id"]]
                elif not LearningPathService(self.learning).material_path(
                    LearningPathService(self.learning).chapter(task["chapter_id"])
                ):
                    task.update(status="blocked", detail="章节资料暂不可用")
            else:
                # Fresh evidence from outside this plan can also satisfy its goal.
                newer = [
                    item
                    for item in attempts.values()
                    if list(family(self.learning._exercise(item))) == task["family"]
                    and independent(item)
                    and item["submissions"][0]["created_at"]
                    >= (plan["adopted_at"] or plan["created_at"])
                ]
                if (a and independent(a)) or newer:
                    task.update(
                        status="completed",
                        evidence_id=(
                            a["id"] if a and independent(a) else newer[-1]["id"]
                        ),
                    )
                    proof = attempts[task["evidence_id"]]
                    task["completed_at"] = proof["submissions"][0]["created_at"]
                elif a and a["status"] == "passed":
                    rec = self._scoped(plan)._recommendation(a, list(attempts.values()))
                    task.update(
                        status="needs_retest" if rec else "blocked",
                        detail="订正或辅助通过，仍需新题独立验证"
                        if rec
                        else "辅助通过，但暂无可用新题，不能判断独立掌握",
                    )
                elif a and a["submissions"]:
                    task.update(
                        status="needs_correction",
                        detail="本题尚未通过，继续订正后再独立复测",
                    )
                elif not a and task["exercise_id"] not in self.learning.catalog:
                    task.update(status="blocked", detail="题目已不可用，请重新生成计划")
            if task["status"] == "completed":
                done.append(task)
            elif task["status"] == "blocked":
                blocked.append(task)
            else:
                pending.append(task)
        # Completed work still consumes today's estimated budget. Do not keep
        # pulling tomorrow's work forward after every successful submission.
        if days:
            days[0]["used"] = sum(
                t["minutes"]
                for t in done
                if datetime.fromtimestamp(
                    t["completed_at"], ZoneInfo("Asia/Shanghai")
                ).date()
                == start
            )
            days[0]["completed_minutes"] = days[0]["used"]
        for task in pending:
            placed = False
            for day in days:
                if day["used"] + task["minutes"] <= day["budget"]:
                    day["tasks"].append(task)
                    day["used"] += task["minutes"]
                    placed = True
                    break
            if not placed:
                overflow.append(task)
        proposal = plan.get("budget_proposal")
        if proposal and proposal["date"] != today().isoformat():
            plan["budget_proposal"] = None
        plan.update(
            days=days,
            completed=done,
            blocked=blocked,
            overflow=overflow,
            expired=deadline <= today(),
            summary=f"已完成 {len(done)}/{len(plan['tasks'])} 项；未完成任务按剩余时间重新安排。阅读完成不代表掌握。",
        )
        return plan

    def start(self, plan_id, task_id):
        with closing(self.store.connect()) as db:
            plan = self._load(db, plan_id)
        if (
            plan["status"] != "active"
            or date.fromisoformat(plan["exam_date"]) <= today()
        ):
            raise LearningConflict("请先采用有效计划")
        task = next(
            (
                t
                for t in plan["tasks"]
                if t["id"] == task_id and t["kind"] == "practice"
            ),
            None,
        )
        if task is None:
            raise LookupError("训练任务不存在")
        original = task.get("attempt_id")
        a = self.learning.store.get(original) if original else None
        if a and (a["status"] != "passed" or independent(a)):
            return self.learning.get(a["id"])
        executor = self._scoped(plan)
        diagnostic_key = f"exam:{plan_id}:{task_id}:{original or 'first'}"
        existing = self.learning.store.list_attempts()
        previous = next(
            (item for item in existing if item.get("diagnostic_key") == diagnostic_key),
            None,
        )
        if previous:
            attempt = executor.get(previous["id"])
        else:
            exercise_id = task["exercise_id"]
            if not a:
                seen = {item["exercise_id"] for item in existing}
                available = [
                    e
                    for e in executor.catalog.values()
                    if list(family(e)) == task["family"] and e["id"] not in seen
                ]
                available.sort(
                    key=lambda e: (
                        e["id"] != exercise_id,
                        e["difficulty"] != "基础",
                        e["id"],
                    )
                )
                if not available:
                    raise LearningConflict(
                        "该任务暂无未练习的新题，请检查学习记录或重新生成计划"
                    )
                exercise_id = available[0]["id"]
            attempt = executor.start(
                exercise_id if not a else None,
                parent_id=a["id"] if a else None,
                diagnostic_key=diagnostic_key,
            )
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            plan = self._load(db, plan_id)
            if plan["status"] != "active":
                raise LearningConflict("计划已被替换；已创建的练习保留在学习记录中")
            target = next(t for t in plan["tasks"] if t["id"] == task_id)
            if target.get("attempt_id") not in {original, attempt["id"]}:
                raise LearningConflict("任务已更新，请刷新")
            target["attempt_id"] = attempt["id"]
            db.execute(
                "UPDATE assistant_exam_plans SET data=? WHERE id=? AND owner=?",
                (encode(plan), plan_id, self.owner),
            )
        return attempt
