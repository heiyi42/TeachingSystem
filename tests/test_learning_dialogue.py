import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
