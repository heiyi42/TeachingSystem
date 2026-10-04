from agenticRAG.agentic_config import OPENAI_MODEL
from langchain_openai import ChatOpenAI


auto_router_llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0)


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
