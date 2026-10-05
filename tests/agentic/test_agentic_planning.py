from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import agenticRAG.agentic_nodes as nodes
from agenticRAG.agentic_schema import SubQuestionQueryPlan, SubQuestionRewrite


class _FakeStructuredInvoker:
    def __init__(self, result: object) -> None:
        self.result = result
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> object:
        self.prompts.append(prompt)
        return self.result


class _FakeAsyncStructuredInvoker:
    def __init__(self, result: object) -> None:
        self.result = result
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> object:
        self.prompts.append(prompt)
        return self.result


class AgenticPlanningTests(unittest.TestCase):
    def test_subquery_tasks_use_selected_course_even_with_stale_targets(self) -> None:
        result = nodes.build_subquery_tasks({
            "allowed_subject_ids": ["operating_systems"],
            "sub_questions": [
                {"id": "sq1", "question": "Q1", "target_subjects": ["C_program"]},
                {"id": "sq2", "question": "Q2", "target_subjects": []},
            ],
        })
        self.assertEqual(
            [(task["sub_question_id"], task["subject_id"]) for task in result["subquery_tasks"]],
            [("sq1", "operating_systems"), ("sq2", "operating_systems")],
        )

    def test_build_global_subquestion_plan_preserves_dynamic_count(self) -> None:
        fake_planner = _FakeStructuredInvoker(
            SubQuestionQueryPlan(
                sub_questions=["Q1", "Q2", "Q3", "Q4"],
                query_modes=["local", "global", "hybrid", "global"],
                query_topks=[1, 2, 999, 4],
                query_chunk_topks=[-5, 3, 4, 5],
            )
        )

        with patch.object(nodes, "llm_subquestion_plan_struct", fake_planner):
            result = nodes.build_global_subquestion_plan(
                {
                    "question": "复杂问题",
                    "allowed_subject_ids": ["C_program", "operating_systems"],
                }
            )

        self.assertIn("所选课程：C_program, operating_systems", fake_planner.prompts[0])
        self.assertEqual(len(result["sub_questions"]), 4)
        self.assertEqual(
            [item["query_mode"] for item in result["sub_questions"]],
            ["local", "global", "hybrid", "global"],
        )
        self.assertEqual(result["sub_questions"][0]["top_k"], nodes.MIN_TOP_K)
        self.assertEqual(result["sub_questions"][2]["top_k"], nodes.MAX_TOP_K)
        self.assertEqual(
            result["sub_questions"][0]["chunk_top_k"],
            nodes.MIN_CHUNK_TOP_K,
        )
        self.assertEqual(result["subquery_tasks"], [])
        self.assertEqual(result["subquery_results"], [])

    def test_rewrite_insufficient_subquestions_calls_llm_rewriter(self) -> None:
        fake_rewriter = _FakeAsyncStructuredInvoker(
            SubQuestionRewrite(
                rewritten_question="C 语言空指针 NULL 的安全检查方式是什么？",
                reason="补充 C 语言和 NULL 安全检查限定",
            )
        )

        async def run_case() -> dict:
            with patch.object(nodes, "llm_subquestion_rewrite_struct", fake_rewriter):
                return await nodes.rewrite_insufficient_subquestions(
                    {
                        "query_attempt": 0,
                        "insufficient_subquestion_ids": ["sq1"],
                        "sub_questions": [
                            {
                                "id": "sq1",
                                "question": "空指针是什么？",
                                "used_question": "空指针是什么？",
                                "judge_reason": "缺少 NULL 检查场景",
                            }
                        ],
                        "subquery_results": [
                            {
                                "sub_question_id": "sq1",
                                "subject_id": "C_program",
                                "answer": "NULL 表示空指针。",
                                "query_status": "success",
                            }
                        ],
                    }
                )

        result = asyncio.run(run_case())
        self.assertIn("证据评审原因：缺少 NULL 检查场景", fake_rewriter.prompts[0])
        self.assertEqual(
            result["sub_questions"][0]["rewritten_question"],
            "C 语言空指针 NULL 的安全检查方式是什么？",
        )
        self.assertEqual(
            result["sub_questions"][0]["rewrite_reason"],
            "补充 C 语言和 NULL 安全检查限定",
        )

    def test_prepare_subquestion_retry_plan_records_rewrite_history(self) -> None:
        result = nodes.prepare_subquestion_retry_plan(
            {
                "query_attempt": 0,
                "allowed_subject_ids": ["C_program", "operating_systems"],
                "insufficient_subquestion_ids": ["sq1"],
                "sub_questions": [
                    {
                        "id": "sq1",
                        "question": "指针是什么？",
                        "used_question": "指针是什么？",
                        "rewritten_question": "C 语言数组指针是什么？",
                        "rewrite_reason": "补充数组指针限定",
                        "judge_reason": "缺少数组指针证据",
                        "query_mode": "local",
                        "top_k": 3,
                        "chunk_top_k": 4,
                        "target_subjects": ["C_program"],
                    }
                ],
            }
        )

        rewrite = result["retry_rewrites"][0]
        self.assertEqual(result["query_attempt"], 1)
        self.assertEqual(rewrite["sub_question_id"], "sq1")
        self.assertEqual(rewrite["previous_used_question"], "指针是什么？")
        self.assertEqual(rewrite["rewritten_question"], "C 语言数组指针是什么？")
        self.assertEqual(rewrite["applied_question"], "C 语言数组指针是什么？")
        self.assertEqual(rewrite["judge_reason"], "缺少数组指针证据")
        self.assertEqual(rewrite["rewrite_reason"], "补充数组指针限定")
        self.assertEqual(result["sub_questions"][0]["used_question"], "C 语言数组指针是什么？")


if __name__ == "__main__":
    unittest.main()
