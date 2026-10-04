from __future__ import annotations

DAY = 86400


def summarize_state(state):
    return {
        key: state[key]
        for key in (
            "code",
            "status",
            "independent_count",
            "pending_review_count",
            "verified_review_count",
        )
    }


def recommendation_feedback(record, point, recommendations=()):
    state = point["learning_state"]
    before = record["before"]
    counts = record["submission_counts"]
    target_id = record.get("attempt_id")
    new_submissions = [
        (e["attempt_id"], s)
        for e in state["evidence"]
        for s in e["submissions"][counts.get(e["attempt_id"], 0) :]
        if s["created_at"] >= record["created_at"]
    ]
    target_submissions = [(aid, s) for aid, s in new_submissions if aid == target_id]
    completed_at = record.get("completed_at")
    if record["action"]["kind"] in {"practice", "review", "continue"}:
        completed_at = next(
            (
                s["created_at"]
                for _, s in target_submissions
                if s["outcome"] != "incorrect"
            ),
            None,
        )
    # 阅读或讲解之后的作答才用于效果观察；点击、草稿和旧提交都不算新证据。
    observations = [
        (aid, s)
        for aid, s in new_submissions
        if aid == target_id
        or (completed_at is not None and s["created_at"] >= completed_at)
    ]
    observations.sort(
        key=lambda item: (item[1]["created_at"], item[0], item[1]["number"])
    )
    independent = list(
        dict.fromkeys(
            aid for aid, s in observations if s["outcome"] == "independent_pass"
        )
    )
    evidence = {e["attempt_id"]: e for e in state["evidence"]}
    baseline = [
        (e["attempt_id"], e["submissions"][0])
        for e in state["evidence"]
        if counts.get(e["attempt_id"], 0) and e["submissions"][0]["independent"]
    ]
    independent_checks = [(aid, s) for aid, s in observations if s["independent"]]
    # Starting a question before the interval and submitting it later is not spaced validation.
    target_exercise = evidence.get(target_id, {}).get("exercise_id")
    delayed = [
        (aid, s)
        for aid, s in independent_checks
        if completed_at is not None
        and evidence[aid]["created_at"] >= completed_at + DAY
        and evidence[aid]["exercise_id"] != target_exercise
    ]
    retention = "awaiting_independent"
    retention_label = "尚无新题独立通过，先完成独立验证"
    if independent:
        retention, retention_label = (
            "awaiting_delayed",
            "已有即时证据，待至少一天后的新题核验",
        )
    if delayed:
        retention = (
            "observed"
            if delayed[-1][1]["outcome"] == "independent_pass"
            else "needs_work"
        )
        retention_label = (
            "至少一天后新题首次独立通过"
            if retention == "observed"
            else "间隔后的新题仍有错误，需继续巩固"
        )
    if observations and observations[-1][1]["outcome"] == "incorrect":
        retention, retention_label = (
            "needs_work",
            "最近作答仍有错误，已有证据不能代替本次订正",
        )
    validation = {
        "baseline": {
            "attempts": len(baseline),
            "passed": sum(s["outcome"] == "independent_pass" for _, s in baseline),
        },
        "followup": {"attempts": len(independent_checks), "passed": len(independent)},
        "retention": retention,
        "retention_label": retention_label,
        "delayed_evidence_ids": [aid for aid, _ in delayed],
        "other_recommendations": sum(
            r["point_id"] == record["point_id"]
            and r["id"] != record["id"]
            and r["created_at"] >= record["created_at"]
            for r in recommendations
        ),
        "checks": [
            {
                "attempt_id": aid,
                "submission_number": s["number"],
                "created_at": s["created_at"],
                "independent": s["independent"],
                "passed": s["outcome"] != "incorrect",
                "delayed": any(
                    aid == delayed_id and s["number"] == ds["number"]
                    for delayed_id, ds in delayed
                ),
            }
            for aid, s in observations
        ],
        "notice": "按当前有效判定重算。仅比较同知识点的已记录作答，题目难度可能不同；不据此认定推荐带来因果提升或长期掌握。",
    }
    repeated = sorted(
        {
            s["error"]["label"]
            for _, s in observations
            if s["error"] and s["error"]["error_code"] in record["error_codes"]
        }
    )
    if observations and observations[-1][1]["outcome"] == "incorrect":
        outcome, label = "needs_work", "仍需巩固"
        reason = "后续作答仍有错误，先订正并核对相关概念。"
    elif independent:
        outcome, label = "independent_evidence", "新增独立证据"
        reason = f"推荐后有 {len(independent)} 次新题首次独立通过；继续用间隔复测判断能否保持。"
    elif observations and observations[-1][1]["outcome"] != "incorrect":
        outcome, label = "assisted", "完成后仍待独立验证"
        reason = "后续通过涉及订正、辅助或重复练习，还需要未练新题独立验证。"
    elif completed_at is not None:
        outcome, label = "awaiting_validation", "任务完成，待作答验证"
        reason = "尚无完成后的新作答；阅读和 AI 讲解本身不计入独立能力证据。"
    else:
        outcome, label = "pending", "任务待完成"
        reason = "尚未完成推荐任务，不判断推荐效果。"
    return {
        "id": record["id"],
        "action": record["action"],
        "created_at": record["created_at"],
        "completed_at": completed_at,
        "attempt_id": target_id,
        "before": before,
        "after": summarize_state(state),
        "outcome": outcome,
        "label": label,
        "reason": reason,
        "repeated_errors": repeated,
        "evidence_ids": list(dict.fromkeys(aid for aid, _ in observations)),
        "validation": validation,
    }
