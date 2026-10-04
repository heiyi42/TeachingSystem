import copy
import itertools
import unittest
from unittest.mock import patch

from tests.learning import test_learning_path as fixtures
from webapp_core.learning.learning_exercises import EXERCISES
from webapp_core.learning.learning_pv import PV_KINDS, solve_pv, evaluate_pv
from webapp_core.learning.learning_path import DAY
from webapp_core.learning.learning_service import ERROR_LABELS, LearningService


class PvTrainingTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp

    def rows(self, exercise):
        return [{"value": row["value"]} for row in solve_pv(exercise)["security_trace"]]

    def evaluate(self, key, values):
        return evaluate_pv(
            EXERCISES[key], [{"value": value} for value in values], ERROR_LABELS
        )

    def test_trace_goldens_negative_count_fifo_and_blocked_actor(self):
        golden = [
            [
                "0",
                "无",
                "继续执行",
                "-1",
                "B",
                "阻塞",
                "0",
                "无",
                "唤醒 B",
                "1",
                "无",
                "继续执行",
            ],
            [
                "-1",
                "A",
                "阻塞",
                "-2",
                "A,B",
                "阻塞",
                "-1",
                "B",
                "唤醒 A",
                "0",
                "无",
                "唤醒 B",
                "1",
                "无",
                "继续执行",
            ],
            [
                "1",
                "无",
                "继续执行",
                "0",
                "无",
                "继续执行",
                "-1",
                "C",
                "阻塞",
                "-1",
                "C",
                "不可执行",
                "0",
                "无",
                "唤醒 C",
                "1",
                "无",
                "继续执行",
                "2",
                "无",
                "继续执行",
            ],
        ]
        for number, values in enumerate(golden, 1):
            exercise = EXERCISES[f"pv_trace_{number:02d}"]
            self.assertEqual([r["value"] for r in self.rows(exercise)], values)
            self.assertTrue(self.evaluate(exercise["id"], values)["passed"])
        values = list(golden[1])
        values[4] = "B,A"
        self.assertEqual(self.evaluate("pv_trace_02", values)["first_error"]["step"], 5)

    def test_all_reference_programs_persist_hints_and_solutions(self):
        for exercise in EXERCISES.values():
            if exercise["kind"] not in PV_KINDS:
                continue
            with self.subTest(key=exercise["id"]):
                original = copy.deepcopy(exercise)
                attempt = self.service.start(exercise["id"])
                self.assertTrue(self.service.hint(attempt["id"])["hint"]["text"])
                service = LearningService(self.service.store, self.service.solver)
                result = service.submit(attempt["id"], self.rows(exercise))
                self.assertTrue(result["evaluation"]["passed"], result)
                self.assertFalse(result["first_unassisted_pass"])
                self.assertTrue(
                    service.solution(attempt["id"])["solution"]["security_trace"]
                )
                self.assertEqual(exercise, original)

    def test_mutex_checks_all_operation_orders_not_reference_text(self):
        operations = ["P(MUTEX)", "ENTER", "EXIT", "V(MUTEX)"]
        for permutation in itertools.permutations(operations):
            values = [",".join(permutation)] * 2
            result = self.evaluate("pv_mutex_01", values)
            self.assertEqual(
                result["passed"], list(permutation) == operations, permutation
            )
        bad = self.evaluate("pv_mutex_01", ["P(MUTEX),V(MUTEX),ENTER,EXIT"] * 2)
        self.assertEqual(bad["first_error"]["error_code"], "pv_safety")
        self.assertIn("反例交错", bad["first_error"]["message"])
        self.assertNotIn("correct", bad["row_statuses"])

    def test_buffer_alternative_signal_order_and_mutex_first_deadlock(self):
        for number in range(1, 4):
            key = f"pv_buffer_{number:02d}"
            values = [r["value"] for r in self.rows(EXERCISES[key])]
            alternate = []
            for value in values:
                ops = value.lower().split(",")
                ops[-1], ops[-2] = ops[-2], ops[-1]
                alternate.append("， ".join(ops))
            self.assertTrue(self.evaluate(key, alternate)["passed"])
        values = [r["value"] for r in self.rows(EXERCISES["pv_buffer_01"])]
        values[1] = "P(MUTEX),P(FULL),ENTER,GET,EXIT,V(MUTEX),V(EMPTY)"
        result = self.evaluate("pv_buffer_01", values)
        self.assertEqual(result["first_error"]["error_code"], "pv_deadlock")
        values[1] = "ENTER,GET,EXIT,P(FULL),P(MUTEX),V(MUTEX),V(EMPTY)"
        self.assertEqual(
            self.evaluate("pv_buffer_01", values)["first_error"]["error_code"],
            "pv_safety",
        )

    def test_dining_circular_wait_and_asymmetric_safe_orders(self):
        for number, count in enumerate([3, 4, 5], 1):
            key = f"pv_dining_{number:02d}"
            circular, alternate = [], []
            for i in range(count):
                left, right = i, (i + 1) % count
                circular.append(f"P(F{left}),P(F{right}),EAT,V(F{right}),V(F{left})")
                first, second = (left, right) if i % 2 == 0 else (right, left)
                alternate.append(
                    f"P(F{first}),P(F{second}),EAT,V(F{first}),V(F{second})"
                )
            with self.subTest(count=count):
                self.assertEqual(
                    self.evaluate(key, circular)["first_error"]["error_code"],
                    "pv_deadlock",
                )
                self.assertTrue(self.evaluate(key, alternate)["passed"])

    def test_dining_requires_ownership_and_both_forks_before_eating(self):
        values = [r["value"] for r in self.rows(EXERCISES["pv_dining_01"])]
        for wrong in ["EAT,P(F0),P(F1),V(F1),V(F0)", "V(F0),P(F0),P(F1),EAT,V(F1)"]:
            values[0] = wrong
            self.assertEqual(
                self.evaluate("pv_dining_01", values)["first_error"]["error_code"],
                "pv_safety",
            )
        values[0] = "P(F0),P(F9),EAT,V(F9),V(F0)"
        self.assertEqual(
            self.evaluate("pv_dining_01", values)["first_error"]["error_code"],
            "wrong_pv",
        )

    def test_all_four_families_recommendation_and_two_spaced_reviews(self):
        for key in ["pv_trace_01", "pv_mutex_01", "pv_buffer_01", "pv_dining_01"]:
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
                    wrong[0]["value"] = ""
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
                self.assertTrue(
                    next(r for r in self.study.reviews() if r["id"] == origin["id"])[
                        "complete"
                    ]
                )
