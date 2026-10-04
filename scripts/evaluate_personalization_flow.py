from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp_core.learning.learning_roadshow import CASES, roadshow


def evaluate():
    checks = []
    for subject in CASES:
        a, b = roadshow(subject)["students"]
        checks.extend(
            [
                {
                    "subject": subject,
                    "check": "新题首次独立通过保留独立证据",
                    "passed": a["independent_pass"]
                    and a["memories"][0]["status"] == "independent_evidence",
                },
                {
                    "subject": subject,
                    "check": "后续错误不记为掌握",
                    "passed": not b["independent_pass"]
                    and b["memories"][0]["status"] == "needs_work",
                },
                {
                    "subject": subject,
                    "check": "仍有错误安排继续订正",
                    "passed": any(t["kind"] == "continue" for t in b["tasks"]),
                },
                {
                    "subject": subject,
                    "check": "两条路径均保留完整过程",
                    "passed": len(a["timeline"]) == len(b["timeline"]) == 6,
                },
            ]
        )
    return {
        "kind": "simulated_functional_validation",
        "real_participants": 0,
        "simulated_paths": 6,
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
        "checks": checks,
        "notice": "这是模拟路径的功能一致性检查，不验证判题本身的准确率，不是教学效果研究，不能推导成绩提升或真实个性化收益。",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output/learning_evaluation/personalization_flow.json",
    )
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"模拟功能检查：{result['passed']}/{result['total']}，真实参与者：0。报告：{args.output}"
    )
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
