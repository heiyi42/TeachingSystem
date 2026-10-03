from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from langgraph.graph import END, START, StateGraph
from agenticRAG.workflow_checkpoint import invoke_workflow
from agenticRAG import agentic_config as cfg

from agenticRAG.agentic_nodes import (
    build_global_subquestion_plan,
    build_subquery_tasks,
    judge_subquestion_results,
    prepare_subquestion_retry_plan,
    query_subquestion_tasks,
    rewrite_insufficient_subquestions,
)
from agenticRAG.agentic_runtime import use_rag_working_dir


async def run_question_plan_state(
    question: str,
    *,
    requested_mode: str = "deepsearch",
    working_dir: str | None = None,
    allowed_subject_ids: list[str] | None = None,
    subject_working_dirs: dict[str, str] | None = None,
    response_language: str = "zh",
    workflow_stage_callback: Callable[..., Awaitable[None]] | None = None,
) -> dict[str, Any]:
    if requested_mode != "deepsearch" or not allowed_subject_ids:
        raise ValueError("DeepSearch requires selected course IDs")

    state: dict[str, Any] = {
        "question": question,
        "requested_mode": requested_mode,
        "response_language": response_language,
    }
    if isinstance(allowed_subject_ids, list):
        state["allowed_subject_ids"] = list(allowed_subject_ids)
    if isinstance(subject_working_dirs, dict):
        state["subject_working_dirs"] = dict(subject_working_dirs)

    async def emit(stage, current):
        if workflow_stage_callback:
            await workflow_stage_callback(stage, dict(current))

    async def plan(current):
        await emit("deepsearch_plan_start", current)
        result = {
            **current,
            **await asyncio.to_thread(build_global_subquestion_plan, current),
        }
        for item in result.get("sub_questions", []):
            item["target_subjects"] = list(allowed_subject_ids)
        await emit("deepsearch_plan_end", result)
        return result

    async def tasks(current):
        return {**current, **await asyncio.to_thread(build_subquery_tasks, current)}

    async def retrieve(current):
        await emit("deepsearch_retrieve_start", current)
        result = {**current, **await query_subquestion_tasks(current)}
        await emit("deepsearch_retrieve_end", result)
        return result

    async def review(current):
        await emit("deepsearch_review_start", current)
        result = {**current, **await judge_subquestion_results(current)}
        await emit("deepsearch_review_end", result)
        if not result.get("needs_retry"):
            await emit("deepsearch_retry_skipped", result)
        return result

    async def rewrite(current):
        await emit("deepsearch_retry_start", current)
        result = {**current, **await rewrite_insufficient_subquestions(current)}
        result.update(await asyncio.to_thread(prepare_subquestion_retry_plan, result))
        await emit("deepsearch_retry_end", result)
        return result

    graph = StateGraph(dict)
    for name, node in [
        ("plan", plan),
        ("tasks", tasks),
        ("retrieve", retrieve),
        ("review", review),
        ("rewrite", rewrite),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "tasks")
    graph.add_edge("tasks", "retrieve")
    graph.add_edge("retrieve", "review")
    graph.add_conditional_edges(
        "review",
        lambda current: "rewrite" if current.get("needs_retry") else END,
        {"rewrite": "rewrite", END: END},
    )
    graph.add_edge("rewrite", "tasks")
    with use_rag_working_dir(working_dir):
        return await invoke_workflow(
            graph,
            "deepsearch_plan",
            state,
            recursion_limit=max(
                32,
                8 + 4 * max(cfg.MAX_RETRY, cfg.COMPLEX_MAX_RETRY, cfg.SIMPLE_MAX_RETRY),
            ),
        )
