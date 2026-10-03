from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agenticRAG import agentic_runtime as runtime
from webapp_core.problem_tutoring_service import ProblemTutoringService


ROOT = Path(__file__).resolve().parents[1]


class ModelConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_model_setting_reaches_chat_routing_and_summary_from_other_cwd(self):
        script = """
import json
from agenticRAG.agentic_config import OPENAI_MODEL, EMBEDDING_MODEL
from agenticRAG.agentic_runtime import llm, configured_openai_embed, openai_embed_no_tiktoken
from agenticRAG.cli_utils import build_memory_factory
from agenticRAG.short_memory import shutdown_shared_conversation_memories
from webapp_core.auto_runtime import auto_router_llm
from webapp_core.problem_tutoring_service import ProblemTutoringService
factory = build_memory_factory(use_summary_memory=True, summary_trigger_tokens=2000,
    max_turns_before_summary=4, keep_recent_turns=1)
memory = factory('model-config-test')
print(json.dumps([OPENAI_MODEL, llm.model_name, auto_router_llm.model_name,
    memory.summary_model.model_name, EMBEDDING_MODEL,
    configured_openai_embed.model_name, openai_embed_no_tiktoken.model_name,
    ProblemTutoringService().question_bank_embed_model]))
shutdown_shared_conversation_memories()
"""
        env = {
            **os.environ,
            "OPENAI_MODEL": "  configured-test-model  ",
            "EMBEDDING_MODEL": "  configured-embedding-model  ",
            "EMBEDDING_API_KEY": "embedding-test-key",
            "EMBEDDING_BASE_URL": "https://embedding.example.invalid/v1",
            "EMBEDDING_DIMENSION": "1536",
            "LIGHTRAG_EMBED_MODEL": "obsolete-override",
            "QUESTION_BANK_EMBED_MODEL": "obsolete-override",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://example.invalid/v1",
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        self.assertEqual(
            json.loads(result.stdout),
            ["configured-test-model"] * 4 + ["configured-embedding-model"] * 4,
        )

    def test_blank_model_is_rejected_without_silent_fallback(self):
        result = subprocess.run(
            [sys.executable, "-B", "-c", "import agenticRAG.agentic_config"],
            cwd=ROOT,
            env={**os.environ, "OPENAI_MODEL": "   "},
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("设置 OPENAI_MODEL", result.stderr)

    def test_blank_embedding_model_is_rejected(self):
        result = subprocess.run(
            [sys.executable, "-B", "-c", "import agenticRAG.agentic_config"],
            cwd=ROOT,
            env={
                **os.environ,
                "OPENAI_MODEL": "test-model",
                "EMBEDDING_MODEL": " ",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("设置 EMBEDDING_MODEL", result.stderr)

    async def test_embedding_calls_use_configured_model(self):
        with patch.object(
            runtime.openai_embed, "func", new_callable=AsyncMock
        ) as embed:
            embed.return_value = [[1.0, 0.0]]
            for callback in (
                runtime.configured_openai_embed,
                runtime.openai_embed_no_tiktoken,
            ):
                await callback.func(["问题"], model="unexpected-override")
                self.assertEqual(
                    embed.call_args.kwargs["model"], runtime.EMBEDDING_MODEL
                )
                self.assertEqual(embed.call_args.kwargs["embedding_dim"], 1536)
                self.assertEqual(
                    embed.call_args.kwargs["api_key"], runtime.EMBEDDING_API_KEY
                )
                self.assertEqual(
                    embed.call_args.kwargs["base_url"], runtime.EMBEDDING_BASE_URL
                )
            service = ProblemTutoringService()
            self.assertEqual(
                await service._embed_question_bank_texts(["问题"]), [[1.0, 0.0]]
            )
            self.assertEqual(embed.call_args.kwargs["model"], runtime.EMBEDDING_MODEL)
            self.assertEqual(embed.call_args.kwargs["embedding_dim"], 1536)
            self.assertEqual(
                embed.call_args.kwargs["api_key"], runtime.EMBEDDING_API_KEY
            )
            self.assertEqual(
                embed.call_args.kwargs["base_url"], runtime.EMBEDDING_BASE_URL
            )

    def test_question_bank_rejects_other_model_even_with_same_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            service = ProblemTutoringService(
                question_bank_embedding_index_path=path,
                question_bank_embed_enabled=True,
            )
            row = service.load_question_bank()[0]
            payload = {
                "model": runtime.EMBEDDING_MODEL,
                "items": [{"id": row["id"], "embedding": [1.0, 0.0]}],
            }
            path.write_text(json.dumps(payload))
            self.assertIn(row["id"], service.load_question_bank_embedding_index())
            for other_model in ("old-embedding-model", None):
                payload["model"] = other_model
                path.write_text(json.dumps(payload))
                fresh_service = ProblemTutoringService(
                    question_bank_embedding_index_path=path,
                    question_bank_embed_enabled=True,
                )
                self.assertEqual(fresh_service.load_question_bank_embedding_index(), {})

    async def test_rag_completion_preserves_extraction_context_and_model(self):
        history = [{"role": "user", "content": "上一条问题"}]
        with (
            patch.object(runtime, "OPENAI_MODEL", "configured-test-model"),
            patch.object(
                runtime, "openai_complete_if_cache", new_callable=AsyncMock
            ) as complete,
        ):
            complete.return_value = "结果"
            result = await runtime.configured_openai_complete(
                "当前问题",
                system_prompt="规则",
                history_messages=history,
                model="unexpected-override",
                keyword_extraction=True,
                entity_extraction=True,
                hashing_kv="cache",
                timeout=12,
            )
        self.assertEqual(result, "结果")
        self.assertEqual(complete.call_args.args, ("configured-test-model", "当前问题"))
        kwargs = complete.call_args.kwargs
        self.assertEqual(kwargs["history_messages"], history)
        self.assertEqual(kwargs["system_prompt"], "规则")
        self.assertTrue(kwargs["keyword_extraction"])
        self.assertTrue(kwargs["entity_extraction"])
        self.assertEqual(kwargs["hashing_kv"], "cache")
        self.assertEqual(kwargs["timeout"], 12)
        self.assertNotIn("model", kwargs)

    async def test_rag_stream_is_returned_without_consuming_it(self):
        async def chunks():
            yield "第一段"
            yield "第二段"

        stream = chunks()
        with patch.object(
            runtime, "openai_complete_if_cache", new_callable=AsyncMock
        ) as complete:
            complete.return_value = stream
            result = await runtime.configured_openai_complete("问题", stream=True)
        self.assertIs(result, stream)
        self.assertEqual([part async for part in result], ["第一段", "第二段"])
        self.assertTrue(complete.call_args.kwargs["stream"])
        self.assertEqual(complete.call_args.kwargs["history_messages"], [])

    async def test_rag_registers_configured_callback_and_model_name(self):
        with (
            patch.object(runtime, "LightRAG") as rag,
            patch.object(runtime, "_build_embedding_func"),
            patch.object(runtime, "_build_tokenizer"),
        ):
            rag.return_value.initialize_storages = AsyncMock()
            await runtime._ainit_rag("unused-test-storage")
        self.assertEqual(rag.call_args.kwargs["llm_model_name"], runtime.OPENAI_MODEL)
        self.assertIs(
            rag.call_args.kwargs["llm_model_func"], runtime.configured_openai_complete
        )
