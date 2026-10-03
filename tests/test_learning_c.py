import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from webapp_core.learning_c import check_c_return, solve_c
from webapp_core.learning_exercises import EXERCISES
from webapp_core.learning_routes import learning_blueprint
from webapp_core.learning_service import LearningService
from webapp_core.learning_store import LearningStore
from webapp_core.problem_tutoring_service import ProblemTutoringService


GOLD = {
    'c_loop_01': [1, 3, 6, 6], 'c_loop_02': [3, 6, 10, 15, 15], 'c_loop_03': [1, 5, 10, 16, 16],
    'c_pointer_01': [0, 1, 9, 9], 'c_pointer_02': [1, 2, 16, 16], 'c_pointer_03': [0, 1, 4, 4],
    'c_call_01': [4, 7, 7, 7], 'c_call_02': [-2, 3, 3, 3], 'c_call_03': [7, 4, 4, 4],
}


class CTrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = LearningService(LearningStore(Path(self.temp.name) / 'records.sqlite3'), ProblemTutoringService())
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(learning_blueprint(self.service))
        self.client = app.test_client()

    def test_trace_correction_hint_retest_and_restore(self):
        attempt = self.service.start('c_loop_01')
        self.assertIsNone(attempt['solution'])
        rows = [{'value': str(x)} for x in [1, 4, 7, 7]]
        result = self.client.post(f"/api/learning/attempts/{attempt['id']}/submit", json={'rows': rows})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['evaluation']['first_error']['step'], 2)
        self.assertEqual(result.json['evaluation']['row_statuses'], ['correct','error','pending','pending'])
        self.assertEqual(self.service.hint(attempt['id'])['hint']['level'], 1)
        self.assertEqual(self.service.hint(attempt['id'])['hint']['level'], 2)
        rows = [{'value': str(x)} for x in GOLD['c_loop_01']]
        self.service.save_draft(attempt['id'], rows)
        restored = LearningService(LearningStore(self.service.store.path), ProblemTutoringService())
        self.assertEqual(restored.get(attempt['id'])['draft'], rows)
        passed = self.service.submit(attempt['id'], rows)
        self.assertFalse(passed['first_unassisted_pass'])
        retest = self.service.start(None, attempt['id'])
        self.assertEqual(retest['exercise']['id'], 'c_loop_02')
        result = self.service.submit(retest['id'], [{'value':str(x)} for x in GOLD['c_loop_02']])
        self.assertTrue(result['first_unassisted_pass'])
        self.assertEqual(self.service.progress()['summary']['independent_retest_passes'], 1)

    def test_all_trace_gold_and_initial_solution_privacy(self):
        for key, values in GOLD.items():
            with self.subTest(key=key):
                exercise = EXERCISES[key]
                self.assertEqual([r['value'] for r in solve_c(exercise)['c_trace']], values)
                attempt = self.service.start(key)
                self.assertIsNone(attempt['solution'])
                self.assertNotIn('c_trace', exercise['parameters'])
                result = self.service.submit(attempt['id'], [{'value':str(x)} for x in values])
                self.assertEqual(result['status'], 'passed')

    def test_trace_invalid_shape_and_signed_values(self):
        attempt = self.service.start('c_call_02')
        with self.assertRaises(ValueError): self.service.submit(attempt['id'], [{'value':'1'}])
        with self.assertRaises(ValueError): self.service.submit(attempt['id'], [{'value':'1.5'}]*4)
        self.assertEqual(self.service.get(attempt['id'])['submission_count'], 0)
        result = self.service.submit(attempt['id'], [{'value':v} for v in ['-02','+3','3','3']])
        self.assertTrue(result['first_unassisted_pass'])

    def test_repair_equivalence_hints_and_failure_evidence(self):
        attempt = self.service.start('c_repair_01')
        result = self.service.submit(attempt['id'], attempt['draft'])
        self.assertEqual(result['evaluation']['first_error']['error_code'], 'wrong_code')
        self.assertIn('失败用例', result['evaluation']['first_error']['message'])
        self.service.hint(attempt['id'])
        self.assertEqual(self.service.hint(attempt['id'])['hint']['level'], 2)
        result = self.service.submit(attempt['id'], [{'code':'return 3 + x + x;'}])
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(result['first_unassisted_pass'])
        retest = self.service.start(None, attempt['id'])
        self.assertEqual(retest['exercise']['id'], 'c_repair_02')
        passed = self.service.submit(retest['id'], [{'code':'return x * 3 + 4;'}])
        self.assertTrue(passed['first_unassisted_pass'])
        self.assertIn('code', self.service.solution(attempt['id'])['solution'])

    def test_expression_boundaries_and_unsupported_syntax(self):
        for code in ['return x++3;', 'return x--3;', 'return 0x10;', 'return x**2;', 'return x//2;', 'return abs(x);', 'return x; system("id");', 'return x = 3;', 'return x & 3;']:
            with self.subTest(code=code): self.assertEqual(check_c_return(code, 2, 3)[0], 'invalid_code')
        self.assertEqual(check_c_return('return x * 2 + 3 + (x / 11);', 2, 3)[0], None)
        self.assertEqual(check_c_return('return x * 2 + 3 + (x % 2 - (x - (x / 2) * 2));', 2, 3)[0], None)
        for code in ['return x / 0;', 'return 2147483647 + x;', 'return x * 2 - 3;']:
            with self.subTest(code=code): self.assertEqual(check_c_return(code, 2, 3)[0], 'wrong_code')
        attempt = self.service.start('c_repair_03')
        result = self.service.submit(attempt['id'], [{'code':'return abs(x);'}])
        self.assertEqual(result['submission_count'], 1)
        self.assertEqual(result['evaluation']['first_error']['error_code'], 'invalid_code')

    def test_solution_exposure_prevents_independent_pass(self):
        attempt = self.service.start('c_repair_01')
        self.service.solution(attempt['id'])
        result = self.service.submit(attempt['id'], [{'code':'return 2*x+3;'}])
        self.assertFalse(result['first_unassisted_pass'])

    def test_fixed_program_outputs_against_c_compiler(self):
        compiler = shutil.which('clang') or shutil.which('gcc')
        if not compiler: self.skipTest('C compiler unavailable')
        for key, values in GOLD.items():
            code = EXERCISES[key]['parameters']['code']
            if 'int main(' not in code: code = 'int main(void) {\n' + code + '\nreturn 0;\n}'
            path = Path(self.temp.name)
            (path / 'check.c').write_text('#include <stdio.h>\n'+code)
            subprocess.run([compiler, '-std=c11', str(path/'check.c'), '-o', str(path/'check')], check=True, capture_output=True, timeout=10)
            output = subprocess.run([str(path/'check')], check=True, capture_output=True, text=True, timeout=2).stdout
            self.assertEqual(output, str(values[-1]), key)
