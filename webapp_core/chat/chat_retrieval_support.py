from __future__ import annotations

import asyncio
from contextlib import aclosing
from agenticRAG.answer_evidence import (
    answer_evidence,
    citation_result,
)
import time
from typing import Any, Awaitable, Callable

from langgraph.graph import END, START, StateGraph
from agenticRAG.workflow_checkpoint import invoke_workflow

from webapp_core.chat import auto_runtime as auto
from webapp_core.chat.chat_routing import CHAT_ANSWER_PRESENTATION

from agenticRAG.agentic_answer import run_question_plan_state
from agenticRAG.agentic_nodes import (
    build_final_answer_prompt,
)
from agenticRAG.agentic_runtime import get_rag, llm

from webapp_core import config as cfg


class ChatRetrievalSupportMixin:
    def _pick_problem_tutoring_subject(
        self,
        *,
        subject_route: dict[str, Any] | None,
        tutoring_candidate: dict[str, Any] | None,
    ) -> str:
        route = subject_route or {}
        requested = [
            subject_id
            for subject_id in route.get("requested_subjects", [])
            if subject_id in self.subject_catalog
        ]
        if requested:
            return requested[0]

        analysis = (tutoring_candidate or {}).get("analysis") or {}
        candidate_subject = str(analysis.get("subject_id", "") or "").strip()
        if candidate_subject in self.subject_catalog:
            return candidate_subject

        primary_subject = str(route.get("primary_subject", "") or "").strip()
        if primary_subject in self.subject_catalog:
            return primary_subject

        ranked = list(route.get("ranked", []) or [])
        for item in ranked:
            if isinstance(item, (list, tuple)) and item:
                subject_id = str(item[0])
            elif isinstance(item, dict):
                subject_id = str(item.get("subject", "") or "")
            else:
                subject_id = ""
            if subject_id in self.subject_catalog:
                return subject_id

        return "C_program"

    async def _run_problem_tutoring_stream(
        self,
        *,
        user_question: str,
        augmented_question: str,
        mode: str,
        timeout_s: int,
        subject_route: dict[str, Any] | None,
        response_language: str,
        tutoring_candidate: dict[str, Any] | None = None,
        emit_text: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        subject_id = self._pick_problem_tutoring_subject(
            subject_route=subject_route,
            tutoring_candidate=tutoring_candidate,
        )
        prep_timeout = max(
            3,
            min(
                int(timeout_s),
                int(cfg.WEB_PROBLEM_TUTORING_PREP_TIMEOUT_S),
            ),
        )
        prepared = await self.problem_tutoring_service.prepare(
            user_question=user_question,
            augmented_question=augmented_question,
            subject_id_hint=subject_id,
            working_dir=self._subject_working_dir(subject_id),
            mode=mode,
            response_language=response_language,
            timeout_s=prep_timeout,
        )
        elapsed_s = int(time.perf_counter() - started)
        final_timeout = max(1, int(timeout_s) - elapsed_s)
        answer = await self._stream_llm_text(
            llm_client=auto.auto_router_llm,
            prompt=str(prepared.get("prompt", "")),
            timeout_s=final_timeout,
            emit_text=emit_text,
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        solver_result = prepared.get("solver_result") or {}
        return {
            "mode_used": mode,
            "answer": answer,
            "request_kind": "problem_tutoring",
            "message_details": prepared.get("learning_outline"),
            "route": {
                "chain": "problem_tutoring",
                "reason": str((tutoring_candidate or {}).get("trigger") or "auto"),
                "subject": subject_id,
                "template_id": str((prepared.get("template") or {}).get("id", "")),
                "problem_type": str(
                    (prepared.get("analysis") or {}).get("problem_type", "")
                ),
                "solver_status": str((solver_result or {}).get("status", "")),
                "solver": str((solver_result or {}).get("solver", "")),
            },
            "subject_route": self._build_subject_route_meta(subject_route or {}),
            "elapsed_ms": str(total_ms),
        }

    async def _stream_llm_text(
        self,
        *,
        llm_client: Any,
        prompt: str,
        timeout_s: int | None,
        emit_text: Callable[[str], None] | None = None,
        before_first_emit: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        safe_timeout = max(1, int(timeout_s)) if timeout_s is not None else None
        chunks: list[str] = []
        first_emit_notified = False

        async with asyncio.timeout(safe_timeout):
            async with aclosing(llm_client.astream(prompt)) as stream:
                async for item in stream:
                    text = self.store.content_to_text(getattr(item, "content", item))
                    if not text:
                        continue
                    chunks.append(text)
                    if emit_text is not None:
                        if not first_emit_notified:
                            first_emit_notified = True
                            if before_first_emit is not None:
                                await before_first_emit()
                        emit_text(text)

        return "".join(chunks).strip()

    def _subject_working_dir(self, subject_id: str) -> str:
        return self.subject_catalog[subject_id]["working_dir"]

    @staticmethod
    def _build_subject_route_meta(subject_route: dict[str, Any]) -> dict[str, Any]:
        ranked = [
            {"subject": subject_id, "score": score}
            for subject_id, score in subject_route.get("ranked", [])
        ]
        return {
            "primary_subject": subject_route.get("primary_subject", ""),
            "cross_subject": bool(subject_route.get("cross_subject", False)),
            "confidence": float(subject_route.get("confidence", 0.0)),
            "reason": str(subject_route.get("reason", "") or ""),
            "requested_subjects": list(subject_route.get("requested_subjects", [])),
            "ranked": ranked,
        }

    async def prewarm_subject_rags(self) -> list[str]:
        subject_ids = list(self.subject_catalog.keys())
        tasks = [
            get_rag(self._subject_working_dir(subject_id)) for subject_id in subject_ids
        ]
        await asyncio.gather(*tasks)
        return subject_ids

    async def _run_deepsearch_plan_state(
        self,
        *,
        question: str,
        allowed_subject_ids: list[str] | None = None,
        response_language: str = "zh",
        workflow_stage_callback: (
            Callable[
                [str, dict[str, Any]],
                Awaitable[None],
            ]
            | None
        ) = None,
    ) -> dict[str, Any]:
        normalized_subject_ids = [
            subject_id
            for subject_id in (allowed_subject_ids or [])
            if subject_id in self.subject_catalog
        ]
        candidate_subject_ids = normalized_subject_ids or ["C_program"]
        subject_working_dirs = {
            subject_id: self._subject_working_dir(subject_id)
            for subject_id in candidate_subject_ids
        }

        return await run_question_plan_state(
            question,
            requested_mode="deepsearch",
            allowed_subject_ids=candidate_subject_ids,
            subject_working_dirs=subject_working_dirs,
            response_language=response_language,
            workflow_stage_callback=workflow_stage_callback,
        )

    async def _stream_routed_deepsearch_mode(
        self,
        *,
        question: str,
        timeout_s: int,
        allowed_subject_ids: list[str] | None = None,
        response_language: str = "zh",
        answer_style_instruction: str = "",
        emit_text: Callable[[str], None] | None = None,
        workflow_stage_callback: (
            Callable[
                [str, dict[str, Any]],
                Awaitable[None],
            ]
            | None
        ) = None,
    ) -> dict[str, Any]:
        safe_timeout = max(1, int(timeout_s))
        started = time.perf_counter()

        async def retrieve(state):
            return await self._run_deepsearch_plan_state(
                question=question,
                allowed_subject_ids=allowed_subject_ids,
                response_language=response_language,
                workflow_stage_callback=workflow_stage_callback,
            )

        async def generate(state):
            safe_style_instruction = str(answer_style_instruction or "").strip()
            if safe_style_instruction:
                state = {
                    **state,
                    "answer_style_instruction": safe_style_instruction,
                }
            planning_ms = int((time.perf_counter() - started) * 1000)
            answer_stage_closed = False
            if workflow_stage_callback is not None:
                await workflow_stage_callback("answer_generate_start", dict(state))
            prompt_started = time.perf_counter()
            final_prompt = build_final_answer_prompt(state)
            if not str(final_prompt or "").strip():
                raise ValueError("DeepSearch final answer prompt is empty")
            final_prompt += "\n\n" + CHAT_ANSWER_PRESENTATION
            final_prompt_ms = int((time.perf_counter() - prompt_started) * 1000)
            final_prompt_state = {
                **state,
                "final_answer_prompt": final_prompt,
                "final_answer_prompt_chars": len(final_prompt),
                "final_prompt_ms": final_prompt_ms,
            }

            async def close_answer_generate_stage() -> None:
                nonlocal answer_stage_closed
                if answer_stage_closed or workflow_stage_callback is None:
                    return
                answer_stage_closed = True
                await workflow_stage_callback("answer_generate_end", final_prompt_state)

            elapsed = time.perf_counter() - started
            remaining = max(1, safe_timeout - int(elapsed))
            answer_started = time.perf_counter()
            answer = await self._stream_llm_text(
                llm_client=llm,
                prompt=final_prompt,
                timeout_s=remaining,
                emit_text=emit_text,
                before_first_emit=close_answer_generate_stage,
            )
            answer, citations = citation_result(answer, answer_evidence(state))
            answer_ms = int((time.perf_counter() - answer_started) * 1000)
            await close_answer_generate_stage()

            total_ms = int((time.perf_counter() - started) * 1000)
            return {
                "mode_used": "deepsearch",
                "answer": answer,
                "citations": citations,
                "query_total_ms": str(total_ms),
                "raw": {
                    "query_total_ms": str(total_ms),
                    "sub_questions": state.get("sub_questions", []),
                    "subquery_tasks": state.get("subquery_tasks", []),
                    "subquery_results": state.get("subquery_results", []),
                    "retry_rewrites": state.get("retry_rewrites", []),
                    "query_attempt": state.get("query_attempt", 0),
                    "planning_ms": str(planning_ms),
                    "final_answer_prompt": final_prompt,
                    "final_answer_prompt_chars": str(len(final_prompt)),
                    "final_prompt_ms": str(final_prompt_ms),
                    "answer_ms": str(answer_ms),
                    "insufficient_subquestion_ids": state.get(
                        "insufficient_subquestion_ids",
                        [],
                    ),
                    "needs_retry": bool(state.get("needs_retry", False)),
                },
            }

        graph = StateGraph(dict)
        graph.add_node("retrieve_evidence", retrieve)
        graph.add_node("stream_answer", generate)
        graph.add_edge(START, "retrieve_evidence")
        graph.add_edge("retrieve_evidence", "stream_answer")
        graph.add_edge("stream_answer", END)
        async with asyncio.timeout(safe_timeout):
            return await invoke_workflow(graph, "deepsearch_answer", {})
