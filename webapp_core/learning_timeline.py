from __future__ import annotations

import json
from contextlib import closing

from .learning_path import point_key


LABELS = {
    "started": "开始练习",
    "submitted": "提交核验",
    "hint_viewed": "查看提示",
    "solution_viewed": "查看解析",
    "interactive_diagnosis": "互动诊断",
    "process_prediction": "过程预测",
    "grading_review_requested": "提出评测异议",
    "grading_review_resolved": "评测复核完成",
    "personal_guidance_viewed": "查看个性化讲解",
}


def evidence_timeline(learning, points):
    allowed = {p["id"] for p in points if not p["guidance_blocked"]}
    attempts = {
        a["id"]: a
        for a in learning.store.list_attempts()
        if not a.get("assignment_id") and point_key(learning._exercise(a)) in allowed
    }
    events = []
    with closing(learning.store._connect()) as connection:
        for event_id, attempt_id, kind, created_at, raw in connection.execute(
            "SELECT id,attempt_id,kind,created_at,data FROM learning_events ORDER BY id DESC"
        ):
            if attempt_id not in attempts or kind not in LABELS:
                continue
            state = attempts[attempt_id]
            data = json.loads(raw)
            detail = ""
            if kind == "process_prediction":
                detail = (
                    f"第 {data.get('step', '')} 步预测：{data.get('prediction', '')}"
                )
            elif kind == "interactive_diagnosis":
                detail = {
                    "start": "开始追问，错因假设待验证",
                    "answer": "记录回答并继续诊断",
                    "verify": "返回练习核验",
                    "exit": "结束本次诊断",
                }.get(data.get("action"), "互动记录已保存")
            elif kind == "submitted":
                index = next(
                    (
                        i
                        for i, submission in enumerate(state["submissions"])
                        if submission["created_at"] == data.get("created_at")
                    ),
                    -1,
                )
                independent = index == 0 and data.get("unassisted", False)
                result = (
                    "通过" if (data.get("evaluation") or {}).get("passed") else "未通过"
                )
                detail = f"当次记录：{result}；{'首次独立作答' if independent else '辅导、订正或重复作答'}。如有复核，以原练习当前判定为准。"
            elif kind == "grading_review_resolved":
                detail = "判定已复核，当前学习状态以修订后的结果计算"
            events.append(
                {
                    "id": event_id,
                    "attempt_id": attempt_id,
                    "title": learning._exercise(state)["title"],
                    "kind": kind,
                    "label": LABELS[kind],
                    "created_at": created_at,
                    "detail": detail,
                }
            )
            if len(events) == 80:
                break
    return events
