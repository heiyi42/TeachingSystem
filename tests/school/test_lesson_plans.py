from __future__ import annotations

import unittest
import json
from pathlib import Path
import tempfile
import asyncio
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.school import test_school_tasks as task_fixtures


def lesson_model_reply(messages):
    context = json.loads(messages[1][1])
    minutes = context["minutes"]
    return SimpleNamespace(
        content=json.dumps(
            dict(
                title=f"{context['chapter']}课堂设计",
                objectives="学生能说明本章核心概念并解释判断依据。",
                focus="结合提供的作答证据，讲解概念与判断过程。",
                teacher_notes="请教师核对资料与选题。",
                stages=[
                    dict(
                        title="概念探究",
                        minutes=minutes - 5,
                        content="通过对比实例解释关键概念，提问检查学生的判断依据。",
                    ),
                    dict(
                        title="课堂反馈",
                        minutes=5,
                        content="请学生独立说明结论并核对关键步骤。",
                    ),
                ],
                classroom_ids=context["recommended_classroom_ids"],
                homework_ids=context["recommended_homework_ids"],
            ),
            ensure_ascii=False,
        )
    )


class LessonPlanTests(unittest.TestCase):
    setUpClass = task_fixtures.SchoolTaskTests.__dict__["setUpClass"]

    def setUp(self):
        task_fixtures.SchoolTaskTests.setUp(self)
        self.model = SimpleNamespace(ainvoke=AsyncMock(side_effect=lesson_model_reply))
        mocked = patch("webapp_core.chat.auto_runtime.auto_router_llm", self.model)
        mocked.start()
        self.addCleanup(mocked.stop)

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
        with patch(
            "webapp_core.school.lesson_plan_service.LearningPathService.material_path",
            return_value=None,
        ):
            result = self.teacher.post(
                url + "/generate", json=dict(chapter_id="cybersec_lab_02", minutes=45)
            )
        self.assertEqual(result.status_code, 200, result.json)
        return result.json, url, body, attempt

    def save(self, draft, url):
        response = self.teacher.put(
            url + "/" + draft["id"], json={**draft, "confirmed": True}
        )
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_cancel_generation_interrupts_model_and_does_not_save(self):
        _, classroom, _ = self.setup_task()
        started, stopped = Event(), Event()
        generation_id = str(uuid4())
        async def slow_reply(messages):
            started.set()
            try:
                await asyncio.sleep(30)
                return lesson_model_reply(messages)
            finally:
                stopped.set()
        self.model.ainvoke.side_effect = slow_reply
        observer = self.app.test_client()
        observer.post("/api/identity/login", json=self.credentials("teacher"))
        url = f"/api/school/classes/{classroom['id']}/lesson-plans"
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: self.teacher.post(url + "/generate", json=dict(chapter_id="cybersec_lab_02", minutes=45, generation_id=generation_id)))
            self.assertTrue(started.wait(5))
            response = observer.post(url + f"/generations/{generation_id}/cancel", json={})
            self.assertTrue(response.json["cancelled"])
            generated = future.result(timeout=5)
        self.assertEqual(generated.status_code, 400)
        self.assertTrue(stopped.is_set())
        self.assertEqual(observer.get(url).json["plans"], [])

    def test_cancel_before_generation_and_completed_generation(self):
        _, classroom, _ = self.setup_task()
        url = f"/api/school/classes/{classroom['id']}/lesson-plans"
        cancelled_id = str(uuid4())
        self.teacher.post(url + f"/generations/{cancelled_id}/cancel", json={})
        response = self.teacher.post(url + "/generate", json=dict(chapter_id="cybersec_lab_02", minutes=45, generation_id=cancelled_id))
        self.assertEqual(response.status_code, 400)
        self.model.ainvoke.assert_not_awaited()
        completed_id = str(uuid4())
        response = self.teacher.post(url + "/generate", json=dict(chapter_id="cybersec_lab_02", minutes=45, generation_id=completed_id))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.teacher.post(url + f"/generations/{completed_id}/cancel", json={}).json["cancelled"])
        self.assertEqual(len(self.teacher.get(url).json["plans"]), 1)

    def test_generation_persists_pending_draft_and_evidence_is_server_owned(self):
        draft, url, _, attempt = self.draft()
        self.assertIsNotNone(draft["id"])
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["revision"], 0)
        self.assertEqual(self.teacher.get(url).json["plans"][0]["id"], draft["id"])
        self.assertEqual(sum(s["minutes"] for s in draft["stages"]), 45)
        self.assertEqual(draft["homework_ids"], ["dh_02"])
        self.assertEqual(draft["classroom_ids"], ["dh_01"])
        self.assertFalse(draft["material_available"])
        self.assertEqual(
            draft["baseline"]["errors"][0]["evidence"][0]["attempt_id"], attempt["id"]
        )
        self.assertEqual(self.teacher.post(url, json=draft).status_code, 400)
        draft["baseline"] = {"counts": {"independent": 999}}
        draft["focus"] = "教师核对后的讲评内容"
        saved = self.save(draft, url)
        self.assertEqual(saved["focus"], draft["focus"])
        self.assertEqual(saved["id"], draft["id"])
        self.assertEqual(saved["status"], "confirmed")
        self.model.ainvoke.assert_awaited_once()
        self.assertEqual(saved["baseline"]["counts"]["independent"], 0)
        self.assertEqual(len(self.teacher.get(url).json["plans"]), 1)

    def test_validation_rejects_invalid_time_scope_and_choices(self):
        draft, url, _, _ = self.draft()
        for minutes in (True, 0, 14, 241, 45.5, "45"):
            self.assertEqual(
                self.teacher.post(
                    url + "/generate",
                    json=dict(chapter_id="cybersec_lab_02", minutes=minutes),
                ).status_code,
                400,
            )
        self.assertEqual(
            self.teacher.post(
                url + "/generate",
                json=dict(chapter_id="operating_systems_01", minutes=45),
            ).status_code,
            400,
        )
        for changes in (
            {"title": " "},
            {"focus": ""},
            {"stages": []},
            {"stages": [{"title": "环节", "content": "内容", "minutes": 44}]},
            {"homework_ids": ["not_published"]},
            {"homework_ids": ["dh_02", "dh_02"]},
            {"homework_ids": ["dh_01"]},
        ):
            response = self.teacher.post(
                url, json={**draft, **changes, "confirmed": True}
            )
            self.assertEqual(response.status_code, 400, changes)
        self.assertEqual(len(self.teacher.get(url).json["plans"]), 1)
        self.assertEqual(self.teacher.get(url).json["plans"][0]["status"], "draft")

    def test_owner_only_for_generate_save_edit_effects(self):
        draft, url, body, _ = self.draft()
        saved = self.save(draft, url)
        for client in (self.student, self.other_teacher, self.admin):
            self.assertIn(client.get(url).status_code, (403, 404))
            self.assertIn(
                client.post(url + "/generate", json=draft).status_code, (403, 404)
            )
            self.assertIn(
                client.post(url, json={**draft, "confirmed": True}).status_code,
                (403, 404),
            )
            self.assertIn(
                client.put(
                    url + "/" + saved["id"], json={**saved, "confirmed": True}
                ).status_code,
                (403, 404),
            )
            self.assertIn(
                client.get(url + "/" + saved["id"] + "/effects").status_code, (403, 404)
            )
        other_class = self.teacher.post(
            "/api/school/classes", json={"title": "第二班", "course_id": "cybersec_lab"}
        ).json
        other_url = f"/api/school/classes/{other_class['id']}/lesson-plans"
        self.assertEqual(
            self.teacher.get(other_url + "/" + saved["id"] + "/effects").status_code,
            403,
        )
        response = self.teacher.post(
            "/api/school/tasks",
            json={
                **body,
                "class_id": other_class["id"],
                "lesson_plan_id": saved["id"],
                "lesson_plan_revision": 1,
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_revisions_keep_baseline_and_reject_stale_save(self):
        draft, url, body, attempt = self.draft()
        saved = self.save(draft, url)
        self.submit(attempt)
        response = self.teacher.put(
            url + "/" + saved["id"],
            json={**saved, "teacher_notes": "增加课堂提问", "confirmed": True},
        )
        self.assertEqual(response.status_code, 200, response.json)
        updated = response.json
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["baseline"], saved["baseline"])
        self.assertEqual(
            self.teacher.put(
                url + "/" + saved["id"], json={**saved, "confirmed": True}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.teacher.post(
                "/api/school/tasks",
                json={**body, "lesson_plan_id": saved["id"], "lesson_plan_revision": 1},
            ).status_code,
            409,
        )

    def test_linked_task_publication_and_effects(self):
        draft, url, body, _ = self.draft()
        saved = self.save(draft, url)
        payload = {
            **body,
            "exercise_ids": ["dh_02"],
            "lesson_plan_id": saved["id"],
            "lesson_plan_revision": saved["revision"],
        }
        response = self.teacher.post("/api/school/tasks", json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        task = response.json
        effects_url = url + "/" + saved["id"] + "/effects"
        effects = self.teacher.get(effects_url).json["tasks"]
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["status"], "draft")
        self.assertEqual(effects[0]["counts"]["expected_answers"], 0)
        self.assertEqual(
            self.student.get(f"/api/school/tasks/{task['id']}").status_code, 404
        )
        self.assertEqual(
            self.teacher.post(
                f"/api/school/tasks/{task['id']}/publish", json={}
            ).status_code,
            200,
        )
        attempt = self.start_task(task, exercise_id="dh_02")
        result = self.student.post(
            f"/api/learning/attempts/{attempt['id']}/submit",
            json={"rows": [{"value": str(v)} for v in (8, 5, 4, 4)]},
        )
        self.assertEqual(result.status_code, 200, result.json)
        self.assertTrue(result.json["evaluation"]["passed"], result.json)
        effects = self.teacher.get(effects_url).json["tasks"]
        self.assertEqual(effects[0]["counts"]["expected_answers"], 1)
        self.assertEqual(effects[0]["counts"]["independent"], 1)
        self.assertEqual(effects[0]["plan_revision"], 1)
        self.assertEqual(
            self.teacher.get(url).json["plans"][0]["baseline"]["counts"]["independent"],
            0,
        )

    def test_withdrawn_exercise_cannot_be_saved_or_published(self):
        draft, url, body, _ = self.draft()
        saved = self.save(draft, url)
        response = self.teacher.post(
            "/api/school/tasks",
            json={
                **body,
                "exercise_ids": ["dh_02"],
                "lesson_plan_id": saved["id"],
                "lesson_plan_revision": 1,
            },
        )
        self.assertEqual(response.status_code, 201)
        task_id = response.json["id"]
        self.teacher.post(
            "/api/school/content/training:dh_02/1/withdraw",
            json={"note": "测试题目撤下"},
        )
        self.assertEqual(
            self.teacher.post(url, json={**draft, "confirmed": True}).status_code, 400
        )
        self.assertEqual(
            self.teacher.post(
                f"/api/school/tasks/{task_id}/publish", json={}
            ).status_code,
            409,
        )

    def test_invalid_generation_repairs_once_and_failure_does_not_create_draft(self):
        classroom = self.classroom()
        url = f"/api/school/classes/{classroom['id']}/lesson-plans"
        payload = dict(chapter_id="cybersec_lab_01", minutes=45)
        self.model.ainvoke.side_effect = [
            SimpleNamespace(content="not json"),
            lesson_model_reply(
                [
                    ("system", ""),
                    (
                        "human",
                        json.dumps(
                            dict(
                                chapter="导读",
                                minutes=45,
                                recommended_classroom_ids=[],
                                recommended_homework_ids=[],
                            )
                        ),
                    ),
                ]
            ),
        ]
        repaired = self.teacher.post(url + "/generate", json=payload)
        self.assertEqual(repaired.status_code, 200, repaired.json)
        self.assertEqual(self.model.ainvoke.await_count, 2)
        self.model.ainvoke.side_effect = lambda messages: SimpleNamespace(
            content=json.dumps(
                dict(
                    title="错误计划",
                    focus="重点",
                    objectives="目标",
                    stages=[dict(title="环节", minutes=44, content="说明")],
                    classroom_ids=["invented"],
                    homework_ids=[],
                )
            )
        )
        failed = self.teacher.post(url + "/generate", json=payload)
        self.assertEqual(failed.status_code, 400, failed.json)
        self.assertEqual(len(self.teacher.get(url).json["plans"]), 1)
        self.model.ainvoke.side_effect = TimeoutError("provider timeout")
        failed = self.teacher.post(url + "/generate", json=payload)
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(len(self.teacher.get(url).json["plans"]), 1)

    def test_requirements_material_and_unconfirmed_plan_cannot_create_task(self):
        draft, url, body, _ = self.draft()
        response = self.teacher.post(
            "/api/school/tasks",
            json={**body, "lesson_plan_id": draft["id"], "lesson_plan_revision": 0},
        )
        self.assertEqual(response.status_code, 400)
        with tempfile.TemporaryDirectory() as folder:
            material = Path(folder) / "chapter.md"
            material.write_text("本章资料：对称加密与密钥管理。", encoding="utf-8")
            with patch(
                "webapp_core.school.lesson_plan_service.LearningPathService.material_path",
                return_value=material,
            ):
                response = self.teacher.post(
                    url + "/generate",
                    json=dict(
                        chapter_id="cybersec_lab_02",
                        minutes=45,
                        requirements="基础较弱，重点讲清概念",
                    ),
                )
        self.assertEqual(response.status_code, 200, response.json)
        context = json.loads(self.model.ainvoke.call_args.args[0][1][1])
        self.assertEqual(context["requirements"], "基础较弱，重点讲清概念")
        self.assertIn("密钥管理", context["material"]["text"])
        self.assertEqual(response.json["material_source"]["source"], "chapter.md")
        self.assertEqual(len(self.teacher.get(url).json["plans"]), 2)
        self.assertNotEqual(response.json["id"], draft["id"])

    def test_no_data_or_published_questions_still_allows_preparation(self):
        classroom = self.classroom()
        url = f"/api/school/classes/{classroom['id']}/lesson-plans"
        response = self.teacher.post(
            url + "/generate", json=dict(chapter_id="cybersec_lab_01", minutes=15)
        )
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
