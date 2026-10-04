import copy
import unittest
from unittest.mock import patch

from tests.learning import test_learning_path as path_fixtures
from tests.learning.test_learning_service import LRU_01
from webapp_core.learning.learning_path import DAY, LearningPathService
from webapp_core.learning.learning_service import LearningService
from webapp_core.learning.learning_store import LearningStore


class PersonalLearningStateTests(unittest.TestCase):
    setUp = path_fixtures.LearningPathTests.setUp
    rows = path_fixtures.LearningPathTests.rows
    mistake = path_fixtures.LearningPathTests.mistake

    def point(self, title="DH 计算"):
        return next(p for p in self.study.dashboard()["points"] if p["title"] == title)[
            "learning_state"
        ]

    def test_cold_start_and_reading_never_infer_mastery(self):
        dashboard = self.study.dashboard()
        self.assertEqual(len({p["subject_id"] for p in dashboard["points"]}), 3)
        self.assertTrue(
            all(
                p["learning_state"]["code"] == "insufficient"
                for p in dashboard["points"]
            )
        )
        self.study.mark_reading("cybersec_lab_02", True)
        self.assertEqual(self.point()["code"], "insufficient")
        attempt = self.service.start("dh_01")
        self.service.hint(attempt["id"])
        state = self.point()
        self.assertEqual(state["code"], "in_progress")
        self.assertEqual(state["independent_count"], 0)
        self.assertEqual(state["evidence"][0]["activity"]["hint_count"], 1)
        self.assertEqual(state["evidence"][0]["submissions"], [])

    def test_three_courses_share_state_and_repeat_is_not_new_evidence(self):
        for exercise_id in ("c_loop_01", "lru_01", "dh_01"):
            attempt = self.service.start(exercise_id)
            self.service.submit(
                attempt["id"],
                (
                    copy.deepcopy(LRU_01)
                    if exercise_id == "lru_01"
                    else self.rows(attempt)
                ),
            )
        repeated = self.service.start("dh_01")
        self.service.submit(repeated["id"], self.rows(repeated))
        points = [p for p in self.study.dashboard()["points"] if p["submitted_count"]]
        self.assertEqual(len(points), 3)
        self.assertTrue(
            all(
                p["learning_state"]["code"] == "initial" and p["independent_count"] == 1
                for p in points
            )
        )
        record = next(
            e for e in self.point()["evidence"] if e["attempt_id"] == repeated["id"]
        )
        self.assertEqual(record["submissions"][0]["outcome"], "non_independent_pass")
        self.assertTrue(record["submissions"][0]["assistance"]["previously_seen"])

    def test_error_counts_and_correction_keep_submission_evidence(self):
        attempt = self.mistake()
        wrong = self.rows(attempt)
        wrong[0]["value"] = str(int(wrong[0]["value"]) + 1)
        self.service.submit(attempt["id"], wrong)
        self.service.hint(attempt["id"])
        self.service.submit(attempt["id"], self.rows(attempt))
        state = self.point()
        self.assertEqual(state["code"], "needs_review")
        self.assertEqual(state["independent_count"], 0)
        self.assertEqual(state["non_independent_pass_count"], 1)
        error = state["errors"][0]
        self.assertEqual((error["count"], error["attempt_count"]), (2, 1))
        self.assertEqual({o["submission_number"] for o in error["occurrences"]}, {1, 2})
        submissions = state["evidence"][0]["submissions"]
        self.assertEqual(
            [s["outcome"] for s in submissions],
            ["incorrect", "incorrect", "corrected_pass"],
        )
        self.assertEqual(submissions[0]["assistance"]["hint_count"], 0)
        self.assertEqual(submissions[-1]["assistance"]["hint_count"], 1)
        self.assertEqual(submissions[0]["rows"], wrong)
        self.assertEqual(submissions[-1]["rows"], self.rows(attempt))

    def test_later_assistance_does_not_rewrite_submission_snapshot(self):
        attempt = self.service.start("dh_01")
        self.service.submit(attempt["id"], self.rows(attempt))
        before = self.point()["evidence"][0]["submissions"][0]
        self.service.solution(attempt["id"])
        self.study.question_context(attempt["id"])
        after = self.point()
        self.assertEqual(after["evidence"][0]["submissions"][0], before)
        self.assertEqual(after["code"], "initial")
        self.assertTrue(after["evidence"][0]["activity"]["solution_viewed"])
        self.assertTrue(after["evidence"][0]["activity"]["tutoring_viewed"])

    def test_new_error_changes_state_without_erasing_earlier_success(self):
        for key in ("dh_01", "dh_02"):
            attempt = self.service.start(key)
            self.service.submit(attempt["id"], self.rows(attempt))
        self.assertEqual(self.point()["code"], "independent")
        self.mistake("dh_03")
        self.mistake("dh_04")
        state = self.point()
        self.assertEqual(state["code"], "needs_review")
        self.assertEqual(state["independent_count"], 2)
        self.assertEqual(state["pending_review_count"], 2)
        self.assertEqual(state["errors"][0]["attempt_count"], 2)

    def test_assisted_first_pass_has_its_own_state(self):
        attempt = self.service.start("dh_01")
        self.study.question_context(attempt["id"])
        self.service.submit(attempt["id"], self.rows(attempt))
        state = self.point()
        self.assertEqual(state["code"], "assisted")
        self.assertEqual(state["independent_count"], 0)
        self.assertTrue(
            state["evidence"][0]["submissions"][0]["assistance"]["tutoring_viewed"]
        )

    def test_two_delayed_retests_resolve_pending_state_keep_errors(self):
        with patch("time.time", return_value=1000):
            original = self.mistake()
            self.service.submit(original["id"], self.rows(original))
        for timestamp in (1000 + DAY, 1000 + 4 * DAY):
            with patch("time.time", return_value=timestamp):
                attempt = self.service.start(None, review_id=original["id"])
                self.service.submit(attempt["id"], self.rows(attempt))
        state = self.point()
        self.assertEqual(state["code"], "verified")
        self.assertEqual(state["pending_review_count"], 0)
        self.assertEqual(state["verified_review_count"], 1)
        self.assertEqual(state["last_independent_at"], 1000 + 4 * DAY)
        self.assertEqual(state["errors"][0]["count"], 1)

    def test_restart_and_other_account_isolation(self):
        self.mistake()
        expected = self.point()
        restarted = LearningPathService(
            LearningService(
                LearningStore(self.path, owner_id="alice"), self.service.solver
            )
        )
        actual = next(
            p for p in restarted.dashboard()["points"] if p["title"] == "DH 计算"
        )["learning_state"]
        self.assertEqual(actual, expected)
        bob = LearningPathService(
            LearningService(
                LearningStore(self.path, owner_id="bob"), self.service.solver
            )
        )
        self.assertTrue(
            all(
                not p["learning_state"]["evidence"]
                and not p["learning_state"]["errors"]
                for p in bob.dashboard()["points"]
            )
        )

    def test_legacy_submission_never_invents_assistance_details(self):
        attempt = self.service.start("dh_01")
        self.service.submit(attempt["id"], self.rows(attempt))

        def legacy(state, _existing):
            state["submissions"][0].pop("assistance")
            return "legacy_fixture", {}

        self.service.store.update(attempt["id"], legacy)
        state = self.point()
        self.assertEqual(state["code"], "initial")
        self.assertIsNone(state["evidence"][0]["submissions"][0]["assistance"])
