from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts.evaluate_learning_diagnosis import DEFAULT_FIXTURE, load_cases, run_evaluation
from scripts.replay_learning_demo import replay
from webapp_core.learning_exercises import EXERCISES
from webapp_core.learning_service import LearningService


class LearningDiagnosisEvaluationTests(unittest.TestCase):
    def test_fixed_held_out_cases_match_expected_diagnosis(self):
        result = run_evaluation(DEFAULT_FIXTURE)
        self.assertEqual(result["problem_count"], 12)
        self.assertEqual(result["training_overlap"], 0)
        self.assertEqual(result["summary"]["category_counts"], {
            "correct": 24, "incorrect": 48, "incomplete": 12, "invalid": 24,
        })
        self.assertEqual(result["summary"]["exact_match"]["numerator"], 108)
        self.assertEqual(result["summary"]["unexpected_exceptions"], 0)

    def test_broken_always_pass_classifier_does_not_pass_evaluation(self):
        def always_pass(_service, _exercise, rows):
            return {"passed": True, "first_error": None, "row_statuses": ["correct"] * len(rows)}

        with patch.object(LearningService, "_evaluate", always_pass):
            result = run_evaluation(DEFAULT_FIXTURE)
        self.assertEqual(result["summary"]["exact_match"]["numerator"], 24)
        self.assertEqual(result["summary"]["first_error_localization"]["numerator"], 0)
        self.assertEqual(result["summary"]["invalid_rejection"]["numerator"], 0)

    def test_overlapping_training_input_is_rejected(self):
        fixture = json.loads(DEFAULT_FIXTURE.read_text())
        fixture["problems"][0]["parameters"] = EXERCISES["lru_01"]["parameters"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.json"
            path.write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError, "重复"):
                load_cases(path)

    def test_changed_expected_label_is_not_replaced_by_solver_output(self):
        fixture = json.loads(DEFAULT_FIXTURE.read_text())
        fixture["problems"][0]["variants"][2]["expected"]["first_error_step"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.json"
            path.write_text(json.dumps(fixture))
            result = run_evaluation(path)
        self.assertEqual(result["summary"]["exact_match"]["numerator"], 107)

    def test_demo_replays_real_routes_with_separate_simulated_records(self):
        result = replay()
        self.assertEqual(result["kind"], "simulated_demo")
        self.assertEqual(len(result["events"]), 8)
        self.assertEqual(result["summary"]["corrected_passes"], 1)
        self.assertEqual(result["summary"]["independent_retest_passes"], 1)

    def test_reference_trajectories_obey_workload_and_state_invariants(self):
        dataset, _ = load_cases(DEFAULT_FIXTURE)
        for problem in dataset["problems"]:
            with self.subTest(problem=problem["id"]):
                parameters = problem["parameters"]
                if problem["kind"] == "page_replacement":
                    before = set()
                    self.assertEqual(len(problem["gold_rows"]), len(parameters["sequence"]))
                    for page, row in zip(parameters["sequence"], problem["gold_rows"]):
                        after = {int(value.strip()) for value in row["frames"].split(",")}
                        self.assertIn(page, after)
                        self.assertLessEqual(len(after), parameters["frames"])
                        if page in before:
                            self.assertEqual(row["event"], "hit")
                            self.assertEqual(after, before)
                            self.assertEqual(row["evicted"], "—")
                        elif len(before) < parameters["frames"]:
                            self.assertEqual(row["event"], "fault")
                            self.assertEqual(after, before | {page})
                            self.assertEqual(row["evicted"], "—")
                        else:
                            victim = int(row["evicted"])
                            self.assertEqual(row["event"], "fault")
                            self.assertIn(victim, before)
                            self.assertEqual(after, before - {victim} | {page})
                        before = after
                else:
                    by_name = {process["name"]: process for process in parameters["processes"]}
                    work = Counter()
                    previous_end = 0
                    for row in problem["gold_rows"]:
                        start, end = int(row["start"]), int(row["end"])
                        self.assertGreaterEqual(start, previous_end)
                        self.assertGreater(end, start)
                        self.assertGreaterEqual(start, by_name[row["process"]]["arrival"])
                        if parameters["quantum"] is not None:
                            self.assertLessEqual(end - start, parameters["quantum"])
                        work[row["process"]] += end - start
                        previous_end = end
                    self.assertEqual(dict(work), {name: process["service"] for name, process in by_name.items()})


if __name__ == "__main__":
    unittest.main()
