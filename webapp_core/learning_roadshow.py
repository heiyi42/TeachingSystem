from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

from .learning_store import LearningStore
from .learning_service import LearningService
from .learning_plan import LearningPlanService
from .learning_walkthrough import LearningWalkthroughService
from .problem_tutoring_service import ProblemTutoringService


CASES = {
    "C_program": ("c_pointer_01", "c_pointer_02", "指针移动会不会改变数组元素？"),
    "operating_systems": ("lru_01", "lru_02", "命中以后最近使用次序会不会变化？"),
    "cybersec_lab": ("dh_01", "dh_02", "公开值计算在哪一步取模？"),
}


def answer_rows(service, attempt):
    solution = service._solve(attempt["exercise"])
    if attempt["exercise"]["kind"] == "page_replacement":
        return [
            {
                "frames": ", ".join(map(str, s["frames_after"])),
                "event": s["event"],
                "evicted": str(s["evicted"]) if s["evicted"] is not None else "—",
            }
            for s in solution["trace"]
        ]
    return [
        {"value": str(s["value"])}
        for s in solution.get("c_trace", solution.get("security_trace", []))
    ]


@lru_cache(maxsize=3)
def roadshow(subject):
    if subject not in CASES:
        raise ValueError("课程不存在")
    original_id, followup_id, prediction = CASES[subject]
    students = []
    with TemporaryDirectory(prefix="teaching-roadshow-") as folder:
        for person in ("A", "B"):
            learning = LearningService(
                LearningStore(Path(folder) / "demo.sqlite3", owner_id=person),
                ProblemTutoringService(),
            )
            original = learning.start(original_id)
            correct = answer_rows(learning, original)
            wrong = [{**r} for r in correct]
            if subject == "operating_systems":
                wrong[0]["event"] = "hit"
            else:
                wrong[0]["value"] = "999"
            learning.submit(original["id"], wrong)
            LearningWalkthroughService(learning).advance(
                original["id"], {"prediction": prediction}
            )
            learning.submit(original["id"], correct)
            followup = learning.start(followup_id)
            rows = answer_rows(learning, followup)
            if person == "B":
                if subject == "operating_systems":
                    rows[0]["event"] = "hit"
                else:
                    rows[0]["value"] = "999"
            result = learning.submit(followup["id"], rows)
            plan = LearningPlanService(learning).view(subject)
            students.append(
                {
                    "name": f"模拟学生 {person}",
                    "outcome": "新题首次独立通过" if person == "A" else "新题仍有错误",
                    "independent_pass": result["first_unassisted_pass"],
                    "memories": plan["memories"],
                    "timeline": list(reversed(plan["timeline"])),
                    "tasks": [
                        {
                            "title": t["point_title"],
                            "kind": t["kind"],
                            "reason": t.get("reason", ""),
                            "minutes": t["estimated_minutes"],
                        }
                        for t in plan["tasks"]
                    ],
                }
            )
    return {
        "subject_id": subject,
        "kind": "simulated_demo",
        "notice": "模拟作答，经真实评测与学习计划逻辑回放；预测文字为预设脚本，不调用 LLM。数据在临时库生成，不写入个人记录，也不是学习效果实验。",
        "students": students,
    }
