import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp_core import config
from webapp_core.session_store import SessionStore


class ChatModeTests(unittest.TestCase):
    def test_only_two_modes_and_legacy_auto_defaults_to_instant(self):
        self.assertEqual(config.MODE_SET, {"instant", "deepsearch"})
        for old_mode in ("auto", "AUTO", None, ""):
            self.assertEqual(SessionStore.normalize_mode(old_mode), "instant")
        self.assertEqual(SessionStore.normalize_mode("deepsearch"), "deepsearch")

    def test_restoring_auto_session_preserves_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chats.json"
            messages = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}]
            path.write_text(json.dumps({"sessions": [{"chat_id": "old", "title": "旧会话", "mode": "auto", "messages": messages}]}))
            with patch.object(config, "WEB_CHAT_STORE_PATH", str(path)):
                store = SessionStore(lambda _: None)
                store.load_sessions_from_disk()
                session = store.get_session("old")
                self.assertEqual(session.mode, "instant")
                self.assertEqual(session.title, "旧会话")
                self.assertEqual([(m["role"], m["content"]) for m in session.messages], [(m["role"], m["content"]) for m in messages])
