from __future__ import annotations

import re
from typing import Any

from .learning_courses import course_chapters


SECURITY_KINDS = {"dh", "access_control", "log_evidence"}
LOG_CONCLUSIONS = ["见失败后的成功", "有成功但未见失败后的成功", "仅见失败", "无匹配记录"]


def build_security_exercises() -> dict[str, dict[str, Any]]:
    exercises = {}

    def add(kind: str, number: int, title: str, chapter_number: int, parameters: dict, rules: str, tags: list[str]) -> None:
        chapter = course_chapters("cybersec_lab")[chapter_number]
        algorithm = {"dh": "DH 计算", "access_control": "权限矩阵", "log_evidence": "日志证据"}[kind]
        key = f"{kind}_{number:02d}"
        exercises[key] = {
            "id": key, "subject_id": "cybersec_lab", "subject_name": "网络安全实验",
            "chapter_id": chapter["id"], "chapter_title": chapter["title"],
            "title": f"{algorithm} · {title}", "algorithm": algorithm, "kind": kind,
            "difficulty": "基础" if number <= 3 else "进阶", "parameters": parameters,
            "rules": rules, "training_tags": tags,
        }

    for number, (p, g, a, b) in enumerate([(23, 5, 6, 15), (11, 2, 3, 4), (17, 3, 4, 5), (19, 2, 7, 9), (29, 2, 5, 12)], 1):
        add("dh", number, f"模 {p} 的密钥交换", 2, {
            "p": p, "g": g, "a": a, "b": b,
            "description": f"公开参数 p = {p}，g = {g}；甲的私有指数 a = {a}，乙的私有指数 b = {b}。计算双方公开值及各自得到的共享值。",
            "security_checks": [{"label": label, "format": "integer"} for label in ["甲的公开值 A", "乙的公开值 B", "甲的共享值 K甲", "乙的共享值 K乙"]],
        }, "A = g^a mod p，B = g^b mod p，K甲 = B^a mod p，K乙 = A^b mod p。填写 0 至 p−1 的整数余数。这是小参数教学计算；双方共享值相等不代表已验证对方身份。", ["wrong_public", "wrong_shared"])

    access_cases = [
        ("单角色与默认拒绝", {"小林": ["阅读者"], "小周": []}, [("阅读者", "报告", "读")], [("小林", "报告", "读"), ("小林", "报告", "写"), ("小周", "报告", "读")]),
        ("多个角色的权限合并", {"小林": ["阅读者", "编辑者"], "小周": ["阅读者"]}, [("阅读者", "报告", "读"), ("编辑者", "报告", "写")], [("小林", "报告", "写"), ("小周", "报告", "写"), ("小林", "报告", "删")]),
        ("资源不能混用", {"小林": ["编辑者"], "小周": ["阅读者"]}, [("编辑者", "草稿", "写"), ("阅读者", "报告", "读")], [("小林", "报告", "写"), ("小林", "草稿", "写"), ("小周", "草稿", "读")]),
        ("操作独立授权", {"小林": ["维护者"], "小周": ["审计者", "阅读者"]}, [("维护者", "日志", "删"), ("审计者", "日志", "读"), ("阅读者", "报告", "读")], [("小林", "日志", "读"), ("小林", "日志", "删"), ("小周", "日志", "读"), ("小周", "日志", "删")]),
        ("相同角色与无角色用户", {"小林": ["编辑者"], "小周": ["编辑者"], "小陈": []}, [("编辑者", "报告", "读"), ("编辑者", "报告", "写")], [("小周", "报告", "读"), ("小林", "报告", "删"), ("小陈", "报告", "写"), ("小周", "报告", "写")]),
    ]
    for number, (title, users, grants, queries) in enumerate(access_cases, 1):
        add("access_control", number, title, 4, {
            "description": "根据用户角色表和角色授权表，逐项判断请求是否允许。",
            "users": [{"name": name, "roles": roles} for name, roles in users.items()],
            "grants": [{"role": role, "resource": resource, "action": action} for role, resource, action in grants],
            "queries": [{"user": user, "resource": resource, "action": action} for user, resource, action in queries],
            "security_checks": [{"label": f"{user} / {resource} / {action}", "format": "choice", "options": ["允许", "拒绝"]} for user, resource, action in queries],
        }, "本题仅使用表中列出的角色授权；用户具有全部已分配角色，权限取并集。角色、资源、操作三项都匹配才允许，否则默认拒绝。无角色继承、显式拒绝、所有者特权；读、写、删互不隐含。", ["wrong_permission"])

    log_cases = [
        ("失败后成功与干扰来源", [("L1", "09:00:10", "小林", "终端A", "失败"), ("L2", "09:00:20", "小周", "终端A", "成功"), ("L3", "09:01:00", "小林", "终端A", "失败"), ("L4", "09:02:00", "小林", "终端B", "成功"), ("L5", "09:03:00", "小林", "终端A", "成功")]),
        ("窗口外成功不作证据", [("L1", "08:59:59", "小林", "终端A", "成功"), ("L2", "09:00:00", "小林", "终端A", "失败"), ("L3", "09:05:00", "小林", "终端A", "失败"), ("L4", "09:05:01", "小林", "终端A", "成功")]),
        ("成功在失败之前", [("L1", "09:04:00", "小林", "终端A", "失败"), ("L2", "09:01:00", "小林", "终端A", "成功"), ("L3", "09:03:00", "小周", "终端A", "失败")]),
        ("只有成功记录", [("L1", "09:00:00", "小林", "终端A", "成功"), ("L2", "09:02:00", "小林", "终端B", "失败"), ("L3", "09:05:00", "小林", "终端A", "成功")]),
        ("没有匹配证据", [("L1", "09:01:00", "小周", "终端A", "失败"), ("L2", "09:02:00", "小林", "终端B", "成功"), ("L3", "09:06:00", "小林", "终端A", "失败")]),
    ]
    for number, (title, logs) in enumerate(log_cases, 1):
        add("log_evidence", number, title, 6, {
            "description": "以下为同一天的模拟登录日志。仅分析用户“小林”、来源“终端A”、09:00:00 至 09:05:00（含边界）的记录。",
            "target_user": "小林", "target_source": "终端A", "window_start": "09:00:00", "window_end": "09:05:00",
            "logs": [{"id": key, "time": time, "user": user, "source": source, "outcome": outcome} for key, time, user, source, outcome in logs],
            "security_checks": [
                {"label": "匹配的失败记录编号", "format": "evidence"},
                {"label": "匹配的成功记录编号", "format": "evidence"},
                {"label": "日志直接支持的时序结论", "format": "choice", "options": LOG_CONCLUSIONS},
            ],
        }, "编号用逗号分隔，顺序不限；没有记录填“无”。先按用户、来源和时间窗口筛选，再按时间比较，不能按表格行号推断先后。失败后的成功要求成功时间严格晚于至少一次匹配失败。这些记录仅证明所列登录现象，不能据此确认入侵、口令破解或攻击者身份。", ["wrong_evidence", "wrong_conclusion"])
    return exercises


def solve_security(exercise: dict[str, Any], solver: Any) -> dict[str, Any]:
    p = exercise["parameters"]
    kind = exercise["kind"]
    if kind == "dh":
        solved = solver.solve_diffie_hellman(p["p"], p["g"], p["a"], p["b"])
        if solved["status"] != "success":
            raise ValueError(solved["message"])
        result = solved["result"]
        values = [result[key] for key in ("public_a", "public_b", "shared_key_from_a", "shared_key_from_b")]
        explanations = solved["steps"]
    elif kind == "access_control":
        users = {user["name"]: set(user["roles"]) for user in p["users"]}
        values, explanations = [], []
        for query in p["queries"]:
            roles = users.get(query["user"], set())
            matches = [grant["role"] for grant in p["grants"] if grant["role"] in roles and grant["resource"] == query["resource"] and grant["action"] == query["action"]]
            values.append("允许" if matches else "拒绝")
            explanations.append(f"匹配授权角色：{'、'.join(matches)}。" if matches else "该用户没有角色同时授权此资源与操作，按默认拒绝处理。")
    else:
        matching = [row for row in p["logs"] if row["user"] == p["target_user"] and row["source"] == p["target_source"] and p["window_start"] <= row["time"] <= p["window_end"]]
        failures = [row for row in matching if row["outcome"] == "失败"]
        successes = [row for row in matching if row["outcome"] == "成功"]
        after_failure = any(success["time"] > failure["time"] for success in successes for failure in failures)
        conclusion = LOG_CONCLUSIONS[0 if after_failure else 1 if successes else 2 if failures else 3]
        values = [",".join(row["id"] for row in failures) or "无", ",".join(row["id"] for row in successes) or "无", conclusion]
        explanations = ["同时满足用户、来源、时间窗口及失败结果的记录。", "同时满足用户、来源、时间窗口及成功结果的记录。", "比较匹配记录的时间；结论限定为日志现象，不能确认入侵。"]
    return {"security_trace": [{"checkpoint": check["label"], "value": str(value), "explanation": explanation} for check, value, explanation in zip(p["security_checks"], values, explanations)]}


def evaluate_security(exercise: dict[str, Any], rows: list[dict[str, str]], solution: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    first_error = None
    for index, (row, check, expected) in enumerate(zip(rows, exercise["parameters"]["security_checks"], solution["security_trace"]), 1):
        value = row["value"]
        if check["format"] == "integer":
            if value and not re.fullmatch(r"\d{1,8}", value):
                raise ValueError(f"第 {index} 步须填写非负整数")
            matches = bool(value) and int(value) == int(expected["value"])
        elif check["format"] == "evidence":
            parts = re.split(r"[,，\s]+", value.upper()) if value and value != "无" else []
            if len(parts) != len(set(parts)) or any(part not in {log["id"] for log in exercise["parameters"]["logs"]} for part in parts):
                raise ValueError(f"第 {index} 步须填写不重复的题目日志编号，或填写“无”")
            matches = bool(value) and set(parts) == (set(expected["value"].split(",")) if expected["value"] != "无" else set())
        else:
            if value and value not in check["options"]:
                raise ValueError(f"第 {index} 步请选择题目列出的选项")
            matches = value == expected["value"]
        if not matches and first_error is None:
            if not value:
                code = "incomplete"
            elif exercise["kind"] == "dh":
                code = "wrong_public" if index <= 2 else "wrong_shared"
            elif exercise["kind"] == "access_control":
                code = "wrong_permission"
            else:
                code = "wrong_evidence" if index <= 2 else "wrong_conclusion"
            first_error = {"step": index, "field": "value", "error_code": code, "label": labels[code], "message": "请补全当前检查点。" if not value else f"“{check['label']}”不符，请按题目规则重新核验。", "possible_cause": None}
    step = first_error["step"] if first_error else None
    return {"passed": first_error is None, "first_error": first_error, "row_statuses": ["correct" if step is None or i < step else "error" if i == step else "pending" for i in range(1, len(rows) + 1)]}


def security_hint(exercise: dict[str, Any], step: int, level: int) -> str:
    p = exercise["parameters"]
    if exercise["kind"] == "dh":
        formula = ["A = g^a mod p", "B = g^b mod p", "K甲 = B^a mod p", "K乙 = A^b mod p"][step - 1]
        return f"本步使用 {formula}。先做幂运算再取模，可反复平方并在每步取模。" + (" 共享值使用对方公开值作为底数、自己的私有指数作为指数；不能直接使用 g 或对方的私有指数。" if level == 2 and step > 2 else f" 本题模数 p = {p['p']}，余数必须在 0 至 {p['p'] - 1} 之间。" if level == 2 else "")
    if exercise["kind"] == "access_control":
        query = p["queries"][step - 1]
        roles = next(user["roles"] for user in p["users"] if user["name"] == query["user"])
        return "先查用户的全部角色，再查是否有任一角色明确授权当前资源和操作；未匹配默认拒绝。" + (f" 本步用户 {query['user']} 的角色为 {'、'.join(roles) or '无'}；只比较资源“{query['resource']}”与操作“{query['action']}”，不能从其他操作推导。" if level == 2 else "")
    return "同时匹配用户、来源和时间窗口，再分别筛选失败与成功记录。时序比较使用时间字段。" + (f" 本题窗口为 [{p['window_start']}, {p['window_end']}]，两端都包含；用户为 {p['target_user']}，来源为 {p['target_source']}。" + (" 成功必须严格晚于至少一次匹配失败，才支持“见失败后的成功”。" if step == 3 else "无匹配记录时填“无”，不要填空。") if level == 2 else "")
