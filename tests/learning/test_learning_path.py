from __future__ import annotations

import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from webapp_core.learning.learning_courses import COURSES, course_chapters
from webapp_core.learning.learning_exercises import EXERCISES
from webapp_core.learning.learning_path import DAY, LearningPathService
from webapp_core.learning.learning_service import LearningConflict, LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.chat.problem_tutoring_service import ProblemTutoringService
from tests.school import test_school_permissions as fixtures
from tests.school import test_school_tasks as task_fixtures


class LearningPathTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "learning.sqlite3"
        self.service = LearningService(
            LearningStore(self.path, owner_id="alice"), ProblemTutoringService()
        )
        self.study = LearningPathService(self.service)

    def rows(self, attempt):
        exercise = attempt["exercise"]
        result = self.service._solve(exercise)
        trace = (
            result["c_trace"]
            if exercise["kind"] == "c_trace"
            else result["security_trace"]
        )
        return [{"value": str(row["value"])} for row in trace]

    def mistake(self, exercise_id="dh_01"):
        attempt = self.service.start(exercise_id)
        wrong = self.rows(attempt)
        wrong[0]["value"] = str(int(wrong[0]["value"]) + 1)
        self.service.submit(attempt["id"], wrong)
        return attempt

    def test_recommendation_tracks_correction_then_unsubmitted_new_question(self):
        from tests.learning.test_learning_service import LRU_01, LRU_02

        original = self.service.start("lru_01")
        wrong = copy.deepcopy(LRU_01)
        wrong[-1]["evicted"] = "2"
        self.service.submit(original["id"], wrong)

        def action():
            point = next(p for p in self.study.dashboard()["points"] if p["id"] == "operating_systems:LRU")
            return point["actions"][0]

        correction = action()
        self.assertEqual(correction["title"], "订正原题")
        self.assertEqual(correction["attempt_id"], original["id"])
        self.assertEqual(correction["submission_count"], 1)
        self.service.hint(original["id"], None)
        self.service.submit(original["id"], LRU_01)
        new = self.service.start("lru_02")
        recommendation = action()
        self.assertEqual(recommendation["title"], "继续新题作答")
        self.assertEqual(recommendation["exercise_id"], "lru_02")
        self.assertEqual(recommendation["submission_count"], 0)
        self.assertNotIn("核对错误", recommendation["reason"])
        opened = self.study.begin_recommendation("operating_systems:LRU", recommendation["token"])
        self.assertEqual(opened["attempt_id"], new["id"])
        self.service.hint(new["id"], None)
        self.assertEqual(action()["title"], "继续辅助练习")
        self.service.submit(new["id"], LRU_02)
        repeat = self.service.start("lru_01")
        self.assertEqual(action()["title"], "继续重复练习")
        self.assertEqual(action()["attempt_id"], repeat["id"])

    def test_all_three_course_materials_and_source_ranges(self):
        for subject in COURSES:
            for chapter in course_chapters(subject):
                material = self.study.material(chapter["id"])
                self.assertEqual(material["subject_id"], subject)
                self.assertTrue(material["text"])
                self.assertEqual(len(material["sha256"]), 64)
                self.assertLessEqual(material["end_line"] - material["start_line"], 119)
        material = self.study.material("C_program_04", query="switch")
        self.assertIsNotNone(material["match_line"])
        self.assertIn("switch", material["text"])
        second = self.study.material("C_program_04", material["end_line"] + 1)
        self.assertGreater(second["start_line"], material["end_line"])
        with self.assertRaises(LookupError):
            self.study.material("../../.env")
        with self.assertRaises(ValueError):
            self.study.material("C_program_04", 0)

    def test_reading_is_owned_persistent_reversible_and_not_independent(self):
        self.study.mark_reading("C_program_04", True)
        restarted = LearningPathService(
            LearningService(
                LearningStore(self.path, owner_id="alice"), self.service.solver
            )
        )
        course = next(
            c for c in restarted.dashboard()["courses"] if c["id"] == "C_program"
        )
        self.assertEqual(
            (course["read_chapters"], course["independent_chapters"]), (1, 0)
        )
        bob = LearningStore(self.path, owner_id="bob")
        self.assertEqual(bob.reading(), {})
        self.study.mark_reading("C_program_04", False)
        self.assertEqual(self.service.store.reading(), {})
        with self.assertRaises(ValueError):
            self.study.mark_reading("C_program_04", 1)

    def test_delayed_review_boundaries_restart_and_two_independent_checks(self):
        with patch("time.time", return_value=1000):
            origin = self.mistake()
            self.service.submit(origin["id"], self.rows(origin))
            entry = self.study.reviews()[0]
            self.assertTrue(entry["corrected"])
            self.assertFalse(entry["complete"])
            self.assertEqual(entry["due_at"], 1000 + DAY)
        with patch("time.time", return_value=1000 + DAY - 1):
            with self.assertRaises(LearningConflict):
                self.service.start(None, review_id=origin["id"])
        with patch("time.time", return_value=1000 + DAY):
            review = self.service.start(None, review_id=origin["id"])
            self.assertEqual(review["exercise"]["id"], "dh_02")
            self.assertEqual(review["review_id"], origin["id"])
            self.assertEqual(
                self.service.start(None, review_id=origin["id"])["id"], review["id"]
            )
            self.service.submit(review["id"], self.rows(review))
            self.assertEqual(self.study.reviews()[0]["stage"], 1)
        with patch("time.time", return_value=1000 + 4 * DAY):
            service = LearningService(
                LearningStore(self.path, owner_id="alice"), self.service.solver
            )
            review = service.start(None, review_id=origin["id"])
            service.submit(review["id"], self.rows(review))
            entry = LearningPathService(service).reviews()[0]
            self.assertTrue(entry["complete"])
            self.assertIsNone(entry["due_at"])
            self.assertEqual(entry["stage"], 2)
            self.assertEqual(
                service.progress()["summary"]["independent_retest_passes"], 2
            )
            with self.assertRaises(LearningConflict):
                service.start(None, review_id=origin["id"])

    def test_review_assistance_and_correction_do_not_advance_schedule(self):
        with patch("time.time", return_value=1000):
            origin = self.mistake("c_loop_01")
        with patch("time.time", return_value=1000 + DAY):
            review = self.service.start(None, review_id=origin["id"])
            self.service.hint(review["id"])
            self.service.submit(review["id"], self.rows(review))
            entry = self.study.reviews()[0]
            self.assertEqual(entry["stage"], 0)
            self.assertEqual(entry["due_at"], 1000 + 2 * DAY)
            self.assertFalse(entry["checks"][0]["independent_pass"])
        with patch("time.time", return_value=1000 + 2 * DAY):
            review = self.service.start(None, review_id=origin["id"])
            wrong = self.rows(review)
            wrong[0]["value"] = "999"
            self.service.submit(review["id"], wrong)
            self.service.submit(review["id"], self.rows(review))
            entry = self.study.reviews()[0]
            self.assertEqual(entry["stage"], 0)
            self.assertIsNone(entry["recommendation"])
        with patch("time.time", return_value=1000 + 3 * DAY):
            with self.assertRaises(LearningConflict):
                self.service.start(None, review_id=origin["id"])

    def test_review_concurrency_ownership_and_catalog_withdrawal(self):
        with patch("time.time", return_value=1000):
            origin = self.mistake()
        bob = LearningService(
            LearningStore(self.path, owner_id="bob"), self.service.solver
        )
        with self.assertRaises(LookupError):
            bob.start(None, review_id=origin["id"])
        self.assertEqual(LearningPathService(bob).reviews(), [])
        with patch("time.time", return_value=1000 + DAY):
            self.service.catalog = {"dh_01": EXERCISES["dh_01"]}
            with self.assertRaises(LearningConflict):
                self.service.start(None, review_id=origin["id"])
            self.service.catalog = EXERCISES
            with ThreadPoolExecutor(max_workers=3) as pool:
                responses = list(
                    pool.map(
                        lambda _: self.service.start(None, review_id=origin["id"]),
                        range(3),
                    )
                )
            self.assertEqual(len({r["id"] for r in responses}), 1)
            self.assertEqual(len(self.service.store.list_attempts()), 2)

    def test_knowledge_evidence_all_courses_and_repeated_ids(self):
        for exercise_id in ("c_loop_01", "lru_01", "dh_01"):
            attempt = self.service.start(exercise_id)
            if exercise_id == "lru_01":
                from tests.learning.test_learning_service import LRU_01

                rows = copy.deepcopy(LRU_01)
            else:
                rows = self.rows(attempt)
            self.service.submit(attempt["id"], rows)
        repeated = self.service.start("dh_01")
        self.service.submit(repeated["id"], self.rows(repeated))
        dashboard = self.study.dashboard()
        self.assertEqual({c["id"] for c in dashboard["courses"]}, set(COURSES))
        dh = next(p for p in dashboard["points"] if p["title"] == "DH 计算")
        self.assertEqual(dh["independent_count"], 1)
        self.assertEqual(dh["status"], "初步独立证据")
        self.assertEqual(dh["exercise_id"], "dh_02")
        self.assertTrue(
            all(c["independent_chapters"] == 1 for c in dashboard["courses"])
        )

    def test_question_context_marks_assistance_without_regrading_history(self):
        attempt = self.service.start("dh_01")
        context = self.study.question_context(attempt["id"])
        self.assertEqual(context["subject_id"], "cybersec_lab")
        self.assertIn("资料来源", context["prompt"])
        result = self.service.submit(attempt["id"], self.rows(attempt))
        self.assertFalse(result["first_unassisted_pass"])
        self.assertTrue(result["tutoring_viewed"])
        fresh = self.service.start("dh_02")
        self.service.submit(fresh["id"], self.rows(fresh))
        self.study.question_context(fresh["id"])
        self.assertTrue(self.service.get(fresh["id"])["first_unassisted_pass"])


class LearningPathPermissionsTests(unittest.TestCase):
    setUpClass = fixtures.SchoolPermissionsTests.__dict__["setUpClass"]

    def setUp(self):
        fixtures.SchoolPermissionsTests.setUp(self)
        persistence = patch.object(
            self.webapp.get_store(self.app), "persist_sessions_safely"
        )
        persistence.start()
        self.addCleanup(persistence.stop)

    credentials = staticmethod(fixtures.SchoolPermissionsTests.credentials)
    account = fixtures.SchoolPermissionsTests.account
    publish = fixtures.SchoolPermissionsTests.publish
    classroom = fixtures.SchoolPermissionsTests.classroom
    setup_task = task_fixtures.SchoolTaskTests.setup_task
    start_task = task_fixtures.SchoolTaskTests.start_task
    submit = task_fixtures.SchoolTaskTests.submit
    age = task_fixtures.SchoolTaskTests.age

    def test_quiz_hidden_in_path_reviews_context_and_stream_until_release(self):
        task, _, _ = self.setup_task("quiz")
        attempt = self.start_task(task)
        self.submit(attempt, False)
        dashboard = self.student.get("/api/learning/path").json
        self.assertEqual(dashboard["reviews"], [])
        point = next(p for p in dashboard["points"] if p["title"] == "DH 计算")
        self.assertEqual(point["submitted_count"], 0)
        self.assertEqual(point["learning_state"]["evidence"], [])
        self.assertEqual(point["learning_state"]["errors"], [])
        for suffix in (
            f"reviews/{attempt['id']}/start",
            f"attempts/{attempt['id']}/question-context",
        ):
            response = self.student.post(f"/api/learning/{suffix}", json={})
            self.assertEqual(response.status_code, 409, response.json)
        chat = self.student.post("/api/chats", json={"mode": "naive"}).json
        response = self.student.post(
            f"/api/chats/{chat['chat_id']}/messages/stream",
            json={"message": "解释", "learning_attempt_id": attempt["id"]},
        )
        self.assertEqual(response.status_code, 409)
        self.age(task, due_at=0)
        dashboard = self.student.get("/api/learning/path").json
        self.assertEqual(len(dashboard["reviews"]), 1)
        point = next(p for p in dashboard["points"] if p["title"] == "DH 计算")
        self.assertEqual(
            point["learning_state"]["evidence"][0]["attempt_id"], attempt["id"]
        )
        context = self.student.post(
            f"/api/learning/attempts/{attempt['id']}/question-context", json={}
        )
        self.assertEqual(context.status_code, 200, context.json)
        grade = self.student.get(f"/api/school/tasks/{task['id']}").json["submission"][
            "score"
        ]
        self.assertEqual(grade, 0)

    def test_api_auth_ownership_reading_validation_and_demo_isolation(self):
        self.publish()
        attempt = fixtures.SchoolPermissionsTests.start(self)
        self.submit(attempt, False)
        anonymous = self.app.test_client()
        for endpoint in ("path", "chapters/cybersec_lab_02/material"):
            self.assertEqual(
                anonymous.get(f"/api/learning/{endpoint}").status_code, 401
            )
        for client in (self.other_student, self.other_teacher):
            self.assertEqual(
                client.post(
                    f"/api/learning/reviews/{attempt['id']}/start", json={}
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    f"/api/learning/attempts/{attempt['id']}/question-context", json={}
                ).status_code,
                404,
            )
        self.assertEqual(
            self.student.put(
                "/api/learning/chapters/cybersec_lab_02/reading", json={"read": True}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.student.put(
                "/api/learning/chapters/cybersec_lab_02/reading", json={"read": "true"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.student.get(
                "/api/learning/chapters/cybersec_lab_02/material?start=no"
            ).status_code,
            400,
        )
        demo = anonymous.post("/api/learning/demo", json={}).json["demo_id"]
        demo_dashboard = anonymous.get(f"/api/learning-demo/{demo}/path").json
        self.assertEqual(demo_dashboard["reviews"], [])
        self.assertEqual(sum(c["read_chapters"] for c in demo_dashboard["courses"]), 0)
        self.assertEqual(
            sum(
                c["read_chapters"]
                for c in self.other_student.get("/api/learning/path").json["courses"]
            ),
            0,
        )

    def test_stream_context_is_server_owned_and_subject_locked(self):
        self.publish()
        attempt = fixtures.SchoolPermissionsTests.start(self)
        chat = self.student.post("/api/chats", json={"mode": "naive"}).json
        endpoint = f"/api/chats/{chat['chat_id']}/messages/stream"
        self.assertEqual(
            self.other_student.post(
                endpoint, json={"message": "test", "learning_attempt_id": attempt["id"]}
            ).status_code,
            404,
        )
        chat_service = self.webapp.get_chat_service(self.app)
        with patch.object(
            chat_service,
            "build_chat_message_stream_handler",
            return_value=(lambda: iter([]), None),
        ) as stream:
            response = self.student.post(
                endpoint,
                json={
                    "message": "请解释概念",
                    "subjects": ["C_program"],
                    "learning_attempt_id": attempt["id"],
                },
            )
            self.assertEqual(response.status_code, 200)
            response.close()
            sent = stream.call_args.args[1]
            self.assertEqual(sent["subjects"], ["cybersec_lab"])
            self.assertIn("资料来源", sent["message"])
            self.assertIn("我的已保存作答", sent["message"])
        self.assertEqual(
            self.student.post(
                endpoint, json={"message": "", "learning_attempt_id": attempt["id"]}
            ).status_code,
            400,
        )


if __name__ == "__main__":
    unittest.main()
