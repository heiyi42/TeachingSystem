import { useDiscardChanges } from "../shared/useDiscardChanges";
import { useEffect, useRef, useState } from "react";
import { ClassReportCharts } from "./ClassReportCharts";
import { LessonPlanWorkspace } from "./LessonPlanWorkspace";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type {
  SchoolClass,
  SchoolTask,
  SchoolUser,
  TaskDetail,
  ReportCounts,
  ReportError,
  LessonPlan,
} from "../../schoolTypes";

const KINDS = { homework: "作业", quiz: "测验", experiment: "实验" };
const STUDENT_STATUS = { not_started: "待开始", in_progress: "进行中", finalized: "已交卷", ended: "已截止" };
const STATUS = { draft: "草稿", published: "已发布", closed: "已结束" };
const date = (value?: number) =>
  value
    ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false })
    : "—";
const localDate = (value: number) => {
  const d = new Date(value * 1000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
const defaultDue = () => localDate(Date.now() / 1000 + 86400 * 7);

function passSummary(counts: ReportCounts) {
  return `独立 ${counts.independent} · 辅助 ${counts.assisted} · 订正 ${counts.corrected}`
    + (counts.repeated ? ` · 重复作答 ${counts.repeated}` : "")
    + (counts.unclassified_pass ? ` · 辅助信息缺失 ${counts.unclassified_pass}` : "");
}

export function AssignmentWorkspace({
  user,
  classes,
  reports = false,
  preparation = false,
  initialTaskId,
  initialTeaching,
  onDirtyChange,
  selectedClassId,
  onClassChange,
  onOpenAttempt,
}: {
  user: SchoolUser;
  classes: SchoolClass[];
  onDirtyChange?: (dirty: boolean) => void;
  selectedClassId?: string;
  onClassChange?: (id: string) => void;
  reports?: boolean;
  preparation?: boolean;
  initialTaskId?: string;
  initialTeaching?: { kind: "report" | "lesson" | "homework"; chapter_id?: string; plan?: LessonPlan };
  onOpenAttempt: (attemptId: string, taskId: string) => void;
}) {
  const { confirmDiscard, discardDialog } = useDiscardChanges();
  const teacher = user.role !== "student";
  const queryClient = useQueryClient();
  const [classId, setClassId] = useState(selectedClassId || classes[0]?.id || "");
  const [chapterId, setChapterId] = useState(initialTeaching?.chapter_id || "");
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [studentId, setStudentId] = useState("");
  const [editing, setEditing] = useState(false);
  const [lessonDirty, setLessonDirty] = useState(false);
  useEffect(() => {
    const dirty = editing || lessonDirty;
    onDirtyChange?.(dirty);
    function warn(event: BeforeUnloadEvent) { event.preventDefault(); event.returnValue = ""; }
    if (dirty) window.addEventListener("beforeunload", warn);
    return () => { onDirtyChange?.(false); window.removeEventListener("beforeunload", warn); };
  }, [editing, lessonDirty, onDirtyChange]);
  const editorRef = useRef<HTMLFormElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const [editId, setEditId] = useState("");
  const [lessonPlan, setLessonPlan] = useState<{ id: string; revision: number } | null>(null);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<SchoolTask["kind"]>("homework");
  const [instructions, setInstructions] = useState("");
  const [rubric, setRubric] = useState("");
  const [due, setDue] = useState(defaultDue);
  const [late, setLate] = useState("");
  const [limit, setLimit] = useState(10);
  const [selected, setSelected] = useState<string[]>([]);
  const [report, setReport] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [comment, setComment] = useState("");
  const [score, setScore] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [confirmFinal, setConfirmFinal] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const tasks = useQuery({
    queryKey: ["school-tasks", user.id, classId],
    queryFn: () => api.schoolTasks(classId),
    refetchInterval: 30000,
    enabled: Boolean(classId) && !reports && !preparation,
  });
  const content = useQuery({
    queryKey: ["task-published-content", user.id],
    queryFn: api.taskPublishedExercises,
    enabled: teacher && editing,
  });
  const stats = useQuery({
    queryKey: ["school-report", user.id, classId, chapterId],
    queryFn: () => api.classReport(classId, chapterId),
    enabled: teacher && reports && Boolean(classId),
  });
  const classroom = classes.find((c) => c.id === classId);
  const available =
    content.data?.exercises.filter(
      (e) =>
        e.publication_status === "published" &&
        e.subject_id === classroom?.course_id,
    ) || [];

  useEffect(() => {
    if (!classes.length) return;
    const next = classes.some(c => c.id === classId) ? classId : classes[0].id;
    if (next !== classId) setClassId(next);
    if (next !== selectedClassId) onClassChange?.(next);
  }, [classes, classId, selectedClassId, onClassChange]);
  useEffect(() => {
    if (initialTeaching?.kind === "homework" && initialTeaching.plan) useLesson(initialTeaching.plan);
  }, [initialTeaching]);
  useEffect(() => {
    if (editing) editorRef.current?.scrollIntoView({ block: "start" });
  }, [editing]);
  useEffect(() => {
    if (detail) detailRef.current?.scrollIntoView({ block: "start" });
  }, [detail?.task.id, studentId]);
  useEffect(() => {
    if (initialTaskId && !reports && !preparation) {
      api
        .taskDetail(initialTaskId)
        .then((next) => {
          setClassId(next.task.class_id);
          adopt(next);
        })
        .catch((e) => setError(String(e.message || e)));
    }
  }, [initialTaskId, reports, preparation]);

  function adopt(next: TaskDetail) {
    setDetail(next);
    setReport(next.submission?.report || "");
    setConfirmFinal(false);
    setConfirmClose(false);
    setComment("");
    setScore(
      next.submission?.score == null ? "" : String(next.submission.score),
    );
  }
  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["school-tasks"] });
    await queryClient.invalidateQueries({ queryKey: ["school-report"] });
    await queryClient.invalidateQueries({ queryKey: ["lesson-effects"] });
    await queryClient.invalidateQueries({ queryKey: ["lesson-plans"] });
    await queryClient.invalidateQueries({ queryKey: ["learning-progress"] });
    await queryClient.invalidateQueries({
      queryKey: ["learning-path", user.id],
    });
  }
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function open(id: string, student = "") {
    setStudentId(student);
    adopt(await api.taskDetail(id, student || undefined));
    setEditing(false);
  }
  function edit(task?: SchoolTask) {
    setEditing(true);
    setDetail(null);
    setEditId(task?.id || "");
    setLessonPlan(task?.lesson_plan_id ? { id: task.lesson_plan_id, revision: task.lesson_plan_revision! } : null);
    setTitle(task?.title || "");
    setKind(task?.kind || "homework");
    setInstructions(task?.instructions || "");
    setRubric(task?.rubric || "");
    setDue(task ? localDate(task.due_at) : defaultDue());
    setLate(task?.late_until ? localDate(task.late_until) : "");
    setLimit(task?.max_submissions || 10);
    setSelected(task?.exercises.map((e) => e.id) || []);
  }
  function useSuggestion(group: ReportError) {
    edit();
    setStudentId("");
    setTitle(`${group.chapter_title}：${group.label}补练`.slice(0, 120));
    setInstructions(`讲评要点：围绕“${group.label}”，说明首错步骤的判断依据，对照题目条件核验。\n补练要求：先独立完成并提交，再根据反馈订正。辅助或订正通过与首次独立通过分别记录。`);
    setSelected(group.exercises.map(e => e.id));
    setMessage("已填入全班补练表单，尚未保存或发布。请核对题目、说明和截止时间，保存草稿后再确认发布。");
  }
  function useLesson(plan: LessonPlan) {
    edit();
    setStudentId("");
    setLessonPlan({ id: plan.id!, revision: plan.revision });
    setTitle(`${plan.title}·课后补练`.slice(0, 120));
    setInstructions("请先独立完成并提交，再根据反馈订正。独立、辅助和订正通过分别记录。");
    setSelected(plan.homework_ids);
    setMessage("已填写关联补练表单，请核对题目和截止时间，保存草稿后再单独发布。");
  }

  const task = detail?.task;
  const finalized = detail?.submission?.status === "finalized";
  const editable = Boolean(
    task &&
    !teacher &&
    !finalized &&
    task.status === "published" &&
    Date.now() / 1000 <= (task.late_until || task.due_at),
  );
  const reportRows = stats.data?.rows || [];
  const [taskFilter, setTaskFilter] = useState("pending");
  const taskList = tasks.data?.tasks || [];
  const pendingTasks = taskList.filter(t => t.student_progress && ["not_started", "in_progress"].includes(t.student_progress.status));
  const visibleTasks = teacher ? taskList : [...(taskFilter === "pending" ? pendingTasks : taskList)].sort((a, b) => {
    const rank = (t: SchoolTask) => t.student_progress && ["not_started", "in_progress"].includes(t.student_progress.status) ? 0 : 1;
    return rank(a) - rank(b) || (a.late_until || a.due_at) - (b.late_until || b.due_at);
  });

  return (
    <section className="assignment-workspace">
      {discardDialog}
      <div className="training-filters" hidden={Boolean(editing || detail || (initialTeaching && preparation))}>
        <label>
          班级
          <select
            disabled={busy || editing || lessonDirty}
            value={classId}
            onChange={(e) => {
              setClassId(e.target.value);
              setChapterId("");
              setDetail(null);
              setEditing(false);
              setSelected([]);
              setError("");
            }}
          >
            {classes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        </label>
        {reports && stats.data && <label>章节范围
          <select value={chapterId} onChange={e => { setChapterId(e.target.value); setDetail(null); setEditing(false); }}>
            <option value="">全部已布置章节</option>
            {stats.data.chapter_options.map(chapter => <option key={chapter.id} value={chapter.id}>{chapter.title}</option>)}
          </select>
        </label>}
        <button
          className="ghost-action"
          disabled={busy || !classId}
          onClick={() =>
            run(async () => {
              await refresh();
              if (task)
                adopt(await api.taskDetail(task.id, studentId || undefined));
            })
          }
        >
          刷新
        </button>
        {teacher && !reports && !preparation && (
          <button
            className="send-action"
            disabled={!classId || busy}
            onClick={() => edit()}
          >
            布置任务
          </button>
        )}
        {teacher && reports && classId && (
          <a
            className="training-link"
            href={`/api/school/classes/${classId}/report.csv${chapterId ? `?chapter_id=${encodeURIComponent(chapterId)}` : ""}`}
          >
            导出 CSV
          </a>
        )}
      </div>
      {!classes.length && <p>请先{teacher ? "创建授课班级" : "加入班级"}。</p>}
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">正在处理，请稍候…</p>}
      {message && <p role="status">{message}</p>}
      {(tasks.error || stats.error) && (
        <p role="alert">{String((tasks.error || stats.error)?.message)} <button className="training-link" disabled={tasks.isFetching || stats.isFetching} onClick={() => { if (reports) void stats.refetch(); else void tasks.refetch(); }}>重新读取</button></p>
      )}
      {(tasks.isLoading || stats.isLoading) && <p role="status">正在读取…</p>}
      <div hidden={Boolean(editing || detail)}>
      {preparation && teacher && classId && <LessonPlanWorkspace
        key={classId} classId={classId} courseId={classroom?.course_id || ""} userId={user.id} onAssign={useLesson} onDirtyChange={setLessonDirty}
        initialPlan={initialTeaching?.plan}
        onOpenTask={(id, student) => { run(() => open(id, student)); }}
      />}
      {reports && stats.data && (
        <>
          <div className="report-context"><span>{stats.data.course_name} · 已发布及已结束任务</span><span>更新于 {date(stats.data.generated_at)}</span></div>
          <dl className="report-metrics">
            <div><dt>班级学生</dt><dd>{stats.data.member_count}<small> 人</small></dd></div>
            <div><dt>统计任务</dt><dd>{stats.data.task_count}<small> 项</small></dd></div>
            <div><dt>已交卷 / 应交</dt><dd>{stats.data.finalized}<small> / {stats.data.expected_submissions} 人次</small></dd></div>
            <div><dt>已提交 / 应作答</dt><dd>{stats.data.totals.submitted}<small> / {stats.data.totals.expected_answers} 题次</small></dd></div>
          </dl>
          <ClassReportCharts report={stats.data} />
          <details className="task-evidence">
            <summary>通过类型与统计口径</summary>
            <p>统计范围为当前班级成员和已发布、已结束任务，不包含私人练习。交卷与通过分别统计，未提交不计为错误。过程数据反映已保存证据，不等同于掌握全部课程。</p>
            <p>每名学生在每项任务中的每道题只计一次。通过类型互斥，按首次通过时的提交证据判定；重复提交不会增加通过题次。</p>
            <p>独立：首次提交正确，未使用提示、解析、问答辅导或先前同题作答。辅助：首次提交即通过，但此前已使用提示、解析或问答辅导。订正：先有未通过提交，后来通过，不论是否借助辅导。</p>
            <p>重复作答：首次提交通过，已有同题作答记录且没有本次辅导证据。旧记录缺少辅助信息时单独列出，不推定独立或辅助。通过后查看解析不会改变首次通过时的分类。</p>
            <p>错误记录按出现首错的提交次数计算，不代表出错学生人数。已交卷任务使用归档证据；未交卷任务使用当前已保存记录。选择章节后，作答和错误统计限于该章节，任务交卷状态与总分仍对应整份任务。</p>
          </details>
          <h3>常见错误与教学建议</h3>
          <p className="training-muted">按章节、题型和算法族分别汇总。同一学生重复提交不增加涉及人数；建议来自核验记录与已发布题库，需教师核对后使用。</p>
          {!stats.data.common_errors.length && <p>{stats.data.totals.submitted ? "当前范围内没有首错记录；这不等于已掌握全部内容。" : "当前范围内尚无已提交作答，不能据此判断薄弱点。"}</p>}
          {stats.data.common_errors.map(group => <section key={group.id} className="task-evidence">
            <h4>{group.chapter_title} · {group.label}</h4>
            <p>涉及 {group.student_count} 名学生 · 首错出现 {group.occurrences} 次 · 最新提交仍出现该首错 {group.current_student_count} 人</p>
            <p>{group.teaching_advice}</p>
            <p>{group.practice_advice}</p>
            {!!group.exercises.length && <ul>{group.exercises.map(exercise => <li key={exercise.id}>{exercise.title}</li>)}</ul>}
            <details><summary>查看 {group.evidence.length} 条作答证据</summary>
              <ul>{group.evidence.map(evidence => <li key={`${evidence.attempt_id}:${evidence.submission_number}`}>
                <p>{evidence.student_name} · {evidence.task_title} · {evidence.exercise_title}</p>
                <p>第 {evidence.submission_number} 次提交 · {date(evidence.created_at)}{evidence.step != null && ` · 第 ${evidence.step} 步`}：{evidence.message}</p>
                <button className="training-link" disabled={busy} onClick={() => run(() => open(evidence.task_id, evidence.student_id))}>查看完整作答</button>
              </li>)}</ul>
            </details>
            <button className="ghost-action" disabled={busy || !group.exercises.length} onClick={() => useSuggestion(group)}>采用建议，填写全班补练草稿</button>
            <p className="training-muted">采用仅填入可编辑表单；保存草稿和发布到班级仍由教师分别确认。</p>
          </section>)}
          <h3>学生任务记录</h3>
          <div className="training-table-wrap">
            <table className="training-table task-report-table">
              <thead>
                <tr>
                  <th>任务 / 学生</th>
                  <th>状态</th>
                  <th>任务总分</th>
                  <th>已核验 / 通过</th>
                  <th>通过类型（题次）</th>
                  <th>提示 / 解析 / 订正</th>
                  <th>首错提交次数</th>
                  <th>证据</th>
                </tr>
              </thead>
              <tbody>
                {reportRows.map((r) => (
                  <tr key={`${r.task_id}:${r.student_id}`}>
                    <td>
                      {r.task_title}
                      <br />
                      {r.student_name}
                    </td>
                    <td>
                      {r.status === "finalized"
                        ? "已交卷"
                        : r.status === "started"
                          ? "进行中"
                          : "未开始"}
                      {r.late && " · 补交"}
                      {r.pending_review && " · 待评分"}
                    </td>
                    <td>{r.score ?? "—"}</td>
                    <td>
                      {r.submitted} / {r.passed}
                    </td>
                    <td>{passSummary(r)}</td>
                    <td>
                      {r.hints} / {r.solutions} / {r.corrections}
                    </td>
                    <td>
                      {Object.entries(r.errors)
                        .map(([name, n]) => `${name} ${n} 次`)
                        .join("；") || "—"}
                    </td>
                    <td>
                      <button
                        className="training-link"
                        disabled={busy}
                        onClick={() => run(() => open(r.task_id, r.student_id))}
                      >
                        查看
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!reportRows.length && <p>尚无已发布任务。</p>}
          <h3>章节表现</h3>
          <div className="training-table-wrap">
            <table className="training-table">
              <thead>
                <tr>
                  <th>章节</th>
                  <th>布置题次 / 应作答人次</th>
                  <th>已核验 / 通过 / 未提交</th>
                  <th>通过类型（题次）</th>
                </tr>
              </thead>
              <tbody>
                {stats.data.chapters.map((c) => (
                  <tr key={c.id}>
                    <td>{c.title}</td>
                    <td>
                      {c.assigned_questions} / {c.expected_answers}
                    </td>
                    <td>
                      {c.submitted} / {c.passed} / {c.unsubmitted}
                    </td>
                    <td>{passSummary(c)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>题目表现</h3>
          <div className="training-table-wrap">
            <table className="training-table">
              <thead>
                <tr>
                  <th>任务 / 题目</th>
                  <th>章节 / 版本</th>
                  <th>已开始 / 当前人数</th>
                  <th>已核验 / 通过 / 未提交</th>
                  <th>通过类型（题次）</th>
                </tr>
              </thead>
              <tbody>
                {stats.data.questions.map((q) => (
                  <tr key={`${q.task_id}:${q.exercise_id}`}>
                    <td>
                      {q.task_title}
                      <br />
                      {q.title}
                    </td>
                    <td>
                      {q.chapter_title}
                    </td>
                    <td>
                      {q.started} / {stats.data.member_count}
                    </td>
                    <td>
                      {q.submitted} / {q.passed} / {q.unsubmitted}
                    </td>
                    <td>{passSummary(q)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {!reports && !preparation && !editing && (
        <>
          <h2>{teacher ? "班级任务" : "我的作业与测验"}</h2>
          {!teacher && tasks.data && <>
            <div className="student-task-heading">
              <p>{pendingTasks.length ? `当前班级有 ${pendingTasks.length} 项待完成任务，按最晚提交时间排列。` : "当前班级没有待完成任务。"}</p>
              <label>显示范围 <select value={taskFilter} onChange={e => setTaskFilter(e.target.value)}><option value="pending">待完成</option><option value="all">全部任务</option></select></label>
            </div>
            <p className="training-muted">题目提交后仍需在任务详情中交卷。实验还需核对报告和附件。</p>
          </>}
          {tasks.isSuccess && !tasks.data.tasks.length && classId && (
            <p>
              暂无任务。{teacher && "请先在题库审核中发布训练题，再布置任务。"}
            </p>
          )}
          <div className="training-table-wrap">
            <table className="training-table">
              <thead>
                <tr>
                  <th>任务</th>
                  <th>类型</th>
                  <th>截止时间</th>
                  <th>状态</th>
                  {!teacher && <th>已提交题目</th>}
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleTasks.map((t) => (
                  <tr key={t.id}>
                    <td>{t.title}</td>
                    <td>{KINDS[t.kind]}</td>
                    <td>
                      {date(t.due_at)}
                      {t.late_until && (
                        <>
                          <br />
                          补交至 {date(t.late_until)}
                        </>
                      )}
                    </td>
                    <td>{teacher ? STATUS[t.status] : <span className={`task-state task-state-${t.student_progress?.status || "ended"}`}>{t.student_progress?.late ? "可补交" : t.student_progress ? STUDENT_STATUS[t.student_progress.status] : STATUS[t.status]}</span>}</td>
                    {!teacher && <td>{t.student_progress?.total ? `${t.student_progress.submitted} / ${t.student_progress.total} 题` : "报告 / 附件"}</td>}
                    <td>
                      <button
                        className="training-link"
                        disabled={busy}
                        onClick={() => run(() => open(t.id))}
                      >
                        {teacher ? "查看任务" : t.student_progress?.status === "not_started" ? "开始任务" : t.student_progress?.status === "in_progress" ? "继续完成" : "查看详情"}
                      </button>
                      {teacher && t.status === "draft" && (
                        <button
                          className="training-link"
                          disabled={busy}
                          onClick={() => edit(t)}
                        >
                          编辑
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      </div>
      {editing && (
        <form
          ref={editorRef}
          className="task-editor"
          onSubmit={(e) => {
            e.preventDefault();
            run(async () => {
              const saved = await api.saveTask(
                {
                  class_id: classId,
                  title,
                  kind,
                  instructions,
                  rubric,
                  due_at: new Date(due).getTime() / 1000,
                  late_until:
                    kind !== "quiz" && late
                      ? new Date(late).getTime() / 1000
                      : null,
                  max_submissions: limit,
                  exercise_ids: selected,
                  ...(lessonPlan ? { lesson_plan_id: lessonPlan.id, lesson_plan_revision: lessonPlan.revision } : {}),
                },
                editId || undefined,
              );
              setEditId(saved.id);
              setEditing(false);
              await refresh();
              await open(saved.id);
              setMessage("草稿已保存，检查题目和截止时间后发布。");
            });
          }}
        >
          <h2>{editId ? "编辑任务草稿" : "布置任务"}</h2>
          <p className="training-muted">{classroom?.title} · {lessonPlan ? "来自已保存的备课安排" : "填写任务内容"} → 保存草稿 → 核对并发布</p>
          <div className="training-filters">
            <label>
              类型
              <select
                value={kind}
                onChange={(e) => {
                  const v = e.target.value as SchoolTask["kind"];
                  setKind(v);
                  setLimit(v === "quiz" ? 1 : 10);
                  setLate("");
                }}
              >
                {Object.entries(KINDS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              截止时间
              <input
                type="datetime-local"
                required
                value={due}
                onChange={(e) => setDue(e.target.value)}
              />
            </label>
            {kind !== "quiz" && (
              <label>
                允许补交至（可选）
                <input
                  type="datetime-local"
                  value={late}
                  onChange={(e) => setLate(e.target.value)}
                />
              </label>
            )}
            <label>
              每题核验次数
              <input
                type="number"
                min={1}
                max={kind === "quiz" ? 5 : 20}
                required
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
              />
            </label>
          </div>
          <label>
            任务名称
            <input
              required
              maxLength={120}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label>
            说明
            <textarea
              maxLength={6000}
              rows={3}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
            />
          </label>
          {kind === "experiment" && (
            <label>
              评分标准
              <textarea
                required
                maxLength={3000}
                rows={3}
                value={rubric}
                onChange={(e) => setRubric(e.target.value)}
                placeholder="写明评分项和分值，报告由教师评阅。"
              />
            </label>
          )}
          <p className="training-muted">
            {kind === "quiz"
              ? "测验结束后公布核验结果，过程中不提供提示与解析。交卷会核验已保存草稿，未作答题计零分；到截止时间按已保存作答自动归档。"
              : "作业和实验训练题即时反馈，交卷时固定已核验作答。"}{" "}
            发布后题目和期限固定。
          </p>
          <fieldset>
            <legend>
              选择本课程已发布训练题（最多 20 道，已选 {selected.length}）
            </legend>
            {content.isPending && <p role="status">正在读取已发布题目…</p>}
            {content.error && <p role="alert">{content.error.message} <button type="button" className="training-link" disabled={content.isFetching} onClick={() => content.refetch()}>重新读取题目</button></p>}
            {content.isSuccess && !available.length && (
              <p>本课程暂无已发布训练题。实验可以只提交报告和代码材料。</p>
            )}
            <div className="task-question-list">
              {available.map((e) => (
                <label key={e.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(e.id)}
                    disabled={!selected.includes(e.id) && selected.length >= 20}
                    onChange={(event) =>
                      setSelected(
                        event.target.checked
                          ? [...selected, e.id]
                          : selected.filter((id) => id !== e.id),
                      )
                    }
                  />
                  <span>
                    {e.title} · {e.chapter_title}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="training-actions">
            <button className="send-action" disabled={busy}>
              保存草稿
            </button>
            <button
              type="button"
              className="ghost-action"
              disabled={busy}
              onClick={() => confirmDiscard(() => { setEditing(false); setMessage(""); })}
            >
              放弃修改
            </button>
          </div>
        </form>
      )}
      {detail && task && (
        <section ref={detailRef} className="task-detail">
          <div className="training-actions">
            <h2>{task.title}</h2>
            <button className="training-link" onClick={() => setDetail(null)}>
              {teacher ? preparation ? "返回备课安排" : reports ? "返回学情报告" : "返回任务列表" : "返回我的作业"}
            </button>
          </div>
          <p>
            {KINDS[task.kind]} · {STATUS[task.status]} · 截止{" "}
            {date(task.due_at)}
            {task.late_until && ` · 补交至 ${date(task.late_until)}`} · 每题最多{" "}
            {task.max_submissions} 次核验
          </p>
          <p className="task-prose">
            {task.instructions || "按题目要求完成并交卷。"}
          </p>
          {task.rubric && (
            <>
              <h3>评分标准</h3>
              <p className="task-prose">{task.rubric}</p>
            </>
          )}
          {teacher && !studentId && (
            <div className="training-actions">
              {task.status === "draft" && (
                <>
                  <button
                    className="ghost-action"
                    disabled={busy}
                    onClick={() => edit(task)}
                  >
                    编辑草稿
                  </button>
                  <button
                    className="send-action"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.taskAction(task.id, "publish");
                        await refresh();
                        await open(task.id);
                        setMessage("任务已发布。");
                      })
                    }
                  >
                    发布到班级
                  </button>
                </>
              )}
              {task.status === "published" && (
                <button
                  className="ghost-action"
                  disabled={busy}
                  onClick={() => setConfirmClose(true)}
                >
                  提前结束任务
                </button>
              )}
              {confirmClose && (
                <>
                  <span>结束后不能提交，测验将公布结果。</span>
                  <button
                    className="send-action"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.taskAction(task.id, "close");
                        await refresh();
                        await open(task.id);
                      })
                    }
                  >
                    确认结束
                  </button>
                  <button
                    className="ghost-action"
                    onClick={() => setConfirmClose(false)}
                  >
                    取消
                  </button>
                </>
              )}
            </div>
          )}
          {studentId && (
            <p>
              学生：
              {
                reportRows.find((r) => r.student_id === studentId)?.student_name
              }{" "}
              · 以下为只读作答证据
            </p>
          )}
          <div className="training-table-wrap">
            <table className="training-table">
              <thead>
                <tr>
                  <th>题目 / 固定版本</th>
                  <th>作答状态</th>
                  <th>核验次数</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {task.exercises.map((e) => {
                  const attempt = detail.attempts.find(
                    (a) => a.exercise.id === e.id,
                  );
                  return (
                    <tr key={e.id}>
                      <td>
                        {e.title}
                      </td>
                      <td>
                        {teacher && !studentId
                          ? "—"
                          : !attempt
                            ? "未开始"
                            : detail.feedback_hidden
                              ? attempt.submission_count
                                ? "已提交，待公布"
                                : "作答中"
                              : attempt.status === "passed"
                                ? "已通过"
                                : attempt.status === "needs_correction"
                                  ? "待订正"
                                  : "作答中"}
                      </td>
                      <td>
                        {teacher && !studentId
                          ? "—"
                          : attempt?.submission_count || 0}
                      </td>
                      <td>
                        {!teacher && (attempt || editable) && (
                          <button
                            className="training-link"
                            disabled={busy}
                            onClick={() =>
                              run(async () => {
                                const next =
                                  attempt ||
                                  (await api.startTask(task.id, e.id));
                                onOpenAttempt(next.id, task.id);
                              })
                            }
                          >
                            {attempt
                              ? finalized ||
                                attempt.status === "passed" ||
                                !attempt.assignment?.can_edit
                                ? "查看作答"
                                : "继续作答"
                              : "开始作答"}
                          </button>
                        )}
                        {teacher && "只读"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {task.kind === "experiment" && (
            <>
              <h3>实验报告与代码材料</h3>
              <p className="training-muted">
                最多 3 个文件，每个不超过 1 MB。支持 UTF-8 文本、Markdown、C
                源码和 PDF。交卷后材料锁定，代码材料供教师评阅。
              </p>
              <label>
                报告
                <textarea
                  rows={6}
                  maxLength={12000}
                  readOnly={!editable}
                  value={report}
                  onChange={(e) => setReport(e.target.value)}
                />
              </label>
              {editable && (
                <div className="training-actions">
                  <button
                    className="ghost-action"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        adopt(await api.saveTaskReport(task.id, report));
                        setMessage("报告已保存。");
                      })
                    }
                  >
                    保存报告
                  </button>
                  <label>
                    材料文件
                    <input
                      type="file"
                      accept=".txt,.md,.c,.pdf"
                      onChange={(e) => setFile(e.target.files?.[0] || null)}
                    />
                  </label>
                  <button
                    className="ghost-action"
                    disabled={busy || !file}
                    onClick={() =>
                      run(async () => {
                        if (file) {
                          await api.saveTaskReport(task.id, report);
                          adopt(await api.uploadTaskFile(task.id, file));
                          setFile(null);
                          setMessage("材料已上传。");
                        }
                      })
                    }
                  >
                    上传材料
                  </button>
                </div>
              )}
              <ul>
                {detail.files.map((f) => (
                  <li key={f.id}>
                    <a href={`/api/school/tasks/${task.id}/files/${f.id}`}>
                      {f.name}
                    </a>{" "}
                    · {Math.ceil(f.size / 1024)} KB{" "}
                    {editable && (
                      <button
                        className="training-link"
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await api.saveTaskReport(task.id, report);
                            await api.deleteTaskFile(task.id, f.id);
                            adopt(await api.taskDetail(task.id));
                          })
                        }
                      >
                        删除
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          {detail.submission && (
            <>
              <p>
                {finalized
                  ? `已交卷 · ${date(detail.submission.finalized_at)}${detail.submission.automatic ? " · 结束归档" : ""}${detail.submission.late ? " · 补交" : ""}`
                  : "尚未交卷"}
              </p>
              {detail.feedback_hidden && (
                <p role="status">
                  测验结果将在截止时间或教师结束测验后公布。
                  {finalized && "交卷后不能修改。"}
                </p>
              )}
              {finalized && !detail.feedback_hidden && (
                <p>
                  得分：
                  {detail.submission.score ??
                    (task.kind === "experiment" ? "待教师评分" : "—")}
                  {task.kind === "experiment" &&
                    detail.submission.training_score != null &&
                    ` · 训练题通过得分 ${detail.submission.training_score}（与教师评分分开记录）`}
                </p>
              )}
              {detail.submission.reviews.map((r, i) => (
                <p key={i} className="task-prose">
                  {r.teacher_name} · {date(r.created_at)}
                  {r.score != null && ` · ${r.score} 分`}
                  <br />
                  {r.comment}
                </p>
              ))}
            </>
          )}
          {editable && (
            <div className="training-actions">
              <button
                className="send-action"
                disabled={busy}
                onClick={() => setConfirmFinal(true)}
              >
                交卷
              </button>
              {confirmFinal && (
                <>
                  <span>交卷后锁定本任务的作答与材料。</span>
                  <button
                    className="send-action"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        if (task.kind === "experiment")
                          await api.saveTaskReport(task.id, report);
                        adopt(await api.finalizeTask(task.id));
                        await refresh();
                        setMessage("已交卷。");
                      })
                    }
                  >
                    确认交卷
                  </button>
                  <button
                    className="ghost-action"
                    disabled={busy}
                    onClick={() => setConfirmFinal(false)}
                  >
                    取消
                  </button>
                </>
              )}
            </div>
          )}
          {!teacher && !finalized && !editable && (
            <p>任务已结束，无法继续提交。</p>
          )}
          {teacher && studentId && (
            <>
              {detail.attempts.map((a) => (
                <details key={a.id} className="task-evidence">
                  <summary>
                    {a.exercise.title} · 作答证据
                  </summary>
                  <p>
                    提示 {a.hint_count} 次 ·{" "}
                    {a.solution_viewed ? "已查看解析" : "未查看解析"} ·
                    首次独立通过 {a.first_unassisted_pass ? "是" : "否"}
                    {a.tutoring_viewed ? " · 已打开问答辅导" : ""}
                  </p>
                  {a.submissions.map((s, i) => (
                    <div key={i}>
                      <h4>
                        第 {i + 1} 次提交 · {date(s.created_at)} ·{" "}
                        {s.evaluation?.passed ? "通过" : "未通过"}
                      </h4>
                      <p>{s.evaluation?.first_error?.message}</p>
                      <div className="training-table-wrap">
                        <table className="training-table">
                          <tbody>
                            {s.rows.map((row, j) => (
                              <tr key={j}>
                                <th>第 {j + 1} 行</th>
                                {Object.entries(row).map(([k, v]) => (
                                  <td key={k}>
                                    {k}: {v}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ))}
                </details>
              ))}
              {finalized && (
                <form
                  className="task-editor"
                  onSubmit={(e) => {
                    e.preventDefault();
                    run(async () => {
                      adopt(
                        await api.reviewTask(
                          task.id,
                          studentId,
                          comment,
                          task.kind === "experiment"
                            ? Number(score)
                            : undefined,
                        ),
                      );
                      await refresh();
                      setMessage("评阅已保存，并保留历史记录。");
                    });
                  }}
                >
                  <h3>教师评阅</h3>
                  {task.kind === "experiment" && (
                    <label>
                      实验评分（0–100）
                      <input
                        type="number"
                        min={0}
                        max={100}
                        step="0.1"
                        required
                        value={score}
                        onChange={(e) => setScore(e.target.value)}
                      />
                    </label>
                  )}
                  <label>
                    评语
                    <textarea
                      rows={3}
                      maxLength={3000}
                      required
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                    />
                  </label>
                  <button className="send-action" disabled={busy}>
                    保存评阅
                  </button>
                </form>
              )}
            </>
          )}
        </section>
      )}
    </section>
  );
}
