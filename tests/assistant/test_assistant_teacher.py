import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.school import test_school_tasks as fixtures


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
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(
            content="", tool_calls=[dict(name=name, args=args)])))
        model.bind_tools = Mock(return_value=model)
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", model):
            result = (client or self.teacher).post("/api/assistant/messages", json=dict(
                content="分析本班加密机制的作答情况", request_id=key))
        self.assertEqual(result.status_code, 200, result.json)
        return result.json, model

    def test_report_uses_authorized_evidence_and_retry_is_idempotent(self):
        task, classroom, _ = self.setup_task()
        self.submit(self.start_task(task), False)
        args = dict(class_id=classroom["id"], chapter_id="cybersec_lab_02")
        result, model = self.chat("class_learning_report", args)
        self.assertIn("首错提交 1 次", result["messages"][-1]["content"])
        self.assertEqual(result["teacher_results"][0]["kind"], "report")
        names = {t["function"]["name"] for t in model.bind_tools.call_args.args[0]}
        self.assertEqual(names, {"class_learning_report", "draft_teaching_plan", "prepare_teaching_homework"})
        context = model.ainvoke.call_args.args[0][0][1]
        self.assertIn(classroom["title"], context)
        self.assertIn("加密机制", context)
        repeated, second = self.chat("class_learning_report", args)
        self.assertEqual(len(repeated["teacher_results"]), 1)
        second.ainvoke.assert_not_awaited()
        self.assertEqual(self.other_teacher.get("/api/assistant").json["teacher_results"], [])

    def test_draft_requires_manual_save_and_homework_does_not_create_task(self):
        task, classroom, _ = self.setup_task()
        self.submit(self.start_task(task), False)
        self.publish("training:dh_02")
        args = dict(class_id=classroom["id"], chapter_id="cybersec_lab_02", minutes=45)
        result, _ = self.chat("draft_teaching_plan", args)
        draft = result["teacher_results"][0]["plan"]
        url = f"/api/school/classes/{classroom['id']}/lesson-plans"
        self.assertEqual(self.teacher.get(url).json["plans"], [])
        self.assertIsNone(draft["id"])
        self.assertEqual(sum(s["minutes"] for s in draft["stages"]), 45)
        saved = self.teacher.post(url, json={**draft, "confirmed": True}).json
        result, _ = self.chat("prepare_teaching_homework", dict(class_id=classroom["id"], plan_id=saved["id"]), key="teacher-request-002")
        self.assertEqual(result["teacher_results"][-1]["kind"], "homework")
        self.assertIn("尚未创建作业", result["messages"][-1]["content"])
        self.assertEqual(len(self.teacher.get(f"/api/school/classes/{classroom['id']}/tasks").json["tasks"]), 1)
        self.assertEqual(self.teacher.delete("/api/assistant/memory", json={}).status_code, 200)
        self.assertEqual(self.teacher.get("/api/assistant").json["teacher_results"], [])

    def test_student_foreign_teacher_and_unsupported_actions_are_rejected(self):
        _, classroom, _ = self.setup_task()
        args = dict(class_id=classroom["id"], chapter_id="cybersec_lab_02", minutes=45)
        for index, client in enumerate((self.student, self.other_teacher, self.admin)):
            result, _ = self.chat("draft_teaching_plan", args, client=client, key=f"teacher-forbidden-{index:03}")
            self.assertEqual(result["teacher_results"], [])
        result, _ = self.chat("publish_task", args)
        self.assertEqual(result["teacher_results"], [])
        self.assertIn("不支持", result["messages"][-1]["content"])

    def test_missing_or_invalid_scope_produces_no_proposal(self):
        _, classroom, _ = self.setup_task()
        for index, changes in enumerate((dict(minutes=None), dict(chapter_id="operating_systems_08"), dict(class_id=None))):
            args = {"class_id": classroom["id"], "chapter_id": "cybersec_lab_02", "minutes": 45, **changes}
            result, _ = self.chat("draft_teaching_plan", args, key=f"teacher-invalid-{index:03}")
            self.assertEqual(result["teacher_results"], [])
        result, _ = self.chat("class_learning_report", dict(class_id=classroom["id"], chapter_id="cybersec_lab_01"), key="teacher-no-data-001")
        self.assertIn("尚无已发布任务", result["messages"][-1]["content"])
        self.assertEqual(result["teacher_results"], [])


if __name__ == "__main__":
    unittest.main()
