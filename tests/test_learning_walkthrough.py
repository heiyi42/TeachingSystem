import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_learning_path as fixtures
from webapp_core.learning_walkthrough import LearningWalkthroughService
from webapp_core.learning_dialogue import LearningDialogueService
from webapp_core.learning_service import LearningService, LearningConflict
from webapp_core.learning_store import LearningStore


class WalkthroughTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp
    rows = fixtures.LearningPathTests.rows

    def test_all_supported_published_shapes_and_no_future_answers(self):
        subjects = set()
        for exercise in self.service.catalog.values():
            if exercise["kind"] in {
                "c_repair",
                "pv_design",
                "c_program",
                "security_lab",
            }:
                continue
            with self.subTest(exercise=exercise["id"]):
                attempt = self.service.start(exercise["id"])
                result = LearningWalkthroughService(self.service).advance(
                    attempt["id"], {"prediction": "不确定"}
                )
                self.assertEqual(len(result["walkthrough"]["steps"]), 1)
                self.assertTrue(result["tutoring_viewed"])
                self.assertIsNone(result["solution"])
                self.assertIsNone(result["evaluation"])
                self.assertGreater(result["walkthrough"]["total"], 0)
                subjects.add(exercise["subject_id"])
        self.assertEqual(len(subjects), 3)

    def test_revision_ownership_limits_and_assistance(self):
        attempt = self.service.start("dh_01")
        service = LearningWalkthroughService(self.service)
        for bad in ("", "x" * 2001, None):
            with self.assertRaises(ValueError):
                service.advance(attempt["id"], {"prediction": bad})
        self.assertFalse(self.service.get(attempt["id"])["tutoring_viewed"])
        result = service.advance(attempt["id"], {"prediction": "甲公开值需要取模"})
        with self.assertRaises(LearningConflict):
            service.advance(attempt["id"], {"prediction": "重复", "revision": 0})
        for revision in range(1, result["walkthrough"]["total"]):
            result = service.advance(
                attempt["id"], {"prediction": "继续", "revision": revision}
            )
        with self.assertRaises(LearningConflict):
            service.advance(
                attempt["id"],
                {"prediction": "继续", "revision": result["walkthrough"]["revision"]},
            )
        other = LearningService(
            LearningStore(self.path, owner_id="bob"), self.service.solver
        )
        with self.assertRaises(LookupError):
            LearningWalkthroughService(other).advance(
                attempt["id"], {"prediction": "测试"}
            )
        submitted = self.service.submit(attempt["id"], self.rows(attempt))
        self.assertTrue(submitted["evaluation"]["passed"])
        self.assertFalse(submitted["first_unassisted_pass"])
        self.assertEqual(submitted["walkthrough"], result["walkthrough"])

    def test_assignment_denied_and_runtime_requires_submission(self):
        attempt = self.service.start("dh_01")

        def assign(state, existing):
            state["assignment_id"] = "quiz"
            return "fixture", {}

        self.service.store.update(attempt["id"], assign)
        with self.assertRaises(LearningConflict):
            LearningWalkthroughService(self.service).advance(
                attempt["id"], {"prediction": "测试"}
            )
        attempt = self.service.start("c_program_01")
        with self.assertRaises(LearningConflict):
            LearningWalkthroughService(self.service).advance(
                attempt["id"], {"prediction": "测试"}
            )


class WalkthroughDialogueTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.LearningPathTests.setUp

    async def test_prediction_is_sent_as_evidence_to_diagnosis(self):
        attempt = self.service.start("c_pointer_01")
        LearningWalkthroughService(self.service).advance(
            attempt["id"], {"prediction": "p++ 会修改数组值"}
        )
        model = SimpleNamespace(
            model_name="test",
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(
                    content=json.dumps(
                        {
                            "hypothesis": "可能混淆指针和值",
                            "question": "指针移动后数组值会改变吗？",
                            "next_step": "核对指针和数组值",
                        }
                    )
                )
            ),
        )
        await LearningDialogueService(self.service).advance(
            attempt["id"], {"action": "start"}, model
        )
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        self.assertEqual(
            context["process_predictions"][0]["prediction"], "p++ 会修改数组值"
        )


@unittest.skipUnless(
    os.getenv("TEST_C_PROGRAM_DOCKER") == "1", "Docker enabled explicitly"
)
class WalkthroughRuntimeTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp

    def test_real_program_and_lab_artifact_replay(self):
        for exercise_id in ["c_program_01", "security_lab_database_01"]:
            with self.subTest(exercise=exercise_id):
                attempt = self.service.start(exercise_id)
                solution = self.service._solve(attempt["exercise"])
                submitted = self.service.submit(
                    attempt["id"], [{"code": solution["code"]}]
                )
                self.assertTrue(submitted["evaluation"]["passed"])
                service = LearningWalkthroughService(self.service)
                result = service.advance(
                    attempt["id"], {"prediction": "预计按规则生成输出"}
                )
                for revision in range(1, result["walkthrough"]["total"]):
                    result = service.advance(
                        attempt["id"], {"prediction": "核对产物", "revision": revision}
                    )
                self.assertNotIn("base64", json.dumps(result["walkthrough"]))
                self.assertTrue(result["first_unassisted_pass"])

    def test_new_submission_replaces_runtime_replay(self):
        attempt = self.service.start("c_program_01")
        self.service.submit(attempt["id"], [{"code": "int main(void) { return 0; }"}])
        service = LearningWalkthroughService(self.service)
        first = service.advance(attempt["id"], {"prediction": "检查旧输出"})
        solution = self.service._solve(attempt["exercise"])
        self.service.submit(attempt["id"], [{"code": solution["code"]}])
        latest = service.advance(
            attempt["id"],
            {"prediction": "检查新输出", "revision": first["walkthrough"]["revision"]},
        )
        self.assertEqual(latest["walkthrough"]["submission_count"], 2)
        self.assertEqual(len(latest["walkthrough"]["steps"]), 1)
        self.assertEqual(latest["walkthrough"]["steps"][0]["prediction"], "检查新输出")
        self.assertTrue(latest["walkthrough"]["steps"][0]["details"]["passed"])
