import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.learning import test_learning_path as fixtures
from webapp_core.learning.learning_path import LearningPathService, DAY
from webapp_core.learning.learning_service import LearningService, LearningConflict
from webapp_core.learning.learning_store import LearningStore


class PersonalGuidanceTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.LearningPathTests.setUp
    rows = fixtures.LearningPathTests.rows
    mistake = fixtures.LearningPathTests.mistake

    def point(self):
        return next(
            p for p in self.study.dashboard()["points"] if p["title"] == "DH 计算"
        )

    def model(self, content="请先核对公开值计算中底数、指数和模数，再逐步取模。"):
        return SimpleNamespace(
            model_name="test-model",
            ainvoke=AsyncMock(return_value=SimpleNamespace(content=content)),
        )

    def test_cold_start_material_then_unseen_basic_practice(self):
        self.assertEqual(self.point()["actions"][0]["kind"], "material")
        self.study.mark_reading("cybersec_lab_02", True)
        self.assertEqual(self.point()["actions"][0]["kind"], "practice")
        self.assertEqual(self.point()["actions"][0]["exercise_id"], "dh_01")
        self.service.catalog = {}
        self.assertNotIn("practice", [a["kind"] for a in self.point()["actions"]])

    def test_correction_then_wait_then_due_review_then_continue(self):
        with patch("time.time", return_value=1000):
            origin = self.mistake()
            self.assertEqual(self.point()["actions"][0]["attempt_id"], origin["id"])
            self.service.submit(origin["id"], self.rows(origin))
            self.assertEqual(self.point()["actions"][0]["kind"], "material")
            self.assertNotIn("practice", [a["kind"] for a in self.point()["actions"]])
        with patch("time.time", return_value=1000 + DAY):
            self.assertEqual(self.point()["actions"][0]["kind"], "review")
            review = self.service.start(None, review_id=origin["id"])
            self.assertEqual(self.point()["actions"][0]["kind"], "continue")
            self.assertEqual(self.point()["actions"][0]["attempt_id"], review["id"])

    async def test_explanation_uses_own_evidence_material_and_persists(self):
        origin = self.mistake()
        model = self.model()
        result = await self.study.explain("cybersec_lab:DH 计算", model)
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        self.assertEqual(context["作答证据"][0]["attempt_id"], origin["id"])
        self.assertTrue(context["资料"])
        self.assertIn(origin["id"], result["evidence_ids"])
        self.assertTrue(self.service.get(origin["id"])["tutoring_viewed"])
        restarted = LearningPathService(
            LearningService(
                LearningStore(self.path, owner_id="alice"), self.service.solver
            )
        )
        point = next(
            p for p in restarted.dashboard()["points"] if p["id"] == result["point_id"]
        )
        self.assertEqual(point["guidance"]["content"], result["content"])
        self.assertFalse(point["guidance"]["stale"])
        bob = LearningPathService(
            LearningService(
                LearningStore(self.path, owner_id="bob"), self.service.solver
            )
        )
        self.assertTrue(all(p["guidance"] is None for p in bob.dashboard()["points"]))

    async def test_failed_explanation_does_not_mark_assistance(self):
        origin = self.service.start("dh_01")
        model = self.model()
        model.ainvoke.side_effect = TimeoutError("simulated")
        with self.assertRaises(TimeoutError):
            await self.study.explain("cybersec_lab:DH 计算", model)
        self.assertFalse(self.service.get(origin["id"])["tutoring_viewed"])
        self.assertIsNone(self.point()["guidance"])

    async def test_guidance_changes_future_independence_not_past_results(self):
        origin = self.service.start("dh_01")
        await self.study.explain("cybersec_lab:DH 计算", self.model())
        self.service.submit(origin["id"], self.rows(origin))
        self.assertFalse(self.service.get(origin["id"])["first_unassisted_pass"])
        self.assertTrue(self.point()["guidance"]["stale"])
        fresh = self.service.start("dh_02")
        self.service.submit(fresh["id"], self.rows(fresh))
        await self.study.explain("cybersec_lab:DH 计算", self.model())
        self.assertTrue(self.service.get(fresh["id"])["first_unassisted_pass"])

    async def test_changed_evidence_during_generation_is_rejected(self):
        origin = self.service.start("dh_01")
        model = self.model()

        async def change(_messages):
            self.service.submit(origin["id"], self.rows(origin))
            return SimpleNamespace(content="过期讲解")

        model.ainvoke.side_effect = change
        with self.assertRaises(LearningConflict):
            await self.study.explain("cybersec_lab:DH 计算", model)
        self.assertIsNone(self.point()["guidance"])

    async def test_empty_history_and_unknown_point(self):
        model = self.model()
        await self.study.explain("cybersec_lab:DH 计算", model)
        self.assertEqual(
            json.loads(model.ainvoke.call_args.args[0][1][1])["作答证据"], []
        )
        with self.assertRaises(LookupError):
            await self.study.explain("other:point", model)


class PersonalGuidancePermissionTests(unittest.TestCase):
    setUpClass = fixtures.LearningPathPermissionsTests.__dict__["setUpClass"]
    setUp = fixtures.LearningPathPermissionsTests.setUp
    credentials = staticmethod(fixtures.LearningPathPermissionsTests.credentials)
    account = fixtures.LearningPathPermissionsTests.account
    publish = fixtures.LearningPathPermissionsTests.publish
    classroom = fixtures.LearningPathPermissionsTests.classroom
    setup_task = fixtures.LearningPathPermissionsTests.setup_task
    start_task = fixtures.LearningPathPermissionsTests.start_task
    submit = fixtures.LearningPathPermissionsTests.submit
    age = fixtures.LearningPathPermissionsTests.age

    def test_hidden_quiz_blocks_recommendations_and_ai_until_release(self):
        task, _, _ = self.setup_task("quiz")
        self.start_task(task)
        point = next(
            p
            for p in self.student.get("/api/learning/path").json["points"]
            if p["title"] == "DH 计算"
        )
        self.assertEqual(point["actions"], [])
        endpoint = "/api/learning/points/cybersec_lab:DH 计算/explanation"
        with patch(
            "webapp_core.chat.auto_runtime.auto_router_llm",
            new=SimpleNamespace(model_name="test-model", ainvoke=AsyncMock()),
        ) as model:
            self.assertEqual(self.student.post(endpoint, json={}).status_code, 409)
            model.ainvoke.assert_not_awaited()
            self.assertEqual(
                self.app.test_client().post(endpoint, json={}).status_code, 401
            )
            self.age(task, due_at=0)
            model.ainvoke.return_value = SimpleNamespace(
                content="请对照课程资料，核对公开值计算。"
            )
            response = self.student.post(endpoint, json={})
            self.assertEqual(response.status_code, 200, response.json)
        other = next(
            p
            for p in self.other_student.get("/api/learning/path").json["points"]
            if p["title"] == "DH 计算"
        )
        self.assertIsNone(other["guidance"])

    def test_ai_failure_returns_recoverable_error(self):
        with patch(
            "webapp_core.chat.auto_runtime.auto_router_llm",
            new=SimpleNamespace(model_name="test-model", ainvoke=AsyncMock()),
        ) as model:
            model.ainvoke.side_effect = TimeoutError("provider unavailable")
            response = self.student.post(
                "/api/learning/points/cybersec_lab:DH 计算/explanation", json={}
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("阅读推荐资料", response.json["error"])
