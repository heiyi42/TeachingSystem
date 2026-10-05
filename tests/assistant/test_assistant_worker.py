import sqlite3
import unittest
from unittest.mock import Mock, patch

from scripts.run_assistant_worker import main


class AssistantWorkerTests(unittest.TestCase):
    def test_claim_failure_is_retried_without_exiting_worker(self):
        store = Mock()
        store.claim.side_effect = [sqlite3.OperationalError("locked"), None]
        with (
            patch("scripts.run_assistant_worker.AssistantStore", return_value=store),
            patch("sys.argv", ["worker"]),
            patch(
                "scripts.run_assistant_worker.time.sleep",
                side_effect=[None, KeyboardInterrupt],
            ),
            patch("scripts.run_assistant_worker.logging.disable"),
            patch("builtins.print") as output,
        ):
            with self.assertRaises(KeyboardInterrupt):
                main()
        self.assertEqual(store.claim.call_count, 2)
        store.initialize_memory.assert_called_once_with()
        store.fail.assert_not_called()
        self.assertIn("OperationalError", output.call_args.args[0])

    def test_once_claim_failure_returns_nonzero(self):
        store = Mock()
        store.claim.side_effect = sqlite3.OperationalError("private database path")
        with (
            patch("scripts.run_assistant_worker.AssistantStore", return_value=store),
            patch("sys.argv", ["worker", "--once"]),
            patch("scripts.run_assistant_worker.logging.disable"),
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(), 1)
        self.assertNotIn("private database path", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
