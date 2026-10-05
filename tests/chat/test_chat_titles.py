from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.chat import test_chat_streaming
from webapp_core.chat import auto_runtime as auto
from webapp_core.runtime.session_store import ChatSession, SessionStore
from webapp_core import config as cfg


class ChatTitleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = test_chat_streaming.ChatStreamingTests._build_service()
        self.store = SessionStore(memory_for_thread=lambda _: None)
        self.store.persist_sessions_safely = Mock()
        self.service.store = self.store
        self.session = ChatSession(chat_id="title-test", title="新聊天 2")
        self.tasks = []

        def submit(coro):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

        self.service.submit_async = submit
        self.service._publish_event = Mock()

    async def test_only_first_question_is_used_even_before_answer_finishes(self):
        llm = AsyncMock(return_value=SimpleNamespace(content="页面置换算法"))
        with patch.object(auto, "auto_router_llm", SimpleNamespace(ainvoke=llm)):
            self.service._schedule_chat_title_refinement(self.session, "解释页面置换")
            self.service._schedule_chat_title_refinement(self.session, "解释进程")
            self.assertEqual(self.session.turns, [])
            self.assertTrue(self.session.to_public()["title_pending"])
            self.assertEqual(len(self.tasks), 1)
            await asyncio.gather(*self.tasks)
            self.service._schedule_chat_title_refinement(self.session, "第三个问题")
        llm.assert_awaited_once()
        self.assertIn("解释页面置换", llm.call_args.args[0])
        self.assertNotIn("解释进程", llm.call_args.args[0])
        self.assertEqual(self.session.title, "页面置换算法")
        self.assertFalse(self.session.to_public()["title_pending"])
        self.service._publish_event.assert_called_once()

    async def test_failure_is_not_retried_and_manual_title_is_preserved(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                self.session = ChatSession(chat_id="title-test", title="新聊天 2")
                llm = AsyncMock(return_value=SimpleNamespace(content="模型标题"))
                if failure:
                    llm.side_effect = TimeoutError("timeout")
                with patch.object(
                    auto, "auto_router_llm", SimpleNamespace(ainvoke=llm)
                ):
                    self.service._schedule_chat_title_refinement(
                        self.session, "新聊天如何使用"
                    )
                    if not failure:
                        self.session.title = "我的标题"
                    await asyncio.gather(*self.tasks)
                    self.service._schedule_chat_title_refinement(self.session, "新问题")
                self.assertFalse(self.session.title_pending)
                llm.assert_awaited_once()
                self.assertEqual(
                    self.session.title, "新聊天如何使用" if failure else "我的标题"
                )

    async def test_title_runs_alongside_answer_in_request_flow(self):
        self.store._sessions[self.session.chat_id] = self.session
        loop = asyncio.get_running_loop()
        self.service.submit_async = lambda coro: asyncio.run_coroutine_threadsafe(
            coro, loop
        )
        title_started = asyncio.Event()
        answer_started = asyncio.Event()

        async def title_model(prompt):
            title_started.set()
            await answer_started.wait()
            return SimpleNamespace(content="页面置换算法")

        async def answer_model(**kwargs):
            answer_started.set()
            await asyncio.wait_for(title_started.wait(), timeout=1)
            kwargs["emit_text"]("完整回答")
            return "完整回答"

        self.service._stream_llm_text = answer_model
        llm = AsyncMock(side_effect=title_model)
        with patch.object(auto, "auto_router_llm", SimpleNamespace(ainvoke=llm)):
            for question in ("解释页面置换", "再举个例子"):
                handler, error = self.service.build_chat_message_stream_handler(
                    self.session.chat_id, {"message": question, "mode": "instant"}
                )
                self.assertIsNone(error)
                events = await asyncio.to_thread(lambda: list(handler()))
                self.assertTrue(any("event: done" in event for event in events))
        llm.assert_awaited_once()
        self.assertEqual(len(self.session.turns), 2)
        self.assertEqual(self.session.title, "页面置换算法")

    async def test_attempt_survives_reload_without_completed_turn(self):
        self.session.title_generation_started = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            path.write_text(
                json.dumps({"sessions": [self.store.session_to_record(self.session)]})
            )
            with patch.object(cfg, "WEB_CHAT_STORE_PATH", str(path)):
                self.store.load_sessions_from_disk()
            restored = self.store.get_or_create_session("title-test")
            self.service._schedule_chat_title_refinement(restored, "后续问题")
            self.assertEqual(self.tasks, [])
            self.assertTrue(restored.title_generation_started)
