import unittest
from tests import test_learning_path as fixtures
from webapp_core.learning_courses import COURSES, course_chapters
from webapp_core.learning_curriculum import (
    CURRICULUM,
    OBJECTIVE_LINKS,
    chapter_curriculum,
    prerequisite_chain,
)
from webapp_core.learning_exercises import EXERCISES
from webapp_core.learning_path import LearningPathService
from webapp_core.learning_service import LearningService, LearningConflict
from webapp_core.learning_store import LearningStore


class CurriculumTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp
    rows = fixtures.LearningPathTests.rows

    def chapter(self, key):
        return next(
            c
            for course in self.study.dashboard()["courses"]
            for c in course["chapters"]
            if c["id"] == key
        )

    def test_complete_goals_links_and_acyclic_graph(self):
        ids = {c["id"] for subject in COURSES for c in course_chapters(subject)}
        self.assertEqual(len(ids), 40)
        for subject in COURSES:
            chapters = course_chapters(subject)
            self.assertEqual(len(chapters), len(CURRICULUM[subject]))
            for c in chapters:
                d = chapter_curriculum(subject, c["number"])
                self.assertEqual(len(d["objectives"]), 2)
                chain = prerequisite_chain(subject, c["number"])
                self.assertEqual(len(chain), len(set(chain)))
                self.assertNotIn(c["id"], chain)
                for key in chain:
                    self.assertIn(key, ids)
                    for parent in chapter_curriculum(
                        subject, int(key.rsplit("_", 1)[1])
                    )["prerequisite_ids"]:
                        self.assertLess(chain.index(parent), chain.index(key))
        for e in EXERCISES.values():
            family = e.get("family_id", e["algorithm"])
            self.assertTrue(OBJECTIVE_LINKS[family])
            self.assertTrue(set(OBJECTIVE_LINKS[family]) <= {0, 1})

    def test_reading_does_not_turn_objectives_into_evidence(self):
        before = self.chapter("C_program_10")
        self.study.mark_reading("C_program_10", True)
        after = self.chapter("C_program_10")
        self.assertEqual(before["objectives"], after["objectives"])
        self.assertEqual(after["readiness"], "insufficient")
        self.assertTrue(after["read_at"])
        self.assertTrue(
            all(
                o["status"] == "unassessed"
                for o in self.chapter("C_program_11")["objectives"]
            )
        )

    def test_independent_training_only_supports_its_linked_objective(self):
        a = self.service.start("c_pointer_01")
        self.service.submit(a["id"], self.rows(a))
        c = self.chapter("C_program_08")
        self.assertEqual(
            [o["status"] for o in c["objectives"]], ["evidence", "unassessed"]
        )
        self.assertEqual(c["readiness"], "partial")
        self.assertEqual(c["objectives"][0]["evidence_ids"], [a["id"]])
        bob = LearningPathService(
            LearningService(
                LearningStore(self.path, owner_id="bob"), self.service.solver
            )
        )
        bc = next(
            c
            for course in bob.dashboard()["courses"]
            for c in course["chapters"]
            if c["id"] == "C_program_08"
        )
        self.assertEqual(bc["readiness"], "insufficient")
        self.assertEqual(bc["objectives"][0]["evidence_ids"], [])

    def test_transitive_prerequisite_error_changes_recommendation_and_token(self):
        key = "C_program:c_program_memory"
        point = lambda: next(
            p for p in self.study.dashboard()["points"] if p["id"] == key
        )
        before = point()
        old = next(a for a in before["actions"] if a["kind"] == "material")["token"]
        a = self.service.start("c_pointer_01")
        rows = self.rows(a)
        rows[0]["value"] = "999"
        self.service.submit(a["id"], rows)
        self.study.mark_reading("C_program_08", True)
        after = point()
        self.assertEqual(after["actions"][0]["kind"], "material")
        self.assertEqual(after["actions"][0]["chapter_id"], "C_program_08")
        self.assertIn("先修章节", after["actions"][0]["reason"])
        self.assertTrue(
            any(
                g["id"] == "C_program_08" and g["readiness"] == "needs_work"
                for g in after["prerequisite_gaps"]
            )
        )
        self.assertNotEqual(
            self.study.evidence_signature(before), self.study.evidence_signature(after)
        )
        with self.assertRaises(LearningConflict):
            self.study.begin_recommendation(key, old)
        self.assertIn("practice", [a["kind"] for a in after["actions"]])

    def test_closed_catalog_does_not_invent_available_training(self):
        self.service.catalog = {}
        c = self.chapter("C_program_10")
        self.assertTrue(
            all(
                o["exercise_count"] == 0 and o["status"] == "unassessed"
                for o in c["objectives"]
            )
        )
