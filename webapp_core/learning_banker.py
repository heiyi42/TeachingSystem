from __future__ import annotations

import re


def evaluate_banker(exercise: dict, rows: list[dict], solution: dict) -> dict:
    processes = exercise['parameters']['banker_processes']
    dimension = len(exercise['parameters']['available'])
    count = len(processes)
    parsed = []
    for index, row in enumerate(rows):
        field = 'need' if index < count else 'work' if index < count * 2 else None
        value = row[field] if field else ''
        vector = None
        if value:
            parts = re.split(r'[,，\s]+', value)
            if len(parts) != dimension or any(not re.fullmatch(r'[0-9]{1,8}', part) for part in parts):
                raise ValueError(f'第 {index + 1} 行资源向量须填写 {dimension} 个非负整数')
            vector = [int(part) for part in parts]
        process = row['process'].upper()
        if count <= index < count * 2 and process and process not in solution['need']:
            raise ValueError(f'第 {index + 1} 行请选择题目中的进程')
        parsed.append((vector, process))
    error = None

    def fail(index, field, code, message):
        nonlocal error
        error = {'step': index + 1, 'field': field, 'error_code': code, 'message': message, 'possible_cause': None}

    for index, process in enumerate(processes):
        need = parsed[index][0]
        if need is None:
            fail(index, 'need', 'incomplete', '请补全当前进程的 Need 向量。')
        elif need != list(solution['need'][process['name']]):
            fail(index, 'need', 'wrong_need', 'Need 的每个分量应为 Max 减 Allocation，请按资源列逐项计算。')
        if error:
            break
    work = list(exercise['parameters']['available'])
    finished = set()
    stopped = False
    if error is None:
        for index in range(count, count * 2):
            vector, name = parsed[index]
            eligible = [p['name'] for p in processes if p['name'] not in finished
                        and all(n <= w for n, w in zip(solution['need'][p['name']], work))]
            if not name and vector is None:
                if eligible:
                    fail(index, 'process', 'incomplete', '仍有可以完成的进程，请继续填写安全性检查过程。')
                stopped = True
            elif stopped or len(finished) == count:
                fail(index, 'process', 'extra_segment', '检查过程已经结束，请将剩余过程行留空。')
            elif not name or vector is None:
                fail(index, 'process' if not name else 'work', 'incomplete', '请同时填写本步进程和释放资源后的 Work。')
            elif name in finished:
                fail(index, 'process', 'repeated_process', '该进程已完成，不能再次释放其 Allocation。')
            elif name not in eligible:
                fail(index, 'process', 'ineligible_process', f'当前 Work 为 {work}，所选进程的 Need 有分量超过 Work，暂时不能完成。')
            else:
                allocation = solution['allocation'][name]
                expected = [w + a for w, a in zip(work, allocation)]
                if vector != expected:
                    fail(index, 'work', 'wrong_work', '完成进程后应把 Allocation 加回 Work；不要加 Max 或 Need。')
                else:
                    work = expected
                    finished.add(name)
            if error:
                break
    if error is None:
        verdict = rows[-1]['verdict']
        safe = len(finished) == count
        if not verdict:
            fail(len(rows) - 1, 'verdict', 'incomplete', '请选择安全性结论。')
        elif (verdict == 'safe') != safe:
            fail(len(rows) - 1, 'verdict', 'wrong_safety', '所有进程均能完成才是安全状态；部分完成后无法继续仍是不安全状态。')
    step = error['step'] if error else None
    return {'passed': error is None, 'first_error': error,
            'row_statuses': ['correct' if step is None or i < step else 'error' if i == step else 'pending'
                             for i in range(1, len(rows) + 1)]}
