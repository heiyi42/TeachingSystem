from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp_core.learning_exercises import EXERCISES  # noqa: E402
from webapp_core.learning_service import LearningService  # noqa: E402
from webapp_core.learning_store import LearningStore  # noqa: E402
from webapp_core.problem_tutoring_service import ProblemTutoringService  # noqa: E402


DEFAULT_FIXTURE = ROOT / "tests/fixtures/learning_diagnosis_v1.json"


def signature(problem: dict[str, Any]) -> str:
    parameters = copy.deepcopy(problem["parameters"])
    if "processes" in parameters:
        parameters["processes"].sort(key=lambda process: process["name"])
    return json.dumps(
        [problem["algorithm"], problem["kind"], parameters],
        sort_keys=True,
    )


def load_cases(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("schema_version") != 1 or not dataset.get("problems"):
        raise ValueError("评测集版本不支持或题目为空")
    training_inputs = {signature(item) for item in EXERCISES.values()}
    problems_seen: set[str] = set()
    ids_seen: set[str] = set()
    cases = []
    for problem in dataset["problems"]:
        if not problem.get("gold_rows") or not problem.get("variants"):
            raise ValueError(f"参考作答或变体为空：{problem.get('id')}")
        key = signature(problem)
        if key in training_inputs or key in problems_seen:
            raise ValueError(f"题目与训练集或其他评测题重复：{problem['id']}")
        problems_seen.add(key)
        snapshots_seen: set[str] = set()
        for variant in problem["variants"]:
            case_id = f"{problem['id']}/{variant['id']}"
            if case_id in ids_seen:
                raise ValueError(f"评测编号重复：{case_id}")
            ids_seen.add(case_id)
            rows = copy.deepcopy(problem["gold_rows"])
            for change in variant["changes"]:
                index = change["step"] - 1
                if not 0 <= index < len(rows) or change["field"] not in rows[index]:
                    raise ValueError(f"错误变体位置无效：{case_id}")
                if rows[index][change["field"]] == change["value"]:
                    raise ValueError(f"变体没有实际改变字段：{case_id}")
                rows[index][change["field"]] = change["value"]
            if variant["drop_last"]:
                rows.pop()
            if variant["append"] is not None:
                rows.append(copy.deepcopy(variant["append"]))
            snapshot = json.dumps(rows, sort_keys=True)
            if snapshot in snapshots_seen:
                raise ValueError(f"同题重复作答变体：{case_id}")
            snapshots_seen.add(snapshot)
            expectation = variant["expected"]
            if expectation["category"] not in {"correct", "incorrect", "incomplete", "invalid"}:
                raise ValueError(f"类别无效：{case_id}")
            if expectation["category"] in {"incorrect", "incomplete"}:
                step = expectation["first_error_step"]
                if type(step) is not int or not 1 <= step <= len(rows) + 1:
                    raise ValueError(f"首错标注无效：{case_id}")
            cases.append({
                "id": case_id,
                "problem": problem,
                "rows": rows,
                "expected": copy.deepcopy(expectation),
            })
    return dataset, cases


def evaluate_case(service: LearningService, case: dict[str, Any]) -> dict[str, Any]:
    problem, expected = case["problem"], case["expected"]
    try:
        # 与 submit 共用输入校验和诊断路径；参考答案始终来自固定评测文件。
        draft = service._draft(problem, case["rows"])
        evaluation = service._evaluate(problem, draft)
        error = evaluation["first_error"]
        actual = {
            "category": "correct" if evaluation["passed"] else "incomplete" if error["error_code"] == "incomplete" else "incorrect",
            "first_error_step": error["step"] if error else None,
            "error_code": error["error_code"] if error else None,
            "field": error["field"] if error else None,
        }
        row_statuses = evaluation["row_statuses"]
        detail = error["message"] if error else "作答通过"
    except ValueError as exc:
        actual = {"category": "invalid", "first_error_step": None, "error_code": None, "field": None}
        row_statuses, detail = None, str(exc)
    except Exception as exc:
        actual = {"category": "exception", "first_error_step": None, "error_code": None, "field": None}
        row_statuses, detail = None, f"{type(exc).__name__}: {exc}"

    step = expected["first_error_step"]
    expected_statuses = None if expected["category"] == "invalid" else [
        "correct" if step is None or index < step else "error" if index == step else "pending"
        for index in range(1, len(case["rows"]) + 1)
    ]
    return {
        "id": case["id"], "algorithm": problem["algorithm"],
        "rows": case["rows"], "expected": expected, "actual": actual,
        "matched": actual == expected and row_statuses == expected_statuses,
        "row_statuses_match": row_statuses == expected_statuses,
        "row_statuses": row_statuses, "detail": detail,
    }


def metric(numerator: int, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator,
            "percent": round(100 * numerator / denominator, 2) if denominator else None}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    correct = [row for row in results if row["expected"]["category"] == "correct"]
    incorrect = [row for row in results if row["expected"]["category"] in {"incorrect", "incomplete"}]
    invalid = [row for row in results if row["expected"]["category"] == "invalid"]
    return {
        "case_count": len(results),
        "category_counts": dict(Counter(row["expected"]["category"] for row in results)),
        "exact_match": metric(sum(row["matched"] for row in results), len(results)),
        "correct_acceptance": metric(sum(row["actual"]["category"] == "correct" for row in correct), len(correct)),
        "false_rejection": metric(sum(row["actual"]["category"] != "correct" for row in correct), len(correct)),
        "first_error_localization": metric(sum(
            row["actual"]["category"] in {"incorrect", "incomplete"}
            and row["actual"]["first_error_step"] == row["expected"]["first_error_step"]
            for row in incorrect
        ), len(incorrect)),
        "error_type_match": metric(sum(row["actual"]["error_code"] == row["expected"]["error_code"] for row in incorrect), len(incorrect)),
        "invalid_rejection": metric(sum(row["actual"]["category"] == "invalid" for row in invalid), len(invalid)),
        "row_status_match": metric(sum(row["row_statuses_match"] for row in incorrect), len(incorrect)),
        "unexpected_exceptions": sum(row["actual"]["category"] == "exception" for row in results),
    }


def run_evaluation(fixture: Path) -> dict[str, Any]:
    dataset, cases = load_cases(fixture)
    with tempfile.TemporaryDirectory(prefix="learning-eval-") as temporary:
        service = LearningService(LearningStore(Path(temporary) / "records.sqlite3"), ProblemTutoringService())
        results = [evaluate_case(service, case) for case in cases]
    sources = [fixture, Path(__file__), ROOT / "webapp_core/learning_exercises.py",
               ROOT / "webapp_core/learning_service.py", ROOT / "webapp_core/learning_security.py",
               ROOT / "webapp_core/learning_c.py", ROOT / "webapp_core/learning_banker.py",
               ROOT / "webapp_core/problem_tutoring_service.py"]
    return {
        "dataset": dataset["name"], "provenance": dataset["provenance"],
        "fixture": str(fixture.relative_to(ROOT) if fixture.is_relative_to(ROOT) else fixture),
        "limitations": dataset["limitations"],
        "run_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "problem_count": len(dataset["problems"]),
        "training_overlap": 0,
        "source_sha256": {str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        "summary": summarize(results),
        "by_algorithm": {algorithm: summarize([row for row in results if row["algorithm"] == algorithm]) for algorithm in dict.fromkeys(row["algorithm"] for row in results)},
        "results": results,
    }


def write_report(result: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 训练中心过程诊断评测", "",
        f"运行时间：{result['run_at']}。", "",
        f"本次使用 {result['problem_count']} 道固定题目、{result['summary']['case_count']} 个构造作答。题目参数与内置训练题的重复数为 {result['training_overlap']}。",
        "", result["provenance"], "",
        "## 实测结果", "",
        "| 指标 | 分子 / 分母 | 比例 |", "|---|---:|---:|",
    ]
    labels = {
        "exact_match": "完整预期匹配", "correct_acceptance": "正确作答通过",
        "false_rejection": "正确作答误判", "first_error_localization": "首错位置一致",
        "error_type_match": "错误类型一致", "invalid_rejection": "非法输入拒绝",
        "row_status_match": "前序正确与后续待订正状态一致",
    }
    for key, label in labels.items():
        value = result["summary"][key]
        percent = "不适用" if value["percent"] is None else f"{value['percent']:.2f}%"
        lines.append(f"| {label} | {value['numerator']} / {value['denominator']} | {percent} |")
    lines.extend(["", "首错位置及类型的分母包含错误作答和未完成作答，不包含非法输入。完整匹配同时检查类别、位置、字段、错误类型及逐行状态。", "",
                  "| 算法 | 构造作答 | 完整匹配 | 首错位置一致 | 正确作答误判 |", "|---|---:|---:|---:|---:|"])
    for algorithm, summary in result["by_algorithm"].items():
        def ratio(key: str) -> str:
            value = summary[key]
            return f"{value['numerator']} / {value['denominator']}"
        lines.append(f"| {algorithm} | {summary['case_count']} | {ratio('exact_match')} | {ratio('first_error_localization')} | {ratio('false_rejection')} |")
    failures = [row for row in result["results"] if not row["matched"]]
    lines.extend(["", "## 不一致案例", ""])
    if failures:
        for row in failures:
            lines.append(f"- `{row['id']}`：预期 `{row['expected']}`，实际 `{row['actual']}`；{row['detail']}")
    else:
        lines.append("本轮未发现与固定预期不一致的案例。逐条输入、预期和实际结果保存在同目录 results.json。")
    lines.extend(["", "## 解释范围", "",
                  result["limitations"], "",
                  "这些结果只能说明当前程序在本评测集上的表现，不能外推为所有题目的准确率，也不能证明学生成绩提升或优于通用 LLM。预期轨迹由开发助手编写，仍需要教师复核；变体来自同一批基础题，存在相关性。", "",
                  "本次评测调用与提交接口相同的输入校验和诊断方法，未调用 LLM，也未写入正式学习记录。它不替代浏览器流程测试、接口权限测试或学生对照试验。", "",
                  "## 复现", "", "```bash", f"python scripts/evaluate_learning_diagnosis.py --fixture \"{result['fixture']}\"", "```", "",
                  "参考数据位置及 SHA-256 保存在同目录 results.json 的 source_sha256 中，便于核对运行版本。", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="评测训练中心的首错诊断；使用固定参考答案，不写正式记录。")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=ROOT / "output/learning_evaluation")
    args = parser.parse_args()
    result = run_evaluation(args.fixture.resolve())
    write_report(result, args.output)
    summary = result["summary"]
    print(f"题目 {result['problem_count']}，作答 {summary['case_count']}，完整匹配 {summary['exact_match']['numerator']}。")
    print(f"报告：{args.output / 'report.md'}")
    return 0 if summary["exact_match"]["numerator"] == summary["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
