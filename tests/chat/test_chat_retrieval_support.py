from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from webapp_core import config as cfg
import webapp_core.chat.chat_retrieval_support as retrieval_support_module
from webapp_core.chat.chat_service import ChatService


class _DummyStore:
    @staticmethod
    def content_to_text(value: object) -> str:
        return str(getattr(value, "content", value) or "")


class _FakeProblemTutoringService:
    def __init__(self, prepared: dict[str, object]) -> None:
        self.prepared = prepared
        self.calls: list[dict[str, object]] = []

    async def prepare(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return dict(self.prepared)


class _FakeStreamingLLM:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = list(chunks)

    async def astream(self, prompt: str):
        del prompt
        for chunk in self.chunks:
            yield SimpleNamespace(content=chunk)


class ChatRetrievalSupportTests(unittest.IsolatedAsyncioTestCase):
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
        service.store = _DummyStore()
        return service

    def test_pick_problem_tutoring_subject_prefers_requested_subject(self) -> None:
        service = self._build_service()

        subject_id = service._pick_problem_tutoring_subject(
            subject_route={
                "requested_subjects": ["operating_systems"],
                "primary_subject": "C_program",
                "ranked": [("C_program", 0.9)],
            },
            tutoring_candidate={"analysis": {"subject_id": "cybersec_lab"}},
        )

        self.assertEqual(subject_id, "operating_systems")

    async def test_run_problem_tutoring_stream_uses_prepared_prompt(self) -> None:
        service = self._build_service()
        fake_problem_tutoring = _FakeProblemTutoringService(
            {
                "prompt": "请按模板解答这道题。",
                "template": {"id": "os-banker"},
                "analysis": {"problem_type": "banker"},
                "solver_result": {"status": "success", "solver": "banker"},
                "learning_outline": {
                    "kind": "problem_tutoring",
                    "subject_label": "操作系统",
                    "problem_type_label": "银行家算法题",
                },
            }
        )
        service.problem_tutoring_service = fake_problem_tutoring
        captured: dict[str, object] = {}

        async def fake_stream_llm_text(**kwargs: object) -> str:
            captured.update(kwargs)
            emit_text = kwargs.get("emit_text")
            if callable(emit_text):
                emit_text("规则求解已完成。")
            return "规则求解已完成。"

        service._stream_llm_text = fake_stream_llm_text  # type: ignore[method-assign]
        emitted: list[str] = []

        result = await service._run_problem_tutoring_stream(
            user_question="请解答银行家算法题",
            augmented_question="请解答银行家算法题",
            mode="auto",
            timeout_s=12,
            subject_route={
                "requested_subjects": ["operating_systems"],
                "primary_subject": "operating_systems",
                "confidence": 0.95,
                "reason": "用户显式指定学科",
                "ranked": [("operating_systems", 1.0)],
            },
            response_language="zh",
            tutoring_candidate={"trigger": "explicit"},
            emit_text=emitted.append,
        )

        expected_prep_timeout = max(
            3,
            min(12, int(cfg.WEB_PROBLEM_TUTORING_PREP_TIMEOUT_S)),
        )
        self.assertEqual(len(fake_problem_tutoring.calls), 1)
        self.assertEqual(
            fake_problem_tutoring.calls[0]["working_dir"],
            "/tmp/operating_systems",
        )
        self.assertEqual(
            fake_problem_tutoring.calls[0]["timeout_s"],
            expected_prep_timeout,
        )
        self.assertEqual(result["answer"], "规则求解已完成。")
        self.assertEqual(result["route"]["chain"], "problem_tutoring")
        self.assertEqual(result["route"]["subject"], "operating_systems")
        self.assertEqual(result["route"]["template_id"], "os-banker")
        self.assertEqual(result["route"]["solver_status"], "success")
        self.assertEqual(result["route"]["reason"], "explicit")
        self.assertEqual(result["message_details"]["kind"], "problem_tutoring")
        self.assertEqual(result["message_details"]["subject_label"], "操作系统")
        self.assertEqual(emitted, ["规则求解已完成。"])
        self.assertEqual(captured["prompt"], "请按模板解答这道题。")

    async def test_stream_llm_text_flushes_accumulated_chunks(self) -> None:
        service = self._build_service()
        emitted: list[str] = []
        before_first_emit_counts: list[int] = []

        async def before_first_emit() -> None:
            before_first_emit_counts.append(len(emitted))

        answer = await service._stream_llm_text(
            llm_client=_FakeStreamingLLM(["Hello", " world.", " Done"]),
            prompt="irrelevant",
            timeout_s=3,
            emit_text=emitted.append,
            flush_chars=6,
            before_first_emit=before_first_emit,
        )

        self.assertEqual(answer, "Hello world. Done")
        self.assertEqual(emitted, ["Hello", " world.", " Done"])
        self.assertEqual(before_first_emit_counts, [0])

    async def test_secondary_evidence_returns_originals_without_generating_a_summary(
        self,
    ) -> None:
        service = self._build_service()
        rag = AsyncMock()
        rag.aquery_data.return_value = {
            "status": "success",
            "data": {"chunks": [{"chunk_id": "c", "content": "课程原文"}]},
        }
        rag.text_chunks.get_by_ids.return_value = [
            {"content": "课程原文", "full_doc_id": "d"}
        ]
        rag.full_docs.get_by_ids.return_value = [
            {"content": "[章节标题] 指针\n课程原文", "file_path": "/data/指针.txt"}
        ]
        with patch.object(
            retrieval_support_module, "get_rag", new=AsyncMock(return_value=rag)
        ) as get_rag:
            result = await service.retrieve_subject_evidence(
                "测试问题", "C_program", 10
            )
        get_rag.assert_awaited_once_with("/tmp/C_program")
        rag.aquery_llm.assert_not_awaited()
        self.assertEqual(result["answer"], "课程原文")
        self.assertEqual(result["evidence"][0]["source"], "指针.txt")
        self.assertEqual(result["query_status"], "success")

    async def test_run_deepsearch_plan_state_delegates_to_agenticrag_kernel(
        self,
    ) -> None:
        service = self._build_service()

        with patch.object(
            retrieval_support_module,
            "run_question_plan_state",
            new=AsyncMock(
                return_value={
                    "requested_mode": "deepsearch",
                    "effective_strategy": "deep",
                    "sub_questions": ["Q1", "Q2"],
                }
            ),
        ) as mocked_runner:
            state = await service._run_deepsearch_plan_state(question="测试问题")

        mocked_runner.assert_awaited_once()
        args = mocked_runner.await_args.args
        kwargs = mocked_runner.await_args.kwargs
        self.assertEqual(args, ("测试问题",))
        self.assertEqual(kwargs["requested_mode"], "deepsearch")
        self.assertNotIn("routing_question", kwargs)
        self.assertEqual(kwargs["response_language"], "zh")
        self.assertCountEqual(
            kwargs["allowed_subject_ids"],
            ["C_program"],
        )
        self.assertEqual(
            kwargs["subject_working_dirs"],
            {
                subject_id: f"/tmp/{subject_id}"
                for subject_id in ["C_program"]
            },
        )
        self.assertNotIn("route_subquestion_subjects", kwargs)
        self.assertEqual(state["requested_mode"], "deepsearch")
        self.assertEqual(state["effective_strategy"], "deep")
        self.assertEqual(state["sub_questions"], ["Q1", "Q2"])

    async def test_stream_routed_deepsearch_builds_prompt_before_streaming_output(
        self,
    ) -> None:
        service = self._build_service()
        emitted: list[str] = []
        stage_events: list[tuple[str, dict[str, object], int]] = []

        async def fake_plan_state(**kwargs: object) -> dict[str, object]:
            del kwargs
            return {
                "question": "原问题",
                "sub_questions": [
                    {
                        "id": "q1",
                        "question": "子问题",
                        "used_question": "子问题",
                        "query_mode": "hybrid",
                        "top_k": 30,
                        "chunk_top_k": 8,
                        "target_subjects": ["C_program"],
                    }
                ],
                "subquery_tasks": [
                    {
                        "task_id": "q1::C_program",
                        "sub_question_id": "q1",
                        "question": "子问题",
                        "used_question": "子问题",
                        "subject_id": "C_program",
                        "mode": "hybrid",
                        "top_k": 30,
                        "chunk_top_k": 8,
                    }
                ],
                "subquery_results": [
                    {
                        "sub_question_id": "q1",
                        "subject_id": "C_program",
                        "answer": "证据",
                        "query_status": "success",
                    }
                ],
                "retry_rewrites": [
                    {
                        "attempt": 1,
                        "sub_question_id": "q1",
                        "previous_used_question": "子问题",
                        "rewritten_question": "改写后的子问题",
                        "applied_question": "改写后的子问题",
                    }
                ],
                "query_attempt": 0,
                "insufficient_subquestion_ids": [],
                "needs_retry": False,
            }

        async def callback(stage: str, state: dict[str, object]) -> None:
            stage_events.append((stage, dict(state), len(emitted)))

        service._run_deepsearch_plan_state = fake_plan_state  # type: ignore[method-assign]
        with patch.object(
            retrieval_support_module, "llm", _FakeStreamingLLM(["最终回答"])
        ):
            result = await service._stream_routed_deepsearch_mode(
                question="原问题",
                timeout_s=10,
                answer_style_instruction=(
                    "请使用 Markdown，并优先按以下学习型结构回答：\n"
                    "1) `## 结论`；\n"
                    "2) `## 核心概念与机制`。\n"
                    "不要完整罗列每个子问题。"
                ),
                emit_text=emitted.append,
                workflow_stage_callback=callback,
            )

        stages = [item[0] for item in stage_events]
        self.assertEqual(stages, ["answer_generate_start", "answer_generate_end"])
        answer_end = stage_events[1]
        self.assertEqual(answer_end[2], 0)
        self.assertIn("final_answer_prompt", answer_end[1])
        self.assertIn("## 核心概念与机制", answer_end[1]["final_answer_prompt"])
        self.assertIn("不要完整罗列每个子问题", answer_end[1]["final_answer_prompt"])
        self.assertEqual(emitted, ["最终回答"])
        self.assertIn("final_answer_prompt", result["raw"])
        self.assertEqual(
            result["raw"]["retry_rewrites"][0]["rewritten_question"],
            "改写后的子问题",
        )
        self.assertEqual(result["raw"]["subquery_tasks"][0]["top_k"], 30)


if __name__ == "__main__":
    unittest.main()
