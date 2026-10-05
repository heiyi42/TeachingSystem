import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from tests.learning import test_learning_path as fixtures
from webapp_core.learning.learning_dialogue import LearningDialogueService
from webapp_core.learning.learning_service import LearningConflict, LearningService
from webapp_core.learning.learning_store import LearningStore


class LearningDialogueTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.LearningPathTests.setUp
    rows = fixtures.LearningPathTests.rows

    async def test_sts_preferences_are_scoped_optional_and_do_not_change_grading(self):
        from tests.learning.test_ai_learning_plan import AIPlanTests
        from webapp_core.assistant.assistant_prompts import STS_ANSWER_GUIDANCE

        store = AIPlanTests.publish_memory(
            self, content="C语言学习偏好：先用红蓝盒子举例，再自己做题。"
        )
        AIPlanTests.publish_memory(
            self, "bob", "C语言学习偏好：只用陌生账号的暗号讲解。"
        )
        attempt = self.service.start("c_pointer_01")
        service = LearningDialogueService(self.service)
        model = self.model()
        result = await service.advance(attempt["id"], {"action": "start"}, model)
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        memories = json.dumps(context["personal_memories"], ensure_ascii=False)
        self.assertIn("红蓝盒子", memories)
        self.assertNotIn("陌生账号", memories)
        self.assertIn(STS_ANSWER_GUIDANCE, model.ainvoke.call_args.args[0][0][1])
        self.assertIsNone(result["evaluation"])
        self.assertTrue(result["tutoring_viewed"])
        store.settings("alice", enabled=False)
        await service.advance(
            attempt["id"],
            {"action": "answer", "answer": "不确定", "revision": 1},
            model,
        )
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        self.assertEqual(context["personal_memories"], [])

    async def test_forgetting_sts_during_tutoring_rejects_stale_response(self):
        from tests.learning.test_ai_learning_plan import AIPlanTests

        store = AIPlanTests.publish_memory(self)
        attempt = self.service.start("c_pointer_01")
        model = self.model()
        response = model.ainvoke.return_value

        async def forget(*args):
            store.settings("alice", forget=True)
            return response

        model.ainvoke.side_effect = forget
        with self.assertRaises(LearningConflict):
            await LearningDialogueService(self.service).advance(
                attempt["id"], {"action": "start"}, model
            )
        self.assertIsNone(self.service.get(attempt["id"])["dialogue"])

    async def test_lru_explanation_gets_verified_example_without_current_answer(self):
        attempt = self.service.start("lru_01")
        service = LearningDialogueService(self.service)
        model = self.model()
        await service.advance(attempt["id"], {"action": "start"}, model)
        first = json.loads(model.ainvoke.call_args.args[0][1][1])
        self.assertIsNone(first["worked_example"])
        await service.advance(
            attempt["id"],
            {"action": "answer", "answer": "我不确定淘汰谁", "revision": 1},
            model,
        )
        example = json.loads(model.ainvoke.call_args.args[0][1][1])["worked_example"]
        self.assertEqual(example["algorithm"], "LRU")
        sequence = example["parameters"]["sequence"]
        self.assertFalse(
            set(sequence) & set(attempt["exercise"]["parameters"]["sequence"])
        )
        self.assertEqual(
            example["solution"],
            self.service._solve(
                {**attempt["exercise"], "parameters": example["parameters"]}
            ),
        )
        self.assertEqual(example["solution"]["trace"][-1]["evicted"], sequence[1])
        self.assertEqual(example["solution"]["trace"][2]["event"], "hit")
        self.assertIsNone(self.service.get(attempt["id"])["evaluation"])

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
                            "explanation": "先区分位置变化与数值变化，再用一个不同输入逐步检查。",
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
                        {"ready_to_verify": "true"}, {"explanation": ""},
                        {"ready_to_verify": False, "explanation": "  "}):
            model.ainvoke.return_value.content = json.dumps({**payload, **invalid})
            with self.assertRaises(RuntimeError):
                await service.advance(attempt["id"], {
                    "action": "answer", "answer": "不确定", "revision": 1,
                }, model)
            self.assertEqual(self.service.get(attempt["id"])["dialogue"]["revision"], 1)

    async def test_invalid_reply_is_repaired_once_before_saving(self):
        attempt = self.service.start("dh_01")
        service = LearningDialogueService(self.service)
        model = self.model()
        await service.advance(attempt["id"], {"action": "start"}, model)
        good = model.ainvoke.return_value
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        invalid = json.loads(good.content)
        invalid["focus_code"] = next(iter(context["focus_options"].values()))
        model.ainvoke.reset_mock()
        model.ainvoke.side_effect = [SimpleNamespace(content=json.dumps(invalid)), good]
        result = await service.advance(attempt["id"], {
            "action": "answer", "answer": "不确定", "revision": 1,
        }, model)
        self.assertEqual(model.ainvoke.await_count, 2)
        self.assertEqual(result["dialogue"]["revision"], 2)
        self.assertEqual(len(result["dialogue"]["turns"]), 2)
        self.assertIsNone(result["evaluation"])

    async def test_repair_shares_deadline_and_does_not_save_partial_reply(self):
        attempt = self.service.start("dh_01")
        service = LearningDialogueService(self.service)
        await service.advance(attempt["id"], {"action": "start"}, self.model())
        before = self.service.get(attempt["id"])["dialogue"]
        model = self.model()

        async def slow_reply(_messages):
            await asyncio.sleep(0.03)
            return SimpleNamespace(content="{}")

        model.ainvoke.side_effect = slow_reply
        timeout = asyncio.timeout
        with patch("webapp_core.learning.learning_dialogue.asyncio.timeout", side_effect=lambda _: timeout(0.05)):
            with self.assertRaises(TimeoutError):
                await service.advance(attempt["id"], {
                    "action": "answer", "answer": "不确定", "revision": 1,
                }, model)
        self.assertEqual(model.ainvoke.await_count, 2)
        self.assertEqual(self.service.get(attempt["id"])["dialogue"], before)

    async def test_diagnosis_without_optional_course_materials(self):
        with patch("webapp_core.learning.learning_path.MATERIAL_ROOT", self.path.parent / "missing"):
            attempt = self.service.start("dh_01")
            result = await LearningDialogueService(self.service).advance(
                attempt["id"], {"action": "start"}, self.model()
            )
        self.assertEqual(result["dialogue"]["status"], "talking")

    async def test_lru_misconception_to_new_question(self):
        import copy
        from tests.learning.test_learning_service import LRU_01, LRU_02

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
