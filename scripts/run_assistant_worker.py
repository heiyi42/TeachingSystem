"""Run independently of Flask: python scripts/run_assistant_worker.py [--once]."""

import argparse
import logging
from contextlib import redirect_stdout, redirect_stderr
import asyncio
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from webapp_core.assistant_store import AssistantStore


def main():
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--store",
        default=os.getenv("WEB_LEARNING_STORE_PATH", "./data/learning.sqlite3"),
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    store = AssistantStore(args.store)
    while True:
        job = store.claim()
        failed = False
        if job:
            try:
                from webapp_core.assistant_memory import STSMemory

                # Upstream benchmark extractors print prompts and responses.
                # The dedicated worker suppresses these private-content logs.
                with (
                    open(os.devnull, "w") as sink,
                    redirect_stdout(sink),
                    redirect_stderr(sink),
                ):
                    result = asyncio.run(
                        asyncio.wait_for(
                            STSMemory().build(job["owner"], job["events"]), timeout=480
                        )
                    )
                store.publish(job, result)
            except Exception as error:
                failed = True
                store.fail(job)
                # Do not log user content, prompts, or provider credentials.
                print(
                    f"Assistant memory build failed: {type(error).__name__}", flush=True
                )
        if args.once:
            return 1 if failed else 0
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
