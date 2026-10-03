from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask

from webapp_core.learning_routes import learning_blueprint
from webapp_core.learning_service import LearningService
from webapp_core.learning_store import LearningStore
from webapp_core.problem_tutoring_service import ProblemTutoringService


LRU_01 = [
    {"frames": "1", "event": "fault", "evicted": "—"},
    {"frames": "1, 2", "event": "fault", "evicted": "—"},
    {"frames": "1, 2, 3", "event": "fault", "evicted": "—"},
    {"frames": "1, 2, 3", "event": "hit", "evicted": "—"},
    {"frames": "1, 3, 4", "event": "fault", "evicted": "2"},
    {"frames": "1, 2, 4", "event": "fault", "evicted": "3"},
]
LRU_02 = [
    {"frames": "2", "event": "fault", "evicted": "—"},
    {"frames": "2, 4", "event": "fault", "evicted": "—"},
    {"frames": "2, 4, 6", "event": "fault", "evicted": "—"},
    {"frames": "2, 4, 6", "event": "hit", "evicted": "—"},
    {"frames": "2, 4, 6", "event": "hit", "evicted": "—"},
    {"frames": "2, 4, 8", "event": "fault", "evicted": "6"},
    {"frames": "4, 6, 8", "event": "fault", "evicted": "2"},
]


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "learning.sqlite3"
        self.solver = ProblemTutoringService()
        self.service = LearningService(LearningStore(self.path), self.solver)
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(learning_blueprint(self.service))
        self.client = app.test_client()

    def start(self, exercise_id="lru_01", **extra):
        response = self.client.post("/api/learning/attempts", json={"exercise_id": exercise_id, **extra})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def submit(self, attempt_id, rows):
        response = self.client.post(f"/api/learning/attempts/{attempt_id}/submit", json={"rows": rows})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_lru_correction_retest_and_record_round_trip(self):
        attempt = self.start()
        self.assertIsNone(attempt["solution"])
        self.assertNotIn("trace", json.dumps(attempt))
        wrong = copy.deepcopy(LRU_01)
        wrong[4] = {"frames": "2, 3, 4", "event": "fault", "evicted": "1"}
        wrong[5] = {"frames": "2, 3, 4", "event": "hit", "evicted": "—"}
        result = self.submit(attempt["id"], wrong)
        error = result["evaluation"]["first_error"]
        self.assertEqual((error["step"], error["error_code"]), (5, "wrong_victim"))
        self.assertIn("可能混淆", error["possible_cause"])
        self.assertEqual(result["evaluation"]["row_statuses"], ["correct"] * 4 + ["error", "pending"])
        for level in (1, 2, 2):
            result = self.client.post(f"/api/learning/attempts/{attempt['id']}/hint", json={}).json
            self.assertEqual(result["hint"]["level"], level)
        self.assertEqual(result["hint_count"], 2)
        result = self.submit(attempt["id"], LRU_01)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["first_unassisted_pass"])
        self.assertEqual(result["correction_count"], 1)
        retest = self.start(parent_id=attempt["id"])
        self.assertEqual(retest["exercise"]["id"], "lru_02")
        result = self.submit(retest["id"], LRU_02)
        self.assertTrue(result["first_unassisted_pass"])
        restored = LearningService(LearningStore(self.path), self.solver).progress()
        self.assertEqual(restored["summary"]["corrected_passes"], 1)
        self.assertEqual(restored["summary"]["independent_retest_passes"], 1)
        self.assertEqual(restored["errors"], [{"code": "wrong_victim", "label": "淘汰页面错误", "count": 1}])
        parent = next(record for record in restored["records"] if record["id"] == attempt["id"])
        self.assertTrue(parent["retests"][0]["independent_pass"])
        with sqlite3.connect(self.path) as connection:
            snapshot = json.loads(connection.execute("SELECT data FROM learning_events WHERE kind = 'submitted' ORDER BY id LIMIT 1").fetchone()[0])
        self.assertEqual(snapshot["rows"], wrong)

    def test_frame_order_is_not_an_error(self):
        attempt = self.start()
        rows = copy.deepcopy(LRU_01)
        rows[4]["frames"] = "4，1，3"
        result = self.submit(attempt["id"], rows)
        self.assertTrue(result["first_unassisted_pass"])

    def test_draft_survives_new_service_without_submission(self):
        attempt = self.start()
        response = self.client.put(f"/api/learning/attempts/{attempt['id']}/draft", json={"rows": LRU_01})
        self.assertEqual(response.status_code, 200)
        result = LearningService(LearningStore(self.path), self.solver).get(attempt["id"])
        self.assertEqual(result["draft"], LRU_01)
        self.assertEqual(result["submission_count"], 0)

    def test_solution_and_hint_usage_prevent_independent_credit(self):
        for action in ("hint", "solution"):
            with self.subTest(action=action):
                attempt = self.start("lru_01" if action == "hint" else "lru_02")
                response = self.client.post(f"/api/learning/attempts/{attempt['id']}/{action}", json={})
                self.assertEqual(response.status_code, 200)
                if action == "solution":
                    self.assertIsNotNone(response.json["solution"]["trace"])
                result = self.submit(attempt["id"], LRU_01 if action == "hint" else LRU_02)
                self.assertEqual(result["status"], "passed")
                self.assertFalse(result["first_unassisted_pass"])

    def test_viewing_solution_after_pass_does_not_rewrite_history(self):
        attempt = self.start()
        self.submit(attempt["id"], LRU_01)
        result = self.client.post(f"/api/learning/attempts/{attempt['id']}/solution", json={}).json
        self.assertTrue(result["first_unassisted_pass"])

    def test_repeated_question_is_not_new_independent_work(self):
        first = self.start()
        self.submit(first["id"], LRU_01)
        repeat = self.start()
        result = self.submit(repeat["id"], LRU_01)
        self.assertFalse(result["first_unassisted_pass"])

    def test_solution_in_another_attempt_prevents_independent_credit(self):
        original = self.start()
        another = self.start()
        self.service.solution(another["id"])
        result = self.submit(original["id"], LRU_01)
        self.assertFalse(result["first_unassisted_pass"])

    def test_blank_answers_are_incomplete_and_invalid_numbers_do_not_count(self):
        attempt = self.start()
        result = self.submit(attempt["id"], attempt["draft"])
        self.assertEqual(result["evaluation"]["first_error"]["error_code"], "incomplete")
        for frames in ("1,1", "1,2,3,4", "abc", "1.5", "-1"):
            rows = copy.deepcopy(LRU_01)
            rows[0]["frames"] = frames
            response = self.client.post(f"/api/learning/attempts/{attempt['id']}/submit", json={"rows": rows})
            self.assertEqual(response.status_code, 400, frames)
        self.assertEqual(self.service.get(attempt["id"])["submission_count"], 1)

    def test_malformed_requests_and_missing_attempt(self):
        for payload in ([], {"exercise_id": []}, {"exercise_id": "missing"}, {"parent_id": {}}):
            self.assertEqual(self.client.post("/api/learning/attempts", json=payload).status_code, 400)
        self.assertEqual(self.client.get("/api/learning/attempts/missing").status_code, 404)
        attempt = self.start()
        for payload in ({}, {"rows": []}, {"rows": [None] * 6}):
            self.assertEqual(self.client.post(f"/api/learning/attempts/{attempt['id']}/submit", json=payload).status_code, 400)
        self.assertEqual(self.client.post(f"/api/learning/attempts/{attempt['id']}/hint", json={"step": True}).status_code, 400)
        self.assertEqual(self.client.post("/api/learning/attempts", json={"exercise_id": "x" * 70000}).status_code, 413)

    def test_passed_attempt_cannot_be_overwritten_or_retested_early(self):
        attempt = self.start()
        self.assertEqual(self.client.post("/api/learning/attempts", json={"parent_id": attempt["id"]}).status_code, 409)
        self.submit(attempt["id"], LRU_01)
        for method, action in (("post", "submit"), ("put", "draft"), ("post", "hint")):
            response = getattr(self.client, method)(f"/api/learning/attempts/{attempt['id']}/{action}", json={"rows": LRU_01})
            self.assertEqual(response.status_code, 409)

    def test_concurrent_hints_do_not_lose_events_or_inflate_unique_hint_count(self):
        attempt = self.start()
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: self.service.hint(attempt["id"]), range(4)))
        self.assertEqual(self.service.get(attempt["id"])["hint_count"], 2)

    def test_fifo_hit_keeps_insertion_order(self):
        attempt = self.start("fifo_01")
        wrong = self.submit(attempt["id"], LRU_01)
        self.assertEqual(wrong["evaluation"]["first_error"]["step"], 5)
        self.assertEqual(wrong["evaluation"]["first_error"]["error_code"], "wrong_victim")
        self.assertIsNone(wrong["evaluation"]["first_error"]["possible_cause"])
        rows = copy.deepcopy(LRU_01)
        rows[4] = {"frames": "2, 3, 4", "event": "fault", "evicted": "1"}
        rows[5] = {"frames": "2, 3, 4", "event": "hit", "evicted": "—"}
        self.assertEqual(self.submit(attempt["id"], rows)["status"], "passed")

    def test_fifo_zero_page_and_two_frames(self):
        attempt = self.start("fifo_03")
        rows = [
            {"frames": "0", "event": "fault", "evicted": "—"},
            {"frames": "0,1", "event": "fault", "evicted": "—"},
            {"frames": "0,1", "event": "hit", "evicted": "—"},
            {"frames": "1,2", "event": "fault", "evicted": "0"},
            {"frames": "1,2", "event": "hit", "evicted": "—"},
            {"frames": "2,3", "event": "fault", "evicted": "1"},
            {"frames": "1,3", "event": "fault", "evicted": "2"},
        ]
        self.assertTrue(self.submit(attempt["id"], rows)["first_unassisted_pass"])

    def test_fcfs_order_idle_time_and_missing_segment(self):
        attempt = self.start("fcfs_03")
        rows = [
            {"process": "P1", "start": "1", "end": "3"},
            {"process": "P2", "start": "6", "end": "9"},
            {"process": "P3", "start": "9", "end": "10"},
        ]
        missing = self.submit(attempt["id"], rows[:2])
        self.assertEqual(missing["evaluation"]["first_error"]["step"], 3)
        self.assertEqual(missing["evaluation"]["first_error"]["error_code"], "incomplete")
        wrong_time = copy.deepcopy(rows)
        wrong_time[1]["start"] = "3"
        result = self.submit(attempt["id"], wrong_time)
        self.assertEqual(result["evaluation"]["first_error"]["error_code"], "wrong_time")
        self.assertEqual(result["evaluation"]["row_statuses"], ["correct", "error", "pending"])
        self.assertEqual(self.submit(attempt["id"], rows)["status"], "passed")
        solution = self.service.solution(attempt["id"])["solution"]
        self.assertEqual(solution["metrics"]["P2"]["waiting"], 0)
        self.assertEqual(solution["metrics"]["P3"]["turnaround"], 3)

    def test_fcfs_simultaneous_arrivals_use_process_number(self):
        attempt = self.start("fcfs_02")
        rows = [
            {"process": "P1", "start": "0", "end": "4"},
            {"process": "P2", "start": "4", "end": "6"},
            {"process": "P3", "start": "6", "end": "9"},
        ]
        wrong_order = copy.deepcopy(rows)
        wrong_order[0]["process"] = "P2"
        self.assertEqual(self.submit(attempt["id"], wrong_order)["evaluation"]["first_error"]["error_code"], "wrong_order")
        extra = rows + [{"process": "P1", "start": "9", "end": "10"}]
        self.assertEqual(self.submit(attempt["id"], extra)["evaluation"]["first_error"]["error_code"], "extra_segment")
        self.assertEqual(self.submit(attempt["id"], rows)["status"], "passed")

    def test_schedule_rejects_invalid_times_without_recording_submission(self):
        attempt = self.start("fcfs_01")
        for start, end in (("-1", "2"), ("NaN", "2"), ("1.5", "2"), ("2", "2"), ("3", "2")):
            response = self.client.post(f"/api/learning/attempts/{attempt['id']}/submit", json={"rows": [{"process": "P1", "start": start, "end": end}]})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.service.get(attempt["id"])["submission_count"], 0)

    def test_rr_boundaries_short_slices_and_idle(self):
        cases = {
            "rr_01": [("P1", 0, 2), ("P2", 2, 4), ("P3", 4, 5), ("P1", 5, 7), ("P2", 7, 8), ("P1", 8, 9)],
            "rr_02": [("P1", 0, 2), ("P2", 2, 4), ("P3", 4, 6), ("P1", 6, 8), ("P3", 8, 9)],
            "rr_03": [("P1", 1, 3), ("P2", 6, 8), ("P3", 8, 9), ("P2", 9, 10)],
            "rr_04": [("P1", 0, 2), ("P2", 2, 4), ("P1", 4, 6), ("P3", 6, 8), ("P1", 8, 9), ("P3", 9, 10)],
            "rr_05": [("P1", 0, 1), ("P2", 3, 6), ("P3", 6, 8), ("P2", 8, 9)],
        }
        for exercise_id, expected in cases.items():
            with self.subTest(exercise=exercise_id):
                attempt = self.start(exercise_id)
                rows = [{"process": name, "start": str(start), "end": str(end)} for name, start, end in expected]
                self.assertTrue(self.submit(attempt["id"], rows)["first_unassisted_pass"])
                solution = self.service.solution(attempt["id"])["solution"]
                self.assertEqual(solution["quantum"], 3 if exercise_id == "rr_05" else 2)

    def test_rr_arrivals_at_quantum_end_enter_before_running_process(self):
        attempt = self.start("rr_04")
        wrong = [
            {"process": "P1", "start": "0", "end": "2"},
            {"process": "P1", "start": "2", "end": "4"},
        ]
        result = self.submit(attempt["id"], wrong)
        self.assertEqual(result["evaluation"]["first_error"]["step"], 2)
        self.assertEqual(result["evaluation"]["first_error"]["error_code"], "wrong_order")
        self.service.hint(attempt["id"])
        hint = self.service.hint(attempt["id"])["hint"]
        self.assertIn("上一段结束于 2", hint["text"])
        self.assertIn("时间片为 2", hint["text"])


if __name__ == "__main__":
    unittest.main()
