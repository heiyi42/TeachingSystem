from __future__ import annotations

from agenticRAG.agentic_runtime import llm


async def answer_instant_stream(question: str) -> dict:
    async def text_stream():
        async for chunk in llm.astream(
            [
                (
                    "system",
                    "你是教学问答助手。仅根据会话上下文和已有知识回答，不进行检索。"
                    "使用 Markdown，遵循用户指定的语言与格式。不编造课程材料或来源；无法确定时明确说明。",
                ),
                ("human", question),
            ]
        ):
            text = chunk.text
            if text:
                yield text

    return {
        "route_mode": "direct",
        "route_reason": "instant_direct_llm",
        "answer": "",
        "query_status": "success",
        "query_message": "",
        "is_streaming": True,
        "response_iterator": text_stream(),
    }
