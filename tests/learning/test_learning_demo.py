import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from webapp_core.learning.learning_routes import learning_blueprint
from webapp_core.learning.learning_service import LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.chat.problem_tutoring_service import ProblemTutoringService


class LearningDemoTests(unittest.TestCase):
    def test_demo_flow_isolated_from_practice_and_other_demo(self):
        rows = json.loads((Path(__file__).parents[2] / 'frontend/src/learningDemo.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            service = LearningService(LearningStore(Path(directory) / 'learning.sqlite3'), ProblemTutoringService())
            app = Flask(__name__)
            app.testing = True
            app.register_blueprint(learning_blueprint(service))
            app.register_blueprint(learning_blueprint(service, demo=True))
            client = app.test_client()
            original = service.start('fifo_01')
            before = service.progress()
            demo = client.post('/api/learning/demo', json={}).json
            base = f"/api/learning-demo/{demo['demo_id']}"
            attempt = f"{base}/attempts/{demo['attempt']['id']}"
            wrong = client.post(f'{attempt}/submit', json={'rows': rows['wrong']})
            self.assertEqual(wrong.status_code, 200)
            self.assertEqual(wrong.json['evaluation']['first_error']['step'], 5)
            for level in (1, 2):
                self.assertEqual(client.post(f'{attempt}/hint', json={}).json['hint']['level'], level)
            self.assertEqual(client.post(f'{attempt}/submit', json={'rows': rows['correct']}).json['status'], 'passed')
            retest = client.post(f'{base}/attempts', json={'parent_id': demo['attempt']['id']}).json
            self.assertEqual(retest['exercise']['id'], 'lru_02')
            result = client.post(f"{base}/attempts/{retest['id']}/submit", json={'rows': rows['retest']}).json
            self.assertTrue(result['first_unassisted_pass'])
            self.assertEqual(client.get(f'{base}/progress').json['summary']['independent_retest_passes'], 1)
            self.assertEqual(service.progress(), before)
            self.assertEqual(client.get(f"/api/learning/attempts/{demo['attempt']['id']}").status_code, 404)
            self.assertEqual(client.get(f"{base}/attempts/{original['id']}").status_code, 404)
            second = client.post('/api/learning/demo', json={}).json
            self.assertNotEqual(second['demo_id'], demo['demo_id'])
            other = f"/api/learning-demo/{second['demo_id']}"
            self.assertEqual(client.get(f"{other}/attempts/{demo['attempt']['id']}").status_code, 404)
            self.assertEqual(client.get(f'{other}/progress').json['summary']['submitted'], 0)
            self.assertEqual(client.get('/api/learning-demo/not-a-uuid/progress').status_code, 404)
            self.assertEqual(client.get('/api/learning-demo/00000000-0000-0000-0000-000000000000/progress').status_code, 404)
