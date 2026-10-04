from __future__ import annotations

from collections import deque
import re

from webapp_core.learning.learning_courses import course_chapters

PV_KINDS = {"pv_trace", "pv_design"}


def build_pv_exercises():
    result = {}
    chapter = course_chapters("operating_systems")[5]

    def add(key, kind, algorithm, title, parameters, rules, number):
        result[key] = {
            "id": key,
            "subject_id": "operating_systems",
            "subject_name": "操作系统",
            "chapter_id": chapter["id"],
            "chapter_title": chapter["title"],
            "kind": kind,
            "algorithm": algorithm,
            "title": f"{algorithm} · {title}",
            "difficulty": "基础" if number < 3 else "进阶",
            "parameters": parameters,
            "rules": rules,
            "training_tags": ["wrong_pv", "pv_deadlock", "pv_safety"],
        }

    traces = [
        (1, [("A", "P"), ("B", "P"), ("A", "V"), ("B", "V")]),
        (0, [("A", "P"), ("B", "P"), ("C", "V"), ("A", "V"), ("B", "V")]),
        (
            2,
            [
                ("A", "P"),
                ("B", "P"),
                ("C", "P"),
                ("C", "V"),
                ("A", "V"),
                ("C", "V"),
                ("B", "V"),
            ],
        ),
    ]
    for number, (initial, events) in enumerate(traces, 1):
        names = sorted({name for name, _ in events})
        add(
            f"pv_trace_{number:02d}",
            "pv_trace",
            "PV 阻塞与唤醒",
            f"初值 {initial}",
            {
                "initial": initial,
                "events": events,
                "description": f"信号量 S 初值为 {initial}，等待队列为空；按给定顺序尝试操作："
                + "；".join(
                    f"{i}. {name} 执行 {op}(S)"
                    for i, (name, op) in enumerate(events, 1)
                ),
                "security_checks": [
                    dict(
                        label=f"操作 {i}：{label}",
                        format=fmt,
                        **(
                            {
                                "options": ["继续执行", "阻塞", "不可执行"]
                                + ["唤醒 " + name for name in names]
                            }
                            if fmt == "choice"
                            else {}
                        ),
                    )
                    for i in range(1, len(events) + 1)
                    for label, fmt in [
                        ("S 值", "text"),
                        ("等待队列", "pv_queue"),
                        ("操作结果", "choice"),
                    ]
                ],
            },
            "P、V 均为原子操作。P 先减 1，结果小于 0 则当前进程入队阻塞；V 先加 1，结果不大于 0 则按 FIFO 唤醒队首进程。被唤醒者已完成先前 P，不重做减 1。阻塞进程不能执行任何操作；若题目尝试调度它，标为不可执行且状态不变。等待队列按先后填 A,B；为空填无。",
            number,
        )

    for number, count in enumerate([2, 3, 4], 1):
        processes = [{"name": chr(65 + i), "role": "mutex"} for i in range(count)]
        add(
            f"pv_mutex_{number:02d}",
            "pv_design",
            "PV 互斥方案",
            f"{count} 个进程",
            {
                "semaphores": {"MUTEX": 1},
                "processes": processes,
                "description": f"{count} 个进程各进入临界区一次，MUTEX 初值为 1。每个进程必须且只能使用一次：ENTER、P(MUTEX)、V(MUTEX)、EXIT。填写各进程的执行顺序，用逗号分隔。ENTER 表示进入临界区，EXIT 表示离开。",
                "security_checks": [
                    dict(label=f"{p['name']} 的操作顺序", format="pv_operations")
                    for p in processes
                ],
            },
            "P、V 为原子操作，采用先减/加计数及 FIFO 阻塞唤醒语义。每个进程顺序执行自己的操作，进程之间可任意交错；阻塞者不能继续，已结束者不再执行。核验所有有限交错，必须保证临界区至多一个进程、退出者确已进入、所有进程最终可完成且信号量恢复初值。通过限于给定次数和进程数，不证明无限循环下的公平性。",
            number,
        )

    for number, (capacity, initial, roles) in enumerate(
        [
            (1, 0, ["producer", "consumer"]),
            (2, 0, ["producer", "producer", "consumer"]),
            (2, 1, ["producer", "consumer", "consumer"]),
        ],
        1,
    ):
        processes = [
            {"name": chr(65 + i), "role": role} for i, role in enumerate(roles)
        ]
        role_text = "；".join(
            f"{p['name']} 为{'生产者' if p['role']=='producer' else '消费者'}，操作集合："
            + ", ".join(sorted(required_operations(p["role"])))
            for p in processes
        )
        add(
            f"pv_buffer_{number:02d}",
            "pv_design",
            "PV 生产者消费者",
            f"容量 {capacity}、初始 {initial} 项",
            {
                "capacity": capacity,
                "items": initial,
                "semaphores": {
                    "MUTEX": 1,
                    "EMPTY": capacity - initial,
                    "FULL": initial,
                },
                "processes": processes,
                "description": f"缓冲区容量 {capacity}，初始已有 {initial} 项；MUTEX=1，EMPTY={capacity-initial}，FULL={initial}。每个进程仅完成一次生产或消费，操作集合中的每项恰好使用一次，填写顺序。"
                + role_text,
                "security_checks": [
                    dict(label=f"{p['name']} 的操作顺序", format="pv_operations")
                    for p in processes
                ],
            },
            "ENTER/EXIT 表示进入/离开共享缓冲区临界区，PUT 增加一项，GET 取走一项；PUT/GET 必须在本进程临界区内执行。P、V 采用原子计数及 FIFO 阻塞唤醒语义。核验给定有限任务的所有交错：互斥、缓冲区上下界、无死锁，并在全部完成时核对资源计数。可接受不同安全操作顺序；通过不证明任意进程数、无限循环或饥饿自由。",
            number,
        )
    for number, count in enumerate([3, 4, 5], 1):
        processes = [
            {"name": chr(65 + i), "role": "philosopher", "forks": [i, (i + 1) % count]}
            for i in range(count)
        ]
        add(
            f"pv_dining_{number:02d}",
            "pv_design",
            "PV 哲学家进餐",
            f"{count} 人圆桌",
            {
                "semaphores": {f"F{i}": 1 for i in range(count)},
                "processes": processes,
                "description": f"{count} 位哲学家围坐，叉子 F0 至 F{count-1} 各用一个初值为 1 的信号量表示。每人进餐一次。"
                + "；".join(
                    f"{p['name']} 需要 F{p['forks'][0]} 和 F{p['forks'][1]}，操作集合："
                    + ", ".join(sorted(required_operations(p["role"], p.get("forks"))))
                    for p in processes
                )
                + "。每项恰好一次，用逗号分隔；集合列举不规定执行顺序。",
                "security_checks": [
                    dict(label=f"{p['name']} 的操作顺序", format="pv_operations")
                    for p in processes
                ],
            },
            "P/V 为原子操作，阻塞与唤醒采用 FIFO。P 成功或阻塞后被唤醒才获得叉子，V 只能释放自己持有的叉子。EAT 时必须同时持有左右两把叉子；每个人最后归还两把叉子。核验给定人数、每人一次进餐的所有执行交错，拒绝死锁及非法用叉，接受不同正确取放顺序。操作集合固定为两次 P、一次 EAT、两次 V，不支持增加服务生或条件分支；本题不证明无限重复进餐时无饥饿。",
            number,
        )
    return result


def required_operations(role, forks=None):
    if role == "philosopher":
        first, second = sorted(forks)
        return [f"P(F{first})", f"P(F{second})", "EAT", f"V(F{second})", f"V(F{first})"]
    if role == "mutex":
        return ["P(MUTEX)", "ENTER", "EXIT", "V(MUTEX)"]
    source, target, action = (
        ("EMPTY", "FULL", "PUT") if role == "producer" else ("FULL", "EMPTY", "GET")
    )
    return [
        f"P({source})",
        "P(MUTEX)",
        "ENTER",
        action,
        "EXIT",
        "V(MUTEX)",
        f"V({target})",
    ]


def solve_pv(exercise):
    p = exercise["parameters"]
    if exercise["kind"] == "pv_design":
        values = [
            ",".join(required_operations(process["role"], process.get("forks")))
            for process in p["processes"]
        ]
    else:
        value, queue, values = p["initial"], [], []
        for name, op in p["events"]:
            outcome = "继续执行"
            if name in queue:
                outcome = "不可执行"
            elif op == "P":
                value -= 1
                if value < 0:
                    queue.append(name)
                    outcome = "阻塞"
            else:
                value += 1
                if value <= 0:
                    outcome = "唤醒 " + queue.pop(0)
            values.extend([str(value), ",".join(queue) or "无", outcome])
    return {
        "security_trace": [
            {"checkpoint": check["label"], "value": value}
            for check, value in zip(p["security_checks"], values)
        ],
        "explanation": exercise["rules"],
    }


def verify_interleavings(parameters, programs):
    names = [p["name"] for p in parameters["processes"]]
    sem_names = list(parameters["semaphores"])
    dining = parameters["processes"][0]["role"] == "philosopher"
    initial = (
        tuple(0 for _ in names),
        tuple(parameters["semaphores"].values()),
        tuple(() for _ in sem_names),
        -1,
        parameters.get("items", 0),
        tuple(-1 for _ in sem_names) if dining else (),
    )
    pending = deque([(initial, [])])
    seen = {initial}
    while pending:
        state, path = pending.popleft()
        positions, counts, queues, holder, items, owners = state
        blocked = {process for queue in queues for process in queue}
        ready = [
            i
            for i in range(len(names))
            if i not in blocked and positions[i] < len(programs[i])
        ]
        finished = (
            all(positions[i] == len(programs[i]) for i in range(len(names)))
            and not blocked
        )
        if finished:
            expected = {name: 1 for name in sem_names} if dining else {"MUTEX": 1}
            if "capacity" in parameters:
                expected.update(EMPTY=parameters["capacity"] - items, FULL=items)
            if (
                holder != -1
                or any(owner != -1 for owner in owners)
                or any(counts[j] != expected[name] for j, name in enumerate(sem_names))
            ):
                return {
                    "process": path[-1][0],
                    "code": "pv_safety",
                    "reason": "全部完成后临界区或信号量未恢复一致状态。",
                    "path": path,
                }
            continue
        if not ready:
            return {
                "process": min(blocked),
                "code": "pv_deadlock",
                "reason": "未完成进程全部阻塞，出现死锁。",
                "path": path,
            }
        for i in ready:
            op = programs[i][positions[i]]
            next_positions, next_counts = list(positions), list(counts)
            next_queues = [list(queue) for queue in queues]
            next_holder, next_items = holder, items
            next_owners = list(owners)
            next_positions[i] += 1
            reason = None
            if op.startswith(("P(", "V(")):
                j = sem_names.index(op[2:-1])
                if op[0] == "P":
                    next_counts[j] -= 1
                    if next_counts[j] < 0:
                        next_queues[j].append(i)
                    elif dining:
                        next_owners[j] = i
                else:
                    if dining and owners[j] != i:
                        reason = f"{names[i]} 释放了自己没有持有的叉子 {sem_names[j]}。"
                    else:
                        next_counts[j] += 1
                        if dining:
                            next_owners[j] = -1
                        if next_counts[j] <= 0:
                            awakened = next_queues[j].pop(0)
                            if dining:
                                next_owners[j] = awakened
            elif op == "EAT":
                if any(
                    owners[sem_names.index(f"F{fork}")] != i
                    for fork in parameters["processes"][i]["forks"]
                ):
                    reason = f"{names[i]} 没有同时持有左右两把叉子就开始进餐。"
            elif op == "ENTER":
                if holder != -1:
                    reason = f"{names[i]} 进入时 {names[holder]} 仍在临界区。"
                else:
                    next_holder = i
            elif op == "EXIT":
                if holder != i:
                    reason = f"{names[i]} 尚未进入自己的临界区就退出。"
                else:
                    next_holder = -1
            else:
                if holder != i:
                    reason = f"{names[i]} 在临界区外操作共享缓冲区。"
                else:
                    next_items += 1 if op == "PUT" else -1
                    if not 0 <= next_items <= parameters["capacity"]:
                        reason = "缓冲区溢出或从空缓冲区取数据。"
            next_path = path + [(i, op)]
            if reason:
                return {
                    "process": i,
                    "code": "pv_safety",
                    "reason": reason,
                    "path": next_path,
                }
            next_state = (
                tuple(next_positions),
                tuple(next_counts),
                tuple(tuple(queue) for queue in next_queues),
                next_holder,
                next_items,
                tuple(next_owners),
            )
            if next_state not in seen:
                seen.add(next_state)
                if len(seen) > 50000:
                    raise ValueError(
                        "当前方案的交错状态超出核验上限，请简化后重试；未计为通过。"
                    )
                pending.append((next_state, next_path))
    return None


def evaluate_pv(exercise, rows, labels):
    p, error = exercise["parameters"], None
    if exercise["kind"] == "pv_trace":
        for index, (row, expected, check) in enumerate(
            zip(rows, solve_pv(exercise)["security_trace"], p["security_checks"])
        ):
            value = row["value"].strip()
            if check["format"] == "text":
                matches = bool(re.fullmatch(r"-?\d{1,10}", value)) and int(
                    value
                ) == int(expected["value"])
            elif check["format"] == "pv_queue":
                matches = re.split(r"[,，\s]+", value.upper()) == expected[
                    "value"
                ].split(",")
            else:
                matches = value == expected["value"]
            if not matches:
                error = (
                    index,
                    "wrong_pv" if value else "incomplete",
                    f"请核对“{check['label']}”；阻塞者不能继续，唤醒后不重复执行 P。",
                )
                break
    else:
        programs = []
        for index, (row, process) in enumerate(zip(rows, p["processes"])):
            value = row["value"].strip()
            ops = re.sub(r"\s+", "", value.upper()).replace("，", ",").split(",")
            required = required_operations(process["role"], process.get("forks"))
            if len(ops) != len(required) or set(ops) != set(required):
                error = (
                    index,
                    "wrong_pv" if value else "incomplete",
                    "操作必须来自本进程的给定集合，每项恰好一次，用逗号分隔。",
                )
                break
            programs.append(ops)
        if error is None:
            failure = verify_interleavings(p, programs)
            if failure:
                path = " → ".join(
                    f"{p['processes'][i]['name']}:{op}" for i, op in failure["path"]
                )
                error = (
                    failure["process"],
                    failure["code"],
                    failure["reason"] + " 一条反例交错：" + path,
                )
    first = (
        None
        if error is None
        else {
            "step": error[0] + 1,
            "field": "value",
            "error_code": error[1],
            "label": labels[error[1]],
            "message": error[2],
            "possible_cause": None,
        }
    )
    # 方案按多个进程联合核验，反例触发行之前的进程行不因此获得正确判定。
    statuses = (
        ["correct"] * len(rows)
        if first is None
        else [
            "error" if i + 1 == first["step"] else "pending" for i in range(len(rows))
        ]
    )
    if exercise["kind"] == "pv_trace" and first:
        statuses = [
            "correct" if i + 1 < first["step"] else status
            for i, status in enumerate(statuses)
        ]
    return {"passed": first is None, "first_error": first, "row_statuses": statuses}
