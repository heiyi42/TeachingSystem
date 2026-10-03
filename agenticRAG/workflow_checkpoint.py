from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


checkpoint_run: ContextVar[tuple[str, str] | None] = ContextVar(
    "checkpoint_run", default=None
)


def checkpoint_database_path(run_database):
    path = Path(run_database)
    return path.with_name(f"{path.stem}_checkpoints{path.suffix}")


async def invoke_workflow(builder, name, initial, *, recursion_limit=32):
    run = checkpoint_run.get()
    if run is None:
        return await builder.compile(name=name).ainvoke(
            initial, {"recursion_limit": recursion_limit}
        )
    path, run_id = run
    # Async checkpoint transactions must not hold the synchronous SSE store's write lock.
    async with AsyncSqliteSaver.from_conn_string(
        str(checkpoint_database_path(path))
    ) as saver:
        graph = builder.compile(name=name, checkpointer=saver)
        # Each named graph keeps its own progress when its parent node is retried.
        config = {
            "configurable": {"thread_id": f"{run_id}:{name}", "checkpoint_ns": ""},
            "recursion_limit": recursion_limit,
        }
        snapshot = await graph.aget_state(config)
        if snapshot.created_at and not snapshot.next:
            return snapshot.values
        return await graph.ainvoke(
            None if snapshot.created_at else initial, config, durability="sync"
        )
