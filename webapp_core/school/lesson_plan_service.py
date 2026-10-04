from __future__ import annotations

import json
import time
from contextlib import closing
from uuid import uuid4

from webapp_core.learning.learning_courses import course_chapters
from webapp_core.learning.learning_path import LearningPathService
from webapp_core.learning.learning_service import LearningConflict


class LessonPlanService:
    def __init__(self, tasks, learning):
        self.tasks = tasks
        self.learning = learning

    def overview(self, class_id):
        classroom = self.tasks.school.class_access(class_id, self.tasks.user, teacher=True)
        with closing(self.tasks.store._connect()) as c:
            plans = [json.loads(row[0]) for row in c.execute(
                "SELECT data FROM lesson_plans WHERE class_id=?", (class_id,)
            )]
        return dict(
            chapters=course_chapters(classroom["course_id"]),
            plans=sorted(plans, key=lambda p: p["updated_at"], reverse=True),
        )

    def generate(self, class_id, data):
        classroom = self.tasks.school.class_access(class_id, self.tasks.user, teacher=True)
        chapters = {c["id"]: c for c in course_chapters(classroom["course_id"])}
        chapter_id, minutes = data.get("chapter_id"), data.get("minutes")
        if not isinstance(chapter_id, str) or chapter_id not in chapters:
            raise ValueError("请选择本班课程的章节")
        if type(minutes) is not int or not 15 <= minutes <= 240:
            raise ValueError("课时总时长须为15至240分钟的整数")
        report = self.tasks.report(class_id)
        chapter = chapters[chapter_id]
        counts = next((c for c in report["chapters"] if c["id"] == chapter_id), None)
        errors = [g for g in report["common_errors"] if g["chapter_id"] == chapter_id][:5]
        exercises = sorted([
            e for e in self.tasks.school.catalog("training").values()
            if e["subject_id"] == classroom["course_id"] and e["chapter_id"] == chapter_id
        ], key=lambda e: e["id"])
        assigned = {q["exercise_id"] for q in report["questions"]}
        recommended = {e["id"] for g in errors for e in g["exercises"]}
        fresh = [e for e in exercises if e["id"] not in assigned]
        homework = [e["id"] for e in fresh if not errors or e["id"] in recommended][:2]
        classroom_ids = [e["id"] for e in exercises if e["id"] not in homework][:1]
        gaps = []
        material = bool(LearningPathService(self.learning).material_path(
            {**chapter, "subject_id": classroom["course_id"]}
        ))
        if not material:
            gaps.append("本章资料缺失，请补充或自行准备讲义。")
        if not counts or not counts["submitted"]:
            gaps.append("本章尚无本班已提交作答，不能判断班级薄弱点；建议先做课堂诊断。")
        if not classroom_ids:
            gaps.append("未默认选入课堂练习题，请调整选题或自行准备。")
        if not homework:
            gaps.append("未找到适合默认推荐的未布置补练题，请核对选题或补充并审核后再布置。")
        first = max(5, minutes // 4)
        last = max(5, minutes // 5)
        focus = "\n".join(g["teaching_advice"] for g in errors) or (
            f"围绕“{chapter['title']}”检查前置知识，让学生说明解题依据；根据课堂反馈确定讲评重点。"
        )
        return dict(
            id=None, revision=0, class_id=class_id, chapter_id=chapter_id,
            chapter_title=chapter["title"], title=f"{chapter['title']}教学安排",
            minutes=minutes, focus=focus, teacher_notes="",
            stages=[
                dict(title="回顾与诊断", minutes=first, content="核对前置知识，选取作答证据，请学生解释判断依据。"),
                dict(title="讲评与课堂练习", minutes=minutes-first-last, content=focus),
                dict(title="核对与课后安排", minutes=last, content="核对关键步骤，说明课后补练要求；独立完成后再借助反馈订正。"),
            ],
            classroom_ids=classroom_ids, homework_ids=homework,
            exercises=[dict(id=e["id"], title=e["title"], content_version=e["content_version"]) for e in exercises],
            gaps=gaps, material_available=material,
            baseline=dict(generated_at=report["generated_at"], member_count=report["member_count"],
                          counts=counts, errors=errors),
        )

    def save(self, class_id, data, plan_id=None):
        # Rebuild evidence on the server; client edits may change teaching content, not provenance.
        plan = self.generate(class_id, data)
        for key, limit in (("title", 120), ("focus", 6000), ("teacher_notes", 6000)):
            value = data.get(key, "")
            if not isinstance(value, str) or len(value) > limit or (key != "teacher_notes" and not value.strip()):
                raise ValueError("请填写备课标题和讲评重点，内容不可过长")
            plan[key] = value.strip()
        stages = data.get("stages")
        if not isinstance(stages, list) or not 1 <= len(stages) <= 12:
            raise ValueError("请设置1至12个教学环节")
        clean_stages = []
        for stage in stages:
            if not isinstance(stage, dict):
                raise ValueError("教学环节格式不正确")
            title, content, minutes = stage.get("title"), stage.get("content"), stage.get("minutes")
            if (not isinstance(title, str) or not title.strip() or len(title) > 120
                    or not isinstance(content, str) or not content.strip() or len(content) > 6000
                    or type(minutes) is not int or minutes < 1):
                raise ValueError("请填写环节标题、内容及正整数分钟数")
            clean_stages.append(dict(title=title.strip(), content=content.strip(), minutes=minutes))
        if sum(s["minutes"] for s in clean_stages) != plan["minutes"]:
            raise ValueError("各环节分钟数之和必须等于课时总时长")
        plan["stages"] = clean_stages
        allowed = {e["id"] for e in plan["exercises"]}
        for key in ("classroom_ids", "homework_ids"):
            ids = data.get(key, [])
            if (not isinstance(ids, list) or len(ids) > 20
                    or any(not isinstance(i, str) or i not in allowed for i in ids)
                    or len(ids) != len(set(ids))):
                raise ValueError("只能选择本章当前已发布题目，每组选题最多20道")
            plan[key] = ids
        if set(plan["classroom_ids"]) & set(plan["homework_ids"]):
            raise ValueError("课堂练习与课后补练请选择不同题目")
        if data.get("confirmed") is not True:
            raise ValueError("请核对备课安排后确认保存")
        with closing(self.tasks.store._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            old = self._get(c, plan_id) if plan_id else None
            if old and old["class_id"] != class_id:
                raise PermissionError("备课安排不属于本班")
            if old and (type(data.get("revision")) is not int or data["revision"] != old["revision"]):
                raise LearningConflict("备课安排已更新，请重新打开后修改")
            if old and old["chapter_id"] != plan["chapter_id"]:
                raise ValueError("已有备课安排不能更换章节，请另建安排")
            now = time.time()
            plan.update(id=plan_id or uuid4().hex, revision=old["revision"]+1 if old else 1,
                        created_at=old["created_at"] if old else now, updated_at=now)
            if old:
                plan["baseline"] = old["baseline"]
            c.execute(
                "INSERT INTO lesson_plans VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (plan["id"], class_id, json.dumps(plan, ensure_ascii=False)),
            )
        return plan

    @staticmethod
    def _get(c, plan_id):
        row = c.execute("SELECT data FROM lesson_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise LookupError("备课安排不存在")
        return json.loads(row[0])

    def effects(self, class_id, plan_id):
        self.tasks.school.class_access(class_id, self.tasks.user, teacher=True)
        with closing(self.tasks.store._connect()) as c:
            plan = self._get(c, plan_id)
        if plan["class_id"] != class_id:
            raise PermissionError("备课安排不属于本班")
        linked = [t for t in self.tasks.list(class_id) if t.get("lesson_plan_id") == plan_id]
        report = self.tasks.report(class_id)
        results = []
        for task in linked:
            questions = [q for q in report["questions"] if q["task_id"] == task["id"]]
            counts = {key: sum(q[key] for q in questions) for key in report["totals"]}
            results.append(dict(id=task["id"], title=task["title"], status=task["status"],
                                plan_revision=task["lesson_plan_revision"], counts=counts))
        return dict(tasks=results, generated_at=report["generated_at"])
