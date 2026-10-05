import { ProcessWalkthrough } from "./ProcessWalkthrough";
import { LearningDialogue } from "./LearningDialogue";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api as mainApi, createLearningApi } from "../../api";
import { useWorkbenchStore } from "../../store";
import demoRows from "../../learningDemo.json";
import type {
  LearningAttempt,
  LearningRecord,
  LearningRow,
  LearningSolution,
  LearningSubject,
  LearningQuestionContext,
} from "../../learningTypes";

const STATUS = {
  in_progress: "作答中",
  needs_correction: "待订正",
  passed: "已通过",
};
function recordResult(record: LearningRecord) {
  if (record.assignment?.feedback_hidden) return "待公布";
  if (record.status !== "passed") return STATUS[record.status];
  if (record.first_unassisted_pass) return "独立通过";
  if (record.hint_count || record.solution_viewed || record.tutoring_viewed) return "辅助完成";
  if (record.correction_count) return "订正通过";
  return record.previously_seen ? "重复练习通过" : "辅助完成";
}

type WorkspaceProps = {
  userId: string;
  initialAttemptId?: string;
  requestNonce?: number;
  reviewReturnId?: string;
  onBackToReview?: () => void;
  onBackToTask?: (taskId: string) => void;
  onExitGuestDemo?: () => void;
  section: "training" | "records";
  onOpenCourse: (subject: LearningSubject) => void;
  onQuestion?: (context: LearningQuestionContext) => void;
  onSectionChange: (section: "training" | "records") => void;
};

export function TrainingWorkspace(props: WorkspaceProps) {
  const [demoId, setDemoId] = useState(
    () => sessionStorage.getItem(`gm.learning.${props.userId}.demo`) || "",
  );
  useEffect(() => {
    if (props.initialAttemptId) {
      sessionStorage.removeItem(`gm.learning.${props.userId}.demo`);
      setDemoId("");
    }
  }, [props.requestNonce]);
  return (
    <TrainingSession
      key={`${props.userId}:${demoId || "practice"}:${props.requestNonce || 0}`}
      {...props}
      initialAttemptId={demoId ? undefined : props.initialAttemptId}
      demoId={demoId}
      onModeChange={(id) => {
        if (id) sessionStorage.setItem(`gm.learning.${props.userId}.demo`, id);
        else sessionStorage.removeItem(`gm.learning.${props.userId}.demo`);
        if (!id && props.userId === "guest") props.onExitGuestDemo?.();
        setDemoId(id);
      }}
    />
  );
}

function TrainingSession({
  userId,
  initialAttemptId,
  onBackToTask,
  reviewReturnId,
  onBackToReview,
  section,
  onSectionChange,
  onOpenCourse,
  onQuestion,
  demoId,
  onModeChange,
}: WorkspaceProps & {
  demoId: string;
  onModeChange: (id: string) => void;
}) {
  const [recordCourse, setRecordCourse] = useState("");
  const [recordStatus, setRecordStatus] = useState("");
  const [recordReturnId, setRecordReturnId] = useState("");
  const [classId, setClassId] = useState("");
  const classes = useQuery({
    queryKey: ["school-classes", userId],
    queryFn: mainApi.schoolClasses,
    enabled: !demoId,
  });
  const api = createLearningApi(demoId, classId);
  const ACTIVE_KEY = demoId
    ? `gm.learning.demo.${demoId}.attempt`
    : `gm.learning.${userId}.attempt`;
  const DRAFT_KEY = demoId
    ? `gm.learning.demo.${demoId}.draft`
    : `gm.learning.${userId}.draft`;
  const queryClient = useQueryClient();
  const [view, setView] = useState<"exercises" | "answer" | "records">(
    section === "records" ? "records" : "exercises",
  );
  const subject = useWorkbenchStore(state => state.preferredSubject);
  const setSubject = useWorkbenchStore(state => state.setPreferredSubject);
  const [chapter, setChapter] = useState("");
  const [assistanceHidden, setAssistanceHidden] = useState(false);
  const [algorithm, setAlgorithm] = useState("全部");
  const [difficulty, setDifficulty] = useState("全部");
  useEffect(() => { setChapter(""); setAlgorithm("全部"); setDifficulty("全部"); }, [subject]);
  const [attempt, setAttempt] = useState<LearningAttempt | null>(null);
  const [rows, setRows] = useState<LearningRow[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [appealReason, setAppealReason] = useState("");
  const [appealNumber, setAppealNumber] = useState(0);
  useEffect(() => {
    setAppealReason("");
    setAppealNumber(0);
  }, [attempt?.id]);
  const [saveStatus, setSaveStatus] = useState("已保存");
  const timer = useRef<number | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pending = useRef<{ id: string; rows: LearningRow[] } | null>(null);
  const currentId = useRef<string | null>(null);
  const courses = useQuery({
    queryKey: ["learning-courses", userId, demoId],
    queryFn: api.learningCourses,
    retry: false,
  });
  const exercises = useQuery({
    queryKey: ["learning-exercises", userId, demoId],
    queryFn: api.learningExercises,
    retry: false,
  });
  const progress = useQuery({
    queryKey: ["learning-progress", userId, demoId],
    queryFn: api.learningProgress,
    enabled: view === "records",
    retry: false,
  });

  const visibleRecords = (progress.data?.records || []).filter(record =>
    (!recordCourse || record.exercise.subject_id === recordCourse) &&
    (!recordStatus || recordResult(record) === recordStatus)
  );

  useEffect(() => {
    if (
      classId &&
      !classes.data?.classes.some(
        (item) => item.id === classId && item.course_id === subject,
      )
    )
      setClassId("");
  }, [subject, classes.data]);

  function enqueue<T>(action: () => Promise<T>): Promise<T> {
    const next = queue.current.catch(() => undefined).then(action);
    queue.current = next;
    return next;
  }

  function adopt(next: LearningAttempt) {
    currentId.current = next.id;
    setSubject(next.exercise.subject_id);
    setChapter("");
    setAlgorithm("全部");
    setAttempt(next);
    setRows(next.draft);
    localStorage.setItem(ACTIVE_KEY, next.id);
    setSaveStatus("已保存");
  }

  async function flushDraft() {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    const snapshot = pending.current;
    if (!snapshot) return;
    setSaveStatus("保存中");
    try {
      const next = await enqueue(() =>
        api.saveLearningDraft(snapshot.id, snapshot.rows),
      );
      if (currentId.current === snapshot.id) setAttempt(next);
      if (pending.current === snapshot) {
        pending.current = null;
        setRows(next.draft);
        localStorage.removeItem(DRAFT_KEY);
        setSaveStatus("已保存");
      }
    } catch (reason) {
      if (attempt?.assignment) {
        const latest = await api.getLearning(snapshot.id).catch(() => null);
        if (latest?.assignment && !latest.assignment.can_edit) {
          pending.current = null;
          localStorage.removeItem(DRAFT_KEY);
          adopt(latest);
          setSaveStatus("任务已锁定，以已保存作答为准");
          return;
        }
      }
      setSaveStatus("保存失败");
      throw reason;
    }
  }

  function scheduleDraft(id: string, next: LearningRow[]) {
    pending.current = { id, rows: next };
    localStorage.setItem(DRAFT_KEY, JSON.stringify(pending.current));
    setSaveStatus("待保存");
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      flushDraft().catch((reason) =>
        setError(reason instanceof Error ? reason.message : String(reason)),
      );
    }, 650);
  }

  useEffect(() => {
    let cancelled = false;
    const id = initialAttemptId || localStorage.getItem(ACTIVE_KEY);
    async function restore() {
      try {
        if (!id) return;
        const next = await api.getLearning(id);
        if (cancelled) return;
        adopt(next);
        const cached = localStorage.getItem(DRAFT_KEY);
        if (
          cached &&
          next.status !== "passed" &&
          (!next.assignment || next.assignment.can_edit)
        ) {
          try {
            const draft = JSON.parse(cached) as {
              id: string;
              rows: LearningRow[];
            };
            if (draft.id === id && Array.isArray(draft.rows)) {
              setRows(draft.rows);
              scheduleDraft(id, draft.rows);
            }
          } catch {
            localStorage.removeItem(DRAFT_KEY);
          }
        }
        if (section === "training")
          setView((current) => (current === "records" ? current : "answer"));
      } catch (reason) {
        if (!cancelled)
          setError(
            `恢复练习失败：${reason instanceof Error ? reason.message : String(reason)}`,
          );
      } finally {
        if (!cancelled) setBusy(false);
      }
    }
    restore();
    return () => {
      cancelled = true;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  useEffect(() => {
    if (section === "records") setView("records");
    else
      setView((current) =>
        current === "records"
          ? currentId.current
            ? "answer"
            : "exercises"
          : current,
      );
  }, [section]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
      if (!demoId)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["learning-path", userId] }),
          queryClient.invalidateQueries({ queryKey: ["study-plan", userId] }),
        ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  function changeView(next: typeof view) {
    run(async () => {
      await flushDraft();
      onSectionChange(next === "records" ? "records" : "training");
      setView(next);
    });
  }

  function start(exerciseId?: string, parentId?: string) {
    run(async () => {
      await flushDraft();
      adopt(await enqueue(() => api.startLearning(exerciseId, parentId)));
      onSectionChange("training");
      setView("answer");
      queryClient.invalidateQueries({
        queryKey: ["learning-progress", userId, demoId],
      });
    });
  }

  function cancelRecord(id: string) {
    run(async () => {
      await flushDraft();
      await enqueue(() => api.cancelLearning(id));
      if (currentId.current === id) {
        currentId.current = null;
        pending.current = null;
        setAttempt(null);
        setRows([]);
        localStorage.removeItem(ACTIVE_KEY);
        localStorage.removeItem(DRAFT_KEY);
      }
      await progress.refetch();
    });
  }

  function openRecord(id: string) {
    run(async () => {
      await flushDraft();
      adopt(await enqueue(() => api.getLearning(id)));
      setRecordReturnId(id);
      onSectionChange("training");
      setView("answer");
    });
  }

  function updateRows(next: LearningRow[]) {
    if (!attempt || busy || attempt.status === "passed") return;
    setRows(next);
    scheduleDraft(attempt.id, next);
  }

  function updateField(index: number, field: string, value: string) {
    updateRows(
      rows.map((row, i) => (i === index ? { ...row, [field]: value } : row)),
    );
  }

  function submit() {
    if (!attempt) return;
    const id = attempt.id;
    const snapshot = rows;
    run(async () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      const next = await enqueue(() => api.submitLearning(id, snapshot));
      pending.current = null;
      localStorage.removeItem(DRAFT_KEY);
      adopt(next);
      queryClient.invalidateQueries({
        queryKey: ["learning-progress", userId, demoId],
      });
    });
  }

  function assistance(kind: "hint" | "solution") {
    if (!attempt) return;
    const id = attempt.id;
    run(async () => {
      await flushDraft();
      const next = await enqueue(() =>
        kind === "hint" ? api.learningHint(id) : api.learningSolution(id),
      );
      adopt(next);
      queryClient.invalidateQueries({
        queryKey: ["learning-progress", userId, demoId],
      });
    });
  }

  function switchMode(enterDemo: boolean) {
    run(async () => {
      await flushDraft();
      await queue.current;
      let id = "";
      if (enterDemo) {
        const created = await mainApi.createLearningDemo();
        id = created.demo_id;
        await createLearningApi(id).saveLearningDraft(
          created.attempt.id,
          demoRows.wrong,
        );
        localStorage.setItem(
          `gm.learning.demo.${id}.attempt`,
          created.attempt.id,
        );
      }
      onSectionChange("training");
      onModeChange(id);
    });
  }

  const exercise = attempt?.exercise;
  const page = exercise?.kind === "page_replacement";
  const cQuestion =
    exercise?.kind === "c_trace" ||
    exercise?.kind === "c_repair" ||
    exercise?.kind === "c_program" ||
    exercise?.kind === "security_lab";
  const labProgram = exercise?.kind === "security_lab";
  const fullProgram = exercise?.kind === "c_program" || labProgram;
  const pvDesign = exercise?.kind === "pv_design";
  const codeRepair = exercise?.kind === "c_repair" || fullProgram;
  const banker = exercise?.kind === "banker";
  const security = [
    "process_states",
    "thread_resources",
    "file_allocation",
    "dh",
    "access_control",
    "log_evidence",
    "readers_writers",
    "pv_trace",
    "pv_design",
    "resource_request",
    "schedule_metrics",
    "disk_schedule",
    "clock_trace",
    "address_translation",
    "auth_flow",
    "signature_check",
  ].includes(exercise?.kind || "");
  const bankerProcesses = exercise?.parameters.banker_processes || [];
  const passed = attempt?.status === "passed";
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    if (!attempt?.assignment) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [attempt?.assignment?.id]);
  const deadlineExpired = Boolean(
    attempt?.assignment &&
    clock / 1000 > (attempt.assignment.late_until || attempt.assignment.due_at),
  );
  const locked =
    passed ||
    deadlineExpired ||
    Boolean(attempt?.assignment && !attempt.assignment.can_edit);
  const firstError = attempt?.evaluation?.first_error;
  const dirty = Boolean(
    attempt?.draft_dirty ||
    (attempt?.evaluation &&
      JSON.stringify(rows) !== JSON.stringify(attempt.draft)),
  );
  const filtered =
    exercises.data?.exercises.filter(
      (item) =>
        item.subject_id === subject &&
        (!chapter || item.chapter_id === chapter) &&
        (algorithm === "全部" || item.algorithm === algorithm) &&
        (difficulty === "全部" || item.difficulty === difficulty),
    ) || [];

  const selectedCourse = courses.data?.courses.find(
    (item) => item.id === subject,
  );
  const algorithms = Array.from(
    new Set(
      exercises.data?.exercises
        .filter(
          (item) =>
            item.subject_id === subject &&
            (!chapter || item.chapter_id === chapter),
        )
        .map((item) => item.algorithm) || [],
    ),
  );

  return (
    <main className="training-surface">
      {demoId && (
        <div className="training-demo-controls">
          <p>
            演示记录单独保存。预设作答用于展示流程，记录中的“独立通过”为模拟结果。
          </p>
          <div>
            <button disabled={busy} onClick={() => switchMode(false)}>退出演示</button>
            <button disabled={busy} onClick={() => switchMode(true)}>
              重新演示
            </button>
            {view === "answer" && !passed && exercise?.id === "lru_01" && (
              <>
                <button
                  disabled={busy}
                  onClick={() => updateRows(demoRows.wrong)}
                >
                  填入错答
                </button>
                <button
                  disabled={busy}
                  onClick={() => updateRows(demoRows.correct)}
                >
                  填入订正
                </button>
              </>
            )}
            {view === "answer" && !passed && exercise?.id === "lru_02" && (
              <button
                disabled={busy}
                onClick={() => updateRows(demoRows.retest)}
              >
                填入复测作答
              </button>
            )}
          </div>
          <p>
            操作顺序：提交错答 → 查看两级提示 → 填入订正并提交 → 开始复测 →
            填入复测作答并提交 → 学习记录。
          </p>
        </div>
      )}
      <nav className="training-tabs" aria-label="训练视图">
        {(
          [
            ["exercises", "选题"],
            ["answer", "过程作答"],
            ["records", "学习记录"],
          ] as const
        ).map(([id, title]) => (
          <button
            key={id}
            className={view === id ? "selected" : ""}
            disabled={busy || (id === "answer" && !attempt)}
            onClick={() => changeView(id)}
          >
            {title}
          </button>
        ))}
      </nav>
      {view === "answer" && attempt && (recordReturnId === attempt.id || (reviewReturnId === attempt.id && onBackToReview)) && (
        <div className="training-return">
          <button className="training-link" disabled={busy} onClick={() => run(async () => {
            await flushDraft();
            if (recordReturnId === attempt.id) {
              onSectionChange("records");
              setView("records");
            }
            else onBackToReview?.();
          })}>
            {recordReturnId === attempt.id ? "返回学习记录" : "返回错题复习"}
          </button>
        </div>
      )}
      {error && (
        <div className="training-error" role="alert">
          {error}
          <button onClick={() => setError("")} aria-label="关闭错误提示">
            关闭
          </button>
        </div>
      )}
      <div className="training-content">
        {busy && !attempt && (
          <p className="training-muted" role="status">
            正在加载练习…
          </p>
        )}
        {view === "exercises" && (
          <section className="exercise-list">
            <h2 className="section-label">选择课程</h2>
            {courses.isPending && <p role="status">正在读取课程…</p>}
            {courses.error && (
              <p role="alert">
                课程加载失败：{courses.error.message}{" "}
                <button
                  className="training-link"
                  onClick={() => courses.refetch()}
                >
                  重试
                </button>
              </p>
            )}
            <div
              className="training-courses"
              role="group"
              aria-label="选择课程"
            >
              {courses.data?.courses.map((course) => (
                <button
                  key={course.id}
                  className={subject === course.id ? "selected" : ""}
                  aria-pressed={subject === course.id}
                  disabled={busy}
                  onClick={() => {
                    setSubject(course.id);
                    setChapter("");
                    setAlgorithm("全部");
                  }}
                >
                  {course.name}
                  <small>
                    {course.exercise_count
                      ? `${course.exercise_count} 道训练题`
                      : "暂无开放训练"}
                  </small>
                </button>
              ))}
            </div>
            {selectedCourse && (
              <details className="training-course-info">
                <summary>课程说明与问答入口</summary>
                <p>逐步填写解题过程，提交后检查首个错步。订正完成后可换一道新题复测。</p>
                <p>已有功能：{selectedCourse.capabilities.join("、")}。</p>
                <p className="training-muted">{selectedCourse.planned}</p>
                <button
                  className="training-link"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await flushDraft();
                      onOpenCourse(subject);
                    })
                  }
                >
                  进入{selectedCourse.name}问答
                </button>
              </details>
            )}
            <div className="exercise-results-heading">
              <h2>{selectedCourse?.name || "课程"}练习</h2>
              <span>{filtered.length} 道可选题目</span>
            </div>
            <div className="training-filters">
            {!demoId && (
              <label className="training-record-filter">
                记录归属
                <select
                  aria-label="练习记录归属"
                  value={classId}
                  onChange={(event) => setClassId(event.target.value)}
                >
                  <option value="">个人练习</option>
                  {classes.data?.classes
                    .filter((item) => item.course_id === subject)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.title}
                      </option>
                    ))}
                </select>
              </label>
            )}

              <label>
                章节
                <select
                  aria-label="章节"
                  value={chapter}
                  onChange={(e) => {
                    setChapter(e.target.value);
                    setAlgorithm("全部");
                  }}
                >
                  <option value="">全部章节</option>
                  {selectedCourse?.chapters.map((item) => (
                    <option key={item.id} value={item.id}>
                      {subject === "cybersec_lab"
                        ? item.number
                          ? `实验 ${item.number} · `
                          : ""
                        : `第 ${item.number} 章 · `}
                      {item.title} ·{" "}
                      {item.exercise_count
                        ? `${item.exercise_count} 题`
                        : "暂无开放训练"}
                    </option>
                  ))}
                </select>
              </label>
              {Boolean(selectedCourse?.exercise_count) && (
                <>
                  <label>
                    {subject === "operating_systems" ? "算法" : "题型"}
                    <select
                      value={algorithm}
                      onChange={(e) => setAlgorithm(e.target.value)}
                    >
                      {["全部", ...algorithms].map((id) => (
                        <option key={id}>{id}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    难度
                    <select
                      value={difficulty}
                      onChange={(e) => setDifficulty(e.target.value)}
                    >
                      {["全部", "基础", "进阶"].map((id) => (
                        <option key={id}>{id}</option>
                      ))}
                    </select>
                  </label>
                </>
              )}
            </div>
            {exercises.isPending && <p>正在读取题目…</p>}
            {exercises.error && (
              <p role="alert">
                题目加载失败：{exercises.error.message}{" "}
                <button
                  className="training-link"
                  onClick={() => exercises.refetch()}
                >
                  重试
                </button>
              </p>
            )}
            {exercises.data && (
              <div className="training-table-wrap">
                {filtered.length > 0 && (
                  <table className="training-table training-exercise-table">
                    <thead>
                      <tr>
                        <th>题目</th>
                        <th>难度</th>
                        <th>条件</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map((item) => (
                        <tr key={item.id}>
                          <td>
                            {item.title}
                          </td>
                          <td>{item.difficulty}</td>
                          <td>
                            {item.parameters.security_checks ||
                            item.parameters.checkpoints
                              ? `${(item.parameters.security_checks || item.parameters.checkpoints)?.length} 个检查点`
                              : item.kind === "page_replacement"
                                ? `${item.parameters.frames} 个页框 · ${item.parameters.sequence?.length} 次访问`
                                : item.kind === "banker"
                                  ? `${item.parameters.banker_processes?.length} 个进程 · ${item.parameters.available?.length} 类资源`
                                  : `${item.parameters.processes?.length} 个进程${item.parameters.quantum ? ` · 时间片 ${item.parameters.quantum}` : ""}`}
                          </td>
                          <td>
                            <button
                              className="ghost-action exercise-start"
                              disabled={busy}
                              onClick={() => start(item.id)}
                            >
                              开始练习
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {!filtered.length && (
                  <p>
                    {selectedCourse?.exercise_count === 0
                      ? "本课程暂无已发布训练，请等待教师审核发布。"
                      : chapter &&
                          selectedCourse?.chapters.find(
                            (item) => item.id === chapter,
                          )?.exercise_count === 0
                        ? "本章暂无已发布训练。"
                        : "暂无符合筛选条件的题目。"}
                  </p>
                )}
              </div>
            )}
          </section>
        )}
        {view === "answer" && attempt?.assignment && (
          <section className="task-banner">
            <strong>{attempt.assignment.title}</strong>
            <span>
              {" "}
              · 截止{" "}
              {new Date(attempt.assignment.due_at * 1000).toLocaleString(
                "zh-CN",
              )}{" "}
              · 每题最多 {attempt.assignment.max_submissions} 次核验
            </span>
            <p>
              {attempt.assignment.feedback_hidden
                ? "测验结束前不公布核验结果。交卷请返回任务页面；已保存草稿会在交卷时核验。"
                : "作答属于班级任务，完成后请返回任务页面交卷。"}
              {!attempt.assignment.can_edit && " 当前作答已锁定。"}
            </p>
            <button
              className="training-link"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  if (!deadlineExpired) await flushDraft();
                  else pending.current = null;
                  adopt(await api.getLearning(attempt.id));
                })
              }
            >
              刷新任务状态
            </button>
            <button
              className="ghost-action"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  if (!deadlineExpired) await flushDraft();
                  else pending.current = null;
                  onBackToTask?.(attempt.assignment!.id);
                })
              }
            >
              返回任务 / 交卷
            </button>
          </section>
        )}
        {view === "answer" && attempt && exercise && (
          <div className={`training-answer-layout${assistanceHidden ? " assistance-hidden" : ""}`}>
            <section className="training-answer">
              <div className="training-answer-toolbar">
              <div className="training-breadcrumb">
                {exercise.subject_name} / {exercise.chapter_title} /{" "}
                {exercise.algorithm} · 版本 {attempt.content_version}
                {attempt.parent_id ? " / 新题复测" : ""}
              </div>
              {assistanceHidden && <button type="button" className="training-link" aria-expanded={false} onClick={() => setAssistanceHidden(false)}>展开练习辅助</button>}
              </div>
              <div className="training-exercise-title">
                <h2>{exercise.title}</h2>
                <span>{exercise.difficulty}</span>
              </div>
              {security ? (
                <>
                  <p>{exercise.parameters.description}</p>
                  {exercise.parameters.users && (
                    <div className="training-table-wrap">
                      <table className="training-table">
                        <thead>
                          <tr>
                            <th>用户</th>
                            <th>已分配角色</th>
                          </tr>
                        </thead>
                        <tbody>
                          {exercise.parameters.users.map((user) => (
                            <tr key={user.name}>
                              <td>{user.name}</td>
                              <td>{user.roles.join("、") || "无"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <table className="training-table">
                        <thead>
                          <tr>
                            <th>授权角色</th>
                            <th>资源</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {exercise.parameters.grants?.map((grant, i) => (
                            <tr key={i}>
                              <td>{grant.role}</td>
                              <td>{grant.resource}</td>
                              <td>{grant.action}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {exercise.parameters.logs && (
                    <div className="training-table-wrap">
                      <table className="training-table">
                        <thead>
                          <tr>
                            <th>编号</th>
                            <th>时间</th>
                            <th>用户</th>
                            <th>来源</th>
                            <th>登录结果</th>
                          </tr>
                        </thead>
                        <tbody>
                          {exercise.parameters.logs.map((log) => (
                            <tr key={log.id}>
                              <td>{log.id}</td>
                              <td>{log.time}</td>
                              <td>{log.user}</td>
                              <td>{log.source}</td>
                              <td>{log.outcome}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              ) : cQuestion ? (
                <>
                  <p>{exercise.parameters.description}</p>
                  {exercise.parameters.code && (
                    <pre className="training-code">
                      <code>{exercise.parameters.code}</code>
                    </pre>
                  )}
                </>
              ) : banker ? (
                <>
                  <p>
                    资源列顺序：{exercise.parameters.resources?.join(", ")}
                    ；Available = {exercise.parameters.available?.join(", ")}。
                  </p>
                  <div className="training-table-wrap">
                    <table className="training-table">
                      <thead>
                        <tr>
                          <th>进程</th>
                          <th>Allocation</th>
                          <th>Max</th>
                        </tr>
                      </thead>
                      <tbody>
                        {bankerProcesses.map((p) => (
                          <tr key={p.name}>
                            <td>{p.name}</td>
                            <td>{p.allocation.join(", ")}</td>
                            <td>{p.maximum.join(", ")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : page ? (
                <>
                  <p>
                    内存有 {exercise.parameters.frames}{" "}
                    个页框，初始为空。请填写每次访问后的页框状态。
                  </p>
                  <div className="training-sequence">
                    <strong>访问序列</strong>
                    {exercise.parameters.sequence?.map((value, i) => (
                      <span key={i}>{value}</span>
                    ))}
                  </div>
                </>
              ) : (
                <>
                  <p>
                    请按执行顺序填写进程、开始时间与结束时间。
                    {exercise.parameters.quantum
                      ? `时间片为 ${exercise.parameters.quantum}。`
                      : ""}
                    时间单位为同一计时单位。
                  </p>
                  <table className="training-table training-processes">
                    <thead>
                      <tr>
                        <th>进程</th>
                        <th>到达时间</th>
                        <th>服务时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {exercise.parameters.processes?.map((process) => (
                        <tr key={process.name}>
                          <td>{process.name}</td>
                          <td>{process.arrival}</td>
                          <td>{process.service}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
              <p className="training-muted">{exercise.rules}</p>
              <details className="training-support" key={attempt.id}>
                <summary>过程推演与运行回放</summary>
              <ProcessWalkthrough attempt={attempt} busy={busy}
                onAdvance={async (prediction) => {
                  let completed = false;
                  await run(async () => {
                    await flushDraft();
                    adopt(await api.learningWalkthrough(attempt.id, attempt.walkthrough?.revision || 0, prediction));
                    completed = true;
                  });
                  return completed;
                }} />
              </details>

              {attempt.previously_seen && (
                <p className="training-muted">
                  这道题已练习过，本次通过将记为重复练习。
                </p>
              )}
              {security ? (
                <div className="training-table-wrap">
                  <table className="training-table training-input-table training-checkpoint-table">
                    <thead>
                      <tr>
                        <th>步骤</th>
                        <th>检查点</th>
                        <th>作答</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, index) => {
                        const check =
                          exercise.parameters.security_checks?.[index];
                        const status = attempt.evaluation?.row_statuses[index];
                        return (
                          <tr
                            key={index}
                            className={`answer-row ${status || ""}`}
                          >
                            <td>
                              {index + 1}
                              {status === "error" && (
                                <small>
                                  {pvDesign ? "需检查的进程" : "首个错误"}
                                </small>
                              )}
                              {status === "pending" && (
                                <small>待订正后检查</small>
                              )}
                            </td>
                            <td>{check?.label}</td>
                            <td>
                              {check?.options ? (
                                <select
                                  aria-label={`第${index + 1}步作答`}
                                  value={row.value}
                                  className={
                                    status === "error" ? "field-error" : ""
                                  }
                                  disabled={busy || locked}
                                  onChange={(e) =>
                                    updateField(index, "value", e.target.value)
                                  }
                                >
                                  <option value="">请选择</option>
                                  {check.options.map((option) => (
                                    <option key={option}>{option}</option>
                                  ))}
                                </select>
                              ) : (
                                <input
                                  aria-label={`第${index + 1}步作答`}
                                  value={row.value}
                                  className={
                                    status === "error" ? "field-error" : ""
                                  }
                                  placeholder={
                                    check?.format === "rw_active" ||
                                    check?.format === "rw_queue"
                                      ? "如 R1,R2 或 W1；无人填无"
                                      : check?.format === "pv_operations"
                                        ? "按题目给定操作集合填写，用逗号分隔"
                                        : check?.format === "pv_queue"
                                          ? "如 A,B；空队列填无"
                                          : check?.format === "process_sequence"
                                            ? "如 P0,P1,P2；不允许分配时填无"
                                            : check?.format === "evidence"
                                              ? "如 L1, L3；没有记录填无"
                                              : check?.format === "text"
                                                ? "填写整数；多个数用逗号分隔"
                                                : "填写整数余数"
                                  }
                                  maxLength={120}
                                  disabled={busy || locked}
                                  onChange={(e) =>
                                    updateField(index, "value", e.target.value)
                                  }
                                />
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : banker ? (
                <div className="training-table-wrap">
                  <table className="training-table training-input-table training-banker-table">
                    <thead>
                      <tr>
                        <th>步骤</th>
                        <th>检查项</th>
                        <th>作答</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, index) => {
                        const count = bankerProcesses.length;
                        const status = attempt.evaluation?.row_statuses[index];
                        const fieldClass = (field: string) =>
                          status === "error" && firstError?.field === field
                            ? "field-error"
                            : "";
                        return (
                          <tr
                            key={index}
                            className={`answer-row ${status || ""}`}
                          >
                            <td>
                              {index + 1}
                              {status === "error" && <small>首个错误</small>}
                              {status === "pending" && (
                                <small>待订正后检查</small>
                              )}
                            </td>
                            <td>
                              {index < count
                                ? `${bankerProcesses[index].name} 的 Need`
                                : index < count * 2
                                  ? `安全性检查第 ${index - count + 1} 步`
                                  : "安全性结论"}
                            </td>
                            <td>
                              {index < count ? (
                                <input
                                  aria-label={`${bankerProcesses[index].name}的Need`}
                                  value={row.need}
                                  className={fieldClass("need")}
                                  placeholder="按资源列填写，如 1, 0"
                                  maxLength={120}
                                  disabled={busy || locked}
                                  onChange={(e) =>
                                    updateField(index, "need", e.target.value)
                                  }
                                />
                              ) : index < count * 2 ? (
                                <>
                                  <select
                                    aria-label={`安全检查第${index - count + 1}步进程`}
                                    value={row.process}
                                    className={fieldClass("process")}
                                    disabled={busy || locked}
                                    onChange={(e) =>
                                      updateField(
                                        index,
                                        "process",
                                        e.target.value,
                                      )
                                    }
                                  >
                                    <option value="">留空 / 请选择</option>
                                    {bankerProcesses.map((p) => (
                                      <option key={p.name}>{p.name}</option>
                                    ))}
                                  </select>
                                  <input
                                    aria-label={`安全检查第${index - count + 1}步Work`}
                                    value={row.work}
                                    className={fieldClass("work")}
                                    placeholder="释放 Allocation 后的 Work"
                                    maxLength={120}
                                    disabled={busy || locked}
                                    onChange={(e) =>
                                      updateField(index, "work", e.target.value)
                                    }
                                  />
                                </>
                              ) : (
                                <select
                                  aria-label="安全性结论"
                                  value={row.verdict}
                                  className={fieldClass("verdict")}
                                  disabled={busy || locked}
                                  onChange={(e) =>
                                    updateField(
                                      index,
                                      "verdict",
                                      e.target.value,
                                    )
                                  }
                                >
                                  <option value="">请选择</option>
                                  <option value="safe">安全</option>
                                  <option value="unsafe">不安全</option>
                                </select>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : cQuestion ? (
                codeRepair ? (
                  <div>
                    {rows.map((row, index) => {
                      const label =
                        exercise.parameters.source_files?.[index] ||
                        (fullProgram ? "完整 C 程序" : "返回语句");
                      return (
                        <label className="training-code-editor" key={index}>
                          {label}
                          <textarea
                            aria-label={label}
                            value={row.code || ""}
                            maxLength={fullProgram ? 12000 : 120}
                            rows={fullProgram ? (rows.length > 1 ? 8 : 16) : 3}
                            spellCheck={false}
                            disabled={busy || locked}
                            onChange={(e) =>
                              updateField(index, "code", e.target.value)
                            }
                          />
                        </label>
                      );
                    })}
                  </div>
                ) : (
                  <div className="training-table-wrap">
                    <table className="training-table training-input-table training-code-checkpoints">
                      <thead>
                        <tr>
                          <th>步骤</th>
                          <th>检查点</th>
                          <th>变量值或输出</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row, index) => (
                          <tr
                            key={index}
                            className={`answer-row ${attempt.evaluation?.row_statuses[index] || ""}`}
                          >
                            <td>
                              {index + 1}
                              {attempt.evaluation?.row_statuses[index] ===
                                "error" && <small>首个错误</small>}
                              {attempt.evaluation?.row_statuses[index] ===
                                "pending" && <small>待订正后检查</small>}
                            </td>
                            <td>{exercise.parameters.checkpoints?.[index]}</td>
                            <td>
                              <input
                                aria-label={`第${index + 1}步变量值`}
                                value={row.value}
                                maxLength={120}
                                disabled={busy || locked}
                                onChange={(e) =>
                                  updateField(index, "value", e.target.value)
                                }
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              ) : (
                <div className="training-table-wrap">
                  <table className="training-table training-input-table">
                    <thead>
                      <tr>
                        <th>{page ? "步骤" : "执行段"}</th>
                        {page ? (
                          <>
                            <th>访问页</th>
                            <th>页框内容</th>
                            <th>命中 / 缺页</th>
                            <th>淘汰页</th>
                          </>
                        ) : (
                          <>
                            <th>进程</th>
                            <th>开始时间</th>
                            <th>结束时间</th>
                            <th>操作</th>
                          </>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, index) => {
                        const status = attempt.evaluation?.row_statuses[index];
                        const fieldClass = (field: string) =>
                          status === "error" && firstError?.field === field
                            ? "field-error"
                            : "";
                        return (
                          <tr
                            key={index}
                            className={`answer-row ${status || ""}`}
                          >
                            <td>
                              {index + 1}
                              {status === "error" && <small>首个错误</small>}
                              {status === "pending" && (
                                <small>待订正后检查</small>
                              )}
                            </td>
                            {page ? (
                              <>
                                <td>{exercise.parameters.sequence?.[index]}</td>
                                <td>
                                  <input
                                    aria-label={`第${index + 1}步页框内容`}
                                    className={fieldClass("frames")}
                                    value={row.frames}
                                    placeholder="如 1, 2, 3"
                                    disabled={busy || locked}
                                    onChange={(e) =>
                                      updateField(
                                        index,
                                        "frames",
                                        e.target.value,
                                      )
                                    }
                                  />
                                </td>
                                <td>
                                  <select
                                    aria-label={`第${index + 1}步命中状态`}
                                    className={fieldClass("event")}
                                    value={row.event}
                                    disabled={busy || locked}
                                    onChange={(e) =>
                                      updateField(
                                        index,
                                        "event",
                                        e.target.value,
                                      )
                                    }
                                  >
                                    <option value="">请选择</option>
                                    <option value="hit">命中</option>
                                    <option value="fault">缺页</option>
                                  </select>
                                </td>
                                <td>
                                  <input
                                    aria-label={`第${index + 1}步淘汰页`}
                                    className={fieldClass("evicted")}
                                    value={row.evicted}
                                    placeholder="—"
                                    disabled={busy || locked}
                                    onChange={(e) =>
                                      updateField(
                                        index,
                                        "evicted",
                                        e.target.value,
                                      )
                                    }
                                  />
                                </td>
                              </>
                            ) : (
                              <>
                                <td>
                                  <select
                                    aria-label={`第${index + 1}段进程`}
                                    className={fieldClass("process")}
                                    value={row.process}
                                    disabled={busy || locked}
                                    onChange={(e) =>
                                      updateField(
                                        index,
                                        "process",
                                        e.target.value,
                                      )
                                    }
                                  >
                                    <option value="">请选择</option>
                                    {exercise.parameters.processes?.map(
                                      (process) => (
                                        <option key={process.name}>
                                          {process.name}
                                        </option>
                                      ),
                                    )}
                                  </select>
                                </td>
                                <td>
                                  <input
                                    aria-label={`第${index + 1}段开始时间`}
                                    className={fieldClass("start")}
                                    inputMode="numeric"
                                    value={row.start}
                                    disabled={busy || locked}
                                    onChange={(e) =>
                                      updateField(
                                        index,
                                        "start",
                                        e.target.value,
                                      )
                                    }
                                  />
                                </td>
                                <td>
                                  <input
                                    aria-label={`第${index + 1}段结束时间`}
                                    className={fieldClass("end")}
                                    inputMode="numeric"
                                    value={row.end}
                                    disabled={busy || locked}
                                    onChange={(e) =>
                                      updateField(index, "end", e.target.value)
                                    }
                                  />
                                </td>
                                <td>
                                  <button
                                    className="training-link"
                                    aria-label={`删除第${index + 1}段`}
                                    disabled={
                                      busy || locked || rows.length <= 1
                                    }
                                    onClick={() =>
                                      updateRows(
                                        rows.filter((_, i) => i !== index),
                                      )
                                    }
                                  >
                                    删除
                                  </button>
                                </td>
                              </>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {exercise.kind === "cpu_scheduling" && !locked && (
                <button
                  className="ghost-action training-add"
                  disabled={busy || rows.length >= 100}
                  onClick={() =>
                    updateRows([...rows, { process: "", start: "", end: "" }])
                  }
                >
                  增加执行段
                </button>
              )}
              <section className="training-feedback" aria-label="作答诊断" aria-live="polite">
                <h2>作答诊断</h2>
                {!attempt.evaluation ? (
                  <p className="training-muted">
                    {attempt.assignment?.feedback_hidden
                      ? "已保存作答；核验结果将在测验结束后公布。"
                      : labProgram
                        ? "提交后实际执行实验脚本，独立检查数据库、文件权限、密文、签名或日志报告，并提供产物下载。"
                        : fullProgram
                          ? "提交后在隔离环境编译并运行测试，展示编译诊断和逐用例输出。"
                          : pvDesign
                            ? "提交后核验给定有限模型的执行交错；失败时展示一条反例。"
                            : codeRepair
                              ? "提交作答后，系统会核验声明范围内的返回值，并给出首个失败用例。"
                              : "提交作答后，系统会检查过程并定位首个错误。"}
                  </p>
                ) : passed ? (
                  <div className="training-pass">
                    {labProgram
                      ? "实验运行与产物校验通过"
                      : fullProgram
                        ? "本次测试集全部通过"
                        : pvDesign
                          ? "给定有限模型核验通过"
                          : codeRepair
                            ? "全部 21 个输入核验通过"
                            : "过程核验通过"}
                    {attempt.first_unassisted_pass
                      ? " · 首次独立完成"
                      : " · 已完成订正或辅助练习"}
                  </div>
                ) : (
                  <>
                    <div className="training-first-error">
                      {firstError?.error_code === "grading_review"
                        ? "人工复核判定本次提交未通过"
                        : labProgram
                          ? "实验执行或产物校验未通过"
                          : fullProgram
                            ? "完整程序评测未通过"
                            : pvDesign
                              ? `方案未通过：第 ${firstError?.step} 个进程需要检查`
                              : codeRepair
                                ? "返回语句核验未通过"
                                : `第 ${firstError?.step} ${page || cQuestion || banker || security ? "步" : "段"}出现首个错误`}
                    </div>
                    <h3>核验结果</h3>
                    <p>{firstError?.message}</p>
                    {firstError?.possible_cause && (
                      <>
                        <h3>可能原因</h3>
                        <p>{firstError.possible_cause}</p>
                      </>
                    )}
                    {!codeRepair && (
                      <p className="training-muted">
                        {pvDesign
                          ? "多个进程联合核验，其他进程行尚不能判为正确。"
                          : "后续步骤待订正后再检查。"}
                      </p>
                    )}
                  </>
                )}
              </section>
              <p className="training-save-status" role="status">
                {saveStatus} · 已提交 {attempt.submission_count} 次
                {dirty ? " · 已修改，诊断对应上次提交" : ""}
              </p>
              <div className="training-actions">
                <button
                  className="send-action"
                  disabled={busy || locked}
                  onClick={submit}
                >
                  {locked
                    ? passed
                      ? "已通过"
                      : "已锁定"
                    : attempt.submission_count
                      ? "提交订正"
                      : "提交作答"}
                </button>
              </div>
              <details className="training-support" key={`solution-${attempt.id}`}>
                <summary>完整解析{attempt.solution_viewed ? " · 已查看" : ""}</summary>
                <p className="training-muted">查看解析后通过将记为辅助完成。</p>
                {attempt.solution ? (
                  <SolutionBlock solution={attempt.solution} />
                ) : (
                  <button
                    className="training-link"
                    disabled={
                      busy ||
                      Boolean(attempt.assignment && !attempt.assignment.can_solution)
                    }
                    onClick={() => assistance("solution")}
                  >
                    查看解析
                  </button>
                )}
              </details>
              {attempt.evaluation?.lab_environment && (
                <section aria-label="实验环境与产物">
                  <h3>本轮实验环境与产物</h3>
                  <p>
                    输入只读，输出目录每次重新创建。产物：
                    {attempt.evaluation.lab_environment.artifacts
                      .map((file) => file.name)
                      .join("、") || "未生成"}
                  </p>
                  <ul>
                    {attempt.evaluation.lab_environment.artifacts.map(
                      (file) => (
                        <li key={file.name}>
                          <a
                            download={file.name}
                            href={`data:application/octet-stream;base64,${file.base64}`}
                          >
                            {file.name}
                          </a>
                          （{file.size} 字节，权限 {file.mode}）
                        </li>
                      ),
                    )}
                  </ul>
                  {Object.entries(
                    attempt.evaluation.lab_environment.inputs,
                  ).map(([name, content]) => (
                    <details key={name}>
                      <summary>{name}</summary>
                      <pre className="training-code">{content}</pre>
                    </details>
                  ))}
                </section>
              )}
              {attempt.evaluation?.program_feedback && (
                <section aria-label="程序评测反馈">
                  <h3>程序评测反馈</h3>
                  {attempt.evaluation.program_feedback.compiler && (
                    <pre className="training-code">
                      {attempt.evaluation.program_feedback.compiler}
                    </pre>
                  )}
                  <p className="training-muted">
                    以下为实际运行结果；用例失败位置不是代码首错行。超时或运行异常后停止后续用例；实验脚本会进一步检查实际产物。
                  </p>
                  {attempt.evaluation.program_feedback.tests.map((test) => (
                    <details key={test.number}>
                      <summary>
                        用例 {test.number} · {test.status}
                      </summary>
                      <p>输入</p>
                      <pre className="training-code">{test.input}</pre>
                      <p>期望输出</p>
                      <pre className="training-code">{test.expected}</pre>
                      <p>实际输出</p>
                      <pre className="training-code">
                        {test.output || "（无输出）"}
                      </pre>
                    </details>
                  ))}
                </section>
              )}
              <p className="training-muted">
                {attempt.assignment?.kind === "quiz"
                  ? "本测验按保存的作答核验，结果结束后公布。"
                  : "使用提示、查看解析或进入作答问答后通过，会记为辅助完成；原题订正通过与新题独立通过分别记录。"}
              </p>
              {attempt.tutoring_viewed && (
                <p className="training-muted">本次练习已打开作答问答辅导。</p>
              )}
              {attempt.review_id && (
                <p className="training-muted">
                  本题为错题间隔复测。提交后可在“错题复习”中查看记录与下次时间。
                </p>
              )}
              {!attempt.assignment && !attempt.review_id && (
                <div className="training-retest">
                  <div>
                    <strong>新题复测</strong>
                    <p>
                      {attempt.recommendation
                        ? attempt.recommendation.reason
                        : "该题型暂无尚未练习的新题。"}
                    </p>
                  </div>
                  <button
                    className="ghost-action"
                    disabled={busy || !passed || !attempt.recommendation}
                    onClick={() => start(undefined, attempt.id)}
                  >
                    开始复测
                  </button>
                </div>
              )}
            </section>
            <aside className="training-diagnosis" hidden={assistanceHidden}>
              <div className="training-assistance-heading"><h2>练习辅助</h2><button type="button" className="training-link" aria-expanded={true} onClick={() => setAssistanceHidden(true)}>隐藏</button></div>
              <details className="training-support" key={`hint-${attempt.id}`}>
                <summary>分步提示{attempt.hint ? " · 已获取" : ""}</summary>
                <p className="training-muted">获取提示后通过将记为辅助完成。</p>
                {attempt.hint && (
                  <div className="training-hint">
                    <h3>
                      {labProgram
                        ? "实验脚本 · "
                        : fullProgram
                          ? "完整程序 · "
                          : codeRepair
                            ? "返回语句 · "
                            : `第 ${attempt.hint.step} ${page || cQuestion || banker || security ? "步" : "段"} · `}
                      {attempt.hint.level === 1 ? "一级" : "二级"}提示
                    </h3>
                    <p>{attempt.hint.text}</p>
                  </div>
                )}
                <button
                  className="ghost-action"
                  disabled={
                    busy ||
                    locked ||
                    Boolean(attempt.assignment && !attempt.assignment.can_hint)
                  }
                  onClick={() => assistance("hint")}
                >
                  {attempt.hint?.level === 1 ? "查看二级提示" : "获取提示"}
                </button>
              </details>
              {!demoId && onQuestion && (
                <button
                  className="training-link"
                  disabled={busy || attempt.assignment?.feedback_hidden}
                  onClick={() =>
                    run(async () => {
                      await flushDraft();
                      const context = await api.learningQuestionContext(
                        attempt.id,
                      );
                      setAttempt(await api.getLearning(attempt.id));
                      onQuestion(context);
                    })
                  }
                >
                  带着作答进入问答
                </button>
              )}

              {!demoId &&
                attempt.submission_count > 0 &&
                !attempt.assignment?.feedback_hidden && (
                  <details className="training-support">
                    <summary>评测异议与复核</summary>
                    <p>班级练习由授课教师处理，课外练习由管理员处理。</p>
                    <select
                      aria-label="复核提交次数"
                      value={appealNumber || attempt.submission_count}
                      disabled={busy}
                      onChange={(e) => setAppealNumber(Number(e.target.value))}
                    >
                      {Array.from(
                        { length: attempt.submission_count },
                        (_, i) => (
                          <option key={i + 1} value={i + 1}>
                            第 {i + 1} 次提交
                          </option>
                        ),
                      )}
                    </select>
                    <textarea
                      aria-label="评测异议理由"
                      placeholder="说明有异议的步骤和依据，至少5个字"
                      maxLength={2000}
                      value={appealReason}
                      disabled={busy}
                      onChange={(e) => setAppealReason(e.target.value)}
                    />
                    <button
                      disabled={
                        busy ||
                        appealReason.trim().length < 5 ||
                        attempt.grading_reviews?.some(
                          (r) =>
                            r.submission_number ===
                            (appealNumber || attempt.submission_count),
                        )
                      }
                      onClick={() =>
                        run(async () => {
                          await flushDraft();
                          setAttempt(
                            await api.requestGradingReview(
                              attempt.id,
                              appealNumber || attempt.submission_count,
                              appealReason,
                            ),
                          );
                          setAppealReason("");
                        })
                      }
                    >
                      提交评测异议
                    </button>
                    <button
                      className="training-link"
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await flushDraft();
                          setAttempt(await api.getLearning(attempt.id));
                        })
                      }
                    >
                      刷新复核结果
                    </button>
                    {attempt.grading_reviews?.map((r) => (
                      <p key={r.id}>
                        第 {r.submission_number} 次提交：
                        {r.status === "pending"
                          ? "待复核"
                          : r.decision === "uphold"
                            ? "维持原判"
                            : r.decision === "pass"
                              ? "改判通过"
                              : "改判未通过"}
                        {r.note && ` · ${r.reviewer_name}：${r.note}`}
                      </p>
                    ))}
                    {attempt.grading_reviews?.some(
                      (r) => r.status === "resolved" && r.decision !== "uphold",
                    ) && (
                      <p>当前结果包含人工复核；原自动评测输出保留作为证据。</p>
                    )}
                  </details>
                )}
              {!demoId && !attempt.assignment && <LearningDialogue key={attempt.id}
                attempt={attempt} busy={busy}
                onRetest={() => { void start(undefined, attempt.id); }}
                onRefresh={async () => {
                  const latest = await api.getLearning(attempt.id);
                  if (currentId.current === latest.id) setAttempt(latest);
                }}
                onAction={async (action, answer, resumeRunId) => {
                  await run(async () => {
                    if (!resumeRunId) await flushDraft();
                    const latest = await api.learningDialogue(attempt.id, action, attempt.dialogue?.revision || 0, answer, resumeRunId);
                    if (currentId.current === latest.id) setAttempt(latest);
                  });
                }} />}
              <details className="training-attempt-info" key={`info-${attempt.id}`}>
                <summary>本次练习记录</summary>
                <dl>
                  <dt>提交次数</dt>
                  <dd>{attempt.submission_count}</dd>
                  <dt>订正次数</dt>
                  <dd>{attempt.correction_count}</dd>
                  <dt>提示次数</dt>
                  <dd>{attempt.hint_count}</dd>
                  <dt>完整解析</dt>
                  <dd>{attempt.solution_viewed ? "已查看" : "未查看"}</dd>
                  <dt>练习类型</dt>
                  <dd>
                    {attempt.assignment
                      ? {
                          homework: "班级作业",
                          quiz: "班级测验",
                          experiment: "实验任务",
                        }[attempt.assignment.kind]
                      : attempt.review_id
                        ? "错题间隔复测"
                        : attempt.parent_id
                          ? "新题复测"
                          : attempt.diagnostic
                            ? "入门诊断"
                            : "常规练习"}
                  </dd>
                </dl>
                <button
                  className="training-link"
                  disabled={busy}
                  onClick={() => changeView("records")}
                >
                  查看学习记录
                </button>
              </details>
            </aside>
          </div>
        )}
        {view === "records" && (
          <section className="learning-records">
            <p className="training-muted">
              当前账号的练习记录；管理员另可查看标注的旧版共享记录。独立通过指未使用提示、未查看解析且首次提交通过的新题；重复题不计入。
            </p>
            {progress.isPending && <p>正在读取学习记录…</p>}
            {progress.error && (
              <p role="alert">
                记录加载失败：{progress.error.message}{" "}
                <button
                  className="training-link"
                  onClick={() => progress.refetch()}
                >
                  重试
                </button>
              </p>
            )}
            {progress.data && (
              <>
                <dl className="learning-summary">
                  <div>
                    <dt>已提交练习</dt>
                    <dd>{progress.data.summary.submitted}</dd>
                  </div>
                  <div>
                    <dt>首次独立通过</dt>
                    <dd>{progress.data.summary.first_unassisted_passes}</dd>
                  </div>
                  <div>
                    <dt>订正后通过（含辅助）</dt>
                    <dd>{progress.data.summary.corrected_passes}</dd>
                  </div>
                  <div>
                    <dt>新题独立复测通过</dt>
                    <dd>{progress.data.summary.independent_retest_passes}</dd>
                  </div>
                </dl>
                {!!progress.data.errors.length && (
                  <details className="learning-errors">
                    <summary>首错类型统计</summary>
                    <p className="training-muted">
                      每次提交只统计一个首错；同一问题多次提交出错会重复计数。
                    </p>
                    <div>
                      {progress.data.errors.map((item) => (
                        <span key={item.code}>
                          {item.label}：{item.count} 次
                        </span>
                      ))}
                    </div>
                  </details>
                )}
                <div className="record-filters">
                  <label>课程<select value={recordCourse} onChange={e => setRecordCourse(e.target.value)}>
                    <option value="">全部课程</option>
                    {Array.from(new Map(progress.data.records.map(r => [r.exercise.subject_id, r.exercise.subject_name]))).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
                  </select></label>
                  <label>结果<select value={recordStatus} onChange={e => setRecordStatus(e.target.value)}>
                    <option value="">全部结果</option>
                    {["作答中", "待订正", "独立通过", "订正通过", "辅助完成", "重复练习通过", "待公布"].map(status => <option key={status}>{status}</option>)}
                  </select></label>
                  <span className="training-muted">{visibleRecords.length} 条记录 · 上方统计为全部课程</span>
                  <button className="training-link" disabled={progress.isFetching} onClick={() => progress.refetch()}>刷新记录</button>
                </div>
                {!!progress.data.records.length && !visibleRecords.length && <p className="training-muted">没有符合筛选条件的记录。</p>}
                {!progress.data.records.length ? (
                  <p>
                    还没有练习记录。
                    <button
                      className="training-link"
                      onClick={() => changeView("exercises")}
                    >
                      开始选题
                    </button>
                  </p>
                ) : visibleRecords.length > 0 && (
                  <div className="training-table-wrap">
                    <table className="training-table learning-record-table">
                      <thead>
                        <tr>
                          <th>时间 / 题目</th>
                          <th>结果</th>
                          <th>过程与复测</th>
                          <th>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visibleRecords.map((record) => (
                          <tr key={record.id}>
                            <td>
                              <small>
                                {new Date(
                                  record.created_at * 1000,
                                ).toLocaleString("zh-CN")}
                              </small>
                              {record.exercise.title}
                              {record.assignment && (
                                <small>
                                  {record.assignment.title}
                                  {record.assignment.feedback_hidden
                                    ? " · 待公布测验结果"
                                    : ""}
                                </small>
                              )}
                              <small>
                                {record.exercise.subject_name} ·{" "}
                                {record.exercise.chapter_title}
                              </small>
                              {record.parent_id && (
                                <small>关联原题的新题复测</small>
                              )}
                              {record.review_id && <small>错题间隔复测</small>}
                            </td>
                            <td>
                              {recordResult(record)}
                            </td>
                            <td>
                              <details className="record-details">
                                <summary>查看过程 · 提交 {record.submission_count} 次</summary>
                              <small>
                                版本 {record.content_version}
                                {!demoId && record.legacy_record
                                  ? " · 旧版共享记录"
                                  : ""}
                                {record.class_id
                                  ? " · 班级练习"
                                  : " · 个人练习"}
                              </small>
                                <p>订正 {record.correction_count} 次 · 提示 {record.hint_count} 次{record.solution_viewed ? " · 已查看解析" : ""}{record.tutoring_viewed ? " · 已打开问答辅导" : ""}</p>
                                <p>首次错误：
                              {record.first_error
                                ? record.first_error.error_code ===
                                  "grading_review"
                                  ? record.first_error.label
                                  : [
                                        "c_repair",
                                        "c_program",
                                        "security_lab",
                                      ].includes(record.exercise.kind)
                                    ? record.first_error.label
                                    : `第 ${record.first_error.step} 步 · ${record.first_error.label}`
                                : "—"}
                                </p>
                                <p>新题复测</p>
                              {record.retests.length
                                ? record.retests.map((retest) => (
                                    <button
                                      key={retest.id}
                                      className="training-link record-retest-link"
                                      disabled={busy}
                                      onClick={() => openRecord(retest.id)}
                                    >
                                      {retest.independent_pass
                                        ? "独立通过"
                                        : STATUS[retest.status]}
                                    </button>
                                  ))
                                : "未开始"}
                              </details>
                            </td>
                            <td>
                              <div className="path-actions">
                                <button
                                  className="training-link"
                                  disabled={busy}
                                  onClick={() => openRecord(record.id)}
                                >
                                  {record.status === "passed"
                                    ? "查看作答"
                                    : "继续作答"}
                                </button>
                                {record.status === "in_progress" && record.submission_count === 0 && !record.assignment && (
                                  <button
                                    className="training-link"
                                    disabled={busy}
                                    onClick={() => cancelRecord(record.id)}
                                  >
                                    取消作答
                                  </button>
                                )}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </section>
        )}
      </div>
    </main>
  );
}

function SolutionBlock({ solution }: { solution: LearningSolution }) {
  const total = solution.timeline?.[solution.timeline.length - 1]?.end || 1;
  return (
    <section className="training-solution">
      {solution.security_trace ? (
        <div className="training-table-wrap">
          <table className="training-table">
            <thead>
              <tr>
                <th>检查点</th>
                <th>参考结果</th>
                <th>依据</th>
              </tr>
            </thead>
            <tbody>
              {solution.security_trace.map((row, i) => (
                <tr key={i}>
                  <td>{row.checkpoint}</td>
                  <td>{row.value}</td>
                  <td>{row.explanation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : solution.need ? (
        <>
          <p>
            {solution.safe
              ? "安全；以下是一条合法安全序列，其他合法顺序同样可通过。"
              : "不安全；只能完成以下进程，其余进程的 Need 均无法由当前 Work 满足。"}
          </p>
          <p>
            完成顺序：{solution.safe_sequence?.join(" → ") || "无进程可完成"}
          </p>
          <table className="training-table">
            <thead>
              <tr>
                <th>进程</th>
                <th>Need</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(solution.need).map(([name, need]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{need.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!!solution.rounds?.length && (
            <div className="training-table-wrap">
              <table className="training-table">
                <thead>
                  <tr>
                    <th>进程</th>
                    <th>Work（完成前）</th>
                    <th>释放 Allocation</th>
                    <th>Work（完成后）</th>
                  </tr>
                </thead>
                <tbody>
                  {solution.rounds.map((r, i) => (
                    <tr key={i}>
                      <td>{r.process}</td>
                      <td>{r.work_before.join(", ")}</td>
                      <td>{r.allocation_released.join(", ")}</td>
                      <td>{r.work_after.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : solution.c_trace ? (
        <table className="training-table">
          <thead>
            <tr>
              <th>检查点</th>
              <th>正确值</th>
            </tr>
          </thead>
          <tbody>
            {solution.c_trace.map((row, i) => (
              <tr key={i}>
                <td>{row.checkpoint}</td>
                <td>{row.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : solution.files ? (
        <>
          {solution.files.map((file) => (
            <div key={file.name}>
              <h4>{file.name}</h4>
              <pre className="training-code">
                <code>{file.code}</code>
              </pre>
            </div>
          ))}
          <p>{solution.explanation}</p>
        </>
      ) : solution.code ? (
        <>
          <pre className="training-code">
            <code>{solution.code}</code>
          </pre>
          <p>{solution.explanation}</p>
        </>
      ) : solution.trace ? (
        <>
          <p>
            缺页 {solution.faults} 次，命中 {solution.hits} 次。
          </p>
          {solution.algorithm === "OPT" && (
            <p className="training-muted">
              以下是一条合法参考轨迹。并列最远时，其他合法淘汰选择同样可通过，后续按你选择的页框状态核验。
            </p>
          )}
          <div className="training-table-wrap">
            <table className="training-table">
              <thead>
                <tr>
                  <th>步骤</th>
                  <th>访问页</th>
                  <th>页框内容</th>
                  <th>命中 / 缺页</th>
                  <th>淘汰页</th>
                  {solution.algorithm === "OPT" && <th>驻留页后续首次访问</th>}
                </tr>
              </thead>
              <tbody>
                {solution.trace.map((row) => (
                  <tr key={row.step}>
                    <td>{row.step}</td>
                    <td>{row.page}</td>
                    <td>{row.frames_after.join(", ")}</td>
                    <td>{row.event === "hit" ? "命中" : "缺页"}</td>
                    <td>{row.evicted ?? "—"}</td>
                    {solution.algorithm === "OPT" && (
                      <td>
                        {Object.entries(row.next_uses || {})
                          .map(
                            ([page, position]) =>
                              `${page}：${position === null ? "不再访问" : `第 ${position} 次`}`,
                          )
                          .join("；") || "—"}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          <p>时间轴中的间隔表示 CPU 空闲。时间单位与题干一致。</p>
          <div className="training-timeline" aria-label="标准调度时间轴">
            {solution.timeline?.map((segment, i) => (
              <div
                key={i}
                style={{
                  left: `${(segment.start / total) * 100}%`,
                  width: `${((segment.end - segment.start) / total) * 100}%`,
                }}
              >
                <strong>{segment.process}</strong>
                <small>
                  {segment.start}–{segment.end}
                </small>
              </div>
            ))}
          </div>
          <div className="training-table-wrap">
            <table className="training-table">
              <thead>
                <tr>
                  <th>进程</th>
                  <th>开始时间</th>
                  <th>结束时间</th>
                </tr>
              </thead>
              <tbody>
                {solution.timeline?.map((segment, i) => (
                  <tr key={i}>
                    <td>{segment.process}</td>
                    <td>{segment.start}</td>
                    <td>{segment.end}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>调度指标</h3>
          <p className="training-muted">
            周转时间 = 完成时间 − 到达时间；等待时间 = 周转时间 − 服务时间。
          </p>
          <table className="training-table">
            <thead>
              <tr>
                <th>进程</th>
                <th>完成时间</th>
                <th>周转时间</th>
                <th>等待时间</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(solution.metrics || {}).map(([name, metrics]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{metrics.completion}</td>
                  <td>{metrics.turnaround}</td>
                  <td>{metrics.waiting}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>
            平均周转时间 {solution.average_turnaround}，平均等待时间{" "}
            {solution.average_waiting}。
          </p>
        </>
      )}
    </section>
  );
}
