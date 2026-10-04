from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from webapp_core.chat.chat_service import ChatService


class ChatDeepSearchTests(unittest.IsolatedAsyncioTestCase):

    def test_response_language_follows_question(self):
        for question, expected in [
            ("请解释死锁的四个必要条件。", "zh"),
            ("Explain the four necessary conditions for deadlock.", "en"),
            ("请用英文解释死锁。", "en"),
            ("Explain deadlock，请用中文回答。", "zh"),
        ]:
            with self.subTest(question=question):
                self.assertEqual(ChatService._response_language_from_question(question), expected)


    async def test_brief_request_keeps_necessary_evidence_without_forced_outline(self):
        service = self._build_service()
        question = "根据课程资料简短解释指针，并给出出处。"
        prompt = service._apply_answer_style_to_question(
            question,
            user_question=question,
            subject_id="C_program",
            mode="auto",
            response_language="zh",
        )
        self.assertIn("真实检索出处", prompt)
        self.assertNotIn("## 扩展", prompt)
        service._stream_routed_deepsearch_mode = AsyncMock(
            return_value={"answer": "有依据的回答"}
        )
        await service._run_multi_subject_deep_stream(
            user_question=question,
            question=question,
            thread_id="test",
            timeout_s=30,
            subject_ids=["C_program"],
        )
        style = service._stream_routed_deepsearch_mode.call_args.kwargs[
            "answer_style_instruction"
        ]
        self.assertIn("真实检索出处", style)
        self.assertNotIn("## 扩展", style)
        self.assertEqual(service._requested_brief_style("不要简短回答，请详细讲解"), "")


    @staticmethod
    def _build_service() -> ChatService:
        service = ChatService.__new__(ChatService)
        service.subject_catalog = {
            subject_id: {
                "id": subject_id,
                "label": label,
                "working_dir": f"/tmp/{subject_id}",
            }
            for subject_id, label in ChatService.SUBJECT_LABELS.items()
        }
        return service


    async def test_run_multi_subject_deep_stream_uses_subquestion_routed_chain(
        self,
    ) -> None:
        service = self._build_service()
        captured: dict[str, object] = {}

        async def fake_stream_routed_deepsearch_mode(**kwargs: object):
            captured["kwargs"] = dict(kwargs)
            emit_text = kwargs.get("emit_text")
            if callable(emit_text):
                emit_text("子问题路由后的最终回答")
            return {
                "mode_used": "deepsearch",
                "answer": "子问题路由后的最终回答",
            }

        service._stream_routed_deepsearch_mode = fake_stream_routed_deepsearch_mode  # type: ignore[method-assign]
        emitted: list[str] = []

        result = await service._run_multi_subject_deep_stream(
            user_question="解释栈溢出为什么同时和 C、OS、安全有关",
            question="解释栈溢出为什么同时和 C、OS、安全有关",
            thread_id="thread-1",
            timeout_s=12,
            subject_ids=["C_program", "operating_systems"],
            response_language="zh",
            emit_text=emitted.append,
        )

        self.assertEqual(result["mode_used"], "deepsearch")
        self.assertEqual(result["answer"], "子问题路由后的最终回答")
        self.assertEqual(result["route"]["chain"], "deepsearch-subquestion-routed")
        self.assertEqual(
            captured["kwargs"]["allowed_subject_ids"],
            ["C_program", "operating_systems"],
        )
        self.assertIn(
            "## 问题拆解与综合判断",
            captured["kwargs"]["answer_style_instruction"],
        )
        self.assertIn(
            "不要完整罗列每个子问题",
            captured["kwargs"]["answer_style_instruction"],
        )
        self.assertIn("[Answer language requirement]", captured["kwargs"]["question"])
        self.assertNotIn("[Answer format requirement]", captured["kwargs"]["question"])
        self.assertEqual(emitted, ["子问题路由后的最终回答"])

    async def test_single_subject_deepsearch_keeps_format_out_of_retrieval_question(
        self,
    ) -> None:
        service = self._build_service()
        captured: dict[str, object] = {}

        async def fake_stream_routed_deepsearch_mode(**kwargs: object):
            captured["kwargs"] = dict(kwargs)
            return {
                "mode_used": "deepsearch",
                "answer": "单学科深搜回答",
            }

        service._stream_routed_deepsearch_mode = fake_stream_routed_deepsearch_mode  # type: ignore[method-assign]

        result = await service._run_multi_subject_deep_stream(
            user_question="空指针是什么",
            question="空指针是什么",
            thread_id="thread-1",
            timeout_s=12,
            subject_ids=["C_program"],
            response_language="zh",
        )

        style = str(captured["kwargs"]["answer_style_instruction"])
        self.assertEqual(result["mode_used"], "deepsearch")
        self.assertEqual(captured["kwargs"]["allowed_subject_ids"], ["C_program"])
        self.assertIn("## 问题拆解与综合判断", style)
        self.assertNotIn("[学科专项补充]", style)
        self.assertNotIn("## 代码示例", style)
        self.assertIn("[Answer language requirement]", captured["kwargs"]["question"])
        self.assertNotIn("[Answer format requirement]", captured["kwargs"]["question"])

    async def test_deepsearch_uses_default_course_without_request_route(
        self,
    ) -> None:
        service = self._build_service()
        captured: dict[str, object] = {}

        async def fake_run_multi_subject_deep_stream(**kwargs: object):
            captured["kwargs"] = dict(kwargs)
            return {
                "mode_used": "deepsearch",
                "answer": "深搜结果",
            }

        service._run_multi_subject_deep_stream = fake_run_multi_subject_deep_stream  # type: ignore[method-assign]

        result = await service._stream_mode_with_retrieval(
            mode="deepsearch",
            subject_route=None,
            requested_subjects=None,
            user_question="解释这道综合题",
            augmented_question="解释这道综合题",
            thread_id="thread-1",
            timeout_s=12,
            response_language="zh",
        )

        self.assertEqual(result["mode_used"], "deepsearch")
        self.assertEqual(
            captured["kwargs"]["subject_ids"],
            ["C_program"],
        )

    async def test_stream_mode_with_retrieval_deepsearch_honors_explicit_subject_lock(
        self,
    ) -> None:
        service = self._build_service()
        captured: dict[str, object] = {}

        async def fake_run_multi_subject_deep_stream(**kwargs: object):
            captured["kwargs"] = dict(kwargs)
            return {
                "mode_used": "deepsearch",
                "answer": "深搜结果",
            }

        service._run_multi_subject_deep_stream = fake_run_multi_subject_deep_stream  # type: ignore[method-assign]

        result = await service._stream_mode_with_retrieval(
            mode="deepsearch",
            subject_route=None,
            requested_subjects=["C_program"],
            user_question="解释指针",
            augmented_question="解释指针",
            thread_id="thread-1",
            timeout_s=12,
            response_language="zh",
        )

        self.assertEqual(result["mode_used"], "deepsearch")
        self.assertEqual(captured["kwargs"]["subject_ids"], ["C_program"])

    async def test_retrieval_entry_rejects_instant(self) -> None:
        service = self._build_service()
        with self.assertRaisesRegex(ValueError, "does not use retrieval"):
            await service._stream_mode_with_retrieval(
                mode="instant",
                subject_route={},
                user_question="问题",
                augmented_question="问题",
                thread_id="test",
                timeout_s=10,
            )


if __name__ == "__main__":
    unittest.main()
