import time

from webapp_core.learning.learning_courses import course_chapters
from webapp_core.school.lesson_plan_service import LessonPlanService
from webapp_core.school.task_service import TaskService


class TeacherAssistant:
    def __init__(self, learning, school, user):
        self.user = user
        self.school = school
        self.tasks = TaskService(school, learning.store, learning.solver, user)
        self.plans = LessonPlanService(self.tasks, learning)

    def context(self):
        if self.user["role"] != "teacher":
            return []
        return [dict(
            **{key: c[key] for key in ("id", "title", "course_id")},
            chapters=course_chapters(c["course_id"]),
            plans=[{key: p[key] for key in ("id", "title", "chapter_id", "revision")}
                   for p in self.plans.overview(c["id"])["plans"]],
        ) for c in self.school.classes(self.user)]

    @staticmethod
    def tools():
        common = {"class_id": {"type": "string", "description": "授权目录中的班级ID"},
                  "chapter_id": {"type": "string", "description": "本班课程目录中的章节ID"}}
        definitions = [
            ("class_learning_report", "查询本班学情与常见首错依据。省略章节时查询全班。",
             common, ["class_id"]),
            ("draft_teaching_plan", "生成未保存的备课草案。班级、章节和分钟数必须明确；保存需教师在界面确认。",
             {**common, "minutes": {"type": "integer", "minimum": 15, "maximum": 240}},
             ["class_id", "chapter_id", "minutes"]),
            ("prepare_teaching_homework", "为已保存备课准备补练选题供教师核对，不创建或发布作业。",
             {"class_id": common["class_id"], "plan_id": {"type": "string", "description": "授权目录中的已保存备课ID"}},
             ["class_id", "plan_id"]),
        ]
        return [{"type": "function", "function": dict(name=name, description=description,
                 parameters=dict(type="object", properties=properties, required=required, additionalProperties=False))}
                for name, description, properties, required in definitions]

    def execute(self, name, args):
        if self.user["role"] != "teacher":
            raise PermissionError("教学工具仅供任课教师使用")
        class_id = args.get("class_id")
        if not isinstance(class_id, str):
            raise ValueError("请明确选择授课班级")
        classroom = self.school.class_access(class_id, self.user, teacher=True)
        chapter_id = args.get("chapter_id")
        if chapter_id is not None and (
            not isinstance(chapter_id, str)
            or chapter_id not in {c["id"] for c in course_chapters(classroom["course_id"])}
        ):
            raise ValueError("请选择本班课程的章节")
        result = dict(class_id=class_id, class_title=classroom["title"], generated_at=time.time())
        if name == "class_learning_report":
            report = self.tasks.report(class_id)
            if chapter_id and chapter_id not in {c["id"] for c in report["chapters"]}:
                return "本班所选章节尚无已发布任务，不能据此判断薄弱点。可先生成备课草案安排课堂诊断。", None
            if chapter_id:
                report = self.tasks.report(class_id, chapter_id)
            counts = report["totals"]
            scope = report["chapters"][0]["title"] if chapter_id else "全部已布置章节"
            text = (f"{classroom['title']} · {scope}：应作答 {counts['expected_answers']} 题次，"
                    f"已提交 {counts['submitted']}，独立通过 {counts['independent']}，"
                    f"辅助通过 {counts['assisted']}，订正通过 {counts['corrected']}，"
                    f"重复作答通过 {counts['repeated']}，辅助信息缺失 {counts['unclassified_pass']}，"
                    f"已提交未通过 {counts['unpassed']}，未提交 {counts['unsubmitted']}。\n\n")
            for group in report["common_errors"][:5]:
                text += (f"- {group['chapter_title']} · {group['label']}：涉及 {group['student_count']} 人，"
                         f"首错提交 {group['occurrences']} 次，对应作答最新提交仍出现 {group['current_student_count']} 人。"
                         f"{group['teaching_advice']}\n")
            if not report["common_errors"]:
                text += "暂无首错记录，不能据此断定全班已掌握。\n"
            text += "\n以上仅统计本班任务。点击下方查看学情，核对图表、统计口径和完整作答证据。"
            result.update(kind="report", chapter_id=chapter_id or "", title=f"{scope}学情依据")
            return text, result
        if name == "draft_teaching_plan":
            plan = self.plans.generate(class_id, args)
            result.update(kind="lesson", plan=plan, title=plan["title"])
            return (f"已根据本班任务记录生成“{plan['chapter_title']}”{plan['minutes']}分钟备课草案。"
                    "尚未保存或发布作业。请点击下方编辑草案，核对依据、时间分配和选题后确认保存。", result)
        if name == "prepare_teaching_homework":
            plan_id = args.get("plan_id")
            plan = next((p for p in self.plans.overview(class_id)["plans"] if p["id"] == plan_id), None)
            if not plan:
                raise LookupError("请先选择本班已保存的备课安排")
            if not plan["homework_ids"]:
                raise ValueError("该备课安排尚未选择课后补练，请在备课安排中选题并保存")
            catalog = self.school.catalog("training")
            if any(i not in catalog for i in plan["homework_ids"]):
                raise ValueError("补练题已撤下，请先编辑备课选题")
            result.update(kind="homework", plan=plan, title=f"{plan['title']}·补练建议")
            titles = [catalog[i]["title"] for i in plan["homework_ids"]]
            return ("补练选题来自已保存备课：" + "、".join(titles) + "。尚未创建作业。"
                    "请点击下方核对补练，检查题目和截止时间，保存草稿后再单独发布。", result)
        raise ValueError("不支持的教学操作")
