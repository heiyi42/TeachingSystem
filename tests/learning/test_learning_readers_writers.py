import copy
import unittest
from unittest.mock import patch

from tests.learning import test_learning_path as fixtures
from webapp_core.learning.learning_exercises import EXERCISES
from webapp_core.learning.learning_extended import solve_extended, evaluate_extended
from webapp_core.learning.learning_readers_writers import reader_writer_trace
from webapp_core.learning.learning_path import DAY
from webapp_core.learning.learning_service import ERROR_LABELS


class ReadersWritersTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp

    def rows(self, exercise):
        return [
            {"value": row["value"]}
            for row in solve_extended(exercise)["security_trace"]
        ]

    def test_independent_goldens_for_all_policies_and_scenarios(self):
        common_first = [("R1", "", "进入"), ("R1", "W1", "等待")]
        first = {
            "reader": [
                ("R1,R2", "W1", "进入"),
                ("R2", "W1", "退出"),
                ("W1", "", "退出"),
                ("", "", "退出"),
            ],
            "writer": [
                ("R1", "W1,R2", "等待"),
                ("W1", "R2", "退出"),
                ("W1", "R2", "不可执行"),
                ("R2", "", "退出"),
            ],
            "fifo": [
                ("R1", "W1,R2", "等待"),
                ("W1", "R2", "退出"),
                ("W1", "R2", "不可执行"),
                ("R2", "", "退出"),
            ],
        }
        second = {
            "reader": ("R1,R2", "W2", "退出"),
            "writer": ("W2", "R1,R2", "退出"),
            "fifo": ("R1", "W2,R2", "退出"),
        }
        third = {
            "reader": [
                ("R1,R2,R3", "W2", "退出"),
                ("R2,R3", "W2", "退出"),
                ("R3", "W2", "退出"),
            ],
            "writer": [
                ("W2", "R1,R2,R3", "退出"),
                ("W2", "R1,R2,R3", "不可执行"),
                ("W2", "R1,R2,R3", "不可执行"),
            ],
            "fifo": [
                ("R1,R2", "W2,R3", "退出"),
                ("R2", "W2,R3", "退出"),
                ("W2", "R3", "退出"),
            ],
        }
        for policy in first:
            expected = [
                common_first + first[policy],
                [
                    ("W1", "", "进入"),
                    ("W1", "R1", "等待"),
                    ("W1", "R1,W2", "等待"),
                    ("W1", "R1,W2,R2", "等待"),
                    second[policy],
                ],
                [
                    ("W1", "", "进入"),
                    ("W1", "R1", "等待"),
                    ("W1", "R1,R2", "等待"),
                    ("W1", "R1,R2,W2", "等待"),
                    ("W1", "R1,R2,W2,R3", "等待"),
                ]
                + third[policy],
            ]
            for number, states in enumerate(expected, 1):
                exercise = EXERCISES[f"rw_{policy}_{number:02d}"]
                with self.subTest(key=exercise["id"]):
                    before = copy.deepcopy(exercise)
                    result = reader_writer_trace(exercise["parameters"])
                    self.assertEqual(
                        [
                            (
                                ",".join(s["active"]),
                                ",".join(s["waiting"]),
                                s["outcome"],
                            )
                            for s in result
                        ],
                        states,
                    )
                    self.assertEqual(exercise, before)
                    for state in result:
                        self.assertFalse(set(state["active"]) & set(state["waiting"]))
                        if any(a.startswith("W") for a in state["active"]):
                            self.assertEqual(len(state["active"]), 1)
                    attempt = self.service.start(exercise["id"])
                    rows = [
                        {"value": value or "无"} for state in states for value in state
                    ]
                    self.assertTrue(
                        self.service.submit(attempt["id"], rows)["evaluation"]["passed"]
                    )

    def test_active_readers_unordered_but_waiting_fifo_and_duplicates_rejected(self):
        e = EXERCISES["rw_reader_02"]
        rows = self.rows(e)
        rows[-3]["value"] = "r2， r1"
        self.assertTrue(evaluate_extended(e, rows, ERROR_LABELS)["passed"])
        rows[-3]["value"] = "R1,R2,R2"
        self.assertEqual(
            evaluate_extended(e, rows, ERROR_LABELS)["first_error"]["step"], 13
        )
        rows = self.rows(e)
        rows[10]["value"] = "R2,W2,R1"
        self.assertEqual(
            evaluate_extended(e, rows, ERROR_LABELS)["first_error"]["step"], 11
        )

    def test_duplicate_requests_and_invalid_exits_do_not_change_state(self):
        for policy in ["reader", "writer", "fifo"]:
            states = reader_writer_trace(
                {
                    "policy": policy,
                    "events": [
                        ("R1", "arrive"),
                        ("R1", "arrive"),
                        ("W9", "leave"),
                        ("R1", "leave"),
                        ("R1", "arrive"),
                        ("R1", "leave"),
                    ],
                }
            )
            self.assertEqual(
                [s["outcome"] for s in states],
                ["进入", "不可执行", "不可执行", "退出", "不可执行", "不可执行"],
            )
            self.assertEqual(states[1]["active"], ["R1"])
            self.assertEqual(states[-1]["active"], [])

    def test_each_family_hints_and_two_spaced_reviews(self):
        for policy in ["reader", "writer", "fifo"]:
            e = EXERCISES[f"rw_{policy}_01"]
            point = next(
                p
                for p in self.study.dashboard()["points"]
                if p["title"] == e["algorithm"]
            )
            action = next(a for a in point["actions"] if a["kind"] == "practice")
            with patch("time.time", return_value=1000):
                receipt = self.study.begin_recommendation(point["id"], action["token"])
                origin = self.service.get(receipt["attempt_id"])
                rows = self.rows(origin["exercise"])
                wrong = copy.deepcopy(rows)
                wrong[0]["value"] = "无"
                self.service.submit(origin["id"], wrong)
                self.assertTrue(self.service.hint(origin["id"])["hint"]["text"])
                self.service.submit(origin["id"], rows)
                self.assertTrue(
                    self.service.solution(origin["id"])["solution"]["security_trace"]
                )
            seen = {origin["exercise"]["id"]}
            for day in [1, 4]:
                with patch("time.time", return_value=1000 + DAY * day):
                    review = self.service.start(None, review_id=origin["id"])
                    self.assertEqual(review["exercise"]["algorithm"], e["algorithm"])
                    self.assertNotIn(review["exercise"]["id"], seen)
                    seen.add(review["exercise"]["id"])
                    self.service.submit(review["id"], self.rows(review["exercise"]))
            self.assertTrue(
                next(r for r in self.study.reviews() if r["id"] == origin["id"])[
                    "complete"
                ]
            )
