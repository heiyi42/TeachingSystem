from __future__ import annotations

from typing import Any

from .learning_c import build_c_exercises
from .learning_pv import build_pv_exercises
from .learning_readers_writers import build_readers_writers_exercises
from .learning_program import build_program_exercises
from .learning_c_advanced import build_advanced_program_exercises
from .learning_os_foundations import build_os_foundation_exercises
from .learning_extended import build_extended_exercises
from .learning_security import build_security_exercises
from .learning_security_lab import build_security_lab_exercises
from .learning_courses import COURSES, course_chapters


def build_exercises() -> dict[str, dict[str, Any]]:
    exercises: dict[str, dict[str, Any]] = {}
    page_cases = [
        (
            "命中后的置换",
            3,
            [1, 2, 3, 1, 4, 2],
            "基础",
            ["wrong_victim", "wrong_event"],
        ),
        (
            "连续命中与更新",
            3,
            [2, 4, 6, 2, 4, 8, 6],
            "基础",
            ["wrong_victim", "wrong_frames"],
        ),
        (
            "两个页框的变化",
            2,
            [0, 1, 0, 2, 1, 3, 1],
            "基础",
            ["wrong_event", "wrong_victim"],
        ),
        (
            "多次置换",
            3,
            [7, 0, 1, 2, 0, 3, 0, 4, 2, 3],
            "进阶",
            ["wrong_frames", "wrong_victim"],
        ),
        (
            "四个页框的跟踪",
            4,
            [1, 2, 3, 4, 2, 5, 1, 6, 3, 2],
            "进阶",
            ["wrong_event", "wrong_frames"],
        ),
    ]
    opt_cases = [
        (
            "比较后续访问",
            3,
            [1, 2, 3, 4, 1, 2, 3],
            "基础",
            ["wrong_victim", "wrong_event"],
        ),
        (
            "不再访问与并列选择",
            2,
            [4, 5, 6, 7, 6, 7],
            "基础",
            ["wrong_victim", "wrong_frames"],
        ),
        (
            "零号页与命中",
            2,
            [0, 1, 0, 2, 1, 3, 1],
            "基础",
            ["wrong_event", "wrong_victim"],
        ),
        (
            "页框未满与连续命中",
            4,
            [1, 2, 1, 3, 2],
            "基础",
            ["wrong_event", "wrong_frames"],
        ),
        ("单页框的变化", 1, [2, 2, 3, 2, 4], "进阶", ["wrong_event", "wrong_frames"]),
    ]
    schedule_cases = [
        (
            "不同到达时间",
            [("P1", 0, 5), ("P2", 1, 3), ("P3", 2, 1)],
            2,
            "基础",
            ["wrong_order", "wrong_time"],
        ),
        (
            "同时到达",
            [("P1", 0, 4), ("P2", 0, 2), ("P3", 0, 3)],
            2,
            "基础",
            ["wrong_order"],
        ),
        (
            "CPU 空闲",
            [("P1", 1, 2), ("P2", 6, 3), ("P3", 7, 1)],
            2,
            "基础",
            ["wrong_time"],
        ),
        (
            "时间片边界",
            [("P1", 0, 5), ("P2", 2, 2), ("P3", 4, 3)],
            2,
            "进阶",
            ["wrong_order", "wrong_time"],
        ),
        (
            "短运行与后续到达",
            [("P1", 0, 1), ("P2", 3, 4), ("P3", 4, 2)],
            3,
            "进阶",
            ["wrong_time", "wrong_order"],
        ),
    ]
    shortest_cases = [
        (
            "运行中的短作业到达",
            [("P1", 0, 7), ("P2", 2, 3), ("P3", 3, 1)],
            None,
            "基础",
            ["wrong_order", "wrong_time"],
        ),
        (
            "同时到达与编号并列",
            [("P1", 0, 3), ("P2", 0, 1), ("P3", 0, 1)],
            None,
            "基础",
            ["wrong_order"],
        ),
        (
            "空闲后的重新选择",
            [("P1", 2, 2), ("P2", 8, 4), ("P3", 9, 1)],
            None,
            "基础",
            ["wrong_time", "wrong_order"],
        ),
        (
            "相等剩余时间",
            [("P1", 0, 5), ("P2", 2, 3), ("P3", 6, 1)],
            None,
            "进阶",
            ["wrong_order", "wrong_time"],
        ),
        (
            "连续到达与多次抢占",
            [("P1", 0, 9), ("P2", 1, 5), ("P3", 2, 2), ("P4", 3, 1)],
            None,
            "进阶",
            ["wrong_order", "wrong_time"],
        ),
    ]
    for algorithm in ("LRU", "FIFO", "OPT", "FCFS", "RR", "SJF", "SRTF"):
        cases = (
            opt_cases
            if algorithm == "OPT"
            else page_cases if algorithm in {"LRU", "FIFO"} else schedule_cases
        )
        if algorithm in {"SJF", "SRTF"}:
            cases = shortest_cases
        for index, (title, *case) in enumerate(cases, 1):
            if algorithm == "SJF" and index == 4:
                title = "短作业到达时不抢占"
            elif algorithm == "SJF" and index == 5:
                title = "连续到达的选择"
            if algorithm in {"LRU", "FIFO", "OPT"}:
                frames, sequence, difficulty, tags = case
                parameters = {"frames": frames, "sequence": sequence}
                kind = "page_replacement"
                rules = "页框初始为空；页框排列顺序不影响判定；无淘汰时填 —。"
                if algorithm == "OPT":
                    rules += "页框满且缺页时，淘汰下一次访问最远的驻留页；以后不再访问视为最远。多个页面均不再访问时任选其一，后续作答按你的选择继续。OPT 使用已知完整访问序列。"
            else:
                processes, quantum, difficulty, tags = case
                parameters = {
                    "processes": [
                        {"name": name, "arrival": arrival, "service": service}
                        for name, arrival, service in processes
                    ],
                    "quantum": quantum if algorithm == "RR" else None,
                }
                kind = "cpu_scheduling"
                rules = "同时到达按进程编号升序入队；忽略切换开销；只填写执行段，CPU 空闲保留时间间隔。"
                if algorithm == "RR":
                    rules += "每行填写一个时间片；片末已到达的进程先入队，再将未完成的当前进程放回队尾。"
                elif algorithm == "SRTF":
                    rules += "每次到达或完成时选择剩余服务时间最短的就绪进程；相等时依次按到达时间、进程编号升序选择。每行填写同一进程的最大连续执行段，未发生切换不要拆行。"
                elif algorithm == "SJF":
                    rules += "每次选择服务时间最短的就绪进程，不抢占；相等时依次按到达时间、进程编号升序选择。每行填写完整执行段。"
                else:
                    rules += "每行填写一个进程的完整执行段，运行中不抢占。"
            chapter_number = 8 if kind == "page_replacement" else 9
            chapter = course_chapters("operating_systems")[chapter_number]
            exercise_id = f"{algorithm.lower()}_{index:02d}"
            exercises[exercise_id] = {
                "subject_id": "operating_systems",
                "subject_name": COURSES["operating_systems"]["name"],
                "chapter_id": chapter["id"],
                "chapter_title": chapter["title"],
                "id": exercise_id,
                "title": f"{algorithm} · {title}",
                "algorithm": algorithm,
                "kind": kind,
                "difficulty": difficulty,
                "parameters": parameters,
                "training_tags": tags,
                "rules": rules,
            }
    banker_cases = [
        ("多个合法序列", [1, 1], [[1, 0], [0, 1], [1, 1]], [[2, 1], [1, 2], [3, 2]]),
        ("逐步释放资源", [0, 1], [[1, 0], [0, 1], [1, 1]], [[1, 1], [1, 2], [2, 3]]),
        (
            "无法完成全部进程",
            [1, 0],
            [[1, 0], [0, 1], [1, 1]],
            [[2, 0], [3, 2], [3, 2]],
        ),
        (
            "零需求与相等边界",
            [0, 0, 0],
            [[1, 0, 1], [0, 1, 0], [1, 1, 0]],
            [[1, 0, 1], [1, 1, 1], [2, 2, 1]],
        ),
        ("初始即无法推进", [0, 0], [[1, 0], [0, 1], [1, 1]], [[2, 0], [0, 2], [2, 2]]),
    ]
    chapter = course_chapters("operating_systems")[6]
    for number, (title, available, allocation, maximum) in enumerate(banker_cases, 1):
        exercise_id = f"banker_{number:02d}"
        exercises[exercise_id] = {
            "id": exercise_id,
            "subject_id": "operating_systems",
            "subject_name": "操作系统",
            "chapter_id": chapter["id"],
            "chapter_title": chapter["title"],
            "title": f"银行家算法 · {title}",
            "algorithm": "银行家算法",
            "kind": "banker",
            "difficulty": "基础" if number <= 2 else "进阶",
            "parameters": {
                "available": available,
                "resources": [chr(65 + i) for i in range(len(available))],
                "banker_processes": [
                    {"name": f"P{i}", "allocation": a, "maximum": m}
                    for i, (a, m) in enumerate(zip(allocation, maximum))
                ],
            },
            "training_tags": [
                "wrong_need",
                "ineligible_process",
                "wrong_work",
                "wrong_safety",
            ],
            "rules": "先填写 Need = Max - Allocation；Work 初始为 Available。每步选择 Need 各分量均不超过 Work 的未完成进程，填写释放 Allocation 后的 Work。接受任意合法顺序；无法继续时，剩余过程行留空，再判断是否全部完成。不安全不等同于已经死锁。",
        }
    exercises.update(build_c_exercises())
    exercises.update(build_security_exercises())
    exercises.update(build_program_exercises())
    exercises.update(build_extended_exercises())
    exercises.update(build_pv_exercises())
    exercises.update(build_readers_writers_exercises())
    exercises.update(build_advanced_program_exercises())
    exercises.update(build_os_foundation_exercises())
    exercises.update(build_security_lab_exercises())
    return exercises


EXERCISES = build_exercises()
