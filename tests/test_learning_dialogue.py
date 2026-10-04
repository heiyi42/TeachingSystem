import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from tests import test_learning_path as fixtures
from webapp_core.learning_dialogue import LearningDialogueService
from webapp_core.learning_service import LearningConflict, LearningService
from webapp_core.learning_store import LearningStore


class LearningDialogueTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.LearningPathTests.setUp
    rows = fixtures.LearningPathTests.rows

    def model(self):
        return SimpleNamespace(
            model_name="test",
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(
                    content=json.dumps(
                        {
                            "hypothesis": "目前证据不足，可能混淆了执行顺序。",
                            "question": "你判断第一步的依据是什么？",
                            "next_step": "重新检查第一步并提交。",
                        },
                        ensure_ascii=False,
                    )
                )
            ),
        )

    async def test_three_courses_bounded_persistent_and_verified_only_by_grader(self):
        for exercise in ("c_pointer_01", "lru_01", "dh_01"):
            with self.subTest(exercise=exercise):
                attempt = self.service.start(exercise)
                service = LearningDialogueService(self.service)
                model = self.model()
                result = await service.advance(
                    attempt["id"], {"action": "start"}, model
                )
                self.assertTrue(result["tutoring_viewed"])
                self.assertIsNone(result["evaluation"])
                for revision in (1, 2, 3):
                    result = await service.advance(
                        attempt["id"],
                        {"action": "answer", "answer": "不确定", "revision": revision},
                        model,
                    )
                self.assertEqual(result["dialogue"]["status"], "verifying")
                self.assertEqual(len(result["dialogue"]["turns"]), 3)
                self.assertEqual(
                    self.service.get(attempt["id"])["dialogue"], result["dialogue"]
                )
                with self.assertRaises(LearningConflict):
                    await service.advance(
                        attempt["id"],
                        {"action": "answer", "answer": "继续", "revision": 4},
                        model,
                    )
                if exercise != "lru_01":
                    submitted = self.service.submit(attempt["id"], self.rows(attempt))
                    self.assertTrue(submitted["evaluation"]["passed"])
                    self.assertFalse(submitted["first_unassisted_pass"])

    async def test_failure_owner_assignment_and_stale_replies(self):
        attempt = self.service.start("dh_01")
        model = self.model()
        model.ainvoke.side_effect = TimeoutError()
        service = LearningDialogueService(self.service)
        with self.assertRaises(TimeoutError):
            await service.advance(attempt["id"], {"action": "start"}, model)
        self.assertFalse(self.service.get(attempt["id"])["tutoring_viewed"])
        self.assertIsNone(self.service.get(attempt["id"])["dialogue"])
        stranger = LearningService(
            LearningStore(self.path, owner_id="bob"), self.service.solver
        )
        with self.assertRaises(LookupError):
            await LearningDialogueService(stranger).advance(
                attempt["id"], {"action": "start"}, self.model()
            )
        model = self.model()
        response = model.ainvoke.return_value

        async def concurrent(*args):
            self.service.save_draft(attempt["id"], self.rows(attempt))
            return response

        model.ainvoke.side_effect = concurrent
        with self.assertRaises(LearningConflict):
            await service.advance(attempt["id"], {"action": "start"}, model)

        def assigned(state, existing):
            state["assignment_id"] = "exam"
            return "fixture", {}

        self.service.store.update(attempt["id"], assigned)
        model = self.model()
        with self.assertRaises(LearningConflict):
            await service.advance(attempt["id"], {"action": "start"}, model)
        model.ainvoke.assert_not_awaited()

    async def test_skip_exit_duplicate_and_invalid_model(self):
        for action in ("verify", "exit"):
            attempt = self.service.start("dh_01")
            service = LearningDialogueService(self.service)
            await service.advance(attempt["id"], {"action": "start"}, self.model())
            with self.assertRaises(LearningConflict):
                await service.advance(attempt["id"], {"action": "start"}, self.model())
            model = self.model()
            result = await service.advance(
                attempt["id"], {"action": action, "revision": 1}, model
            )
            model.ainvoke.assert_not_awaited()
            self.assertEqual(
                result["dialogue"]["status"],
                "verifying" if action == "verify" else "closed",
            )
        attempt = self.service.start("dh_02")
        model = self.model()
        model.ainvoke.return_value.content = "{}"
        with self.assertRaises(RuntimeError):
            await service.advance(attempt["id"], {"action": "start"}, model)
        self.assertIsNone(self.service.get(attempt["id"])["dialogue"])

    async def test_targeted_explanation_correction_and_independent_retest(self):
        attempt = self.service.start("dh_01")
        wrong = self.rows(attempt)
        wrong[0]["value"] = str(int(wrong[0]["value"]) + 1)
        self.service.submit(attempt["id"], wrong)
        service = LearningDialogueService(self.service)
        model = self.model()
        await service.advance(attempt["id"], {"action": "start"}, model)
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        focus = next(iter(context["focus_options"]))
        payload = json.loads(model.ainvoke.return_value.content)
        payload.update(explanation="公开参数与私钥不同；先分清各自作用，再计算。",
                       focus_code=focus, ready_to_verify=True)
        model.ainvoke.return_value.content = json.dumps(payload)
        diagnosed = await service.advance(attempt["id"], {
            "action": "answer", "answer": "我不确定", "revision": 1,
        }, model)
        self.assertEqual(diagnosed["dialogue"]["status"], "verifying")
        self.assertEqual(diagnosed["dialogue"]["focus_code"], focus)
        self.assertEqual(len(diagnosed["dialogue"]["turns"]), 1)
        self.assertEqual(diagnosed["dialogue"]["explanation"], payload["explanation"])
        self.assertFalse(diagnosed["evaluation"]["passed"])
        with self.assertRaises(LearningConflict):
            self.service.start(None, parent_id=attempt["id"])
        corrected = self.service.submit(attempt["id"], self.rows(attempt))
        self.assertFalse(corrected["first_unassisted_pass"])
        retest = self.service.start(None, parent_id=attempt["id"])
        self.assertNotEqual(retest["exercise"]["id"], attempt["exercise"]["id"])
        self.assertFalse(retest["tutoring_viewed"])
        self.assertIsNone(retest["dialogue"])
        passed = self.service.submit(retest["id"], self.rows(retest))
        self.assertTrue(passed["first_unassisted_pass"])

    async def test_model_cannot_skip_first_question_or_inject_focus(self):
        attempt = self.service.start("dh_01")
        service = LearningDialogueService(self.service)
        model = self.model()
        payload = json.loads(model.ainvoke.return_value.content)
        payload.update(explanation="一个对比例子", ready_to_verify=True)
        model.ainvoke.return_value.content = json.dumps(payload)
        result = await service.advance(attempt["id"], {"action": "start"}, model)
        self.assertEqual(result["dialogue"]["status"], "talking")
        self.assertNotIn("explanation", result["dialogue"])
        for invalid in ({"focus_code": "invented_tag"},
                        {"ready_to_verify": "true"}, {"explanation": ""}):
            model.ainvoke.return_value.content = json.dumps({**payload, **invalid})
            with self.assertRaises(RuntimeError):
                await service.advance(attempt["id"], {
                    "action": "answer", "answer": "不确定", "revision": 1,
                }, model)
            self.assertEqual(self.service.get(attempt["id"])["dialogue"]["revision"], 1)

    async def test_diagnosis_without_optional_course_materials(self):
        with patch("webapp_core.learning_path.MATERIAL_ROOT", self.path.parent / "missing"):
            attempt = self.service.start("dh_01")
            result = await LearningDialogueService(self.service).advance(
                attempt["id"], {"action": "start"}, self.model()
            )
        self.assertEqual(result["dialogue"]["status"], "talking")

    async def test_lru_misconception_to_new_question(self):
        import copy
        from tests.test_learning_service import LRU_01, LRU_02

        attempt = self.service.start("lru_01")
        wrong = copy.deepcopy(LRU_01)
        wrong[4] = {"frames": "2, 3, 4", "event": "fault", "evicted": "1"}
        failed = self.service.submit(attempt["id"], wrong)
        self.assertEqual(failed["evaluation"]["first_error"]["error_code"], "wrong_victim")
        service = LearningDialogueService(self.service)
        model = self.model()
        await service.advance(attempt["id"], {"action": "start"}, model)
        payload = json.loads(model.ainvoke.return_value.content)
        payload.update(
            explanation="例如依次访问 A、B、A 后，FIFO 比装入先后，LRU 比最近访问时间。",
            focus_code="wrong_victim", ready_to_verify=True,
        )
        model.ainvoke.return_value.content = json.dumps(payload)
        await service.advance(attempt["id"], {
            "action": "answer", "answer": "我按最早装入的顺序淘汰", "revision": 1,
        }, model)
        corrected = self.service.submit(attempt["id"], LRU_01)
        self.assertFalse(corrected["first_unassisted_pass"])
        self.assertIn("淘汰页面", corrected["recommendation"]["reason"])
        retest = self.service.start(None, parent_id=attempt["id"])
        self.assertEqual(retest["exercise"]["id"], "lru_02")
        self.assertTrue(self.service.submit(retest["id"], LRU_02)["first_unassisted_pass"])
