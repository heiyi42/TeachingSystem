"""Run the web backend and assistant worker with a shared lifecycle."""

import importlib.util
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from threading import Event

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


def supervise(commands, *, cwd, env):
    stopped = Event()
    exit_code = 0
    children = []

    def stop(signum, _frame):
        nonlocal exit_code
        exit_code = 128 + signum
        stopped.set()

    previous = {
        sig: signal.signal(sig, stop)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        for name, command in commands:
            if stopped.is_set():
                break
            # Separate groups let us also stop Flask's debug reloader children.
            child = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
            children.append((name, child))
        while not stopped.is_set():
            for name, child in children:
                status = child.poll()
                if status is not None:
                    print(f"{name} 已退出（退出码 {status}），正在停止其他进程。", file=sys.stderr)
                    exit_code = status if status > 0 else 1
                    stopped.set()
                    break
            stopped.wait(0.2)
    except OSError as error:
        print(f"启动失败：{type(error).__name__}。", file=sys.stderr)
        exit_code = 1
    finally:
        for _, child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for _, child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return exit_code


def main():
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    host = os.getenv("WEB_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("WEB_PORT", "7860"))
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        print("WEB_PORT 须为 1—65535 的整数。", file=sys.stderr)
        return 1
    try:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.create_server((host, port), family=family):
            pass
    except OSError:
        print(f"无法监听 {host}:{port}，请检查地址或端口占用；已有进程未被停止。", file=sys.stderr)
        return 1

    if importlib.util.find_spec("sts") is None:
        print('缺少 STS 依赖，请使用当前 Python 执行 pip install -e ".[assistant]"。', file=sys.stderr)
        return 1
    checked = subprocess.run([sys.executable, str(ROOT / "scripts/check_assistant_live.py")])
    if checked.returncode:
        return checked.returncode

    env = os.environ.copy()
    store = Path(env.get("WEB_LEARNING_STORE_PATH") or "./data/learning.sqlite3").resolve()
    env["WEB_LEARNING_STORE_PATH"] = str(store)
    env["PYTHONUNBUFFERED"] = "1"
    print(f"启动后端与记忆 worker，共用数据库：{store}。按 Ctrl+C 一起停止。", flush=True)
    return supervise(
        [
            ("后端", [sys.executable, str(ROOT / "webapp.py")]),
            ("记忆 worker", [sys.executable, str(ROOT / "scripts/run_assistant_worker.py")]),
        ],
        cwd=ROOT,
        env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
