import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_learning_path as fixtures
from webapp_core.learning_exercises import EXERCISES
from webapp_core.learning_extended import EXTENDED_KINDS, solve_extended
from webapp_core.learning_program import (
    evaluate_program,
    program_solution,
    ProgramRunnerUnavailable,
)
from webapp_core.learning_service import ERROR_LABELS


class ExpandedTrainingTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp

    def test_all_structured_variants_submit_hint_solution_and_followup(self):
        for exercise in [e for e in EXERCISES.values() if e["kind"] in EXTENDED_KINDS]:
            with self.subTest(exercise=exercise["id"]):
                attempt = self.service.start(exercise["id"])
                self.assertTrue(self.service.hint(attempt["id"])["hint"]["text"])
                rows = [
                    {"value": str(r["value"])}
                    for r in solve_extended(exercise)["security_trace"]
                ]
                result = self.service.submit(attempt["id"], rows)
                self.assertTrue(result["evaluation"]["passed"])
                self.assertFalse(result["first_unassisted_pass"])
                self.assertTrue(
                    self.service.solution(attempt["id"])["solution"]["security_trace"]
                )
        self.assertTrue(
            all(
                p["learning_state"]["submitted_count"] == 3
                for p in self.study.dashboard()["points"]
                if p["title"] in {"CLOCK 状态", "分页地址转换", "认证流程", "签名验证"}
            )
        )

    def test_disk_golden_orders_distances_and_first_error(self):
        golden = [
            ("40,60,10,90", "160"),
            ("0,10,10,199", "199"),
            ("90,20,30,20", "100"),
            ("40,60,90,10", "140"),
            ("0,10,10,199", "199"),
            ("90,30,20,20", "80"),
            ("60,90,40,10", "120"),
            ("0,10,10,199", "199"),
            ("90,30,20,20", "80"),
        ]
        for number, expected in enumerate(golden, 1):
            exercise = EXERCISES[f"disk_schedule_{number:02d}"]
            with self.subTest(exercise=exercise["id"]):
                self.assertEqual(
                    [r["value"] for r in solve_extended(exercise)["security_trace"]],
                    list(expected),
                )
                attempt = self.service.start(exercise["id"])
                rows = [
                    {"value": expected[0].replace(",", "，")},
                    {"value": expected[1]},
                ]
                self.assertTrue(
                    self.service.submit(attempt["id"], rows)["evaluation"]["passed"]
                )
        attempt = self.service.start("disk_schedule_07")
        result = self.service.submit(
            attempt["id"], [{"value": "60,90,40,10"}, {"value": "338"}]
        )
        self.assertEqual(result["evaluation"]["first_error"]["step"], 2)
        self.assertEqual(
            result["evaluation"]["first_error"]["error_code"], "wrong_disk_schedule"
        )

    def test_new_families_have_material_and_distinct_review_questions(self):
        points = [
            p
            for p in self.study.dashboard()["points"]
            if p["title"] in {"字符串程序", "磁盘 FCFS", "磁盘 SSTF", "磁盘 LOOK"}
        ]
        self.assertEqual(len(points), 4)
        for point in points:
            action = next(a for a in point["actions"] if a["kind"] == "practice")
            receipt = self.study.begin_recommendation(point["id"], action["token"])
            self.assertTrue(
                self.service.get(receipt["attempt_id"])["exercise"]["chapter_id"]
            )
        families = {}
        for exercise in EXERCISES.values():
            key = exercise.get("family_id", exercise["algorithm"])
            families.setdefault(key, []).append(exercise["id"])
        for key in ["c_program_string", "磁盘 FCFS", "磁盘 SSTF", "磁盘 LOOK"]:
            self.assertEqual(len(set(families[key])), 3)

    def test_clock_golden_slots_bits_pointer_and_slot_order_matters(self):
        exercise = EXERCISES["clock_trace_01"]
        expected = [
            "1,-1,-1",
            "1,0,0",
            "1",
            "1,2,-1",
            "1,1,0",
            "2",
            "1,2,3",
            "1,1,1",
            "0",
            "1,2,3",
            "1,1,1",
            "0",
            "4,2,3",
            "1,0,0",
            "1",
            "4,2,3",
            "1,1,0",
            "1",
        ]
        self.assertEqual(
            [r["value"] for r in solve_extended(exercise)["security_trace"]], expected
        )
        attempt = self.service.start(exercise["id"])
        rows = [{"value": v} for v in expected]
        rows[12]["value"] = "2,4,3"
        result = self.service.submit(attempt["id"], rows)
        self.assertEqual(result["evaluation"]["first_error"]["step"], 13)
        self.assertEqual(
            result["evaluation"]["first_error"]["error_code"], "wrong_clock_trace"
        )

    def test_address_zero_frame_missing_page_and_out_of_range(self):
        self.assertEqual(
            [
                r["value"]
                for r in solve_extended(EXERCISES["address_translation_01"])[
                    "security_trace"
                ]
            ],
            [
                "0",
                "0",
                "有效",
                "3072",
                "0",
                "1023",
                "有效",
                "4095",
                "1",
                "0",
                "有效",
                "0",
                "2",
                "0",
                "缺页",
                "-1",
            ],
        )
        values = [
            r["value"]
            for r in solve_extended(EXERCISES["address_translation_02"])[
                "security_trace"
            ]
        ]
        self.assertEqual(values[-4:], ["3", "0", "越界", "-1"])

    def test_auth_priority_and_signature_tampering(self):
        self.assertEqual(
            [
                r["value"]
                for r in solve_extended(EXERCISES["auth_flow_02"])["security_trace"]
            ],
            ["拒绝", "随机数已使用", "允许", "无", "拒绝", "挑战过期"],
        )
        for i in range(1, 4):
            values = [
                r["value"]
                for r in solve_extended(EXERCISES[f"signature_check_{i:02d}"])[
                    "security_trace"
                ]
            ]
            self.assertEqual(values[1::2], ["通过", "失败", "失败"])

    def test_new_point_recommendation_records_independent_evidence(self):
        point = next(
            p for p in self.study.dashboard()["points"] if p["title"] == "分页地址转换"
        )
        action = next(a for a in point["actions"] if a["kind"] == "practice")
        receipt = self.study.begin_recommendation(point["id"], action["token"])
        attempt = self.service.get(receipt["attempt_id"])
        self.service.submit(
            attempt["id"],
            [
                {"value": r["value"]}
                for r in solve_extended(attempt["exercise"])["security_trace"]
            ],
        )
        point = next(
            p for p in self.study.dashboard()["points"] if p["id"] == point["id"]
        )
        self.assertEqual(point["followups"][0]["outcome"], "independent_evidence")
        self.assertIsNotNone(point["exercise_id"])

    def test_missing_runner_does_not_save_submission(self):
        attempt = self.service.start("c_program_01")
        with patch(
            "webapp_core.learning_program.subprocess.Popen",
            side_effect=FileNotFoundError,
        ):
            with self.assertRaises(ProgramRunnerUnavailable):
                self.service.submit(
                    attempt["id"], [{"code": "int main(void){return 0;}"}]
                )
        self.assertEqual(self.service.get(attempt["id"])["submission_count"], 0)


@unittest.skipUnless(
    os.getenv("TEST_C_PROGRAM_DOCKER") == "1", "Docker integration enabled explicitly"
)
class DockerProgramTests(unittest.TestCase):
    def evaluate(self, code, exercise_id="c_program_01"):
        return evaluate_program(EXERCISES[exercise_id], code, ERROR_LABELS)

    def test_all_six_reference_programs_and_alternative_implementation(self):
        for number in range(1, 7):
            exercise = EXERCISES[f"c_program_{number:02d}"]
            with self.subTest(exercise=exercise["id"]):
                result = self.evaluate(
                    program_solution(exercise)["code"], exercise["id"]
                )
                self.assertTrue(result["passed"], result)
        code = '#include <stdio.h>\nint main(void){long long n; if(scanf("%lld",&n)!=1)return 1;printf("%lld", n*(n+1)/2);return 0;}'
        self.assertTrue(self.evaluate(code)["passed"])

    def test_string_programs_empty_spaces_eof_and_alternative_implementation(self):
        for number in range(7, 10):
            exercise = EXERCISES[f"c_program_{number:02d}"]
            with self.subTest(exercise=exercise["id"]):
                result = self.evaluate(
                    program_solution(exercise)["code"], exercise["id"]
                )
                self.assertTrue(result["passed"], result)
                self.assertEqual(len(result["program_feedback"]["tests"]), 16)
        code = r"""#include <stdio.h>
#include <string.h>
int main(void){char s[1002]=""; if(!fgets(s,sizeof s,stdin)){puts("0");return 0;} printf("%zu",strcspn(s,"\n"));return 0;}
"""
        self.assertTrue(self.evaluate(code, "c_program_07")["passed"])
        wrong = r"""#include <stdio.h>
#include <string.h>
int main(void){char s[1001]="";scanf("%1000s",s);printf("%zu",strlen(s));return 0;}
"""
        self.assertFalse(self.evaluate(wrong, "c_program_07")["passed"])

    def test_compile_error_wrong_output_and_timeout(self):
        self.assertEqual(
            self.evaluate("int main( {")["first_error"]["error_code"], "compile_error"
        )
        wrong = self.evaluate('#include <stdio.h>\nint main(void){puts("0");}')
        self.assertEqual(wrong["first_error"]["error_code"], "wrong_output")
        self.assertTrue(wrong["program_feedback"]["tests"][0]["passed"])
        infinite = self.evaluate("int main(void){for(;;){} }")
        self.assertIn(
            infinite["first_error"]["error_code"], {"time_limit", "runtime_error"}
        )

    def test_no_host_secrets_no_network_and_read_only_root(self):
        with tempfile.TemporaryDirectory() as d:
            secret = Path(d) / "secret"
            secret.write_text("private-marker")
            # 返回非零代表隔离失败；正常情况下每个输入都能按题目计算。
            code = """#include <stdio.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
int main(void){
 if(access("SECRET",F_OK)==0)return 11;
 FILE *f=fopen("/etc/probe","w"); if(f)return 12;
 int s=socket(AF_INET,SOCK_STREAM,0); struct sockaddr_in a={0};
 a.sin_family=AF_INET;a.sin_port=htons(443);inet_pton(AF_INET,"1.1.1.1",&a.sin_addr);
 if(connect(s,(struct sockaddr*)&a,sizeof(a))==0)return 13;
 long long n;if(scanf("%lld",&n)!=1)return 1;printf("%lld",n*(n+1)/2);return 0;
}""".replace(
                "SECRET", str(secret)
            )
            self.assertTrue(self.evaluate(code)["passed"])
            self.assertEqual(secret.read_text(), "private-marker")

    def test_memory_and_output_limits_and_container_cleanup(self):
        output = self.evaluate(
            '#include <stdio.h>\nint main(void){for(;;)puts("012345678901234567890123456789");}'
        )
        self.assertFalse(output["passed"])
        self.assertLessEqual(
            len(output["program_feedback"]["tests"][0]["output"]), 2000
        )
        memory = self.evaluate(
            "#include <stdlib.h>\n#include <string.h>\nint main(void){for(;;){void *p=malloc(1024*1024);if(!p)return 1;memset(p,1,1024*1024);} }"
        )
        self.assertFalse(memory["passed"])
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "name=teaching-c-",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "")
