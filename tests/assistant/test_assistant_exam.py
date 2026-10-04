import copy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from webapp_core.assistant.assistant_exam import ExamPlans, today
from webapp_core.learning.learning_service import LearningService, LearningConflict
from webapp_core.learning.learning_store import LearningStore
from webapp_core.learning.learning_exercises import EXERCISES
from webapp_core.chat.problem_tutoring_service import ProblemTutoringService
from tests.learning.test_learning_service import LRU_01, LRU_02
from tests.school import test_school_permissions as school_fixtures


class ExamPlanTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch(
                "webapp_core.learning.learning_path.LearningPathService.material_path",
                return_value=None,
            )
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "learning.sqlite3"
        catalog = {key: EXERCISES[key] for key in ("lru_01", "lru_02")}
        self.learning = LearningService(
            LearningStore(self.path, owner_id="alice"),
            ProblemTutoringService(),
            catalog=catalog,
        )
        self.plans = ExamPlans(self.learning)
        self.chapter = catalog["lru_01"]["chapter_id"]

    def draft(self, days=3, minutes=15):
        return self.plans.draft(
            {
                "subject_id": "operating_systems",
                "exam_date": (today() + timedelta(days=days)).isoformat(),
                "minutes": minutes,
                "chapters": [self.chapter],
            }
        )

    def active(self):
        plan = self.draft()
        return self.plans.adopt(plan["id"], plan["revision"])

    def test_draft_confirmation_execution_and_first_independent_completion(self):
        plan = self.draft()
        task = next(t for t in plan["tasks"] if t["kind"] == "practice")
        with self.assertRaises(LearningConflict):
            self.plans.start(plan["id"], task["id"])
        self.assertIsNone(self.plans.store.claim())
        active = self.plans.adopt(plan["id"], 0)
        self.assertEqual(
            self.plans.adopt(plan["id"], 0)["revision"], active["revision"]
        )
        attempt = self.plans.start(active["id"], task["id"])
        self.assertEqual(
            self.plans.start(active["id"], task["id"])["id"], attempt["id"]
        )
        self.learning.submit(attempt["id"], LRU_01)
        current = self.plans.overview()["plans"][0]
        self.assertEqual(len(current["completed"]), 1)
        self.assertEqual(current["completed"][0]["evidence_id"], attempt["id"])
        self.assertEqual(current["days"][0]["completed_minutes"], 15)
        self.assertEqual(current["days"][0]["tasks"], [])
        self.assertTrue(
            ExamPlans(
                LearningService(
                    LearningStore(self.path, owner_id="alice"),
                    self.learning.solver,
                    catalog=self.learning.catalog,
                )
            ).overview()["plans"]
        )

    def test_correction_requires_new_independent_retest(self):
        plan = self.active()
        task = next(t for t in plan["tasks"] if t["kind"] == "practice")
        attempt = self.plans.start(plan["id"], task["id"])
        wrong = copy.deepcopy(LRU_01)
        wrong[4] = {"frames": "2, 3, 4", "event": "fault", "evicted": "1"}
        self.learning.submit(attempt["id"], wrong)
        self.assertEqual(
            self.plans.overview()["plans"][0]["tasks"][0]["status"], "needs_correction"
        )
        self.learning.submit(attempt["id"], LRU_01)
        view = self.plans.overview()["plans"][0]
        self.assertEqual(view["tasks"][0]["status"], "needs_retest")
        self.assertEqual(view["completed"], [])
        retest = self.plans.start(plan["id"], task["id"])
        self.assertNotEqual(attempt["id"], retest["id"])
        self.assertEqual(retest["parent_id"], attempt["id"])
        self.learning.submit(retest["id"], LRU_02)
        self.assertEqual(len(self.plans.overview()["plans"][0]["completed"]), 1)

    def test_today_adjustment_proposal_stale_revision_and_rollover(self):
        plan = self.active()
        proposed = self.plans.budget(plan["id"], plan["revision"], 0, propose=True)
        self.assertEqual(proposed["days"][0]["budget"], 15)
        self.assertEqual(proposed["budget_proposal"]["minutes"], 0)
        with self.assertRaises(LearningConflict):
            self.plans.budget(plan["id"], plan["revision"], 0)
        adjusted = self.plans.budget(plan["id"], proposed["revision"], 0)
        self.assertEqual(adjusted["days"][0]["tasks"], [])
        self.assertEqual(adjusted["days"][1]["budget"], 15)
        self.assertTrue(adjusted["days"][1]["tasks"])
        tomorrow = today() + timedelta(days=1)
        with patch("webapp_core.assistant.assistant_exam.today", return_value=tomorrow):
            self.assertEqual(self.plans.overview()["plans"][0]["days"][0]["budget"], 15)

    def test_isolation_expiration_missing_data_and_capacity(self):
        plan = self.active()
        bob = ExamPlans(
            LearningService(
                LearningStore(self.path, owner_id="bob"),
                self.learning.solver,
                catalog=self.learning.catalog,
            )
        )
        self.assertEqual(bob.overview()["plans"], [])
        with self.assertRaises(LookupError):
            bob.adopt(plan["id"], plan["revision"])
        with self.assertRaises(ValueError):
            self.draft(days=0)
        with self.assertRaises(ValueError):
            self.draft(minutes=5)
        short = self.draft(days=1)
        short = self.plans.adopt(short["id"], 0)
        empty = self.plans.budget(short["id"], short["revision"], 0)
        self.assertEqual(len(empty["overflow"]), 1)
        self.assertTrue(empty["gaps"])
        with patch(
            "webapp_core.assistant.assistant_exam.today", return_value=today() + timedelta(days=1)
        ):
            view = self.plans.overview()["plans"][0]
            self.assertTrue(view["expired"])
            with self.assertRaises(LearningConflict):
                self.plans.start(view["id"], view["tasks"][0]["id"])

    def test_prior_mastery_reduces_work_and_source_deletion_does_not_erase_plan(self):
        attempt = self.learning.start("lru_01")
        self.learning.submit(attempt["id"], LRU_01)
        plan = self.draft()
        self.assertEqual(len(plan["covered"]), 1)
        self.assertEqual(plan["tasks"], [])
        self.plans.adopt(plan["id"], 0)
        self.plans.store.settings("alice", forget=True)
        self.assertEqual(len(self.plans.overview()["plans"]), 1)

    def test_reading_uses_real_mark_and_does_not_assert_mastery(self):
        material = self.path.parent / "chapter.txt"
        material.write_text("资料")
        with patch(
            "webapp_core.learning.learning_path.LearningPathService.material_path",
            return_value=material,
        ):
            plan = self.active()
            self.assertEqual(len(plan["tasks"]), 2)
            self.learning.store.mark_reading(self.chapter, True)
            current = self.plans.overview()["plans"][0]
            self.assertEqual(len(current["completed"]), 1)
            self.assertEqual(current["completed"][0]["kind"], "reading")
            self.assertEqual(len(current["days"][0]["tasks"]), 0)
            self.assertEqual(current["days"][1]["tasks"][0]["kind"], "practice")


class ExamHTTPTests(unittest.TestCase):
    setUpClass = school_fixtures.SchoolPermissionsTests.__dict__["setUpClass"]

    def setUp(self):
        school_fixtures.SchoolPermissionsTests.setUp(self)
        self.enterContext(
            patch(
                "webapp_core.learning.learning_path.LearningPathService.material_path",
                return_value=None,
            )
        )

    credentials = staticmethod(school_fixtures.SchoolPermissionsTests.credentials)
    account = school_fixtures.SchoolPermissionsTests.account
    publish = school_fixtures.SchoolPermissionsTests.publish

    def test_published_only_adopt_and_student_ownership(self):
        payload = {
            "subject_id": "operating_systems",
            "exam_date": (today() + timedelta(days=3)).isoformat(),
            "minutes": 30,
            "chapters": [EXERCISES["lru_01"]["chapter_id"]],
        }
        initial = self.student.post("/api/assistant/exam-plans", json=payload)
        self.assertEqual(initial.status_code, 201, initial.json)
        self.assertEqual(initial.json["tasks"], [])
        self.publish("training:lru_01")
        draft = self.student.post("/api/assistant/exam-plans", json=payload).json
        self.assertEqual(len(draft["tasks"]), 1)
        adopted = self.student.post(
            f"/api/assistant/exam-plans/{draft['id']}/adopt", json={"revision": 0}
        )
        self.assertEqual(adopted.status_code, 200, adopted.json)
        url = f"/api/assistant/exam-plans/{draft['id']}/tasks/{draft['tasks'][0]['id']}/start"
        self.assertEqual(self.other_student.post(url, json={}).status_code, 404)
        self.assertEqual(self.student.post(url, json={}).status_code, 200)
        self.assertEqual(
            self.app.test_client().get("/api/assistant/exam-plans").status_code, 401
        )

    def test_chat_proposes_but_cannot_adopt(self):
        self.publish("training:lru_01")
        args = {
            "subject_id": "operating_systems",
            "exam_date": (today() + timedelta(days=3)).isoformat(),
            "minutes": 30,
            "chapters": [EXERCISES["lru_01"]["chapter_id"]],
        }
        model = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(
                    content="", tool_calls=[{"name": "draft_exam_plan", "args": args}]
                )
            )
        )
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", model):
            result = self.student.post(
                "/api/assistant/messages",
                json={
                    "request_id": "exam-chat-request-001",
                    "content": "请生成复习草稿",
                },
            )
        self.assertEqual(result.status_code, 200, result.json)
        messages = model.ainvoke.call_args.args[0]
        system_messages = [content for role, content in messages if role == "system"]
        self.assertEqual(len(system_messages), 1)
        self.assertIn('"id": "operating_systems_08"', system_messages[0])
        self.assertIn('"title": "虚拟内存"', system_messages[0])
        plans = self.student.get("/api/assistant/exam-plans").json["plans"]
        self.assertEqual([p["status"] for p in plans], ["draft"])
        self.assertIn("采用计划", result.json["messages"][-1]["content"])
