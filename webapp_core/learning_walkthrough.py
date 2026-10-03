from __future__ import annotations

import time

from .learning_service import LearningConflict


def walkthrough_steps(learning, state):
    exercise = learning._exercise(state)
    kind = exercise["kind"]
    evaluation = state.get("evaluation") or {}
    if kind in {"c_program", "security_lab"}:
        if not state["submissions"]:
            raise LearningConflict("请先提交运行，再查看实际执行反馈")
        if kind == "c_program":
            feedback = evaluation.get("program_feedback", {})
            steps = [
                {"title": f"测试用例 {item['number']}", "details": item}
                for item in feedback.get("tests", [])
            ]
            if not steps:
                steps = [
                    {
                        "title": "编译反馈",
                        "details": {
                            "compiler": feedback.get("compiler", "暂无运行反馈")
                        },
                    }
                ]
        else:
            environment = evaluation.get("lab_environment") or {}
            steps = [
                {
                    "title": "实验输入",
                    "details": {
                        "inputs": environment.get("inputs", {}),
                        "task": environment.get("task", ""),
                    },
                }
            ]
            steps.extend(
                {
                    "title": f"产物：{item['name']}",
                    "details": {k: v for k, v in item.items() if k != "base64"},
                }
                for item in environment.get("artifacts", [])
            )
            steps.append(
                {
                    "title": "实验核验结果",
                    "details": {
                        "passed": evaluation.get("passed", False),
                        "first_error": evaluation.get("first_error"),
                    },
                }
            )
        return steps
    if kind == "c_repair" or kind == "pv_design":
        raise ValueError("该题型请使用现有代码或方案核验")
    solution = learning._solve(exercise)
    if "c_trace" in solution:
        return [
            {"title": item["checkpoint"], "details": {"value": item["value"]}}
            for item in solution["c_trace"]
        ]
    if "security_trace" in solution:
        return [
            {
                "title": item["checkpoint"],
                "details": {
                    "value": item["value"],
                    "explanation": item.get("explanation", ""),
                },
            }
            for item in solution["security_trace"]
        ]
    if "trace" in solution:
        return [
            {
                "title": f"第 {i + 1} 次访问：页面 {item['page']}",
                "details": {
                    **{
                        key: item[key]
                        for key in (
                            "page",
                            "frames_after",
                            "event",
                            "evicted",
                            "next_uses",
                            "optimal_victims",
                        )
                        if item.get(key) is not None
                    },
                    "frames_before": (
                        solution["trace"][i - 1]["frames_after"] if i else []
                    ),
                },
            }
            for i, item in enumerate(solution["trace"])
        ]
    if "timeline" in solution:
        return [
            {"title": f"调度片段 {i + 1}", "details": item}
            for i, item in enumerate(solution["timeline"])
        ]
    if "rounds" in solution:
        return [
            {"title": f"安全序列步骤 {i + 1}", "details": item}
            for i, item in enumerate(solution["rounds"])
        ] or [{"title": "安全性检查", "details": {"safe": solution.get("safe", False)}}]
    raise ValueError("该题型暂无可展开的过程")


class LearningWalkthroughService:
    def __init__(self, learning):
        self.learning = learning

    def advance(self, attempt_id, data):
        revision = data.get("revision", 0)
        prediction = data.get("prediction", "")
        if type(revision) is not int or revision < 0:
            raise ValueError("过程版本无效")
        if not isinstance(prediction, str) or not 1 <= len(prediction.strip()) <= 2000:
            raise ValueError("请先填写预测或判断依据，也可以填写不确定")

        def action(state, existing):
            if state.get("assignment_id"):
                raise LearningConflict("过程演示仅用于课外训练")
            history = state.get("walkthrough") or {
                "revision": 0,
                "steps": [],
                "submission_count": len(state["submissions"]),
            }
            if history["revision"] != revision:
                raise LearningConflict("过程已更新，请刷新练习后继续")
            # Runtime output belongs to a specific submission; never mix different runs.
            if self.learning._exercise(state)["kind"] in {
                "c_program",
                "security_lab",
            } and history["submission_count"] != len(state["submissions"]):
                history = {
                    "revision": revision,
                    "steps": [],
                    "submission_count": len(state["submissions"]),
                }
            steps = walkthrough_steps(self.learning, state)
            index = len(history["steps"])
            if index >= len(steps):
                raise LearningConflict("本次过程已全部展开")
            history["steps"].append(
                {
                    **steps[index],
                    "prediction": prediction.strip(),
                    "created_at": time.time(),
                }
            )
            history["revision"] += 1
            history["total"] = len(steps)
            state["walkthrough"] = history
            state["tutoring_viewed"] = True
            return "process_prediction", {
                "step": index + 1,
                "prediction": prediction.strip(),
            }

        return self.learning._public(
            self.learning.store.update(
                attempt_id, action, guard=self.learning._guard("tutoring")
            )
        )
