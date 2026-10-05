from __future__ import annotations

from contextlib import nullcontext
import unittest
from unittest.mock import AsyncMock, patch

import agenticRAG.agentic_answer as agentic_answer_module
import agenticRAG.agentic_nodes as agentic_nodes_module
from agenticRAG.agentic_schema import EvidenceCheck


class AgenticAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_evidence_review_checks_content_even_for_long_success_results(self):
        for status in ("success", "unknown"):
            for sufficient in (False, True):
                with self.subTest(status=status, sufficient=sufficient):
                    evidence = "家庭菜谱：清洗蔬菜后加水煮熟。" * 30
                    judge = AsyncMock(
                        return_value=EvidenceCheck(
                            sufficient=sufficient,
                            reason="模型评审结论",
                        )
                    )
                    state = {
                        "sub_questions": [
                            {"id": "sq1", "question": "LRU 如何选择淘汰页？"}
                        ],
                        "subquery_results": [
                            {
                                "sub_question_id": "sq1",
                                "subject_id": "operating_systems",
                                "query_status": status,
                                "answer": evidence,
                            }
                        ],
                        "query_attempt": 0,
                    }
                    with (
                        patch.object(agentic_nodes_module, "_judge_evidence", judge),
                        patch.object(agentic_nodes_module, "COMPLEX_MAX_RETRY", 1),
                    ):
                        result = await agentic_nodes_module.judge_subquestion_results(
                            state
                        )
                        self.assertEqual(result["needs_retry"], not sufficient)
                        self.assertEqual(
                            result["sub_questions"][0]["sufficient"], str(sufficient)
                        )
                        self.assertIn(evidence, judge.await_args.args[2])
                        judge.assert_awaited_once()
                        if not sufficient:
                            exhausted = (
                                await agentic_nodes_module.judge_subquestion_results(
                                    {**state, "query_attempt": 1}
                                )
                            )
                            self.assertFalse(exhausted["needs_retry"])
                            self.assertEqual(
                                exhausted["insufficient_subquestion_ids"], ["sq1"]
                            )

    async def test_empty_and_failed_retrieval_do_not_call_evidence_model(self):
        for status, answer in (
            ("success", ""),
            ("failure", "查询失败"),
            ("unknown", ""),
        ):
            with (
                self.subTest(status=status),
                patch.object(
                    agentic_nodes_module, "_judge_evidence", new_callable=AsyncMock
                ) as judge,
            ):
                result = await agentic_nodes_module.judge_subquestion_results(
                    {
                        "sub_questions": [{"id": "sq1", "question": "LRU？"}],
                        "subquery_results": [
                            {
                                "sub_question_id": "sq1",
                                "query_status": status,
                                "answer": answer,
                            }
                        ],
                    }
                )
                self.assertEqual(result["sub_questions"][0]["sufficient"], "False")
                judge.assert_not_awaited()

    def test_brief_answer_prompt_does_not_force_sections(self):
        prompt = agentic_nodes_module.build_final_answer_prompt(
            {
                "question": "一句话解释 LRU",
                "answer_style_instruction": "一句话回答，不套用固定章节。",
            }
        )
        self.assertIn("一句话回答，不套用固定章节", prompt)
        self.assertNotIn("必须优先使用这里列出的可见一级标题", prompt)
        self.assertNotIn("的标题组织", prompt)
        self.assertIn("不得编造出处", prompt)

    async def test_run_question_plan_state_requires_selected_course(self) -> None:
        with self.assertRaisesRegex(ValueError, "selected course"):
            await agentic_answer_module.run_question_plan_state("测试问题")

    def test_build_final_answer_prompt_uses_answer_style_instruction(self) -> None:
        prompt = agentic_nodes_module.build_final_answer_prompt(
            {
                "question": "解释栈溢出为什么同时和 C、OS、安全有关",
                "response_language": "zh",
                "answer_style_instruction": (
                    "请使用 Markdown，并优先按以下学习型结构回答：\n"
                    "1) `## 结论`；\n"
                    "2) `## 问题拆解与综合判断`；\n"
                    "3) `## 核心概念与机制`。"
                ),
                "sub_questions": [
                    {
                        "id": "sq1",
                        "question": "栈溢出在 C 中如何发生？",
                        "used_question": "栈溢出 C",
                        "target_subjects": ["C_program"],
                        "sufficient": "True",
                        "judge_reason": "证据充分",
                    }
                ],
                "subquery_results": [
                    {
                        "sub_question_id": "sq1",
                        "subject_id": "C_program",
                        "answer": (
                            "## 核心概念\n"
                            "栈缓冲区越界写入会覆盖相邻栈帧数据。\n"
                            "## 代码示例\n"
                            "char buf[16]; strcpy(buf, input);"
                        ),
                        "query_status": "success",
                    }
                ],
                "query_attempt": 0,
            }
        )

        self.assertIn("## 问题拆解与综合判断", prompt)
        self.assertIn("## 核心概念与机制", prompt)
        self.assertIn("不要完整罗列每个子问题", prompt)
        self.assertIn("不要沿用检索材料里的旧标题结构", prompt)
        self.assertIn("请基于给定子问题及其检索原文", prompt)

    async def test_run_question_plan_state_retries_by_subquestion(self) -> None:
        query_rounds: list[int] = []

        def fake_build_global_subquestion_plan(
            state: dict[str, object]
        ) -> dict[str, object]:
            self.assertEqual(state["question"], "测试问题")
            self.assertEqual(
                state["allowed_subject_ids"], ["C_program", "operating_systems"]
            )
            return {
                "sub_questions": [
                    {
                        "id": "sq1",
                        "question": "Q1",
                        "used_question": "Q1",
                        "query_mode": "local",
                        "top_k": 3,
                        "chunk_top_k": 5,
                        "target_subjects": [],
                    },
                    {
                        "id": "sq2",
                        "question": "Q2",
                        "used_question": "Q2",
                        "query_mode": "global",
                        "top_k": 4,
                        "chunk_top_k": 6,
                        "target_subjects": [],
                    },
                ],
                "subquery_tasks": [],
                "subquery_results": [],
                "query_attempt": 0,
                "needs_retry": False,
                "insufficient_subquestion_ids": [],
                "query_total_ms": "0",
            }

        def fake_build_subquery_tasks(state: dict[str, object]) -> dict[str, object]:
            for sub_question in state["sub_questions"]:
                self.assertEqual(sub_question["target_subjects"], ["C_program", "operating_systems"])
            return {
                "subquery_tasks": [
                    {
                        "task_id": "sq1::C_program",
                        "sub_question_id": "sq1",
                        "subject_id": "C_program",
                        "question": "Q1",
                        "used_question": state["sub_questions"][0]["used_question"],
                    },
                    {
                        "task_id": "sq2::operating_systems",
                        "sub_question_id": "sq2",
                        "subject_id": "operating_systems",
                        "question": "Q2",
                        "used_question": state["sub_questions"][1]["used_question"],
                    },
                ]
            }

        async def fake_query_subquestion_tasks(
            state: dict[str, object]
        ) -> dict[str, object]:
            query_rounds.append(int(state.get("query_attempt", 0)))
            return {
                "subquery_results": [
                    {
                        "sub_question_id": "sq1",
                        "subject_id": "C_program",
                        "question": "Q1",
                        "used_question": state["sub_questions"][0]["used_question"],
                        "answer": (
                            "第一轮结果" if len(query_rounds) == 1 else "第二轮结果"
                        ),
                        "query_status": "success",
                        "query_message": "",
                        "query_failure_reason": "",
                    },
                    {
                        "sub_question_id": "sq2",
                        "subject_id": "operating_systems",
                        "question": "Q2",
                        "used_question": "Q2",
                        "answer": "稳定结果",
                        "query_status": "success",
                        "query_message": "",
                        "query_failure_reason": "",
                    },
                ]
            }

        async def fake_judge_subquestion_results(
            state: dict[str, object],
        ) -> dict[str, object]:
            if len(query_rounds) == 1:
                return {
                    "sub_questions": [
                        {
                            **state["sub_questions"][0],
                            "sufficient": "False",
                            "judge_reason": "需要补充第二学科证据",
                            "rewritten_question": "",
                        },
                        {
                            **state["sub_questions"][1],
                            "sufficient": "True",
                            "judge_reason": "证据充分",
                            "rewritten_question": "",
                        },
                    ],
                    "needs_retry": True,
                    "insufficient_subquestion_ids": ["sq1"],
                }
            return {
                "sub_questions": [
                    {
                        **state["sub_questions"][0],
                        "sufficient": "True",
                        "judge_reason": "证据已充分",
                        "rewritten_question": "",
                    },
                    state["sub_questions"][1],
                ],
                "needs_retry": False,
                "insufficient_subquestion_ids": [],
            }

        def fake_prepare_subquestion_retry_plan(
            state: dict[str, object]
        ) -> dict[str, object]:
            self.assertEqual(state["insufficient_subquestion_ids"], ["sq1"])
            self.assertEqual(state["sub_questions"][0]["rewritten_question"], "Q1-改写")
            return {
                "query_attempt": 1,
                "sub_questions": [
                    {
                        **state["sub_questions"][0],
                        "used_question": "Q1-改写",
                        "query_mode": "hybrid",
                        "top_k": 5,
                        "chunk_top_k": 8,
                        "target_subjects": ["C_program", "operating_systems"],
                        "sufficient": "unknown",
                        "judge_reason": "",
                        "rewritten_question": "",
                    },
                    state["sub_questions"][1],
                ],
                "subquery_tasks": [],
                "subquery_results": [],
            }

        async def fake_rewrite_insufficient_subquestions(
            state: dict[str, object],
        ) -> dict[str, object]:
            self.assertEqual(state["insufficient_subquestion_ids"], ["sq1"])
            return {
                "sub_questions": [
                    {
                        **state["sub_questions"][0],
                        "rewritten_question": "Q1-改写",
                        "rewrite_reason": "补充第二学科限定",
                    },
                    state["sub_questions"][1],
                ]
            }

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with (
            patch.object(
                agentic_answer_module,
                "use_rag_working_dir",
                return_value=nullcontext(),
            ),
            patch.object(
                agentic_answer_module.asyncio,
                "to_thread",
                side_effect=fake_to_thread,
            ),
            patch.object(
                agentic_answer_module,
                "build_global_subquestion_plan",
                side_effect=fake_build_global_subquestion_plan,
            ),
            patch.object(
                agentic_answer_module,
                "build_subquery_tasks",
                side_effect=fake_build_subquery_tasks,
            ),
            patch.object(
                agentic_answer_module,
                "query_subquestion_tasks",
                side_effect=fake_query_subquestion_tasks,
            ),
            patch.object(
                agentic_answer_module,
                "judge_subquestion_results",
                side_effect=fake_judge_subquestion_results,
            ),
            patch.object(
                agentic_answer_module,
                "rewrite_insufficient_subquestions",
                side_effect=fake_rewrite_insufficient_subquestions,
            ),
            patch.object(
                agentic_answer_module,
                "prepare_subquestion_retry_plan",
                side_effect=fake_prepare_subquestion_retry_plan,
            ),
        ):
            state = await agentic_answer_module.run_question_plan_state(
                "测试问题",
                requested_mode="deepsearch",
                allowed_subject_ids=["C_program", "operating_systems"],
                subject_working_dirs={
                    "C_program": "/tmp/C_program",
                    "operating_systems": "/tmp/operating_systems",
                },
            )

        self.assertEqual(query_rounds, [0, 1])
        self.assertEqual(state["query_attempt"], 1)
        self.assertEqual(state["sub_questions"][0]["used_question"], "Q1-改写")
        self.assertEqual(
            state["sub_questions"][0]["target_subjects"],
            ["C_program", "operating_systems"],
        )
        self.assertEqual(state["subquery_results"][0]["answer"], "第二轮结果")
