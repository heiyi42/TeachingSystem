from __future__ import annotations

from uuid import UUID, uuid4

from flask import Blueprint, current_app, g, jsonify, request

from webapp_core.learning.learning_store import LearningStore

from webapp_core.learning.learning_service import LearningConflict, LearningService
from webapp_core.learning.learning_path import LearningPathService
from webapp_core.learning.learning_plan import LearningPlanService
from webapp_core.learning.learning_workflow import LearningWorkflow


def learning_blueprint(service: LearningService, *, demo: bool = False) -> Blueprint:
    blueprint = Blueprint(
        "learning_demo" if demo else "learning",
        __name__,
        url_prefix="/api/learning-demo/<demo_id>" if demo else "/api/learning",
    )
    demo_root = service.store.path.parent / f"{service.store.path.stem}_demos"

    def current_service():
        return (
            g.learning_demo_service if demo else getattr(g, "learning_service", service)
        )

    def learning_workflow():
        return LearningWorkflow(
            current_service(), current_app.extensions["agenticrag.workflow_runs"]
        )

    if demo:

        @blueprint.url_value_preprocessor
        def select_demo(endpoint, values):
            demo_id = values.pop("demo_id")
            try:
                valid = str(UUID(demo_id)) == demo_id
            except ValueError:
                valid = False
            path = demo_root / f"{demo_id}.sqlite3"
            if not valid or not path.is_file():
                raise LookupError("演示不存在，请重新开始演示")
            g.learning_demo_service = LearningService(
                LearningStore(path), service.solver
            )

    else:

        @blueprint.post("/demo")
        def create_demo():
            demo_id = str(uuid4())
            demo_service = LearningService(
                LearningStore(demo_root / f"{demo_id}.sqlite3"), service.solver
            )
            return (
                jsonify({"demo_id": demo_id, "attempt": demo_service.start("lru_01")}),
                201,
            )

    @blueprint.before_request
    def limit_payload():
        if request.content_length and request.content_length > 65536:
            return jsonify({"error": "作答数据过大"}), 413

    @blueprint.errorhandler(ValueError)
    def invalid(error):
        return jsonify({"error": str(error)}), (
            409 if isinstance(error, LearningConflict) else 400
        )

    @blueprint.errorhandler(LookupError)
    def missing(error):
        return jsonify({"error": str(error)}), 404

    def payload():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("请求须为 JSON 对象")
        return data

    @blueprint.post("/training-match")
    def training_match():
        data = payload()
        return jsonify(
            {
                "matches": LearningPathService(current_service()).match_training(
                    data.get("question", ""),
                    data.get("subject_id", "auto"),
                    data.get("chapter_id"),
                )
            }
        )

    @blueprint.get("/roadshow/<subject>")
    def learning_roadshow(subject):
        from webapp_core.learning.learning_roadshow import roadshow

        return jsonify(roadshow(subject))

    @blueprint.get("/courses")
    def courses():
        return jsonify({"courses": current_service().courses()})

    @blueprint.get("/exercises")
    def exercises():
        return jsonify({"exercises": current_service().exercises()})

    @blueprint.post("/attempts")
    def start():
        data = payload()
        for field in ("exercise_id", "parent_id", "class_id"):
            if data.get(field) is not None and not isinstance(data[field], str):
                raise ValueError("题目、练习和班级编号须为字符串")
        if not demo and hasattr(g, "school_store"):
            parent = (
                current_service().get(data["parent_id"])
                if data.get("parent_id")
                else None
            )
            class_id = parent.get("class_id") if parent else data.get("class_id")
            if parent and data.get("class_id") and data["class_id"] != class_id:
                raise ValueError("复测必须沿用原练习的班级归属")
            if class_id:
                classroom = g.school_store.class_access(class_id, g.current_user)
                target = (
                    parent["exercise"]
                    if parent
                    else current_service().catalog.get(data.get("exercise_id"))
                )
                if target and target["subject_id"] != classroom["course_id"]:
                    raise ValueError("题目须属于所选班级的课程")
                current_service().class_id = class_id
        return (
            jsonify(
                current_service().start(data.get("exercise_id"), data.get("parent_id"))
            ),
            201,
        )

    @blueprint.get("/attempts/<attempt_id>")
    def get(attempt_id):
        return jsonify(current_service().get(attempt_id))

    @blueprint.put("/attempts/<attempt_id>/draft")
    def draft(attempt_id):
        return jsonify(current_service().save_draft(attempt_id, payload().get("rows")))

    @blueprint.post("/attempts/<attempt_id>/submit")
    def submit(attempt_id):
        return jsonify(current_service().submit(attempt_id, payload().get("rows")))

    @blueprint.post("/attempts/<attempt_id>/hint")
    def hint(attempt_id):
        return jsonify(current_service().hint(attempt_id, payload().get("step")))

    @blueprint.post("/attempts/<attempt_id>/solution")
    def solution(attempt_id):
        payload()
        return jsonify(current_service().solution(attempt_id))

    @blueprint.get("/progress")
    def progress():
        return jsonify(current_service().progress())

    from webapp_core.learning.learning_program import ProgramRunnerUnavailable

    @blueprint.errorhandler(ProgramRunnerUnavailable)
    def program_unavailable(error):
        return jsonify(error=str(error)), 503

    @blueprint.get("/plan/<subject>")
    def study_plan(subject):
        return jsonify(LearningPlanService(current_service()).view(subject))

    @blueprint.put("/plan/<subject>")
    def configure_plan(subject):
        return jsonify(
            LearningPlanService(current_service()).configure(subject, payload())
        )

    @blueprint.post("/plan/<subject>/diagnostic")
    def begin_diagnostic(subject):
        data = payload()
        return jsonify(
            LearningPlanService(current_service()).begin_diagnostic(
                subject, data.get("point_id")
            )
        )

    @blueprint.post("/plan/<subject>/ai")
    def generate_study_plan(subject):
        data = payload()
        if demo:
            return jsonify(error="请登录后使用 AI 学习计划"), 403
        from webapp_core.runtime.async_runner import run_async
        from webapp_core.chat.auto_runtime import auto_router_llm

        try:
            return jsonify(
                run_async(
                    learning_workflow().execute(
                        "learning_plan", subject, data, auto_router_llm
                    )
                )
            )
        except (LearningConflict, LookupError):
            raise
        except Exception:
            return (
                jsonify(
                    error="AI 计划暂未生成成功，当前基础安排仍可使用，请稍后重试。"
                ),
                503,
            )

    @blueprint.get("/plan/<subject>/ai")
    def study_plan_run(subject):
        if demo:
            return jsonify(error="请登录后使用 AI 学习计划"), 403
        return jsonify(learning_workflow().status("learning_plan", subject))

    @blueprint.post("/attempts/<attempt_id>/walkthrough")
    def learning_walkthrough(attempt_id):
        from webapp_core.learning.learning_walkthrough import LearningWalkthroughService

        return jsonify(
            LearningWalkthroughService(current_service()).advance(attempt_id, payload())
        )

    @blueprint.post("/attempts/<attempt_id>/dialogue")
    def learning_dialogue(attempt_id):
        if demo:
            return jsonify(error="请登录后使用互动诊断"), 403
        from webapp_core.runtime.async_runner import run_async
        from webapp_core.chat.auto_runtime import auto_router_llm

        data = payload()
        try:
            return jsonify(
                run_async(
                    learning_workflow().execute(
                        "learning_dialogue", attempt_id, data, auto_router_llm
                    )
                )
            )
        except (ValueError, LookupError):
            raise
        except Exception:
            return (
                jsonify(error="互动诊断暂不可用，已保存的记录保留，请稍后重试。"),
                503,
            )

    @blueprint.get("/attempts/<attempt_id>/dialogue")
    def learning_dialogue_run(attempt_id):
        if demo:
            return jsonify(error="请登录后使用互动诊断"), 403
        return jsonify(learning_workflow().status("learning_dialogue", attempt_id))

    @blueprint.post("/attempts/<attempt_id>/grading-reviews")
    def request_grading_review(attempt_id):
        if demo:
            return jsonify(error="演示练习不支持评测复核"), 403
        from webapp_core.school.grading_review import GradingReviewService

        data = payload()
        return jsonify(
            GradingReviewService(current_service()).request(
                attempt_id, data.get("submission_number"), data.get("reason")
            )
        )

    @blueprint.get("/path")
    def path():
        return jsonify(LearningPathService(current_service()).dashboard())

    @blueprint.post("/points/<point_id>/recommendations")
    def begin_recommendation(point_id):
        data = payload()
        return jsonify(
            LearningPathService(current_service()).begin_recommendation(
                point_id, data.get("token")
            )
        )

    @blueprint.post("/points/<point_id>/explanation")
    def explanation(point_id):
        payload()
        if demo:
            return jsonify(error="请登录后使用个性化 AI 讲解"), 403
        from webapp_core.runtime.async_runner import run_async
        from webapp_core.chat.auto_runtime import auto_router_llm

        try:
            return jsonify(
                run_async(
                    LearningPathService(current_service()).explain(
                        point_id, auto_router_llm
                    )
                )
            )
        except (LearningConflict, LookupError):
            raise
        except Exception:
            return (
                jsonify(
                    error="AI 讲解暂时不可用，可以先阅读推荐资料或继续练习，稍后重试。"
                ),
                503,
            )

    @blueprint.get("/chapters/<chapter_id>/material")
    def material(chapter_id):
        try:
            start = int(request.args.get("start", "1"))
        except ValueError:
            raise ValueError("资料起始行须为正整数")
        return jsonify(
            LearningPathService(current_service()).material(
                chapter_id, start, request.args.get("q", "")
            )
        )

    @blueprint.put("/chapters/<chapter_id>/reading")
    def reading(chapter_id):
        return jsonify(
            LearningPathService(current_service()).mark_reading(
                chapter_id, payload().get("read")
            )
        )

    @blueprint.post("/reviews/<attempt_id>/start")
    def review_start(attempt_id):
        payload()
        current = current_service()
        current.get(attempt_id)
        return jsonify(current.start(None, review_id=attempt_id)), 201

    @blueprint.post("/attempts/<attempt_id>/question-context")
    def question_context(attempt_id):
        payload()
        return jsonify(
            LearningPathService(current_service()).question_context(attempt_id)
        )

    return blueprint
