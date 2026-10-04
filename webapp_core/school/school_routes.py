from __future__ import annotations

from threading import Lock
from contextlib import closing

import io

from flask import Blueprint, g, jsonify, request, send_file

from webapp_core.learning.learning_service import LearningConflict, LearningService
from webapp_core.learning.learning_store import LearningStore
from webapp_core.school.school_store import SchoolStore
from webapp_core.learning.learning_authoring import authoring_templates
from webapp_core.school.task_service import TaskService
from webapp_core.school.lesson_plan_service import LessonPlanService


COOKIE = "gm_session"


def school_blueprint(school: SchoolStore, learning: LearningService):
    blueprint = Blueprint("school", __name__, url_prefix="/api")

    def payload():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("请求须为 JSON 对象")
        return data

    def teacher():
        if g.current_user["role"] not in {"teacher", "admin"}:
            raise PermissionError("此操作仅供教师使用")
        return g.current_user

    def tasks():
        return TaskService(school, learning.store, learning.solver, g.current_user)

    @blueprint.get("/school/classes/<class_id>/lesson-plans")
    def lesson_plans(class_id):
        teacher()
        return jsonify(LessonPlanService(tasks(), learning).overview(class_id))

    @blueprint.post("/school/classes/<class_id>/lesson-plans/generate")
    def lesson_plan_generate(class_id):
        teacher()
        return jsonify(LessonPlanService(tasks(), learning).generate(class_id, payload()))

    @blueprint.post("/school/classes/<class_id>/lesson-plans")
    def lesson_plan_save(class_id):
        teacher()
        return jsonify(LessonPlanService(tasks(), learning).save(class_id, payload())), 201

    @blueprint.put("/school/classes/<class_id>/lesson-plans/<plan_id>")
    def lesson_plan_edit(class_id, plan_id):
        teacher()
        return jsonify(LessonPlanService(tasks(), learning).save(class_id, payload(), plan_id))

    @blueprint.get("/school/classes/<class_id>/lesson-plans/<plan_id>/effects")
    def lesson_plan_effects(class_id, plan_id):
        teacher()
        return jsonify(LessonPlanService(tasks(), learning).effects(class_id, plan_id))

    @blueprint.get("/school/task-exercises")
    def task_exercises():
        teacher()
        return jsonify(exercises=list(school.catalog("training").values()))

    @blueprint.get("/school/classes/<class_id>/tasks")
    def task_list(class_id):
        return jsonify(tasks=tasks().list(class_id))

    @blueprint.post("/school/tasks")
    def task_create():
        teacher()
        return jsonify(tasks().write(payload())), 201

    @blueprint.put("/school/tasks/<task_id>")
    def task_edit(task_id):
        teacher()
        return jsonify(tasks().write(payload(), task_id))

    @blueprint.get("/school/tasks/<task_id>")
    def task_detail(task_id):
        return jsonify(tasks().detail(task_id, request.args.get("student_id")))

    @blueprint.post("/school/tasks/<task_id>/publish")
    def task_publish(task_id):
        teacher()
        payload()
        return jsonify(tasks().transition(task_id, "publish"))

    @blueprint.post("/school/tasks/<task_id>/close")
    def task_close(task_id):
        teacher()
        payload()
        return jsonify(tasks().transition(task_id, "close"))

    @blueprint.post("/school/tasks/<task_id>/attempts")
    def task_start(task_id):
        exercise_id = payload().get("exercise_id")
        if not isinstance(exercise_id, str):
            raise ValueError("题目编号须为字符串")
        return jsonify(tasks().start(task_id, exercise_id)), 201

    @blueprint.post("/school/tasks/<task_id>/finalize")
    def task_finalize(task_id):
        payload()
        return jsonify(tasks().finalize(task_id))

    @blueprint.put("/school/tasks/<task_id>/report")
    def task_report_text(task_id):
        return jsonify(tasks().report_text(task_id, payload().get("report")))

    @blueprint.post("/school/tasks/<task_id>/files")
    def task_upload(task_id):
        return jsonify(tasks().upload(task_id, request.files.get("file"))), 201

    @blueprint.get("/school/tasks/<task_id>/files/<file_id>")
    def task_download(task_id, file_id):
        name, raw = tasks().file(task_id, file_id)
        response = send_file(
            io.BytesIO(raw),
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=name,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @blueprint.delete("/school/tasks/<task_id>/files/<file_id>")
    def task_delete_file(task_id, file_id):
        payload()
        tasks().file(task_id, file_id, delete=True)
        return jsonify(ok=True)

    @blueprint.post("/school/tasks/<task_id>/students/<student_id>/review")
    def task_review(task_id, student_id):
        teacher()
        return jsonify(tasks().review(task_id, student_id, payload()))

    @blueprint.get("/school/classes/<class_id>/report")
    def class_report(class_id):
        teacher()
        return jsonify(tasks().report(class_id, request.args.get("chapter_id")))

    @blueprint.get("/school/classes/<class_id>/report.csv")
    def class_report_csv(class_id):
        teacher()
        return send_file(
            io.BytesIO(tasks().csv(class_id, request.args.get("chapter_id"))),
            mimetype="text/csv; charset=utf-8",
            as_attachment=True,
            download_name="班级学情.csv",
        )

    @blueprint.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), (
            409 if isinstance(error, LearningConflict) else 400
        )

    @blueprint.errorhandler(LookupError)
    def missing(error):
        return jsonify(error=str(error)), 404

    @blueprint.errorhandler(PermissionError)
    def forbidden(error):
        return jsonify(error=str(error)), 403

    @blueprint.get("/identity/session")
    def identity():
        return jsonify(user=g.current_user, setup_needed=school.setup_needed())

    @blueprint.post("/identity/setup")
    def setup():
        if request.remote_addr not in {"127.0.0.1", "::1"}:
            raise PermissionError("首个管理员须在服务器本机初始化")
        data = payload()
        school.create_user(data, role="admin", initial=True)
        user, token = school.login(
            data["username"], data["password"], request.remote_addr
        )
        return logged_in(user, token)

    def logged_in(user, token):
        response = jsonify(user=user, setup_needed=False)
        response.set_cookie(
            COOKIE,
            token,
            max_age=28800,
            httponly=True,
            secure=request.is_secure,
            samesite="Strict",
        )
        return response

    @blueprint.post("/identity/register")
    def register():
        if school.setup_needed():
            raise LearningConflict("请先由本机管理员完成初始化")
        data = payload()
        school.create_user(data)
        user, token = school.login(
            data["username"], data["password"], request.remote_addr
        )
        return logged_in(user, token)

    @blueprint.post("/identity/login")
    def login():
        data = payload()
        user, token = school.login(
            data.get("username"), data.get("password"), request.remote_addr
        )
        return logged_in(user, token)

    @blueprint.post("/identity/logout")
    def logout():
        school.logout(request.cookies.get(COOKIE))
        response = jsonify(ok=True)
        response.delete_cookie(COOKIE)
        return response

    @blueprint.post("/identity/password")
    def password():
        data = payload()
        school.change_password(
            g.current_user, data.get("old_password"), data.get("new_password")
        )
        response = jsonify(ok=True)
        response.delete_cookie(COOKIE)
        return response

    @blueprint.post("/school/teachers")
    def create_teacher():
        if g.current_user["role"] != "admin":
            raise PermissionError("教师账号由管理员创建")
        return (
            jsonify(
                user=school.create_user(
                    payload(), role="teacher", actor=g.current_user["id"]
                )
            ),
            201,
        )

    @blueprint.get("/school/classes")
    def classes():
        return jsonify(classes=school.classes(g.current_user))

    @blueprint.post("/school/classes")
    def create_class():
        user = teacher()
        data = payload()
        return (
            jsonify(
                school.create_class(user, data.get("title"), data.get("course_id"))
            ),
            201,
        )

    @blueprint.post("/school/classes/join")
    def join_class():
        if g.current_user["role"] != "student":
            raise PermissionError("加入码供学生使用")
        school.join_class(g.current_user, payload().get("join_code"))
        return jsonify(ok=True)

    @blueprint.post("/school/classes/<class_id>/rotate-code")
    def rotate_code(class_id):
        return jsonify(join_code=school.rotate_code(class_id, teacher()))

    @blueprint.get("/school/classes/<class_id>/members")
    def members(class_id):
        return jsonify(members=school.roster(class_id, teacher()))

    @blueprint.delete("/school/classes/<class_id>/members/<user_id>")
    def remove_member(class_id, user_id):
        school.remove_member(class_id, user_id, teacher())
        return jsonify(ok=True)

    def student_service(class_id, user_id):
        roster = school.roster(class_id, teacher())
        if not any(member["id"] == user_id for member in roster):
            raise LookupError("学生不属于该班级")
        return LearningService(
            LearningStore(learning.store.path, owner_id=user_id, class_id=class_id),
            learning.solver,
            catalog=school.catalog("training"),
        )

    @blueprint.get("/school/classes/<class_id>/students/<user_id>/progress")
    def student_progress(class_id, user_id):
        return jsonify(student_service(class_id, user_id).progress())

    @blueprint.get(
        "/school/classes/<class_id>/students/<user_id>/attempts/<attempt_id>"
    )
    def student_attempt(class_id, user_id, attempt_id):
        service = student_service(class_id, user_id)
        state = service.store.get(attempt_id)
        return jsonify({**service._public(state), "submissions": state["submissions"]})

    def review_access(state):
        user = teacher()
        if user["role"] == "admin":
            return
        if not state.get("class_id"):
            raise LookupError("课外自主练习由管理员复核")
        school.class_access(state["class_id"], user, teacher=True)

    @blueprint.get("/school/grading-reviews")
    def grading_reviews():
        teacher()
        items = []
        with closing(school.connect()) as connection:
            names = dict(connection.execute("SELECT id,name FROM users"))
        for state in learning.store.list_attempts():
            if not state.get("grading_reviews"):
                continue
            try:
                review_access(state)
            except LookupError:
                continue
            for record in state["grading_reviews"]:
                items.append(
                    {
                        **record,
                        "attempt_id": state["id"],
                        "owner_id": state.get("owner_id"),
                        "student_name": names.get(state.get("owner_id"), "旧版账号"),
                        "title": learning._exercise(state)["title"],
                        "class_id": state.get("class_id"),
                    }
                )
        return jsonify(
            items=sorted(
                items, key=lambda r: (r["status"] != "pending", -r["created_at"])
            )
        )

    @blueprint.get("/school/grading-reviews/<attempt_id>")
    def grading_review_detail(attempt_id):
        state = learning.store.get(attempt_id)
        review_access(state)
        return jsonify(
            exercise=learning._exercise(state),
            submissions=state["submissions"],
            reviews=state.get("grading_reviews", []),
            content_version=state.get("content_version"),
            grading_version=state.get("grading_version"),
        )

    @blueprint.post("/school/grading-reviews/<attempt_id>/<review_id>")
    def resolve_grading_review(attempt_id, review_id):
        from webapp_core.school.grading_review import GradingReviewService

        state = learning.store.get(attempt_id)
        review_access(state)
        service = LearningService(
            LearningStore(learning.store.path, owner_id=state.get("owner_id")),
            learning.solver,
            catalog=school.catalog("training"),
        )
        data = payload()
        return jsonify(
            GradingReviewService(service).resolve(
                attempt_id, review_id, data.get("decision"), data.get("note"), teacher()
            )
        )

    @blueprint.get("/school/content")
    def content():
        teacher()
        latest = {}
        for row in school.versions():
            latest.setdefault(row["item_key"], row)
        return jsonify(items=list(latest.values()))

    @blueprint.get("/school/content/<item_key>")
    def versions(item_key):
        teacher()
        items = school.versions(item_key)
        if not items:
            raise LookupError("题目不存在")
        solutions = {}
        for item in items:
            if item["category"] == "training":
                solutions[str(item["version"])] = learning._solve(item["data"])
        return jsonify(
            versions=items,
            solutions=solutions,
            audit=school.audit_log(item_key),
            templates=(
                authoring_templates(items[0]["data"])
                if items[0]["category"] == "training"
                else []
            ),
        )

    @blueprint.post("/school/content/<item_key>/versions")
    def new_version(item_key):
        return jsonify(version=school.new_version(item_key, teacher())), 201

    @blueprint.put("/school/content/<item_key>/<int:version>")
    def edit_version(item_key, version):
        data = payload()
        school.edit_version(
            item_key, version, teacher(), data.get("data"), data.get("source")
        )
        return jsonify(ok=True)

    @blueprint.post("/school/content/<item_key>/<int:version>/<action>")
    def transition(item_key, version, action):
        school.transition(item_key, version, teacher(), action, payload().get("note"))
        return jsonify(ok=True)

    return blueprint


def register_identity(app, school, learning, chat_store, questions):
    app.extensions["agenticrag.school_store"] = school
    app.register_blueprint(school_blueprint(school, learning))
    initialized = False
    initialize_lock = Lock()

    @app.before_request
    def identify():
        nonlocal initialized
        if not request.path.startswith("/api/"):
            return None
        if not initialized:
            with initialize_lock:
                if not initialized:
                    school.seed_content(questions)
                    learning.store.freeze_legacy(learning.catalog)
                    initialized = True
        g.school_store = school
        g.current_user = school.user_for_token(request.cookies.get(COOKIE))
        material_upload = request.endpoint == "school.task_upload"
        request.max_content_length = 1200000 if material_upload else 65536
        if request.content_length and request.content_length > (
            1200000 if material_upload else 65536
        ):
            return jsonify(error="请求数据过大"), 413
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
                return jsonify(error="不允许跨来源修改数据"), 403
            if not request.is_json and not (
                material_upload and request.mimetype == "multipart/form-data"
            ):
                return jsonify(error="请求须为 JSON 对象"), 400
        public = request.path in {
            "/api/identity/session",
            "/api/identity/setup",
            "/api/identity/register",
            "/api/identity/login",
            "/api/learning/demo",
        } or request.path.startswith("/api/learning-demo/")
        if public:
            return None
        if not g.current_user:
            return jsonify(error="请先登录"), 401
        user = g.current_user
        if request.path.startswith("/api/chats/"):
            chat_id = (request.view_args or {}).get("chat_id")
            if (
                not chat_id
                or chat_store.get_session(chat_id) is None
                or not school.can_read_chat(chat_id, user)
            ):
                return jsonify(error="chat 不存在"), 404
        if (
            request.path.startswith("/api/learning/")
            or request.endpoint == "chat_message_stream"
        ):
            g.learning_service = LearningService(
                LearningStore(
                    learning.store.path,
                    owner_id=user["id"],
                    include_legacy=user["role"] == "admin",
                ),
                learning.solver,
                catalog=school.catalog(
                    "training", preview=user["role"] in {"teacher", "admin"}
                ),
                tasks=TaskService(school, learning.store, learning.solver, user),
            )
            attempt_id = (request.view_args or {}).get("attempt_id")
            if request.endpoint == "chat_message_stream":
                chat_payload = request.get_json(silent=True)
                attempt_id = (
                    chat_payload.get("learning_attempt_id")
                    if isinstance(chat_payload, dict)
                    else None
                )
                if attempt_id is not None and not isinstance(attempt_id, str):
                    return jsonify(error="练习编号须为字符串"), 400
            if attempt_id and request.method not in {"GET", "HEAD", "OPTIONS"}:
                try:
                    state = g.learning_service.store.get(attempt_id)
                    if state.get("class_id"):
                        school.class_access(state["class_id"], user)
                except LookupError:
                    return jsonify(error="练习不存在或已失去班级访问权限"), 404

    @app.after_request
    def private_cache(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
