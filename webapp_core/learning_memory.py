from __future__ import annotations

from .learning_path import point_key


def learning_memories(learning, points, *, exclude_attempt=None):
    """Rebuild personal summaries from current evidence, including corrected grades."""
    allowed = {p["id"]: p for p in points if not p["guidance_blocked"]}
    attempts = [a for a in learning.store.list_attempts() if not a.get("assignment_id")]
    records = []
    for source in attempts:
        if source["id"] == exclude_attempt:
            continue
        key = point_key(learning._exercise(source))
        if key not in allowed:
            continue
        dialogue = source.get("dialogue") or {}
        steps = (source.get("walkthrough") or {}).get("steps", [])
        if not dialogue and not steps:
            continue
        started = min(
            [s["created_at"] for s in steps]
            + [dialogue.get("updated_at", source["updated_at"])]
        )
        followups = []
        for attempt in attempts:
            if point_key(learning._exercise(attempt)) != key:
                continue
            for index, submission in enumerate(attempt["submissions"]):
                if submission["created_at"] <= started:
                    continue
                independent = bool(
                    index == 0
                    and submission["unassisted"]
                    and attempt["exercise_id"] != source["exercise_id"]
                )
                followups.append(
                    {
                        "attempt_id": attempt["id"],
                        "exercise_id": attempt["exercise_id"],
                        "submission_number": index + 1,
                        "created_at": submission["created_at"],
                        "passed": bool(submission["evaluation"]["passed"]),
                        "independent_new_question": independent,
                        "error": (
                            submission["evaluation"].get("first_error") or {}
                        ).get("label"),
                    }
                )
        followups.sort(key=lambda s: s["created_at"])
        latest = followups[-1] if followups else None
        if not latest:
            status, basis = "pending", "互动后尚无作答核验，错因假设仍待验证。"
        elif not latest["passed"]:
            status, basis = (
                "needs_work",
                "后续同知识点作答仍有错误，需要继续订正；不能据此确认 AI 的错因猜测。",
            )
        elif latest["independent_new_question"]:
            status, basis = (
                "independent_evidence",
                "后续不同题目的首次独立作答通过，新增独立证据；尚不能确认具体错因或长期掌握。",
            )
        else:
            status, basis = (
                "assisted_evidence",
                "最近一次通过属于订正、辅导或原题作答，仍需独立复测；此前的独立证据保留在核验记录中。",
            )
        records.append(
            {
                "attempt_id": source["id"],
                "point_id": key,
                "title": allowed[key]["title"],
                "updated_at": max(
                    [source["updated_at"]] + [s["created_at"] for s in followups]
                ),
                "hypothesis": dialogue.get("hypothesis", ""),
                "answers": [
                    {"question": t["question"], "answer": t["answer"]}
                    for t in dialogue.get("turns", [])
                    if t.get("answer")
                ][-3:],
                "predictions": [
                    {"title": s["title"], "prediction": s["prediction"]}
                    for s in steps[-3:]
                ],
                "status": status,
                "basis": basis,
                "followups": followups[-4:],
            }
        )
    return sorted(
        records, key=lambda r: (r["updated_at"], r["attempt_id"]), reverse=True
    )[:12]
