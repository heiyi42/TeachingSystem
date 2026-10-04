import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tests.learning import test_learning_plan as fixtures
from webapp_core.learning.learning_plan import LearningPlanService
from webapp_core.learning.learning_service import LearningConflict, LearningService
from webapp_core.learning.learning_store import LearningStore


class AIPlanTests(unittest.TestCase):
    setUp = fixtures.StudyPlanTests.setUp

    def model(self, snapshot, transform=None):
        options = snapshot["options"]
        chosen = [options[0]]
        data = {
            "analysis": "目前证据有限，先核对相关基础，再通过新题验证。",
            "tasks": [
                {"token": t["token"], "reason": "依据目前证据安排这一学习任务。"}
                for t in chosen
            ],
        }
        if transform:
            transform(data)
        return SimpleNamespace(
            model_name="test-model",
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(
                    content=json.dumps(data, ensure_ascii=False)
                )
            ),
        )

    def test_selection_cached_and_evidence_invalidates(self):
        service = LearningPlanService(self.service)
        snapshot = service.view("C_program", include_ai=False)
        model = self.model(snapshot)
        result = asyncio.run(service.generate("C_program", model))
        self.assertIsNotNone(result["ai_plan"])
        self.assertEqual(len(result["tasks"]), 1)
        self.assertIn("依据", result["tasks"][0]["ai_reason"])
        self.assertIn("知识点证据", model.ainvoke.call_args.args[0][1][1])
        other = LearningPlanService(
            LearningService(
                LearningStore(self.path, owner_id="bob"), self.service.solver
            )
        )
        self.assertIsNone(other.view("C_program")["ai_plan"])
        service.configure("C_program", {"minutes": 15, "chapter_id": ""})
        stale = service.view("C_program")
        self.assertIsNone(stale["ai_plan"])
        self.assertTrue(stale["ai_stale"])

    def test_bad_model_outputs_and_budget_rejected(self):
        service = LearningPlanService(self.service)
        snapshot = service.view("C_program", include_ai=False)
        for change in [
            lambda d: d["tasks"][0].update(token="invented"),
            lambda d: d.update(tasks=[]),
            lambda d: d["tasks"].append(dict(d["tasks"][0])),
            lambda d: d.update(analysis=""),
        ]:
            with self.assertRaises(ValueError):
                asyncio.run(service.generate("C_program", self.model(snapshot, change)))
        self.assertIsNone(service.view("C_program")["ai_plan"])
        model = SimpleNamespace(
            model_name="test", ainvoke=AsyncMock(side_effect=TimeoutError)
        )
        with self.assertRaises(TimeoutError):
            asyncio.run(service.generate("C_program", model))
        self.assertTrue(service.view("C_program")["tasks"])
        service.configure("C_program", {"minutes": 15, "chapter_id": ""})
        snapshot = service.view("C_program", include_ai=False)

        def over_budget(data):
            data["tasks"] = [
                {"token": t["token"], "reason": "测试时间约束"}
                for t in snapshot["options"][:5]
            ]

        self.assertGreater(
            sum(t["estimated_minutes"] for t in snapshot["options"][:5]), 15
        )
        with self.assertRaises(ValueError):
            asyncio.run(
                service.generate("C_program", self.model(snapshot, over_budget))
            )

    def test_concurrent_evidence_change_rejects_result(self):
        service = LearningPlanService(self.service)
        snapshot = service.view("C_program", include_ai=False)
        good = self.model(snapshot)
        response = good.ainvoke.return_value

        async def changed(*args):
            self.service.start("c_loop_01")
            return response

        good.ainvoke.side_effect = changed
        with self.assertRaises(LearningConflict):
            asyncio.run(service.generate("C_program", good))
        self.assertIsNone(service.view("C_program")["ai_plan"])
