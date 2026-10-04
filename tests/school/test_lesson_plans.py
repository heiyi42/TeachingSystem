from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.school import test_school_tasks as task_fixtures


class LessonPlanTests(unittest.TestCase):
    setUpClass = task_fixtures.SchoolTaskTests.__dict__["setUpClass"]
    setUp = task_fixtures.SchoolTaskTests.setUp
    credentials = staticmethod(task_fixtures.SchoolTaskTests.credentials)
    account = task_fixtures.SchoolTaskTests.account
    publish = task_fixtures.SchoolTaskTests.publish
    classroom = task_fixtures.SchoolTaskTests.classroom
    setup_task = task_fixtures.SchoolTaskTests.setup_task
    start_task = task_fixtures.SchoolTaskTests.start_task
    submit = task_fixtures.SchoolTaskTests.submit

    def draft(self):
        task, classroom, body = self.setup_task()
        attempt = self.start_task(task)
        self.submit(attempt, False)
        self.publish("training:dh_02")
        url = f"/api/school/classes/{classroom['id']}/lesson-plans"
        with patch("webapp_core.school.lesson_plan_service.LearningPathService.material_path", return_value=None):
            result = self.teacher.post(url + "/generate", json=dict(chapter_id="cybersec_lab_02", minutes=45))
        self.assertEqual(result.status_code, 200, result.json)
        return result.json, url, body, attempt

    def save(self, draft, url):
        response = self.teacher.post(url, json={**draft, "confirmed": True})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_generation_is_unsaved_and_evidence_is_server_owned(self):
        draft, url, _, attempt = self.draft()
        self.assertIsNone(draft["id"])
        self.assertEqual(self.teacher.get(url).json["plans"], [])
        self.assertEqual(sum(s["minutes"] for s in draft["stages"]), 45)
        self.assertEqual(draft["homework_ids"], ["dh_02"])
        self.assertEqual(draft["classroom_ids"], ["dh_01"])
        self.assertFalse(draft["material_available"])
        self.assertEqual(draft["baseline"]["errors"][0]["evidence"][0]["attempt_id"], attempt["id"])
        self.assertEqual(self.teacher.post(url, json=draft).status_code, 400)
        draft["baseline"] = {"counts": {"independent": 999}}
        draft["focus"] = "教师核对后的讲评内容"
        saved = self.save(draft, url)
        self.assertEqual(saved["focus"], draft["focus"])
        self.assertEqual(saved["baseline"]["counts"]["independent"], 0)
        self.assertEqual(len(self.teacher.get(url).json["plans"]), 1)

    def test_validation_rejects_invalid_time_scope_and_choices(self):
        draft, url, _, _ = self.draft()
        for minutes in (True, 0, 14, 241, 45.5, "45"):
            self.assertEqual(self.teacher.post(url + "/generate", json=dict(chapter_id="cybersec_lab_02", minutes=minutes)).status_code, 400)
        self.assertEqual(self.teacher.post(url + "/generate", json=dict(chapter_id="operating_systems_01", minutes=45)).status_code, 400)
        for changes in (
            {"title": " "}, {"focus": ""}, {"stages": []},
            {"stages": [{"title": "环节", "content": "内容", "minutes": 44}]},
            {"homework_ids": ["not_published"]},
            {"homework_ids": ["dh_02", "dh_02"]},
            {"homework_ids": ["dh_01"]},
        ):
            response = self.teacher.post(url, json={**draft, **changes, "confirmed": True})
            self.assertEqual(response.status_code, 400, changes)
        self.assertEqual(self.teacher.get(url).json["plans"], [])

    def test_owner_only_for_generate_save_edit_effects(self):
        draft, url, body, _ = self.draft()
        saved = self.save(draft, url)
        for client in (self.student, self.other_teacher, self.admin):
            self.assertIn(client.get(url).status_code, (403, 404))
            self.assertIn(client.post(url + "/generate", json=draft).status_code, (403, 404))
            self.assertIn(client.post(url, json={**draft, "confirmed": True}).status_code, (403, 404))
            self.assertIn(client.put(url + "/" + saved["id"], json={**saved, "confirmed": True}).status_code, (403, 404))
            self.assertIn(client.get(url + "/" + saved["id"] + "/effects").status_code, (403, 404))
        other_class = self.teacher.post("/api/school/classes", json={"title": "第二班", "course_id": "cybersec_lab"}).json
        other_url = f"/api/school/classes/{other_class['id']}/lesson-plans"
        self.assertEqual(self.teacher.get(other_url + "/" + saved["id"] + "/effects").status_code, 403)
        response = self.teacher.post("/api/school/tasks", json={**body, "class_id": other_class["id"], "lesson_plan_id": saved["id"], "lesson_plan_revision": 1})
        self.assertEqual(response.status_code, 403)

    def test_revisions_keep_baseline_and_reject_stale_save(self):
        draft, url, body, attempt = self.draft()
        saved = self.save(draft, url)
        self.submit(attempt)
        response = self.teacher.put(url + "/" + saved["id"], json={**saved, "teacher_notes": "增加课堂提问", "confirmed": True})
        self.assertEqual(response.status_code, 200, response.json)
        updated = response.json
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["baseline"], saved["baseline"])
        self.assertEqual(self.teacher.put(url + "/" + saved["id"], json={**saved, "confirmed": True}).status_code, 409)
        self.assertEqual(self.teacher.post("/api/school/tasks", json={**body, "lesson_plan_id": saved["id"], "lesson_plan_revision": 1}).status_code, 409)

    def test_linked_task_publication_and_effects(self):
        draft, url, body, _ = self.draft()
        saved = self.save(draft, url)
        payload = {**body, "exercise_ids": ["dh_02"], "lesson_plan_id": saved["id"], "lesson_plan_revision": saved["revision"]}
        response = self.teacher.post("/api/school/tasks", json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        task = response.json
        effects_url = url + "/" + saved["id"] + "/effects"
        effects = self.teacher.get(effects_url).json["tasks"]
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["status"], "draft")
        self.assertEqual(effects[0]["counts"]["expected_answers"], 0)
        self.assertEqual(self.student.get(f"/api/school/tasks/{task['id']}").status_code, 404)
        self.assertEqual(self.teacher.post(f"/api/school/tasks/{task['id']}/publish", json={}).status_code, 200)
        attempt = self.start_task(task, exercise_id="dh_02")
        result = self.student.post(f"/api/learning/attempts/{attempt['id']}/submit", json={"rows": [{"value": str(v)} for v in (8, 5, 4, 4)]})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertTrue(result.json["evaluation"]["passed"], result.json)
        effects = self.teacher.get(effects_url).json["tasks"]
        self.assertEqual(effects[0]["counts"]["expected_answers"], 1)
        self.assertEqual(effects[0]["counts"]["independent"], 1)
        self.assertEqual(effects[0]["plan_revision"], 1)
        self.assertEqual(self.teacher.get(url).json["plans"][0]["baseline"]["counts"]["independent"], 0)

    def test_withdrawn_exercise_cannot_be_saved_or_published(self):
        draft, url, body, _ = self.draft()
        saved = self.save(draft, url)
        response = self.teacher.post("/api/school/tasks", json={**body, "exercise_ids": ["dh_02"], "lesson_plan_id": saved["id"], "lesson_plan_revision": 1})
        self.assertEqual(response.status_code, 201)
        task_id = response.json["id"]
        self.teacher.post("/api/school/content/training:dh_02/1/withdraw", json={"note": "测试题目撤下"})
        self.assertEqual(self.teacher.post(url, json={**draft, "confirmed": True}).status_code, 400)
        self.assertEqual(self.teacher.post(f"/api/school/tasks/{task_id}/publish", json={}).status_code, 409)

    def test_no_data_or_published_questions_still_allows_preparation(self):
        classroom = self.classroom()
        url = f"/api/school/classes/{classroom['id']}/lesson-plans"
        response = self.teacher.post(url + "/generate", json=dict(chapter_id="cybersec_lab_01", minutes=15))
        self.assertEqual(response.status_code, 200)
        draft = response.json
        self.assertIsNone(draft["baseline"]["counts"])
        self.assertEqual(draft["homework_ids"], [])
        self.assertEqual(draft["exercises"], [])
        self.assertTrue(any("尚无" in gap for gap in draft["gaps"]))
        self.assertEqual(sum(s["minutes"] for s in draft["stages"]), 15)
        self.assertEqual(self.save(draft, url)["revision"], 1)


if __name__ == "__main__":
    unittest.main()
