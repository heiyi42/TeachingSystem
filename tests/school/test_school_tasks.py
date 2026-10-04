from __future__ import annotations

import csv
import io
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests.school import test_school_permissions as fixtures
from webapp_core.learning.learning_store import LearningStore


class SchoolTaskTests(unittest.TestCase):
    setUpClass = fixtures.SchoolPermissionsTests.__dict__["setUpClass"]
    setUp = fixtures.SchoolPermissionsTests.setUp
    credentials = staticmethod(fixtures.SchoolPermissionsTests.credentials)
    account = fixtures.SchoolPermissionsTests.account
    publish = fixtures.SchoolPermissionsTests.publish
    classroom = fixtures.SchoolPermissionsTests.classroom

    def setup_task(self, kind="homework", ids=None, **extra):
        self.publish()
        classroom = self.classroom()
        body = dict(
            class_id=classroom["id"],
            kind=kind,
            title="DH 班级任务",
            instructions="完成全部检查点",
            rubric="步骤正确 60 分，报告论证 40 分" if kind == "experiment" else "",
            due_at=time.time() + 3600,
            max_submissions=1 if kind == "quiz" else 10,
            exercise_ids=["dh_01"] if ids is None else ids,
            **extra,
        )
        response = self.teacher.post("/api/school/tasks", json=body)
        self.assertEqual(response.status_code, 201, response.json)
        task = response.json
        self.assertEqual(
            self.teacher.post(
                f"/api/school/tasks/{task['id']}/publish", json={}
            ).status_code,
            200,
        )
        return task, classroom, body

    def start_task(self, task, client=None, exercise_id="dh_01"):
        response = (client or self.student).post(
            f"/api/school/tasks/{task['id']}/attempts",
            json={"exercise_id": exercise_id},
        )
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def submit(self, attempt, correct=True):
        response = self.student.post(
            f"/api/learning/attempts/{attempt['id']}/submit",
            json={"rows": [{"value": str(v)} for v in (8 if correct else 9, 19, 2, 2)]},
        )
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def age(self, task, **changes):
        with LearningStore(self.path)._connect() as c:
            item = json.loads(
                c.execute(
                    "SELECT data FROM school_tasks WHERE id=?", (task["id"],)
                ).fetchone()[0]
            )
            item.update(changes)
            c.execute(
                "UPDATE school_tasks SET data=? WHERE id=?",
                (json.dumps(item), task["id"]),
            )

    def test_homework_version_evidence_and_final_lock(self):
        task, classroom, body = self.setup_task()
        attempt = self.start_task(task)
        self.assertEqual(self.start_task(task)["id"], attempt["id"])
        self.submit(attempt, False)
        self.student.post(f"/api/learning/attempts/{attempt['id']}/hint", json={})
        self.submit(attempt)
        final = self.student.post(f"/api/school/tasks/{task['id']}/finalize", json={})
        self.assertEqual(final.status_code, 200, final.json)
        self.assertEqual(final.json["submission"]["score"], 100)
        self.assertEqual(final.json["attempts"][0]["content_version"], 1)
        for suffix in ("draft", "submit", "hint"):
            response = getattr(self.student, "put" if suffix == "draft" else "post")(
                f"/api/learning/attempts/{attempt['id']}/{suffix}",
                json={"rows": attempt["draft"]},
            )
            self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(
            self.teacher.put(f"/api/school/tasks/{task['id']}", json=body).status_code,
            409,
        )
        evidence = self.teacher.get(
            f"/api/school/tasks/{task['id']}?student_id={self.student_user['id']}"
        ).json
        self.assertEqual(len(evidence["attempts"][0]["submissions"]), 2)
        self.assertFalse(evidence["attempts"][0]["first_unassisted_pass"])
        self.assertEqual(
            self.student.post(
                "/api/learning/attempts", json={"parent_id": attempt["id"]}
            ).status_code,
            409,
        )
        report = self.teacher.get(f"/api/school/classes/{classroom['id']}/report").json
        self.assertEqual((report["finalized"], report["expected_submissions"]), (1, 1))
        self.assertEqual(report["chapters"][0]["expected_answers"], 1)
        self.assertEqual(report["chapters"][0]["passed"], 1)
        self.assertEqual(report["rows"][0]["corrections"], 1)
        self.assertEqual(report["rows"][0]["hints"], 1)

    def test_report_pass_types_denominators_and_csv(self):
        task, classroom, _ = self.setup_task()
        clients = {"independent": (self.student, self.student_user)}
        for kind in ("assisted", "corrected", "repeated", "unclassified_pass", "unpassed", "unsubmitted"):
            client, user = self.account("report_" + kind)
            response = client.post("/api/school/classes/join", json={"join_code": classroom["join_code"]})
            self.assertEqual(response.status_code, 200)
            clients[kind] = client, user
        for kind, (client, user) in clients.items():
            if kind == "unsubmitted":
                continue
            if kind == "repeated":
                private = client.post("/api/learning/attempts", json={"exercise_id": "dh_01"}).json
                response = client.post(f"/api/learning/attempts/{private['id']}/submit", json={"rows": [{"value": str(v)} for v in (8, 19, 2, 2)]})
                self.assertEqual(response.status_code, 200)
            attempt = self.start_task(task, client=client)
            url = f"/api/learning/attempts/{attempt['id']}"
            if kind in {"assisted", "unclassified_pass"}:
                self.assertEqual(client.post(url + "/hint", json={}).status_code, 200)
            if kind == "corrected":
                for _ in range(2):
                    self.assertEqual(client.post(url + "/submit", json={"rows": [{"value": str(v)} for v in (9, 19, 2, 2)]}).status_code, 200)
            values = (9, 19, 2, 2) if kind == "unpassed" else (8, 19, 2, 2)
            self.assertEqual(client.post(url + "/submit", json={"rows": [{"value": str(v)} for v in values]}).status_code, 200)
            if kind == "independent":
                self.assertEqual(client.post(url + "/solution", json={}).status_code, 200)
            if kind == "unclassified_pass":
                with LearningStore(self.path)._connect() as db:
                    state = json.loads(db.execute("SELECT data FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0])
                    state["submissions"][0].pop("assistance")
                    db.execute("UPDATE attempts SET data=? WHERE id=?", (json.dumps(state), attempt["id"]))
        report = self.teacher.get(f"/api/school/classes/{classroom['id']}/report").json
        totals = report["totals"]
        self.assertEqual(len(report["common_errors"]), 1)
        error = report["common_errors"][0]
        self.assertEqual((error["student_count"], error["occurrences"], error["current_student_count"]), (2, 3, 1))
        self.assertEqual(len(error["evidence"]), 3)
        self.assertEqual({e["student_id"] for e in error["evidence"]}, {clients[k][1]["id"] for k in ("corrected", "unpassed")})
        self.assertEqual(error["exercises"], [])
        self.assertIn("暂无", error["practice_advice"])
        chapter = report["chapters"][0]["id"]
        filtered = self.teacher.get(f"/api/school/classes/{classroom['id']}/report?chapter_id={chapter}")
        self.assertEqual(filtered.json["totals"], totals)
        self.assertEqual(self.teacher.get(f"/api/school/classes/{classroom['id']}/report?chapter_id=invalid").status_code, 400)
        self.assertEqual((totals["expected_answers"], totals["submitted"], totals["passed"], totals["unpassed"], totals["unsubmitted"]), (7, 6, 5, 1, 1))
        for kind in ("independent", "assisted", "corrected", "repeated", "unclassified_pass"):
            self.assertEqual(totals[kind], 1, kind)
            row = next(r for r in report["rows"] if r["student_id"] == clients[kind][1]["id"])
            self.assertEqual(row[kind], 1)
            self.assertEqual(report["chapters"][0][kind], 1)
            self.assertEqual(report["questions"][0][kind], 1)
        corrected = next(r for r in report["rows"] if r["student_id"] == clients["corrected"][1]["id"])
        self.assertEqual(corrected["corrections"], 2)
        self.assertEqual(sum(corrected["errors"].values()), 2)
        response = self.teacher.get(f"/api/school/classes/{classroom['id']}/report.csv")
        rows = list(csv.DictReader(io.StringIO(response.data.decode("utf-8-sig"))))
        self.assertEqual(sum(int(r["首次独立通过"]) for r in rows), 1)
        self.assertEqual(sum(int(r["辅助通过"]) for r in rows), 1)
        self.assertEqual(sum(int(r["订正通过"]) for r in rows), 1)
        self.assertEqual(sum(int(r["未提交题次"]) for r in rows), 1)

    def test_report_recommendation_draft_confirmation_and_withdrawal(self):
        task, classroom, _ = self.setup_task()
        attempt = self.start_task(task)
        self.submit(attempt, False)
        self.publish("training:dh_02")
        report_url = f"/api/school/classes/{classroom['id']}/report"
        report = self.teacher.get(report_url).json
        group = report["common_errors"][0]
        self.assertEqual([e["id"] for e in group["exercises"]], ["dh_02"])
        self.assertEqual(group["evidence"][0]["attempt_id"], attempt["id"])
        self.assertEqual(group["evidence"][0]["submission_number"], 1)
        self.assertEqual(group["evidence"][0]["content_version"], 1)
        self.assertEqual(len(self.teacher.get(f"/api/school/classes/{classroom['id']}/tasks").json["tasks"]), 1)
        payload = dict(class_id=classroom["id"], kind="homework", title="DH 公开值补练", instructions=group["practice_advice"], due_at=time.time()+3600, exercise_ids=[e["id"] for e in group["exercises"]])
        created = self.teacher.post("/api/school/tasks", json=payload)
        self.assertEqual(created.status_code, 201)
        draft = created.json
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(self.student.get(f"/api/school/tasks/{draft['id']}").status_code, 404)
        self.assertEqual(self.teacher.get(report_url).json["task_count"], 1)
        self.assertEqual(self.teacher.post(f"/api/school/tasks/{draft['id']}/publish", json={}).status_code, 200)
        self.assertEqual(self.student.get(f"/api/school/tasks/{draft['id']}").status_code, 200)
        self.assertEqual(self.teacher.get(report_url).json["common_errors"][0]["exercises"], [])
        self.assertEqual(self.teacher.post("/api/school/content/training:dh_02/1/withdraw", json={"note": "撤下候选题，检查过期建议"}).status_code, 200)
        self.assertEqual(self.teacher.post("/api/school/tasks", json=payload).status_code, 400)
        for client in (self.student, self.other_teacher, self.admin):
            self.assertIn(client.get(report_url + "?chapter_id=cybersec_lab_02").status_code, (403, 404))

    def test_report_chapter_filter_keeps_full_task_snapshot(self):
        self.publish("training:access_control_01")
        task, classroom, _ = self.setup_task(kind="quiz", ids=["dh_01", "access_control_01"])
        attempt = self.start_task(task)
        self.submit(attempt)
        final = self.student.post(f"/api/school/tasks/{task['id']}/finalize", json={})
        self.assertEqual(final.status_code, 200)
        url = f"/api/school/classes/{classroom['id']}/report"
        full = self.teacher.get(url).json
        self.assertEqual(full["totals"]["expected_answers"], 2)
        self.assertEqual(full["totals"]["independent"], 1)
        self.assertEqual(len(full["chapter_options"]), 2)
        filtered = self.teacher.get(url + "?chapter_id=cybersec_lab_04").json
        self.assertEqual(filtered["totals"]["expected_answers"], 1)
        self.assertEqual(filtered["totals"]["submitted"], 0)
        self.assertEqual(filtered["totals"]["unsubmitted"], 1)
        self.assertEqual(filtered["common_errors"], [])
        self.assertEqual(filtered["rows"][0]["status"], "finalized")
        self.assertEqual(filtered["rows"][0]["score"], full["rows"][0]["score"])
        self.assertEqual([q["exercise_id"] for q in filtered["questions"]], ["access_control_01"])
        response = self.teacher.get(url + ".csv?chapter_id=cybersec_lab_04")
        rows = list(csv.DictReader(io.StringIO(response.data.decode("utf-8-sig"))))
        self.assertEqual(rows[0]["订正通过"], "0")
        self.assertEqual(rows[0]["未提交题次"], "1")

    def test_report_empty_class_has_zero_denominators(self):
        classroom = self.teacher.post("/api/school/classes", json={"title": "空班级", "course_id": "cybersec_lab"}).json
        response = self.teacher.get(f"/api/school/classes/{classroom['id']}/report")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(value == 0 for value in response.json["totals"].values()))
        self.assertEqual(response.json["chapters"], [])
        self.assertEqual(response.json["rows"], [])

    def test_task_permissions_and_no_arbitrary_attachment(self):
        task, classroom, _ = self.setup_task()
        for client in (self.other_student, self.other_teacher, self.admin):
            for path in (
                f"/api/school/tasks/{task['id']}",
                f"/api/school/classes/{classroom['id']}/tasks",
                f"/api/school/classes/{classroom['id']}/report",
            ):
                self.assertIn(client.get(path).status_code, (403, 404), path)
        self.assertEqual(
            self.teacher.post(
                f"/api/school/tasks/{task['id']}/attempts",
                json={"exercise_id": "dh_01"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.student.post("/api/school/tasks", json={}).status_code, 403
        )
        self.assertEqual(
            self.student.get(
                f"/api/school/tasks/{task['id']}?student_id={self.other_user['id']}"
            ).status_code,
            404,
        )
        self.assertEqual(self.start_task(task)["class_id"], classroom["id"])
        self.assertEqual(
            self.student.post(
                f"/api/school/tasks/{task['id']}/attempts",
                json={"exercise_id": "dh_02"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.student.get(
                f"/api/school/classes/{classroom['id']}/report.csv"
            ).status_code,
            403,
        )
        self.teacher.delete(
            f"/api/school/classes/{classroom['id']}/members/{self.student_user['id']}",
            json={},
        )
        self.assertEqual(
            self.student.post(
                f"/api/school/tasks/{task['id']}/finalize", json={}
            ).status_code,
            404,
        )

    def test_quiz_redacts_all_student_endpoints_and_blocks_aids(self):
        task, classroom, _ = self.setup_task("quiz")
        attempt = self.start_task(task)
        good = self.submit(attempt)
        for response in (
            good,
            self.student.get(f"/api/learning/attempts/{attempt['id']}").json,
            self.student.get(f"/api/school/tasks/{task['id']}").json["attempts"][0],
        ):
            self.assertEqual(response["status"], "in_progress")
            self.assertIsNone(response["evaluation"])
            self.assertIsNone(response["first_error"])
            self.assertIsNone(response["solution"])
            self.assertFalse(response["first_unassisted_pass"])
            self.assertFalse(response["assignment"]["can_edit"])
        for suffix in ("hint", "solution", "submit"):
            self.assertEqual(
                self.student.post(
                    f"/api/learning/attempts/{attempt['id']}/{suffix}",
                    json={"rows": attempt["draft"]},
                ).status_code,
                409,
            )
        progress = self.student.get("/api/learning/progress").json
        self.assertEqual(progress["summary"]["first_unassisted_passes"], 0)
        self.assertEqual(progress["errors"], [])
        final = self.student.post(
            f"/api/school/tasks/{task['id']}/finalize", json={}
        ).json
        self.assertIsNone(final["submission"]["score"])
        self.assertEqual(
            self.teacher.get(
                f"/api/school/tasks/{task['id']}?student_id={self.student_user['id']}"
            ).json["submission"]["score"],
            100,
        )
        self.teacher.post(f"/api/school/tasks/{task['id']}/close", json={})
        revealed = self.student.get(f"/api/school/tasks/{task['id']}").json
        self.assertEqual(revealed["submission"]["score"], 100)
        self.assertEqual(revealed["attempts"][0]["status"], "passed")
        self.assertEqual(
            self.student.post(
                f"/api/learning/attempts/{attempt['id']}/solution", json={}
            ).status_code,
            200,
        )

    def test_quiz_limit_does_not_leak_pass_through_editability(self):
        task, classroom, _ = self.setup_task("quiz")
        self.age(task, max_submissions=2)
        attempt = self.start_task(task)
        self.assertTrue(self.submit(attempt)["assignment"]["can_edit"])
        self.assertFalse(self.submit(attempt, False)["assignment"]["can_edit"])
        progress = self.student.get("/api/learning/progress").json
        self.assertEqual(progress["errors"], [])
        self.assertEqual(progress["records"][0]["status"], "in_progress")
        self.assertIsNone(progress["records"][0]["first_error"])

    def test_deadline_archives_saved_drafts_missing_questions_and_no_fabricated_participation(
        self,
    ):
        self.publish("training:dh_02")
        task, classroom, _ = self.setup_task("quiz", ids=["dh_01", "dh_02"])
        self.other_student.post(
            "/api/school/classes/join", json={"join_code": classroom["join_code"]}
        )
        attempt = self.start_task(task)
        rows = [{"value": str(v)} for v in (8, 19, 2, 2)]
        self.assertEqual(
            self.student.put(
                f"/api/learning/attempts/{attempt['id']}/draft", json={"rows": rows}
            ).status_code,
            200,
        )
        self.age(task, due_at=time.time() - 1)
        self.assertEqual(
            self.student.put(
                f"/api/learning/attempts/{attempt['id']}/draft", json={"rows": rows}
            ).status_code,
            409,
        )
        detail = self.student.get(f"/api/school/tasks/{task['id']}").json
        self.assertTrue(detail["submission"]["automatic"])
        self.assertEqual(detail["submission"]["score"], 50)
        self.assertFalse(detail["submission"]["late"])
        self.assertEqual(detail["attempts"][0]["submission_count"], 1)
        second = self.student.get(f"/api/school/tasks/{task['id']}").json
        self.assertEqual(
            second["submission"]["finalized_at"], detail["submission"]["finalized_at"]
        )
        report = self.teacher.get(f"/api/school/classes/{classroom['id']}/report").json
        self.assertEqual(
            (
                report["member_count"],
                report["expected_submissions"],
                report["finalized"],
            ),
            (2, 2, 1),
        )
        self.assertEqual(
            next(q for q in report["questions"] if q["exercise_id"] == "dh_02")[
                "started"
            ],
            0,
        )
        self.assertEqual(
            next(r for r in report["rows"] if r["student_id"] == self.other_user["id"])[
                "status"
            ],
            "not_started",
        )

    def test_ungradable_saved_quiz_draft_is_archived_as_failed(self):
        task, _, _ = self.setup_task("quiz")
        attempt = self.start_task(task)
        self.student.put(
            f"/api/learning/attempts/{attempt['id']}/draft",
            json={"rows": [{"value": "不是数字"}] * 4},
        )
        self.age(task, due_at=time.time() - 1)
        response = self.student.get(f"/api/school/tasks/{task['id']}")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["submission"]["score"], 0)
        self.assertEqual(response.json["attempts"][0]["status"], "needs_correction")

    def test_late_window_and_homework_requires_last_draft_verified(self):
        task, _, _ = self.setup_task()
        attempt = self.start_task(task)
        self.submit(attempt, False)
        self.student.put(
            f"/api/learning/attempts/{attempt['id']}/draft",
            json={"rows": [{"value": "8"}] * 4},
        )
        self.assertEqual(
            self.student.post(
                f"/api/school/tasks/{task['id']}/finalize", json={}
            ).status_code,
            409,
        )
        self.age(task, due_at=time.time() - 1, late_until=time.time() + 1800)
        self.submit(attempt)
        final = self.student.post(
            f"/api/school/tasks/{task['id']}/finalize", json={}
        ).json
        self.assertTrue(final["submission"]["late"])

    def test_experiment_files_review_history_and_final_evidence(self):
        task, classroom, _ = self.setup_task("experiment", ids=[])
        url = f"/api/school/tasks/{task['id']}"
        self.assertEqual(self.student.post(url + "/finalize", json={}).status_code, 409)
        uploaded = self.student.post(
            url + "/files",
            data={"file": (io.BytesIO(b"int main(void){return 0;}"), "实验.c")},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.json)
        file_id = uploaded.json["files"][0]["id"]
        self.assertEqual(
            self.other_student.get(url + f"/files/{file_id}").status_code, 404
        )
        download = self.teacher.get(url + f"/files/{file_id}")
        self.assertEqual(download.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertEqual(download.data, b"int main(void){return 0;}")
        self.student.put(url + "/report", json={"report": "记录实验步骤与结果"})
        final = self.student.post(url + "/finalize", json={}).json
        self.assertIsNone(final["submission"]["score"])
        self.assertIsNone(final["submission"]["training_score"])
        self.assertEqual(
            self.student.delete(url + f"/files/{file_id}", json={}).status_code, 409
        )
        self.assertEqual(
            self.student.put(url + "/report", json={"report": "篡改"}).status_code, 409
        )
        review = url + f"/students/{self.student_user['id']}/review"
        self.assertEqual(
            self.teacher.post(
                review, json={"score": 101, "comment": "越界"}
            ).status_code,
            400,
        )
        for score in (80, 85):
            self.assertEqual(
                self.teacher.post(
                    review, json={"score": score, "comment": "依评分标准评阅"}
                ).status_code,
                200,
            )
        retained = self.student.get(url).json
        self.assertEqual(retained["submission"]["score"], 85)
        self.assertEqual(len(retained["submission"]["reviews"]), 2)
        self.assertEqual(retained["submission"]["report"], "记录实验步骤与结果")
        self.assertEqual(
            self.teacher.get(f"/api/school/classes/{classroom['id']}/report").json[
                "rows"
            ][0]["pending_review"],
            False,
        )

    def test_material_validation_and_request_limits(self):
        task, _, _ = self.setup_task("experiment", ids=[])
        url = f"/api/school/tasks/{task['id']}/files"
        for name, raw in (
            ("../secret.c", b"text"),
            ("script.html", b"text"),
            ("binary.c", b"\x00text"),
            ("gbk.txt", b"\xff"),
            ("fake.pdf", b"not a PDF"),
            ("empty.txt", b""),
        ):
            self.assertEqual(
                self.student.post(
                    url, data={"file": (io.BytesIO(raw), name)}
                ).status_code,
                400,
                name,
            )
        for size, expected in ((1048577, 400), (1200001, 413)):
            response = self.student.post(
                url, data={"file": (io.BytesIO(b"a" * size), "large.txt")}
            )
            self.assertEqual(response.status_code, expected)
            response.request.environ["wsgi.input"].close()
        self.assertEqual(
            self.student.post(
                url,
                data={"file": (io.BytesIO(b"text"), "cross.txt")},
                headers={"Origin": "https://other.test"},
            ).status_code,
            403,
        )
        for n in range(3):
            self.assertEqual(
                self.student.post(
                    url, data={"file": (io.BytesIO(b"text"), f"report{n}.txt")}
                ).status_code,
                201,
            )
        self.assertEqual(
            self.student.post(
                url, data={"file": (io.BytesIO(b"text"), "extra.txt")}
            ).status_code,
            409,
        )

    def test_csv_scope_formula_escape_and_pending_review(self):
        task, classroom, _ = self.setup_task("experiment", ids=[])
        self.age(task, title='=HYPERLINK("bad")')
        self.student.put(
            f"/api/school/tasks/{task['id']}/report", json={"report": "完成报告"}
        )
        self.student.post(f"/api/school/tasks/{task['id']}/finalize", json={})
        response = self.teacher.get(f"/api/school/classes/{classroom['id']}/report.csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))
        self.assertIn("'=HYPERLINK", response.data.decode("utf-8-sig"))
        self.assertTrue(
            self.teacher.get(f"/api/school/classes/{classroom['id']}/report").json[
                "rows"
            ][0]["pending_review"]
        )

    def test_draft_visibility_course_and_published_version_validation(self):
        self.publish()
        classroom = self.classroom()
        body = dict(
            class_id=classroom["id"],
            kind="quiz",
            title="任务",
            due_at=time.time() + 3600,
            max_submissions=1,
            exercise_ids=["dh_01"],
        )
        self.assertEqual(
            self.teacher.post(
                "/api/school/tasks", json={**body, "kind": []}
            ).status_code,
            400,
        )
        task = self.teacher.post("/api/school/tasks", json=body).json
        self.assertEqual(
            self.student.get(f"/api/school/tasks/{task['id']}").status_code, 404
        )
        self.assertEqual(
            self.student.get(f"/api/school/classes/{classroom['id']}/tasks").json[
                "tasks"
            ],
            [],
        )
        self.assertEqual(
            self.teacher.post(
                "/api/school/tasks", json={**body, "exercise_ids": ["c_loop_01"]}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.teacher.post(
                "/api/school/tasks", json={**body, "late_until": time.time() + 7200}
            ).status_code,
            400,
        )
        self.teacher.post(
            "/api/school/content/training:dh_01/1/withdraw", json={"note": "测试撤下"}
        )
        self.assertEqual(
            self.teacher.post(
                f"/api/school/tasks/{task['id']}/publish", json={}
            ).status_code,
            409,
        )

    def test_task_snapshot_survives_content_withdrawal_and_restart(self):
        task, _, _ = self.setup_task()
        self.teacher.post(
            "/api/school/content/training:dh_01/1/withdraw", json={"note": "撤下核查"}
        )
        attempt = self.start_task(task)
        self.assertEqual(attempt["content_version"], 1)
        self.submit(attempt)
        self.student.post(f"/api/school/tasks/{task['id']}/finalize", json={})
        app = self.webapp.create_app(learning_store_path=self.path)
        client = app.test_client()
        client.post("/api/identity/login", json=self.credentials("student"))
        self.assertEqual(
            client.get(f"/api/school/tasks/{task['id']}").json["submission"]["score"],
            100,
        )
        self.assertEqual(client.get("/legacy").status_code, 404)

    def test_concurrent_start_has_one_attempt_and_finalization_blocks_late_write(self):
        task, _, _ = self.setup_task()
        cookie = self.student.get_cookie("gm_session").value

        def start():
            client = self.app.test_client()
            client.set_cookie("gm_session", cookie)
            return client.post(
                f"/api/school/tasks/{task['id']}/attempts",
                json={"exercise_id": "dh_01"},
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: start(), range(4)))
        self.assertTrue(all(r.status_code == 201 for r in responses))
        self.assertEqual(len({r.json["id"] for r in responses}), 1)
        attempt = responses[0].json
        self.submit(attempt, False)
        self.student.post(f"/api/school/tasks/{task['id']}/finalize", json={})
        self.assertEqual(
            self.student.post(
                f"/api/learning/attempts/{attempt['id']}/submit",
                json={"rows": [{"value": str(v)} for v in (8, 19, 2, 2)]},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.student.get(f"/api/school/tasks/{task['id']}").json["submission"][
                "score"
            ],
            0,
        )

    def test_assignments_work_in_c_and_operating_systems_classes(self):
        examples = [
            ("C_program", "c_loop_01", [{"value": str(v)} for v in (1, 3, 6, 6)]),
            (
                "operating_systems",
                "lru_01",
                [
                    dict(frames=f, event=e, evicted=v)
                    for f, e, v in [
                        ("1", "fault", ""),
                        ("1,2", "fault", ""),
                        ("1,2,3", "fault", ""),
                        ("1,2,3", "hit", ""),
                        ("1,4,3", "fault", "2"),
                        ("1,4,2", "fault", "3"),
                    ]
                ],
            ),
        ]
        for course, exercise_id, rows in examples:
            with self.subTest(course=course):
                self.publish("training:" + exercise_id)
                classroom = self.teacher.post(
                    "/api/school/classes", json={"title": course, "course_id": course}
                ).json
                self.student.post(
                    "/api/school/classes/join",
                    json={"join_code": classroom["join_code"]},
                )
                task = self.teacher.post(
                    "/api/school/tasks",
                    json=dict(
                        class_id=classroom["id"],
                        title="课程任务",
                        kind="homework",
                        exercise_ids=[exercise_id],
                        due_at=time.time() + 3600,
                    ),
                ).json
                self.teacher.post(f"/api/school/tasks/{task['id']}/publish", json={})
                attempt = self.start_task(task, exercise_id=exercise_id)
                submitted = self.student.post(
                    f"/api/learning/attempts/{attempt['id']}/submit",
                    json={"rows": rows},
                )
                self.assertEqual(submitted.json["status"], "passed", submitted.json)
                final = self.student.post(
                    f"/api/school/tasks/{task['id']}/finalize", json={}
                )
                self.assertEqual(final.json["submission"]["score"], 100, final.json)
                report = self.teacher.get(
                    f"/api/school/classes/{classroom['id']}/report"
                ).json
                self.assertEqual(report["rows"][0]["independent"], 1)

    def test_solution_after_deadline_archives_before_marking_assistance(self):
        task, _, _ = self.setup_task("quiz")
        attempt = self.start_task(task)
        self.student.put(
            f"/api/learning/attempts/{attempt['id']}/draft",
            json={"rows": [{"value": str(v)} for v in (8, 19, 2, 2)]},
        )
        self.age(task, due_at=time.time() - 1)
        response = self.student.post(
            f"/api/learning/attempts/{attempt['id']}/solution", json={}
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["status"], "passed")
        self.assertEqual(response.json["submission_count"], 1)
        self.assertTrue(response.json["solution_viewed"])
        detail = self.student.get(f"/api/school/tasks/{task['id']}").json
        self.assertEqual(detail["submission"]["score"], 100)
        self.assertFalse(detail["attempts"][0]["solution_viewed"])
        self.assertTrue(detail["attempts"][0]["first_unassisted_pass"])

    def test_student_task_list_progress_is_private_and_deadline_aware(self):
        task, classroom, _ = self.setup_task(kind="quiz")
        url = f"/api/school/classes/{classroom['id']}/tasks"
        def progress():
            response = self.student.get(url)
            self.assertEqual(response.status_code, 200, response.json)
            return response.json["tasks"][0]["student_progress"]
        self.assertEqual(progress(), dict(status="not_started", submitted=0, total=1, late=False))
        attempt = self.start_task(task)
        self.assertEqual(progress()["status"], "in_progress")
        self.submit(attempt)
        self.assertEqual(progress()["submitted"], 1)
        self.assertEqual(set(progress()), {"status", "submitted", "total", "late"})
        self.assertNotIn("student_progress", self.teacher.get(url).json["tasks"][0])
        other, _ = self.account("list_other")
        other.post("/api/school/classes/join", json={"join_code": classroom["join_code"]})
        self.assertEqual(other.get(url).json["tasks"][0]["student_progress"]["submitted"], 0)
        self.age(task, due_at=time.time() - 60)
        self.assertEqual(progress()["status"], "ended")
        self.student.get(f"/api/school/tasks/{task['id']}")
        self.assertEqual(progress()["status"], "finalized")
        self.age(task, kind="homework", due_at=time.time()-60, late_until=time.time()+600)
        other_progress = other.get(url).json["tasks"][0]["student_progress"]
        self.assertTrue(other_progress["late"])
        self.assertEqual(other_progress["status"], "not_started")


if __name__ == "__main__":
    unittest.main()
