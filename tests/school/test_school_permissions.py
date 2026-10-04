from __future__ import annotations

import copy
import hashlib
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webapp_core.learning.learning_service import LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.chat.problem_tutoring_service import ProblemTutoringService


class SchoolPermissionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.webapp = importlib.import_module("webapp")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "learning.sqlite3"
        self.app = self.webapp.create_app(learning_store_path=self.path)
        self.app.testing = True
        self.school = self.app.extensions["agenticrag.school_store"]
        self.solver = self.webapp.get_chat_service(self.app).problem_tutoring_service
        self.admin = self.app.test_client()
        self.assertEqual(
            self.admin.post(
                "/api/identity/setup", json=self.credentials("admin")
            ).status_code,
            200,
        )
        self.teacher, self.teacher_user = self.account("teacher", teacher=True)
        self.other_teacher, _ = self.account("other_teacher", teacher=True)
        self.student, self.student_user = self.account("student")
        self.other_student, self.other_user = self.account("other_student")

    @staticmethod
    def credentials(username):
        return dict(username=username, name=username, password="test-school-password")

    def account(self, username, *, teacher=False):
        client = self.app.test_client()
        data = self.credentials(username)
        if teacher:
            self.assertEqual(
                self.admin.post("/api/school/teachers", json=data).status_code, 201
            )
        response = client.post(
            "/api/identity/login" if teacher else "/api/identity/register",
            json={**data, "role": "admin"},
        )
        self.assertEqual(response.status_code, 200, response.json)
        return client, response.json["user"]

    def publish(self, key="training:dh_01", version=1):
        for action in ("approve", "publish"):
            response = self.teacher.post(
                f"/api/school/content/{key}/{version}/{action}",
                json={"note": "流程测试审核依据"},
            )
            self.assertEqual(response.status_code, 200, response.json)

    def classroom(self):
        response = self.teacher.post(
            "/api/school/classes",
            json={"title": "安全实验一班", "course_id": "cybersec_lab"},
        )
        self.assertEqual(response.status_code, 201, response.json)
        classroom = response.json
        self.assertEqual(
            self.student.post(
                "/api/school/classes/join", json={"join_code": classroom["join_code"]}
            ).status_code,
            200,
        )
        return classroom

    def start(self, *, client=None, class_id=None):
        response = (client or self.student).post(
            "/api/learning/attempts",
            json={
                "exercise_id": "dh_01",
                "class_id": class_id,
                "owner_id": self.other_user["id"],
            },
        )
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_automated_review_has_explicit_audit_identity_without_login_user(self):
        self.school.transition("training:dh_01", 1, {"id": "system:codex"}, "approve", "用户授权的 Codex 自动化审核")
        entry = self.school.audit_log("training:dh_01")[-1]
        self.assertEqual(entry["actor_name"], "Codex 自动化审核")
        self.assertEqual(entry["actor_id"], "system:codex")

    def test_anonymous_role_and_request_boundaries(self):
        anonymous = self.app.test_client()
        for path in (
            "/api/learning/progress",
            "/api/learning/exercises",
            "/api/chats",
            "/api/school/content",
            "/api/events/chat-updates",
        ):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        self.assertEqual(
            anonymous.post(
                "/api/learning/attempts", json={"exercise_id": "dh_01"}
            ).status_code,
            401,
        )
        self.assertEqual(self.student_user["role"], "student")
        for path in ("/api/school/teachers", "/api/school/classes"):
            self.assertEqual(self.student.post(path, json={}).status_code, 403)
        self.assertEqual(self.student.get("/api/school/content").status_code, 403)
        self.assertEqual(
            self.teacher.post(
                "/api/school/teachers", json=self.credentials("bad")
            ).status_code,
            403,
        )
        self.assertEqual(
            self.student.post(
                "/api/identity/logout",
                json={},
                headers={"Origin": "https://foreign.example"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.student.post("/api/identity/logout", data="{}").status_code, 400
        )
        self.assertEqual(
            anonymous.post(
                "/api/identity/setup",
                json=self.credentials("remote"),
                environ_overrides={"REMOTE_ADDR": "192.0.2.1"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.admin.post(
                "/api/identity/setup", json=self.credentials("another_admin")
            ).status_code,
            409,
        )
        self.assertEqual(
            self.teacher.post(
                "/api/school/classes", json={"title": "bad", "course_id": []}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.student.post(
                "/api/learning/attempts", json={"exercise_id": {}, "class_id": []}
            ).status_code,
            400,
        )
        self.assertEqual(anonymous.post("/api/learning/demo", json={}).status_code, 201)

    def test_private_attempts_cannot_be_read_mutated_or_retested(self):
        self.publish()
        attempt = self.start()
        aid = attempt["id"]
        for method, suffix, data in (
            ("get", "", None),
            ("put", "/draft", {"rows": attempt["draft"]}),
            ("post", "/submit", {"rows": attempt["draft"]}),
            ("post", "/hint", {}),
            ("post", "/solution", {}),
        ):
            for client in (self.other_student, self.teacher, self.admin):
                response = getattr(client, method)(
                    f"/api/learning/attempts/{aid}{suffix}",
                    **({"json": data} if data is not None else {}),
                )
                self.assertEqual(
                    response.status_code, 404, (method, suffix, response.json)
                )
        self.assertEqual(
            self.other_student.post(
                "/api/learning/attempts", json={"parent_id": aid}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.other_student.get("/api/learning/progress").json["records"], []
        )
        self.assertEqual(
            LearningStore(self.path).get(aid)["owner_id"], self.student_user["id"]
        )
        gold = [{"value": str(value)} for value in (8, 19, 2, 2)]
        self.assertEqual(
            self.student.post(
                f"/api/learning/attempts/{aid}/submit", json={"rows": gold}
            ).json["status"],
            "passed",
        )

    def test_class_scope_roster_and_revocation(self):
        self.publish()
        classroom = self.classroom()
        cid = classroom["id"]
        class_attempt = self.start(class_id=cid)
        private_attempt = self.start()
        aid = class_attempt["id"]
        for first_value in (9, 8):
            response = self.student.post(
                f"/api/learning/attempts/{aid}/submit",
                json={
                    "rows": [{"value": str(value)} for value in (first_value, 19, 2, 2)]
                },
            )
            self.assertEqual(response.status_code, 200)
        prefix = f"/api/school/classes/{cid}/students/{self.student_user['id']}"
        progress = self.teacher.get(prefix + "/progress")
        self.assertEqual(
            [row["id"] for row in progress.json["records"]], [class_attempt["id"]]
        )
        self.assertEqual(
            self.teacher.get(prefix + f"/attempts/{class_attempt['id']}").status_code,
            200,
        )
        self.assertEqual(
            self.teacher.get(prefix + f"/attempts/{private_attempt['id']}").status_code,
            404,
        )
        evidence = self.teacher.get(prefix + f"/attempts/{aid}").json
        self.assertEqual(len(evidence["submissions"]), 2)
        self.assertEqual(evidence["submissions"][0]["rows"][0]["value"], "9")
        self.assertEqual(evidence["submissions"][1]["rows"][0]["value"], "8")
        self.assertEqual(evidence["first_error"]["error_code"], "wrong_public")
        self.assertEqual(self.other_teacher.get(prefix + "/progress").status_code, 404)
        self.assertEqual(self.other_student.get(prefix + "/progress").status_code, 403)
        self.assertEqual(
            self.other_student.post(
                "/api/learning/attempts", json={"exercise_id": "dh_01", "class_id": cid}
            ).status_code,
            404,
        )
        self.assertNotIn(
            "join_code", self.student.get("/api/school/classes").json["classes"][0]
        )
        self.assertEqual(
            self.other_teacher.post(
                f"/api/school/classes/{cid}/rotate-code", json={}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.teacher.post(
                f"/api/school/classes/{cid}/rotate-code", json={}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.other_student.post(
                "/api/school/classes/join", json={"join_code": classroom["join_code"]}
            ).status_code,
            404,
        )
        self.publish("training:sjf_01")
        self.assertEqual(
            self.student.post(
                "/api/learning/attempts",
                json={"exercise_id": "sjf_01", "class_id": cid},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.student.post(
                "/api/learning/attempts",
                json={"parent_id": private_attempt["id"], "class_id": cid},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.teacher.delete(
                f"/api/school/classes/{cid}/members/{self.student_user['id']}", json={}
            ).status_code,
            200,
        )
        self.assertEqual(self.teacher.get(prefix + "/progress").status_code, 404)
        self.assertEqual(
            self.student.post(
                f"/api/learning/attempts/{class_attempt['id']}/hint", json={}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.student.get(
                f"/api/learning/attempts/{class_attempt['id']}"
            ).status_code,
            200,
        )

    def test_review_state_machine_and_pinned_version(self):
        self.assertEqual(
            self.student.get("/api/learning/exercises").json["exercises"], []
        )
        self.assertEqual(
            self.student.post(
                "/api/learning/attempts", json={"exercise_id": "dh_01"}
            ).status_code,
            400,
        )
        key = "training:dh_01"
        self.assertEqual(
            self.teacher.post(
                f"/api/school/content/{key}/1/publish", json={"note": "test"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.teacher.post(
                f"/api/school/content/{key}/1/approve", json={"note": ""}
            ).status_code,
            400,
        )
        self.publish(key)
        old = self.start()
        original = self.teacher.get(f"/api/school/content/{key}").json["versions"][0][
            "data"
        ]
        self.assertEqual(
            self.teacher.put(
                f"/api/school/content/{key}/1",
                json={"data": original, "source": "test"},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.teacher.post(f"/api/school/content/{key}/versions", json={}).json[
                "version"
            ],
            2,
        )
        changed = copy.deepcopy(original)
        changed["parameters"]["security_checks"][0]["label"] = "篡改核验条件"
        self.assertEqual(
            self.teacher.put(
                f"/api/school/content/{key}/2", json={"data": changed, "source": "test"}
            ).status_code,
            400,
        )
        original["title"] = "DH 修订标题"
        self.assertEqual(
            self.teacher.put(
                f"/api/school/content/{key}/2",
                json={"data": original, "source": "教材核查"},
            ).status_code,
            200,
        )
        self.publish(key, 2)
        new = self.start()
        self.assertEqual(new["content_version"], 2)
        self.assertEqual(new["exercise"]["title"], "DH 修订标题")
        retained = self.student.get(f"/api/learning/attempts/{old['id']}").json
        self.assertEqual(retained["content_version"], 1)
        self.assertEqual(retained["exercise"], old["exercise"])
        self.assertEqual(
            self.student.post(
                f"/api/learning/attempts/{old['id']}/submit",
                json={"rows": [{"value": str(value)} for value in (8, 19, 2, 2)]},
            ).json["status"],
            "passed",
        )
        self.assertEqual(
            self.teacher.post(
                f"/api/school/content/{key}/2/withdraw", json={"note": "核查后撤下"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.student.post(
                "/api/learning/attempts", json={"exercise_id": "dh_01"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.student.get(f"/api/learning/attempts/{old['id']}").status_code, 200
        )
        actions = [
            row["action"]
            for row in self.teacher.get(f"/api/school/content/{key}").json["audit"]
        ]
        self.assertIn("content_edited", actions)
        self.assertIn("content_withdraw", actions)

    def test_qa_publication_excludes_drafts_and_stale_vectors(self):
        items = self.teacher.get("/api/school/content").json["items"]
        qa = next(item for item in items if item["category"] == "qa")
        self.assertEqual(
            len([item for item in items if item["category"] == "training"]), 178
        )
        self.assertEqual(len([item for item in items if item["category"] == "qa"]), 107)
        self.assertEqual(self.solver.load_question_bank(), [])
        key = qa["item_key"]
        self.publish(key)
        self.assertEqual(len(self.solver.load_question_bank()), 1)
        self.assertEqual(self.solver.load_question_bank()[0]["content_version"], 1)
        bank = self.solver.load_question_bank()[0]
        text = self.solver.build_question_bank_embedding_text(
            subject_id=bank["subject_id"],
            problem_type=bank["problem_type"],
            knowledge_points=bank["knowledge_points"],
            question=bank["question"],
        )
        vector_path = self.path.parent / "vectors.json"
        vector_path.write_text(
            json.dumps(
                {"model": self.solver.question_bank_embed_model, "items": [{"id": bank["id"], "text": text, "embedding": [1, 2]}]}
            )
        )
        self.solver.question_bank_embedding_index_path = vector_path
        self.solver.question_bank_embed_enabled = True
        self.assertIn(bank["id"], self.solver.load_question_bank_embedding_index())
        self.teacher.post(f"/api/school/content/{key}/versions", json={})
        data = self.teacher.get(f"/api/school/content/{key}").json["versions"][0][
            "data"
        ]
        self.assertEqual(data["review"]["teacher_status"], "pending")
        blank = {**data, "question": "   "}
        self.assertEqual(
            self.teacher.put(
                f"/api/school/content/{key}/2", json={"data": blank, "source": "test"}
            ).status_code,
            400,
        )
        data["question"] += "（修订条件）"
        self.assertEqual(
            self.teacher.put(
                f"/api/school/content/{key}/2",
                json={"data": data, "source": "修订教材"},
            ).status_code,
            200,
        )
        self.assertEqual(self.solver.load_question_bank()[0]["content_version"], 1)
        self.publish(key, 2)
        self.assertEqual(self.solver.load_question_bank()[0]["content_version"], 2)
        self.assertNotIn(bank["id"], self.solver.load_question_bank_embedding_index())
        self.teacher.post(f"/api/school/content/{key}/versions", json={})
        self.assertEqual(
            self.teacher.post(
                f"/api/school/content/{key}/3/reject", json={"note": "条件待核查"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.teacher.post(
                f"/api/school/content/{key}/3/approve", json={"note": "test"}
            ).status_code,
            409,
        )
        self.assertEqual(self.solver.load_question_bank()[0]["content_version"], 2)

    def test_password_sessions_and_login_limits(self):
        cookie = self.student.get_cookie("gm_session")
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site, "Strict")
        with self.school.connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id=?", (self.student_user["id"],)
            ).fetchone()
            self.assertNotEqual(row[0], self.credentials("student")["password"])
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM login_sessions WHERE token_hash=?", (cookie.value,)
                ).fetchone()
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM login_sessions WHERE token_hash=?",
                    (hashlib.sha256(cookie.value.encode()).hexdigest(),),
                ).fetchone()
            )
        second = self.app.test_client()
        second.post("/api/identity/login", json=self.credentials("student"))
        self.assertEqual(
            self.student.post(
                "/api/identity/password",
                json={"old_password": "wrong", "new_password": "replacement-password"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.student.post(
                "/api/identity/password",
                json={
                    "old_password": "test-school-password",
                    "new_password": "replacement-password",
                },
            ).status_code,
            200,
        )
        self.assertEqual(second.get("/api/learning/progress").status_code, 401)
        stale = self.app.test_client()
        stale.set_cookie("gm_session", cookie.value)
        self.assertEqual(stale.get("/api/chats").status_code, 401)
        response = second.post(
            "/api/identity/login",
            json={"username": "student", "password": "replacement-password"},
        )
        self.assertEqual(response.status_code, 200)
        logout_cookie = second.get_cookie("gm_session").value
        self.assertEqual(second.post("/api/identity/logout", json={}).status_code, 200)
        stale.set_cookie("gm_session", logout_cookie)
        self.assertEqual(stale.get("/api/chats").status_code, 401)
        for _ in range(5):
            self.assertEqual(
                second.post(
                    "/api/identity/login",
                    json={"username": "student", "password": "incorrect"},
                ).status_code,
                400,
            )
        self.assertEqual(
            second.post(
                "/api/identity/login",
                json={"username": "student", "password": "replacement-password"},
            ).status_code,
            409,
        )

    def test_chat_ownership_and_filtered_live_updates(self):
        store = self.webapp.get_store(self.app)
        service = self.webapp.get_chat_service(self.app)
        with patch.object(store, "persist_sessions_safely"):
            own = self.student.post("/api/chats", json={"mode": "auto"}).json
            other = self.other_student.post("/api/chats", json={"mode": "auto"}).json
        chat_id = own["chat_id"]
        self.assertEqual(
            self.other_student.get(f"/api/chats/{chat_id}").status_code, 404
        )
        self.assertEqual(self.admin.get(f"/api/chats/{chat_id}").status_code, 404)
        for suffix in ("delete", "rename", "mode", "pin", "messages/stream"):
            self.assertEqual(
                self.other_student.post(
                    f"/api/chats/{chat_id}/{suffix}",
                    json={"message": "test", "title": "bad"},
                ).status_code,
                404,
            )
        self.assertEqual(
            [row["chat_id"] for row in self.student.get("/api/chats").json["chats"]],
            [chat_id],
        )
        response = self.student.get("/api/events/chat-updates", buffered=False)
        self.assertEqual(response.status_code, 200)
        stream = iter(response.response)
        self.assertIn(b"retry", next(stream))
        service._publish_event(
            "changed", {"chat_id": other["chat_id"], "private": "other student's text"}
        )
        service._publish_event("changed", {"chat_id": chat_id, "own": "my text"})
        event = next(stream).decode()
        self.assertIn("my text", event)
        self.assertNotIn("other student's text", event)
        response.close()

    def test_legacy_migration_preserves_evidence_and_admin_only_access(self):
        legacy_path = self.path.parent / "legacy.sqlite3"
        original = LearningService(LearningStore(legacy_path), ProblemTutoringService())
        attempt = original.start("dh_01")
        original.submit(
            attempt["id"], [{"value": str(value)} for value in (8, 19, 2, 2)]
        )
        state = original.store.get(attempt["id"])
        state.pop("exercise_snapshot")
        state.pop("content_version")
        state.pop("grading_version")
        with original.store._connect() as connection:
            connection.execute(
                "UPDATE attempts SET data=? WHERE id=?",
                (json.dumps(state), attempt["id"]),
            )
        app = self.webapp.create_app(learning_store_path=legacy_path)
        admin = app.test_client()
        self.assertEqual(
            admin.post(
                "/api/identity/setup", json=self.credentials("legacy_admin")
            ).status_code,
            200,
        )
        student = app.test_client()
        student.post("/api/identity/register", json=self.credentials("legacy_student"))
        self.assertEqual(
            student.get(f"/api/learning/attempts/{attempt['id']}").status_code, 404
        )
        migrated = LearningStore(legacy_path).get(attempt["id"])
        self.assertEqual(migrated["submissions"], state["submissions"])
        self.assertEqual(migrated["created_at"], state["created_at"])
        self.assertEqual(migrated["updated_at"], state["updated_at"])
        self.assertEqual(migrated["content_version"], "legacy")
        self.assertTrue(legacy_path.with_name("legacy_pre_identity.sqlite3").is_file())
        self.assertTrue(
            admin.get(f"/api/learning/attempts/{attempt['id']}").json["legacy_record"]
        )


if __name__ == "__main__":
    unittest.main()
