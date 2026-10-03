from __future__ import annotations

import re
from .learning_courses import COURSES, course_chapters
from .learning_readers_writers import reader_writer_trace
from .learning_os_foundations import KINDS as OS_FOUNDATION_KINDS, solve_os_foundation

EXTENDED_KINDS = OS_FOUNDATION_KINDS | {
    "readers_writers",
    "clock_trace",
    "address_translation",
    "auth_flow",
    "signature_check",
    "disk_schedule",
    "resource_request",
    "schedule_metrics",
}


def build_extended_exercises():
    result = {}

    def add(kind, number, title, subject, chapter_number, parameters, rules):
        chapter = next(
            c for c in course_chapters(subject) if c["number"] == chapter_number
        )
        algorithm = {
            "resource_request": "资源请求判断",
            "schedule_metrics": parameters.get("schedule_algorithm", "") + " 时间指标",
            "clock_trace": "CLOCK 状态",
            "address_translation": "分页地址转换",
            "auth_flow": "认证流程",
            "signature_check": "签名验证",
            "disk_schedule": "磁盘 " + parameters.get("disk_algorithm", ""),
        }[kind]
        key = f"{kind}_{number:02d}"
        exercise = {
            "id": key,
            "subject_id": subject,
            "subject_name": COURSES[subject]["name"],
            "chapter_id": chapter["id"],
            "chapter_title": chapter["title"],
            "title": f"{algorithm} · {title}",
            "algorithm": algorithm,
            "kind": kind,
            "difficulty": "基础" if number < 3 else "进阶",
            "parameters": parameters,
            "rules": rules,
            "training_tags": ["wrong_" + kind],
        }
        parameters["security_checks"] = checks(exercise)
        result[key] = exercise

    for n, (frames, sequence) in enumerate(
        [(3, [1, 2, 3, 1, 4, 2]), (2, [0, 1, 0, 2, 1]), (1, [2, 2, 3, 2])], 1
    ):
        add(
            "clock_trace",
            n,
            f"{frames} 个页框",
            "operating_systems",
            8,
            {
                "frames": frames,
                "sequence": sequence,
                "description": f"页框槽位从 0 编号，初始为空（填 -1），访问位初始为 0，指针从 0 开始。访问序列：{sequence}。每步填写访问后的完整状态。",
            },
            "命中时置该槽访问位为 1，指针不动；缺页从指针处扫描，遇访问位 1 则清零并前移，遇空槽或访问位 0 则装入并置 1，指针移到下一槽。按槽位顺序填逗号分隔整数，不能把页框看作无序集合。",
        )
    for n, (size, pages, addresses) in enumerate(
        [
            (1024, [3, 0, None], [0, 1023, 1024, 2048]),
            (256, [None, 7, 2], [0, 256, 767, 768]),
            (4096, [8, None, 1], [4095, 4096, 8192, 12288]),
        ],
        1,
    ):
        add(
            "address_translation",
            n,
            f"页大小 {size} 字节",
            "operating_systems",
            7,
            {
                "page_size": size,
                "page_table": pages,
                "addresses": addresses,
                "description": f"页大小 {size} 字节；虚拟页从 0 编号，页表映射页框为 {pages}（None 表示不在内存）。虚拟地址依次为 {addresses}。",
            },
            "对每个地址计算页号和页内偏移；超出页表长度为越界，合法但不在内存为缺页，均不产生物理地址，填 -1。有效映射的物理地址 = 页框号×页大小+偏移。无 TLB，暂不处理调页。",
        )
    events = [
        [
            dict(proof=True, expired=False, used=False),
            dict(proof=False, expired=False, used=False),
            dict(proof=True, expired=True, used=False),
        ],
        [
            dict(proof=True, expired=False, used=True),
            dict(proof=True, expired=False, used=False),
            dict(proof=False, expired=True, used=False),
        ],
        [
            dict(proof=False, expired=False, used=True),
            dict(proof=True, expired=True, used=True),
            dict(proof=True, expired=False, used=False),
        ],
    ]
    for n, cases in enumerate(events, 1):
        lines = [
            f"请求 {i}：凭据验证{'匹配' if e['proof'] else '不匹配'}；挑战{'过期' if e['expired'] else '有效'}；随机数{'已使用' if e['used'] else '未使用'}。"
            for i, e in enumerate(cases, 1)
        ]
        add(
            "auth_flow",
            n,
            f"挑战与重放检查 {n}",
            "cybersec_lab",
            3,
            {
                "auth_events": cases,
                "description": "受控认证流程模拟，各请求独立。" + " ".join(lines),
            },
            "按固定顺序检查：挑战过期→随机数已使用→凭据不匹配；任一步失败即拒绝，全部通过才允许，并消费随机数。填写结果和第一个失败原因。本题检验给定流程，不代表真实系统已具备安全认证。",
        )
    for n, message in enumerate([42, 57, 91], 1):
        signature = pow(message, 2753, 3233)
        cases = [
            dict(digest=message, signature=signature),
            dict(digest=message + 1, signature=signature),
            dict(digest=message, signature=(signature + 1) % 3233),
        ]
        add(
            "signature_check",
            n,
            f"消息与签名变化 {n}",
            "cybersec_lab",
            5,
            {
                "n": 3233,
                "e": 17,
                "signature_cases": cases,
                "description": "教学用小参数 RSA 验证，公钥 n=3233、e=17。给定已重算的消息摘要与签名："
                + "；".join(
                    f"样例 {i}：摘要={c['digest']}，签名={c['signature']}"
                    for i, c in enumerate(cases, 1)
                ),
            },
            "计算签名^e mod n，并与当前消息的给定摘要比较，相等才通过。公钥已由题目固定；这里只验证给定证据，不推断身份可信。小参数与直接摘要模幂仅用于教学，不能用于实际签名系统；真实系统须使用标准哈希、填充与可信密钥。",
        )
    for algorithm in ["FCFS", "SSTF", "LOOK"]:
        for variant, (head, requests) in enumerate(
            [
                (50, [40, 60, 10, 90]),
                (0, [0, 10, 10, 199]),
                (100, [90, 20, 30, 20]),
            ],
            1,
        ):
            number = ["FCFS", "SSTF", "LOOK"].index(algorithm) * 3 + variant
            add(
                "disk_schedule",
                number,
                f"请求组 {variant}",
                "operating_systems",
                11,
                {
                    "disk_algorithm": algorithm,
                    "head": head,
                    "requests": requests,
                    "description": f"磁道范围 0 至 199，磁头起点 {head}；所有请求在开始前到达，按队列顺序为 {requests}。使用 {algorithm}，填写服务磁道顺序及总移动距离。",
                },
                "重复磁道请求分别服务，当前位置请求距离为 0。FCFS 按输入顺序；SSTF 每次选距离最近的未服务请求，等距取原队列靠前者；LOOK 初始向磁道号增大方向，服务完该方向最后一个请求后反向，不移动到磁盘端点。总距离含起点到首个请求的移动，不含最终返回。",
            )
            result[f"disk_schedule_{number:02d}"]["difficulty"] = (
                "基础" if variant < 3 else "进阶"
            )
    for scale in [1, 2, 3]:
        allocation = {"P0": [scale, 0], "P1": [0, scale], "P2": [scale, scale]}
        maximum = {
            "P0": [2 * scale, scale],
            "P1": [scale, 2 * scale],
            "P2": [3 * scale, 2 * scale],
        }
        requests = [
            ("P0", [scale, 0]),
            ("P2", [scale, scale]),
            ("P0", [2 * scale, 0]),
            ("P2", [2 * scale, 0]),
            ("P1", [0, 0]),
            ("P0", [scale, scale]),
        ]
        add(
            "resource_request",
            scale,
            f"双资源请求组 {scale}",
            "operating_systems",
            6,
            {
                "available": [scale, scale],
                "allocation": allocation,
                "maximum": maximum,
                "requests": requests,
                "description": f"资源列顺序为 A、B；Available={[scale, scale]}。"
                + "；".join(
                    f"{name}: Allocation={allocation[name]}, Max={maximum[name]}"
                    for name in allocation
                )
                + "。独立请求："
                + "；".join(
                    f"{i}：{name} 请求 {request}"
                    for i, (name, request) in enumerate(requests, 1)
                ),
            },
            "各请求独立从题目初始状态开始，不累计分配。先检查 Request≤Need（Max−Allocation），超出为非法请求；再检查 Request≤Available，不足则等待资源；随后试分配并做安全性检查，不安全则等待安全状态并回滚，安全才允许分配。允许时填写任意完整合法安全序列，如 P0,P1,P2；其他情况填无。零请求也执行完整检查。不安全不等于已发生死锁。",
        )
    cases = [
        [("P1", 0, 5), ("P2", 1, 3), ("P3", 2, 1)],
        [("P1", 2, 2), ("P2", 8, 4), ("P3", 9, 1)],
        [("P1", 0, 3), ("P2", 0, 1), ("P3", 0, 1)],
    ]
    for algorithm in ["FCFS", "RR", "SJF", "SRTF"]:
        for variant, processes in enumerate(cases, 1):
            number = ["FCFS", "RR", "SJF", "SRTF"].index(algorithm) * 3 + variant
            add(
                "schedule_metrics",
                number,
                f"进程组 {variant}",
                "operating_systems",
                9,
                {
                    "schedule_algorithm": algorithm,
                    "metric_processes": processes,
                    "quantum": 2,
                    "description": f"采用 {algorithm}，忽略切换开销，无 I/O 阻塞，RR 时间片为 2。"
                    + "；".join(
                        f"{name}：到达 {arrival}，服务 {service}"
                        for name, arrival, service in processes
                    ),
                },
                "所有时间单位相同。完成时间为最后一次执行的结束时刻；周转时间=完成−到达；等待时间=周转−服务；响应时间=首次开始−到达。FCFS 按到达、编号排序；SJF 非抢占，选已到达的最短服务进程；SRTF 抢占，选最短剩余时间，相等按到达、编号排序。RR 同时到达按编号，片末到达者先入队，再将未完成进程放回队尾。CPU 空闲须计入绝对时刻。",
            )
            result[f"schedule_metrics_{number:02d}"]["difficulty"] = (
                "基础" if variant < 3 else "进阶"
            )
    return result


def request_result(parameters, request_index, solver):
    name, request = parameters["requests"][request_index]
    allocation = {key: list(value) for key, value in parameters["allocation"].items()}
    maximum = parameters["maximum"]
    available = list(parameters["available"])
    need = [m - a for m, a in zip(maximum[name], allocation[name])]
    if any(r > n for r, n in zip(request, need)):
        return "非法请求", None
    if any(r > a for r, a in zip(request, available)):
        return "等待资源", None
    available = [a - r for a, r in zip(available, request)]
    allocation[name] = [a + r for a, r in zip(allocation[name], request)]
    safety = solver.solve_banker(available, allocation, maximum)
    return ("允许分配" if safety["safe"] else "等待安全状态"), safety


def checks(exercise):
    kind, p = exercise["kind"], exercise["parameters"]
    if kind == "resource_request":
        return [
            dict(
                label=f"请求 {i}：{label}",
                format="choice" if label == "结论" else "process_sequence",
                **(
                    {"options": ["非法请求", "等待资源", "等待安全状态", "允许分配"]}
                    if label == "结论"
                    else {}
                ),
            )
            for i in range(1, len(p["requests"]) + 1)
            for label in ["结论", "安全序列"]
        ]
    if kind == "schedule_metrics":
        return [
            dict(label=f"{name}：{label}", format="text")
            for name, _, _ in p["metric_processes"]
            for label in ["完成时间", "周转时间", "等待时间", "响应时间"]
        ]
    if kind == "disk_schedule":
        return [
            dict(label="服务磁道顺序", format="text"),
            dict(label="总移动距离（磁道）", format="text"),
        ]
    if kind == "clock_trace":
        return [
            dict(label=f"访问 {i}（页面 {page}）后的{label}", format="text")
            for i, page in enumerate(p["sequence"], 1)
            for label in ["页框槽位", "访问位", "下一指针"]
        ]
    if kind == "address_translation":
        return [
            dict(
                label=f"地址 {a}：{label}",
                format="choice" if label == "结果" else "text",
                **({"options": ["有效", "缺页", "越界"]} if label == "结果" else {}),
            )
            for a in p["addresses"]
            for label in ["页号", "页内偏移", "结果", "物理地址"]
        ]
    if kind == "auth_flow":
        return [
            dict(
                label=f"请求 {i}：{label}",
                format="choice",
                options=(
                    ["允许", "拒绝"]
                    if label == "结果"
                    else ["无", "挑战过期", "随机数已使用", "凭据不匹配"]
                ),
            )
            for i in range(1, len(p["auth_events"]) + 1)
            for label in ["结果", "首个失败原因"]
        ]
    return [
        dict(
            label=f"样例 {i}：{label}",
            format="choice" if label == "结果" else "text",
            **({"options": ["通过", "失败"]} if label == "结果" else {}),
        )
        for i in range(1, len(p["signature_cases"]) + 1)
        for label in ["恢复摘要", "结果"]
    ]


def solve_extended(exercise, solver=None):
    if exercise["kind"] in OS_FOUNDATION_KINDS:
        return solve_os_foundation(exercise)
    kind, p = exercise["kind"], exercise["parameters"]
    values = []
    if kind == "readers_writers":
        for state in reader_writer_trace(p):
            values.extend(
                [
                    ",".join(state["active"]) or "无",
                    ",".join(state["waiting"]) or "无",
                    state["outcome"],
                ]
            )
    elif kind in {"resource_request", "schedule_metrics"}:
        if solver is None:
            from .problem_tutoring_service import ProblemTutoringService

            solver = ProblemTutoringService()
        if kind == "resource_request":
            for i in range(len(p["requests"])):
                outcome, safety = request_result(p, i, solver)
                values.extend(
                    [
                        outcome,
                        (
                            ",".join(safety["safe_sequence"])
                            if outcome == "允许分配"
                            else "无"
                        ),
                    ]
                )
        else:
            result = solver.solve_cpu_scheduling(
                p["schedule_algorithm"],
                [tuple(process) for process in p["metric_processes"]],
                p["quantum"],
            )
            if result["status"] != "success":
                raise ValueError(result["message"])
            metrics = result["result"]["metrics"]
            for name, arrival, _ in p["metric_processes"]:
                item = metrics[name]
                values.extend(
                    str(item[key]) for key in ["completion", "turnaround", "waiting"]
                )
                values.append(str(item["start"] - arrival))
    elif kind == "disk_schedule":
        pending = list(enumerate(p["requests"]))
        head = p["head"]
        if p["disk_algorithm"] == "LOOK":
            pending = sorted(
                [r for r in pending if r[1] >= head], key=lambda r: (r[1], r[0])
            ) + sorted([r for r in pending if r[1] < head], key=lambda r: (-r[1], r[0]))
        order, distance = [], 0
        while pending:
            chosen = (
                min(pending, key=lambda r: (abs(r[1] - head), r[0]))
                if p["disk_algorithm"] == "SSTF"
                else pending[0]
            )
            pending.remove(chosen)
            distance += abs(chosen[1] - head)
            head = chosen[1]
            order.append(head)
        values = [",".join(map(str, order)), str(distance)]
    elif kind == "clock_trace":
        slots = [-1] * p["frames"]
        bits = [0] * p["frames"]
        hand = 0
        for page in p["sequence"]:
            if page in slots:
                bits[slots.index(page)] = 1
            else:
                while slots[hand] != -1 and bits[hand]:
                    bits[hand] = 0
                    hand = (hand + 1) % len(slots)
                slots[hand] = page
                bits[hand] = 1
                hand = (hand + 1) % len(slots)
            values.extend(
                [",".join(map(str, slots)), ",".join(map(str, bits)), str(hand)]
            )
    elif kind == "address_translation":
        for address in p["addresses"]:
            page, offset = divmod(address, p["page_size"])
            frame = p["page_table"][page] if page < len(p["page_table"]) else None
            outcome = (
                "越界"
                if page >= len(p["page_table"])
                else "缺页" if frame is None else "有效"
            )
            values.extend(
                [
                    str(page),
                    str(offset),
                    outcome,
                    str(frame * p["page_size"] + offset) if frame is not None else "-1",
                ]
            )
    elif kind == "auth_flow":
        for event in p["auth_events"]:
            cause = (
                "挑战过期"
                if event["expired"]
                else (
                    "随机数已使用"
                    if event["used"]
                    else "凭据不匹配" if not event["proof"] else "无"
                )
            )
            values.extend(["允许" if cause == "无" else "拒绝", cause])
    else:
        for case in p["signature_cases"]:
            digest = pow(case["signature"], p["e"], p["n"])
            values.extend([str(digest), "通过" if digest == case["digest"] else "失败"])
    return {
        "security_trace": [
            {"checkpoint": c["label"], "value": v}
            for c, v in zip(p["security_checks"], values)
        ],
        "explanation": exercise["rules"],
    }


def evaluate_extended(exercise, rows, labels, solver=None):
    first = None
    if exercise["kind"] == "resource_request" and solver is None:
        from .problem_tutoring_service import ProblemTutoringService

        solver = ProblemTutoringService()
    for number, (row, expected, check) in enumerate(
        zip(
            rows,
            solve_extended(exercise, solver)["security_trace"],
            exercise["parameters"]["security_checks"],
        ),
        1,
    ):
        value = row["value"].strip()
        if check.get("format") == "process_sequence":
            outcome, safety = request_result(
                exercise["parameters"], (number - 1) // 2, solver
            )
            if outcome != "允许分配":
                matches = value == "无"
            else:
                names = re.split(r"[,，\s]+", value.upper()) if value else []
                matches = len(names) == len(safety["need"]) and set(names) == set(
                    safety["need"]
                )
                work = list(safety["available"])
                if matches:
                    for name in names:
                        if any(n > w for n, w in zip(safety["need"][name], work)):
                            matches = False
                            break
                        work = [w + a for w, a in zip(work, safety["allocation"][name])]
        elif check.get("format") in {"rw_active", "rw_queue"}:
            names = re.split(r"[,，\s]+", value.upper()) if value else []
            expected_names = expected["value"].split(",")
            if check["format"] == "rw_active":
                matches = len(names) == len(set(names)) and set(names) == set(
                    expected_names
                )
            else:
                matches = names == expected_names
        elif check.get("options"):
            if value and value not in check["options"]:
                raise ValueError("请选择题目列出的选项")
            matches = value == expected["value"]
        else:
            parts = re.split(r"[,，\s]+", value) if value else []
            if any(not re.fullmatch(r"-?\d{1,10}", part) for part in parts):
                raise ValueError("数值检查点须填写整数，多个数用逗号分隔")
            matches = bool(parts) and [int(x) for x in parts] == [
                int(x) for x in expected["value"].split(",")
            ]
        if not matches and first is None:
            code = "wrong_" + exercise["kind"] if value else "incomplete"
            first = {
                "step": number,
                "field": "value",
                "error_code": code,
                "label": labels[code],
                "message": f"请重新核对“{check['label']}”。",
                "possible_cause": None,
            }
    step = first["step"] if first else len(rows) + 1
    return {
        "passed": first is None,
        "first_error": first,
        "row_statuses": [
            "correct" if i < step else "error" if i == step else "pending"
            for i in range(1, len(rows) + 1)
        ],
    }
