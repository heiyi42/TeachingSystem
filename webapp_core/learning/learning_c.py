from __future__ import annotations

import ast
import re

from webapp_core.learning.learning_courses import course_chapters


def build_c_exercises() -> dict[str, dict]:
    exercises = {}
    chapters = {chapter['number']: chapter for chapter in course_chapters('C_program')}
    cases = [
        ('loop', '循环累加', 4, [(1, 3, 0), (2, 5, 1), (3, 6, -2)]),
        ('pointer', '数组与指针', 8, [([3, 7, 12, 18], 0), ([5, 9, 14, 20], 1), ([-4, 2, 8, 15], 0)]),
        ('call', '指针参数', 5, [(4, 3), (-2, 5), (7, -3)]),
        ('repair', '返回语句订正', 14, [(2, 3), (3, 4), (4, 5)]),
    ]
    for family, label, chapter_number, variants in cases:
        for number, values in enumerate(variants, 1):
            parameters = {'family': family, 'values': values}
            if family == 'loop':
                start, end, initial = values
                code = f'int s = {initial};\nfor (int i = {start}; i <= {end}; i++) {{\n    s += i;\n}}\nprintf("%d", s);'
                checkpoints = [f'i = {i}，执行 s += i 后的 s' for i in range(start, end + 1)] + ['printf 输出']
                description = '逐次跟踪循环体执行后的 s，最后填写输出。'
            elif family == 'pointer':
                array, offset = values
                code = f'int a[4] = {{{", ".join(map(str, array))}}};\nint *p = &a[{offset}];\np++;\n*p += 2;\nprintf("%d", *p);'
                checkpoints = ['初始化后 p 指向的数组下标', 'p++ 后 p 指向的数组下标', '*p += 2 后该元素的值', 'printf 输出']
                description = '填写数组下标与元素值，不填写机器地址。'
            elif family == 'call':
                initial, delta = values
                code = f'void change(int *p) {{\n    *p += {delta};\n}}\nint main(void) {{\n    int x = {initial};\n    change(&x);\n    printf("%d", x);\n    return 0;\n}}'
                checkpoints = ['调用 change 前的 x', 'change 执行 *p += 常量后的 x', '返回 main 后的 x', 'printf 输出']
                description = '跟踪指针参数对调用者变量 x 的影响。'
            else:
                factor, bias = values
                code = f'int transform(int x) {{\n    return x * {factor} - {bias};\n}}'
                checkpoints = ['返回语句']
                description = f'函数应先将 x 乘以 {factor}，再加 {bias}。请修复返回语句。输入 x 的范围为 -10 至 10，核验覆盖范围内全部 21 个整数。'
                parameters['starter'] = f'return x * {factor} - {bias};'
            parameters.update(code=code, checkpoints=checkpoints, description=description)
            exercise_id = f'c_{family}_{number:02d}'
            chapter = chapters[chapter_number]
            exercises[exercise_id] = {
                'id': exercise_id, 'subject_id': 'C_program', 'subject_name': 'C 语言',
                'chapter_id': chapter['id'], 'chapter_title': chapter['title'],
                'title': f'{label} · 练习 {number}', 'algorithm': label, 'family_id': f'c_{family}',
                'kind': 'c_repair' if family == 'repair' else 'c_trace',
                'difficulty': '基础', 'parameters': parameters,
                'training_tags': ['wrong_code'] if family == 'repair' else ['wrong_value'],
                'rules': ('只修改 return 语句；支持变量 x、十进制整数、括号和 + - * / %，不支持函数调用、赋值或其他语句。按 32 位有符号整数及 C 的向零截断规则核验，溢出或除零不通过。'
                          if family == 'repair' else '按检查点顺序填写有符号十进制整数。代码已置于合法上下文并包含 stdio.h；按 C11/C17 语义分析。'),
            }
    return exercises


def solve_c(exercise: dict) -> dict:
    p = exercise['parameters']
    if p['family'] == 'repair':
        factor, bias = p['values']
        return {'code': f'return x * {factor} + {bias};', 'explanation': '返回值须依照题目要求先乘后加。等价表达式也可通过；核验结论仅适用于题目声明的输入范围。'}
    if p['family'] == 'loop':
        start, end, value = p['values']
        values = []
        for i in range(start, end + 1):
            value += i
            values.append(value)
        values.append(value)
    elif p['family'] == 'pointer':
        array, offset = p['values']
        value = array[offset + 1] + 2
        values = [offset, offset + 1, value, value]
    else:
        initial, delta = p['values']
        values = [initial, initial + delta, initial + delta, initial + delta]
    return {'c_trace': [{'checkpoint': label, 'value': value} for label, value in zip(p['checkpoints'], values)]}


def check_c_return(code: str, factor: int, bias: int) -> tuple[str | None, str]:
    match = re.fullmatch(r'return\s+([x0-9+*/%()\s-]+);', code)
    if not match or re.search(r"\+\+|--|0[xX]", code):
        return 'invalid_code', '请填写一条 return 语句，只使用 x、十进制整数、括号和 + - * / %。'
    expression = match.group(1).strip()
    # AST 只用于解析受限语法，不执行提交的代码，也不调用 eval。
    try:
        tree = ast.parse(expression, mode='eval')
        nodes = list(ast.walk(tree))
        allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.UAdd, ast.USub)
        if len(nodes) > 80 or any(not isinstance(node, allowed) for node in nodes):
            raise ValueError('表达式包含未支持的运算或过于复杂')
        if any(isinstance(node, ast.Constant) and type(node.value) is not int for node in nodes):
            raise ValueError('只支持十进制整数')
        if any(isinstance(node, ast.Name) and node.id != 'x' for node in nodes):
            raise ValueError('只支持变量 x')
    except (SyntaxError, ValueError, RecursionError) as error:
        return 'invalid_code', f'返回语句不符合本题支持的语法：{str(error).splitlines()[0]}'

    def calculate(node, x):
        if isinstance(node, ast.Constant): value = node.value
        elif isinstance(node, ast.Name): value = x
        elif isinstance(node, ast.UnaryOp):
            operand = calculate(node.operand, x)
            value = -operand if isinstance(node.op, ast.USub) else operand
        else:
            left, right = calculate(node.left, x), calculate(node.right, x)
            if isinstance(node.op, ast.Add): value = left + right
            elif isinstance(node.op, ast.Sub): value = left - right
            elif isinstance(node.op, ast.Mult): value = left * right
            else:
                if right == 0: raise ValueError('除数为 0')
                if left == -2147483648 and right == -1: raise ValueError('有符号整数除法溢出')
                quotient = (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1)
                value = quotient if isinstance(node.op, ast.Div) else left - quotient * right
        if not -2147483648 <= value <= 2147483647:
            raise ValueError('32 位有符号整数溢出')
        return value

    for x in range(-10, 11):
        try:
            actual = calculate(tree.body, x)
        except ValueError as error:
            return 'wrong_code', f'输入 x = {x} 时出现{error}。这表示该输入下无法得到有效结果，不代表已定位代码首错行。'
        expected = x * factor + bias
        if actual != expected:
            return 'wrong_code', f'输入 x = {x} 时得到 {actual}，期望 {expected}。请检查返回表达式；这是失败用例，不是代码首错行。'
    return None, '声明范围内全部 21 个输入核验通过。'
