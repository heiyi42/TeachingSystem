import asyncio
import importlib.util
import json
from contextlib import closing
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from webapp_core.assistant.assistant_store import AssistantStore, enqueue


class AssistantStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = AssistantStore(Path(tmp.name) / "learning.sqlite3")

    def event(self, key="event-a", owner="alice", content="每天半小时"):
        with closing(self.store.connect()) as db, db:
            enqueue(db, owner, key, content)

    def test_message_and_event_idempotency_and_owner_isolation(self):
        self.assertTrue(self.store.begin_message("alice", "a" * 32, "今天半小时"))
        self.store.finish_message("alice", "a" * 32, "先做一道练习")
        self.assertFalse(self.store.begin_message("alice", "a" * 32, "今天半小时"))
        self.assertEqual(len(self.store.view("alice")["messages"]), 2)
        self.assertEqual(self.store.view("bob")["messages"], [])
        with self.assertRaises(ValueError):
            self.store.begin_message("bob", "a" * 32, "今天半小时")
        self.assertEqual(len(self.store.claim()["events"]), 1)

    def test_new_events_during_build_remain_queued(self):
        self.event()
        job = self.store.claim()
        self.assertIsNone(self.store.claim())
        self.event("event-b")
        self.assertTrue(self.store.publish(job, {"claims": {}}))
        newer = self.store.claim()
        self.assertEqual([event["id"] for event in newer["events"]], ["event-b"])
        self.assertEqual(newer["snapshot"]["processed_through"], job["through"])
        self.assertTrue(self.store.publish(newer, {"claims": {}}))
        self.assertEqual(self.store.view("alice")["memory_status"], "ready")
        self.assertEqual(self.store.view("alice")["version"], 2)

    def test_disable_delete_and_expired_worker_cannot_publish(self):
        self.event()
        old = self.store.claim()
        self.store.settings("alice", enabled=False)
        self.assertFalse(self.store.publish(old, {"secret": "old"}))
        self.event("ignored")
        self.store.settings("alice", enabled=True)
        next_job = self.store.claim()
        self.assertEqual(len(next_job["events"]), 1)
        self.store.settings("alice", forget=True)
        self.assertFalse(self.store.publish(next_job, {"secret": "old"}))
        self.assertEqual(self.store.view("alice")["snapshot"], {})
        self.event()
        self.assertIsNone(self.store.claim())

    def test_forget_source_discards_old_reply_and_rebuilds_remaining_sources(self):
        self.store.begin_message("alice", "a" * 32, "私人的安排")
        self.event("other")
        job = self.store.claim()
        self.store.settings("alice", event_ids=["a" * 32])
        self.store.finish_message("alice", "a" * 32, "不应恢复的回答")
        self.assertFalse(self.store.publish(job, {"secret": True}))
        self.assertEqual(self.store.view("alice")["messages"], [])
        self.assertEqual([e["id"] for e in self.store.claim()["events"]], ["other"])

    def test_crash_lease_reclaim_and_retry_fencing(self):
        self.event()
        old = self.store.claim()
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE assistant_jobs SET lease=0")
        current = self.store.claim()
        self.assertNotEqual(old["token"], current["token"])
        self.assertFalse(self.store.publish(old, {}))
        self.store.fail(old)
        self.assertIsNone(self.store.view("alice")["memory_error"])
        self.store.fail(current)
        self.assertIsNotNone(self.store.view("alice")["memory_error"])
        self.assertIsNone(self.store.claim())

    def test_outbox_rolls_back_with_source_transaction(self):
        with self.assertRaises(RuntimeError):
            with closing(self.store.connect()) as db, db:
                enqueue(db, "alice", "event-a", "没有提交的事实")
                raise RuntimeError()
        self.assertIsNone(self.store.claim())


@unittest.skipUnless(
    importlib.util.find_spec("sts"), "Install .[assistant] for STS integration tests"
)
class STSAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sts_graph_state_and_chinese_retrieval(self):
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
        from webapp_core.assistant.assistant_memory import STSMemory, retrieve

        # Network calls are replaced; graph building, state threading, chronology,
        # persistence payload and Chinese retrieval are actual STS/application code.
        async def scope_extract(_self, request):
            ids = [e.event_id for e in request.history_event_list] + [
                request.new_event.event_id
            ]
            scope = Scope(
                scope_id="scope-a",
                title="复习时间",
                summary="复习安排变化",
                event_ids=ids,
                timestamp=request.new_event.timestamp,
                user_id_list=["alice"],
            )
            relation = ScopeEventRelation(
                id="scope-link",
                relation={key: "developing" for key in ids},
                weights={key: 1.0 for key in ids},
                scope_node_id="scope-a",
            )
            return ScopeExtractResult([scope], "create_new"), [
                ScopeEventRelationExtractResult("scope-a", relation)
            ]

        async def claim_extract(_self, scope, events):
            claims = [
                Claim(
                    claim_id="claim-" + e.event_id,
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
                    scope_id=scope.scope_id,
                    event_id=e.event_id,
                    event_claim_relation=EventClaimRelation(
                        id="link-" + e.event_id,
                        relation={c.claim_id: "core"},
                        event_node_id=e.event_id,
                    ),
                )
                for e, c in zip(events, claims)
            ]
            return ClaimExtractResult(scope_id=scope.scope_id, claims=claims), links

        embeddings = SimpleNamespace(
            model_name="test", embed=lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
        )
        with (
            patch(
                "sts.extraction.scope_extractor.ScopeExtractor.extract_scope",
                scope_extract,
            ),
            patch(
                "sts.extraction.claim_extractor.ClaimExtractor.extract_claims",
                claim_extract,
            ),
        ):
            result = await STSMemory(SimpleNamespace(), embeddings).build(
                "alice",
                [
                    {"id": "one", "created": 1000000000, "content": "每天复习一小时"},
                    {
                        "id": "two",
                        "created": 1000086400,
                        "content": "这周每天只有半小时",
                    },
                ],
            )
        self.assertEqual(len(result["claims"]), 2)
        self.assertTrue(result["state_results"][0]["states"])
        recalled = retrieve(result, "复习时间", embeddings)
        self.assertEqual(len(recalled), 2)
        self.assertTrue(recalled[0]["states"])
        self.assertEqual(set(result["events"]), {"one", "two"})

    async def test_upstream_extractors_with_scripted_provider(self):
        import json
        import re
        import io
        from contextlib import redirect_stdout
        from webapp_core.assistant.assistant_memory import STSMemory

        class Provider:
            calls = 0

            async def generate(self, prompt, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    value = {
                        "title": "复习安排",
                        "summary": "每天复习半小时",
                        "keywords": ["复习"],
                    }
                elif self.calls == 2:
                    value = {
                        "event_roles": [
                            {"event_id": "event_1", "role": "initiating", "weight": 1}
                        ],
                        "coherence_score": 1,
                        "reasoning": "用户明确安排",
                    }
                elif self.calls == 3:
                    value = {
                        "claims": [
                            {
                                "claim_id": "claim_1",
                                "content": "学生每天复习半小时",
                                "event_ids": ["event_1"],
                                "confidence": 0.9,
                            }
                        ]
                    }
                elif self.calls == 4:
                    claim_id = re.search(r"ID: (claim_[a-f0-9-]+)", prompt).group(1)
                    value = {
                        "claim_roles": [
                            {"claim_id": claim_id, "role": "core", "weight": 1}
                        ],
                        "extraction_confidence": 0.9,
                    }
                else:
                    raise asyncio.CancelledError("Unexpected retry")
                return json.dumps(value, ensure_ascii=False)

        provider = Provider()
        embeddings = SimpleNamespace(
            model_name="test", embed=lambda texts: [[1.0, 0.0] for _ in texts]
        )
        with redirect_stdout(io.StringIO()):
            result = await asyncio.wait_for(
                STSMemory(provider, embeddings).build(
                    "alice",
                    [
                        {
                            "id": "source-a",
                            "content": "每天复习半小时",
                            "created": 1000000000,
                        }
                    ],
                ),
                timeout=10,
            )
        self.assertEqual(provider.calls, 4)
        self.assertEqual(len(result["claims"]), 1)
        claim = next(iter(result["claims"].values()))
        self.assertEqual(claim["event_ids"], ["source-a"])
        self.assertTrue(result["state_results"][0]["states"])


from tests.school import test_school_permissions as school_fixtures


class AssistantHTTPTests(unittest.TestCase):
    setUpClass = school_fixtures.SchoolPermissionsTests.__dict__["setUpClass"]
    setUp = school_fixtures.SchoolPermissionsTests.setUp
    credentials = staticmethod(school_fixtures.SchoolPermissionsTests.credentials)
    account = school_fixtures.SchoolPermissionsTests.account

    def test_missing_snapshot_is_visible_and_retry_requires_restored_file(self):
        owner = self.student_user["id"]
        store = AssistantStore(self.path)
        with closing(store.connect()) as db, db:
            enqueue(db, owner, "file-source", "每天复习半小时")
        store.publish(store.claim(), {})
        path = next(store.memory_dir.rglob("*.json"))
        original = path.read_bytes()
        path.unlink()
        response = self.student.get("/api/assistant")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["memory_status"], "failed")
        self.assertIn("恢复", response.json["memory_error"])
        failed = self.student.post("/api/assistant/memory/retry", json={})
        self.assertEqual(failed.status_code, 400)
        self.assertIn("恢复", failed.json["error"])
        path.write_bytes(original)
        self.assertEqual(self.student.post("/api/assistant/memory/retry", json={}).status_code, 200)
        job = store.claim()
        self.assertEqual(job["events"], [])
        self.assertTrue(store.publish(job, job["snapshot"]))
        self.assertEqual(self.student.get("/api/assistant").json["memory_status"], "ready")

    def test_memory_display_preserves_sources_and_time_ranges(self):
        owner = self.student_user["id"]
        store = AssistantStore(self.path)
        with closing(store.connect()) as db, db:
            enqueue(db, owner, "display-source", "原始来源")
        job = store.claim()
        snapshot = {
            "events": {"display-source": {
                "summary": f"{owner}完成lru_01，lru_010不应被替换。",
                "timestamp": "2026-10-04T20:36:43Z",
            }},
            "claims": {
                "instant": {
                    "content": f"参与者{owner}通过lru_01。",
                    "temporal": "2026年10月4日20:36:43",
                    "event_ids": ["display-source"],
                },
                "range": {
                    "content": "本周每天25分钟，下周恢复50分钟。",
                    "temporal": "2026年10月4日至10月10日",
                    "event_ids": ["display-source"],
                },
            },
        }
        store.publish(job, snapshot)
        response = self.student.get("/api/assistant")
        self.assertEqual(response.status_code, 200)
        memories = {m["id"]: m for m in response.json["memories"]}
        self.assertIn(self.student_user["name"], memories["instant"]["content"])
        self.assertIn("LRU · 命中后的置换", memories["instant"]["content"])
        self.assertNotIn(owner, memories["instant"]["content"])
        self.assertEqual(memories["instant"]["temporal"], "2026-10-05 04:36:43 北京时间")
        source = memories["instant"]["sources"][0]
        self.assertEqual(source["timestamp"], "2026-10-04T20:36:43Z")
        self.assertEqual(source["timestamp_label"], "2026-10-05 04:36:43 北京时间")
        self.assertIn("lru_010", source["text"])
        self.assertEqual(memories["range"]["temporal"], "2026年10月4日至10月10日")
        self.assertEqual(store.view(owner)["snapshot"], {**snapshot, "processed_through": job["through"]})
        self.assertEqual(self.other_student.get("/api/assistant").json["memories"], [])

    def test_assistant_receives_assistance_at_each_submission(self):
        from webapp_core.learning.learning_store import LearningStore
        from webapp_core.learning.learning_service import LearningService
        from tests.learning.test_learning_service import LRU_01

        learning = LearningService(
            LearningStore(self.path, owner_id=self.student_user["id"]), self.solver
        )
        attempt = learning.start("lru_01")
        wrong = [dict(row) for row in LRU_01]
        wrong[-1]["evicted"] = "2"
        learning.submit(attempt["id"], wrong)
        learning.hint(attempt["id"], None)
        learning.solution(attempt["id"])
        learning.submit(attempt["id"], LRU_01)
        repeated = learning.start("lru_01")
        learning.submit(repeated["id"], LRU_01)
        learning.start("dh_01", assignment_id="private-assignment")
        other = LearningService(
            LearningStore(self.path, owner_id=self.other_user["id"]), self.solver
        )
        other.start("c_pointer_01")
        model = AsyncMock(return_value=SimpleNamespace(content="先继续新题核验。"))
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", SimpleNamespace(ainvoke=model)):
            response = self.student.post("/api/assistant/messages", json={
                "request_id": "context-check-123456", "content": "我的作答情况如何？"
            })
        self.assertEqual(response.status_code, 200, response.json)
        prompt = model.call_args.args[0][0][1]
        context = json.loads(prompt.split("\n授权上下文：", 1)[1])
        progress = context["recent_practice"]
        self.assertEqual(len(progress), 2)
        corrected = next(p for p in progress if p["提交次数"] == 2)
        self.assertEqual(corrected["课程"], "操作系统")
        self.assertEqual(corrected["题型"], "page_replacement")
        self.assertEqual(corrected["累计提示次数"], 1)
        self.assertTrue(corrected["已查看解析"])
        self.assertFalse(corrected["新题首次独立通过"])
        first, second = corrected["逐次提交"]
        self.assertTrue(first["新题首次独立作答"])
        self.assertFalse(first["通过"])
        self.assertFalse(first["提交时的辅助记录"]["查看解析"])
        self.assertTrue(second["通过"])
        self.assertFalse(second["新题首次独立作答"])
        self.assertTrue(second["提交时的辅助记录"]["查看解析"])
        repeat = next(p for p in progress if p["提交次数"] == 1)
        self.assertTrue(repeat["逐次提交"][0]["提交时的辅助记录"]["重复题"])
        self.assertFalse(repeat["新题首次独立通过"])

    def test_failed_reply_can_retry_without_duplicate_messages(self):
        data = {"request_id": "retry-timeout-123456", "content": "我该怎么复习？"}
        model = AsyncMock(side_effect=[TimeoutError(), SimpleNamespace(content="先用新题核验。")])
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", SimpleNamespace(ainvoke=model)):
            failed = self.student.post("/api/assistant/messages", json=data)
            self.assertEqual(failed.status_code, 503)
            messages = self.student.get("/api/assistant").json["messages"]
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["status"], "failed")
            retried = self.student.post("/api/assistant/messages", json=data)
            self.assertEqual(retried.status_code, 200)
            duplicate = self.student.post("/api/assistant/messages", json=data)
            self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(model.await_count, 2)
        self.assertEqual(len(duplicate.json["messages"]), 2)
        self.assertEqual(duplicate.json["messages"][1]["content"], "先用新题核验。")

    def test_followup_keeps_history_as_data_and_current_request_separate(self):
        from webapp_core.assistant.assistant_prompts import STS_ANSWER_GUIDANCE

        model = AsyncMock(return_value=SimpleNamespace(content="请补充章节范围。"))
        with patch("webapp_core.chat.auto_runtime.auto_router_llm", SimpleNamespace(ainvoke=model)):
            self.student.post("/api/assistant/messages", json={
                "request_id": "history-context-0001", "content": "我每天只有20分钟。",
            })
            response = self.student.post("/api/assistant/messages", json={
                "request_id": "history-context-0002", "content": "先复习虚拟内存，按刚才的时间安排。",
            })
        self.assertEqual(response.status_code, 200)
        messages = model.call_args.args[0]
        self.assertIn(STS_ANSWER_GUIDANCE, messages[0][1])
        self.assertEqual([role for role, _ in messages], ["system", "human"])
        self.assertEqual(messages[-1][1], "先复习虚拟内存，按刚才的时间安排。")
        context = json.loads(messages[0][1].split("\n授权上下文：", 1)[1])
        self.assertEqual(context["conversation"], [
            {"role": "user", "content": "我每天只有20分钟。"},
            {"role": "assistant", "content": "请补充章节范围。"},
        ])

    def test_identity_messages_and_private_memory(self):
        self.assertEqual(self.app.test_client().get("/api/assistant").status_code, 401)
        model = AsyncMock(
            return_value=SimpleNamespace(content="可以，先确认考试范围。")
        )
        with patch(
            "webapp_core.chat.auto_runtime.auto_router_llm", SimpleNamespace(ainvoke=model)
        ):
            result = self.student.post(
                "/api/assistant/messages",
                json={
                    "request_id": "a" * 32,
                    "content": "下周考试，每天半小时",
                    "owner": self.other_user["id"],
                },
            )
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(len(result.json["messages"]), 2)
        self.assertEqual(self.other_student.get("/api/assistant").json["messages"], [])
        self.assertEqual(self.teacher.get("/api/assistant").json["messages"], [])
        self.assertEqual(
            self.other_student.delete(
                "/api/assistant/memory/not-mine", json={}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.student.delete("/api/assistant/memory", json={}).json["messages"], []
        )

    def test_practice_outbox_and_no_assignment_payload(self):
        from webapp_core.learning.learning_store import LearningStore
        from webapp_core.learning.learning_service import LearningService
        from tests.learning.test_learning_service import LRU_01

        owner = self.student_user["id"]
        learning = LearningService(
            LearningStore(self.path, owner_id=owner), self.solver
        )
        attempt = learning.start("lru_01")
        learning.submit(attempt["id"], LRU_01)
        store = AssistantStore(self.path)
        job = store.claim()
        self.assertEqual(job["owner"], owner)
        self.assertIn("首次独立通过", job["events"][0]["content"])
        self.assertNotIn("rows", job["events"][0]["content"])
