from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agenticRAG.answer_evidence import (
    answer_evidence,
    citation_result,
    load_query_evidence,
)
from agenticRAG.agentic_nodes import (
    build_final_answer_prompt,
    _query_subquery_task,
    query_subquestion_tasks,
)
from scripts.check_qa_sources import check_sources
from webapp_core.runtime.workflow_runs import WorkflowRuns
import tempfile


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def rag(self, content="互斥、占有且等待、不可抢占、循环等待。", parent=None):
        return SimpleNamespace(
            text_chunks=SimpleNamespace(
                get_by_ids=AsyncMock(
                    return_value=[
                        {
                            "content": content,
                            "full_doc_id": "doc",
                            "file_path": "old/wrong.txt",
                        }
                    ]
                )
            ),
            full_docs=SimpleNamespace(
                get_by_ids=AsyncMock(
                    return_value=[
                        {
                            "content": parent
                            or "[Document Title] Chapter 06 Deadlock\n" + content,
                            "file_path": "/private/Chapter_06.txt",
                        }
                    ]
                )
            ),
        )

    async def test_canonical_source_and_original_text_reach_final_prompt(self):
        text = "互斥、占有且等待、不可抢占、循环等待。"
        rag = self.rag(text)
        response = {
            "status": "success",
            "data": {
                "chunks": [
                    {
                        "chunk_id": "chunk",
                        "content": text,
                        "file_path": "wrong Chapter 04",
                    }
                ]
            },
            "llm_response": {"content": "捏造的第四章引用"},
        }
        rag.aquery_data = AsyncMock(return_value=response)
        with patch("agenticRAG.agentic_nodes.get_rag", AsyncMock(return_value=rag)):
            row = await _query_subquery_task(
                {"subject_working_dirs": {"operating_systems": "test"}},
                {
                    "task_id": "t",
                    "sub_question_id": "sq1",
                    "subject_id": "operating_systems",
                    "question": "死锁条件",
                    "used_question": "死锁条件",
                },
            )
        state = {
            "question": "死锁条件",
            "sub_questions": [{"id": "sq1", "question": "死锁条件"}],
            "subquery_results": [row],
        }
        prompt = build_final_answer_prompt(state)
        self.assertIn(text, prompt)
        self.assertIn("[E1]", prompt)
        self.assertIn("Chapter 06 Deadlock", prompt)
        self.assertNotIn("捏造", prompt)
        self.assertNotIn("wrong Chapter 04", prompt)
        self.assertNotIn("/private", prompt)
        self.assertEqual(row["evidence"][0]["source"], "Chapter_06.txt")
        self.assertEqual(row["task_id"], "t")
        self.assertEqual(row["sub_question_id"], "sq1")

    async def test_failed_query_rows_keep_task_identity_and_failure_reason(self):
        tasks = [
            {"task_id": "missing", "sub_question_id": "sq1", "question": "Q"},
            {
                "task_id": "unavailable",
                "sub_question_id": "sq2",
                "subject_id": "missing_course",
                "question": "Q",
            },
            {
                "task_id": "exception",
                "sub_question_id": "sq3",
                "subject_id": "C_program",
                "question": "Q",
            },
        ]
        with patch(
            "agenticRAG.agentic_nodes.get_rag",
            AsyncMock(side_effect=RuntimeError("offline")),
        ):
            state = await query_subquestion_tasks(
                {
                    "subject_working_dirs": {"C_program": "test"},
                    "subquery_tasks": tasks,
                }
            )
        rows = state["subquery_results"]
        self.assertEqual([r["task_id"] for r in rows], [t["task_id"] for t in tasks])
        self.assertEqual([r["sub_question_id"] for r in rows], ["sq1", "sq2", "sq3"])
        self.assertEqual([r["query_status"] for r in rows], ["failure"] * 3)
        self.assertEqual(
            [r["query_failure_reason"] for r in rows],
            ["missing_subject", "missing_working_dir", "exception"],
        )
        self.assertEqual(answer_evidence(state), [])

    async def test_empty_graph_retrieval_uses_current_vector_chunks(self):
        text = "互斥、占有且等待、不可抢占、循环等待。"
        rag = self.rag(text)
        rag.aquery_data = AsyncMock(
            side_effect=[
                {
                    "status": "success",
                    "data": {"entities": [{"name": "deadlock"}], "chunks": []},
                },
                {
                    "status": "success",
                    "data": {"chunks": [{"chunk_id": "c", "content": text}]},
                },
            ]
        )
        with patch("agenticRAG.agentic_nodes.get_rag", AsyncMock(return_value=rag)):
            row = await _query_subquery_task(
                {"subject_working_dirs": {"operating_systems": "test"}},
                {"subject_id": "operating_systems", "question": "死锁条件"},
            )
        self.assertEqual(row["mode"], "naive")
        self.assertEqual(row["query_status"], "success")
        self.assertEqual(
            rag.aquery_data.await_args_list[1].kwargs["param"].mode, "naive"
        )
        self.assertIn(text, row["answer"])

    async def test_missing_tampered_and_wrong_parent_evidence_are_rejected(self):
        for response, rag in [
            ({"data": {"chunks": []}}, self.rag()),
            (
                {"data": {"chunks": [{"chunk_id": "x", "content": "伪造文本"}]}},
                self.rag(),
            ),
            (
                {"data": {"chunks": [{"chunk_id": "x", "content": "真实片段"}]}},
                self.rag("真实片段", parent="错误所属文档"),
            ),
        ]:
            self.assertEqual(
                await load_query_evidence(response, rag, "operating_systems"), []
            )

    async def test_metadata_only_chunk_is_not_citable_evidence(self):
        text = "[章节标题] 指针\n[一级小节] sizeof | 数组退化\n[说明] 用于知识图谱"
        result = await load_query_evidence(
            {"data": {"chunks": [{"chunk_id": "c", "content": text}]}},
            self.rag(text),
            "C_program",
        )
        self.assertEqual(result, [])

    async def test_split_unicode_boundary_trimmed_only_when_parent_matches(self):
        text = "�原文中的完整句子�"
        result = await load_query_evidence(
            {"data": {"chunks": [{"chunk_id": "x", "content": text}]}},
            self.rag(text, parent="[Document Title] 标题\n原文中的完整句子"),
            "C_program",
        )
        self.assertEqual(result[0]["text"], "原文中的完整句子")

    async def test_numbered_original_lines_are_not_treated_as_metadata(self):
        text = "[1] 实际概念定义\n[2] 具体条件说明"
        result = await load_query_evidence(
            {"data": {"chunks": [{"chunk_id": "x", "content": text}]}},
            self.rag(text),
            "C_program",
        )
        self.assertEqual(result[0]["text"], text)

    def test_no_original_evidence_does_not_use_model_summary_as_source(self):
        prompt = build_final_answer_prompt(
            {
                "question": "Q",
                "sub_questions": [{"id": "sq1", "question": "Q"}],
                "subquery_results": [
                    {
                        "sub_question_id": "sq1",
                        "subject_id": "C_program",
                        "answer": "来自第999章的伪造内容",
                        "query_status": "success",
                    }
                ],
            }
        )
        self.assertIn("没有可核对", prompt)
        self.assertNotIn("来自第999章", prompt)

    def test_global_ids_deduplicate_and_preserve_subject_coverage(self):
        source = lambda subject, chunk: dict(
            subject_id=subject,
            chunk_id=chunk,
            text="原文",
            title="章节",
            source="章节.txt",
        )
        result = answer_evidence(
            {
                "subquery_results": [
                    {
                        "query_status": "success",
                        "evidence": [
                            source("C_program", "same"),
                            source("C_program", "other"),
                        ],
                    },
                    {
                        "query_status": "success",
                        "evidence": [
                            source("operating_systems", "same"),
                            source("C_program", "same"),
                        ],
                    },
                ]
            }
        )
        self.assertEqual([r["id"] for r in result], ["E1", "E2", "E3"])
        self.assertEqual(result[1]["subject_id"], "operating_systems")
        answer, citations = citation_result("结论[E1]，另一条[E999]", result)
        self.assertNotIn("[E999]", answer)
        self.assertIn("出处未核实", answer)
        self.assertEqual(citations["status"], "invalid")
        self.assertEqual([r["id"] for r in citations["sources"] if r["cited"]], ["E1"])

    def test_prompt_budget_keeps_complete_chunks(self):
        source = dict(
            subject_id="C_program", title="章节", source="a.txt", text="a" * 25000
        )
        result = answer_evidence(
            {
                "subquery_results": [
                    {
                        "query_status": "success",
                        "evidence": [dict(source, chunk_id=str(i)) for i in range(4)],
                    }
                ]
            }
        )
        self.assertEqual(len(result), 2)
        self.assertTrue(all(len(r["text"]) == 25000 for r in result))

    def test_benchmark_detects_wrong_source_and_unknown_id(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "C_program"
            path.mkdir()
            (path / "kv_store_text_chunks.json").write_text(
                json.dumps({"c": {"content": "真实原文", "file_path": "right.txt"}})
            )
            errors, count = check_sources(
                "错误结论[E1] 与 [E2]",
                {
                    "sources": [
                        {
                            "id": "E1",
                            "subject_id": "C_program",
                            "chunk_id": "c",
                            "text": "改写原文",
                            "source": "wrong.txt",
                        }
                    ]
                },
                Path(folder),
            )
            self.assertEqual(count, 1)
            self.assertEqual(
                set(errors),
                {
                    "E1:source_text_mismatch",
                    "E1:source_file_mismatch",
                    "unknown_citation_id",
                },
            )

    def test_old_checkpoint_requires_restart_with_new_evidence_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            runs = WorkflowRuns(Path(folder) / "runs.db")
            run = runs.claim("u", "c", {"message": "Q", "mode": "deepsearch"})
            run.close()
            with runs.connect() as db:
                db.execute("UPDATE workflow_runs SET version=1,status='failed'")
            public = runs.public("u", "c")
            self.assertFalse(public["can_resume"])
            self.assertTrue(public["can_restart"])

    def test_quality_suite_covers_modes_courses_and_abstention(self):
        cases = json.loads(Path("tests/fixtures/qa_source_cases.json").read_text())[
            "cases"
        ]
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        self.assertEqual({c["mode"] for c in cases}, {"instant", "auto", "deepsearch"})
        self.assertEqual(
            {s for c in cases for s in c["subjects"]},
            {"C_program", "operating_systems", "cybersec_lab"},
        )
        self.assertTrue(any(len(c["subjects"]) > 1 for c in cases))
        self.assertTrue(any(c["id"] == "unknown_material" for c in cases))

    async def test_three_courses_keep_separate_stores_in_one_process(self):
        from agenticRAG.agentic_runtime import _ainit_rag
        import uuid

        with tempfile.TemporaryDirectory() as folder:
            instances = []
            try:
                for subject in ("C_program", "operating_systems", "cybersec_lab"):
                    path = Path(folder) / (uuid.uuid4().hex + "_" + subject)
                    path.mkdir()
                    (path / "kv_store_full_docs.json").write_text(
                        json.dumps({"same-id": {"content": subject}})
                    )
                    instance = await _ainit_rag(str(path))
                    instances.append((subject, instance, path))
                for subject, instance, path in instances:
                    self.assertEqual(
                        (await instance.full_docs.get_by_ids(["same-id"]))[0][
                            "content"
                        ],
                        subject,
                    )
                    self.assertEqual(
                        Path(instance.full_docs._file_name),
                        path.resolve() / "kv_store_full_docs.json",
                    )
            finally:
                for _, instance, _ in instances:
                    await instance.finalize_storages()
