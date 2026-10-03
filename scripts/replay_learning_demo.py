from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp_core.learning_routes import learning_blueprint  # noqa: E402
from webapp_core.learning_service import LearningService  # noqa: E402
from webapp_core.learning_store import LearningStore  # noqa: E402
from webapp_core.problem_tutoring_service import ProblemTutoringService  # noqa: E402


def replay() -> dict:
    fixture = json.loads((ROOT / "frontend/src/learningDemo.json").read_text(encoding="utf-8"))
    correct, wrong, retest_rows = fixture["correct"], fixture["wrong"], fixture["retest"]
    events = []
    with tempfile.TemporaryDirectory(prefix="learning-demo-") as temporary:
        service = LearningService(LearningStore(Path(temporary) / "demo.sqlite3"), ProblemTutoringService())
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(learning_blueprint(service))
        client = app.test_client()

        def request(label, method, path, payload=None, status=200):
            response = client.open(path, method=method, json=payload)
            if response.status_code != status:
                raise RuntimeError(f"{label}失败：{response.status_code} {response.json}")
            events.append({"label": label, "method": method, "path": path,
                           "request": payload, "response": response.json})
            return response.json

        original = request("开始 LRU 原题", "POST", "/api/learning/attempts", {"exercise_id": "lru_01"}, 201)
        base = f"/api/learning/attempts/{original['id']}"
        diagnosed = request("提交错误作答", "POST", f"{base}/submit", {"rows": wrong})
        first_error = diagnosed["evaluation"]["first_error"]
        if (first_error["step"], first_error["error_code"]) != (5, "wrong_victim"):
            raise RuntimeError("首错位置或类型与演示预期不一致")
        first_hint = request("查看一级提示", "POST", f"{base}/hint", {})
        second_hint = request("查看二级提示", "POST", f"{base}/hint", {})
        if first_hint["hint"]["level"] != 1 or second_hint["hint"]["level"] != 2 or second_hint["hint_count"] != 2:
            raise RuntimeError("提示等级或计数异常")
        corrected = request("提交订正", "POST", f"{base}/submit", {"rows": correct})
        if corrected["status"] != "passed" or corrected["first_unassisted_pass"]:
            raise RuntimeError("订正应通过，且不能记为首次独立完成")
        retest = request("开始关联新题", "POST", "/api/learning/attempts", {"parent_id": original["id"]}, 201)
        if retest["exercise"]["id"] != "lru_02":
            raise RuntimeError("推荐题已变化，请先更新并核对演示作答")
        if retest["parent_id"] != original["id"]:
            raise RuntimeError("新题没有关联原练习")
        independent = request("新题无提示首次通过", "POST", f"/api/learning/attempts/{retest['id']}/submit", {"rows": retest_rows})
        if not independent["first_unassisted_pass"]:
            raise RuntimeError("新题的独立完成记录异常")
        progress = request("查看学习记录", "GET", "/api/learning/progress")
        expected = {"attempts": 2, "submitted": 2, "first_unassisted_passes": 1,
                    "corrected_passes": 1, "independent_retest_passes": 1}
        if progress["summary"] != expected:
            raise RuntimeError(f"统计与操作不一致：{progress['summary']}")
    return {
        "kind": "simulated_demo", "notice": "模拟作答，通过真实 Flask 训练接口回放；不是学生学习效果证据，未写入正式数据库。",
        "run_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "summary": progress["summary"], "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="回放三分钟 LRU 演示；使用临时数据库，不修改正式记录。")
    parser.add_argument("--output", type=Path, default=ROOT / "output/learning_evaluation/demo_flow.json")
    args = parser.parse_args()
    result = replay()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"模拟演示完成：{len(result['events'])} 个接口步骤。")
    print(f"原题订正通过 1 次，新题独立通过 1 次。证据：{args.output}")


if __name__ == "__main__":
    main()
