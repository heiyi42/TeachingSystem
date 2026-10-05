import unittest
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.school import test_school_tasks as fixtures
from tests.school.test_lesson_plans import lesson_model_reply
from webapp_core.school.lesson_plan_service import LESSON_PLAN_PROMPT


class TeacherAssistantTests(unittest.TestCase):
    setUpClass = fixtures.SchoolTaskTests.__dict__["setUpClass"]
    setUp = fixtures.SchoolTaskTests.setUp
    credentials = staticmethod(fixtures.SchoolTaskTests.credentials)
    account = fixtures.SchoolTaskTests.account
    publish = fixtures.SchoolTaskTests.publish
    classroom = fixtures.SchoolTaskTests.classroom
    setup_task = fixtures.SchoolTaskTests.setup_task
    start_task = fixtures.SchoolTaskTests.start_task
    submit = fixtures.SchoolTaskTests.submit

    def chat(self, name, args, client=None, key="teacher-request-001"):
        def reply(messages):
            if messages[0][1] == LESSON_PLAN_PROMPT:
                return lesson_model_reply(messages)
            return SimpleNamespace(content="", tool_calls=[dict(name=name, args=args)])

        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=reply))
        model.bind_tools = Mock(return_value=model)
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", model):
            result = (client or self.teacher).post(
                "/api/assistant/messages",
                json=dict(content="分析本班加密机制的作答情况", request_id=key),
            )
        self.assertEqual(result.status_code, 200, result.json)
        return result.json, model

    def test_roles_use_distinct_personas_context_and_tools(self):
        from webapp_core.assistant.assistant_prompts import ASSISTANT_PERSONAS

        for role, client in (
            ("teacher", self.teacher),
            ("student", self.student),
            ("admin", self.admin),
        ):
            with self.subTest(role=role):
                model = SimpleNamespace(
                    ainvoke=AsyncMock(
                        return_value=SimpleNamespace(
                            content="已了解当前目标", tool_calls=[dict(name="reply_to_teacher", args={"content": "已了解当前目标"})] if role == "teacher" else []
                        )
                    )
                )
                model.bind_tools = Mock(return_value=model)
                with patch("webapp_core.chat.auto_runtime.auto_router_llm", model):
                    response = client.post(
                        "/api/assistant/messages",
                        json=dict(
                            content="帮我安排下一步", request_id=f"role-check-{role}"
                        ),
                    )
                self.assertEqual(response.status_code, 200, response.json)
                prompt = model.ainvoke.call_args.args[0][0][1]
                self.assertTrue(prompt.startswith(ASSISTANT_PERSONAS[role]))
                context, _ = json.JSONDecoder().raw_decode(
                    prompt.split("授权上下文：", 1)[1]
                )
                self.assertEqual(context["user_role"], role)
                if role == "student":
                    self.assertIn("courses", context["exam_plans"])
                    names = {
                        tool["function"]["name"]
                        for tool in model.bind_tools.call_args.args[0]
                    }
                    self.assertEqual(names, {"draft_exam_plan", "propose_today_budget"})
                else:
                    self.assertEqual(context["exam_plans"], {})
                    self.assertEqual(context["recent_practice"], [])
                    if role == "admin":
                        model.bind_tools.assert_not_called()
                    else:
                        names = {
                            tool["function"]["name"]
                            for tool in model.bind_tools.call_args.args[0]
                        }
                        self.assertIn("class_learning_report", names)
                        self.assertNotIn("draft_exam_plan", names)

    def test_teacher_clarification_uses_required_tool_without_creating_draft(self):
        result, model = self.chat("reply_to_teacher", {"content": "请告诉我是哪个班级、哪个章节和多少分钟。"})
        self.assertIn("哪个班级", result["messages"][-1]["content"])
        self.assertFalse(result["teacher_results"])
        self.assertEqual(model.bind_tools.call_args.kwargs["tool_choice"], "required")

    def test_teacher_text_only_plan_is_not_reported_as_completed(self):
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="已拟好45分钟备课草案：导入、讲授、总结。", tool_calls=[])))
        model.bind_tools = Mock(return_value=model)
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", model):
            response = self.teacher.post("/api/assistant/messages", json=dict(content="帮我生成备课草案", request_id="teacher-text-only-plan"))
        self.assertIn("未生成或保存", response.json["messages"][-1]["content"])
        self.assertNotIn("已拟好", response.json["messages"][-1]["content"])
        self.assertFalse(response.json["teacher_results"])

    def test_admin_cannot_execute_student_plan_tools(self):
        model = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(
                    content="", tool_calls=[dict(name="draft_exam_plan", args={})]
                )
            )
        )
        model.bind_tools = Mock(return_value=model)
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", model):
            response = self.admin.post(
                "/api/assistant/messages",
                json=dict(content="创建复习计划", request_id="admin-plan-check"),
            )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn("当前身份不支持", response.json["messages"][-1]["content"])
        model.bind_tools.assert_not_called()

    def test_report_uses_authorized_evidence_and_retry_is_idempotent(self):
        from webapp_core.assistant.assistant_prompts import STS_ANSWER_GUIDANCE

        task, classroom, _ = self.setup_task()
        self.submit(self.start_task(task), False)
        args = dict(class_id=classroom["id"], chapter_id="cybersec_lab_02")
        result, model = self.chat("class_learning_report", args)
        self.assertIn("首错提交 1 次", result["messages"][-1]["content"])
        self.assertEqual(result["teacher_results"][0]["kind"], "report")
        names = {t["function"]["name"] for t in model.bind_tools.call_args.args[0]}
        self.assertEqual(
            names,
            {
                "class_learning_report",
                "draft_teaching_plan",
                "prepare_teaching_homework",
                "reply_to_teacher",
            },
        )
        context = model.ainvoke.call_args.args[0][0][1]
        self.assertIn(STS_ANSWER_GUIDANCE, context)
        self.assertIn(classroom["title"], context)
        self.assertIn("加密机制", context)
        repeated, second = self.chat("class_learning_report", args)
        self.assertEqual(len(repeated["teacher_results"]), 1)
        second.ainvoke.assert_not_awaited()
        self.assertEqual(
            self.other_teacher.get("/api/assistant").json["teacher_results"], []
        )

    def test_unknown_class_returns_available_choices_without_starting_task(self):
        _, classroom, _ = self.setup_task()
        result, model = self.chat(
            "draft_teaching_plan",
            dict(
                class_id="nonexistent-class", chapter_id="cybersec_lab_02", minutes=45
            ),
        )
        self.assertEqual(result["teacher_results"], [])
        reply = result["messages"][-1]["content"]
        self.assertIn("找不到这个班级", reply)
        self.assertIn(classroom["title"], reply)
        self.assertIn("网络安全", reply)
        prompt = model.ainvoke.call_args.args[0][0][1]
        self.assertIn("不要问老师“哪门课”", prompt)
        context, _ = json.JSONDecoder().raw_decode(prompt.split("授权上下文：", 1)[1])
        self.assertEqual(context["teaching_classes"][0]["course_name"], "网络安全实验")

    def test_report_task_visible_during_analysis(self):
        from webapp_core.school.task_service import TaskService

        _, classroom, _ = self.setup_task()
        observer = self.app.test_client()
        self.assertEqual(
            observer.post(
                "/api/identity/login", json=self.credentials("teacher")
            ).status_code,
            200,
        )
        original = TaskService.report

        def report(service, *args, **kwargs):
            task = observer.get("/api/assistant").json["teacher_results"][0]
            self.assertEqual(task["status"], "running")
            self.assertEqual(task["kind"], "report")
            return original(service, *args, **kwargs)

        with patch.object(TaskService, "report", report):
            result, _ = self.chat(
                "class_learning_report", dict(class_id=classroom["id"])
            )
        self.assertEqual(result["teacher_results"][0]["status"], "completed")
        self.assertIn("学情分析已完成", result["messages"][-1]["content"])

    def test_failed_report_marks_task_failed(self):
        _, classroom, _ = self.setup_task()
        with patch(
            "webapp_core.school.task_service.TaskService.report",
            side_effect=ValueError("学情读取失败"),
        ):
            result, _ = self.chat(
                "class_learning_report", dict(class_id=classroom["id"])
            )
        self.assertEqual(result["teacher_results"][0]["status"], "failed")
        self.assertEqual(result["teacher_results"][0]["error"], "学情读取失败")

    def test_lesson_task_visible_before_generation_and_completed_afterwards(self):
        from webapp_core.school.lesson_plan_service import LessonPlanService

        _, classroom, _ = self.setup_task()
        args = dict(class_id=classroom["id"], chapter_id="cybersec_lab_02", minutes=45)
        original = LessonPlanService.generate
        observer = self.app.test_client()
        self.assertEqual(
            observer.post(
                "/api/identity/login", json=self.credentials("teacher")
            ).status_code,
            200,
        )

        def generate(service, *values, **kwargs):
            state = observer.get("/api/assistant").json
            task = state["teacher_results"][0]
            self.assertEqual(task["status"], "running")
            self.assertNotIn("plan", task)
            self.assertEqual(task["class_id"], classroom["id"])
            return original(service, *values, **kwargs)

        with patch.object(LessonPlanService, "generate", generate):
            result, _ = self.chat("draft_teaching_plan", args)
        self.assertEqual(result["teacher_results"][0]["status"], "completed")
        self.assertIn("备课任务已完成", result["messages"][-1]["content"])
        overview = self.teacher.get(f"/api/school/classes/{classroom['id']}/lesson-plans")
        self.assertEqual(overview.status_code, 200)
        saved = next(p for p in overview.json["plans"] if p["id"] == result["teacher_results"][0]["plan"]["id"])
        self.assertEqual(saved["status"], "draft")
        self.assertEqual(saved["source"], "assistant")

    def test_failed_generation_marks_task_failed(self):
        _, classroom, _ = self.setup_task()
        args = dict(class_id=classroom["id"], chapter_id="cybersec_lab_02", minutes=45)
        with patch(
            "webapp_core.school.lesson_plan_service.LessonPlanService.generate",
            side_effect=ValueError("模型生成失败"),
        ):
            result, _ = self.chat("draft_teaching_plan", args)
        task = result["teacher_results"][0]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"], "模型生成失败")
        self.assertNotIn("plan", task)

    def test_missing_chapter_does_not_start_generation(self):
        _, classroom, _ = self.setup_task()
        with patch(
            "webapp_core.school.lesson_plan_service.LessonPlanService.generate"
        ) as generate:
            result, model = self.chat(
                "draft_teaching_plan", dict(class_id=classroom["id"], minutes=45)
            )
        generate.assert_not_called()
        self.assertEqual(result["teacher_results"], [])
        self.assertIn("章节", result["messages"][-1]["content"])
        self.assertIn("不得默认第一个班级", model.ainvoke.call_args.args[0][0][1])

    def test_draft_requires_manual_save_and_homework_does_not_create_task(self):
        task, classroom, _ = self.setup_task()
        self.submit(self.start_task(task), False)
        self.publish("training:dh_02")
        args = dict(class_id=classroom["id"], chapter_id="cybersec_lab_02", minutes=45)
        result, _ = self.chat("draft_teaching_plan", args)
        draft = result["teacher_results"][0]["plan"]
        url = f"/api/school/classes/{classroom['id']}/lesson-plans"
        self.assertEqual(self.teacher.get(url).json["plans"][0]["id"], draft["id"])
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["source"], "assistant")
        repeated, repeated_model = self.chat("draft_teaching_plan", args)
        repeated_model.ainvoke.assert_not_awaited()
        self.assertEqual(len(self.teacher.get(url).json["plans"]), 1)
        self.assertEqual(repeated["teacher_results"][0]["plan"]["id"], draft["id"])
        self.assertEqual(sum(s["minutes"] for s in draft["stages"]), 45)
        saved = self.teacher.put(
            url + "/" + draft["id"], json={**draft, "confirmed": True}
        ).json
        result, _ = self.chat(
            "prepare_teaching_homework",
            dict(class_id=classroom["id"], plan_id=saved["id"]),
            key="teacher-request-002",
        )
        self.assertEqual(result["teacher_results"][-1]["kind"], "homework")
        self.assertIn("尚未创建作业", result["messages"][-1]["content"])
        self.assertEqual(
            len(
                self.teacher.get(f"/api/school/classes/{classroom['id']}/tasks").json[
                    "tasks"
                ]
            ),
            1,
        )
        self.assertEqual(
            self.teacher.delete("/api/assistant/memory", json={}).status_code, 200
        )
        self.assertEqual(self.teacher.get("/api/assistant").json["teacher_results"], [])

    def test_student_foreign_teacher_and_unsupported_actions_are_rejected(self):
        _, classroom, _ = self.setup_task()
        args = dict(class_id=classroom["id"], chapter_id="cybersec_lab_02", minutes=45)
        for index, client in enumerate((self.student, self.other_teacher, self.admin)):
            result, _ = self.chat(
                "draft_teaching_plan",
                args,
                client=client,
                key=f"teacher-forbidden-{index:03}",
            )
            self.assertEqual(result["teacher_results"], [])
        result, _ = self.chat("publish_task", args)
        self.assertEqual(result["teacher_results"], [])
        self.assertIn("不支持", result["messages"][-1]["content"])

    def test_missing_or_invalid_scope_produces_no_proposal(self):
        _, classroom, _ = self.setup_task()
        for index, changes in enumerate(
            (
                dict(minutes=None),
                dict(chapter_id="operating_systems_08"),
                dict(class_id=None),
            )
        ):
            args = {
                "class_id": classroom["id"],
                "chapter_id": "cybersec_lab_02",
                "minutes": 45,
                **changes,
            }
            result, _ = self.chat(
                "draft_teaching_plan", args, key=f"teacher-invalid-{index:03}"
            )
            self.assertEqual(result["teacher_results"], [])
        result, _ = self.chat(
            "class_learning_report",
            dict(class_id=classroom["id"], chapter_id="cybersec_lab_01"),
            key="teacher-no-data-001",
        )
        self.assertIn("尚无已发布任务", result["messages"][-1]["content"])
        self.assertEqual(result["teacher_results"][0]["status"], "completed")
        self.assertEqual(result["teacher_results"][0]["kind"], "report")


if __name__ == "__main__":
    unittest.main()
