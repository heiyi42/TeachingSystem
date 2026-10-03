from __future__ import annotations

import json
import unittest
import tempfile

from agenticRAG.agentic_config import EMBEDDING_MODEL
from webapp_core.problem_tutoring_service import ProblemTutoringService
from webapp_core.learning_courses import course_chapters
from scripts.audit_question_bank import check_answer
from collections import Counter
from pathlib import Path


class TutoringQuestionBankTests(unittest.TestCase):
    def test_question_bank_has_unique_questions_and_traceable_legacy_ids(self) -> None:
        path = Path("data/tutoring_question_bank/questions.jsonl")
        self.assertTrue(path.exists())

        questions = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(questions), 107)

        counts = Counter(item["subject_id"] for item in questions)
        self.assertEqual(counts["C_program"], 45)
        self.assertEqual(counts["operating_systems"], 29)
        self.assertEqual(counts["cybersec_lab"], 33)

        ids = [value for item in questions for value in [item["id"], *item["aliases"]]]
        self.assertEqual(len(ids), 300)
        self.assertEqual(len(set(ids)), 300)
        self.assertEqual(len({(item["subject_id"], item["question"].strip()) for item in questions}), 107)
        self.assertEqual(len({item["family_id"] for item in questions}), 32)
        for item in questions:
            self.assertIn(item["chapter_id"], {chapter["id"] for chapter in course_chapters(item["subject_id"])})
            self.assertEqual(item["review"]["teacher_status"], "pending")

        required_fields = {
            "id",
            "subject_id",
            "problem_type",
            "knowledge_points",
            "difficulty",
            "question",
            "answer",
            "solution_steps",
            "common_mistakes",
        }
        for item in questions:
            self.assertTrue(required_fields.issubset(item.keys()), item.get("id"))
            self.assertTrue(item["question"], item["id"])
            self.assertTrue(item["answer"], item["id"])
            self.assertTrue(item["solution_steps"], item["id"])

    def test_old_id_resolves_to_current_answer(self):
        service = ProblemTutoringService()
        example = service.build_few_shot_examples([{"id": "sec_022"}], limit=1)[0]
        self.assertEqual(example["id"], "sec_014")
        self.assertIn("可信绑定", example["answer"])
        self.assertEqual(example["family_id"], "sec_signature")

    def test_stale_or_deleted_embedding_is_not_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            row = {"id": "q1", "subject_id": "C_program", "problem_type": "c_output", "knowledge_points": [], "question": "new"}
            (path / "bank.jsonl").write_text(json.dumps(row))
            (path / "index.json").write_text(json.dumps({"model": EMBEDDING_MODEL, "items": [
                {"id": "q1", "text": "old", "embedding": [1, 0]},
                {"id": "deleted", "text": "old", "embedding": [1, 0]},
            ]}))
            service = ProblemTutoringService(question_bank_path=path / "bank.jsonl", question_bank_embedding_index_path=path / "index.json", question_bank_embed_enabled=True)
            self.assertEqual(service.load_question_bank_embedding_index(), {})

    def test_numeric_audit_detects_wrong_reference(self):
        row = next(item for item in ProblemTutoringService().load_question_bank() if item["id"] == "sec_001")
        self.assertEqual(check_answer(row)[1], [8, 19, 2])
        row["answer"] = row["answer"].replace("K=2", "K=3")
        with self.assertRaises(AssertionError):
            check_answer(row)


if __name__ == "__main__":
    unittest.main()
