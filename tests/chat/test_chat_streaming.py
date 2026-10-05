from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from types import SimpleNamespace

from webapp_core.chat.chat_service import ChatService


class _DummyStore:
    def __init__(self) -> None:
        self.session = SimpleNamespace(
            mode="auto",
            title="existing",
            turns=[{"role": "user", "content": "history"}],
            lock=threading.Lock(),
            chat_id="chat-1",
            updated_at=0.0,
        )
        self.saved_answers: list[dict[str, object]] = []

    def normalize_mode(self, value: object) -> str:
        return "deepsearch" if value == "deepsearch" else "instant"

    def get_or_create_session(self, chat_id: str) -> SimpleNamespace:
        self.session.chat_id = chat_id
        return self.session

    def build_augmented_question(self, session: object, question: str) -> str:
        del session
        return question

    def is_placeholder_title(self, title: object) -> bool:
        return str(title or "").strip() in {"", "新聊天"}

    def make_assistant_meta(self, mode_used: object, elapsed_ms: object) -> str:
        return f"{mode_used}:{elapsed_ms}"

    def update_session_after_answer(self, session: object, **kwargs: object) -> None:
        del session
        self.saved_answers.append(dict(kwargs))

    def persist_sessions_safely(self) -> None:
        return None


class _FakeProblemTutoringService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def match_request(
        self,
        text: str,
        *,
        requested_subjects: list[str] | None = None,
        requested_by_user: bool = False,
    ) -> dict[str, object]:
        call = {
            "text": text,
            "requested_subjects": list(requested_subjects or []),
            "requested_by_user": requested_by_user,
        }
        self.calls.append(call)
        return {"trigger": "explicit", "question": text}


class _SlowGraphService:
    configured = True

    def local_subgraph(self, **kwargs: object) -> dict[str, object]:
        subject_ids = list(kwargs.get("subject_ids", []) or [])
        time.sleep(0.03)
        return {
            "ok": True,
            "nodes": [],
            "edges": [],
            "chunks": [],
            "subjectIds": subject_ids,
            "centerEntityIds": [],
        }


class ChatStreamingTests(unittest.TestCase):
    @staticmethod
    def _build_service() -> ChatService:
        service = ChatService.__new__(ChatService)
        service.store = _DummyStore()
        service._event_subscribers = set()
        service._event_subscribers_lock = threading.Lock()
        service.problem_tutoring_service = _FakeProblemTutoringService()
        service.subject_catalog = {
            subject_id: {
                "id": subject_id,
                "label": label,
                "working_dir": f"/tmp/{subject_id}",
            }
            for subject_id, label in ChatService.SUBJECT_LABELS.items()
        }
        service.submit_async = None
        service.run_async = asyncio.run
        return service

    def test_iter_answer_chunks_splits_text_by_chunk_size(self) -> None:
        chunks = ChatService.iter_answer_chunks("abcdefghij", 4)

        self.assertEqual(chunks, ["abcd", "efgh", "ij"])

    def test_sse_encode_formats_event_payload(self) -> None:
        payload = ChatService.sse_encode("meta", {"ok": True})

        self.assertEqual(payload, 'event: meta\ndata: {"ok": true}\n\n')

    def test_publish_event_reaches_subscriber_queue(self) -> None:
        service = self._build_service()
        subscriber = service._register_event_subscriber()

        try:
            service._publish_event("title_updated", {"chat_id": "chat-1"})
            payload = subscriber.get_nowait()
        finally:
            service._unregister_event_subscriber(subscriber)

        self.assertEqual(payload["event"], "title_updated")
        self.assertEqual(payload["data"]["chat_id"], "chat-1")

    def test_build_chat_message_stream_handler_rejects_empty_message(self) -> None:
        service = self._build_service()

        handler, error = service.build_chat_message_stream_handler("chat-1", {})

        self.assertIsNone(handler)
        self.assertEqual(error, ("message 不能为空", 400))

    def test_final_response_node_wraps_streamed_answer_output(self) -> None:
        service = self._build_service()

        service._match_code_analysis_request = lambda *args, **kwargs: None  # type: ignore[method-assign]
        service._match_problem_tutoring_request = lambda *args, **kwargs: None  # type: ignore[method-assign]
        service._fast_smalltalk_result_bundle = lambda *args, **kwargs: None  # type: ignore[method-assign]

        async def fake_decide_auto_route(**kwargs: object):
            return {"route": "direct"}

        async def fake_stream_llm_text(**kwargs: object) -> str:
            emit_text = kwargs["emit_text"]
            assert callable(emit_text)
            emit_text("第一段")
            await asyncio.sleep(0.02)
            emit_text("第二段")
            return "第一段第二段"

        service.decide_auto_route = fake_decide_auto_route  # type: ignore[method-assign]
        service._stream_llm_text = fake_stream_llm_text  # type: ignore[method-assign]

        handler, error = service.build_chat_message_stream_handler(
            "chat-1",
            {
                "message": "解释一下指针",
                "mode": "auto",
            },
        )

        self.assertIsNone(error)
        self.assertIsNotNone(handler)

        parsed_events: list[tuple[str, dict[str, object]]] = []
        for chunk in list(handler()):
            event_line = next(
                (line for line in chunk.splitlines() if line.startswith("event: ")),
                "",
            )
            data_line = next(
                (line for line in chunk.splitlines() if line.startswith("data: ")),
                "",
            )
            if not event_line or not data_line:
                continue
            parsed_events.append(
                (
                    event_line[len("event: ") :],
                    json.loads(data_line[len("data: ") :]),
                )
            )

        final_start_index = next(
            index
            for index, (event_name, data) in enumerate(parsed_events)
            if event_name == "workflow_node_start"
            and data.get("nodeId") == "final_response"
        )
        delta_indexes = [
            index
            for index, (event_name, _data) in enumerate(parsed_events)
            if event_name == "delta"
        ]
        final_end_index = next(
            index
            for index, (event_name, data) in enumerate(parsed_events)
            if event_name == "workflow_node_end"
            and data.get("nodeId") == "final_response"
        )
        final_end = parsed_events[final_end_index][1]
        self.assertFalse(any(
            name == "workflow_node_end" and payload.get("nodeId") == "retrieval_gate"
            for name, payload in parsed_events
        ))
        self.assertLess(final_start_index, delta_indexes[0])
        self.assertLess(delta_indexes[-1], final_end_index)
        self.assertEqual(final_end["nodeName"], "LLM输出")
        self.assertGreaterEqual(final_end["durationMs"], 1)

    def test_instant_streams_directly_even_with_explicit_subject(self) -> None:
        from unittest.mock import AsyncMock, Mock
        for subjects in ([], ["C_program"]):
            with self.subTest(subjects=subjects):
                service = self._build_service()
                service.graph_service = Mock(configured=True)
                service.decide_auto_route = AsyncMock(side_effect=AssertionError("不应判断检索"))
                service.decide_subject_route = AsyncMock(side_effect=AssertionError("不应路由知识库"))
                service._stream_mode_with_retrieval = AsyncMock(side_effect=AssertionError("不应检索"))

                async def stream(**kwargs):
                    self.assertIn("解释指针", kwargs["prompt"])
                    kwargs["emit_text"]("第一段")
                    kwargs["emit_text"]("第二段")
                    return "第一段第二段"

                service._stream_llm_text = stream
                handler, error = service.build_chat_message_stream_handler(
                    "chat-1", {"message": "解释指针", "mode": "instant", "subjects": subjects},
                )
                self.assertIsNone(error)
                events = []
                for block in handler():
                    lines = block.strip().splitlines()
                    event = next((line[7:] for line in lines if line.startswith("event: ")), "")
                    data = next((json.loads(line[6:]) for line in lines if line.startswith("data: ")), {})
                    events.append((event, data))
                self.assertEqual([data["text"] for event, data in events if event == "delta"], ["第一段", "第二段"])
                self.assertTrue(any(event == "done" for event, _ in events))
                self.assertTrue(any(event == "meta" and data.get("retrieval_used") is False for event, data in events))
                self.assertFalse(any(event == "graph_update" for event, _ in events))
                answer_ends = [data for event, data in events if event == "workflow_node_end" and data.get("nodeId") == "answer_generate"]
                self.assertEqual(len(answer_ends), 1)
                self.assertEqual(answer_ends[0]["nodeName"], "LLM 直答")
                service.decide_auto_route.assert_not_awaited()
                service.decide_subject_route.assert_not_awaited()
                service._stream_mode_with_retrieval.assert_not_awaited()
                service.graph_service.local_subgraph.assert_not_called()

    def test_match_problem_tutoring_request_requires_explicit_button(self) -> None:
        service = self._build_service()

        candidate = service._match_problem_tutoring_request(
            "这题怎么做：给定页面访问序列 7 0 1 2 0 3，页框数为 3，用 FIFO 页面置换，求缺页次数。",
            requested_subjects=["operating_systems"],
            requested_by_user=False,
        )

        self.assertIsNone(candidate)
        self.assertEqual(service.problem_tutoring_service.calls, [])

    def test_match_problem_tutoring_request_delegates_on_explicit_button(self) -> None:
        service = self._build_service()

        candidate = service._match_problem_tutoring_request(
            "这题怎么做：给定页面访问序列 7 0 1 2 0 3，页框数为 3，用 FIFO 页面置换，求缺页次数。",
            requested_subjects=["operating_systems"],
            requested_by_user=True,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(len(service.problem_tutoring_service.calls), 1)
        self.assertTrue(service.problem_tutoring_service.calls[0]["requested_by_user"])

    def test_deepsearch_without_subject_skips_gate_and_defaults_to_course(self) -> None:
        service = self._build_service()
        captured: dict[str, object] = {}

        service._match_code_analysis_request = lambda *args, **kwargs: None  # type: ignore[method-assign]
        service._match_problem_tutoring_request = lambda *args, **kwargs: None  # type: ignore[method-assign]
        service._fast_smalltalk_result_bundle = lambda *args, **kwargs: None  # type: ignore[method-assign]
        service.graph_service = _SlowGraphService()

        async def fake_decide_auto_route(**kwargs: object):
            captured["gate_kwargs"] = dict(kwargs)
            return {"route": "deep_retrieval"}

        async def forbidden_decide_subject_route(**kwargs: object):
            raise AssertionError(f"deepsearch 不应在拆题前调用请求级学科路由: {kwargs}")

        async def fake_stream_mode_with_retrieval(**kwargs: object):
            captured["stream_kwargs"] = dict(kwargs)
            callback = kwargs.get("workflow_stage_callback")
            sub_questions = [
                {
                    "id": "q1",
                    "question": "栈溢出在 C 里如何发生？",
                    "used_question": "栈溢出 C",
                    "query_mode": "hybrid",
                    "top_k": 30,
                    "chunk_top_k": 8,
                    "target_subjects": ["C_program"],
                    "sufficient": "True",
                    "judge_reason": "证据足够",
                }
            ]
            if callback is not None:
                await callback("deepsearch_plan_start", {})
                await callback("deepsearch_plan_end", {"sub_questions": sub_questions})
                await callback("deepsearch_retrieve_start", {"query_attempt": 0})
                await callback(
                    "deepsearch_retrieve_end",
                    {
                        "sub_questions": sub_questions,
                        "subquery_tasks": [
                            {
                                "task_id": "q1::C_program",
                                "sub_question_id": "q1",
                                "question": "栈溢出在 C 里如何发生？",
                                "used_question": "栈溢出 C",
                                "subject_id": "C_program",
                                "mode": "hybrid",
                                "top_k": 30,
                                "chunk_top_k": 8,
                            }
                        ],
                        "subquery_results": [{"sub_question_id": "q1"}],
                        "query_total_ms": "12",
                    },
                )
                await callback("deepsearch_review_start", {})
                await callback(
                    "deepsearch_review_end",
                    {"insufficient_subquestion_ids": []},
                )
                await callback("deepsearch_retry_skipped", {})
                await callback("answer_generate_start", {})
                await callback(
                    "answer_generate_end",
                    {
                        "sub_questions": sub_questions,
                        "final_answer_prompt": "最终回答 Prompt",
                        "final_answer_prompt_chars": 11,
                        "final_prompt_ms": 7,
                    },
                )
            return {
                "mode_used": "deepsearch",
                "answer": "深搜回答",
                "route": {"chain": "deepsearch-subquestion-routed"},
                "raw": {
                    "sub_questions": sub_questions,
                    "subquery_results": [{"sub_question_id": "q1"}],
                    "query_attempt": 0,
                    "insufficient_subquestion_ids": [],
                    "needs_retry": False,
                },
            }

        service.decide_auto_route = fake_decide_auto_route  # type: ignore[method-assign]
        service.decide_subject_route = forbidden_decide_subject_route  # type: ignore[method-assign]
        service._stream_mode_with_retrieval = fake_stream_mode_with_retrieval  # type: ignore[method-assign]

        handler, error = service.build_chat_message_stream_handler(
            "chat-1",
            {
                "message": "解释栈溢出和 C、OS、安全的关系",
                "mode": "deepsearch",
            },
        )

        self.assertIsNone(error)
        self.assertIsNotNone(handler)

        events = list(handler())
        done_payloads = []
        workflow_events: list[tuple[str, str, str]] = []
        parsed_events: list[tuple[str, dict[str, object]]] = []
        for chunk in events:
            event_line = next(
                (line for line in chunk.splitlines() if line.startswith("event: ")),
                "",
            )
            event_name = event_line[len("event: ") :] if event_line else ""
            data_line = next(
                (line for line in chunk.splitlines() if line.startswith("data: ")),
                "",
            )
            data = json.loads(data_line[len("data: ") :]) if data_line else {}
            if event_name:
                parsed_events.append((event_name, data))
            if event_name.startswith("workflow_node_"):
                workflow_events.append(
                    (
                        event_name,
                        str(data.get("nodeId", "")),
                        str(data.get("status", "")),
                    )
                )
            if not chunk.startswith("event: done\n"):
                continue
            self.assertTrue(data_line)
            done_payloads.append(data)

        self.assertEqual(len(done_payloads), 1)
        self.assertIn(
            ("workflow_node_start", "deepsearch_plan", "running"),
            workflow_events,
        )
        self.assertIn(
            ("workflow_node_end", "deepsearch_plan", "success"),
            workflow_events,
        )
        self.assertFalse(
            any(
                node in {"subject_route", "deepsearch_subject_route"}
                for _, node, _ in workflow_events
            )
        )
        plan_end_index = workflow_events.index(
            ("workflow_node_end", "deepsearch_plan", "success")
        )
        retrieve_start_index = workflow_events.index(
            ("workflow_node_start", "deepsearch_retrieve", "running")
        )
        self.assertLess(plan_end_index, retrieve_start_index)
        graph_end_index = workflow_events.index(
            ("workflow_node_end", "neo4j_subgraph", "success")
        )
        plan_start_index = workflow_events.index(
            ("workflow_node_start", "deepsearch_plan", "running")
        )
        self.assertLess(plan_start_index, graph_end_index)
        self.assertEqual(done_payloads[0]["mode_used"], "deepsearch")
        self.assertEqual(
            captured["stream_kwargs"]["subject_route"]["requested_subjects"],
            ["C_program"],
        )
        self.assertEqual(captured["stream_kwargs"]["requested_subjects"], [])
        self.assertEqual(
            done_payloads[0]["subject_route"]["reason"],
            "用户显式指定学科",
        )
        retrieve_end = next(
            data
            for event_name, data in parsed_events
            if event_name == "workflow_node_end"
            and data.get("nodeId") == "deepsearch_retrieve"
        )
        retrieve_trace = retrieve_end["details"]["deepsearchTrace"]
        self.assertEqual(retrieve_trace["subqueryTasks"][0]["queryMode"], "hybrid")
        self.assertEqual(retrieve_trace["subqueryTasks"][0]["topK"], 30)
        self.assertEqual(retrieve_trace["subqueryTasks"][0]["chunkTopK"], 8)
        answer_end_index = next(
            index
            for index, (event_name, data) in enumerate(parsed_events)
            if event_name == "workflow_node_end"
            and data.get("nodeId") == "answer_generate"
        )
        final_start_index = next(
            index
            for index, (event_name, data) in enumerate(parsed_events)
            if event_name == "workflow_node_start"
            and data.get("nodeId") == "final_response"
        )
        self.assertLess(answer_end_index, final_start_index)
        answer_trace = parsed_events[answer_end_index][1]["details"]["deepsearchTrace"]
        self.assertEqual(answer_trace["finalAnswerPrompt"], "最终回答 Prompt")
        explainability = done_payloads[0]["message_details"]["explainability"]
        self.assertEqual(explainability["mode"], "deepsearch")
        self.assertEqual(explainability["status"], "done")
        self.assertTrue(explainability["workflowSteps"])
        self.assertNotIn(
            "neo4j_subgraph",
            [step["nodeId"] for step in explainability["workflowSteps"]],
        )
        self.assertEqual(
            service.store.saved_answers[-1]["message_details"]["explainability"][
                "mode"
            ],
            "deepsearch",
        )

    def test_deepsearch_preserves_selected_courses_and_rewrites_without_gateway_node(
        self,
    ) -> None:
        service = self._build_service()
        captured: dict[str, object] = {}

        service._match_code_analysis_request = lambda *args, **kwargs: None  # type: ignore[method-assign]
        service._match_problem_tutoring_request = lambda *args, **kwargs: None  # type: ignore[method-assign]
        service._fast_smalltalk_result_bundle = lambda *args, **kwargs: None  # type: ignore[method-assign]

        async def forbidden_decide_auto_route(**kwargs: object):
            raise AssertionError(f"显式学科 DeepSearch 不应再调用检索网关 LLM: {kwargs}")

        async def forbidden_decide_subject_route(**kwargs: object):
            raise AssertionError(f"显式学科 DeepSearch 不应调用请求级学科路由: {kwargs}")

        async def fake_stream_mode_with_retrieval(**kwargs: object):
            captured["stream_kwargs"] = dict(kwargs)
            return {
                "mode_used": "deepsearch",
                "answer": "指针回答",
                "route": {"chain": "deepsearch-subquestion-routed"},
                "raw": {
                    "sub_questions": [
                        {
                            "id": "q1",
                            "question": "指针是什么？",
                            "used_question": "指针是什么？",
                            "query_mode": "hybrid",
                            "top_k": 30,
                            "chunk_top_k": 8,
                            "target_subjects": ["C_program"],
                            "sufficient": "False",
                            "judge_reason": "缺少数组指针证据",
                            "rewritten_question": "",
                        }
                    ],
                    "subquery_results": [{"sub_question_id": "q1"}],
                    "retry_rewrites": [
                        {
                            "attempt": 1,
                            "sub_question_id": "q1",
                            "question": "指针是什么？",
                            "previous_used_question": "指针是什么？",
                            "rewritten_question": "C 语言数组指针是什么？",
                            "applied_question": "C 语言数组指针是什么？",
                            "judge_reason": "缺少数组指针证据",
                            "rewrite_reason": "补充数组指针限定",
                            "query_mode": "hybrid",
                            "top_k": 35,
                            "chunk_top_k": 11,
                            "target_subjects": ["C_program"],
                        }
                    ],
                    "query_attempt": 1,
                    "insufficient_subquestion_ids": ["q1"],
                    "needs_retry": False,
                },
            }

        service.decide_auto_route = forbidden_decide_auto_route  # type: ignore[method-assign]
        service.decide_subject_route = forbidden_decide_subject_route  # type: ignore[method-assign]
        service._stream_mode_with_retrieval = fake_stream_mode_with_retrieval  # type: ignore[method-assign]

        handler, error = service.build_chat_message_stream_handler(
            "chat-1",
            {
                "message": "指针是什么",
                "mode": "deepsearch",
                "subjects": ["C_program"],
            },
        )

        self.assertIsNone(error)
        self.assertIsNotNone(handler)

        done_payloads = []
        for chunk in list(handler()):
            if not chunk.startswith("event: done\n"):
                continue
            data_line = next(
                (line for line in chunk.splitlines() if line.startswith("data: ")),
                "",
            )
            done_payloads.append(json.loads(data_line[len("data: ") :]))

        self.assertEqual(len(done_payloads), 1)
        self.assertEqual(captured["stream_kwargs"]["requested_subjects"], ["C_program"])
        explainability = done_payloads[0]["message_details"]["explainability"]
        workflow_node_ids = [step["nodeId"] for step in explainability["workflowSteps"]]
        self.assertNotIn("retrieval_gate", workflow_node_ids)
        self.assertIn("deepsearch_review", workflow_node_ids)
        self.assertIn("deepsearch_retry", workflow_node_ids)
        self.assertNotIn("neo4j_subgraph", workflow_node_ids)
        trace = explainability["deepsearchTrace"]
        rewrite = trace["retry"]["rewrites"][0]
        self.assertEqual(rewrite["previousUsedQuestion"], "指针是什么？")
        self.assertEqual(rewrite["rewrittenQuestion"], "C 语言数组指针是什么？")
        self.assertEqual(rewrite["appliedQuestion"], "C 语言数组指针是什么？")
        self.assertEqual(rewrite["rewriteReason"], "补充数组指针限定")


if __name__ == "__main__":
    unittest.main()
