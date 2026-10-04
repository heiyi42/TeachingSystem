from __future__ import annotations

import copy
import re
from webapp_core.learning.learning_exercises import EXERCISES


def authoring_templates(exercise):
    return [
        copy.deepcopy(e)
        for e in EXERCISES.values()
        if e["kind"] == exercise["kind"]
        and e["chapter_id"] == exercise["chapter_id"]
        and e["subject_id"] == exercise["subject_id"]
    ]


def validate_training_edit(original, data):
    if (
        not isinstance(data.get("title"), str)
        or not 1 <= len(data["title"].strip()) <= 120
    ):
        raise ValueError("题目标题须为 1 至 120 字符")
    if data.get("id") != original["id"]:
        raise ValueError("题目编号不能修改")
    templates = [original, *authoring_templates(original)]
    for template in templates:
        if any(
            data.get(k) != template.get(k)
            for k in template
            if k not in {"id", "title", "parameters"}
        ) or set(data) != set(template):
            continue
        p = data.get("parameters")
        if not isinstance(p, dict) or set(p) != set(template["parameters"]):
            continue
        free = (
            {"frames", "sequence"}
            if data["kind"] == "page_replacement"
            else {"processes", "quantum"} if data["kind"] == "cpu_scheduling" else set()
        )
        if any(p[k] != v for k, v in template["parameters"].items() if k not in free):
            continue
        if data["kind"] == "page_replacement":
            if type(p["frames"]) is not int or not 1 <= p["frames"] <= 8:
                raise ValueError("页框数须为1至8的整数")
            if (
                not isinstance(p["sequence"], list)
                or not 1 <= len(p["sequence"]) <= 40
                or any(type(x) is not int or not 0 <= x <= 999 for x in p["sequence"])
            ):
                raise ValueError("访问序列须为1至40个0至999的整数")
        elif data["kind"] == "cpu_scheduling":
            processes = p["processes"]
            if not isinstance(processes, list) or not 1 <= len(processes) <= 8:
                raise ValueError("请填写1至8个进程")
            names = []
            for row in processes:
                if (
                    not isinstance(row, dict)
                    or set(row) != {"name", "arrival", "service"}
                    or not isinstance(row["name"], str)
                    or not re.fullmatch(r"P[1-9][0-9]?", row["name"])
                ):
                    raise ValueError(
                        "进程名称须为P1至P99，字段须包含名称、到达和服务时间"
                    )
                if (
                    type(row["arrival"]) is not int
                    or not 0 <= row["arrival"] <= 100
                    or type(row["service"]) is not int
                    or not 1 <= row["service"] <= 20
                ):
                    raise ValueError("到达时间须为0至100，服务时间须为1至20的整数")
                names.append(row["name"])
            if len(names) != len(set(names)):
                raise ValueError("进程名称不能重复")
            if data["algorithm"] == "RR":
                if type(p["quantum"]) is not int or not 1 <= p["quantum"] <= 20:
                    raise ValueError("时间片须为1至20的整数")
                if (
                    sum(
                        (r["service"] + p["quantum"] - 1) // p["quantum"]
                        for r in processes
                    )
                    > 100
                ):
                    raise ValueError("调度段数不能超过100")
            elif p["quantum"] is not None:
                raise ValueError("仅时间片轮转设置时间片")
        return
    raise ValueError("请选择本题支持的核验模板；规则、代码和检查点不能脱离模板单独修改")
