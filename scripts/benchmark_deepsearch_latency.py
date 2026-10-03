"""Measure real DeepSearch requests against an isolated test server and account."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import requests


CASES = [
    (
        "c_source",
        "C_program",
        "请根据课程资料解释 C 语言数组名与指针变量的区别，并给出资料出处。简短回答。",
    ),
    (
        "os_source",
        "operating_systems",
        "请根据教材解释死锁的四个必要条件，并注明出处。简短回答。",
    ),
    (
        "security_source",
        "cybersec_lab",
        "请依据课程资料解释数字签名与加密的区别，给出引用来源。简短回答。",
    ),
    ("simple", "C_program", "C 语言中 sizeof(char) 的值是多少？一句话回答。"),
]


def measure(client, base, case):
    name, subject, question = case
    response = client.post(base + "/api/chats", json={"mode": "deepsearch"}, timeout=10)
    response.raise_for_status()
    chat = response.json()["chat_id"]
    started = time.perf_counter()
    first_text_ms, done, event = None, {}, ""
    delta_events = 0
    with client.post(
        base + f"/api/chats/{chat}/messages/stream",
        json={"message": question, "mode": "deepsearch", "subjects": [subject]},
        stream=True,
        timeout=(10, 240),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(chunk_size=1, decode_unicode=True):
            if line.startswith("event: "):
                event = line[7:]
            if not line.startswith("data: "):
                continue
            data = json.loads(line[6:])
            if event == "delta" and data.get("text"):
                delta_events += 1
                if first_text_ms is None:
                    first_text_ms = round((time.perf_counter() - started) * 1000)
            if event == "done":
                done = data
    details = done.get("message_details", {}).get("explainability", {})
    return {
        "case": name,
        "subject": subject,
        "question": question,
        "first_text_ms": first_text_ms,
        "delta_events": delta_events,
        "total_ms": round((time.perf_counter() - started) * 1000),
        "completed": details.get("status") == "done",
        "response": done,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-env", default="TEACHING_BENCHMARK_PASSWORD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    password = os.getenv(args.password_env)
    if not password or args.repeats < 1:
        parser.error("须配置测试账号密码环境变量，repeats 须大于零")
    base = args.base_url.rstrip("/")
    report = {
        "notice": "小样本真实请求记录。模型与缓存会影响耗时；不代表总体性能或质量评测。每条请求创建独立测试聊天。",
        "runs": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with requests.Session() as client:
        login = client.post(
            base + "/api/identity/login",
            json={"username": args.username, "password": password},
            timeout=10,
        )
        login.raise_for_status()
        for repeat in range(args.repeats):
            for case in CASES:
                try:
                    row = measure(client, base, case)
                except requests.RequestException as error:
                    row = {
                        "case": case[0],
                        "completed": False,
                        "error": type(error).__name__,
                    }
                row["repeat"] = repeat + 1
                report["runs"].append(row)
                args.output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(
                    f"{case[0]}: completed={row['completed']}, first={row.get('first_text_ms')} ms, total={row.get('total_ms')} ms",
                    flush=True,
                )
    if not all(row["completed"] for row in report["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
