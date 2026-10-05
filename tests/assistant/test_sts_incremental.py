import copy
import importlib.util
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from webapp_core.assistant.assistant_store import AssistantStore, enqueue
from webapp_core.assistant.assistant_memory import STSMemory, retrieve


class IncrementalStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "learning.sqlite3"
        self.store = AssistantStore(self.path)

    def add(self, key, owner="alice"):
        with closing(self.store.connect()) as db, db:
            enqueue(db, owner, key, key)

    def test_filtered_events_are_consumed_across_restart(self):
        self.add("greeting")
        job = self.store.claim()
        self.store.publish(job, {})
        self.add("fact")
        restarted = AssistantStore(self.path)
        next_job = restarted.claim()
        self.assertEqual([e["id"] for e in next_job["events"]], ["fact"])
        self.assertEqual(next_job["snapshot"]["processed_through"], job["through"])

    def test_failure_does_not_advance_cursor_or_change_published_memory(self):
        self.add("one")
        first = self.store.claim()
        self.store.publish(first, {"marker": "original"})
        original = self.store.view("alice")["snapshot"]
        self.add("two")
        job = self.store.claim()
        self.store.fail(job)
        self.assertEqual(self.store.view("alice")["snapshot"], original)
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE assistant_jobs SET next_at=0")
        retry = self.store.claim()
        self.assertEqual([e["id"] for e in retry["events"]], ["two"])
        self.assertEqual(retry["snapshot"], original)
        self.assertFalse(self.store.publish(job, {"marker": "stale"}))

    def test_cursor_does_not_skip_other_account_or_deleted_sources(self):
        self.add("alice-one")
        self.add("bob-one", "bob")
        first = self.store.claim()
        self.assertEqual(first["owner"], "alice")
        self.store.publish(first, {})
        bob = self.store.claim()
        self.assertEqual([e["id"] for e in bob["events"]], ["bob-one"])
        self.store.publish(bob, {})
        self.add("alice-two")
        second = self.store.claim()
        self.store.publish(second, {})
        self.store.settings("alice", event_ids=["alice-one"])
        remaining = self.store.claim()
        self.assertEqual([e["id"] for e in remaining["events"]], ["alice-two"])
        self.assertEqual(remaining["snapshot"], {})


@unittest.skipUnless(importlib.util.find_spec("sts"), "Install .[assistant]")
class IncrementalGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from sts.graph.types import Scope, Claim
        from sts.graph.schema import ScopeEventRelation, EventClaimRelation
        from sts.extraction.scope_extractor import (
            ScopeExtractResult,
            ScopeEventRelationExtractResult,
        )
        from sts.extraction.claim_extractor import (
            ClaimExtractResult,
            EventClaimRelationExtractResult,
        )

        self.scope_calls = []
        self.claim_calls = []
        self.generation = 0

        async def scope_extract(_self, request):
            self.scope_calls.append(request.new_event.event_id)
            matched = request.new_event.summary.split("|", 1)[0]
            existing = {s.scope_id: s for s in request.existing_scopes}
            scopes, links = [], []
            for key in matched:
                ids = list(existing[key].event_ids) if key in existing else []
                ids.append(request.new_event.event_id)
                scopes.append(
                    Scope(
                        scope_id=key,
                        title=key,
                        summary=key,
                        event_ids=ids,
                        timestamp=request.new_event.timestamp,
                        user_id_list=["alice"],
                    )
                )
                links.append(
                    ScopeEventRelationExtractResult(
                        key,
                        ScopeEventRelation(
                            id="scope-link-" + key,
                            scope_node_id=key,
                            relation={eid: "developing" for eid in ids},
                            weights={eid: 1 for eid in ids},
                        ),
                    )
                )
            return ScopeExtractResult(scopes, "update_existing"), links

        async def claim_extract(_self, scope, events):
            self.claim_calls.append(scope.scope_id)
            self.generation += 1
            claims = [
                Claim(
                    claim_id=f"claim-{scope.scope_id}-{e.event_id}-{self.generation}",
                    content=e.summary,
                    event_ids=[e.event_id],
                    scope_id=scope.scope_id,
                    timestamp=e.timestamp,
                    entities=["alice"],
                )
                for e in events
            ]
            links = [
                EventClaimRelationExtractResult(
                    scope.scope_id,
                    e.event_id,
                    EventClaimRelation(
                        id=f"event-link-{e.event_id}-{self.generation}",
                        event_node_id=e.event_id,
                        relation={c.claim_id: "core"},
                        weights={c.claim_id: 1},
                    ),
                )
                for e, c in zip(events, claims)
            ]
            return ClaimExtractResult(scope.scope_id, claims), links

        for name, fn in [
            ("scope_extractor.ScopeExtractor.extract_scope", scope_extract),
            ("claim_extractor.ClaimExtractor.extract_claims", claim_extract),
        ]:
            patcher = patch("sts.extraction." + name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.embeddings = SimpleNamespace(
            model_name="test",
            embed=Mock(side_effect=lambda texts: [[1.0, 0.0] for _ in texts]),
        )
        self.provider = SimpleNamespace(
            prepare_events=AsyncMock(side_effect=lambda rows: rows)
        )
        self.memory = STSMemory(self.provider, self.embeddings)

    def record(self, key, content):
        return {"id": key, "content": content, "created": 1700000000}

    async def test_only_new_events_and_affected_scopes_are_processed(self):
        original = await self.memory.build(
            "alice",
            [self.record("one", "A|学习时间"), self.record("two", "B|讲解偏好")],
        )
        saved = copy.deepcopy(original)
        self.scope_calls.clear()
        self.claim_calls.clear()
        self.embeddings.embed.reset_mock()
        updated = await self.memory.build(
            "alice", [self.record("three", "A|调整时间")], original
        )
        self.assertEqual(original, saved)
        self.assertEqual(self.scope_calls, ["three"])
        self.assertEqual(self.claim_calls, ["A"])
        self.assertEqual(updated["scopes"]["B"], original["scopes"]["B"])
        old_b = {k: c for k, c in original["claims"].items() if c["scope_id"] == "B"}
        self.assertEqual({k: updated["claims"][k] for k in old_b}, old_b)
        self.assertEqual(
            next(s for s in updated["state_results"] if s["scope_id"] == "B"),
            next(s for s in original["state_results"] if s["scope_id"] == "B"),
        )
        for key in old_b:
            self.assertEqual(updated["vectors"][key], original["vectors"][key])
        self.assertEqual(updated["scope_vectors"]["B"], original["scope_vectors"]["B"])
        self.assertFalse(
            any(
                "B|讲解偏好" in text
                for call in self.embeddings.embed.call_args_list
                for text in call.args[0]
            )
        )
        self.assertTrue(
            set(original["claims"]) - set(old_b)
            <= set(original["claims"]) - set(updated["claims"])
        )
        from sts.graph.schema import STSGraph

        self.assertFalse(STSGraph.from_dict(updated).validate_bidirectional_links())

    async def test_shared_event_relations_survive_one_scope_update(self):
        original = await self.memory.build(
            "alice", [self.record("shared", "AB|时间和偏好")]
        )
        updated = await self.memory.build(
            "alice", [self.record("new", "A|新时间")], original
        )
        old_b = next(c for c in original["claims"].values() if c["scope_id"] == "B")
        self.assertEqual(updated["claims"][old_b["id"]], old_b)
        relation_id = updated["events"]["shared"]["event_claim_relation_id"]
        linked = updated["event_claim_relations"][relation_id]["relation"]
        self.assertEqual(len(linked), 2)
        self.assertIn(old_b["id"], linked)
        self.claim_calls.clear()
        both = await self.memory.build(
            "alice", [self.record("both", "AB|共同变化")], updated
        )
        self.assertEqual(set(self.claim_calls), {"A", "B"})
        self.assertTrue(all("both" in s["event_ids"] for s in both["scopes"].values()))
        from sts.graph.schema import STSGraph

        self.assertFalse(STSGraph.from_dict(both).validate_bidirectional_links())

    async def test_duplicate_legacy_events_and_filtered_batch_leave_graph_unchanged(
        self,
    ):
        event = self.record("one", "A|学习时间")
        original = await self.memory.build("alice", [event])
        self.provider.prepare_events.reset_mock()
        self.scope_calls.clear()
        duplicate = await self.memory.build("alice", [event], original)
        self.assertEqual(duplicate, original)
        self.provider.prepare_events.assert_not_awaited()
        self.provider.prepare_events.side_effect = lambda records: []
        ignored = await self.memory.build(
            "alice", [self.record("hi", "你好")], original
        )
        self.assertEqual(ignored, original)
        self.assertEqual(self.scope_calls, [])

    async def test_failure_does_not_mutate_input_snapshot(self):
        original = await self.memory.build("alice", [self.record("one", "A|学习时间")])
        saved = copy.deepcopy(original)
        with patch(
            "sts.extraction.claim_extractor.ClaimExtractor.extract_claims",
            AsyncMock(side_effect=RuntimeError("failed")),
        ):
            with self.assertRaises(RuntimeError):
                await self.memory.build(
                    "alice", [self.record("two", "A|新时间")], original
                )
        self.assertEqual(original, saved)

    async def test_prepared_summary_and_context_provenance_survive_graph_roundtrip(
        self,
    ):
        record = self.record("change", "改成周五")
        context = [
            {
                "id": "before",
                "role": "user",
                "content": "网络安全考试在周四",
                "created": 1699999000,
            }
        ]
        self.provider.prepare_events.side_effect = lambda rows: [
            {**rows[0], "summary": "A|网络安全考试由周四改成周五", "context": context}
        ]
        snapshot = await self.memory.build("alice", [record])
        from sts.graph.schema import STSGraph

        event = STSGraph.from_dict(snapshot).events["change"].to_event()
        self.assertEqual(event.summary, "A|网络安全考试由周四改成周五")
        self.assertEqual(event.original_data[0]["content"], "改成周五")
        self.assertEqual(event.original_data[0]["id"], "change")
        self.assertEqual(event.timestamp.timestamp(), record["created"])
        self.assertEqual(event.metadata["context_sources"], context)
        self.assertEqual(event.metadata["source_role"], "user")
        self.assertEqual(len(event.original_data), 1)
        snapshot["scopes"]["A"]["title"] = "网络安全考试"
        evidence = retrieve(snapshot, "网络安全考试")
        self.assertEqual(evidence[0]["source_events"][0]["source_role"], "user")

    async def test_legacy_scope_index_is_backfilled_without_reextracting_events(self):
        original = await self.memory.build("alice", [self.record("one", "A|学习时间")])
        del original["scope_vectors"]
        self.scope_calls.clear()
        self.claim_calls.clear()
        indexed = await self.memory.build("alice", [], original)
        self.assertEqual(self.scope_calls, [])
        self.assertEqual(self.claim_calls, [])
        self.assertEqual(indexed["claims"], original["claims"])
        self.assertEqual(indexed["state_results"], original["state_results"])
        self.assertEqual(set(indexed["scope_vectors"]), {"A"})


@unittest.skipUnless(importlib.util.find_spec("rank_bm25"), "Install .[assistant]")
class RetrievalTests(unittest.TestCase):

    def test_topic_coverage_keeps_high_ranked_correction(self):
        snapshot = {
            "scopes": {key: {"title": key} for key in ("os", "c", "food")},
            "claims": {
                key: {"id": key, "scope_id": scope, "content": content, "event_ids": []}
                for key, scope, content in [
                    ("old", "os", "原来先看例子"),
                    ("time", "os", "每天40分钟"),
                    ("new", "os", "现在改为先独立做题"),
                    ("c", "c", "先阅读C语言例子"),
                    ("food", "food", "喜欢火锅"),
                ]
            },
        }
        with patch(
            "webapp_core.assistant.assistant_memory.rank_evidence",
            side_effect=[["os", "c", "food"], ["old", "time", "new", "c", "food"]],
        ):
            result = retrieve(snapshot, "现在的学习偏好和时间安排", limit=6)
        ids = [row["claim"]["id"] for row in result]
        self.assertIn("new", ids)
        self.assertEqual({row["scope"]["id"] for row in result}, {"os", "c", "food"})
        self.assertLessEqual(len(ids), 6)

    def test_cross_course_question_keeps_less_redundant_topic(self):
        snapshot = {
            "scopes": {
                "c": {"title": "C语言的学习时间和偏好"},
                "os": {"title": "操作系统的学习时间和偏好"},
            },
            "claims": {
                **{
                    f"c-{i}": {
                        "id": f"c-{i}",
                        "scope_id": "c",
                        "content": "C语言的学习时间和偏好保持不变，每天30分钟，先阅读例子。",
                        "event_ids": [],
                    }
                    for i in range(8)
                },
                "os-new": {
                    "id": "os-new",
                    "scope_id": "os",
                    "content": "现在改为先独立做题，卡住再看讲解。",
                    "event_ids": ["change"],
                },
            },
            "events": {"change": {"summary": "操作系统改为先独立做题。"}},
        }
        for limit in (2, 4, 6):
            with self.subTest(limit=limit):
                result = retrieve(
                    snapshot,
                    "C语言和操作系统现在各有什么学习偏好和时间安排？",
                    limit=limit,
                )
                self.assertEqual({row["scope"]["id"] for row in result}, {"c", "os"})
                self.assertIn(
                    "change",
                    [event["id"] for row in result for event in row["source_events"]],
                )
                self.assertLessEqual(
                    sum(1 + len(row["related_claims"]) for row in result), limit
                )

    def test_scope_vectors_exclude_a_high_scoring_claim_from_another_scope(self):
        snapshot = {
            "scopes": {"exam": {"title": "考试日期"}, "travel": {"title": "旅行行程"}},
            "claims": {
                "exam": {
                    "id": "exam",
                    "scope_id": "exam",
                    "content": "定于周五。",
                    "event_ids": [],
                },
                "travel": {
                    "id": "travel",
                    "scope_id": "travel",
                    "content": "定于周六。",
                    "event_ids": [],
                },
            },
            "embedding_model": "test",
            "vectors": {"exam": [1.0, 0.0], "travel": [1.0, 0.0]},
            "scope_vectors": {"exam": [1.0, 0.0], "travel": [0.0, 1.0]},
        }
        embeddings = SimpleNamespace(
            model_name="test", embed=Mock(return_value=[[1.0, 0.0]])
        )
        self.assertEqual(
            [r["claim"]["id"] for r in retrieve(snapshot, "exam schedule", embeddings)],
            ["exam"],
        )
        embeddings.embed.assert_called_once_with(["exam schedule"])
        del snapshot["scope_vectors"]
        saved = copy.deepcopy(snapshot)
        embeddings.embed = Mock(return_value=[[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(
            [r["claim"]["id"] for r in retrieve(snapshot, "exam schedule", embeddings)],
            ["exam"],
        )
        self.assertEqual(snapshot, saved)
        self.assertEqual(len(embeddings.embed.call_args.args[0]), 3)

    def test_expiring_constraint_future_recovery_and_cancellation_are_retained(self):
        snapshot = {
            "scopes": {"study": {"title": "操作系统复习", "summary": "复习时间变更"}},
            "claims": {
                "old": {
                    "id": "old",
                    "scope_id": "study",
                    "content": "操作系统每天复习50分钟。",
                    "event_ids": ["old"],
                },
                "temporary": {
                    "id": "temporary",
                    "scope_id": "study",
                    "content": "临时改为25分钟。",
                    "temporal": "2026-10-04截止",
                    "event_ids": ["temporary"],
                },
                "future": {
                    "id": "future",
                    "scope_id": "study",
                    "content": "恢复为50分钟。",
                    "temporal": "2026-10-05起",
                    "event_ids": ["future"],
                },
            },
            "state_results": [
                {
                    "states": [
                        {
                            "state_id": "time",
                            "scope_id": "study",
                            "claim_ids": ["old", "temporary", "future"],
                            "time_groups": [
                                {"claim_ids": ["old"], "rank": 0},
                                {"claim_ids": ["temporary"], "rank": 1},
                                {"claim_ids": ["future"], "rank": 2},
                            ],
                        }
                    ]
                }
            ],
        }
        result = retrieve(snapshot, "操作系统现在怎样复习？", limit=3)
        ids = [r["claim"]["id"] for r in result] + [
            c["id"] for r in result for c in r["related_claims"]
        ]
        self.assertEqual(set(ids), {"old", "temporary", "future"})
        self.assertEqual(result[0]["states"][0]["omitted_claim_count"], 0)
        limited = retrieve(snapshot, "操作系统现在怎样复习？", limit=1)
        self.assertEqual(limited[0]["states"][0]["omitted_claim_count"], 2)
        snapshot["claims"]["future"]["content"] = "撤回此前安排，不再执行。"
        result = retrieve(snapshot, "操作系统现在怎样复习？", limit=3)
        self.assertIn("撤回", json.dumps(result, ensure_ascii=False))

    def test_scope_match_can_retrieve_claim_without_repeating_topic(self):
        snapshot = {
            "scopes": {
                "study": {"title": "操作系统复习安排", "summary": "页面置换复习时间"},
                "travel": {"title": "旅行安排", "summary": "登山行程"},
            },
            "claims": {
                "study-fact": {
                    "id": "study-fact",
                    "scope_id": "study",
                    "content": "每天安排25分钟。",
                    "event_ids": [],
                },
                "travel-fact": {
                    "id": "travel-fact",
                    "scope_id": "travel",
                    "content": "每天安排50分钟。",
                    "event_ids": [],
                },
            },
        }
        result = retrieve(snapshot, "操作系统复习有什么要求？")
        self.assertEqual([row["claim"]["id"] for row in result], ["study-fact"])

    def test_correction_in_another_time_group_is_not_lost(self):
        snapshot = {
            "scopes": {"exam": {"title": "操作系统考试", "summary": "考试日期的更正"}},
            "claims": {
                "old": {
                    "id": "old",
                    "scope_id": "exam",
                    "content": "操作系统考试日期为10月18日。",
                    "event_ids": ["old"],
                },
                "correction": {
                    "id": "correction",
                    "scope_id": "exam",
                    "content": "更正为10月20日，原日期作废。",
                    "event_ids": ["correction"],
                },
            },
            "state_results": [
                {
                    "states": [
                        {
                            "state_id": "exam-dates",
                            "scope_id": "exam",
                            "claim_ids": ["old", "correction"],
                            "time_groups": [
                                {
                                    "claim_ids": ["old"],
                                    "start_time": "2026-10-01T00:00:00+08:00",
                                    "rank": 0,
                                },
                                {
                                    "claim_ids": ["correction"],
                                    "start_time": "2026-10-03T00:00:00+08:00",
                                    "rank": 1,
                                },
                            ],
                        }
                    ]
                }
            ],
        }
        result = retrieve(snapshot, "操作系统考试现在是哪天？", limit=2)
        selected = [row["claim"]["id"] for row in result] + [
            c["id"] for row in result for c in row["related_claims"]
        ]
        self.assertEqual(set(selected), {"old", "correction"})

    def snapshot(self):
        claims = {
            str(i): {
                "id": str(i),
                "content": text,
                "scope_id": "scope",
                "event_ids": [str(i)],
            }
            for i, text in enumerate(
                [
                    "偏好画图讲解",
                    "喜欢先看示意图",
                    "本周复习时间二十分钟",
                    "下周复习时间四十分钟",
                    "无关的登山经历",
                ]
            )
        }
        return {
            "claims": claims,
            "scopes": {
                "scope": {
                    "title": "个人学习安排",
                    "summary": "画图讲解、学习偏好、本周下周复习时间",
                }
            },
            "events": {
                k: {"summary": c["content"], "timestamp": "2026-10-04"}
                for k, c in claims.items()
            },
            "state_results": [
                {
                    "states": [
                        {
                            "state_id": "state",
                            "scope_id": "scope",
                            "claim_ids": list(claims),
                            "summary": "不能带入的全量摘要",
                            "time_groups": [
                                {"claim_ids": ["0", "1"]},
                                {"claim_ids": ["2", "3"]},
                                {"claim_ids": ["4"]},
                            ],
                        }
                    ]
                }
            ],
        }

    def test_unrelated_query_returns_nothing_even_with_vectors(self):
        snapshot = self.snapshot()
        for claim in snapshot["claims"].values():
            claim["query_patterns"] = [
                "用户喜欢什么讲解方式？",
                "用户使用什么复习安排？",
            ]
        self.assertEqual(retrieve(snapshot, "火星探测器使用什么燃料？"), [])
        self.assertEqual(retrieve(snapshot, "火星探测器燃料"), [])
        snapshot.update(
            embedding_model="test",
            vectors={k: [1.0, 0.0] for k in snapshot["claims"]},
            scope_vectors={"scope": [1.0, 0.0]},
        )
        embeddings = SimpleNamespace(
            model_name="test", embed=lambda texts: [[0.0, 1.0]]
        )
        self.assertEqual(retrieve(snapshot, "火星探测器燃料", embeddings), [])
        embeddings.embed = Mock(side_effect=TimeoutError)
        self.assertEqual(retrieve(snapshot, "火星探测器燃料", embeddings), [])
        self.assertTrue(retrieve(snapshot, "画图讲解", embeddings))
        embeddings.embed = Mock(return_value=[[1.0, 0.0]])
        snapshot["scope_vectors"]["scope"] = ["invalid"]
        snapshot["vectors"]["0"] = ["invalid"]
        self.assertTrue(retrieve(snapshot, "画图讲解", embeddings))

    def test_regular_question_does_not_expand_entire_state(self):
        result = retrieve(self.snapshot(), "画图讲解")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["related_claims"], [])
        self.assertEqual(result[0]["states"][0]["claim_ids"], ["0"])
        self.assertNotIn("summary", result[0]["states"][0])
        self.assertEqual(result[0]["source_events"][0]["id"], "0")

    def test_temporal_field_and_semantic_only_match_remain_searchable(self):
        snapshot = self.snapshot()
        snapshot["claims"]["0"]["temporal"] = "暑假期间"
        snapshot["scopes"]["scope"]["summary"] += "、暑假安排"
        self.assertEqual(retrieve(snapshot, "暑假")[0]["claim"]["id"], "0")
        snapshot.update(
            embedding_model="test",
            scope_vectors={"scope": [1.0, 0.0]},
            vectors={
                key: [1.0, 0.0] if key == "0" else [0.0, 1.0]
                for key in snapshot["claims"]
            },
        )
        embeddings = SimpleNamespace(
            model_name="test", embed=lambda texts: [[1.0, 0.0]]
        )
        result = retrieve(snapshot, "visual approach", embeddings)
        self.assertEqual([r["claim"]["id"] for r in result], ["0"])

    def test_temporal_expansion_is_deduplicated_bounded_and_within_time_group(self):
        result = retrieve(self.snapshot(), "本周复习时间", limit=3)
        ids = [r["claim"]["id"] for r in result] + [
            c["id"] for r in result for c in r["related_claims"]
        ]
        self.assertLessEqual(len(ids), 3)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {"2", "3"})
        self.assertEqual(sum(len(r["states"]) for r in result), 1)
        self.assertNotIn("登山", json.dumps(result, ensure_ascii=False))
