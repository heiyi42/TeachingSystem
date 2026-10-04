import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tests.learning import test_learning_path as fixtures
from tests.school import test_school_permissions as school_fixtures
from webapp_core.learning.learning_plan import LearningPlanService
from webapp_core.learning.learning_service import LearningService, LearningConflict
from webapp_core.learning.learning_store import LearningStore


class StudyPlanTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp
    rows = fixtures.LearningPathTests.rows

    def test_diagnostic_reuses_evidence_and_plan_updates_after_error(self):
        plan = LearningPlanService(self.service)
        before = plan.view("C_program")
        self.assertEqual(len(before["diagnostics"]), 3)
        self.assertEqual(before["diagnostic_completed"], 0)
        self.assertEqual(len(self.service.store.list_attempts()), 0)
        point = before["diagnostics"][0]["point_id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(
                pool.map(
                    lambda _: plan.begin_diagnostic("C_program", point)["attempt_id"],
                    range(2),
                )
            )
        self.assertEqual(ids[0], ids[1])
        a = self.service.get(ids[0])
        self.assertTrue(a["diagnostic"])
        self.service.submit(a["id"], [{"value": "999"} for _ in a["draft"]])
        after = plan.view("C_program")
        self.assertEqual(after["diagnostic_completed"], 1)
        self.assertEqual(after["tasks"][0]["kind"], "continue")
        self.assertEqual(after["tasks"][0]["attempt_id"], a["id"])
        self.assertIn("需要复习", after["diagnostics"][0]["status"])
        self.service.submit(a["id"], self.rows(a))
        corrected = plan.view("C_program")
        self.assertIn("需要复习", corrected["diagnostics"][0]["status"])
        with self.assertRaises(LearningConflict):
            plan.begin_diagnostic("C_program", point)

    def test_profiles_isolated_scoped_and_budgeted(self):
        plan = LearningPlanService(self.service)
        saved = plan.configure(
            "C_program", {"minutes": 15, "chapter_id": "C_program_10"}
        )
        self.assertTrue(saved["profile"]["configured"])
        self.assertLessEqual(saved["estimated_minutes"], 15)
        self.assertEqual(saved["diagnostics"][0]["chapter_id"], "C_program_10")
        other = LearningPlanService(
            LearningService(
                LearningStore(self.path, owner_id="bob"), self.service.solver
            )
        )
        self.assertFalse(other.view("C_program")["profile"]["configured"])
        self.assertFalse(plan.view("operating_systems")["profile"]["configured"])
        for minutes in [True, 14, 121, "30", 30.5]:
            with self.assertRaises(ValueError):
                plan.configure("C_program", {"minutes": minutes})
        with self.assertRaises(ValueError):
            plan.configure(
                "C_program", {"minutes": 30, "chapter_id": "operating_systems_08"}
            )
        with self.assertRaises(ValueError):
            plan.view("unknown")

    def test_no_catalog_and_hidden_evidence_do_not_leak(self):
        plan = LearningPlanService(self.service)
        dashboard = plan.path.dashboard()
        for p in dashboard["points"]:
            p["guidance_blocked"] = True
        dashboard["recommendations"] = []
        with patch.object(plan.path, "dashboard", return_value=dashboard):
            self.assertEqual(plan.view("C_program")["diagnostics"], [])
            with self.assertRaises(LearningConflict):
                plan.begin_diagnostic("C_program", "C_program:c_loop")
        self.service.catalog = {}
        self.assertEqual(plan.view("C_program")["diagnostics"], [])

    def test_prior_independent_result_counts_without_new_attempt(self):
        a = self.service.start("c_pointer_01")
        self.service.submit(a["id"], self.rows(a))
        plan = LearningPlanService(self.service).view("C_program")
        d = next(
            d for d in plan["diagnostics"] if d["point_id"] == "C_program:c_pointer"
        )
        self.assertTrue(d["completed"])
        self.assertEqual(d["status"], "初步独立证据")
        self.assertEqual(len(self.service.store.list_attempts()), 1)


class StudyPlanRouteTests(unittest.TestCase):
    setUpClass = classmethod(school_fixtures.SchoolPermissionsTests.setUpClass.__func__)
    setUp = school_fixtures.SchoolPermissionsTests.setUp
    credentials = staticmethod(school_fixtures.SchoolPermissionsTests.credentials)
    account = school_fixtures.SchoolPermissionsTests.account
    publish = school_fixtures.SchoolPermissionsTests.publish

    def test_routes_ownership_and_published_catalog(self):
        url = "/api/learning/plan/cybersec_lab"
        self.assertEqual(self.app.test_client().get(url).status_code, 401)
        self.assertEqual(self.student.get(url).json["diagnostics"], [])
        self.publish()
        saved = self.student.put(url, json={"minutes": 45, "chapter_id": ""})
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(self.other_student.get(url).json["profile"]["minutes"], 30)
        point = saved.json["diagnostics"][0]["point_id"]
        start = self.student.post(url + "/diagnostic", json={"point_id": point})
        self.assertEqual(start.status_code, 200, start.json)
        self.assertEqual(
            self.other_student.get(
                "/api/learning/attempts/" + start.json["attempt_id"]
            ).status_code,
            404,
        )
        self.assertEqual(
            self.student.post(
                url + "/diagnostic", json={"point_id": "C_program:c_loop"}
            ).status_code,
            409,
        )
