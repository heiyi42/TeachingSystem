import copy
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from scripts.evaluate_learning_diagnosis import run_evaluation
from webapp_core.learning_exercises import EXERCISES
from webapp_core.learning_routes import learning_blueprint
from webapp_core.learning_service import LearningService
from webapp_core.learning_store import LearningStore
from webapp_core.problem_tutoring_service import ProblemTutoringService


FIXTURE = Path(__file__).parent / 'fixtures' / 'learning_opt_v1.json'


def rows(frames, victims):
    return [{'frames': ', '.join(map(str, memory)),
             'event': 'hit' if victim == 'hit' else 'fault',
             'evicted': '—' if victim in {'hit', None} else str(victim)}
            for memory, victim in zip(frames, victims)]


# 手工参考轨迹，包含空页框、命中、后续访问比较、并列与单页框。
GOLD = {
    'opt_01': rows([[1],[1,2],[1,2,3],[1,2,4],[1,2,4],[1,2,4],[2,3,4]], [None,None,None,3,'hit','hit',1]),
    'opt_02': rows([[4],[4,5],[5,6],[6,7],[6,7],[6,7]], [None,None,4,5,'hit','hit']),
    'opt_03': rows([[0],[0,1],[0,1],[1,2],[1,2],[1,3],[1,3]], [None,None,'hit',0,'hit',2,'hit']),
    'opt_04': rows([[1],[1,2],[1,2],[1,2,3],[1,2,3]], [None,None,'hit',None,'hit']),
    'opt_05': rows([[2],[2],[3],[2],[4]], [None,'hit',2,3,2]),
}
ALT_02 = rows([[4],[4,5],[4,6],[6,7],[6,7],[6,7]], [None,None,5,4,'hit','hit'])


class OPTTrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = LearningService(LearningStore(Path(self.temp.name) / 'records.sqlite3'), ProblemTutoringService())
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(learning_blueprint(self.service))
        self.client = app.test_client()

    def test_all_training_gold_and_solution_privacy(self):
        expected_faults = {'opt_01':5,'opt_02':4,'opt_03':4,'opt_04':3,'opt_05':4}
        for key, gold in GOLD.items():
            with self.subTest(key=key):
                attempt = self.service.start(key)
                self.assertIsNone(attempt['solution'])
                self.assertNotIn('trace', attempt['exercise']['parameters'])
                self.assertEqual(self.service.submit(attempt['id'], gold)['status'], 'passed')
                solution = self.service.solution(attempt['id'])['solution']
                self.assertEqual(solution['faults'], expected_faults[key])
                self.assertEqual(solution['hits'], len(gold) - expected_faults[key])
                for expected, actual in zip(gold, solution['trace']):
                    self.assertEqual(set(actual['frames_after']), {int(v) for v in expected['frames'].split(',')})
                    self.assertEqual(actual['event'], expected['event'])
                    self.assertEqual(actual['evicted'], None if expected['evicted'] == '—' else int(expected['evicted']))

    def test_tied_eviction_alternative_path_corrects_and_retests(self):
        attempt = self.service.start('opt_02')
        wrong = copy.deepcopy(ALT_02)
        wrong[3]['evicted'] = '6'
        result = self.service.submit(attempt['id'], wrong)
        self.assertEqual(result['evaluation']['first_error']['step'], 4)
        self.assertEqual(result['evaluation']['first_error']['error_code'], 'wrong_victim')
        self.assertEqual(result['evaluation']['row_statuses'], ['correct']*3 + ['error','pending','pending'])
        self.service.hint(attempt['id'])
        hint = self.service.hint(attempt['id'])['hint']
        self.assertEqual(hint['level'], 2)
        self.assertIn('此前页框为 [4, 6]', hint['text'])
        self.assertIn('4：不再访问', hint['text'])
        self.assertIn('6：第 5 次', hint['text'])
        with self.assertRaises(ValueError): self.service.hint(attempt['id'], 5)
        self.service.save_draft(attempt['id'], ALT_02)
        restored = LearningService(LearningStore(self.service.store.path), ProblemTutoringService())
        self.assertEqual(restored.get(attempt['id'])['draft'], ALT_02)
        result = self.service.submit(attempt['id'], ALT_02)
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(result['first_unassisted_pass'])
        retest = self.service.start(None, attempt['id'])
        self.assertEqual(retest['exercise']['id'], 'opt_01')
        self.assertTrue(self.service.submit(retest['id'], GOLD['opt_01'])['first_unassisted_pass'])
        self.assertEqual(self.service.progress()['summary']['independent_retest_passes'], 1)

    def test_other_legal_tie_is_accepted_but_inconsistent_frames_are_not(self):
        attempt = self.service.start('opt_02')
        wrong = copy.deepcopy(ALT_02)
        wrong[2]['frames'] = '5,6'
        result = self.service.submit(attempt['id'], wrong)
        self.assertEqual(result['evaluation']['first_error']['error_code'], 'wrong_frames')
        self.assertEqual(result['evaluation']['first_error']['step'], 3)
        correct = copy.deepcopy(ALT_02)
        correct[2]['frames'] = '06，04'
        self.assertEqual(self.service.submit(attempt['id'], correct)['status'], 'passed')

    def test_initial_hint_blank_answers_and_invalid_payloads(self):
        attempt = self.service.start('opt_03')
        self.service.hint(attempt['id'])
        self.assertIn('尚未装入页面', self.service.hint(attempt['id'])['hint']['text'])
        self.assertEqual(self.service.submit(attempt['id'], attempt['draft'])['evaluation']['first_error']['error_code'], 'incomplete')
        for value in ['1,1', '0,1,2', '-1', 'x', '1.5']:
            invalid = copy.deepcopy(GOLD['opt_03'])
            invalid[0]['frames'] = value
            response = self.client.post(f"/api/learning/attempts/{attempt['id']}/submit", json={'rows':invalid})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.service.get(attempt['id'])['submission_count'], 1)
        with self.assertRaises(ValueError): self.service.submit(attempt['id'], GOLD['opt_03'][:-1])

    def test_finite_future_distances_and_zero_victim(self):
        solution = self.service.solver.solve_page_replacement('OPT', [1,2,3,4,1,2,3], 3)['result']
        self.assertEqual(solution['trace'][3]['next_uses'], {1:5,2:6,3:7})
        self.assertEqual(solution['trace'][3]['optimal_victims'], [3])
        zero = self.service.solver.solve_page_replacement('OPT', [0,1,2], 2, evictions=[None,None,0])['result']
        self.assertEqual(zero['trace'][2]['evicted'], 0)
        for algorithm, choices in [('FIFO',[None,None,None]), ('OPT',[None])]:
            self.assertEqual(self.service.solver.solve_page_replacement(algorithm,[0,1,2],2,evictions=choices)['status'], 'skipped')

    def test_held_out_reference_diagnosis(self):
        result = run_evaluation(FIXTURE)
        self.assertEqual(result['problem_count'], 4)
        self.assertEqual(result['training_overlap'], 0)
        self.assertEqual(result['summary']['case_count'], 42)
        self.assertEqual(result['summary']['exact_match']['numerator'], 42)
        self.assertEqual(result['summary']['unexpected_exceptions'], 0)
        self.assertEqual(result['by_algorithm']['OPT']['false_rejection']['numerator'], 0)


if __name__ == '__main__':
    unittest.main()
