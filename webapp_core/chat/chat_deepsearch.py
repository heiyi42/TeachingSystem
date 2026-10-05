from __future__ import annotations

from typing import Any, Callable


class ChatDeepSearchMixin:
    async def _run_multi_subject_deep_stream(
        self,
        *,
        user_question: str,
        question: str,
        timeout_s: int,
        subject_ids: list[str],
        response_language: str = "zh",
        emit_text: Callable[[str], None] | None = None,
        workflow_stage_callback: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        normalized_subject_ids = (
            [
                subject_id
                for subject_id in subject_ids
                if subject_id in self.subject_catalog
            ]
            or list(self.subject_catalog.keys())
            or ["operating_systems"]
        )
        answer_style_instruction = self._requested_brief_style(
            user_question
        ) or self._deepsearch_learning_answer_style_instruction(response_language)
        deep_question = self._apply_response_language_to_question(
            question, response_language
        )
        result = await self._stream_routed_deepsearch_mode(
            question=deep_question,
            timeout_s=timeout_s,
            allowed_subject_ids=normalized_subject_ids,
            response_language=response_language,
            answer_style_instruction=answer_style_instruction,
            emit_text=emit_text,
            workflow_stage_callback=workflow_stage_callback,
        )
        result["subject_ids"] = normalized_subject_ids
        result["route"] = {
            "chain": "deepsearch-subquestion-routed",
            "subjects": normalized_subject_ids,
            "reason": "拆分子问题后，在用户所选课程知识库内并行检索",
        }
        return result

    async def _stream_mode_with_retrieval(
        self,
        *,
        mode: str,
        subject_route: dict[str, Any] | None,
        requested_subjects: list[str] | None = None,
        user_question: str,
        augmented_question: str,
        timeout_s: int,
        response_language: str = "zh",
        emit_text: Callable[[str], None] | None = None,
        workflow_stage_callback: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        if mode == "deepsearch":
            deep_subjects = (
                [
                    subject_id
                    for subject_id in (requested_subjects or [])
                    if subject_id in self.subject_catalog
                ]
                or list((subject_route or {}).get("requested_subjects", []))
                or ["C_program"]
            )
            return await self._run_multi_subject_deep_stream(
                user_question=user_question,
                question=augmented_question,
                timeout_s=timeout_s,
                subject_ids=deep_subjects,
                response_language=response_language,
                emit_text=emit_text,
                workflow_stage_callback=workflow_stage_callback,
            )

        raise ValueError(f"{mode} mode does not use retrieval")
