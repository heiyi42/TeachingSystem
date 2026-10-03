"""核查当前固定题库；独立计算不调用被测规则求解器。"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_answer(row: dict) -> tuple[str, object]:
    question, answer = row['question'], row['answer']
    family = row['family_id']
    if row['subject_id'] == 'C_program' and row['problem_type'] != 'c_debug':
        compiler = shutil.which('clang') or shutil.which('gcc')
        if not compiler:
            raise RuntimeError('需要 C 编译器来核查固定输出题')
        code = question.split('：', 1)[1].rsplit('}', 1)[0] + '}' if 'int main(' in question else None
        if code is None:
            fragment = question.split('：', 1)[1]
            code = 'int main(void) { ' + fragment[:fragment.rfind(';') + 1] + ' return 0; }'
        with tempfile.TemporaryDirectory() as temp:
            source, binary = Path(temp) / 'answer.c', Path(temp) / 'answer'
            source.write_text('#include <stdio.h>\n' + code)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', str(source), '-o', str(binary)], check=True, capture_output=True, timeout=10)
            actual = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=2).stdout.strip()
        expected = re.search(r'输出\s*(.*?)。', answer).group(1)
        assert actual == expected, (actual, expected)
        return 'compiled_output', actual
    if family == 'sec_dh_calculation':
        params = {key: int(value) for key, value in re.findall(r'\b([pgab])\s*=\s*(\d+)', question)}
        p, g, a, b = (params[key] for key in 'pgab')
        actual = [pow(g, a, p), pow(g, b, p), pow(pow(g, b, p), a, p)]
        assert actual[2] == pow(actual[0], b, p)
        expected = [int(x) for x in re.findall(r'[ABK]\s*=\s*(\d+)', answer)]
        assert actual == expected, (actual, expected)
        return 'modular_arithmetic', actual
    if family.startswith('os_pages_'):
        sequence = [int(x) for x in re.search(r'序列为\s*([\d,]+)', question).group(1).split(',')]
        capacity = int(re.search(r'页框数为\s*(\d+)', question).group(1))
        memory, faults = [], 0
        for i, page in enumerate(sequence):
            if page in memory:
                if family.endswith('lru'):
                    memory.remove(page); memory.append(page)
                continue
            faults += 1
            if len(memory) == capacity:
                if family.endswith('opt'):
                    future = sequence[i+1:]
                    victim = max(memory, key=lambda x: future.index(x) if x in future else len(future))
                    memory.remove(victim)
                else:
                    memory.pop(0)
            memory.append(page)
        assert faults == int(re.search(r'为\s*(\d+)', answer).group(1))
        return 'independent_page_simulation', faults
    if family.startswith('os_schedule_'):
        special = {'os_003': [('P1', 0, 3), ('P2', 2, 6), ('P3', 4, 4)],
                   'os_004': [('P1', 0, 5), ('P2', 1, 3)],
                   'os_008': [('P1', 0, 8), ('P2', 1, 4), ('P3', 2, 2)]}
        processes = special.get(row['id'])
        if processes is None:
            processes = ast.literal_eval(re.search(r'\[.*?\]', question).group())
        remaining = {name: duration for name, _, duration in processes}
        arrivals = {name: arrival for name, arrival, _ in processes}
        pending = sorted(processes, key=lambda x: (x[1], int(x[0][1:])))
        ready, completion, timeline = deque(), {}, []
        t = 0
        while len(completion) < len(processes):
            while pending and pending[0][1] <= t:
                ready.append(pending.pop(0)[0])
            if not ready:
                t += 1
                continue
            if family.endswith('sjf'):
                name = min(ready, key=lambda n: (remaining[n], arrivals[n], int(n[1:])))
                ready.remove(name)
            else:
                name = ready.popleft()
            duration = min(2, remaining[name]) if family.endswith('rr') else remaining[name]
            timeline.append((name, t, t + duration))
            t += duration; remaining[name] -= duration
            while pending and pending[0][1] <= t:
                ready.append(pending.pop(0)[0])
            if remaining[name]: ready.append(name)
            else: completion[name] = t
        metrics = {n: (completion[n]-a, completion[n]-a-d) for n,a,d in processes}
        if family.endswith('rr'):
            expected = [(n,int(a),int(b)) for n,a,b in re.findall(r'(P\d+)\((\d+)-(\d+)\)', answer)]
            assert timeline == expected, (timeline, expected)
            if '{' in answer: assert completion == ast.literal_eval(re.search(r'\{.*\}', answer).group())
            else:
                for n,value in re.findall(r'(P\d+) 完成时间 (\d+)',answer): assert completion[n] == int(value)
        elif '{' in answer:
            expected = ast.literal_eval(re.search(r'\{.*\}', answer).group())
            assert metrics == expected, (metrics, expected)
        elif row['id'] == 'os_003':
            values = [list(completion.values()), [metrics[n][0] for n,_,_ in processes], [metrics[n][1] for n,_,_ in processes]]
            expected = [[int(n) for n in group.split('、')] for group in re.findall(r'(?:完成时间|周转时间|等待时间)为 ([\d、]+)', answer)]
            assert values == expected
        else:
            assert {n:v[1] for n,v in metrics.items()} == {n:int(v) for n,v in re.findall(r'(P\d+)=(\d+)',answer)}
        if not family.endswith('rr'):
            order = re.search(r'调度顺序为 (.*?)[；。]',answer).group(1).split(' -> ')
            assert [n for n,_,_ in timeline] == order
        return 'independent_schedule_simulation', {'timeline':timeline,'metrics':metrics}
    if family == 'os_banker_need':
        vectors = [ast.literal_eval('('+x+')') for x in re.findall(r'\(([^()]*)\)', question)]
        actual = tuple(a-b for a,b in zip(*vectors))
        assert actual == ast.literal_eval(re.findall(r'\([^()]*\)',answer)[-1])
        return 'vector_subtraction', actual
    if family == 'os_banker_safety':
        alloc_text,max_text = question.split('Allocation：')[1].split('Max：')
        parse = lambda text: {name: [int(x) for x in nums.split(',')] for name,nums in re.findall(r'(P\d+)\(([\d,]+)\)',text)}
        alloc,maximum = parse(alloc_text),parse(max_text)
        work = [int(x) for x in re.search(r'Available=\(([\d,]+)\)',question).group(1).split(',')]
        sequence = re.findall(r'P\d+', answer)
        assert set(sequence) == set(alloc) and len(sequence) == len(alloc)
        for name in sequence:
            assert all(m-a <= w for m,a,w in zip(maximum[name],alloc[name],work))
            work = [w+a for w,a in zip(work,alloc[name])]
        return 'safe_sequence_constraints', sequence
    return 'conceptual_review_teacher_pending', None


def audit() -> dict:
    bank = ROOT / 'data/tutoring_question_bank/questions.jsonl'
    rows = [json.loads(line) for line in bank.read_text().splitlines() if line.strip()]
    ids = [name for row in rows for name in [row['id'], *row['aliases']]]
    assert len(ids) == len(set(ids)), '题号或别名冲突'
    assert len({(r['subject_id'],r['question'].strip()) for r in rows}) == len(rows), '存在重复题干'
    index = json.loads((bank.parent / 'questions.embedding_index.json').read_text())
    assert len(index['items']) == index['item_count'] == len(rows)
    entries = {item['id']:item for item in index['items']}
    assert set(entries) == {r['id'] for r in rows}
    labels = {'C_program':'C语言','operating_systems':'操作系统','cybersec_lab':'网络安全实验'}
    for row in rows:
        expected = '\n'.join([f"学科: {labels[row['subject_id']]}",f"题型: {row['problem_type']}",f"知识点: {' '.join(row['knowledge_points'])}",f"题目: {row['question'].strip()}"])
        assert entries[row['id']]['text'] == expected, row['id']
    results=[]
    for row in rows:
        try:
            method,actual = check_answer(row)
            results.append({'id':row['id'],'method':method,'actual':actual,'status':'pending_teacher' if actual is None else 'verified'})
        except Exception as error:
            results.append({'id':row['id'],'status':'failed','error':str(error)})
    return {'date':'2026-10-01','source_sha256':hashlib.sha256(bank.read_bytes()).hexdigest(),
            'records':len(rows),'legacy_ids':len(ids),'families':len({r['family_id'] for r in rows}),
            'counts':dict(Counter(r['status'] for r in results)),
            'scope':'固定题目答案核查；不执行任意输入；不证明解析逐句正确或教学效果，全部仍待教师复核。',
            'results':results}


if __name__ == '__main__':
    result = audit()
    output = ROOT / 'output/course_inventory/answer_audit.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result['counts'],ensure_ascii=False))
    for row in result['results']:
        if row['status']=='failed': print(row)
    raise SystemExit(bool(result['counts'].get('failed')))
