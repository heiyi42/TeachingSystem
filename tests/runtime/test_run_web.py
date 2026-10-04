import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import run_web


ROOT = Path(__file__).resolve().parents[2]
CHILD = """
import os, pathlib, sys, time
folder = pathlib.Path(sys.argv[1])
name = sys.argv[2]
(folder / (name + '.pid')).write_text(str(os.getpid()))
while not (folder / (name + '.exit')).exists():
    time.sleep(.02)
raise SystemExit(int((folder / (name + '.exit')).read_text()))
"""
RUNNER = """
import os, pathlib, sys
from scripts.run_web import supervise
folder = pathlib.Path(sys.argv[1])
commands = [(name, [sys.executable, str(folder / 'child.py'), str(folder), name])
            for name in ('backend', 'worker')]
if sys.argv[2] == 'missing':
    commands[1] = ('worker', [str(folder / 'missing-executable')])
raise SystemExit(supervise(commands, cwd=folder, env=os.environ.copy()))
"""


@unittest.skipUnless(os.name == "posix", "Process groups require POSIX")
class WebSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.folder / "child.py").write_text(CHILD)

    def start(self, mode="normal"):
        process = subprocess.Popen(
            [sys.executable, "-c", RUNNER, str(self.folder), mode],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=15)

    def children(self, process):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            files = [self.folder / f"{name}.pid" for name in ("backend", "worker")]
            if all(p.exists() and p.read_text() for p in files):
                return [int(p.read_text()) for p in files]
            if process.poll() is not None:
                self.fail(process.communicate()[1])
            time.sleep(.02)
        self.fail("Children did not start")

    def assert_stopped(self, pids):
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_interrupt_and_terminate_stop_both_children(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                for path in self.folder.glob("*.pid"):
                    path.unlink()
                process = self.start()
                pids = self.children(process)
                process.send_signal(sig)
                process.communicate(timeout=15)
                self.assertEqual(process.returncode, 128 + sig)
                self.assert_stopped(pids)

    def test_either_child_exiting_stops_its_sibling(self):
        for name, status in (("worker", 7), ("backend", 0)):
            with self.subTest(child=name):
                for pattern in ("*.pid", "*.exit"):
                    for path in self.folder.glob(pattern):
                        path.unlink()
                process = self.start()
                pids = self.children(process)
                (self.folder / f"{name}.exit").write_text(str(status))
                _, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, status or 1)
                self.assertIn(name, stderr)
                self.assert_stopped(pids)

    def test_spawn_failure_cleans_up_started_child(self):
        process = self.start("missing")
        _, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 1)
        self.assertIn("FileNotFoundError", stderr)
        pid_file = self.folder / "backend.pid"
        if pid_file.exists():
            self.assert_stopped([int(pid_file.read_text())])


class WebStartupTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        original_cwd = Path.cwd()
        self.addCleanup(os.chdir, original_cwd)
        self.enterContext(patch.object(run_web, "ROOT", self.folder))
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def test_dotenv_port_conflict_does_not_start_or_stop_existing_service(self):
        with socket.create_server(("127.0.0.1", 0)) as listener:
            port = listener.getsockname()[1]
            (self.folder / ".env").write_text(f"WEB_PORT={port}\n")
            with patch.object(run_web, "supervise") as supervise:
                self.assertEqual(run_web.main(), 1)
                supervise.assert_not_called()
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass

    def test_environment_overrides_dotenv_and_both_children_share_absolute_store(self):
        (self.folder / ".env").write_text(
            "WEB_PORT=12345\nWEB_LEARNING_STORE_PATH=wrong.sqlite3\n"
        )
        os.environ.update(WEB_PORT="12346", WEB_LEARNING_STORE_PATH="data/shared.sqlite3")
        with (
            patch.object(run_web.socket, "create_server") as server,
            patch.object(run_web.importlib.util, "find_spec", return_value=object()),
            patch.object(run_web.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
            patch.object(run_web, "supervise", return_value=0) as supervise,
        ):
            self.assertEqual(run_web.main(), 0)
        self.assertEqual(server.call_args.args[0], ("127.0.0.1", 12346))
        env = supervise.call_args.kwargs["env"]
        self.assertEqual(env["WEB_LEARNING_STORE_PATH"], str((self.folder / "data/shared.sqlite3").resolve()))
        self.assertEqual(supervise.call_args.kwargs["cwd"], self.folder)
        commands = supervise.call_args.args[0]
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(command[0] == sys.executable for _, command in commands))


if __name__ == "__main__":
    unittest.main()
