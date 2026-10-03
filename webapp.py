from __future__ import annotations

import atexit
import time
from pathlib import Path
from threading import Lock

from flask import (
    Flask,
    Response,
    current_app,
    g,
    jsonify,
    request,
    send_from_directory,
)

from agenticRAG.cli_utils import build_memory_factory
from agenticRAG.short_memory import shutdown_shared_conversation_memories
from webapp_core import config as cfg
from webapp_core.async_runner import async_runner, run_async, submit_async
from webapp_core.chat_service import ChatService
from webapp_core.workflow_runs import WorkflowRuns, WorkflowConflict
from webapp_core.graph_service import Neo4jGraphService
from webapp_core.learning_routes import learning_blueprint
from webapp_core.learning_service import LearningService
from webapp_core.learning_path import LearningPathService
from webapp_core.learning_store import LearningStore
from webapp_core.school_store import SchoolStore
from webapp_core.school_routes import register_identity
from webapp_core.session_store import SessionStore

_STORE_EXT_KEY = "agenticrag.store"
_CHAT_SERVICE_EXT_KEY = "agenticrag.chat_service"
_BOOTSTRAP_STATE_EXT_KEY = "agenticrag.bootstrap_state"
_FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"

_shared_cleanup_registered = False
_shared_cleanup_lock = Lock()


def _build_memory_factory():
    return build_memory_factory(
        use_summary_memory=cfg.WEB_ENABLE_SUMMARY_MEMORY,
        summary_trigger_tokens=cfg.WEB_SUMMARY_TRIGGER_TOKENS,
        max_turns_before_summary=cfg.WEB_MAX_TURNS_BEFORE_SUMMARY,
        keep_recent_turns=cfg.WEB_KEEP_RECENT_TURNS,
    )


def get_store(app: Flask | None = None) -> SessionStore:
    target = app or current_app
    return target.extensions[_STORE_EXT_KEY]


def get_chat_service(app: Flask | None = None) -> ChatService:
    target = app or current_app
    return target.extensions[_CHAT_SERVICE_EXT_KEY]


def _get_bootstrap_state(app: Flask) -> dict[str, object]:
    return app.extensions[_BOOTSTRAP_STATE_EXT_KEY]


def _register_cleanup_hooks(store: SessionStore) -> None:
    global _shared_cleanup_registered
    atexit.register(store.stop)
    with _shared_cleanup_lock:
        if _shared_cleanup_registered:
            return
        atexit.register(shutdown_shared_conversation_memories)
        atexit.register(async_runner.stop)
        _shared_cleanup_registered = True


def bootstrap_app(
    app: Flask,
    *,
    prewarm: bool = True,
    load_sessions: bool = True,
    register_cleanup: bool = True,
) -> Flask:
    state = _get_bootstrap_state(app)
    state_lock = state["lock"]

    with state_lock:
        store = get_store(app)
        chat_service = get_chat_service(app)

        if load_sessions and not bool(state["sessions_loaded"]):
            store.load_sessions_from_disk()
            state["sessions_loaded"] = True

        if prewarm and not bool(state["prewarm_attempted"]):
            try:
                warmed_subjects = run_async(chat_service.prewarm_subject_rags())
                print(f"[INFO] 已预热知识库: {', '.join(warmed_subjects)}")
                state["prewarm_succeeded"] = True
            except Exception as e:
                print(f"[WARN] 知识库预热失败: {e}")
                state["prewarm_succeeded"] = False
            finally:
                state["prewarm_attempted"] = True

        if register_cleanup and not bool(state["cleanup_registered"]):
            _register_cleanup_hooks(store)
            runs = app.extensions["agenticrag.workflow_runs"]
            runs.start_cleanup()
            atexit.register(runs.stop_cleanup)
            state["cleanup_registered"] = True

    return app


def home():
    index_path = _FRONTEND_DIST / "index.html"
    if index_path.exists():
        return send_from_directory(_FRONTEND_DIST, "index.html")
    return jsonify(error="前端页面尚未构建，请先完成前端构建"), 503


def frontend_asset(filename: str):
    return send_from_directory(_FRONTEND_DIST / "assets", filename)


def list_chats():
    school = current_app.extensions["agenticrag.school_store"]
    return jsonify(
        {
            "chats": [
                chat
                for chat in get_store().list_sessions()
                if school.can_read_chat(chat["chat_id"], g.current_user)
            ]
        }
    )


def create_chat():
    payload = request.get_json(silent=True) or {}
    store = get_store()
    mode = store.normalize_mode(payload.get("mode", "instant"))
    session = store.create_session(mode=mode)
    current_app.extensions["agenticrag.school_store"].bind_chat(
        session.chat_id, g.current_user["id"]
    )
    with session.lock:
        data = session.to_public(include_messages=True)
    return jsonify(data)


def get_chat(chat_id: str):
    session = get_store().get_session(chat_id)
    if session is None:
        return jsonify({"error": "chat 不存在"}), 404
    # Terminal state is written after messages; read it first so the snapshot includes them.
    run = current_app.extensions["agenticrag.workflow_runs"].public(
        g.current_user["id"], chat_id
    )
    with session.lock:
        data = session.to_public(include_messages=True)
    data["workflow_run"] = run
    return jsonify(data)


def delete_chat(chat_id: str):
    try:
        deleted = current_app.extensions["agenticrag.workflow_runs"].delete_chat(
            chat_id, get_store().delete_session
        )
    except WorkflowConflict as error:
        return jsonify(error=str(error)), 409
    if not deleted:
        return jsonify({"error": "chat 不存在"}), 404
    return jsonify({"ok": True, "deleted_chat_id": chat_id})


def set_chat_mode(chat_id: str):
    store = get_store()
    session = store.get_or_create_session(chat_id)
    payload = request.get_json(silent=True) or {}
    mode = store.normalize_mode(payload.get("mode", "instant"))
    with session.lock:
        session.mode = mode
        session.updated_at = time.time()
        data = session.to_public()
    store.persist_sessions_safely()
    return jsonify(data)


def set_chat_pin(chat_id: str):
    store = get_store()
    session = store.get_session(chat_id)
    if session is None:
        return jsonify({"error": "chat 不存在"}), 404
    payload = request.get_json(silent=True) or {}
    pinned = bool(payload.get("pinned", True))
    with session.lock:
        session.pinned = pinned
        session.updated_at = time.time()
        data = session.to_public()
    store.persist_sessions_safely()
    return jsonify(data)


def rename_chat(chat_id: str):
    store = get_store()
    session = store.get_session(chat_id)
    if session is None:
        return jsonify({"error": "chat 不存在"}), 404
    payload = request.get_json(silent=True) or {}
    title = store.normalize_manual_chat_title(payload.get("title", ""))
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    with session.lock:
        session.title = title
        session.updated_at = time.time()
        data = session.to_public()
    store.persist_sessions_safely()
    return jsonify(data)


def chat_message_stream(chat_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error="请求须为 JSON 对象"), 400
    if not str(payload.get("message", "")).strip():
        return jsonify(error="message 不能为空"), 400
    if payload.get("resume_run_id"):
        try:
            run = current_app.extensions["agenticrag.workflow_runs"].claim(
                g.current_user["id"], chat_id, {}, str(payload["resume_run_id"])
            )
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except WorkflowConflict as error:
            return jsonify(error=str(error)), 409
        payload = dict(run.row["payload"])
        if payload.get("learning_attempt_id"):
            try:
                LearningPathService(g.learning_service).question_context(
                    payload["learning_attempt_id"]
                )
            except (LookupError, ValueError) as error:
                run.save(status="failed", error=str(error))
                run.close()
                return jsonify(error=str(error)), 409
        return _chat_stream_response(chat_id, payload, run)
    if payload.get("restart_run_id"):
        previous = current_app.extensions["agenticrag.workflow_runs"].latest(
            g.current_user["id"], chat_id
        )
        if not previous or previous["id"] != str(payload["restart_run_id"]):
            return jsonify(error="可重试任务不存在"), 404
        payload = dict(previous["payload"])
        # The stored question already contains its learning context.
        if payload.get("learning_attempt_id"):
            try:
                LearningPathService(g.learning_service).question_context(
                    payload["learning_attempt_id"]
                )
            except (LookupError, ValueError) as error:
                return jsonify(error=str(error)), 409
        try:
            run = current_app.extensions["agenticrag.workflow_runs"].claim(
                g.current_user["id"], chat_id, payload
            )
        except WorkflowConflict as error:
            return jsonify(error=str(error)), 409
        return _chat_stream_response(chat_id, payload, run)
    if payload.get("learning_attempt_id") is not None:
        if not isinstance(payload["learning_attempt_id"], str):
            return jsonify(error="练习编号须为字符串"), 400
        try:
            context = LearningPathService(g.learning_service).question_context(
                payload["learning_attempt_id"]
            )
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 409
        payload["subjects"] = [context["subject_id"]]
        payload["message"] = (
            str(payload.get("message", "")).strip() + "\n\n" + context["prompt"]
        )
    mode = get_store().normalize_mode(
        payload.get("mode", get_store().get_session(chat_id).mode)
    )
    runs = current_app.extensions["agenticrag.workflow_runs"]
    try:
        payload["mode"] = mode
        run = runs.claim(g.current_user["id"], chat_id, payload)
    except WorkflowConflict as error:
        return jsonify(error=str(error)), 409
    return _chat_stream_response(chat_id, payload, run)


def _chat_stream_response(chat_id, payload, run):
    try:
        (
            event_stream_factory,
            error,
        ) = get_chat_service().build_chat_message_stream_handler(
            chat_id,
            payload,
            workflow_run=run,
            can_read=_stream_permission(chat_id),
        )
    except BaseException:
        if run:
            run.close()
        raise
    if error:
        if run:
            run.save(status="failed", error=error[0])
            run.close()
        message, status = error
        return jsonify({"error": message}), status

    response = Response(
        event_stream_factory(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    if run:
        response.headers["X-Workflow-Run"] = run.id
        response.headers["X-Workflow-Execution"] = run.row["execution_id"]
        response.call_on_close(lambda: run.close() if not run.started else None)
    return response


def _stream_permission(chat_id):
    school = current_app.extensions["agenticrag.school_store"]
    user, token = g.current_user, request.cookies.get("gm_session")
    return lambda: school.user_for_token(token) == user and school.can_read_chat(
        chat_id, user
    )


def chat_run_events(chat_id, run_id):
    runs = current_app.extensions["agenticrag.workflow_runs"]
    execution_id = request.args.get("execution_id", "")
    try:
        after = int(request.args.get("after", "0"))
        if after < 0:
            raise ValueError()
        runs.execution(g.current_user["id"], chat_id, run_id, execution_id)
    except ValueError:
        return jsonify(error="无效的事件序号"), 400
    except LookupError as error:
        return jsonify(error=str(error)), 404
    return Response(
        runs.iter_events(
            g.current_user["id"],
            chat_id,
            run_id,
            execution_id,
            after,
            _stream_permission(chat_id),
        ),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def cancel_chat_run(chat_id, run_id):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error="请求须为 JSON 对象"), 400
    try:
        result = current_app.extensions["agenticrag.workflow_runs"].cancel(
            g.current_user["id"], chat_id, run_id, str(payload.get("execution_id", ""))
        )
    except LookupError as error:
        return jsonify(error=str(error)), 404
    return jsonify(result)


def chat_updates_stream():
    school = current_app.extensions["agenticrag.school_store"]
    user = g.current_user
    token = request.cookies.get("gm_session")
    return Response(
        get_chat_service().iter_chat_update_events(
            can_read=lambda chat_id: school.user_for_token(token) == user
            and school.can_read_chat(chat_id, user)
        ),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _register_routes(app: Flask) -> None:
    app.add_url_rule(
        "/api/chats/<chat_id>/runs/<run_id>/events",
        view_func=chat_run_events,
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/chats/<chat_id>/runs/<run_id>/cancel",
        view_func=cancel_chat_run,
        methods=["POST"],
    )
    app.add_url_rule("/", view_func=home, methods=["GET"])
    app.add_url_rule(
        "/assets/<path:filename>", view_func=frontend_asset, methods=["GET"]
    )
    app.add_url_rule("/api/chats", view_func=list_chats, methods=["GET"])
    app.add_url_rule("/api/chats", view_func=create_chat, methods=["POST"])
    app.add_url_rule("/api/chats/<chat_id>", view_func=get_chat, methods=["GET"])
    app.add_url_rule(
        "/api/chats/<chat_id>/delete",
        view_func=delete_chat,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/chats/<chat_id>/mode",
        view_func=set_chat_mode,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/chats/<chat_id>/pin",
        view_func=set_chat_pin,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/chats/<chat_id>/rename",
        view_func=rename_chat,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/chats/<chat_id>/messages/stream",
        view_func=chat_message_stream,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/events/chat-updates",
        view_func=chat_updates_stream,
        methods=["GET"],
    )


def create_app(
    *,
    bootstrap: bool = False,
    prewarm: bool = False,
    load_sessions: bool = False,
    register_cleanup: bool = True,
    learning_store_path: str | Path | None = None,
) -> Flask:
    app = Flask(__name__, template_folder=None)
    store = SessionStore(_build_memory_factory())
    chat_service = ChatService(store, run_async, submit_async)
    graph_service = Neo4jGraphService()
    chat_service.graph_service = graph_service

    app.extensions[_STORE_EXT_KEY] = store
    app.extensions[_CHAT_SERVICE_EXT_KEY] = chat_service
    app.extensions[_BOOTSTRAP_STATE_EXT_KEY] = {
        "lock": Lock(),
        "cleanup_registered": False,
        "sessions_loaded": False,
        "prewarm_attempted": False,
        "prewarm_succeeded": False,
    }

    _register_routes(app)
    learning_service = LearningService(
        LearningStore(learning_store_path or cfg.WEB_LEARNING_STORE_PATH),
        chat_service.problem_tutoring_service,
    )
    app.extensions["agenticrag.learning_service"] = learning_service
    app.extensions["agenticrag.workflow_runs"] = WorkflowRuns(
        learning_service.store.path.with_name(
            f"{learning_service.store.path.stem}_workflows.sqlite3"
        )
    )
    app.register_blueprint(learning_blueprint(learning_service))
    app.register_blueprint(learning_blueprint(learning_service, demo=True))
    school = SchoolStore(
        learning_service.store.path.with_name(
            f"{learning_service.store.path.stem}_school.sqlite3"
        )
    )
    questions = chat_service.problem_tutoring_service.load_question_bank()
    register_identity(app, school, learning_service, store, questions)
    chat_service.problem_tutoring_service.question_bank_provider = (
        lambda: school.catalog("qa").values()
    )

    if bootstrap:
        bootstrap_app(
            app,
            prewarm=prewarm,
            load_sessions=load_sessions,
            register_cleanup=register_cleanup,
        )

    return app


app = create_app()
store = get_store(app)
chat_service = get_chat_service(app)


def main() -> None:
    bootstrap_app(app, prewarm=True, load_sessions=True)
    app.run(host=cfg.WEB_HOST, port=cfg.WEB_PORT, debug=cfg.WEB_DEBUG)


if __name__ == "__main__":
    main()
