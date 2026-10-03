"""Preview or remove expired workflow recovery data without deleting learning records."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from webapp_core.workflow_runs import WorkflowRuns


def main():
    load_dotenv(ROOT / ".env")
    learning = Path(
        os.getenv("WEB_LEARNING_STORE_PATH", str(ROOT / "data/learning.sqlite3"))
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=learning.with_name(f"{learning.stem}_workflows.sqlite3"),
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true", help="实际删除；默认仅预览")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("工作流数据库不存在")
    runs = WorkflowRuns(args.database)
    print(
        json.dumps(
            runs.prune(dry_run=not args.apply, limit=args.limit),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
