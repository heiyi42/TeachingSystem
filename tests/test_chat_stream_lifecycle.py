from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock, patch

import requests
from werkzeug.serving import make_server

from tests import test_workflow_checkpoints as checkpoint_fixtures
from webapp_core.workflow_runs import WorkflowRuns, WorkflowConflict


class StreamLifecycleTests(unittest.TestCase):
    setUpClass = checkpoint_fixtures.WorkflowRecoveryHttpTests.__dict__["setUpClass"]
    prepare_service = checkpoint_fixtures.WorkflowRecoveryHttpTests.prepare_service

    def setUp(self):
        checkpoint_fixtures.WorkflowRecoveryHttpTests.setUp(self)
        self.service = self.fixture.webapp.get_chat_service(self.app)
        self.started = Event()
        self.cleaned = Event()
        self.release = Event()
        self.addCleanup(self.release.set)
        self.generations = 0
        self.prefix = True
        self.memory = Mock(summary="", recent_turns=[])
        self.memory.build_augmented_question.side_effect = lambda question: question

        async def stream(**kwargs):
            self.generations += 1
            if self.prefix:
                kwargs["emit_text"]("保留这段回答")
            self.started.set()
            try:
                while not self.release.is_set():
                    await asyncio.sleep(0.01)
                kwargs["emit_text"]("，接收完毕。")
                return "保留这段回答，接收完毕。"
            finally:
                await asyncio.sleep(0.02)
                self.cleaned.set()

        self.service._stream_llm_text = stream
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.http = requests.Session()
        self.addCleanup(self.http.close)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.http.post(
            self.base + "/api/identity/login",
            json=self.fixture.credentials("student"),
            timeout=3,
        ).raise_for_status()

    def start(self, mode="instant"):
        chat = self.client.post("/api/chats", json={"mode": mode}).json["chat_id"]
        session = self.fixture.webapp.get_store(self.app).get_session(chat)
        session.memory = self.memory
        response = self.http.post(
            self.base + f"/api/chats/{chat}/messages/stream",
            json={"mode": mode, "message": "解释指针", "subjects": ["C_program"]},
            stream=True,
            timeout=5,
        )
        response.raise_for_status()
        self.addCleanup(response.close)
        self.assertTrue(self.started.wait(3))
        run = self.client.get(f"/api/chats/{chat}").json["workflow_run"]
        return chat, run, response

    def wait_status(self, chat, expected):
        for _ in range(200):
            value = self.client.get(f"/api/chats/{chat}").json
            if value["workflow_run"]["status"] == expected:
                # Terminal status can be written just before final events and unlock.
                try:
                    lock = self.app.extensions["agenticrag.workflow_runs"]._lock(chat)
                except WorkflowConflict:
                    pass
                else:
                    lock.close()
                    return value
            time.sleep(0.01)
        self.fail(f"did not reach {expected}: {value['workflow_run']}")

    def cancel(self, chat, run):
        return self.client.post(
            f"/api/chats/{chat}/runs/{run['id']}/cancel",
            json={"execution_id": run["execution_id"]},
        )

    def replay(self, chat, run, after=0):
        return self.client.get(
            f"/api/chats/{chat}/runs/{run['id']}/events?execution_id={run['execution_id']}&after={after}"
        )

    def test_disconnect_replays_prefix_then_follows_same_generation(self):
        chat, run, response = self.start()
        response.close()
        self.assertFalse(self.cleaned.is_set())
        follow = self.http.get(
            self.base + f"/api/chats/{chat}/runs/{run['id']}/events",
            params={"execution_id": run["execution_id"]},
            stream=True,
            timeout=5,
        )
        self.addCleanup(follow.close)
        lines = follow.iter_lines(chunk_size=1, decode_unicode=True)
        prefix = []
        for line in lines:
            prefix.append(line)
            if "保留这段回答" in line:
                break
        self.assertEqual(self.generations, 1)
        self.release.set()
        rest = "\n".join(lines)
        self.assertIn("event: done", rest)
        self.assertIn("接收完毕", rest)
        complete = self.wait_status(chat, "completed")
        self.assertEqual(len(complete["messages"]), 2)
        ids = [
            int(line[4:])
            for line in prefix + rest.splitlines()
            if line.startswith("id: ")
        ]
        self.assertEqual(ids, sorted(set(ids)))
        tail = self.replay(chat, run, ids[-1]).text
        self.assertNotIn("event: delta", tail)
        self.cancel(chat, run)
        self.assertEqual(
            self.wait_status(chat, "completed")["workflow_run"]["status"], "completed"
        )
        self.memory.update.assert_called_once()

    def test_stop_cancels_producer_keeps_partial_and_restarts_original_request(self):
        chat, run, response = self.start()
        response.close()
        self.assertEqual(self.cancel(chat, run).status_code, 200)
        state = self.wait_status(chat, "cancelled")
        self.assertTrue(self.cleaned.is_set())
        self.assertEqual(state["messages"][-1]["content"], "保留这段回答")
        self.assertEqual(
            state["messages"][-1]["details"]["explainability"]["status"], "cancelled"
        )
        self.assertFalse(state["workflow_run"]["can_resume"])
        self.memory.update.assert_not_called()
        self.assertIn("event: done", self.replay(chat, run).text)
        self.release.set()
        result = self.client.post(
            f"/api/chats/{chat}/messages/stream",
            json={"message": "ignored", "restart_run_id": run["id"]},
        )
        self.assertIn("event: done", result.text)
        state = self.wait_status(chat, "completed")
        self.assertEqual(state["messages"][-2]["content"], "解释指针")
        self.assertEqual(self.generations, 2)
        self.assertEqual(self.cancel(chat, run).status_code, 404)
        self.assertEqual(self.replay(chat, run).status_code, 404)

    def test_terminal_status_snapshot_includes_concurrently_committed_messages(self):
        chat = self.client.post("/api/chats", json={"mode": "instant"}).json["chat_id"]
        session = self.fixture.webapp.get_store(self.app).get_session(chat)
        runs = self.app.extensions["agenticrag.workflow_runs"]

        def commit_before_status(*args):
            with session.lock:
                session.messages = [{"role": "assistant", "content": "刚保存的回答"}]
            return {"status": "cancelled"}

        with patch.object(runs, "public", side_effect=commit_before_status):
            result = self.client.get(f"/api/chats/{chat}").json
        self.assertEqual(result["workflow_run"]["status"], "cancelled")
        self.assertEqual(result["messages"][-1]["content"], "刚保存的回答")

    def test_cancel_before_text_and_access_control(self):
        self.prefix = False
        chat, run, response = self.start()
        response.close()
        foreign = self.fixture.other_student
        self.assertIn(
            foreign.post(
                f"/api/chats/{chat}/runs/{run['id']}/cancel",
                json={"execution_id": run["execution_id"]},
            ).status_code,
            (403, 404),
        )
        self.assertIn(
            foreign.get(
                f"/api/chats/{chat}/runs/{run['id']}/events?execution_id={run['execution_id']}"
            ).status_code,
            (403, 404),
        )
        self.assertEqual(
            self.cancel(chat, {**run, "execution_id": "stale"}).status_code, 404
        )
        self.assertFalse(self.cleaned.is_set())
        self.cancel(chat, run)
        state = self.wait_status(chat, "cancelled")
        self.assertEqual(state["messages"][-1]["content"], "已停止生成。")
        self.memory.update.assert_not_called()

    def test_deep_stop_resume_reuses_checkpoint_and_rejects_old_execution(self):
        chat, run, response = self.start("deepsearch")
        response.close()
        self.cancel(chat, run)
        self.assertTrue(
            self.wait_status(chat, "cancelled")["workflow_run"]["can_resume"]
        )
        self.started.clear()
        response = self.http.post(
            self.base + f"/api/chats/{chat}/messages/stream",
            json={"message": "ignored", "resume_run_id": run["id"]},
            stream=True,
            timeout=5,
        )
        self.addCleanup(response.close)
        self.assertTrue(self.started.wait(3))
        current = self.client.get(f"/api/chats/{chat}").json["workflow_run"]
        self.assertNotEqual(run["execution_id"], current["execution_id"])
        self.assertEqual(self.cancel(chat, run).status_code, 404)
        self.assertEqual(self.replay(chat, run).status_code, 404)
        self.release.set()
        self.assertIn("event: done", response.text)
        state = self.wait_status(chat, "completed")
        self.assertEqual(self.calls["retrieve"], 1)
        self.assertEqual(self.generations, 2)
        self.assertEqual(len(state["messages"]), 2)
        self.memory.update.assert_called_once()


class EventStorageTests(unittest.TestCase):
    def test_migration_replay_cursor_permissions_and_deletion(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runs.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute(
                    "CREATE TABLE workflow_runs (id TEXT PRIMARY KEY, owner TEXT, chat_id TEXT, payload TEXT, context TEXT DEFAULT '{}', progress TEXT DEFAULT '{}', result TEXT, status TEXT, error TEXT DEFAULT '', version INTEGER DEFAULT 1, created REAL, updated REAL)"
                )
            runs = WorkflowRuns(path)
            run = runs.claim("owner", "chat", {"mode": "instant", "message": "Q"})
            run.emit("delta", {"text": "one"})
            run.emit("delta", {"text": "two"})
            run.emit("stream_end", {})
            run.close()
            args = ("owner", "chat", run.id, run.row["execution_id"])
            replay = "".join(runs.iter_events(*args, after=1))
            self.assertNotIn('"one"', replay)
            self.assertIn('"two"', replay)
            self.assertNotIn(
                '"two"', "".join(runs.iter_events(*args, can_read=lambda: False))
            )
            runs.delete_chat("chat", lambda _: True)
            with runs.connect() as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0], 0
                )


class ToolCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_tool_kills_and_reaps_process(self):
        from webapp_core.code_analysis_service import CodeAnalysisService
        from unittest.mock import AsyncMock

        process = Mock(returncode=None)
        started = asyncio.Event()
        calls = 0

        async def communicate():
            nonlocal calls
            calls += 1
            started.set()
            if calls == 1:
                await asyncio.Future()
            return b"", b""

        process.communicate = AsyncMock(side_effect=communicate)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(
                CodeAnalysisService.__new__(CodeAnalysisService)._run_tool(
                    ["test"], timeout_s=30
                )
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.await_count, 2)

    async def test_cancellation_closes_llm_stream_while_first_chunk_callback_waits(
        self,
    ):
        from types import SimpleNamespace
        from tests.test_chat_retrieval_support import ChatRetrievalSupportTests

        service = ChatRetrievalSupportTests._build_service()
        closed = asyncio.Event()
        started = asyncio.Event()

        async def stream(_):
            try:
                yield SimpleNamespace(content="first")
                await asyncio.Future()
            finally:
                closed.set()

        async def first():
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(
            service._stream_llm_text(
                llm_client=SimpleNamespace(astream=stream),
                prompt="Q",
                timeout_s=30,
                emit_text=lambda _: None,
                before_first_emit=first,
            )
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
