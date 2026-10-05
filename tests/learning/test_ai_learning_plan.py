import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import closing
from tests.learning import test_learning_plan as fixtures
from webapp_core.learning.learning_plan import LearningPlanService
from webapp_core.learning.learning_service import LearningConflict, LearningService
from webapp_core.learning.learning_store import LearningStore


class AIPlanTests(unittest.TestCase):
    setUp = fixtures.StudyPlanTests.setUp

    def test_ai_candidates_include_executable_alternatives(self):
        service = LearningPlanService(self.service)
        service.configure(
            "operating_systems", {"minutes": 30, "chapter_id": "operating_systems_08"}
        )
        snapshot = service.view("operating_systems", include_ai=False)
        kinds = {o["kind"] for o in snapshot["options"]}
        self.assertTrue({"practice", "explain", "material"} <= kinds)
        tokens = {
            a["token"] for p in service.path.dashboard()["points"] for a in p["actions"]
        }
        self.assertTrue(all(o["token"] in tokens for o in snapshot["options"]))
        self.assertTrue(all(t["kind"] == "material" for t in snapshot["tasks"]))

    def test_regular_tasks_can_follow_preference_but_urgent_work_cannot_be_skipped(
        self,
    ):
        service = LearningPlanService(self.service)
        service.configure(
            "operating_systems", {"minutes": 30, "chapter_id": "operating_systems_08"}
        )
        snapshot = service.view("operating_systems", include_ai=False)
        practice = next(o for o in snapshot["options"] if o["kind"] == "practice")
        material = next(o for o in snapshot["options"] if o["kind"] == "material")

        def choose(data):
            data["tasks"] = [
                {"token": o["token"], "reason": "按偏好先练习，卡住后再查阅。"}
                for o in [practice, material]
            ]

        result = asyncio.run(
            service.generate("operating_systems", self.model(snapshot, choose))
        )
        self.assertEqual([t["kind"] for t in result["tasks"]], ["practice", "material"])
        self.service.start("lru_01")
        urgent = service.view("operating_systems", include_ai=False)
        practice = next(o for o in urgent["options"] if o["kind"] == "practice")
        material = next(o for o in urgent["options"] if o["kind"] == "material")
        with self.assertRaises(ValueError):
            asyncio.run(
                service.generate("operating_systems", self.model(urgent, choose))
            )

    def test_effective_time_budget_is_enforced_without_overwriting_profile(self):
        service = LearningPlanService(self.service)
        service.configure("operating_systems", {"minutes": 50, "chapter_id": ""})
        snapshot = service.view("operating_systems", include_ai=False)
        practice = next(o for o in snapshot["options"] if o["kind"] == "practice")

        def too_long(data):
            data.update(
                available_minutes=10,
                tasks=[{"token": practice["token"], "reason": "测试预算"}],
            )

        with self.assertRaises(ValueError):
            asyncio.run(
                service.generate("operating_systems", self.model(snapshot, too_long))
            )
        self.assertIsNone(service.view("operating_systems")["ai_plan"])
        result = asyncio.run(
            service.generate(
                "operating_systems",
                self.model(snapshot, lambda d: d.update(available_minutes=0, tasks=[])),
            )
        )
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["ai_plan"]["available_minutes"], 0)
        self.assertEqual(service.profile("operating_systems")["minutes"], 50)
        for value in [True, -1, 51, "20"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                asyncio.run(
                    service.generate(
                        "operating_systems",
                        self.model(
                            snapshot, lambda d: d.update(available_minutes=value)
                        ),
                    )
                )

    def publish_memory(
        self, owner="alice", content="C语言学习偏好：先画图理解指针，再做题。"
    ):
        from webapp_core.assistant.assistant_store import AssistantStore, enqueue

        store = AssistantStore(self.path)
        with closing(store.connect()) as db, db:
            enqueue(db, owner, owner + content, content)
        job = store.claim()
        store.publish(
            job,
            {
                "scopes": {"style": {"title": "学习偏好", "summary": content}},
                "claims": {
                    "preference": {
                        "id": "preference",
                        "content": content,
                        "scope_id": "style",
                        "event_ids": [owner + content],
                        "temporal": "长期偏好",
                    }
                },
                "events": {
                    owner + content: {"summary": content, "timestamp": "2026-10-04"}
                },
            },
        )
        return store

    def test_personal_memory_is_used_scoped_and_invalidates_cached_plan(self):
        from webapp_core.assistant.assistant_prompts import STS_ANSWER_GUIDANCE

        store = self.publish_memory()
        self.publish_memory("bob", "C语言学习偏好：只看文字，属于另一个账号。")
        service = LearningPlanService(self.service)
        service.configure("C_program", {"minutes": 15, "chapter_id": "C_program_08"})
        model = self.model(service.view("C_program", include_ai=False))
        result = asyncio.run(service.generate("C_program", model))
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        self.assertIn(STS_ANSWER_GUIDANCE, model.ainvoke.call_args.args[0][0][1])
        memories = json.dumps(context["个人长期记忆"], ensure_ascii=False)
        self.assertIn("先画图", memories)
        self.assertNotIn("另一个账号", memories)
        self.assertEqual(context["目标与时间"]["minutes"], 15)
        self.assertTrue(context["当前日期（北京时间）"])
        self.assertIsNotNone(result["ai_plan"])
        self.publish_memory(content="C语言学习偏好更正：先看实际代码，再画图。")
        self.assertTrue(service.view("C_program")["ai_stale"])
        store.settings("alice", enabled=False)
        disabled = service.view("C_program", include_ai=False)
        self.assertEqual(disabled["personal_memories"], [])
        store.settings("alice", forget=True)
        self.assertEqual(
            service.view("C_program", include_ai=False)["personal_memories"], []
        )

    def test_memory_change_during_generation_rejects_stale_plan(self):
        self.publish_memory()
        service = LearningPlanService(self.service)
        model = self.model(service.view("C_program", include_ai=False))
        response = model.ainvoke.return_value

        async def changed(*args):
            self.publish_memory(content="C语言学习目标已调整。")
            return response

        model.ainvoke.side_effect = changed
        with self.assertRaises(LearningConflict):
            asyncio.run(service.generate("C_program", model))
        self.assertIsNone(service.view("C_program")["ai_plan"])

    def test_failed_independent_attempt_is_not_an_independent_pass(self):
        attempt = self.service.start("c_pointer_01")
        self.service.submit(attempt["id"], [{"value": "999"} for _ in attempt["draft"]])
        plan = LearningPlanService(self.service)
        model = self.model(plan.view("C_program", include_ai=False))
        asyncio.run(plan.generate("C_program", model))
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        evidence = next(
            e for e in context["知识点证据"] if e["id"] == "C_program:c_pointer"
        )
        self.assertEqual(evidence["新题首次独立作答次数（含未通过）"], 1)
        self.assertEqual(evidence["新题首次独立通过题数"], 0)
        self.assertNotIn("独立次数", evidence)

    def model(self, snapshot, transform=None):
        options = snapshot["options"]
        chosen = [options[0]]
        data = {
            "available_minutes": snapshot["profile"]["minutes"],
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

    def test_plan_distinguishes_completed_correction_from_pending_new_question(self):
        from tests.learning.test_learning_service import LRU_01

        original = self.service.start("lru_01")
        wrong = [dict(row) for row in LRU_01]
        wrong[-1]["evicted"] = "2"
        self.service.submit(original["id"], wrong)
        self.service.hint(original["id"], None)
        self.service.submit(original["id"], LRU_01)
        new = self.service.start("lru_02")
        plan = LearningPlanService(self.service)
        model = self.model(plan.view("operating_systems", include_ai=False))
        asyncio.run(plan.generate("operating_systems", model))
        context = json.loads(model.ainvoke.call_args.args[0][1][1])
        evidence = next(e for e in context["知识点证据"] if e["id"] == "operating_systems:LRU")
        attempts = {e["attempt_id"]: e for e in evidence["近期练习"]}
        self.assertEqual(attempts[original["id"]]["当前状态"], "passed")
        self.assertEqual(attempts[original["id"]]["最近结果"], "corrected_pass")
        self.assertEqual(attempts[new["id"]]["最近结果"], "尚未提交")
        task = next(t for t in context["候选任务"] if t.get("attempt_id") == new["id"])
        self.assertEqual(task["exercise_id"], "lru_02")
        self.assertEqual(task["submission_count"], 0)
        self.assertEqual(task["title"], "继续新题作答")

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

    def test_invalid_selection_is_repaired_before_plan_is_saved(self):
        service = LearningPlanService(self.service)
        model = self.model(service.view("C_program", include_ai=False))
        good = model.ainvoke.return_value
        model.ainvoke.side_effect = [SimpleNamespace(content='{"tasks":[]}'), good]
        result = asyncio.run(service.generate("C_program", model))
        self.assertEqual(model.ainvoke.await_count, 2)
        self.assertEqual(len(result["tasks"]), 1)
        self.assertIsNotNone(result["ai_plan"])

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
