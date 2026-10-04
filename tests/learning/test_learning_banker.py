import itertools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from webapp_core.learning.learning_exercises import EXERCISES
from webapp_core.learning.learning_routes import learning_blueprint
from webapp_core.learning.learning_service import LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.chat.problem_tutoring_service import ProblemTutoringService


# 手工计算的 Need、完成顺序和释放后的 Work，不由被测求解器生成。
GOLD = {
    'banker_01': ([[1, 1], [1, 1], [2, 1]], ['P1', 'P0', 'P2'], [[1, 2], [2, 2], [3, 3]], True),
    'banker_02': ([[0, 1], [1, 1], [1, 2]], ['P0', 'P1', 'P2'], [[1, 1], [1, 2], [2, 3]], True),
    'banker_03': ([[1, 0], [3, 1], [2, 1]], ['P0'], [[2, 0]], False),
    'banker_04': ([[0, 0, 0], [1, 0, 1], [1, 1, 1]], ['P0', 'P1', 'P2'], [[1, 0, 1], [1, 1, 1], [2, 2, 1]], True),
    'banker_05': ([[1, 0], [0, 1], [1, 1]], [], [], False),
}


def answer(need, order, work, safe):
    count = len(need)
    rows = [{'need': '', 'process': '', 'work': '', 'verdict': ''} for _ in range(2 * count + 1)]
    for i, vector in enumerate(need): rows[i]['need'] = ', '.join(map(str, vector))
    for i, (name, vector) in enumerate(zip(order, work)):
        rows[count + i].update(process=name, work=', '.join(map(str, vector)))
    rows[-1]['verdict'] = 'safe' if safe else 'unsafe'
    return rows


class BankerTrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = LearningService(LearningStore(Path(self.temp.name) / 'learning.sqlite3'), ProblemTutoringService())
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(learning_blueprint(self.service))
        self.client = app.test_client()

    def test_fixed_gold_safe_and_unsafe(self):
        for key, gold in GOLD.items():
            with self.subTest(key=key):
                attempt = self.service.start(key)
                self.assertIsNone(attempt['solution'])
                self.assertNotIn('need', attempt['exercise']['parameters'])
                parameters = attempt['exercise']['parameters']
                total = [a + sum(p['allocation'][i] for p in parameters['banker_processes'])
                         for i, a in enumerate(parameters['available'])]
                for process in parameters['banker_processes']:
                    self.assertTrue(all(m <= t for m, t in zip(process['maximum'], total)))
                result = self.service.submit(attempt['id'], answer(*gold))
                self.assertEqual(result['status'], 'passed')
                solution = self.service.solution(attempt['id'])['solution']
                self.assertEqual(solution['safe'], gold[-1])
                self.assertEqual(list(solution['need'].values()), [tuple(n) for n in gold[0]])

    def test_all_multiple_safe_sequences(self):
        # 初始 P0/P1 可选，只有 P0 释放后 P2 才可选；其中三条排列合法。
        for order in itertools.permutations(['P0', 'P1', 'P2']):
            with self.subTest(order=order):
                work = [1, 1]
                after = []
                for name in order:
                    allocation = {'P0':[1,0], 'P1':[0,1], 'P2':[1,1]}[name]
                    work = [work[i] + allocation[i] for i in range(2)]
                    after.append(work)
                attempt = self.service.start('banker_01')
                result = self.service.submit(attempt['id'], answer(GOLD['banker_01'][0], order, after, True))
                self.assertEqual(result['status'] == 'passed', order in [('P0','P1','P2'), ('P0','P2','P1'), ('P1','P0','P2')])

    def test_diagnosis_correction_restore_hints_retest(self):
        attempt = self.service.start('banker_01')
        rows = answer(*GOLD['banker_01'])
        rows[4]['work'] = '2, 3'
        result = self.service.submit(attempt['id'], rows)
        self.assertEqual(result['evaluation']['first_error']['step'], 5)
        self.assertEqual(result['evaluation']['first_error']['error_code'], 'wrong_work')
        self.assertEqual(result['evaluation']['row_statuses'], ['correct']*4 + ['error', 'pending', 'pending'])
        self.service.hint(attempt['id'])
        hint = self.service.hint(attempt['id'])['hint']
        self.assertEqual(hint['level'], 2)
        self.assertIn('[1, 2]', hint['text'])  # 学生先完成 P1，不能提示参考路径的 [2,1]。
        with self.assertRaises(ValueError): self.service.hint(attempt['id'], 6)
        rows = answer(*GOLD['banker_01'])
        self.service.save_draft(attempt['id'], rows)
        restored = LearningService(LearningStore(self.service.store.path), ProblemTutoringService())
        self.assertEqual(restored.get(attempt['id'])['draft'], rows)
        self.assertEqual(self.service.submit(attempt['id'], rows)['status'], 'passed')
        retest = self.service.start(None, attempt['id'])
        self.assertEqual(retest['exercise']['id'], 'banker_02')
        self.assertTrue(self.service.submit(retest['id'], answer(*GOLD['banker_02']))['first_unassisted_pass'])
        self.assertEqual(self.service.progress()['summary']['independent_retest_passes'], 1)

    def test_first_error_types(self):
        mutations = [
            (0, 'need', '2,1', 'wrong_need'),
            (3, 'process', 'P2', 'ineligible_process'),
            (4, 'process', 'P1', 'repeated_process'),
            (3, 'work', '1,3', 'wrong_work'),
            (6, 'verdict', 'unsafe', 'wrong_safety'),
            (3, 'process', '', 'incomplete'),
        ]
        for index, field, value, code in mutations:
            with self.subTest(code=code):
                attempt = self.service.start('banker_01')
                rows = answer(*GOLD['banker_01'])
                rows[index][field] = value
                result = self.service.submit(attempt['id'], rows)
                self.assertEqual(result['evaluation']['first_error']['step'], index + 1)
                self.assertEqual(result['evaluation']['first_error']['error_code'], code)

    def test_unsafe_requires_complete_prefix_and_no_holes(self):
        for key, index, value, code in [('banker_03', 3, '', 'incomplete'), ('banker_03', 4, 'P1', 'ineligible_process'), ('banker_03', 5, 'P1', 'extra_segment'), ('banker_05', 6, 'safe', 'wrong_safety')]:
            attempt = self.service.start(key)
            rows = answer(*GOLD[key])
            if index == 3: rows[index].update(process='', work='')
            elif index in {4,5}: rows[index].update(process=value, work='2, 1')
            else: rows[index]['verdict'] = value
            result = self.service.submit(attempt['id'], rows)
            self.assertEqual(result['evaluation']['first_error']['error_code'], code)
        attempt = self.service.start('banker_01')
        rows = answer(*GOLD['banker_01'])
        rows[4].update(process='', work='')
        self.assertEqual(self.service.submit(attempt['id'], rows)['evaluation']['first_error']['step'], 5)

    def test_api_rejects_invalid_input_without_recording_submission(self):
        for field, value in [('need','1'), ('need','-1,1'), ('need','1.0,1'), ('process','P9'), ('verdict','unknown')]:
            attempt = self.service.start('banker_01')
            rows = answer(*GOLD['banker_01'])
            index = 0 if field == 'need' else 3 if field == 'process' else 6
            rows[index][field] = value
            response = self.client.post(f"/api/learning/attempts/{attempt['id']}/submit", json={'rows':rows})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.service.get(attempt['id'])['submission_count'], 0)
        with self.assertRaises(ValueError): self.service.save_draft(attempt['id'], rows[:-1])

    def test_held_out_four_process_three_resource_cases(self):
        # 独立题：初始只能 P0/P1 完成，随后 P2/P3；提高 P2 的需求后双方相互等待资源。
        base = dict(EXERCISES['banker_01'])
        base.update(id='held_out_banker', parameters={
            'available':[1,0,1], 'resources':['A','B','C'],
            'banker_processes':[
                {'name':'P0','allocation':[0,1,0],'maximum':[1,1,1]},
                {'name':'P1','allocation':[1,0,0],'maximum':[2,0,1]},
                {'name':'P2','allocation':[0,0,1],'maximum':[2,1,2]},
                {'name':'P3','allocation':[1,0,1],'maximum':[3,1,3]},
            ]})
        for safe in [True, False]:
            if not safe: base['parameters']['banker_processes'][2]['maximum'] = [3,1,3]
            need = [[1,0,1],[1,0,1],[2,1,1] if safe else [3,1,2],[2,1,2]]
            order = ['P1','P0'] + (['P2','P3'] if safe else [])
            work = [[2,0,1],[2,1,1]] + ([[2,1,2],[3,1,3]] if safe else [])
            with patch.dict(EXERCISES, {base['id']:base}):
                attempt = self.service.start(base['id'])
                self.assertEqual(self.service.submit(attempt['id'], answer(need,order,work,safe))['status'], 'passed')
                self.assertEqual(self.service.solution(attempt['id'])['solution']['safe'], safe)

    def test_solver_input_boundaries_and_solution_assistance(self):
        solver = self.service.solver
        for available, allocation, maximum in [([],{},{}), ([1],{'P0':[1]}, {'P0':[0]}), ([1,0],{'P0':[1]}, {'P0':[2]}), ([1],{'P0':[-1]}, {'P0':[2]})]:
            with self.assertRaises(ValueError): solver.solve_banker(available,allocation,maximum)
        attempt = self.service.start('banker_01')
        self.service.solution(attempt['id'])
        self.assertFalse(self.service.submit(attempt['id'], answer(*GOLD['banker_01']))['first_unassisted_pass'])


if __name__ == '__main__':
    unittest.main()
