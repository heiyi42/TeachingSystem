import copy
import unittest
from tests.learning import test_learning_path as path_fixtures
from tests.school import test_school_permissions as school_fixtures
from webapp_core.learning.learning_exercises import EXERCISES
from webapp_core.learning.learning_authoring import validate_training_edit, authoring_templates


class MatchingTests(unittest.TestCase):
    setUp = path_fixtures.LearningPathTests.setUp

    def test_alias_course_and_empty_query(self):
        results = self.study.match_training("扩容失败如何保留原数据", "C_program")
        self.assertTrue(results)
        self.assertEqual(results[0]["id"], "C_program:c_program_memory")
        self.assertIn("扩容失败", results[0]["reason"])
        self.assertTrue(results[0]["objectives"])
        self.assertFalse(self.study.match_training("扩容失败", "cybersec_lab"))
        self.assertFalse(self.study.match_training(""))
        self.assertFalse(self.study.match_training("hello universe"))
        self.assertFalse(self.study.match_training("myreallocwrapper", "C_program"))

    def test_disk_cpu_disambiguation_and_chapter(self):
        self.assertTrue(
            all(
                "磁盘" in p["title"]
                for p in self.study.match_training("磁盘FCFS", "operating_systems")
            )
        )
        self.assertTrue(
            all(
                "磁盘" not in p["title"]
                for p in self.study.match_training("CPU FCFS", "operating_systems")
            )
        )
        self.assertTrue(self.study.match_training("", "C_program", "C_program_10"))
        with self.assertRaises(ValueError):
            self.study.match_training("指针", "operating_systems", "C_program_10")
        self.service.catalog = {}
        self.assertFalse(self.study.match_training("指针", "C_program"))

    def test_all_templates_and_invalid_conditions(self):
        for e in EXERCISES.values():
            validate_training_edit(e, copy.deepcopy(e))
        original = EXERCISES["lru_01"]
        candidate = copy.deepcopy(
            next(e for e in authoring_templates(original) if e["algorithm"] == "OPT")
        )
        candidate["id"] = original["id"]
        candidate["parameters"]["sequence"] = [7, 2, 7, 3]
        validate_training_edit(original, candidate)
        for frames in [0, 9, True, 1.5]:
            invalid = copy.deepcopy(candidate)
            invalid["parameters"]["frames"] = frames
            with self.assertRaises(ValueError):
                validate_training_edit(original, invalid)
        candidate["rules"] = "任意修改评分规则"
        with self.assertRaises(ValueError):
            validate_training_edit(original, candidate)
        cpu = copy.deepcopy(
            next(e for e in EXERCISES.values() if e["kind"] == "cpu_scheduling")
        )
        original = copy.deepcopy(cpu)
        cpu["parameters"]["processes"] *= 2
        with self.assertRaises(ValueError):
            validate_training_edit(original, cpu)


class AuthoringRouteTests(unittest.TestCase):
    setUpClass = classmethod(school_fixtures.SchoolPermissionsTests.setUpClass.__func__)
    setUp = school_fixtures.SchoolPermissionsTests.setUp
    credentials = staticmethod(school_fixtures.SchoolPermissionsTests.credentials)
    account = school_fixtures.SchoolPermissionsTests.account
    publish = school_fixtures.SchoolPermissionsTests.publish

    def test_edit_publish_preserves_snapshot_and_permissions(self):
        key = "training:lru_01"
        self.publish(key)
        old = self.student.post(
            "/api/learning/attempts", json={"exercise_id": "lru_01"}
        ).json
        detail = self.teacher.get(f"/api/school/content/{key}").json
        self.assertTrue(detail["templates"])
        data = copy.deepcopy(
            next(e for e in detail["templates"] if e["algorithm"] == "OPT")
        )
        data["id"] = "lru_01"
        data["parameters"]["sequence"] = [7, 2, 7, 3]
        self.teacher.post(f"/api/school/content/{key}/versions", json={})
        self.assertEqual(
            self.student.put(
                f"/api/school/content/{key}/2", json={"data": data, "source": "验证"}
            ).status_code,
            403,
        )
        response = self.teacher.put(
            f"/api/school/content/{key}/2",
            json={"data": data, "source": "验证自定义条件"},
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.publish(key, 2)
        new = self.student.post(
            "/api/learning/attempts", json={"exercise_id": "lru_01"}
        ).json
        self.assertEqual(new["exercise"]["algorithm"], "OPT")
        self.assertEqual(new["exercise"]["parameters"]["sequence"], [7, 2, 7, 3])
        retained = self.student.get(f"/api/learning/attempts/{old['id']}").json
        self.assertEqual(retained["exercise"], old["exercise"])
        self.assertEqual(retained["content_version"], 1)
        matched = self.student.post(
            "/api/learning/training-match",
            json={"question": "OPT", "subject_id": "operating_systems"},
        )
        self.assertEqual(matched.status_code, 200, matched.json)
        self.assertTrue(matched.json["matches"])
        self.assertEqual(
            self.student.post(
                "/api/learning/training-match", json={"question": []}
            ).status_code,
            400,
        )
