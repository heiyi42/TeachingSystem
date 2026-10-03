import unittest
from tests import test_school_permissions as fixtures
from tests import test_school_tasks as task_fixtures
from webapp_core.learning_store import LearningStore


class GradingReviewTests(unittest.TestCase):
    setUpClass = fixtures.SchoolPermissionsTests.__dict__["setUpClass"]
    setUp = fixtures.SchoolPermissionsTests.setUp
    credentials = staticmethod(fixtures.SchoolPermissionsTests.credentials)
    account = fixtures.SchoolPermissionsTests.account
    publish = fixtures.SchoolPermissionsTests.publish
    classroom = fixtures.SchoolPermissionsTests.classroom
    start = fixtures.SchoolPermissionsTests.start
    setup_task = task_fixtures.SchoolTaskTests.setup_task
    start_task = task_fixtures.SchoolTaskTests.start_task
    submit = task_fixtures.SchoolTaskTests.submit

    def appeal(self, a, number=1, client=None):
        return (client or self.student).post(
            f"/api/learning/attempts/{a['id']}/grading-reviews",
            json={"submission_number": number, "reason": "请求核查这一步判定依据"},
        )

    def resolve(self, a, review, decision="pass", client=None):
        return (client or self.teacher).post(
            f"/api/school/grading-reviews/{a['id']}/{review}",
            json={
                "decision": decision,
                "note": "测试复核依据：保留原始判定并核查具体提交",
            },
        )

    def test_class_review_permissions_evidence_and_history(self):
        self.publish()
        c = self.classroom()
        a = self.start(class_id=c["id"])
        self.submit(a, False)
        self.assertEqual(self.appeal(a, client=self.other_student).status_code, 404)
        response = self.appeal(a)
        self.assertEqual(response.status_code, 200, response.json)
        review = response.json["grading_reviews"][0]["id"]
        self.assertEqual(self.appeal(a).status_code, 409)
        self.assertEqual(
            self.resolve(a, review, client=self.other_teacher).status_code, 404
        )
        self.assertEqual(self.resolve(a, review, client=self.student).status_code, 403)
        self.assertEqual(
            self.other_teacher.get("/api/school/grading-reviews").json["items"], []
        )
        changed = self.resolve(a, review)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(changed.json["status"], "passed")
        self.assertTrue(changed.json["first_unassisted_pass"])
        self.assertEqual(self.resolve(a, review).status_code, 409)
        state = LearningStore(self.path).get(a["id"])
        self.assertFalse(state["submissions"][0]["original_evaluation"]["passed"])
        self.assertTrue(state["submissions"][0]["evaluation"]["passed"])
        path = self.student.get("/api/learning/path").json
        point = next(p for p in path["points"] if p["id"] == "cybersec_lab:DH 计算")
        self.assertEqual(point["independent_count"], 1)
        self.assertEqual(path["reviews"], [])

    def test_private_practice_admin_and_earlier_submission(self):
        self.publish()
        a = self.start()
        self.submit(a, False)
        self.submit(a, True)
        response = self.appeal(a)
        review = response.json["grading_reviews"][0]["id"]
        self.assertEqual(self.resolve(a, review).status_code, 404)
        result = self.resolve(a, review, client=self.admin)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["status"], "passed")
        self.assertEqual(result.json["submission_count"], 2)
        self.assertEqual(
            self.admin.get("/api/school/grading-reviews").json["items"][0]["status"],
            "resolved",
        )

    def test_recommendation_baseline_recalculates_after_grading_review(self):
        self.publish()
        attempt = self.start()
        self.submit(attempt, False)
        point_id = "cybersec_lab:DH 计算"
        point = next(
            p
            for p in self.student.get("/api/learning/path").json["points"]
            if p["id"] == point_id
        )
        action = next(a for a in point["actions"] if a["kind"] == "material")
        self.assertEqual(
            self.student.post(
                f"/api/learning/points/{point_id}/recommendations",
                json={"token": action["token"]},
            ).status_code,
            200,
        )
        review = self.appeal(attempt).json["grading_reviews"][0]["id"]
        self.assertEqual(
            self.resolve(attempt, review, client=self.admin).status_code, 200
        )
        point = next(
            p
            for p in self.student.get("/api/learning/path").json["points"]
            if p["id"] == point_id
        )
        self.assertEqual(
            point["followups"][0]["validation"]["baseline"],
            {"attempts": 1, "passed": 1},
        )

    def test_finalized_score_updates_and_quiz_feedback_guard(self):
        task, _, _ = self.setup_task()
        a = self.start_task(task)
        self.submit(a, False)
        final = self.student.post(f"/api/school/tasks/{task['id']}/finalize", json={})
        self.assertEqual(final.json["submission"]["score"], 0)
        response = self.appeal(a)
        review = response.json["grading_reviews"][0]["id"]
        self.assertEqual(self.resolve(a, review).status_code, 200)
        final = self.student.get(f"/api/school/tasks/{task['id']}")
        self.assertEqual(final.json["submission"]["score"], 100)
        self.assertEqual(final.json["submission"]["training_score"], 100)
        self.assertEqual(final.json["attempts"][0]["status"], "passed")

    def test_quiz_hidden_and_maintain_or_fail(self):
        task, _, _ = self.setup_task(kind="quiz")
        a = self.start_task(task)
        self.submit(a, False)
        self.assertEqual(self.appeal(a).status_code, 409)
        self.assertEqual(
            self.student.get(f"/api/learning/attempts/{a['id']}").json[
                "grading_reviews"
            ],
            [],
        )

    def test_uphold_preserves_original_result_and_rejects_bad_input(self):
        self.publish()
        a = self.start()
        self.submit(a, False)
        self.assertEqual(self.appeal(a, number=True).status_code, 400)
        response = self.appeal(a)
        review = response.json["grading_reviews"][0]["id"]
        self.assertEqual(
            self.resolve(a, review, "invented", self.admin).status_code, 400
        )
        result = self.resolve(a, review, "uphold", self.admin)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["status"], "needs_correction")
        state = LearningStore(self.path).get(a["id"])
        self.assertNotIn("original_evaluation", state["submissions"][0])

    def test_fail_revision_and_assisted_evidence_remains_assisted(self):
        self.publish()
        a = self.start()
        self.submit(a, True)
        review = self.appeal(a).json["grading_reviews"][0]["id"]
        result = self.resolve(a, review, "fail", self.admin)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["status"], "needs_correction")
        self.assertFalse(result.json["first_unassisted_pass"])
        path = self.student.get("/api/learning/path").json
        self.assertEqual(len(path["reviews"]), 1)
        self.assertEqual(
            path["reviews"][0]["first_error"]["error_code"], "grading_review"
        )
        b = self.start()
        self.student.post(f"/api/learning/attempts/{b['id']}/hint", json={})
        self.submit(b, False)
        review = self.appeal(b).json["grading_reviews"][0]["id"]
        result = self.resolve(b, review, "pass", self.admin)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertFalse(result.json["first_unassisted_pass"])
