import copy
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from scripts.evaluate_learning_diagnosis import run_evaluation
from webapp_core.learning.learning_routes import learning_blueprint
from webapp_core.learning.learning_service import LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.chat.problem_tutoring_service import ProblemTutoringService


def segments(*items):
    return [{"process": process, "start": str(start), "end": str(end)} for process, start, end in items]


def values(*items):
    return [{"value": str(item)} for item in items]


GOLD = {
    "sjf_01": segments(("P1", 0, 7), ("P3", 7, 8), ("P2", 8, 11)),
    "sjf_02": segments(("P2", 0, 1), ("P3", 1, 2), ("P1", 2, 5)),
    "sjf_03": segments(("P1", 2, 4), ("P2", 8, 12), ("P3", 12, 13)),
    "sjf_04": segments(("P1", 0, 5), ("P2", 5, 8), ("P3", 8, 9)),
    "sjf_05": segments(("P1", 0, 9), ("P4", 9, 10), ("P3", 10, 12), ("P2", 12, 17)),
    "srtf_01": segments(("P1", 0, 2), ("P2", 2, 3), ("P3", 3, 4), ("P2", 4, 6), ("P1", 6, 11)),
    "srtf_02": segments(("P2", 0, 1), ("P3", 1, 2), ("P1", 2, 5)),
    "srtf_03": segments(("P1", 2, 4), ("P2", 8, 9), ("P3", 9, 10), ("P2", 10, 13)),
    "srtf_04": segments(("P1", 0, 5), ("P2", 5, 6), ("P3", 6, 7), ("P2", 7, 9)),
    "srtf_05": segments(("P1", 0, 1), ("P2", 1, 2), ("P3", 2, 4), ("P4", 4, 5), ("P2", 5, 9), ("P1", 9, 17)),
    "dh_01": values(8, 19, 2, 2), "dh_02": values(8, 5, 4, 4),
    "dh_03": values(13, 5, 13, 13), "dh_04": values(14, 18, 18, 18), "dh_05": values(3, 7, 16, 16),
    "access_control_01": values("允许", "拒绝", "拒绝"),
    "access_control_02": values("允许", "拒绝", "拒绝"),
    "access_control_03": values("拒绝", "允许", "拒绝"),
    "access_control_04": values("拒绝", "允许", "允许", "拒绝"),
    "access_control_05": values("允许", "拒绝", "拒绝", "允许"),
    "log_evidence_01": values("L1,L3", "L5", "见失败后的成功"),
    "log_evidence_02": values("L2,L3", "无", "仅见失败"),
    "log_evidence_03": values("L1", "L2", "有成功但未见失败后的成功"),
    "log_evidence_04": values("无", "L1,L3", "有成功但未见失败后的成功"),
    "log_evidence_05": values("无", "无", "无匹配记录"),
}


class StageOneTrainingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.service = LearningService(LearningStore(Path(temporary.name) / "records.sqlite3"), ProblemTutoringService())
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(learning_blueprint(self.service))
        self.client = app.test_client()

    def test_all_fixed_gold_and_answer_privacy(self):
        for key, gold in GOLD.items():
            with self.subTest(key=key):
                attempt = self.service.start(key)
                self.assertIsNone(attempt["solution"])
                self.assertNotIn("security_trace", attempt["exercise"]["parameters"])
                result = self.service.submit(attempt["id"], gold)
                self.assertEqual(result["status"], "passed")
                self.assertTrue(result["first_unassisted_pass"])
                solution = self.service.solution(attempt["id"])["solution"]
                if key.startswith(("sjf", "srtf")):
                    self.assertEqual(solution["timeline"], [{"process": row["process"], "start": int(row["start"]), "end": int(row["end"])} for row in gold])
                else:
                    self.assertEqual([row["value"] for row in solution["security_trace"]], [row["value"] for row in gold])

    def test_each_family_corrects_restores_and_retests(self):
        for family, key, field, wrong, code in [
            ("SJF", "sjf_01", "end", "2", "wrong_time"),
            ("SRTF", "srtf_01", "end", "7", "wrong_time"),
            ("DH 计算", "dh_01", "value", "9", "wrong_public"),
            ("权限矩阵", "access_control_01", "value", "拒绝", "wrong_permission"),
            ("日志证据", "log_evidence_01", "value", "L1", "wrong_evidence"),
        ]:
            with self.subTest(family=family):
                attempt = self.service.start(key)
                rows = copy.deepcopy(GOLD[key])
                rows[0][field] = wrong
                result = self.service.submit(attempt["id"], rows)
                self.assertEqual(result["evaluation"]["first_error"]["error_code"], code)
                self.assertEqual(result["evaluation"]["row_statuses"], ["error"] + ["pending"] * (len(rows) - 1))
                self.service.hint(attempt["id"])
                self.assertEqual(self.service.hint(attempt["id"])["hint"]["level"], 2)
                self.service.save_draft(attempt["id"], GOLD[key])
                restored = LearningService(LearningStore(self.service.store.path), ProblemTutoringService())
                self.assertEqual(restored.get(attempt["id"])["draft"], GOLD[key])
                corrected = self.service.submit(attempt["id"], GOLD[key])
                self.assertFalse(corrected["first_unassisted_pass"])
                retest = self.service.start(None, attempt["id"])
                self.assertEqual(retest["exercise"]["algorithm"], family)
                self.assertNotEqual(retest["exercise"]["id"], key)
                self.assertTrue(self.service.submit(retest["id"], GOLD[retest["exercise"]["id"]])["first_unassisted_pass"])
        self.assertEqual(self.service.progress()["summary"]["independent_retest_passes"], 5)

    def test_log_selection_order_case_boundaries_and_unsupported_claim(self):
        attempt = self.service.start("log_evidence_01")
        alternative = copy.deepcopy(GOLD["log_evidence_01"])
        alternative[0]["value"] = "l3，l1"
        self.assertEqual(self.service.submit(attempt["id"], alternative)["status"], "passed")
        attempt = self.service.start("log_evidence_02")
        rows = copy.deepcopy(GOLD["log_evidence_02"])
        rows[1]["value"] = "L4"
        result = self.service.submit(attempt["id"], rows)
        self.assertEqual(result["evaluation"]["first_error"]["step"], 2)
        rows[1]["value"] = "无"
        rows[2]["value"] = "已经确认入侵"
        response = self.client.post(f"/api/learning/attempts/{attempt['id']}/submit", json={"rows": rows})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.service.get(attempt["id"])["submission_count"], 1)

    def test_shared_key_error_and_default_deny(self):
        attempt = self.service.start("dh_01")
        rows = copy.deepcopy(GOLD["dh_01"])
        rows[2]["value"] = "8"
        result = self.service.submit(attempt["id"], rows)
        self.assertEqual(result["evaluation"]["first_error"]["error_code"], "wrong_shared")
        self.service.hint(attempt["id"])
        self.assertIn("对方公开值", self.service.hint(attempt["id"])["hint"]["text"])
        attempt = self.service.start("access_control_04")
        rows = copy.deepcopy(GOLD["access_control_04"])
        rows[0]["value"] = "允许"
        self.assertEqual(self.service.submit(attempt["id"], rows)["evaluation"]["first_error"]["error_code"], "wrong_permission")

    def test_empty_malformed_and_row_counts_are_rejected(self):
        for key in ("dh_01", "access_control_01", "log_evidence_01"):
            attempt = self.service.start(key)
            self.assertEqual(self.service.submit(attempt["id"], attempt["draft"])["evaluation"]["first_error"]["error_code"], "incomplete")
            with self.assertRaises(ValueError):
                self.service.submit(attempt["id"], GOLD[key][:-1])
            for invalid in ({"value": 3}, {"value": "x" * 121}):
                rows = copy.deepcopy(GOLD[key])
                rows[0] = invalid
                with self.assertRaises(ValueError):
                    self.service.save_draft(attempt["id"], rows)
        for key, invalid in [("dh_01", "-1"), ("dh_01", "1.5"), ("dh_01", "x"), ("access_control_01", "allow"), ("log_evidence_01", "L1,L1"), ("log_evidence_01", "L99")]:
            attempt = self.service.start(key)
            rows = copy.deepcopy(GOLD[key])
            rows[0]["value"] = invalid
            with self.assertRaises(ValueError):
                self.service.submit(attempt["id"], rows)
            self.assertEqual(self.service.get(attempt["id"])["submission_count"], 0)

    def test_independent_reference_evaluation(self):
        result = run_evaluation(Path(__file__).parent.parent / "fixtures/learning_stage1_v1.json")
        self.assertEqual(result["training_overlap"], 0)
        self.assertEqual(set(result["by_algorithm"]), {"SJF", "SRTF", "DH 计算", "权限矩阵", "日志证据"})
        self.assertEqual(result["summary"]["exact_match"]["numerator"], result["summary"]["case_count"])
        self.assertEqual(result["summary"]["unexpected_exceptions"], 0)


if __name__ == "__main__":
    unittest.main()
