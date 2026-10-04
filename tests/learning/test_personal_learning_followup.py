import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.learning import test_learning_path as fixtures
from webapp_core.learning.learning_path import LearningPathService, DAY
from webapp_core.learning.learning_service import LearningService, LearningConflict
from webapp_core.learning.learning_store import LearningStore


class PersonalFollowupTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.LearningPathTests.setUp
    rows = fixtures.LearningPathTests.rows
    mistake = fixtures.LearningPathTests.mistake

    def point(self, point_id="cybersec_lab:DH 计算"):
        return next(p for p in self.study.dashboard()["points"] if p["id"] == point_id)

    def begin(self, kind, point_id="cybersec_lab:DH 计算"):
        action = next(a for a in self.point(point_id)["actions"] if a["kind"] == kind)
        return self.study.begin_recommendation(point_id, action["token"])

    def feedback(self, record):
        return next(f for f in self.point()["followups"] if f["id"] == record["id"])

    def test_reading_completion_is_not_mastery_and_survives_unmark_restart(self):
        with patch("time.time", return_value=1000):
            record = self.begin("material")
            self.assertEqual(self.feedback(record)["outcome"], "pending")
        with patch("time.time", return_value=1001):
            self.study.mark_reading(record["action"]["chapter_id"], True)
            self.assertEqual(self.feedback(record)["outcome"], "awaiting_validation")
            self.study.mark_reading(record["action"]["chapter_id"], False)
            self.assertEqual(self.point()["actions"][0]["kind"], "practice")
        self.study = LearningPathService(
            LearningService(
                LearningStore(self.path, owner_id="alice"), self.service.solver
            )
        )
        self.assertEqual(self.feedback(record)["completed_at"], 1001)
        self.assertEqual(self.feedback(record)["after"]["independent_count"], 0)

    def test_open_or_draft_not_complete_independent_pass_updates_evidence(self):
        record = self.begin("practice")
        self.assertIsNone(self.feedback(record)["completed_at"])
        attempt = self.service.get(record["attempt_id"])
        self.service.submit(attempt["id"], self.rows(attempt))
        feedback = self.feedback(record)
        self.assertEqual(feedback["outcome"], "independent_evidence")
        self.assertEqual(feedback["before"]["independent_count"], 0)
        self.assertEqual(feedback["after"]["independent_count"], 1)
        self.assertEqual(feedback["evidence_ids"], [attempt["id"]])

    def test_correction_does_not_clear_review_or_claim_independence(self):
        origin = self.mistake()
        record = self.begin("continue")
        self.assertEqual(self.feedback(record)["outcome"], "pending")
        self.service.submit(origin["id"], self.rows(origin))
        feedback = self.feedback(record)
        self.assertEqual(feedback["outcome"], "assisted")
        self.assertEqual(feedback["after"]["independent_count"], 0)
        self.assertEqual(feedback["after"]["pending_review_count"], 1)
        self.assertEqual(self.point()["actions"][0]["kind"], "material")

    def test_repeated_error_and_later_recovery_both_remain_visible(self):
        origin = self.mistake()
        record = self.begin("continue")
        wrong = self.rows(origin)
        wrong[0]["value"] = "0"
        self.service.submit(origin["id"], wrong)
        feedback = self.feedback(record)
        self.assertEqual(feedback["outcome"], "needs_work")
        self.assertTrue(feedback["repeated_errors"])
        material = next(a for a in self.point()["actions"] if a["kind"] == "material")
        self.assertIn("推荐后的作答仍有错误", material["reason"])
        self.service.submit(origin["id"], self.rows(origin))
        self.assertEqual(self.feedback(record)["outcome"], "assisted")
        self.assertTrue(self.feedback(record)["repeated_errors"])

    def test_material_only_observes_submissions_after_completion(self):
        record = self.begin("material")
        attempt = self.service.start("dh_01")
        self.service.submit(attempt["id"], self.rows(attempt))
        self.assertEqual(self.feedback(record)["outcome"], "pending")
        self.study.mark_reading(record["action"]["chapter_id"], True)
        self.assertEqual(self.feedback(record)["outcome"], "awaiting_validation")
        fresh = self.service.start("dh_02")
        self.service.submit(fresh["id"], self.rows(fresh))
        self.assertEqual(self.feedback(record)["evidence_ids"], [fresh["id"]])

    async def test_failed_ai_does_not_complete_success_does_not_claim_mastery(self):
        record = self.begin("explain")
        model = SimpleNamespace(
            model_name="test-model", ainvoke=AsyncMock(side_effect=TimeoutError())
        )
        with self.assertRaises(TimeoutError):
            await self.study.explain(self.point()["id"], model)
        self.assertIsNone(self.feedback(record)["completed_at"])
        model.ainvoke = AsyncMock(
            return_value=SimpleNamespace(content="请核对参数与取模步骤。")
        )
        await self.study.explain(self.point()["id"], model)
        self.assertEqual(self.feedback(record)["outcome"], "awaiting_validation")
        self.assertEqual(self.point()["actions"][0]["kind"], "practice")

    def test_concurrent_retry_starts_one_attempt_and_one_record(self):
        action = next(a for a in self.point()["actions"] if a["kind"] == "practice")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: self.study.begin_recommendation(
                        self.point()["id"], action["token"]
                    ),
                    range(2),
                )
            )
        self.assertEqual(results[0]["attempt_id"], results[1]["attempt_id"])
        self.assertEqual(len(self.service.store.list_attempts()), 1)
        self.assertEqual(len(self.service.store.recommendations()), 1)

    def test_unaccepted_stale_token_and_other_owner_rejected(self):
        action = next(a for a in self.point()["actions"] if a["kind"] == "practice")
        self.mistake()
        with self.assertRaises(LearningConflict):
            self.study.begin_recommendation(self.point()["id"], action["token"])
        record = self.begin("continue")
        bob = LearningPathService(
            LearningService(
                LearningStore(self.path, owner_id="bob"), self.service.solver
            )
        )
        with self.assertRaises(LearningConflict):
            bob.begin_recommendation(self.point()["id"], record["id"])
        self.assertEqual(bob.learning.store.recommendations(), [])

    def test_new_error_after_independent_pass_changes_outcome(self):
        record = self.begin("practice")
        first = self.service.get(record["attempt_id"])
        self.service.submit(first["id"], self.rows(first))
        self.assertEqual(self.feedback(record)["outcome"], "independent_evidence")
        self.mistake("dh_02")
        self.assertEqual(self.feedback(record)["outcome"], "needs_work")
        self.assertEqual(self.point()["actions"][0]["kind"], "continue")

    def test_unfinished_recommendation_does_not_claim_unrelated_pass(self):
        record = self.begin("practice")
        other = self.service.start("dh_02")
        self.service.submit(other["id"], self.rows(other))
        feedback = self.feedback(record)
        self.assertEqual(feedback["outcome"], "pending")
        self.assertEqual(
            feedback["validation"]["followup"], {"attempts": 0, "passed": 0}
        )
        self.assertEqual(feedback["evidence_ids"], [])

    def test_baseline_failures_and_corrections_are_not_counted_as_independent_success(
        self,
    ):
        with patch("time.time", return_value=1000):
            origin = self.mistake()
        with patch("time.time", return_value=1001):
            record = self.begin("continue")
            self.service.submit(origin["id"], self.rows(origin))
            validation = self.feedback(record)["validation"]
        self.assertEqual(validation["baseline"], {"attempts": 1, "passed": 0})
        self.assertEqual(validation["followup"], {"attempts": 0, "passed": 0})
        self.assertFalse(validation["checks"][0]["independent"])
        with patch("time.time", return_value=1002):
            self.mistake("dh_02")
        validation = self.feedback(record)["validation"]
        self.assertEqual(validation["followup"], {"attempts": 1, "passed": 0})
        self.assertEqual(validation["retention"], "needs_work")

    def test_delayed_validation_requires_new_question_started_after_interval(self):
        with patch("time.time", return_value=1000):
            record = self.begin("practice")
            attempt = self.service.get(record["attempt_id"])
            self.service.submit(attempt["id"], self.rows(attempt))
            early = self.service.start("dh_02")
        with patch("time.time", return_value=1000 + DAY):
            self.service.submit(early["id"], self.rows(early))
            self.assertEqual(
                self.feedback(record)["validation"]["retention"], "awaiting_delayed"
            )
            fresh = self.service.start("dh_03")
            self.service.submit(fresh["id"], self.rows(fresh))
            validation = self.feedback(record)["validation"]
        self.assertEqual(validation["retention"], "observed")
        self.assertEqual(validation["delayed_evidence_ids"], [fresh["id"]])
        self.assertEqual(validation["followup"], {"attempts": 3, "passed": 3})

    def test_hint_and_repeat_after_interval_do_not_create_delayed_evidence(self):
        with patch("time.time", return_value=1000):
            record = self.begin("practice")
            original = self.service.get(record["attempt_id"])
            self.service.submit(original["id"], self.rows(original))
        with patch("time.time", return_value=1000 + DAY):
            hinted = self.service.start("dh_02")
            self.service.hint(hinted["id"])
            self.service.submit(hinted["id"], self.rows(hinted))
            repeated = self.service.start(original["exercise"]["id"])
            self.service.submit(repeated["id"], self.rows(repeated))
        validation = self.feedback(record)["validation"]
        self.assertEqual(validation["followup"], {"attempts": 1, "passed": 1})
        self.assertEqual(validation["delayed_evidence_ids"], [])
        self.assertEqual(validation["retention"], "awaiting_delayed")

    def test_overlapping_recommendations_are_disclosed_and_plan_reads_validation(self):
        from webapp_core.learning.learning_plan import LearningPlanService

        with patch("time.time", return_value=1000):
            first = self.begin("material")
            self.study.mark_reading(first["action"]["chapter_id"], True)
        with patch("time.time", return_value=1001):
            second = self.begin("practice")
            attempt = self.service.get(second["attempt_id"])
            self.service.submit(attempt["id"], self.rows(attempt))
        plan = LearningPlanService(self.service).view("cybersec_lab", include_ai=False)
        self.assertEqual(self.feedback(first)["validation"]["other_recommendations"], 1)
        self.assertEqual(len(plan["recommendation_checks"]), 2)
        point = next(e for e in plan["evidence"] if e["point_id"] == self.point()["id"])
        self.assertTrue(point["recommendation_validation"])

    def test_due_review_completed_by_new_independent_attempt(self):
        with patch("time.time", return_value=1000):
            origin = self.mistake()
            self.service.submit(origin["id"], self.rows(origin))
        with patch("time.time", return_value=1000 + DAY):
            record = self.begin("review")
            attempt = self.service.get(record["attempt_id"])
            self.service.submit(attempt["id"], self.rows(attempt))
            feedback = self.feedback(record)
            self.assertEqual(feedback["outcome"], "independent_evidence")
            self.assertIsNotNone(feedback["completed_at"])
            self.assertEqual(feedback["after"]["pending_review_count"], 1)

    def test_three_courses_count_failure_correction_and_new_independent_evidence(self):
        from webapp_core.learning.learning_plan import LearningPlanService
        from webapp_core.learning.learning_roadshow import CASES, answer_rows

        for subject, (original_id, fresh_id, _) in CASES.items():
            with self.subTest(subject=subject):
                original = self.service.start(original_id)
                correct = answer_rows(self.service, original)
                wrong = [dict(r) for r in correct]
                wrong[0]["event" if subject == "operating_systems" else "value"] = (
                    "hit" if subject == "operating_systems" else "999"
                )
                self.service.submit(original["id"], wrong)
                point = next(
                    p
                    for p in self.study.dashboard()["points"]
                    if any(a.get("attempt_id") == original["id"] for a in p["actions"])
                )
                record = self.begin("continue", point["id"])
                self.service.submit(original["id"], correct)
                fresh = self.service.start(fresh_id)
                self.service.submit(fresh["id"], answer_rows(self.service, fresh))
                plan = LearningPlanService(self.service).view(subject)
                feedback = next(
                    f for f in plan["recommendation_checks"] if f["id"] == record["id"]
                )
                self.assertEqual(
                    feedback["validation"]["baseline"], {"attempts": 1, "passed": 0}
                )
                self.assertEqual(
                    feedback["validation"]["followup"], {"attempts": 1, "passed": 1}
                )
                self.assertEqual(
                    feedback["validation"]["retention"], "awaiting_delayed"
                )
                self.assertFalse(feedback["validation"]["checks"][0]["independent"])

    async def test_ai_plan_receives_validation_and_becomes_stale_after_new_evidence(
        self,
    ):
        from tests.learning.test_ai_learning_plan import AIPlanTests
        from webapp_core.learning.learning_plan import LearningPlanService

        record = self.begin("practice")
        attempt = self.service.get(record["attempt_id"])
        self.service.submit(attempt["id"], self.rows(attempt))
        plan = LearningPlanService(self.service)
        model = AIPlanTests.model(self, plan.view("cybersec_lab", include_ai=False))
        await plan.generate("cybersec_lab", model)
        prompt = model.ainvoke.call_args.args[0][1][1]
        self.assertIn("推荐后核验", prompt)
        self.assertIn("awaiting_delayed", prompt)
        self.assertIsNotNone(plan.view("cybersec_lab")["ai_plan"])
        self.mistake("dh_02")
        changed = plan.view("cybersec_lab")
        self.assertIsNone(changed["ai_plan"])
        self.assertTrue(changed["ai_stale"])
        self.assertEqual(
            changed["recommendation_checks"][0]["validation"]["retention"], "needs_work"
        )


class PersonalFollowupPermissionTests(unittest.TestCase):
    setUpClass = fixtures.LearningPathPermissionsTests.__dict__["setUpClass"]
    setUp = fixtures.LearningPathPermissionsTests.setUp
    credentials = staticmethod(fixtures.LearningPathPermissionsTests.credentials)
    account = fixtures.LearningPathPermissionsTests.account
    publish = fixtures.LearningPathPermissionsTests.publish
    classroom = fixtures.LearningPathPermissionsTests.classroom
    setup_task = fixtures.LearningPathPermissionsTests.setup_task
    start_task = fixtures.LearningPathPermissionsTests.start_task
    submit = fixtures.LearningPathPermissionsTests.submit
    age = fixtures.LearningPathPermissionsTests.age

    def test_hidden_quiz_masks_feedback_and_rejects_stale_start(self):
        point = next(
            p
            for p in self.student.get("/api/learning/path").json["points"]
            if p["title"] == "DH 计算"
        )
        action = next(a for a in point["actions"] if a["kind"] == "material")
        endpoint = "/api/learning/points/cybersec_lab:DH 计算/recommendations"
        self.assertEqual(
            self.student.post(endpoint, json={"token": action["token"]}).status_code,
            200,
        )
        task, _, _ = self.setup_task("quiz")
        self.start_task(task)
        hidden = next(
            p
            for p in self.student.get("/api/learning/path").json["points"]
            if p["title"] == "DH 计算"
        )
        self.assertEqual(hidden["followups"], [])
        self.assertEqual(
            self.student.get("/api/learning/plan/cybersec_lab").json[
                "recommendation_checks"
            ],
            [],
        )
        self.assertEqual(
            self.student.post(endpoint, json={"token": action["token"]}).status_code,
            409,
        )
        self.assertEqual(
            self.app.test_client()
            .post(endpoint, json={"token": action["token"]})
            .status_code,
            401,
        )
        other = next(
            p
            for p in self.other_student.get("/api/learning/path").json["points"]
            if p["title"] == "DH 计算"
        )
        self.assertEqual(other["followups"], [])
        self.assertEqual(
            self.other_student.get("/api/learning/plan/cybersec_lab").json[
                "recommendation_checks"
            ],
            [],
        )
        self.age(task, due_at=0)
        visible = next(
            p
            for p in self.student.get("/api/learning/path").json["points"]
            if p["title"] == "DH 计算"
        )
        self.assertEqual(len(visible["followups"]), 1)
        self.assertEqual(
            len(
                self.student.get("/api/learning/plan/cybersec_lab").json[
                    "recommendation_checks"
                ]
            ),
            1,
        )
