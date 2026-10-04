from __future__ import annotations

from webapp_core.learning.learning_courses import course_chapters

KINDS = {"process_states", "thread_resources", "file_allocation"}


def build_os_foundation_exercises():
    result = {}

    def add(kind, number, title, chapter, parameters, checks, rules, family):
        section = next(
            c for c in course_chapters("operating_systems") if c["number"] == chapter
        )
        key = f"{kind}_{number:02d}"
        parameters["security_checks"] = checks
        result[key] = dict(
            id=key,
            subject_id="operating_systems",
            subject_name="操作系统",
            chapter_id=section["id"],
            chapter_title=section["title"],
            title=title,
            algorithm=family,
            kind=kind,
            difficulty="基础" if number == 1 else "进阶",
            parameters=parameters,
            rules=rules,
            training_tags=["wrong_" + kind],
        )

    scenarios = [
        ("就绪", ["调度", "等待I/O", "时间片到期", "I/O完成", "调度", "退出"]),
        (
            "运行",
            ["时间片到期", "I/O完成", "调度", "等待I/O", "调度", "I/O完成", "调度"],
        ),
        ("阻塞", ["调度", "I/O完成", "时间片到期", "调度", "退出", "I/O完成"]),
    ]
    for n, (initial, events) in enumerate(scenarios, 1):
        add(
            "process_states",
            n,
            f"进程状态 · 事件序列 {n}",
            3,
            dict(
                initial_state=initial,
                events=events,
                description=f"单个进程初始为{initial}。依次发生：" + " → ".join(events),
            ),
            [
                dict(
                    label=f"{i}：{e}后状态",
                    format="choice",
                    options=["就绪", "运行", "阻塞", "终止"],
                )
                for i, e in enumerate(events, 1)
            ],
            "只用就绪、运行、阻塞、终止四态。调度：就绪→运行；时间片到期：运行→就绪；等待I/O：运行→阻塞；I/O完成：阻塞→就绪；退出：运行→终止。其余事件不适用，状态保持不变；终止态不再转换。",
            "进程状态转换",
        )
    resources = [
        ["代码段", "栈", "程序计数器", "堆", "全局变量", "寄存器"],
        ["文件描述符表", "栈", "线程局部变量", "地址空间", "程序计数器", "全局变量"],
        ["寄存器", "堆", "文件描述符表", "线程局部变量", "代码段", "栈"],
    ]
    for n, items in enumerate(resources, 1):
        add(
            "thread_resources",
            n,
            f"线程资源 · 同一进程 {n}",
            4,
            dict(
                resources_to_classify=items,
                description="同一进程内两个 POSIX 线程，判断以下资源的归属："
                + "、".join(items),
            ),
            [
                dict(label=x, format="choice", options=["进程共享", "线程独有"])
                for x in items
            ],
            "按 POSIX 线程基本模型：地址空间、代码、堆、全局变量和文件描述符表属于进程共享；每个线程的栈、寄存器、程序计数器与线程局部变量独有。共享资源可能仍需同步；线程能通过指针访问其他线程栈，不改变栈的逻辑归属。",
            "线程资源归属",
        )
    for policy in ["连续", "链接", "索引"]:
        for variant, (size, blocks, offsets) in enumerate(
            [
                (1024, [7, 8, 9], [0, 1023, 1024, 3071, 3072]),
                (512, [2, 3], [511, 512, 1023, 1024]),
                (4096, [11], [0, 4095, 4096]),
            ],
            1,
        ):
            if policy != "连续":
                blocks = [b * 2 for b in blocks]
            number = ["连续", "链接", "索引"].index(policy) * 3 + variant
            add(
                "file_allocation",
                number,
                f"{policy}分配 · 文件块定位 {variant}",
                12,
                dict(
                    allocation_policy=policy,
                    block_size=size,
                    blocks=blocks,
                    file_size=size * len(blocks),
                    offsets=offsets,
                    description=f"块大小{size}字节，文件长度{size*len(blocks)}字节。"
                    + (
                        f"连续区起始块{blocks[0]}。"
                        if policy == "连续"
                        else f"文件数据块顺序{blocks}，"
                        + (
                            "每块的 next 指向下一块，末块为 EOF。"
                            if policy == "链接"
                            else "索引块为50，条目按逻辑块号排列。"
                        )
                    )
                    + f"访问字节偏移依次为{offsets}。",
                ),
                [
                    dict(
                        label=f"偏移{x}：{label}",
                        format="choice" if label == "结果" else "text",
                        **({"options": ["有效", "越界"]} if label == "结果" else {}),
                    )
                    for x in offsets
                    for label in ["结果", "物理块号", "块内偏移", "定位读取块数"]
                ],
                "从0编号，偏移>=文件长度为越界；后三项均填-1。有效偏移用整除和取余定位。统计无缓存情况下每次独立访问读入的块数，包括目标数据块：连续1，链接从首块顺序走到目标共逻辑块号+1，索引为索引块+数据块共2。忽略目录/inode读取；链接指针单独存放，不扣减给定数据容量。",
                policy + "文件分配",
            )
    return result


def solve_os_foundation(exercise):
    p = exercise["parameters"]
    kind = exercise["kind"]
    values = []
    if kind == "process_states":
        state = p["initial_state"]
        transitions = {
            ("就绪", "调度"): "运行",
            ("运行", "时间片到期"): "就绪",
            ("运行", "等待I/O"): "阻塞",
            ("阻塞", "I/O完成"): "就绪",
            ("运行", "退出"): "终止",
        }
        for event in p["events"]:
            state = transitions.get((state, event), state)
            values.append(state)
    elif kind == "thread_resources":
        shared = {"代码段", "堆", "全局变量", "文件描述符表", "地址空间"}
        values = [
            "进程共享" if x in shared else "线程独有"
            for x in p["resources_to_classify"]
        ]
    else:
        for offset in p["offsets"]:
            if offset >= p["file_size"]:
                values += ["越界", "-1", "-1", "-1"]
                continue
            block, inner = divmod(offset, p["block_size"])
            reads = (
                1
                if p["allocation_policy"] == "连续"
                else block + 1 if p["allocation_policy"] == "链接" else 2
            )
            values += ["有效", str(p["blocks"][block]), str(inner), str(reads)]
    return {
        "security_trace": [
            dict(checkpoint=c["label"], value=v, explanation=exercise["rules"])
            for c, v in zip(p["security_checks"], values)
        ],
        "explanation": exercise["rules"],
    }
