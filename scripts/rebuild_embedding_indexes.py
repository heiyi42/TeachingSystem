from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import zlib

import numpy as np
from nano_vectordb import NanoVectorDB
from lightrag.llm.openai import openai_embed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agenticRAG.agentic_config import (  # noqa: E402
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
)
from webapp_core.chat.problem_tutoring_service import ProblemTutoringService  # noqa: E402


def file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


async def embed_texts(texts: list[str], cache: Path, concurrency: int) -> np.ndarray:
    cache.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    batch_locks: dict[str, asyncio.Lock] = {}
    completed = 0

    async def batch(start: int) -> np.ndarray:
        nonlocal completed
        selected = texts[start : start + EMBEDDING_BATCH_SIZE]
        key = hashlib.sha256(
            json.dumps(
                [EMBEDDING_MODEL, EMBEDDING_DIMENSION, selected], ensure_ascii=False
            ).encode()
        ).hexdigest()
        checkpoint = cache / f"{key}.npy"
        async with semaphore, batch_locks.setdefault(key, asyncio.Lock()):
            if checkpoint.exists():
                vectors = np.load(checkpoint, allow_pickle=False)
            else:
                vectors = np.asarray(
                    await asyncio.wait_for(
                        openai_embed.func(
                            selected,
                            model=EMBEDDING_MODEL,
                            api_key=EMBEDDING_API_KEY,
                            base_url=EMBEDDING_BASE_URL,
                            embedding_dim=EMBEDDING_DIMENSION,
                            max_token_size=0,
                        ),
                        timeout=120,
                    ),
                    dtype=np.float32,
                )
            if (
                vectors.shape != (len(selected), EMBEDDING_DIMENSION)
                or not np.isfinite(vectors).all()
                or np.any(np.linalg.norm(vectors, axis=1) == 0)
            ):
                raise ValueError(f"向量数量、维度或数值异常，批次起点：{start}")
            if not checkpoint.exists():
                temporary = checkpoint.with_suffix(".tmp")
                with temporary.open("wb") as handle:
                    np.save(handle, vectors, allow_pickle=False)
                temporary.replace(checkpoint)
        completed += len(selected)
        if completed % 1000 == 0 or completed == len(texts):
            print(f"  向量进度 {completed}/{len(texts)}", flush=True)
        return vectors

    if not texts:
        return np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)
    results = await asyncio.gather(
        *(batch(start) for start in range(0, len(texts), EMBEDDING_BATCH_SIZE))
    )
    return np.concatenate(results)


async def stage_vector_index(
    source: Path, target: Path, cache: Path, concurrency: int
) -> None:
    payload = json.loads(source.read_text())
    rows = payload["data"]
    ids = [row["__id__"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError(f"索引包含重复 ID：{source}")
    print(f"重建 {source.parent.name}/{source.name}，共 {len(rows)} 条", flush=True)
    vectors = await embed_texts([row["content"] for row in rows], cache, concurrency)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".building.json")
    temporary.unlink(missing_ok=True)
    db = NanoVectorDB(EMBEDDING_DIMENSION, storage_file=str(temporary))
    records = []
    for row, vector in zip(rows, vectors):
        record = {
            key: value
            for key, value in row.items()
            if key not in ("vector", "__vector__")
        }
        record["vector"] = base64.b64encode(
            zlib.compress(vector.astype(np.float16).tobytes())
        ).decode()
        record["__vector__"] = vector
        records.append(record)
    if records:
        db.upsert(records)
    db.save()
    saved = json.loads(temporary.read_text())
    expected = [{k: v for k, v in row.items() if k != "vector"} for row in rows]
    actual = [{k: v for k, v in row.items() if k != "vector"} for row in saved["data"]]
    if saved["embedding_dim"] != EMBEDDING_DIMENSION or actual != expected:
        raise ValueError(f"重建后索引元数据校验失败：{source}")
    temporary.replace(target)


def ensure_backend_stopped() -> None:
    host = os.getenv("WEB_HOST", "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = int(os.getenv("WEB_PORT", "7860"))
    try:
        connection = socket.create_connection((host, port), timeout=1)
    except OSError:
        return
    connection.close()
    raise RuntimeError(f"请先停止 {host}:{port} 上的后端，再用 --apply 替换索引")


def apply_indexes(artifacts: list[dict], backup: Path) -> None:
    # 全部校验完成后才替换，避免中途发现源数据变化而留下混合索引。
    for item in artifacts:
        if file_hash(item["source"]) != item["source_hash"]:
            raise RuntimeError(f"源索引已变化，停止替换：{item['source']}")
        if file_hash(item["staged"]) != item["staged_hash"]:
            raise RuntimeError(f"暂存索引已变化，停止替换：{item['staged']}")
    backup.mkdir(parents=True, exist_ok=False)
    replaced = []
    try:
        for number, item in enumerate(artifacts):
            source = item["source"]
            saved = backup / f"{number}-{source.name}"
            if source.exists():
                shutil.copy2(source, saved)
            temporary = source.with_suffix(".rebuilding.json")
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item["staged"], temporary)
            temporary.replace(source)
            replaced.append((source, saved))
    except BaseException:
        for source, saved in reversed(replaced):
            if saved.exists():
                shutil.copy2(saved, source)
            else:
                source.unlink(missing_ok=True)
        raise
    (backup / "manifest.json").write_text(json.dumps(artifacts, default=str, indent=2))


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="重建三学科及题库向量索引，支持断点续跑。"
    )
    parser.add_argument("--work-dir", type=Path, default=ROOT / "tmp/embedding_rebuild")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="完成全部校验后备份并替换正式索引；须先停止后端",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示模型、维度和索引数量，不调用 API 或写入文件",
    )
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency 必须为正整数")
    sources = [
        ROOT / "storage" / subject / f"vdb_{kind}.json"
        for subject in ("C_program", "operating_systems", "cybersec_lab")
        for kind in ("chunks", "entities", "relationships")
    ]
    for source in sources:
        if not source.exists():
            raise FileNotFoundError(source)
    print(
        f"模型：{EMBEDDING_MODEL}，维度：{EMBEDDING_DIMENSION}，批量：{EMBEDDING_BATCH_SIZE}"
    )
    if args.dry_run:
        for source in sources:
            print(source.relative_to(ROOT), len(json.loads(source.read_text())["data"]))
        print("题库条数：", len(ProblemTutoringService().load_question_bank()))
        return
    if args.apply:
        ensure_backend_stopped()
    artifacts = []
    cache = args.work_dir / "batches"
    for source in sources:
        target = args.work_dir / "staged" / source.relative_to(ROOT)
        original_hash = file_hash(source)
        await stage_vector_index(source, target, cache, args.concurrency)
        artifacts.append(
            dict(
                source=source,
                staged=target,
                source_hash=original_hash,
                staged_hash=file_hash(target),
            )
        )

    async def embed_bank(texts):
        return await embed_texts(texts, cache, args.concurrency)

    service = ProblemTutoringService(question_bank_embedder=embed_bank)
    bank_hash = file_hash(service.question_bank_path)
    source = service.question_bank_embedding_index_path.resolve()
    original_hash = file_hash(source)
    target = args.work_dir / "staged/questions.embedding_index.json"
    await service.build_question_bank_embedding_index(output_path=target)
    payload = json.loads(target.read_text())
    if len(payload["items"]) != len(service.load_question_bank()) or any(
        len(row["embedding"]) != EMBEDDING_DIMENSION for row in payload["items"]
    ):
        raise ValueError("题库索引数量或维度不匹配")
    if file_hash(service.question_bank_path) != bank_hash:
        raise RuntimeError("重建期间题库内容变化，请重新运行")
    artifacts.append(
        dict(
            source=source,
            staged=target,
            source_hash=original_hash,
            staged_hash=file_hash(target),
        )
    )
    if args.apply:
        ensure_backend_stopped()
        backup = (
            args.work_dir
            / "backups"
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        apply_indexes(artifacts, backup)
        print(f"全部索引已替换，原索引备份：{backup}。请重启后端。")
    else:
        print(
            f'全部索引已暂存到 {args.work_dir / "staged"}。停止后端后加 --apply 再次运行即可替换。'
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("已停止。重新运行同一命令会复用已完成的向量批次。")
        sys.exit(130)
