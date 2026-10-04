import unittest

from tests.learning import test_learning_path as fixtures
from webapp_core.learning.learning_roadshow import roadshow, CASES
from webapp_core.learning.learning_plan import LearningPlanService
from webapp_core.learning.learning_timeline import evidence_timeline
from webapp_core.learning.learning_walkthrough import LearningWalkthroughService
from webapp_core.learning.learning_service import LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.school.grading_review import GradingReviewService


class LearningEvidenceTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp
    rows = fixtures.LearningPathTests.rows

    def test_timeline_preserves_submission_and_review_history(self):
        a = self.service.start("dh_01")
        LearningWalkthroughService(self.service).advance(
            a["id"], {"prediction": "还不确定"}
        )
        self.service.submit(a["id"], self.rows(a))
        before = LearningPlanService(self.service).view("cybersec_lab")["timeline"]
        self.assertEqual(
            [e["kind"] for e in before], ["submitted", "process_prediction", "started"]
        )
        self.assertIn("辅导", before[0]["detail"])
        review_service = GradingReviewService(self.service)
        result = review_service.request(a["id"], 1, "测试评测复核的历史事件")
        review_service.resolve(
            a["id"],
            result["grading_reviews"][0]["id"],
            "fail",
            "测试人工修订后保留原始提交证据",
            {"id": "teacher", "name": "teacher"},
        )
        after = LearningPlanService(self.service).view("cybersec_lab")["timeline"]
        self.assertEqual(after[0]["kind"], "grading_review_resolved")
        self.assertEqual(next(e for e in after if e["kind"] == "submitted"), before[0])
        self.assertEqual(
            LearningPlanService(self.service).view("cybersec_lab")["memories"][0][
                "status"
            ],
            "needs_work",
        )

    def test_owner_course_hidden_and_assignment_isolation(self):
        a = self.service.start("dh_01")
        other = LearningService(
            LearningStore(self.path, owner_id="bob"), self.service.solver
        )
        self.assertEqual(
            LearningPlanService(other).view("cybersec_lab")["timeline"], []
        )
        self.assertEqual(
            LearningPlanService(self.service).view("C_program")["timeline"], []
        )
        points = self.study.dashboard()["points"]
        for point in points:
            point["guidance_blocked"] = True
        self.assertEqual(evidence_timeline(self.service, points), [])

        def assign(state, existing):
            state["assignment_id"] = "hidden"
            return "fixture", {}

        self.service.store.update(a["id"], assign)
        self.assertEqual(
            evidence_timeline(self.service, self.study.dashboard()["points"]), []
        )

    def test_roadshow_all_three_courses_and_no_personal_writes(self):
        original = self.service.start("dh_01")
        before = self.service.store.list_attempts()
        for subject in CASES:
            with self.subTest(subject=subject):
                data = roadshow(subject)
                self.assertEqual(data["kind"], "simulated_demo")
                a, b = data["students"]
                self.assertTrue(a["independent_pass"])
                self.assertFalse(b["independent_pass"])
                self.assertEqual(a["memories"][0]["status"], "independent_evidence")
                self.assertEqual(b["memories"][0]["status"], "needs_work")
                self.assertTrue(any(t["kind"] == "continue" for t in b["tasks"]))
                self.assertEqual(len(a["timeline"]), 6)
                self.assertEqual(
                    [e["kind"] for e in a["timeline"]],
                    [e["kind"] for e in b["timeline"]],
                )
                self.assertNotIn(original["id"], str(data))
        self.assertEqual(self.service.store.list_attempts(), before)
        with self.assertRaises(ValueError):
            roadshow("unknown")
