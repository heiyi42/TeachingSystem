import tempfile
import unittest
from pathlib import Path

from flask import Flask

from webapp_core.learning_routes import learning_blueprint
from webapp_core.learning_service import LearningService
from webapp_core.learning_store import LearningStore
from webapp_core.problem_tutoring_service import ProblemTutoringService


class LearningCourseTests(unittest.TestCase):
    def test_catalog_exercises_and_old_records_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            service = LearningService(LearningStore(Path(directory) / 'learning.sqlite3'), ProblemTutoringService())
            app = Flask(__name__)
            app.testing = True
            app.register_blueprint(learning_blueprint(service))
            app.register_blueprint(learning_blueprint(service, demo=True))
            client = app.test_client()
            response = client.get('/api/learning/courses')
            self.assertEqual(response.status_code, 200)
            courses = {course['id']: course for course in response.json['courses']}
            self.assertEqual(set(courses), {'C_program', 'operating_systems', 'cybersec_lab'})
            self.assertEqual([len(courses[key]['chapters']) for key in courses], [16, 17, 7])
            self.assertEqual([courses[key]['exercise_count'] for key in courses], [33, 106, 39])
            exercises = client.get('/api/learning/exercises').json['exercises']
            for course in courses.values():
                for chapter in course['chapters']:
                    self.assertEqual(chapter['exercise_count'], sum(
                        row['subject_id'] == course['id'] and row['chapter_id'] == chapter['id']
                        for row in exercises))
            for exercise in exercises:
                course = courses[exercise['subject_id']]
                chapter = next(c for c in course['chapters'] if c['id'] == exercise['chapter_id'])
                self.assertEqual(exercise['chapter_title'], chapter['title'])
                self.assertEqual(exercise['subject_name'], course['name'])
            original = service.start('lru_01')
            # 持久化旧记录只存 exercise_id；课程字段在读取时由题目定义补全。
            self.assertNotIn('subject_id', service.store.get(original['id']))
            restored = LearningService(LearningStore(service.store.path), ProblemTutoringService())
            self.assertEqual(restored.get(original['id'])['exercise']['chapter_id'], 'operating_systems_08')
            self.assertEqual(restored.progress()['records'][0]['exercise']['subject_id'], 'operating_systems')
            demo = client.post('/api/learning/demo', json={}).json
            self.assertEqual(client.get(f"/api/learning-demo/{demo['demo_id']}/courses").json, response.json)
