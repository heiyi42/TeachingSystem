from __future__ import annotations

import unittest

from webapp_core import graph_service as graph_service_module
from webapp_core.graph_service import Neo4jGraphService


class _RetryableNeo4jError(Exception):
    pass


class _FakeDriver:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.error = error
        self.rows = rows or []
        self.closed = False

    def session(self, **kwargs: object) -> "_FakeDriver":
        del kwargs
        return self

    def __enter__(self) -> "_FakeDriver":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def run(self, query: str, **params: object) -> list[dict[str, object]]:
        del query, params
        if self.error is not None:
            raise self.error
        return self.rows

    def close(self) -> None:
        self.closed = True


class _DriverSequenceService(Neo4jGraphService):
    def __init__(self, drivers: list[_FakeDriver]) -> None:
        super().__init__(uri="bolt://demo", password="pw")
        self.drivers = drivers

    def _driver_or_error(self) -> tuple[object | None, str | None]:
        self._driver = self.drivers.pop(0)
        return self._driver, None


class _FakeGraphService(Neo4jGraphService):
    @property
    def configured(self) -> bool:
        return True

    def _run_read(self, query: str, **params: object) -> list[dict[str, object]]:
        if "MATCH (e:Entity)" in query and "RETURN e.id AS id" in query:
            return [
                {
                    "id": "C_program:printf",
                    "label": "printf",
                    "subjectId": "C_program",
                    "type": "function",
                    "description": "格式化输出函数",
                    "score": 1.0,
                }
            ]
        if "MATCH (center:Entity)" in query:
            return [
                {
                    "nodes": [
                        {
                            "id": "C_program:printf",
                            "label": "printf",
                            "subjectId": "C_program",
                            "type": "function",
                            "hitType": "direct",
                            "score": 1.0,
                            "description": "格式化输出函数",
                            "sourceIds": ["chunk-1"],
                        }
                    ],
                    "edges": [
                        {
                            "id": "edge-1",
                            "source": "C_program:printf",
                            "target": "C_program:stdio",
                            "label": "BELONGS_TO",
                            "weight": 1.0,
                            "description": "属于 stdio",
                            "keywords": "library",
                        }
                    ],
                }
            ]
        if "MATCH (e:Entity)-[:MENTIONED_IN]->(c:Chunk)" in query:
            return [
                {
                    "id": "C_program:chunk-1",
                    "chunkId": "chunk-1",
                    "subjectId": "C_program",
                    "preview": "printf 示例",
                    "content": "printf 示例内容",
                    "tokens": 12,
                    "filePath": "demo.txt",
                    "rawChunkId": "chunk-1",
                }
            ]
        return []


class GraphServiceTests(unittest.TestCase):
    def test_local_subgraph_normalizes_nodes_edges_and_chunks(self) -> None:
        service = _FakeGraphService(uri="bolt://demo", password="pw")

        result = service.local_subgraph(
            subject_ids=["C_program"],
            center_entity_ids=["C_program:printf"],
            depth=1,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["nodes"][0]["id"], "C_program:printf")
        self.assertEqual(result["nodes"][0]["hitType"], "direct")
        self.assertEqual(result["edges"][0]["source"], "C_program:printf")
        self.assertEqual(result["chunks"][0]["chunkId"], "chunk-1")

    def test_unconfigured_service_reports_unavailable(self) -> None:
        service = Neo4jGraphService(uri="", password="")

        result = service.local_subgraph(query="printf")

        self.assertFalse(result["ok"])
        self.assertIn("neo4j", result["error"].lower())

    def test_read_retries_once_after_retryable_neo4j_connection_error(self) -> None:
        original_retryable_errors = graph_service_module._RETRYABLE_NEO4J_ERRORS
        graph_service_module._RETRYABLE_NEO4J_ERRORS = (_RetryableNeo4jError,)
        first_driver = _FakeDriver(error=_RetryableNeo4jError("connection expired"))
        service = _DriverSequenceService(
            [first_driver, _FakeDriver(rows=[{"value": 1}])]
        )

        try:
            rows = service._run_read("RETURN 1 AS value")
        finally:
            graph_service_module._RETRYABLE_NEO4J_ERRORS = original_retryable_errors

        self.assertEqual(rows, [{"value": 1}])
        self.assertTrue(first_driver.closed)

    def test_retryable_neo4j_error_is_sanitized_for_ui(self) -> None:
        original_retryable_errors = graph_service_module._RETRYABLE_NEO4J_ERRORS
        graph_service_module._RETRYABLE_NEO4J_ERRORS = (_RetryableNeo4jError,)
        raw_error = (
            "Failed to write data to connection IPv4Address('p-mt-demo.neo4j.io', "
            "7687)(ResolvedIPv4Address(('198.18.5.37', 7687)))"
        )
        service = _DriverSequenceService(
            [
                _FakeDriver(error=_RetryableNeo4jError(raw_error)),
                _FakeDriver(error=_RetryableNeo4jError(raw_error)),
            ]
        )

        try:
            result = service.search_entities(query="printf")
        finally:
            graph_service_module._RETRYABLE_NEO4J_ERRORS = original_retryable_errors

        self.assertFalse(result["ok"])
        self.assertIn("Neo4j 连接中断", result["error"])
        self.assertNotIn("p-mt-demo.neo4j.io", result["error"])

    def test_query_terms_drop_course_generic_words(self) -> None:
        terms = Neo4jGraphService._query_terms(
            "C language memory management, Dangling pointer errors"
        )

        self.assertIn("memory", terms)
        self.assertIn("dangling", terms)
        self.assertIn("pointer", terms)
        self.assertNotIn("c", terms)
        self.assertNotIn("language", terms)
        self.assertNotIn("errors", terms)


if __name__ == "__main__":
    unittest.main()
