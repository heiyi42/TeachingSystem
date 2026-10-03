from __future__ import annotations

import unittest

from webapp_core.chat_service import ChatService


class ChatRoutingTests(unittest.TestCase):
    def setUp(self):
        self.service = ChatService.__new__(ChatService)
        self.service.subject_catalog = {
            s: {"id": s, "label": label, "working_dir": f"/tmp/{s}"}
            for s, label in ChatService.SUBJECT_LABELS.items()
        }


    def test_normalize_requested_subjects_deduplicates_aliases(self):
        self.assertEqual(self.service.normalize_requested_subjects(
            ["c", "操作系统", "cybersec", "C_program", "unknown"]
        ), ["C_program", "operating_systems", "cybersec_lab"])

    def test_explicit_subject_needs_no_model_routing(self):
        route = self.service._subject_route_from_explicit_subjects(["C_program"])
        self.assertEqual(route["primary_subject"], "C_program")

    def test_direct_prompt_preserves_language_and_subject(self):
        prompt = self.service._build_direct_answer_prompt(
            user_question="Explain pointers", augmented_question="Explain pointers", mode="auto",
            thread_id="test", response_language="en", requested_subjects=["C_program"],
        )
        self.assertIn("Answer entirely in English.", prompt)
        self.assertIn("C语言", prompt)
