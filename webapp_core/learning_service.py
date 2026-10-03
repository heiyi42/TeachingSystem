from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any
from uuid import uuid4

from .learning_exercises import EXERCISES
from .learning_c import solve_c, check_c_return
from .learning_banker import evaluate_banker
from .learning_program import evaluate_program, program_solution
from .learning_security_lab import evaluate_lab, lab_solution
from .learning_extended import EXTENDED_KINDS, solve_extended, evaluate_extended
from .learning_security import (
    SECURITY_KINDS,
    solve_security,
    evaluate_security,
    security_hint,
)
from .learning_courses import COURSES, course_chapters
from .learning_store import LearningStore
from .problem_tutoring_service import ProblemTutoringService
from .learning_pv import PV_KINDS, solve_pv, evaluate_pv

CHECKPOINT_KINDS = SECURITY_KINDS | EXTENDED_KINDS | PV_KINDS

ERROR_LABELS = {
    "grading_review": "人工复核未通过",
    "lab_artifact": "实验产物不符",
    "wrong_process_states": "进程状态转换错误",
    "wrong_thread_resources": "线程资源归属错误",
    "wrong_file_allocation": "文件定位或读取次数错误",
    "compile_error": "编译失败",
    "wrong_output": "输出不符",
    "runtime_error": "运行异常",
    "time_limit": "运行超时",
    "wrong_readers_writers": "读者写者准入或排队错误",
    "wrong_pv": "PV 状态或操作错误",
    "pv_deadlock": "存在死锁交错",
    "pv_safety": "存在并发安全冲突",
    "wrong_resource_request": "资源请求判断错误",
    "wrong_schedule_metrics": "调度时间指标错误",
    "wrong_disk_schedule": "磁盘调度错误",
    "wrong_clock_trace": "CLOCK 状态错误",
    "wrong_address_translation": "地址转换错误",
    "wrong_auth_flow": "认证流程判断错误",
    "wrong_signature_check": "签名验证错误",
    "wrong_event": "命中判断错误",
    "wrong_victim": "淘汰页面错误",
    "wrong_frames": "页框状态错误",
    "wrong_order": "调度顺序错误",
    "wrong_time": "时间计算错误",
    "incomplete": "作答未完成",
    "extra_segment": "多余执行段",
    "wrong_value": "变量或输出错误",
    "wrong_code": "返回值核验失败",
    "invalid_code": "返回语句格式错误",
    "wrong_need": "Need 计算错误",
    "ineligible_process": "进程尚不满足需求",
    "repeated_process": "重复完成进程",
    "wrong_work": "Work 更新错误",
    "wrong_safety": "安全性结论错误",
    "wrong_public": "DH 公开值错误",
    "wrong_shared": "DH 共享值错误",
    "wrong_permission": "权限判断错误",
    "wrong_evidence": "日志证据筛选错误",
    "wrong_conclusion": "日志时序结论错误",
}


class LearningConflict(ValueError):
    pass


class LearningService:
    def __init__(
        self,
        store: LearningStore,
        solver: ProblemTutoringService,
        *,
        catalog=None,
        class_id=None,
        tasks=None,
        assignment_kind=None,
    ) -> None:
        self.store = store
        self.solver = solver
        self.catalog = EXERCISES if catalog is None else catalog
        self.class_id = class_id
        self.tasks = tasks
        self.assignment_kind = assignment_kind

    def _exercise(self, state):
        return state.get("exercise_snapshot") or EXERCISES[state["exercise_id"]]

    def courses(self) -> list[dict[str, Any]]:
        result = []
        for subject_id, course in COURSES.items():
            chapters = course_chapters(subject_id)
            for chapter in chapters:
                chapter["exercise_count"] = sum(
                    exercise["subject_id"] == subject_id
                    and exercise["chapter_id"] == chapter["id"]
                    for exercise in self.catalog.values()
                )
            result.append(
                {
                    "id": subject_id,
                    "name": course["name"],
                    "chapters": chapters,
                    "capabilities": course["capabilities"],
                    "planned": course["planned"],
                    "exercise_count": sum(
                        chapter["exercise_count"] for chapter in chapters
                    ),
                }
            )
        return result

    def exercises(self) -> list[dict[str, Any]]:
        return list(self.catalog.values())

    def _solve(
        self, exercise: dict[str, Any], *, opt_evictions: list[int | None] | None = None
    ) -> dict[str, Any]:
        parameters = exercise["parameters"]
        if exercise["kind"] == "security_lab":
            return lab_solution(exercise)
        if exercise["kind"] == "c_program":
            return program_solution(exercise)
        if exercise["kind"] in PV_KINDS:
            return solve_pv(exercise)
        if exercise["kind"] in EXTENDED_KINDS:
            return solve_extended(exercise, self.solver)
        if exercise["kind"] in CHECKPOINT_KINDS:
            return solve_security(exercise, self.solver)
        if exercise["kind"] in {"c_trace", "c_repair"}:
            return solve_c(exercise)
        if exercise["kind"] == "banker":
            processes = parameters["banker_processes"]
            return self.solver.solve_banker(
                parameters["available"],
                {p["name"]: p["allocation"] for p in processes},
                {p["name"]: p["maximum"] for p in processes},
            )
        if exercise["kind"] == "page_replacement":
            result = self.solver.solve_page_replacement(
                exercise["algorithm"],
                parameters["sequence"],
                parameters["frames"],
                evictions=opt_evictions,
            )
        else:
            processes = [
                (p["name"], p["arrival"], p["service"]) for p in parameters["processes"]
            ]
            result = self.solver.solve_cpu_scheduling(
                exercise["algorithm"], processes, parameters["quantum"]
            )
        if result["status"] != "success":
            raise RuntimeError(result["message"])
        return result["result"]

    def _recommendation(
        self, state: dict[str, Any], existing: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        seen = {item["exercise_id"] for item in existing}
        exercise = self._exercise(state)
        error_code = (state["first_error"] or {}).get("error_code")
        candidates = [
            item
            for item in self.catalog.values()
            if item["subject_id"] == exercise["subject_id"]
            and item["kind"] == exercise["kind"]
            and item.get("family_id", item["algorithm"])
            == exercise.get("family_id", exercise["algorithm"])
            and item["id"] not in seen
        ]
        candidates.sort(
            key=lambda item: (
                error_code not in item["training_tags"],
                item["difficulty"] != exercise["difficulty"],
                item["id"],
            )
        )
        if not candidates:
            return None
        target = candidates[0]
        focus = ERROR_LABELS.get(error_code)
        reason = (
            f"更换题目参数，检查{focus}是否仍然出现。"
            if focus
            else "更换题目参数，检查能否独立应用相同的解题规则。"
        )
        return {"exercise_id": target["id"], "title": target["title"], "reason": reason}

    def start(
        self,
        exercise_id: str | None,
        parent_id: str | None = None,
        *,
        assignment_id=None,
        review_id=None,
        recommendation_id=None,
        diagnostic_key=None,
    ) -> dict[str, Any]:
        if exercise_id is not None and not isinstance(exercise_id, str):
            raise ValueError("题目编号格式错误")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError("原练习编号格式错误")

        def factory(existing: list[dict[str, Any]]) -> dict[str, Any]:
            selected_id = exercise_id
            if diagnostic_key:
                previous = next(
                    (a for a in existing if a.get("diagnostic_key") == diagnostic_key),
                    None,
                )
                if previous:
                    return previous
            if review_id:
                from .learning_path import LearningPathService

                _, previous, selected_id = LearningPathService(self).review_target(
                    review_id, existing
                )
                if previous:
                    return previous
            if assignment_id:
                previous = next(
                    (
                        a
                        for a in existing
                        if a.get("assignment_id") == assignment_id
                        and a["exercise_id"] == exercise_id
                    ),
                    None,
                )
                if previous:
                    return previous
            if parent_id:
                parent = next(
                    (item for item in existing if item["id"] == parent_id), None
                )
                if parent is None:
                    raise LookupError("原练习不存在")
                if parent.get("assignment_id"):
                    raise LearningConflict("班级任务不支持通过复测接口另开作答")
                if parent["status"] != "passed":
                    raise LearningConflict("请先完成原题订正，再开始新题复测")
                recommendation = self._recommendation(parent, existing)
                if recommendation is None:
                    raise LearningConflict("该题型暂时没有尚未练习的新题")
                selected_id = recommendation["exercise_id"]
            if selected_id not in self.catalog:
                raise ValueError("请选择有效的练习题")
            exercise = self.catalog[selected_id]
            if exercise["kind"] in CHECKPOINT_KINDS:
                draft = [
                    {"value": ""} for _ in exercise["parameters"]["security_checks"]
                ]
            elif exercise["kind"] == "c_trace":
                draft = [{"value": ""} for _ in exercise["parameters"]["checkpoints"]]
            elif exercise["kind"] in {"c_repair", "c_program", "security_lab"}:
                draft = [
                    {"code": code}
                    for code in exercise["parameters"].get(
                        "starter_files", [exercise["parameters"]["starter"]]
                    )
                ]
            elif exercise["kind"] == "page_replacement":
                draft = [
                    {"frames": "", "event": "", "evicted": ""}
                    for _ in exercise["parameters"]["sequence"]
                ]
            elif exercise["kind"] == "banker":
                draft = [
                    {"need": "", "process": "", "work": "", "verdict": ""}
                    for _ in range(
                        2 * len(exercise["parameters"]["banker_processes"]) + 1
                    )
                ]
            else:
                draft = [{"process": "", "start": "", "end": ""}]
            now = time.time()
            return {
                "id": uuid4().hex,
                "exercise_id": selected_id,
                "parent_id": parent_id,
                "review_id": review_id,
                "diagnostic_key": diagnostic_key,
                "assignment_id": assignment_id,
                "previously_seen": any(
                    item["exercise_id"] == selected_id for item in existing
                ),
                "exercise_snapshot": exercise,
                "content_version": exercise.get("content_version", "development"),
                "grading_version": exercise.get("grading_version", "rules-v1"),
                "class_id": self.class_id,
                "assignment_kind": self.assignment_kind,
                "created_at": now,
                "updated_at": now,
                "status": "in_progress",
                "draft": draft,
                "submissions": [],
                "evaluation": None,
                "first_error": None,
                "hint_count": 0,
                "hint": None,
                "hint_levels": {},
                "solution_viewed": False,
            }

        return self._public(
            self.store.create(
                factory, guard=self._guard("start"), recommendation_id=recommendation_id
            )
        )

    def get(self, attempt_id: str) -> dict[str, Any]:
        state = self.store.get(attempt_id)
        if self.tasks and state.get("assignment_id"):
            self.tasks.archive_task(state["assignment_id"])
            state = self.store.get(attempt_id)
        return self._public(state)

    @staticmethod
    def _draft(exercise: dict[str, Any], rows: Any) -> list[dict[str, str]]:
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError("作答必须包含 1 至 100 行")
        page = exercise["kind"] == "page_replacement"
        if page and len(rows) != len(exercise["parameters"]["sequence"]):
            raise ValueError("页面置换的作答行数须与访问序列一致")
        fields = (
            ("value",)
            if exercise["kind"] in CHECKPOINT_KINDS
            else {
                "page_replacement": ("frames", "event", "evicted"),
                "cpu_scheduling": ("process", "start", "end"),
                "c_trace": ("value",),
                "c_repair": ("code",),
                "c_program": ("code",),
                "security_lab": ("code",),
                "banker": ("need", "process", "work", "verdict"),
            }[exercise["kind"]]
        )
        if exercise["kind"] in CHECKPOINT_KINDS and len(rows) != len(
            exercise["parameters"]["security_checks"]
        ):
            raise ValueError("作答行数须与题目检查点一致")
        if (
            exercise["kind"] == "banker"
            and len(rows) != 2 * len(exercise["parameters"]["banker_processes"]) + 1
        ):
            raise ValueError("银行家算法作答须包含全部 Need 行、过程行与结论行")
        if exercise["kind"] in {
            "c_trace",
            "c_repair",
            "c_program",
            "security_lab",
        } and len(rows) != len(exercise["parameters"]["checkpoints"]):
            raise ValueError("作答行数须与题目检查点一致")
        result = []
        for row in rows:
            if not isinstance(row, dict) or any(
                not isinstance(row.get(field), str)
                or len(row[field])
                > (12000 if exercise["kind"] in {"c_program", "security_lab"} else 120)
                for field in fields
            ):
                raise ValueError(
                    "作答字段超出长度限制，完整程序最多 12000 字符，其他字段最多 120 字符"
                )
            if page and row["event"] not in {"", "hit", "fault"}:
                raise ValueError("命中状态只能选择命中或缺页")
            if exercise["kind"] == "banker":
                count = len(exercise["parameters"]["banker_processes"])
                index = len(result)
                active = (
                    {"need"}
                    if index < count
                    else {"process", "work"} if index < count * 2 else {"verdict"}
                )
                if any(row[field].strip() for field in set(fields) - active):
                    raise ValueError("请仅填写该行对应的作答字段")
                if row["verdict"] not in {"", "safe", "unsafe"}:
                    raise ValueError("安全性结论只能选择安全或不安全")
            result.append({field: row[field].strip() for field in fields})
        return result

    def _guard(self, operation):
        return (
            (lambda state, connection: self.tasks.guard(state, connection, operation))
            if self.tasks
            else None
        )

    def _editable(self, state: dict[str, Any]) -> None:
        if state["status"] == "passed" and not (
            state.get("assignment_id") and state.get("assignment_kind") == "quiz"
        ):
            raise LearningConflict("这次练习已通过，请开始新题")

    def save_draft(self, attempt_id: str, rows: Any) -> dict[str, Any]:
        def action(
            state: dict[str, Any], _existing: list[dict[str, Any]]
        ) -> tuple[str, dict[str, Any]]:
            self._editable(state)
            state["draft"] = self._draft(self._exercise(state), rows)
            return "draft_saved", {}

        return self._public(
            self.store.update(attempt_id, action, guard=self._guard("draft"))
        )

    @staticmethod
    def _integer(value: str, label: str) -> int | None:
        if value == "":
            return None
        if not re.fullmatch(r"\d{1,8}", value):
            raise ValueError(f"{label}须填写非负整数")
        return int(value)

    def _normalize(
        self, exercise: dict[str, Any], rows: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        result = []
        for index, row in enumerate(rows, 1):
            label = f"第 {index} 行"
            if exercise["kind"] == "page_replacement":
                parts = re.split(r"[,，\s]+", row["frames"]) if row["frames"] else []
                frames = [self._integer(part, f"{label}页框") for part in parts if part]
                if len(frames) > exercise["parameters"]["frames"] or len(
                    set(frames)
                ) != len(frames):
                    raise ValueError(f"{label}页框数量超出限制或包含重复页面")
                victim = row["evicted"]
                if victim in {"", "—", "-", "无"}:
                    victim_value = None
                else:
                    victim_value = self._integer(victim, f"{label}淘汰页")
                result.append(
                    {"frames": frames, "event": row["event"], "evicted": victim_value}
                )
            else:
                process = row["process"].upper()
                names = {p["name"] for p in exercise["parameters"]["processes"]}
                if process and process not in names:
                    raise ValueError(f"{label}请选择题目中的进程")
                start = self._integer(row["start"], f"{label}开始时间")
                end = self._integer(row["end"], f"{label}结束时间")
                if start is not None and end is not None and end <= start:
                    raise ValueError(f"{label}结束时间须大于开始时间")
                result.append({"process": process, "start": start, "end": end})
        return result

    def _evaluate(
        self, exercise: dict[str, Any], rows: list[dict[str, str]]
    ) -> dict[str, Any]:
        if exercise["kind"] == "security_lab":
            return evaluate_lab(exercise, rows[0]["code"], ERROR_LABELS)
        if exercise["kind"] == "c_program":
            return evaluate_program(exercise, rows, ERROR_LABELS)
        if exercise["kind"] in PV_KINDS:
            return evaluate_pv(exercise, rows, ERROR_LABELS)
        if exercise["kind"] in EXTENDED_KINDS:
            return evaluate_extended(exercise, rows, ERROR_LABELS, self.solver)
        if exercise["kind"] in SECURITY_KINDS:
            return evaluate_security(
                exercise, rows, self._solve(exercise), ERROR_LABELS
            )
        if exercise["kind"] == "banker":
            result = evaluate_banker(exercise, rows, self._solve(exercise))
            if result["first_error"]:
                result["first_error"]["label"] = ERROR_LABELS[
                    result["first_error"]["error_code"]
                ]
            return result
        if exercise["kind"] in {"c_trace", "c_repair"}:
            first_error = None
            if exercise["kind"] == "c_repair":
                code, message = (
                    check_c_return(rows[0]["code"], *exercise["parameters"]["values"])
                    if rows[0]["code"]
                    else ("incomplete", "请填写返回语句。")
                )
                if code:
                    first_error = {
                        "step": 1,
                        "field": "code",
                        "error_code": code,
                        "label": ERROR_LABELS[code],
                        "message": message,
                        "possible_cause": None,
                    }
            else:
                for index, (actual, expected) in enumerate(
                    zip(rows, self._solve(exercise)["c_trace"]), 1
                ):
                    value = actual["value"]
                    if value and not re.fullmatch(r"[+-]?\d{1,8}", value):
                        raise ValueError(f"第 {index} 个检查点须填写整数")
                    code = (
                        "incomplete"
                        if not value
                        else "wrong_value" if int(value) != expected["value"] else None
                    )
                    if code and first_error is None:
                        first_error = {
                            "step": index,
                            "field": "value",
                            "error_code": code,
                            "label": ERROR_LABELS[code],
                            "message": (
                                "请补全当前检查点。"
                                if not value
                                else f"检查点“{expected['checkpoint']}”的值不符，请按语句顺序重新跟踪。"
                            ),
                            "possible_cause": None,
                        }
            step = first_error["step"] if first_error else None
            return {
                "passed": first_error is None,
                "first_error": first_error,
                "row_statuses": [
                    (
                        "correct"
                        if step is None or i < step
                        else "error" if i == step else "pending"
                    )
                    for i in range(1, len(rows) + 1)
                ],
            }
        answers = self._normalize(exercise, rows)
        solution = self._solve(
            exercise,
            opt_evictions=(
                [row["evicted"] for row in answers]
                if exercise["algorithm"] == "OPT"
                else None
            ),
        )
        page = exercise["kind"] == "page_replacement"
        standard = solution["trace" if page else "timeline"]
        first_error = None
        for index in range(max(len(standard), len(answers))):
            code = field = ""
            if index >= len(answers):
                code, field = "incomplete", "process"
            elif index >= len(standard):
                code, field = "extra_segment", "process"
            else:
                actual, expected = answers[index], standard[index]
                if page:
                    if not actual["frames"] or not actual["event"]:
                        code, field = "incomplete", (
                            "frames" if not actual["frames"] else "event"
                        )
                    elif actual["event"] != expected["event"]:
                        code, field = "wrong_event", "event"
                    elif actual["evicted"] != expected["evicted"]:
                        code, field = "wrong_victim", "evicted"
                    elif set(actual["frames"]) != set(expected["frames_after"]):
                        code, field = "wrong_frames", "frames"
                else:
                    if (
                        not actual["process"]
                        or actual["start"] is None
                        or actual["end"] is None
                    ):
                        code, field = "incomplete", (
                            "process"
                            if not actual["process"]
                            else "start" if actual["start"] is None else "end"
                        )
                    elif actual["process"] != expected["process"]:
                        code, field = "wrong_order", "process"
                    elif (
                        actual["start"] != expected["start"]
                        or actual["end"] != expected["end"]
                    ):
                        code, field = "wrong_time", (
                            "start" if actual["start"] != expected["start"] else "end"
                        )
            if code:
                cause = None
                if code == "wrong_victim" and exercise["algorithm"] == "LRU":
                    parameters = exercise["parameters"]
                    fifo = self.solver.solve_page_replacement(
                        "FIFO", parameters["sequence"], parameters["frames"]
                    )["result"]["trace"]
                    if answers[index]["evicted"] == fifo[index]["evicted"]:
                        cause = "可能混淆了 FIFO 与 LRU，需结合后续作答确认。"
                messages = {
                    "wrong_event": "当前访问的命中或缺页判断与规则轨迹不符。",
                    "wrong_victim": "淘汰页面与算法规则不符，请检查此前的访问记录。",
                    "wrong_frames": "页框中的页面集合与规则轨迹不符，排列顺序不会影响判定。",
                    "wrong_order": "当前执行进程与调度规则不符，请检查就绪队列。",
                    "wrong_time": "执行段的开始或结束时间与调度规则不符。",
                    "incomplete": "请补全当前步骤或缺少的执行段，再继续核验。",
                    "extra_segment": "全部进程已经完成，当前执行段多余。",
                }
                first_error = {
                    "step": index + 1,
                    "field": field,
                    "error_code": code,
                    "label": ERROR_LABELS[code],
                    "message": messages[code],
                    "possible_cause": cause,
                }
                break
        step = first_error["step"] if first_error else None
        return {
            "passed": first_error is None,
            "first_error": first_error,
            "row_statuses": [
                (
                    "correct"
                    if step is None or i < step
                    else "error" if i == step else "pending"
                )
                for i in range(1, len(rows) + 1)
            ],
        }

    def _apply_submission(self, state, existing, rows, *, allow_invalid=False):
        exercise = self._exercise(state)
        draft = self._draft(exercise, rows)
        try:
            evaluation = self._evaluate(exercise, draft)
        except ValueError:
            if not allow_invalid:
                raise
            evaluation = {
                "passed": False,
                "first_error": {
                    "step": 1,
                    "field": "value",
                    "error_code": "incomplete",
                    "label": "作答格式错误",
                    "message": "保存的作答格式不符合题目要求，未通过核验。",
                    "possible_cause": None,
                },
                "row_statuses": ["error"] + ["pending"] * (len(draft) - 1),
            }
        prior_work = any(
            other["id"] != state["id"]
            and other["exercise_id"] == state["exercise_id"]
            and (
                other["submissions"]
                or other["hint_count"]
                or other["solution_viewed"]
                or other.get("tutoring_viewed")
            )
            for other in existing
        )
        independent = not (
            state["hint_count"]
            or state["solution_viewed"]
            or state["previously_seen"]
            or prior_work
            or state.get("tutoring_viewed", False)
        )
        submission = {
            "rows": draft,
            "evaluation": evaluation,
            "created_at": time.time(),
            "unassisted": independent,
            "assistance": {
                "hint_count": state["hint_count"],
                "solution_viewed": state["solution_viewed"],
                "tutoring_viewed": state.get("tutoring_viewed", False),
                "previously_seen": bool(state["previously_seen"] or prior_work),
            },
        }
        state["draft"] = draft
        state["evaluation"] = evaluation
        state["submissions"].append(submission)
        if evaluation["first_error"] and state["first_error"] is None:
            state["first_error"] = evaluation["first_error"]
        state["status"] = "passed" if evaluation["passed"] else "needs_correction"
        return "submitted", submission

    def submit(self, attempt_id: str, rows: Any) -> dict[str, Any]:
        def action(state, existing):
            self._editable(state)
            return self._apply_submission(state, existing, rows)

        return self._public(
            self.store.update(attempt_id, action, guard=self._guard("submit"))
        )

    def hint(self, attempt_id: str, requested_step: Any = None) -> dict[str, Any]:
        def action(
            state: dict[str, Any], _existing: list[dict[str, Any]]
        ) -> tuple[str, dict[str, Any]]:
            self._editable(state)
            exercise = self._exercise(state)
            if exercise["kind"] in {"c_program", "security_lab"}:
                if requested_step not in (None, 1):
                    raise ValueError("完整程序只有一个提交检查点")
                state["hint_count"] += 1
                state["hint_levels"]["1"] = min(2, state["hint_levels"].get("1", 0) + 1)
                state["hint"] = {
                    "step": 1,
                    "level": state["hint_levels"]["1"],
                    "text": exercise["parameters"].get("hint")
                    or (
                        "逐字符读取时用 int 保存 getchar 的结果以区分 EOF；保留行内空格，不统计行末换行。分别检查空行、没有末尾换行及 1000 字符边界。"
                        if exercise.get("family_id") == "c_program_string"
                        else "先检查输入读取和数据类型，再用最小输入、负数、重复值或上界验证循环边界。"
                    )
                    + "编译错误看诊断位置，输出失败看具体用例；用例编号不是代码行号。",
                }
                return "hint_viewed", state["hint"]
            opt = exercise["algorithm"] == "OPT"
            submitted = state["submissions"][-1]["rows"] if state["submissions"] else []
            opt_evictions = (
                [row["evicted"] for row in self._normalize(exercise, submitted)]
                if opt and submitted
                else None
            )
            solution = self._solve(exercise, opt_evictions=opt_evictions)
            c_question = exercise["kind"] in {"c_trace", "c_repair"}
            banker = exercise["kind"] == "banker"
            security = exercise["kind"] in CHECKPOINT_KINDS
            standard = (
                exercise["parameters"]["security_checks"]
                if security
                else (
                    state["draft"]
                    if banker
                    else (
                        exercise["parameters"]["checkpoints"]
                        if c_question
                        else solution[
                            (
                                "trace"
                                if exercise["kind"] == "page_replacement"
                                else "timeline"
                            )
                        ]
                    )
                )
            )
            error = (state["evaluation"] or {}).get("first_error")
            step = (
                requested_step
                if requested_step is not None
                else (error or {}).get("step", 1)
            )
            if type(step) is not int or not 1 <= step <= len(standard):
                raise ValueError("提示步骤超出范围，请先删除多余执行段")
            if (banker or opt) and step > (error or {}).get("step", 1):
                raise ValueError("请先订正并核验前面的步骤，再获取后续提示")
            key = str(step)
            level = min(2, state["hint_levels"].get(key, 0) + 1)
            rules = {
                "LRU": "LRU 淘汰最近最久未被访问的页面；命中后也要更新最近使用记录。",
                "FIFO": "FIFO 淘汰最早进入内存的页面；命中不会改变装入顺序。",
                "OPT": "OPT 查看当前访问之后的完整序列，淘汰下一次访问最远的驻留页；不再访问视为最远，并列时可任选。",
                "FCFS": "FCFS 按到达顺序执行，不抢占；CPU 空闲时须等到下一进程到达。",
                "RR": "RR 每次运行不超过一个时间片；片末到达者先入队，再将未完成进程放到队尾。",
                "SJF": "SJF 只比较已到达进程的服务时间，选择最短者且运行中不抢占；相等时按到达时间、编号排序。",
                "SRTF": "SRTF 在进程到达或完成时比较剩余服务时间；相等时按到达时间、编号排序。同一进程连续运行须合并成一段。",
            }
            if security:
                text = (
                    exercise["rules"] + " 当前检查点：" + standard[step - 1]["label"]
                    if exercise["kind"] in EXTENDED_KINDS | PV_KINDS
                    else security_hint(exercise, step, level)
                )
            elif banker:
                count = len(exercise["parameters"]["banker_processes"])
                text = "Need = Max - Allocation；选取 Need 各分量不超过 Work 的未完成进程，完成后释放 Allocation。所有进程都能完成才安全。"
                if level == 2:
                    if step <= count:
                        p = exercise["parameters"]["banker_processes"][step - 1]
                        text = f"{p['name']} 的 Max 为 {p['maximum']}，Allocation 为 {p['allocation']}，按相同资源列逐项相减。"
                    elif step <= count * 2:
                        # 提示跟随已核验的学生路径，不能使用另一条参考安全序列的 Work。
                        work = list(exercise["parameters"]["available"])
                        submitted = (
                            state["submissions"][-1]["rows"]
                            if state["submissions"]
                            else []
                        )
                        for row in submitted[count : step - 1]:
                            if row["process"].upper() in solution["allocation"]:
                                work = [
                                    w + a
                                    for w, a in zip(
                                        work,
                                        solution["allocation"][row["process"].upper()],
                                    )
                                ]
                        text += f" 本步开始时 Work 为 {work}。逐项比较需求；更新时只加所选进程的 Allocation。"
                    else:
                        text += " 检查已完成进程的数量；若仍有未完成进程且都不能满足需求，则不存在安全序列。"
            elif c_question:
                family = exercise["parameters"]["family"]
                text = {
                    "loop": "循环体每执行一次，才把当前 i 加到 s；条件不成立时不再执行。",
                    "pointer": "指针加 1 移动一个数组元素；修改 *p 会改变它指向的元素。",
                    "call": "传入 &x 后，函数中的 *p 与调用者的 x 指向同一个对象。",
                    "repair": "先按题目要求列出输入到返回值的运算顺序，再对照运算符。",
                }[family]
                if level == 2:
                    text += (
                        " 用 x = 0 检查常量项，再用一个负数检查乘法项。"
                        if family == "repair"
                        else f" 当前检查点：{standard[step - 1]}。"
                        + (
                            f"前一检查点的正确值为 {solution['c_trace'][step - 2]['value']}。"
                            if step > 1
                            else "从题目中的初始化语句开始。"
                        )
                    )
            else:
                text = rules[exercise["algorithm"]]
            if level == 2 and not c_question and not banker and not security:
                row = standard[step - 1]
                if exercise["kind"] == "page_replacement":
                    before = row["frames_before"]
                    if exercise["algorithm"] == "LRU":
                        prefix = exercise["parameters"]["sequence"][: step - 1]
                        positions = [
                            f"{p}：第 {max(i for i, value in enumerate(prefix, 1) if value == p)} 次"
                            for p in before
                        ]
                        text = f"本步访问 {row['page']}，此前页框为 {before}。各页面最近访问位置：{'；'.join(positions) or '尚未装入页面'}。先判断是否命中；需要置换时比较这些位置。"
                    elif opt:
                        positions = [
                            (
                                f"{p}：第 {position} 次"
                                if position is not None
                                else f"{p}：不再访问"
                            )
                            for p, position in row["next_uses"].items()
                        ]
                        text = f"本步访问 {row['page']}，此前页框为 {before}。后续首次访问：{'；'.join(positions) or '尚未装入页面'}。先判断是否命中或仍有空页框，确需置换时再比较后续位置；并列最远时任选其一。"
                    else:
                        text = f"本步访问 {row['page']}，此前页框为 {before}。回看这些页面的装入顺序，命中时不要移动队列。"
                else:
                    previous_end = standard[step - 2]["end"] if step > 1 else 0
                    text = (
                        rules[exercise["algorithm"]]
                        + f" 上一段结束于 {previous_end}。检查此时已到达且未完成的进程；没有就绪进程时跳到下一到达时刻。"
                    )
                    if exercise["algorithm"] == "RR":
                        text += f"本题时间片为 {exercise['parameters']['quantum']}，运行时长还受剩余服务时间限制。"
            if level > state["hint_levels"].get(key, 0):
                state["hint_count"] += 1
            state["hint_levels"][key] = level
            state["hint"] = {"step": step, "level": level, "text": text}
            return "hint_viewed", state["hint"]

        return self._public(
            self.store.update(attempt_id, action, guard=self._guard("hint"))
        )

    def solution(self, attempt_id: str) -> dict[str, Any]:
        def action(
            state: dict[str, Any], _existing: list[dict[str, Any]]
        ) -> tuple[str, dict[str, Any]]:
            state["solution_viewed"] = True
            return "solution_viewed", {}

        return self._public(
            self.store.update(attempt_id, action, guard=self._guard("solution"))
        )

    def _public(
        self, state: dict[str, Any], existing: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        first = state["submissions"][0] if state["submissions"] else None
        count = len(state["submissions"])
        independent = bool(
            first and first["evaluation"]["passed"] and first["unassisted"]
        )
        result = {
            key: state[key]
            for key in (
                "id",
                "parent_id",
                "created_at",
                "updated_at",
                "status",
                "draft",
                "evaluation",
                "first_error",
                "hint_count",
                "hint",
                "solution_viewed",
                "previously_seen",
            )
        }
        result.update(
            {
                "exercise": self._exercise(state),
                "content_version": state.get("content_version", "legacy"),
                "grading_version": state.get("grading_version", "rules-v1"),
                "class_id": state.get("class_id"),
                "legacy_record": not state.get("owner_id"),
                "diagnostic": bool(state.get("diagnostic_key")),
                "grading_reviews": state.get("grading_reviews", []),
                "dialogue": state.get("dialogue"),
                "walkthrough": state.get("walkthrough"),
                "review_id": state.get("review_id"),
                "tutoring_viewed": state.get("tutoring_viewed", False),
                "submission_count": count,
                "correction_count": max(0, count - 1),
                "first_unassisted_pass": independent,
                "draft_dirty": bool(
                    count and state["draft"] != state["submissions"][-1]["rows"]
                ),
                "recommendation": self._recommendation(
                    state,
                    existing if existing is not None else self.store.list_attempts(),
                ),
                "solution": (
                    self._solve(self._exercise(state))
                    if state["solution_viewed"]
                    else None
                ),
            }
        )
        if state.get("assignment_id"):
            result["recommendation"] = None
            if self.tasks:
                policy = self.tasks.policy(state)
                result["assignment"] = policy
                if policy["feedback_hidden"]:
                    result.update(
                        status="in_progress",
                        evaluation=None,
                        grading_reviews=[],
                        dialogue=None,
                        walkthrough=None,
                        first_error=None,
                        first_unassisted_pass=False,
                        solution=None,
                        hint=None,
                    )
        return result

    def progress(self) -> dict[str, Any]:
        if self.tasks:
            for task_id in {
                state.get("assignment_id") for state in self.store.list_attempts()
            } - {None}:
                self.tasks.archive_task(task_id)
        states = sorted(
            self.store.list_attempts(),
            key=lambda item: item["created_at"],
            reverse=True,
        )
        records = []
        errors: Counter[str] = Counter()
        for state in states:
            public = self._public(state, states)
            children = [
                item
                for item in states
                if item["parent_id"] == state["id"]
                or item.get("review_id") == state["id"]
            ]
            records.append(
                {
                    key: public[key]
                    for key in (
                        "id",
                        "parent_id",
                        "exercise",
                        "content_version",
                        "class_id",
                        "legacy_record",
                        "review_id",
                        "tutoring_viewed",
                        "created_at",
                        "status",
                        "first_error",
                        "submission_count",
                        "correction_count",
                        "hint_count",
                        "solution_viewed",
                        "first_unassisted_pass",
                        "previously_seen",
                    )
                }
            )
            records[-1]["assignment"] = public.get("assignment")
            records[-1]["retests"] = [
                {
                    "id": item["id"],
                    "status": item["status"],
                    "independent_pass": bool(
                        item["submissions"]
                        and item["submissions"][0]["unassisted"]
                        and item["submissions"][0]["evaluation"]["passed"]
                    ),
                }
                for item in children
            ]
            for submission in (
                []
                if (public.get("assignment") or {}).get("feedback_hidden")
                else state["submissions"]
            ):
                error = submission["evaluation"]["first_error"]
                if error:
                    errors[error["error_code"]] += 1
        submitted = [record for record in records if record["submission_count"]]
        return {
            "records": records,
            "summary": {
                "attempts": len(records),
                "submitted": len(submitted),
                "first_unassisted_passes": sum(
                    record["first_unassisted_pass"] for record in submitted
                ),
                "corrected_passes": sum(
                    record["status"] == "passed" and record["correction_count"] > 0
                    for record in records
                ),
                "independent_retest_passes": sum(
                    record["first_unassisted_pass"]
                    and bool(record["parent_id"] or record["review_id"])
                    for record in records
                ),
            },
            "errors": [
                {"code": code, "label": ERROR_LABELS[code], "count": count}
                for code, count in errors.items()
            ],
        }
