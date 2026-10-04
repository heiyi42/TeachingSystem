import asyncio
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.learning import test_learning_path
from tests.learning import test_learning_dialogue
from tests.learning import test_ai_learning_plan
from tests.school import test_school_permissions
from webapp_core.learning.learning_plan import LearningPlanService
from webapp_core.learning.learning_service import LearningConflict, LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.learning.learning_workflow import LearningWorkflow
from webapp_core.runtime.workflow_runs import WorkflowRuns


class LearningCheckpointTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_learning_path.LearningPathTests.setUp

    def workflow(self, learning=None):
        return LearningWorkflow(
            learning or self.service, WorkflowRuns(self.path.with_name("runs.sqlite3"))
        )

    def model(self, kind):
        if kind == "learning_dialogue":
            return test_learning_dialogue.LearningDialogueTests.model(self)
        return test_ai_learning_plan.AIPlanTests.model(
            self, LearningPlanService(self.service).view("C_program", include_ai=False)
        )

    async def test_timeout_resume_uses_saved_request_and_completed_result_is_idempotent(
        self,
    ):
        attempt = self.service.start("dh_01")
        for kind, target, data in [
            ("learning_dialogue", attempt["id"], {"action": "start"}),
            ("learning_plan", "C_program", {}),
        ]:
            with self.subTest(kind=kind):
                flow = self.workflow()
                model = self.model(kind)
                model.ainvoke.side_effect = TimeoutError()
                with self.assertRaises(TimeoutError):
                    await flow.execute(kind, target, data, model)
                run = flow.status(kind, target)
                self.assertTrue(run["can_resume"])
                model = self.model(kind)
                result = await self.workflow().execute(
                    kind,
                    target,
                    {"resume_run_id": run["id"], "action": "exit", "revision": 999},
                    model,
                )
                model.ainvoke.assert_awaited_once()
                self.assertEqual(flow.status(kind, target)["status"], "done")
                model.ainvoke.reset_mock()
                repeated = await flow.execute(
                    kind, target, {"resume_run_id": run["id"]}, model
                )
                self.assertEqual(result, repeated)
                model.ainvoke.assert_not_awaited()
                if kind == "learning_dialogue":
                    self.assertEqual(result["dialogue"]["revision"], 1)
                    self.assertEqual(result["dialogue"]["status"], "talking")

    async def test_invalid_output_can_be_regenerated_and_stale_evidence_cannot_resume(
        self,
    ):
        attempt = self.service.start("dh_01")
        flow = self.workflow()
        model = self.model("learning_dialogue")
        model.ainvoke.return_value.content = "{}"
        with self.assertRaises(RuntimeError):
            await flow.execute(
                "learning_dialogue", attempt["id"], {"action": "start"}, model
            )
        run = flow.status("learning_dialogue", attempt["id"])
        await flow.execute(
            "learning_dialogue",
            attempt["id"],
            {"resume_run_id": run["id"]},
            self.model("learning_dialogue"),
        )
        for kind, target, data in [
            (
                "learning_dialogue",
                attempt["id"],
                {"action": "answer", "answer": "先判断顺序", "revision": 1},
            ),
            ("learning_plan", "C_program", {}),
        ]:
            model = self.model(kind)
            model.ainvoke.side_effect = TimeoutError()
            with self.assertRaises(TimeoutError):
                await flow.execute(kind, target, data, model)
            run = flow.status(kind, target)
            if kind == "learning_dialogue":
                self.service.save_draft(
                    target, test_learning_path.LearningPathTests.rows(self, attempt)
                )
            else:
                LearningPlanService(self.service).configure(
                    target, {"minutes": 15, "chapter_id": ""}
                )
            self.assertEqual(flow.status(kind, target)["status"], "stale")
            model = self.model(kind)
            with self.assertRaises(LearningConflict):
                await flow.execute(kind, target, {"resume_run_id": run["id"]}, model)
            model.ainvoke.assert_not_awaited()

    async def test_failure_after_commit_does_not_duplicate_dialogue_or_plan(self):
        attempt = self.service.start("dh_01")
        flow = self.workflow()
        for kind, target, data, method in [
            ("learning_dialogue", attempt["id"], {"action": "start"}, "update"),
            ("learning_plan", "C_program", {}, "ai_study_plan"),
        ]:
            original = getattr(self.service.store, method)
            writes = []

            def crash(*args, **kwargs):
                result = original(*args, **kwargs)
                if method == "update" or len(args) > 1:
                    writes.append(1)
                    raise RuntimeError("simulated failure after business commit")
                return result

            with patch.object(self.service.store, method, side_effect=crash):
                with self.assertRaises(RuntimeError):
                    await flow.execute(kind, target, data, self.model(kind))
            run = flow.status(kind, target)
            self.assertEqual(run["status"], "done")
            model = self.model(kind)
            result = await flow.execute(
                kind, target, {"resume_run_id": run["id"]}, model
            )
            model.ainvoke.assert_not_awaited()
            self.assertEqual(len(writes), 1)
            if kind == "learning_dialogue":
                self.assertEqual(result["dialogue"]["revision"], 1)

    async def test_concurrent_owner_and_old_run_boundaries(self):
        attempt = self.service.start("dh_01")
        flow = self.workflow()
        model = self.model("learning_dialogue")
        response = model.ainvoke.return_value
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked(*args):
            started.set()
            await release.wait()
            return response

        model.ainvoke.side_effect = blocked
        pending = asyncio.create_task(
            flow.execute("learning_dialogue", attempt["id"], {"action": "start"}, model)
        )
        await asyncio.wait_for(started.wait(), 5)
        try:
            self.assertEqual(
                flow.status("learning_dialogue", attempt["id"])["status"], "running"
            )
            with self.assertRaises(LearningConflict):
                await flow.execute(
                    "learning_dialogue", attempt["id"], {"action": "start"}, model
                )
            other = self.workflow(
                LearningService(
                    LearningStore(self.path, owner_id="bob"), self.service.solver
                )
            )
            with self.assertRaises(LookupError):
                other.status("learning_dialogue", attempt["id"])
        finally:
            release.set()
            await pending
        run = flow.status("learning_dialogue", attempt["id"])
        await flow.execute(
            "learning_dialogue",
            attempt["id"],
            {"action": "verify", "revision": 1},
            model,
        )
        with self.assertRaises(LookupError):
            await flow.execute(
                "learning_dialogue", attempt["id"], {"resume_run_id": run["id"]}, model
            )

    async def test_process_exit_after_validated_generation_resumes_without_model(self):
        attempt = self.service.start("dh_01")
        code = """
import asyncio, os, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from webapp_core.learning.learning_service import LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.learning.learning_workflow import LearningWorkflow
from webapp_core.runtime.workflow_runs import WorkflowRuns
from webapp_core.chat.problem_tutoring_service import ProblemTutoringService
learning = LearningService(LearningStore(Path(sys.argv[1]),owner_id='alice'),ProblemTutoringService())
learning.store.update = lambda *a, **kw: os._exit(23)
model = SimpleNamespace(model_name='test',ainvoke=AsyncMock(return_value=SimpleNamespace(content='{"hypothesis":"证据不足","question":"这一步如何判断？","next_step":"回到练习核对"}')))
asyncio.run(LearningWorkflow(learning,WorkflowRuns(Path(sys.argv[1]).with_name('runs.sqlite3'))).execute('learning_dialogue',sys.argv[2],{'action':'start'},model))
"""
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", code, str(self.path), attempt["id"]],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 23, result.stderr.decode())
        flow = self.workflow()
        run = flow.status("learning_dialogue", attempt["id"])
        self.assertEqual(run["status"], "interrupted")
        model = self.model("learning_dialogue")
        model.ainvoke.side_effect = AssertionError(
            "completed model node must not repeat"
        )
        result = await flow.execute(
            "learning_dialogue", attempt["id"], {"resume_run_id": run["id"]}, model
        )
        self.assertEqual(result["dialogue"]["revision"], 1)
        model.ainvoke.assert_not_awaited()


class LearningCheckpointHTTPTests(unittest.TestCase):
    setUpClass = test_school_permissions.SchoolPermissionsTests.__dict__["setUpClass"]
    setUp = test_school_permissions.SchoolPermissionsTests.setUp
    credentials = staticmethod(
        test_school_permissions.SchoolPermissionsTests.credentials
    )
    account = test_school_permissions.SchoolPermissionsTests.account
    publish = test_school_permissions.SchoolPermissionsTests.publish
    classroom = test_school_permissions.SchoolPermissionsTests.classroom

    def test_revoked_class_access_cannot_read_or_resume_diagnosis(self):
        self.publish()
        classroom = self.classroom()
        attempt = self.student.post(
            "/api/learning/attempts",
            json={
                "exercise_id": "dh_01",
                "class_id": classroom["id"],
            },
        ).json
        endpoint = f"/api/learning/attempts/{attempt['id']}/dialogue"
        model = test_learning_dialogue.LearningDialogueTests.model(self)
        model.ainvoke.side_effect = TimeoutError()
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", model):
            self.assertEqual(
                self.student.post(endpoint, json={"action": "start"}).status_code, 503
            )
        run = self.student.get(endpoint).json
        self.teacher.delete(
            f"/api/school/classes/{classroom['id']}/members/{self.student_user['id']}",
            json={},
        )
        self.assertEqual(self.student.get(endpoint).status_code, 404)
        self.assertEqual(
            self.student.post(endpoint, json={"resume_run_id": run["id"]}).status_code,
            404,
        )

    def test_dialogue_resume_access_and_saved_answer(self):
        self.publish()
        attempt = self.student.post(
            "/api/learning/attempts", json={"exercise_id": "dh_01"}
        ).json
        endpoint = f"/api/learning/attempts/{attempt['id']}/dialogue"
        model = test_learning_dialogue.LearningDialogueTests.model(self)
        model.ainvoke.side_effect = TimeoutError()
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", model):
            self.assertEqual(
                self.student.post(endpoint, json={"action": "start"}).status_code, 503
            )
        run = self.student.get(endpoint).json
        self.assertTrue(run["can_resume"])
        self.assertEqual(self.other_student.get(endpoint).status_code, 404)
        self.assertEqual(
            self.other_student.post(
                endpoint, json={"resume_run_id": run["id"]}
            ).status_code,
            404,
        )
        self.assertEqual(self.app.test_client().get(endpoint).status_code, 401)
        with patch(
            "webapp_core.chat.auto_runtime.auto_router_llm",
            test_learning_dialogue.LearningDialogueTests.model(self),
        ):
            response = self.student.post(endpoint, json={"resume_run_id": run["id"]})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["dialogue"]["revision"], 1)
        self.assertEqual(self.student.get(endpoint).json["status"], "done")

    def test_plan_resume_isolated_by_owner_and_target(self):
        endpoint = "/api/learning/plan/C_program/ai"
        model = test_learning_dialogue.LearningDialogueTests.model(self)
        model.ainvoke.side_effect = TimeoutError()
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", model):
            self.assertEqual(self.student.post(endpoint, json={}).status_code, 503)
        run = self.student.get(endpoint).json
        self.assertIsNone(self.other_student.get(endpoint).json)
        self.assertEqual(
            self.other_student.post(
                endpoint, json={"resume_run_id": run["id"]}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.student.post(
                "/api/learning/plan/operating_systems/ai",
                json={"resume_run_id": run["id"]},
            ).status_code,
            404,
        )
        self.student.put(
            "/api/learning/plan/C_program", json={"minutes": 15, "chapter_id": ""}
        )
        self.assertEqual(self.student.get(endpoint).json["status"], "stale")
        self.assertEqual(
            self.student.post(endpoint, json={"resume_run_id": run["id"]}).status_code,
            409,
        )
