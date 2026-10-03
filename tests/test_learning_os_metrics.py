import copy
import itertools
import json
import unittest
from unittest.mock import patch

from tests import test_learning_path as fixtures
from webapp_core.learning_exercises import EXERCISES
from webapp_core.learning_extended import solve_extended, evaluate_extended
from webapp_core.learning_path import DAY
from webapp_core.learning_service import ERROR_LABELS, LearningService


class OsMetricsTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp

    def rows(self, exercise):
        return [
            {"value": row["value"]}
            for row in solve_extended(exercise)["security_trace"]
        ]

    def test_scheduling_independent_goldens_including_idle_preemption_and_ties(self):
        golden = [
            [5, 5, 0, 0, 8, 7, 4, 4, 9, 7, 6, 6],
            [4, 2, 0, 0, 12, 4, 0, 0, 13, 4, 3, 3],
            [3, 3, 0, 0, 4, 4, 3, 3, 5, 5, 4, 4],
            [9, 9, 4, 0, 8, 7, 4, 1, 5, 3, 2, 2],
            [4, 2, 0, 0, 13, 5, 1, 0, 11, 2, 1, 1],
            [5, 5, 2, 0, 3, 3, 2, 2, 4, 4, 3, 3],
            [5, 5, 0, 0, 9, 8, 5, 5, 6, 4, 3, 3],
            [4, 2, 0, 0, 12, 4, 0, 0, 13, 4, 3, 3],
            [5, 5, 2, 2, 1, 1, 0, 0, 2, 2, 1, 1],
            [9, 9, 4, 0, 5, 4, 1, 0, 3, 1, 0, 0],
            [4, 2, 0, 0, 13, 5, 1, 0, 10, 1, 0, 0],
            [5, 5, 2, 2, 1, 1, 0, 0, 2, 2, 1, 1],
        ]
        for index, expected in enumerate(golden, 1):
            key = f"schedule_metrics_{index:02d}"
            with self.subTest(key=key):
                exercise = json.loads(json.dumps(EXERCISES[key]))
                self.assertEqual(
                    [int(row["value"]) for row in self.rows(exercise)], expected
                )
                attempt = self.service.start(key)
                restarted = LearningService(self.service.store, self.service.solver)
                rows = [{"value": str(value)} for value in expected]
                self.assertTrue(
                    restarted.submit(attempt["id"], rows)["evaluation"]["passed"]
                )

    def test_request_outcomes_and_no_mutation_or_cumulative_allocation(self):
        for index in range(1, 4):
            exercise = EXERCISES[f"resource_request_{index:02d}"]
            original = copy.deepcopy(exercise)
            rows = self.rows(exercise)
            self.assertEqual(
                [row["value"] for row in rows[::2]],
                [
                    "允许分配",
                    "等待安全状态",
                    "非法请求",
                    "等待资源",
                    "允许分配",
                    "允许分配",
                ],
            )
            self.assertEqual([rows[i]["value"] for i in [3, 5, 7]], ["无"] * 3)
            self.assertEqual(exercise, original)
            self.assertTrue(evaluate_extended(exercise, rows, ERROR_LABELS)["passed"])
            self.assertEqual(exercise, original)

    def test_any_valid_safe_sequence_and_duplicate_missing_names(self):
        exercise = EXERCISES["resource_request_01"]
        for order in itertools.permutations(["P0", "P1", "P2"]):
            rows = self.rows(exercise)
            rows[1]["value"] = "，".join(order).lower()
            result = evaluate_extended(exercise, rows, ERROR_LABELS)
            self.assertEqual(result["passed"], order[0] == "P0")
        for sequence in ["P0,P0,P2", "P0,P1", "P0,P1,P9", "无"]:
            rows = self.rows(exercise)
            rows[1]["value"] = sequence
            self.assertEqual(
                evaluate_extended(exercise, rows, ERROR_LABELS)["first_error"]["step"],
                2,
            )
        rows = self.rows(exercise)
        rows[3]["value"] = "P0,P1,P2"
        self.assertEqual(
            evaluate_extended(exercise, rows, ERROR_LABELS)["first_error"]["step"], 4
        )

    def test_metric_diagnosis_and_integer_validation(self):
        attempt = self.service.start("schedule_metrics_04")
        rows = self.rows(attempt["exercise"])
        rows[3]["value"] = "4"  # 等待时间不能代替响应时间。
        result = self.service.submit(attempt["id"], rows)
        self.assertEqual(result["evaluation"]["first_error"]["step"], 4)
        self.assertEqual(
            result["evaluation"]["first_error"]["error_code"], "wrong_schedule_metrics"
        )
        rows[3]["value"] = "0.5"
        with self.assertRaises(ValueError):
            self.service.submit(attempt["id"], rows)
        self.assertEqual(self.service.get(attempt["id"])["submission_count"], 1)

    def test_new_families_recommendation_and_two_delayed_reviews(self):
        for key in [
            "resource_request_01",
            "schedule_metrics_01",
            "schedule_metrics_04",
            "schedule_metrics_07",
            "schedule_metrics_10",
        ]:
            with self.subTest(key=key):
                point = next(
                    p
                    for p in self.study.dashboard()["points"]
                    if p["title"] == EXERCISES[key]["algorithm"]
                )
                action = next(a for a in point["actions"] if a["kind"] == "practice")
                with patch("time.time", return_value=1000):
                    receipt = self.study.begin_recommendation(
                        point["id"], action["token"]
                    )
                    origin = self.service.get(receipt["attempt_id"])
                    rows = self.rows(origin["exercise"])
                    wrong = copy.deepcopy(rows)
                    wrong[0]["value"] = (
                        "等待资源" if key.startswith("resource") else "999"
                    )
                    self.service.submit(origin["id"], wrong)
                    self.service.submit(origin["id"], rows)
                seen = {origin["exercise"]["id"]}
                for day in [1, 4]:
                    with patch("time.time", return_value=1000 + DAY * day):
                        review = self.service.start(None, review_id=origin["id"])
                        self.assertEqual(
                            review["exercise"]["algorithm"],
                            origin["exercise"]["algorithm"],
                        )
                        self.assertNotIn(review["exercise"]["id"], seen)
                        seen.add(review["exercise"]["id"])
                        self.service.submit(review["id"], self.rows(review["exercise"]))
                entry = next(r for r in self.study.reviews() if r["id"] == origin["id"])
                self.assertTrue(entry["complete"])
