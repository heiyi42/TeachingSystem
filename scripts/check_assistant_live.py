"""Check configuration, or use --run to test real STS extraction/recall with synthetic data."""

import argparse
import asyncio
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Calls the configured model/embedding providers using synthetic test data.",
    )
    args = parser.parse_args()
    required = [
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
        "EMBEDDING_MODEL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_BASE_URL",
    ]
    missing = [key for key in required if not os.getenv(key, "").strip()]
    if missing or not args.run:
        print(
            json.dumps(
                {"ready": not missing, "missing": missing, "live_test": "not_run"},
                ensure_ascii=False,
            )
        )
        return 2 if missing else 0
    from webapp_core.assistant_store import AssistantStore, enqueue
    from webapp_core.assistant_memory import (
        STSMemory,
        ModelProvider,
        retrieve,
        Embeddings,
    )

    async def check(store):
        with closing(store.connect()) as db, db:
            enqueue(
                db, "synthetic-user", "synthetic-1", "我原来的计划是每天复习50分钟。"
            )
            enqueue(
                db,
                "synthetic-user",
                "synthetic-2",
                "更正：这周每天只能复习25分钟，下周恢复每天50分钟。",
            )
        job = store.claim()
        memory = STSMemory()
        snapshot = await asyncio.wait_for(
            memory.build("synthetic-user", job["events"]), timeout=480
        )
        if not store.publish(job, snapshot):
            raise RuntimeError("Publication failed")
        # New retrieval call with no chat history simulates a new conversation.
        evidence = await asyncio.to_thread(
            retrieve,
            store.view("synthetic-user")["snapshot"],
            "这周每天复习多少分钟？",
            Embeddings(),
        )
        answer = await ModelProvider().generate(
            '仅依据下面的个人记忆，返回JSON {"daily_minutes":整数}。问题：这周每天复习多少分钟？记忆：'
            + json.dumps(evidence, ensure_ascii=False)
        )
        recall_ok = json.loads(answer).get("daily_minutes") == 25
        store.settings("synthetic-user", forget=True)
        deletion_ok = not store.view("synthetic-user")[
            "snapshot"
        ] and not store.publish(job, snapshot)
        return {
            "live_test": "passed" if recall_ok and deletion_ok else "failed",
            "recall_current_constraint": recall_ok,
            "deletion_blocks_old_job": deletion_ok,
            "claims": len(snapshot.get("claims", {})),
        }

    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="assistant-live-") as folder:
        try:
            with (
                open(os.devnull, "w") as sink,
                redirect_stdout(sink),
                redirect_stderr(sink),
            ):
                result = asyncio.run(
                    check(AssistantStore(Path(folder) / "test.sqlite3"))
                )
        except Exception as error:
            result = {"live_test": "failed", "error_type": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["live_test"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
