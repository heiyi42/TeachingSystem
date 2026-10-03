from __future__ import annotations

import copy
import json
import time
from uuid import uuid4
from .learning_service import LearningConflict


class GradingReviewService:
    def __init__(self, learning):
        self.learning = learning

    def request(self, attempt_id, number, reason):
        if type(number) is not int or number < 1:
            raise ValueError("请选择提交次数")
        if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 2000:
            raise ValueError("请填写5至2000字的异议理由")

        def action(state, _):
            if number > len(state["submissions"]):
                raise ValueError("提交记录不存在")
            if (
                state.get("assignment_id")
                and self.learning.tasks
                and self.learning.tasks.policy(state)["feedback_hidden"]
            ):
                raise LearningConflict("测验反馈公布后才能申请复核")
            reviews = state.setdefault("grading_reviews", [])
            if any(r["submission_number"] == number for r in reviews):
                raise LearningConflict("该次提交已申请复核，请查看处理结果")
            record = {
                "id": uuid4().hex,
                "submission_number": number,
                "reason": reason.strip(),
                "status": "pending",
                "created_at": time.time(),
            }
            reviews.append(record)
            return "grading_review_requested", record

        state = self.learning.store.update(attempt_id, action)
        return self.learning._public(state)

    def resolve(self, attempt_id, review_id, decision, note, reviewer):
        if decision not in {"uphold", "pass", "fail"}:
            raise ValueError("请选择维持原判、改判通过或改判未通过")
        if not isinstance(note, str) or not 5 <= len(note.strip()) <= 3000:
            raise ValueError("请填写5至3000字的复核依据")

        def action(state, _):
            record = next(
                (r for r in state.get("grading_reviews", []) if r["id"] == review_id),
                None,
            )
            if not record:
                raise LookupError("复核记录不存在")
            if record["status"] != "pending":
                raise LearningConflict("该复核已处理，请刷新查看结果")
            submission = state["submissions"][record["submission_number"] - 1]
            original = copy.deepcopy(submission["evaluation"])
            if decision != "uphold":
                submission.setdefault("original_evaluation", original)
                passed = decision == "pass"
                error = (
                    None
                    if passed
                    else {
                        "step": 1,
                        "error_code": "grading_review",
                        "field": "submission",
                        "possible_cause": None,
                        "label": "人工复核未通过",
                        "message": note.strip(),
                    }
                )
                submission["evaluation"] = {
                    **original,
                    "passed": passed,
                    "first_error": error,
                    "row_statuses": ["correct" if passed else "pending"]
                    * len(submission["rows"]),
                    "manual_review": True,
                }
                latest = state["submissions"][-1]["evaluation"]
                state["evaluation"] = latest
                state["status"] = "passed" if latest["passed"] else "needs_correction"
                state["first_error"] = next(
                    (
                        s["evaluation"]["first_error"]
                        for s in state["submissions"]
                        if s["evaluation"]["first_error"]
                    ),
                    None,
                )
            record.update(
                status="resolved",
                decision=decision,
                note=note.strip(),
                reviewer_id=reviewer["id"],
                reviewer_name=reviewer["name"],
                resolved_at=time.time(),
                original_evaluation=original,
                revised_evaluation=copy.deepcopy(submission["evaluation"]),
            )
            return "grading_review_resolved", copy.deepcopy(record)

        state = self.learning.store.update(attempt_id, action, after=self.sync_task)
        return self.learning._public(state)

    @staticmethod
    def sync_task(state, connection):
        task_id = state.get("assignment_id")
        if not task_id:
            return
        row = connection.execute(
            "SELECT data FROM task_submissions WHERE task_id=? AND user_id=?",
            (task_id, state["owner_id"]),
        ).fetchone()
        if not row:
            return
        data = json.loads(row[0])
        if data["status"] != "finalized":
            return
        data["attempts"] = [
            copy.deepcopy(state) if a["id"] == state["id"] else a
            for a in data["attempts"]
        ]
        task = json.loads(
            connection.execute(
                "SELECT data FROM school_tasks WHERE id=?", (task_id,)
            ).fetchone()[0]
        )
        old = data["training_score"]
        data["training_score"] = (
            round(
                100
                * sum(a["status"] == "passed" for a in data["attempts"])
                / len(task["exercises"]),
                2,
            )
            if task["exercises"]
            else None
        )
        if task["kind"] != "experiment":
            data["score"] = data["training_score"]
        data.setdefault("grading_adjustments", []).append(
            {
                "attempt_id": state["id"],
                "before": old,
                "after": data["training_score"],
                "created_at": time.time(),
            }
        )
        connection.execute(
            "UPDATE task_submissions SET data=? WHERE task_id=? AND user_id=?",
            (json.dumps(data, ensure_ascii=False), task_id, state["owner_id"]),
        )
