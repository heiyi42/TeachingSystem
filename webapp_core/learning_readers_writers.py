from __future__ import annotations

from .learning_courses import course_chapters


POLICIES = {"reader": "读者优先", "writer": "写者优先", "fifo": "公平排队"}


def build_readers_writers_exercises():
    chapter = course_chapters("operating_systems")[5]
    scenarios = [
        (
            "新读者与等待写者",
            [
                ("R1", "arrive"),
                ("W1", "arrive"),
                ("R2", "arrive"),
                ("R1", "leave"),
                ("R2", "leave"),
                ("W1", "leave"),
            ],
        ),
        (
            "写者退出后的选择",
            [
                ("W1", "arrive"),
                ("R1", "arrive"),
                ("W2", "arrive"),
                ("R2", "arrive"),
                ("W1", "leave"),
            ],
        ),
        (
            "连续读者与排队边界",
            [
                ("W1", "arrive"),
                ("R1", "arrive"),
                ("R2", "arrive"),
                ("W2", "arrive"),
                ("R3", "arrive"),
                ("W1", "leave"),
                ("R1", "leave"),
                ("R2", "leave"),
            ],
        ),
    ]
    rules = {
        "reader": "无正在写入的进程时，新读者可进入，即使已有写者等待。写者退出时优先放行所有等待读者；只有没有活动或等待读者时，才放行最早等候的写者。持续到达的读者可能使写者饥饿。",
        "writer": "只要有写者等待，后到读者必须排队；正在读取的读者继续至退出，不被抢占。访问者全部退出后，优先放行最早等候的写者；没有等待写者时才一起放行等待读者。持续到达的写者可能使读者饥饿。",
        "fifo": "按请求到达顺序排队；队首是读者时，允许队首连续的一组读者并发进入，但不得越过排队的写者。队首写者须等所有活动读者退出后独占访问。公平指本题的 FIFO 准入顺序，假设已进入者最终退出，不证明任意实现无饥饿。",
    }
    result = {}
    for policy, label in POLICIES.items():
        for number, (title, events) in enumerate(scenarios, 1):
            key = f"rw_{policy}_{number:02d}"
            result[key] = {
                "id": key,
                "subject_id": "operating_systems",
                "subject_name": "操作系统",
                "chapter_id": chapter["id"],
                "chapter_title": chapter["title"],
                "kind": "readers_writers",
                "algorithm": f"读者写者 · {label}",
                "title": f"{label} · {title}",
                "difficulty": "基础" if number < 3 else "进阶",
                "parameters": {
                    "policy": policy,
                    "events": events,
                    "description": f"采用{label}，初始无人访问、无人等待。R 为读者，W 为写者，每个进程最多请求一次。按以下顺序逐项处理："
                    + "；".join(
                        f"{i}. {name} {'请求进入' if event == 'arrive' else '尝试退出'}"
                        for i, (name, event) in enumerate(events, 1)
                    ),
                    "security_checks": [
                        dict(
                            label=f"事件 {i}：{label}",
                            format=fmt,
                            **(
                                {"options": ["进入", "等待", "退出", "不可执行"]}
                                if fmt == "choice"
                                else {}
                            ),
                        )
                        for i in range(1, len(events) + 1)
                        for label, fmt in [
                            ("正在访问", "rw_active"),
                            ("等待队列", "rw_queue"),
                            ("本次操作结果", "choice"),
                        ]
                    ],
                },
                "rules": "多个读者可并发，写者必须独占。"
                + rules[policy]
                + "每个事件结束后立即按策略放行可进入者，再填写状态。等待队列始终按到达先后列出，优先选择可从队列中部取出进程。正在访问的读者排列顺序不限；队列顺序必须准确。无人填无，多人用逗号分隔。未进入者不能退出，重复请求或重复退出标为不可执行且状态不变。题目只推演给定事件前缀，末尾有活动或等待者不代表死锁；不核验具体 PV 程序。",
                "training_tags": ["wrong_readers_writers"],
            }
    return result


def reader_writer_trace(parameters):
    policy = parameters["policy"]
    if policy not in POLICIES:
        raise ValueError("未知的读者写者策略")
    active, waiting, arrived, trace = [], [], set(), []
    for name, event in parameters["events"]:
        if event not in {"arrive", "leave"}:
            raise ValueError("未知的读者写者事件")
        valid = name not in arrived if event == "arrive" else name in active
        if valid:
            if event == "arrive":
                arrived.add(name)
                waiting.append(name)
            else:
                active.remove(name)
            if not any(actor.startswith("W") for actor in active):
                if policy == "fifo":
                    while waiting and waiting[0].startswith("R"):
                        active.append(waiting.pop(0))
                    if not active and waiting:
                        active.append(waiting.pop(0))
                else:
                    writers = [actor for actor in waiting if actor.startswith("W")]
                    readers = [actor for actor in waiting if actor.startswith("R")]
                    if policy == "writer" and writers:
                        if not active:
                            active.append(writers[0])
                            waiting.remove(writers[0])
                    elif readers:
                        active.extend(readers)
                        waiting = [actor for actor in waiting if actor not in readers]
                    elif not active and writers:
                        active.append(writers[0])
                        waiting.remove(writers[0])
            outcome = (
                "退出" if event == "leave" else "进入" if name in active else "等待"
            )
        else:
            outcome = "不可执行"
        trace.append(
            {"active": list(active), "waiting": list(waiting), "outcome": outcome}
        )
    return trace
