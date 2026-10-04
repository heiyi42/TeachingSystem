import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np

from scripts import rebuild_embedding_indexes as rebuild


class RebuildEmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def test_staging_preserves_records_and_resumes_without_api_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "old.json", root / "staged.json"
            rows = [
                {
                    "__id__": "first",
                    "content": "题目",
                    "entity_name": "概念",
                    "vector": "old",
                }
            ]
            source.write_text(json.dumps({"embedding_dim": 3, "data": rows}))
            original = source.read_bytes()
            with (
                patch.object(rebuild, "EMBEDDING_DIMENSION", 2),
                patch.object(
                    rebuild.openai_embed, "func", new_callable=AsyncMock
                ) as embed,
            ):
                embed.return_value = np.array([[0.6, 0.8]], dtype=np.float32)
                await rebuild.stage_vector_index(source, target, root / "cache", 2)
                await rebuild.stage_vector_index(source, target, root / "cache", 2)
                self.assertEqual(embed.await_count, 1)
                self.assertEqual(embed.call_args.kwargs["embedding_dim"], 2)
            self.assertEqual(source.read_bytes(), original)
            output = json.loads(target.read_text())
            self.assertEqual(output["embedding_dim"], 2)
            self.assertEqual(output["data"][0]["__id__"], "first")
            self.assertEqual(output["data"][0]["entity_name"], "概念")
            artifact = dict(
                source=source,
                staged=target,
                source_hash=rebuild.file_hash(source),
                staged_hash=rebuild.file_hash(target),
            )
            rebuild.apply_indexes([artifact], root / "backup")
            self.assertEqual(source.read_bytes(), target.read_bytes())
            self.assertEqual((root / "backup/0-old.json").read_bytes(), original)

    async def test_wrong_response_dimensions_never_create_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "old.json", root / "staged.json"
            source.write_text(
                json.dumps({"data": [{"__id__": "first", "content": "题目"}]})
            )
            original = source.read_bytes()
            with (
                patch.object(rebuild, "EMBEDDING_DIMENSION", 2),
                patch.object(
                    rebuild.openai_embed, "func", new_callable=AsyncMock
                ) as embed,
            ):
                embed.return_value = np.array([[1, 2, 3]])
                with self.assertRaises(ValueError):
                    await rebuild.stage_vector_index(source, target, root / "cache", 2)
            self.assertFalse(target.exists())
            self.assertEqual(source.read_bytes(), original)

    def test_changed_source_prevents_all_replacements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = []
            for number in range(2):
                source, target = root / f"old-{number}", root / f"new-{number}"
                source.write_text("old")
                target.write_text("new")
                artifacts.append(
                    dict(
                        source=source,
                        staged=target,
                        source_hash=rebuild.file_hash(source),
                        staged_hash=rebuild.file_hash(target),
                    )
                )
            artifacts[1]["source"].write_text("changed")
            with self.assertRaises(RuntimeError):
                rebuild.apply_indexes(artifacts, root / "backup")
            self.assertEqual(artifacts[0]["source"].read_text(), "old")
            self.assertEqual(artifacts[1]["source"].read_text(), "changed")

    def test_failed_replacement_rolls_back_previous_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = []
            for number in range(2):
                source, target = root / f"old-{number}", root / f"new-{number}"
                source.write_text("old")
                target.write_text("new")
                artifacts.append(
                    dict(
                        source=source,
                        staged=target,
                        source_hash=rebuild.file_hash(source),
                        staged_hash=rebuild.file_hash(target),
                    )
                )
            real_replace = Path.replace

            def replace(path, target):
                if target == artifacts[1]["source"]:
                    raise OSError("simulated disk failure")
                return real_replace(path, target)

            with patch.object(Path, "replace", replace), self.assertRaises(OSError):
                rebuild.apply_indexes(artifacts, root / "backup")
            for item in artifacts:
                self.assertEqual(item["source"].read_text(), "old")
