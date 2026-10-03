"""Check streaming and original-source integrity against an isolated HTTP server."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_sources(answer, citations, storage_root=ROOT / "storage"):
    sources = (citations or {}).get("sources", [])
    errors, known, databases = [], {}, {}
    for item in sources:
        subject = item.get("subject_id")
        if subject not in {"C_program", "operating_systems", "cybersec_lab"}:
            errors.append("unknown_subject")
            continue
        if subject not in databases:
            databases[subject] = json.loads(
                (storage_root / subject / "kv_store_text_chunks.json").read_text()
            )
        original = databases[subject].get(item.get("chunk_id")) or {}
        if str(original.get("content") or "").strip().strip(
            "\ufffd"
        ).strip() != item.get("text"):
            errors.append(f"{item.get('id')}:source_text_mismatch")
        if Path(original.get("file_path", "")).name != item.get("source"):
            errors.append(f"{item.get('id')}:source_file_mismatch")
        if item.get("id") in known:
            errors.append("duplicate_citation_id")
        known[item.get("id")] = item
    used = set(re.findall(r"\[(E\d+)\]", answer))
    if used - set(known):
        errors.append("unknown_citation_id")
    if (citations or {}).get("invalidIds"):
        errors.append("invalid_citation_generated")
    return errors, len(used & set(known))


def evaluate_case(base, credentials, case):
    started = time.monotonic()
    result = {"case": case, "passed": False}
    try:
        with requests.Session() as client:
            client.post(
                base + "/api/identity/login", json=credentials, timeout=10
            ).raise_for_status()
            response = client.post(
                base + "/api/chats", json={"mode": case["mode"]}, timeout=10
            )
            response.raise_for_status()
            chat = response.json()["chat_id"]
            final, event = {}, ""
            with client.post(
                base + f"/api/chats/{chat}/messages/stream",
                json={
                    "message": case["question"],
                    "mode": case["mode"],
                    "subjects": case["subjects"],
                },
                stream=True,
                timeout=(10, 360),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: ") and event == "done":
                        final = json.loads(line[6:])
            answer = final.get("answer", "")
            details = final.get("message_details", {}).get("explainability", {})
            citations = details.get("citations")
            errors, count = check_sources(answer, citations)
            if details.get("status") != "done":
                errors.append("request_not_completed")
            if case["require_citations"] and not count:
                errors.append("required_citation_missing")
            result.update(
                answer=answer,
                citations=citations,
                source_errors=errors,
                mode_used=final.get("mode_used"),
                chat_id=chat,
            )
            if details.get("status") != "done":
                result["completed"] = False
                result["error"] = "回答生成未完成"
                result["elapsed_seconds"] = round(time.monotonic() - started, 2)
                return result
            result["completed"] = True
            result["passed"] = not errors

    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-env", default="TEACHING_BENCHMARK_PASSWORD")
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "tests/fixtures/qa_source_cases.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 4))
    args = parser.parse_args()
    password = os.getenv(args.password_env)
    if not password:
        parser.error("请配置测试账号密码环境变量")
    cases = json.loads(args.cases.read_text())["cases"]
    if args.only:
        if set(args.only) - {c["id"] for c in cases}:
            parser.error("存在未知测试编号")
        cases = [c for c in cases if c["id"] in args.only]
    report = {
        "scope": "仅检查请求完成、引用编号和原文文件匹配，不评价答案语义正确率。",
        "runs": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                evaluate_case,
                args.base_url.rstrip("/"),
                {"username": args.username, "password": password},
                case,
            )
            for case in cases
        ]
        for future in as_completed(futures):
            row = future.result()
            report["runs"].append(row)
            report["passed"] = sum(r["passed"] for r in report["runs"])
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            print(
                f"{row['case']['id']}: passed={row['passed']}, elapsed={row['elapsed_seconds']}s",
                flush=True,
            )
    if not all(row["passed"] for row in report["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
