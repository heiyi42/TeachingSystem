import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_learning_path as fixtures
from webapp_core.learning_exercises import EXERCISES
from webapp_core.learning_os_foundations import solve_os_foundation, KINDS
from webapp_core.learning_program import (
    evaluate_program,
    program_solution,
    ProgramRunnerUnavailable,
)
from webapp_core.learning_security_lab import evaluate_lab, lab_solution
from webapp_core.learning_service import ERROR_LABELS

NEW = [
    e
    for e in EXERCISES.values()
    if e["id"].startswith(("c_advanced_", "security_lab_")) or e["kind"] in KINDS
]


class CoverageTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp

    def test_catalog_interfaces_drafts_and_material_recommendations(self):
        self.assertEqual(len(NEW), 45)
        points = self.study.dashboard()["points"]
        for e in NEW:
            with self.subTest(e=e["id"]):
                a = self.service.start(e["id"])
                self.assertEqual(
                    len(a["draft"]),
                    len(
                        e["parameters"].get(
                            "security_checks", e["parameters"].get("checkpoints", [])
                        )
                    ),
                )
                self.assertTrue(self.service.hint(a["id"])["hint"]["text"])
                point = next(
                    p
                    for p in points
                    if p["id"].endswith(":" + e.get("family_id", e["algorithm"]))
                )
                self.assertEqual(point["available_count"], 3)
                self.assertTrue(self.study.material(e["chapter_id"])["text"])
                if e["kind"] == "c_program":
                    files = self.service.solution(a["id"])["solution"]["files"]
                    self.assertEqual(
                        [f["name"] for f in files], e["parameters"]["source_files"]
                    )

    def test_os_hand_computed_golden_answers_and_first_errors(self):
        states = [
            ["运行", "阻塞", "阻塞", "就绪", "运行", "终止"],
            ["就绪", "就绪", "运行", "阻塞", "阻塞", "就绪", "运行"],
            ["阻塞", "就绪", "就绪", "运行", "终止", "终止"],
        ]
        resources = [
            ["进程共享", "线程独有", "线程独有", "进程共享", "进程共享", "线程独有"],
            ["进程共享", "线程独有", "线程独有", "进程共享", "线程独有", "进程共享"],
            ["线程独有", "进程共享", "进程共享", "线程独有", "进程共享", "线程独有"],
        ]
        files = [
            [
                "有效",
                "7",
                "0",
                "1",
                "有效",
                "7",
                "1023",
                "1",
                "有效",
                "8",
                "0",
                "1",
                "有效",
                "9",
                "1023",
                "1",
                "越界",
                "-1",
                "-1",
                "-1",
            ],
            [
                "有效",
                "2",
                "511",
                "1",
                "有效",
                "3",
                "0",
                "1",
                "有效",
                "3",
                "511",
                "1",
                "越界",
                "-1",
                "-1",
                "-1",
            ],
            [
                "有效",
                "11",
                "0",
                "1",
                "有效",
                "11",
                "4095",
                "1",
                "越界",
                "-1",
                "-1",
                "-1",
            ],
            [
                "有效",
                "14",
                "0",
                "1",
                "有效",
                "14",
                "1023",
                "1",
                "有效",
                "16",
                "0",
                "2",
                "有效",
                "18",
                "1023",
                "3",
                "越界",
                "-1",
                "-1",
                "-1",
            ],
            [
                "有效",
                "4",
                "511",
                "1",
                "有效",
                "6",
                "0",
                "2",
                "有效",
                "6",
                "511",
                "2",
                "越界",
                "-1",
                "-1",
                "-1",
            ],
            [
                "有效",
                "22",
                "0",
                "1",
                "有效",
                "22",
                "4095",
                "1",
                "越界",
                "-1",
                "-1",
                "-1",
            ],
            [
                "有效",
                "14",
                "0",
                "2",
                "有效",
                "14",
                "1023",
                "2",
                "有效",
                "16",
                "0",
                "2",
                "有效",
                "18",
                "1023",
                "2",
                "越界",
                "-1",
                "-1",
                "-1",
            ],
            [
                "有效",
                "4",
                "511",
                "2",
                "有效",
                "6",
                "0",
                "2",
                "有效",
                "6",
                "511",
                "2",
                "越界",
                "-1",
                "-1",
                "-1",
            ],
            [
                "有效",
                "22",
                "0",
                "2",
                "有效",
                "22",
                "4095",
                "2",
                "越界",
                "-1",
                "-1",
                "-1",
            ],
        ]
        for kind, golden in [
            ("process_states", states),
            ("thread_resources", resources),
            ("file_allocation", files),
        ]:
            for n, expected in enumerate(golden, 1):
                e = EXERCISES[f"{kind}_{n:02d}"]
                self.assertEqual(
                    [r["value"] for r in solve_os_foundation(e)["security_trace"]],
                    expected,
                )
                a = self.service.start(e["id"])
                rows = [{"value": v} for v in expected]
                rows[-1] = {"value": ""}
                bad = self.service.submit(a["id"], rows)
                self.assertEqual(
                    bad["evaluation"]["first_error"]["step"], len(expected)
                )
                self.assertTrue(
                    self.service.submit(a["id"], [{"value": v} for v in expected])[
                        "evaluation"
                    ]["passed"]
                )

    def test_multifile_missing_rows_and_unavailable_lab_do_not_save(self):
        a = self.service.start("c_advanced_module_sum")
        with self.assertRaises(ValueError):
            self.service.submit(a["id"], [{"code": "int main(void){return 0;}"}])
        self.assertEqual(self.service.get(a["id"])["submission_count"], 0)
        a = self.service.start("security_lab_database_01")
        with patch(
            "webapp_core.learning_security_lab._run",
            side_effect=ProgramRunnerUnavailable("unavailable"),
        ):
            with self.assertRaises(ProgramRunnerUnavailable):
                self.service.submit(a["id"], [{"code": "pass"}])
        self.assertEqual(self.service.get(a["id"])["submission_count"], 0)

    def test_os_recommendations_and_two_new_review_variants(self):
        for kind in ["process_states", "thread_resources", "file_allocation"]:
            a = self.service.start(f"{kind}_01")
            e = a["exercise"]
            rows = [
                {"value": r["value"]} for r in solve_os_foundation(e)["security_trace"]
            ]
            bad = [{"value": ""} for _ in rows]
            self.service.submit(a["id"], bad)
            self.service.submit(a["id"], rows)
            with patch("time.time", return_value=a["created_at"] + 86401):
                b = self.service.start(None, review_id=a["id"])
                self.assertNotEqual(b["exercise"]["id"], e["id"])
                self.service.submit(
                    b["id"],
                    [
                        {"value": r["value"]}
                        for r in solve_os_foundation(b["exercise"])["security_trace"]
                    ],
                )
            with patch("time.time", return_value=a["created_at"] + 4 * 86400 + 2):
                c = self.service.start(None, review_id=a["id"])
                self.assertNotIn(c["exercise"]["id"], [e["id"], b["exercise"]["id"]])


@unittest.skipUnless(
    os.getenv("TEST_C_PROGRAM_DOCKER") == "1", "Docker enabled explicitly"
)
class CoverageDockerTests(unittest.TestCase):
    setUp = fixtures.LearningPathTests.setUp

    def test_all_new_c_and_lab_reference_submissions_and_blank_rejection(self):
        for e in NEW:
            if e["kind"] not in {"c_program", "security_lab"}:
                continue
            with self.subTest(e=e["id"]):
                a = self.service.start(e["id"])
                solution = (
                    program_solution(e) if e["kind"] == "c_program" else lab_solution(e)
                )
                rows = (
                    [{"code": f["code"]} for f in solution["files"]]
                    if solution.get("files")
                    else [{"code": solution["code"]}]
                )
                self.assertTrue(
                    self.service.submit(a["id"], rows)["evaluation"]["passed"]
                )
                blank = self.service.start(e["id"])
                result = self.service.submit(
                    blank["id"], [{"code": ""} for _ in blank["draft"]]
                )
                self.assertFalse(result["evaluation"]["passed"])
                self.assertEqual(result["submission_count"], 1)
        self.assertEqual(len(self.service.progress()["records"]), 60)

    def test_memory_failures_leaks_and_original_realloc_ownership(self):
        examples = {
            "clone": "int clone_array(const int*a,size_t n,int**out){*out=malloc(n*sizeof(int));if(!*out)return -1;for(size_t i=0;i<n;i++)(*out)[i]=a[i];return 0;}",
            "append": "int append_value(int **a,size_t*n,int v){*a=realloc(*a,(*n+1)*sizeof(int));if(!*a)return -1;(*a)[(*n)++]=v;return 0;}",
            "filter": "int positive_copy(const int*a,size_t n,int**out,size_t*c){malloc(1);*out=NULL;*c=0;return 0;}",
        }
        for task, code in examples.items():
            e = EXERCISES["c_advanced_" + task]
            result = evaluate_program(
                e, "#include <stdlib.h>\n#include <stddef.h>\n" + code, ERROR_LABELS
            )
            self.assertFalse(result["passed"], task)

    def test_file_binary_zero_byte_and_multifile_link_errors(self):
        e = EXERCISES["c_advanced_file_copy"]
        wrong = '#include <stdio.h>\nint copy_file(const char*s,const char*d){FILE*a=fopen(s,"r"),*b=fopen(d,"w");if(!a||!b)return -1;int c;while((c=fgetc(a))!=EOF&&c!=0)fputc(c,b);fclose(a);fclose(b);return 0;}'
        self.assertFalse(evaluate_program(e, wrong, ERROR_LABELS)["passed"])
        e = EXERCISES["c_advanced_module_sum"]
        rows = [{"code": f["code"]} for f in program_solution(e)["files"]]
        rows[1]["code"] = ""
        self.assertEqual(
            evaluate_program(e, rows, ERROR_LABELS)["first_error"]["error_code"],
            "compile_error",
        )
        e = EXERCISES["c_advanced_reverse"]
        code = "#include <stddef.h>\nvoid reverse(int*a,size_t n){if(!n)return;int *l=a,*r=a+n-1;while(l<r){int v=*l;*l++=*r;*r--=v;}}"
        self.assertTrue(evaluate_program(e, code, ERROR_LABELS)["passed"])

    def test_wrong_lab_artifacts_and_readonly_inputs(self):
        for task in [
            "database",
            "encryption",
            "authentication",
            "permissions",
            "signature",
            "audit",
        ]:
            e = EXERCISES[f"security_lab_{task}_01"]
            if task == "permissions":
                code = lab_solution(e)["code"].replace("0o600", "0o666")
            elif task == "encryption":
                code = "from pathlib import Path\nPath('/output/cipher.bin').write_bytes(Path('/input/message.bin').read_bytes())"
            elif task == "audit":
                code = (
                    lab_solution(e)["code"]
                    + "\nr=json.loads(Path('/output/report.json').read_text());r['failure_ids']*=2;Path('/output/report.json').write_text(json.dumps(r))"
                )
            elif task == "database":
                code = (
                    lab_solution(e)["code"]
                    .replace(" TEXT UNIQUE NOT NULL", " TEXT")
                    .replace(" CHECK(outcome IN ('allow','deny'))", "")
                )
            elif task == "authentication":
                code = lab_solution(e)["code"].replace(
                    "digest=hashlib.pbkdf2_hmac('sha256',u['password'].encode(),bytes.fromhex(u['salt']),100000).hex()",
                    "digest=u['password']",
                )
            else:
                code = "from pathlib import Path\nPath('/output/signature.bin').write_bytes(b'fake');Path('/output/public.pem').write_bytes(Path('/input/public.pem').read_bytes())"
            self.assertFalse(evaluate_lab(e, code, ERROR_LABELS)["passed"], task)
        e = EXERCISES["security_lab_permissions_01"]
        self.assertFalse(
            evaluate_lab(
                e,
                "from pathlib import Path\nPath('/input/report.txt').write_text('changed')",
                ERROR_LABELS,
            )["passed"]
        )

    def test_lab_rejects_symlink_directory_and_stdout_cheating(self):
        e = EXERCISES["security_lab_database_01"]
        for code in [
            "print('产物校验通过')",
            "import os\nos.symlink('/input/config.json','/output/report.json')",
            "import os\nos.mkdir('/output/not-a-file')",
            "from pathlib import Path\nPath('/output/huge').write_bytes(b'a'*40000)",
        ]:
            self.assertFalse(evaluate_lab(e, code, ERROR_LABELS)["passed"])
