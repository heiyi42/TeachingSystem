from __future__ import annotations

import os
import subprocess
import selectors
import time
from uuid import uuid4
import tempfile
from pathlib import Path
from threading import BoundedSemaphore

from .learning_courses import course_chapters
from .learning_c_advanced import (
    TASKS,
    advanced_cases,
    advanced_solution,
    advanced_driver,
)

_PROGRAM_SLOTS = BoundedSemaphore(2)


class ProgramRunnerUnavailable(RuntimeError):
    pass


def build_program_exercises():
    result = {}
    for number, (task, title, chapter) in enumerate(
        [
            ("sum", "从 1 到 n 求和", 4),
            ("squares", "从 1 到 n 的平方和", 4),
            ("even", "不超过 n 的正偶数和", 4),
            ("max", "数组最大值", 6),
            ("min", "数组最小值", 6),
            ("positive", "数组正数计数", 6),
            ("length", "字符串长度", 7),
            ("digits", "字符串数字计数", 7),
            ("vowels", "字符串元音计数", 7),
        ],
        1,
    ):
        key = f"c_program_{number:02d}"
        section = next(
            c for c in course_chapters("C_program") if c["number"] == chapter
        )
        array = task in {"max", "min", "positive"}
        string = task in {"length", "digits", "vowels"}
        result[key] = {
            "id": key,
            "subject_id": "C_program",
            "subject_name": "C 语言",
            "chapter_id": section["id"],
            "chapter_title": section["title"],
            "title": f"完整程序 · {title}",
            "algorithm": (
                "字符串程序" if string else "数组程序" if array else "循环程序"
            ),
            "family_id": (
                "c_program_string"
                if string
                else "c_program_array" if array else "c_program_loop"
            ),
            "kind": "c_program",
            "difficulty": (
                "基础" if number <= 3 or task in {"length", "digits"} else "进阶"
            ),
            "parameters": {
                "program_task": task,
                "checkpoints": ["完整 C 程序"],
                "description": (
                    "输入一行长度为 0 至 1000 的可打印 ASCII 字符（可含空格），以换行或 EOF 结束；不计行末换行。输出"
                    + {
                        "length": "字符总数。",
                        "digits": "数字 0 至 9 的字符个数。",
                        "vowels": "英文字母 a、e、i、o、u 的个数，不区分大小写。",
                    }[task]
                    if string
                    else (
                        (
                            "输入 n（1≤n≤100），再输入 n 个范围为 [-1000,1000] 的整数；输出其中"
                            + {
                                "max": "最大值。",
                                "min": "最小值。",
                                "positive": "严格大于 0 的元素个数。",
                            }[task]
                        )
                        if array
                        else "输入整数 n（0≤n≤1000），输出"
                        + {
                            "sum": "1 到 n 的整数和。",
                            "squares": "1 到 n 的平方和。",
                            "even": "1 到 n 中正偶数的和。",
                        }.get(task, "")
                    )
                ),
                "starter": "#include <stdio.h>\nint main(void) {\n    /* 在这里完成输入、计算和输出 */\n    return 0;\n}\n",
                "code": "",
                "family": "program",
            },
            "rules": "提交包含 main 的单个 C11 程序，使用标准输入输出。只输出结果整数，可有空白。每个用例独立运行，墙钟限时 5 秒、CPU 限时 3 秒、输出 64 KiB；运行内存限 64 MiB、进程数限 16，禁止联网和访问个人文件。通过表示本次测试集通过，不代表所有输入均正确。",
            "training_tags": [
                "compile_error",
                "wrong_output",
                "runtime_error",
                "time_limit",
            ],
        }
    return result


def program_cases(exercise):
    task = exercise["parameters"]["program_task"]
    if task in TASKS:
        return advanced_cases(task)
    if task in {"length", "digits", "vowels"}:
        lines = [
            "",
            " ",
            "a b 09",
            "AEIOUaeiou",
            "0123456789",
            "xyz!?",
            "a" * 1000,
            "9 " * 500,
        ]
        return [
            (
                line + ending,
                str(
                    len(line)
                    if task == "length"
                    else sum(
                        "0" <= c <= "9" if task == "digits" else c.lower() in "aeiou"
                        for c in line
                    )
                ),
            )
            for line in lines
            for ending in ("\n", "")
        ]
    if task in {"max", "min", "positive"}:
        arrays = [
            [0],
            [-7],
            [-8, -3, -9],
            [4, 4, 4],
            [1000, -1000, 0],
            list(range(-50, 50)),
        ]
        return [
            (
                str(len(a)) + "\n" + " ".join(map(str, a)) + "\n",
                str(
                    sum(x > 0 for x in a)
                    if task == "positive"
                    else (max if task == "max" else min)(a)
                ),
            )
            for a in arrays
        ]

    def expected(n):
        if task == "sum":
            return n * (n + 1) // 2
        if task == "squares":
            return n * (n + 1) * (2 * n + 1) // 6
        return (n // 2) * (n // 2 + 1)

    return [(f"{n}\n", str(expected(n))) for n in (0, 1, 2, 7, 20, 101, 1000)]


def program_solution(exercise):
    task = exercise["parameters"]["program_task"]
    if task in TASKS:
        files = advanced_solution(task)
        return {
            "code": files[0],
            "files": [
                {"name": name, "code": code}
                for name, code in zip(exercise["parameters"]["source_files"], files)
            ],
            "explanation": "固定驱动检查接口、边界和产物；接受符合约定的不同实现，结论限于测试集。",
        }
    if task in {"length", "digits", "vowels"}:
        condition = {
            "length": "1",
            "digits": "c >= '0' && c <= '9'",
            "vowels": "c=='a'||c=='e'||c=='i'||c=='o'||c=='u'||c=='A'||c=='E'||c=='I'||c=='O'||c=='U'",
        }[task]
        body = (
            "int c, count = 0;\n    while ((c = getchar()) != EOF && c != '\\n') {\n        if ("
            + condition
            + ') count++;\n    }\n    printf("%d\\n", count);'
        )
    elif task == "positive":
        body = 'int n, x, count = 0;\n    if (scanf("%d", &n) != 1) return 1;\n    for (int i = 0; i < n; i++) {\n        if (scanf("%d", &x) != 1) return 1;\n        if (x > 0) count++;\n    }\n    printf("%d\\n", count);'
    elif task in {"max", "min"}:
        body = (
            'int n, x, best;\n    if (scanf("%d%d", &n, &best) != 2) return 1;\n    for (int i = 1; i < n; i++) {\n        if (scanf("%d", &x) != 1) return 1;\n        if (x '
            + (">" if task == "max" else "<")
            + ' best) best = x;\n    }\n    printf("%d\\n", best);'
        )
    else:
        term = {"sum": "i", "squares": "i * i", "even": "(i % 2 == 0 ? i : 0)"}[task]
        body = (
            'int n;\n    if (scanf("%d", &n) != 1) return 1;\n    long long result = 0;\n    for (long long i = 1; i <= n; i++) result += '
            + term
            + ';\n    printf("%lld\\n", result);'
        )
    return {
        "code": "#include <stdio.h>\nint main(void) {\n    "
        + body
        + "\n    return 0;\n}\n",
        "explanation": "参考实现仅是一种写法；判定比较实际标准输出，接受不同正确实现。失败用例不是代码首错行。",
    }


def _run(
    arguments,
    mounts,
    *,
    stdin="",
    timeout=5,
    memory="64m",
    writable=False,
    workdir="/tmp",
    output_tmpfs=False,
    user="65534:65534",
):
    # 每个挂载可单独指定只读，实验输入不与可写产物混用。
    name = "teaching-c-" + uuid4().hex
    image = os.getenv("C_PROGRAM_IMAGE", "teaching-c-runner:1")
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=" + user,
        "--memory=" + memory,
        "--memory-swap=" + memory,
        "--cpus=1",
        "--pids-limit=16",
        "--ulimit=cpu=3:3",
        "--ulimit=fsize=" + ("8388608:8388608" if writable else "65536:65536"),
        "--ulimit=nofile=64:64",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
        "--workdir=" + workdir,
        "--log-driver=none",
        "-i",
    ]
    if output_tmpfs:
        command += ["--tmpfs=/output:rw,noexec,nosuid,size=16m,mode=0777"]
    for mount in mounts:
        source, target = mount[:2]
        readonly = mount[2] if len(mount) == 3 else not writable
        command += [
            "--mount",
            f"type=bind,source={source},target={target}"
            + (",readonly" if readonly else ""),
        ]
    command += [image, *arguments]
    output = [bytearray(), bytearray()]
    timed_out = False
    exceeded = False
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise ProgramRunnerUnavailable(
            "Docker 不可用，请启动 Docker 并准备评测镜像。"
        ) from error
    try:
        process.stdin.write(stdin.encode())
        process.stdin.close()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            for index, stream in enumerate((process.stdout, process.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, index)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                for key, _ in selector.select(timeout=0.1):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer = output[key.data]
                    remaining = 65536 - len(buffer)
                    buffer.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        exceeded = True
                        break
                if exceeded:
                    break
        if timed_out or exceeded:
            # 超限后不再读取输出，结束可能阻塞在管道写入的 CLI，容器在 finally 中删除。
            process.kill()
        try:
            status = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            status = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
    if status == 125:
        raise ProgramRunnerUnavailable(
            "Docker 评测环境不可用，请确认 Docker 已启动并已构建 teaching-c-runner:1 镜像。"
        )
    return (
        (status if not exceeded else -1),
        output[0].decode(errors="replace"),
        output[1].decode(errors="replace"),
        timed_out,
    )


def evaluate_program(exercise, code, labels):
    if not _PROGRAM_SLOTS.acquire(blocking=False):
        raise ProgramRunnerUnavailable("程序评测正在忙，请稍后重试。")
    try:
        with tempfile.TemporaryDirectory(prefix="learning-c-") as temp:
            folder = Path(temp).resolve()
            folder.chmod(0o777)
            parameters = exercise["parameters"]
            task = parameters["program_task"]
            names = parameters.get("source_files", ["main.c"])
            codes = [code] if isinstance(code, str) else [row["code"] for row in code]
            if len(codes) != len(names):
                raise ValueError("源码文件数量不符")
            source_dir = folder / "source"
            source_dir.mkdir()
            source_dir.chmod(0o755)
            for name, content in zip(names, codes):
                source = source_dir / name
                source.write_text(content)
                source.chmod(0o444)
            driver = advanced_driver(task) if task in TASKS else None
            if driver:
                (source_dir / "driver.c").write_text(driver)
            compile_files = (
                ["driver.c"]
                if driver
                else [name for name in names if name.endswith(".c")]
            )
            output_dir = folder / "output"
            output_dir.mkdir()
            output_dir.chmod(0o777)
            binary = output_dir / "program"
            status, _, diagnostics, timeout = _run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-O0",
                    *["/source/" + name for name in compile_files],
                    "-o",
                    "/output/program",
                ],
                [(source_dir, "/source", True), (output_dir, "/output", False)],
                timeout=20,
                memory="256m",
                writable=True,
            )
            diagnostics = diagnostics.replace("/source/", "")[:4000]
            tests = []
            error_code = "compile_error" if status or timeout else None
            message = "编译未通过。请查看编译反馈。" if error_code else ""
            if not error_code:
                for index, (input_text, expected) in enumerate(
                    program_cases(exercise), 1
                ):
                    status, output, stderr, timeout = _run(
                        ["/program"], [(binary, "/program")], stdin=input_text
                    )
                    passed = (
                        not timeout and status == 0 and output.split() == [expected]
                    )
                    tests.append(
                        {
                            "number": index,
                            "input": input_text,
                            "expected": expected,
                            "output": output[:2000],
                            "passed": passed,
                            "status": (
                                "超时"
                                if timeout
                                else (
                                    "运行异常或输出超限"
                                    if status
                                    else "通过" if passed else "输出不符"
                                )
                            ),
                        }
                    )
                    if not passed and error_code is None:
                        error_code = (
                            "time_limit"
                            if timeout
                            else "runtime_error" if status else "wrong_output"
                        )
                        message = f"用例 {index} 未通过（{tests[-1]['status']}）；这不是代码首错行。"
                    if timeout or status:
                        break
            return {
                "passed": error_code is None,
                "row_statuses": ["error" if error_code else "correct"] * len(names),
                "first_error": (
                    {
                        "step": 1,
                        "field": "code",
                        "error_code": error_code,
                        "label": labels[error_code],
                        "message": message,
                        "possible_cause": None,
                    }
                    if error_code
                    else None
                ),
                "program_feedback": {"compiler": diagnostics, "tests": tests},
            }
    finally:
        _PROGRAM_SLOTS.release()
