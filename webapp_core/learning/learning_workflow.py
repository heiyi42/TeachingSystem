from __future__ import annotations

from agenticRAG.workflow_checkpoint import checkpoint_run

from webapp_core.learning.learning_dialogue import LearningDialogueService
from webapp_core.learning.learning_plan import LearningPlanService
from webapp_core.learning.learning_service import LearningConflict
from webapp_core.runtime.workflow_runs import WorkflowConflict
from webapp_core.assistant.assistant_store import AssistantStore


class LearningWorkflow:
    """Reuse request locks and checkpoints without storing grades in the workflow DB."""

    def __init__(self, learning, runs):
        self.learning = learning
        self.runs = runs
        self.owner = learning.store.owner_id or "demo"

    def key(self, kind, target):
        return f"learning:{self.owner}:{kind}:{target}"

    def current(self, kind, target):
        if kind == "learning_dialogue":
            source = self.learning.store.get(target)
            if source.get("assignment_id"):
                raise LearningConflict("请在课外训练中使用互动诊断")
            if source.get("class_id") and self.learning.tasks:
                self.learning.tasks.school.class_access(
                    source["class_id"], self.learning.tasks.user
                )
            personal = AssistantStore(self.learning.store.path).view(self.owner)
            return (
                [
                    source["updated_at"],
                    personal["enabled"],
                    personal["epoch"],
                    personal["version"],
                ],
                (source.get("dialogue") or {}).get("workflow_run_id"),
            )
        if kind == "learning_plan":
            plan = LearningPlanService(self.learning)
            snapshot = plan.view(target, include_ai=False)
            saved = self.learning.store.ai_study_plan(target) or {}
            committed = (
                saved.get("workflow_run_id")
                if saved.get("signature") == snapshot["signature"]
                else None
            )
            return snapshot["signature"], committed
        raise ValueError("未知学习工作流")

    def status(self, kind, target):
        fingerprint, committed = self.current(kind, target)
        key = self.key(kind, target)
        row = self.runs.latest(self.owner, key)
        public = self.runs.public(self.owner, key)
        if not row or not public:
            return None
        if committed == row["id"]:
            public.update(status="done", can_resume=False, error="")
        elif public["status"] != "running" and fingerprint != row["context"].get(
            "fingerprint"
        ):
            public.update(
                status="stale",
                can_resume=False,
                error="学习记录或目标已变化，请重新生成。",
            )
        return {k: public[k] for k in ("id", "status", "can_resume", "error")}

    async def execute(self, kind, target, data, llm):
        self.current(kind, target)  # Authorize before reading any saved request.
        resume_id = data.get("resume_run_id")
        if resume_id is not None and (not isinstance(resume_id, str) or not resume_id):
            raise ValueError("恢复任务编号无效")
        try:
            run = self.runs.claim(
                self.owner,
                self.key(kind, target),
                {"mode": kind, "data": data},
                resume_id,
            )
        except WorkflowConflict as error:
            raise LearningConflict(str(error)) from error
        token = checkpoint_run.set((str(self.runs.path), run.id))
        try:
            fingerprint, committed = self.current(kind, target)
            if run.resume:
                if committed == run.id:
                    run.save(status="done", error="")
                    return (
                        self.learning.get(target)
                        if kind == "learning_dialogue"
                        else LearningPlanService(self.learning).view(target)
                    )
                if fingerprint != run.row["context"].get("fingerprint"):
                    raise LearningConflict("学习记录或目标已变化，请重新生成。")
            else:
                run.save(context={"fingerprint": fingerprint})
            if kind == "learning_dialogue":
                self.learning._editable(self.learning.store.get(target))
                result = await LearningDialogueService(self.learning).advance(
                    target,
                    run.row["payload"]["data"],
                    llm,
                )
            else:
                result = await LearningPlanService(self.learning).generate(target, llm)
            run.save(status="done", error="")
            return result
        except (LearningConflict, LookupError):
            run.save(status="obsolete", error="学习记录或权限已变化，请重新开始。")
            raise
        except BaseException:
            run.save(status="failed", error="本次执行中断，可继续已保存的步骤。")
            raise
        finally:
            checkpoint_run.reset(token)
            run.close()
