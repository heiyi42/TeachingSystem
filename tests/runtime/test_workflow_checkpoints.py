from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import subprocess
import sqlite3
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

from langgraph.graph import START, END, StateGraph
from agenticRAG.workflow_checkpoint import (
    checkpoint_database_path,
    checkpoint_run,
    invoke_workflow,
)
from webapp_core.runtime.workflow_runs import WorkflowRuns, WorkflowConflict
from webapp_core.runtime.session_store import SessionStore, ChatSession
from tests.school import test_school_permissions as permission_fixtures


class CheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_writer_does_not_block_stream_event_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            runs = WorkflowRuns(Path(folder) / "runs.sqlite3")
            run = runs.claim("owner", "chat", {"mode": "deepsearch", "message": "Q"})
            db = sqlite3.connect(checkpoint_database_path(runs.path))
            db.execute("CREATE TABLE held (id INTEGER)")
            db.execute("BEGIN IMMEDIATE")
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(run.emit, "delta", {"text": "text"}), 1
                )
                with runs.connect() as events:
                    self.assertEqual(
                        events.execute(
                            "SELECT count(*) FROM workflow_events"
                        ).fetchone()[0],
                        1,
                    )
            finally:
                db.rollback()
                db.close()
                run.close()

    async def test_process_exit_releases_lock_and_keeps_completed_node(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "crash.sqlite3")
            code = """
import asyncio, os, sys
from langgraph.graph import StateGraph, START, END
from agenticRAG.workflow_checkpoint import checkpoint_run, invoke_workflow
from webapp_core.runtime.workflow_runs import WorkflowRuns
runs=WorkflowRuns(sys.argv[1]); run=runs.claim('owner','chat',{'mode':'deepsearch','message':'Q'})
async def retrieve(state): return {'evidence':'durable'}
async def answer(state): os._exit(23)
async def main():
 checkpoint_run.set((sys.argv[1],run.id))
 graph=StateGraph(dict); graph.add_node('retrieve',retrieve); graph.add_node('answer',answer)
 graph.add_edge(START,'retrieve'); graph.add_edge('retrieve','answer'); graph.add_edge('answer',END)
 await invoke_workflow(graph,'crash_test',{})
asyncio.run(main())
"""
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", code, path],
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 23, result.stderr.decode())
            runs = WorkflowRuns(path)
            public = runs.public("owner", "chat")
            self.assertEqual(public["status"], "interrupted")
            run = runs.claim("owner", "chat", {}, public["id"])
            token = checkpoint_run.set((path, run.id))
            try:

                async def retrieve(state):
                    self.fail("completed retrieval must not run again")

                async def answer(state):
                    return {"answer": state["evidence"]}

                graph = StateGraph(dict)
                graph.add_node("retrieve", retrieve)
                graph.add_node("answer", answer)
                graph.add_edge(START, "retrieve")
                graph.add_edge("retrieve", "answer")
                graph.add_edge("answer", END)
                self.assertEqual(
                    (await invoke_workflow(graph, "crash_test", {}))["answer"],
                    "durable",
                )
            finally:
                checkpoint_run.reset(token)
                run.close()

    async def test_nested_graph_resumes_after_reopening_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "runs.sqlite3")
            calls = {"retrieve": 0, "answer": 0}

            async def retrieve(state):
                calls["retrieve"] += 1
                return {"evidence": "saved evidence"}

            async def answer(state):
                calls["answer"] += 1
                if calls["answer"] == 1:
                    raise TimeoutError("model interrupted")
                return {"answer": state["evidence"]}

            async def child(state):
                graph = StateGraph(dict)
                graph.add_node("retrieve", retrieve)
                graph.add_node("answer", answer)
                graph.add_edge(START, "retrieve")
                graph.add_edge("retrieve", "answer")
                graph.add_edge("answer", END)
                return await invoke_workflow(graph, "child", {})

            for index in range(2):
                token = checkpoint_run.set((path, "same-request"))
                try:
                    graph = StateGraph(dict)
                    graph.add_node("child", child)
                    graph.add_edge(START, "child")
                    graph.add_edge("child", END)
                    if index == 0:
                        with self.assertRaises(TimeoutError):
                            await invoke_workflow(graph, "parent", {})
                    else:
                        self.assertEqual(
                            (await invoke_workflow(graph, "parent", {}))["answer"],
                            "saved evidence",
                        )
                finally:
                    checkpoint_run.reset(token)
            self.assertEqual(calls, {"retrieve": 1, "answer": 2})


class WorkflowRunTests(unittest.TestCase):
    def test_owner_isolation_active_lock_restart_and_obsolete_run(self):
        with tempfile.TemporaryDirectory() as folder:
            runs = WorkflowRuns(Path(folder) / "runs.db")
            run = runs.claim("alice", "chat", {"message": "Q", "mode": "deepsearch"})
            with self.assertRaises(WorkflowConflict):
                WorkflowRuns(runs.path).claim("alice", "chat", {}, run.id)
            self.assertEqual(runs.public("alice", "chat")["status"], "running")
            run.close()  # A crashed process also releases the OS lock.
            restored = WorkflowRuns(runs.path)
            self.assertEqual(restored.public("alice", "chat")["status"], "interrupted")
            with self.assertRaises(LookupError):
                restored.claim("bob", "chat", {}, run.id)
            self.assertIsNone(restored.public("bob", "chat"))
            retry = restored.claim("alice", "chat", {}, run.id)
            self.assertTrue(retry.resume)
            retry.close()
            restored.claim(
                "alice", "chat", {"mode": "instant", "message": "new"}
            ).close()
            with self.assertRaises(LookupError):
                restored.claim("alice", "chat", {}, run.id)

    def test_failed_answer_is_replaced_and_completed_save_is_idempotent(self):
        store = SessionStore(lambda _: None)
        memory = Mock()
        session = ChatSession("chat", "title", memory=memory)
        args = dict(
            question="Q",
            requested_mode="deepsearch",
            mode_used="deepsearch",
            elapsed_ms=1,
        )
        store.update_session_after_answer(
            session,
            answer="half",
            message_details={
                "workflow_run_id": "run",
                "explainability": {"status": "error"},
            },
            **args,
        )
        memory.update.assert_not_called()
        for _ in range(2):
            store.update_session_after_answer(
                session,
                answer="complete",
                message_details={
                    "workflow_run_id": "run",
                    "explainability": {"status": "done"},
                },
                **args,
            )
        self.assertEqual(len(session.messages), 2)
        self.assertEqual(session.turns, [("Q", "complete")])
        memory.update.assert_called_once_with("Q", "complete")


class WorkflowRecoveryHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        permission_fixtures.SchoolPermissionsTests.setUpClass()

    def setUp(self):
        from webapp_core import config

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config_patch = patch.object(
            config, "WEB_CHAT_STORE_PATH", str(Path(self.temporary.name) / "chats.json")
        )
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.fixture = permission_fixtures.SchoolPermissionsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app
        self.client = self.fixture.student
        self.calls = {"retrieve": 0, "generate": 0}
        self.prepare_service(self.app)

    def prepare_service(self, app):
        service = self.fixture.webapp.get_chat_service(app)
        service._match_code_analysis_request = lambda *a, **kw: None
        service._match_problem_tutoring_request = lambda *a, **kw: None
        service._fast_smalltalk_result_bundle = lambda **kw: None

        async def retrieve(**kwargs):
            self.calls["retrieve"] += 1
            return {
                "question": "Q",
                "sub_questions": [],
                "subquery_results": [],
                "query_attempt": 0,
            }

        async def stream(**kwargs):
            self.calls["generate"] += 1
            kwargs["emit_text"](
                "partial" if self.calls["generate"] == 1 else "complete"
            )
            if self.calls["generate"] == 1:
                raise TimeoutError("interrupted after a chunk")
            return "complete"

        service._run_deepsearch_plan_state = retrieve
        service._stream_llm_text = stream
        service._schedule_chat_title_refinement = lambda *a: None

    def post(self, chat, **payload):
        response = self.client.post(f"/api/chats/{chat}/messages/stream", json=payload)
        self.assertEqual(response.status_code, 200)
        events = []
        for block in response.get_data(as_text=True).split("\n\n"):
            lines = block.splitlines()
            kind = next((x[7:] for x in lines if x.startswith("event: ")), "")
            data = next(
                (json.loads(x[6:]) for x in lines if x.startswith("data: ")), {}
            )
            events.append((kind, data))
        return events

    def test_completed_replay_allows_next_request_on_same_http_session(self):
        from threading import Thread
        import requests
        from werkzeug.serving import make_server

        self.calls["generate"] = 1
        chat = self.client.post("/api/chats", json={"mode": "deepsearch"}).json[
            "chat_id"
        ]
        self.post(chat, message="Q", mode="deepsearch", subjects=["C_program"])
        before = self.client.get(f"/api/chats/{chat}").json
        server = make_server("127.0.0.1", 0, self.app, threaded=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with requests.Session() as client:
                base = f"http://127.0.0.1:{server.server_port}"
                client.post(
                    base + "/api/identity/login",
                    json=self.fixture.credentials("student"),
                    timeout=3,
                ).raise_for_status()
                for _ in range(2):
                    response = client.post(
                        base + f"/api/chats/{chat}/messages/stream",
                        json={
                            "message": "ignored",
                            "resume_run_id": before["workflow_run"]["id"],
                        },
                        timeout=3,
                    )
                    response.raise_for_status()
                    self.assertIn("event: done", response.text)
                    after = client.get(base + f"/api/chats/{chat}", timeout=3).json()
                    self.assertEqual(after["messages"], before["messages"])
                self.assertEqual(self.calls, {"retrieve": 1, "generate": 2})
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    def test_failed_request_resumes_on_new_app_without_retrieval_or_duplicate_messages(
        self,
    ):
        chat = self.client.post("/api/chats", json={"mode": "deepsearch"}).json[
            "chat_id"
        ]
        self.post(chat, message="Q", mode="deepsearch", subjects=["C_program"])
        public = self.client.get(f"/api/chats/{chat}").json
        self.assertEqual(public["workflow_run"]["status"], "failed")
        run_id = public["workflow_run"]["id"]
        denied = self.fixture.other_student.post(
            f"/api/chats/{chat}/messages/stream",
            json={"message": "Q", "resume_run_id": run_id},
        )
        self.assertEqual(denied.status_code, 404)
        self.app = self.fixture.webapp.create_app(learning_store_path=self.fixture.path)
        self.fixture.webapp.get_store(self.app).load_sessions_from_disk()
        self.prepare_service(self.app)
        self.client = self.app.test_client()
        self.assertEqual(
            self.client.post(
                "/api/identity/login", json=self.fixture.credentials("student")
            ).status_code,
            200,
        )
        events = self.post(
            chat, message="ignored", resume_run_id=run_id, subjects=["cybersec_lab"]
        )
        self.assertEqual(
            [data["text"] for kind, data in events if kind == "delta"], ["complete"]
        )
        self.assertEqual(self.calls, {"retrieve": 1, "generate": 2})
        for _ in range(2):
            self.post(chat, message="ignored", resume_run_id=run_id)
        self.assertEqual(self.calls, {"retrieve": 1, "generate": 2})
        public = self.client.get(f"/api/chats/{chat}").json
        self.assertEqual(public["workflow_run"]["status"], "completed")
        self.assertEqual(len(public["messages"]), 2)
        self.assertEqual(public["messages"][-1]["content"], "complete")
        self.assertEqual(
            self.client.post(f"/api/chats/{chat}/delete", json={}).status_code, 200
        )
        runs = self.app.extensions["agenticrag.workflow_runs"]
        with sqlite3.connect(checkpoint_database_path(runs.path)) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0
            )
        with runs.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM workflow_runs").fetchone()[0], 0
            )
