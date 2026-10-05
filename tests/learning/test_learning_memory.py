import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.learning import test_learning_path as fixtures
from webapp_core.learning.learning_memory import learning_memories
from webapp_core.learning.learning_path import LearningPathService
from webapp_core.learning.learning_plan import LearningPlanService
from webapp_core.learning.learning_dialogue import LearningDialogueService
from webapp_core.learning.learning_walkthrough import LearningWalkthroughService
from webapp_core.learning.learning_service import LearningService
from webapp_core.learning.learning_store import LearningStore


class LearningMemoryTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp
    rows = fixtures.LearningPathTests.rows

    def memories(self):
        return LearningPlanService(self.service).view("cybersec_lab")["memories"]

    def origin(self):
        attempt = self.service.start("dh_01")
        LearningWalkthroughService(self.service).advance(
            attempt["id"], {"prediction": "我不确定何时取模"}
        )
        return attempt

    def test_evidence_evolves_without_promoting_hypotheses(self):
        first = self.origin()
        self.assertEqual(self.memories()[0]["status"], "pending")
        self.service.submit(first["id"], self.rows(first))
        self.assertEqual(self.memories()[0]["status"], "assisted_evidence")
        fresh = self.service.start("dh_02")
        self.service.submit(fresh["id"], self.rows(fresh))
        memory = self.memories()[0]
        self.assertEqual(memory["status"], "independent_evidence")
        self.assertIn("不能确认具体错因", memory["basis"])
        third = self.service.start("dh_03")
        wrong = self.rows(third)
        wrong[0]["value"] = "999"
        self.service.submit(third["id"], wrong)
        self.assertEqual(self.memories()[0]["status"], "needs_work")
        self.service.submit(third["id"], self.rows(third))
        self.assertEqual(self.memories()[0]["status"], "assisted_evidence")
        restarted = LearningService(
            LearningStore(self.path, owner_id="alice"), self.service.solver
        )
        self.assertEqual(
            LearningPlanService(restarted).view("cybersec_lab")["memories"],
            self.memories(),
        )

    def test_ownership_scope_and_hidden_assessments(self):
        self.origin()
        other = LearningService(
            LearningStore(self.path, owner_id="bob"), self.service.solver
        )
        self.assertEqual(
            LearningPlanService(other).view("cybersec_lab")["memories"], []
        )
        self.assertEqual(
            LearningPlanService(self.service).view("C_program")["memories"], []
        )
        points = LearningPathService(self.service).dashboard()["points"]
        for point in points:
            point["guidance_blocked"] = True
        self.assertEqual(learning_memories(self.service, points), [])

    def test_plan_cache_invalidates_on_new_prediction_and_model_receives_memory(self):
        first = self.origin()
        plan = LearningPlanService(self.service)
        snapshot = plan.view("cybersec_lab", include_ai=False)
        model = SimpleNamespace(
            model_name="test",
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(
                    content=json.dumps(
                        {
                            "available_minutes": snapshot["profile"]["minutes"],
                            "analysis": "先根据现有预测核对基础，再通过后续作答检验。",
                            "tasks": [
                                {
                                    "token": snapshot["options"][0]["token"],
                                    "reason": "根据近期记录安排",
                                }
                            ],
                        }
                    )
                )
            ),
        )
        result = asyncio.run(plan.generate("cybersec_lab", model))
        self.assertIsNotNone(result["ai_plan"])
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        self.assertEqual(context["跨次学习记录"][0]["attempt_id"], first["id"])
        LearningWalkthroughService(self.service).advance(
            first["id"], {"prediction": "第二次预测", "revision": 1}
        )
        stale = plan.view("cybersec_lab")
        self.assertTrue(stale["ai_stale"])
        self.assertIsNone(stale["ai_plan"])

    def test_next_conversation_receives_prior_interactions(self):
        first = self.origin()
        second = self.service.start("dh_02")
        model = SimpleNamespace(
            model_name="test",
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(
                    content=json.dumps(
                        {
                            "hypothesis": "取模顺序仍待核实",
                            "question": "你会在哪一步取模？",
                            "next_step": "检查公开值计算",
                        }
                    )
                )
            ),
        )
        asyncio.run(
            LearningDialogueService(self.service).advance(
                second["id"], {"action": "start"}, model
            )
        )
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        self.assertEqual(context["prior_learning"][0]["attempt_id"], first["id"])
        self.assertNotIn(
            second["id"], [m["attempt_id"] for m in context["prior_learning"]]
        )
