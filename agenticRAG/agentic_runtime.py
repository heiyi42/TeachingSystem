import asyncio
from pathlib import Path
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import List

from langchain_openai import ChatOpenAI
from lightrag import LightRAG
from lightrag.llm.openai import openai_complete_if_cache, openai_embed
from lightrag.utils import TiktokenTokenizer, Tokenizer, wrap_embedding_func_with_attrs

from agenticRAG.agentic_config import (
    DEBUG,
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    OPENAI_MODEL,
    WORKING_DIR,
)
from agenticRAG.agentic_schema import (
    EvidenceCheck,
    SubQuestionRewrite,
    SubQuestionQueryPlan,
)

llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0)
llm_subquestion_plan_struct = llm.with_structured_output(SubQuestionQueryPlan)
llm_evidence_struct = llm.with_structured_output(EvidenceCheck)
llm_subquestion_rewrite_struct = llm.with_structured_output(SubQuestionRewrite)

rag_by_working_dir: dict[str, LightRAG] = {}
rag_init_locks: dict[str, asyncio.Lock] = {}
tiktoken_ready: bool | None = None
current_working_dir: ContextVar[str] = ContextVar(
    "current_rag_working_dir",
    default=os.path.abspath(WORKING_DIR),
)
EMBED_MODEL = EMBEDDING_MODEL
EMBED_DIM = EMBEDDING_DIMENSION
EMBED_MAX_TOKEN_SIZE = int(os.getenv("LIGHTRAG_EMBED_MAX_TOKEN_SIZE", "8192"))


class _CharTokenizer:
    """Network-free fallback tokenizer when tiktoken encoding download fails."""

    def encode(self, content: str) -> List[int]:
        return [ord(ch) for ch in content]

    def decode(self, tokens: List[int]) -> str:
        chars: List[str] = []
        for t in tokens:
            if isinstance(t, int) and 0 <= t <= 0x10FFFF:
                chars.append(chr(t))
        return "".join(chars)


def _check_tiktoken_ready() -> bool:
    global tiktoken_ready
    if tiktoken_ready is not None:
        return tiktoken_ready
    try:
        # 本地计数编码独立于 API 模型，兼容网关模型名可能不在 tiktoken 注册表中。
        tok = TiktokenTokenizer("gpt-4o-mini")
        # Warm up once to ensure encoding file is actually available locally.
        tok.encode("tokenizer_warmup")
        tiktoken_ready = True
    except Exception as e:
        tiktoken_ready = False
        if DEBUG:
            print(f"[DEBUG] tiktoken 初始化失败，切换到降级路径: {e}")
    return tiktoken_ready


def _build_tokenizer() -> Tokenizer:
    if _check_tiktoken_ready():
        return TiktokenTokenizer("gpt-4o-mini")
    return Tokenizer(model_name="char-fallback", tokenizer=_CharTokenizer())


@wrap_embedding_func_with_attrs(
    embedding_dim=EMBED_DIM,
    max_token_size=EMBED_MAX_TOKEN_SIZE,
    model_name=EMBED_MODEL,
)
async def configured_openai_embed(texts, **kwargs):
    kwargs["model"] = EMBED_MODEL
    kwargs["api_key"] = EMBEDDING_API_KEY
    kwargs["base_url"] = EMBEDDING_BASE_URL
    kwargs["embedding_dim"] = EMBED_DIM
    kwargs.setdefault("max_token_size", EMBED_MAX_TOKEN_SIZE)
    return await openai_embed.func(texts, **kwargs)


@wrap_embedding_func_with_attrs(
    embedding_dim=EMBED_DIM,
    max_token_size=0,
    model_name=EMBED_MODEL,
)
async def openai_embed_no_tiktoken(texts, **kwargs):
    """Disable tiktoken-based truncation to avoid runtime encoding download failures."""
    kwargs["model"] = EMBED_MODEL
    kwargs["api_key"] = EMBEDDING_API_KEY
    kwargs["base_url"] = EMBEDDING_BASE_URL
    kwargs["embedding_dim"] = EMBED_DIM
    kwargs["max_token_size"] = 0
    return await openai_embed.func(texts, **kwargs)


def _build_embedding_func():
    # Keep runtime embedding config aligned with the indexing scripts.
    if _check_tiktoken_ready():
        if DEBUG:
            print(
                f"[DEBUG] 使用 configured_openai_embed: model={EMBED_MODEL}, dim={EMBED_DIM}"
            )
        return configured_openai_embed
    if DEBUG:
        print(
            "[DEBUG] 使用 openai_embed_no_tiktoken "
            f"(model={EMBED_MODEL}, dim={EMBED_DIM}, 禁用 tiktoken 截断)"
        )
    return openai_embed_no_tiktoken


def _normalize_working_dir(working_dir: str | None = None) -> str:
    base = working_dir or current_working_dir.get()
    return os.path.abspath(str(base))


@contextmanager
def use_rag_working_dir(working_dir: str | None):
    token = current_working_dir.set(_normalize_working_dir(working_dir))
    try:
        yield current_working_dir.get()
    finally:
        current_working_dir.reset(token)


async def configured_openai_complete(
    prompt, system_prompt=None, history_messages=None, **kwargs
):
    kwargs.pop("model", None)
    kwargs.setdefault("api_key", os.getenv("OPENAI_API_KEY"))
    kwargs.setdefault("base_url", os.getenv("OPENAI_BASE_URL"))
    return await openai_complete_if_cache(
        OPENAI_MODEL,
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages if history_messages is not None else [],
        **kwargs,
    )


async def _ainit_rag(working_dir: str) -> LightRAG:
    directory = Path(working_dir).resolve()
    # LightRAG keys shared storage by workspace, not directory; keep existing files while isolating courses.
    r = LightRAG(
        working_dir=str(directory.parent),
        workspace=directory.name,
        embedding_func=_build_embedding_func(),
        embedding_batch_num=EMBEDDING_BATCH_SIZE,
        llm_model_func=configured_openai_complete,
        llm_model_name=OPENAI_MODEL,
        tokenizer=_build_tokenizer(),
    )
    await r.initialize_storages()
    return r


async def get_rag(working_dir: str | None = None) -> LightRAG:
    resolved_working_dir = _normalize_working_dir(working_dir)
    rag = rag_by_working_dir.get(resolved_working_dir)
    if rag is not None:
        return rag

    lock = rag_init_locks.get(resolved_working_dir)
    if lock is None:
        lock = asyncio.Lock()
        rag_init_locks[resolved_working_dir] = lock

    async with lock:
        rag = rag_by_working_dir.get(resolved_working_dir)
        if rag is None:
            rag = await _ainit_rag(resolved_working_dir)
            rag_by_working_dir[resolved_working_dir] = rag
    return rag
