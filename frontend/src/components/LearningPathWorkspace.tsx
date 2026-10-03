import { LearningEvidence } from "./LearningEvidence";
import { useLearningWorkflow } from "../useLearningWorkflow";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { MarkdownMessage } from "./MarkdownMessage";
import type {
  ChapterMaterial,
  LearningQuestionContext,
  LearningSubject,
  LearningAction,
} from "../learningTypes";

const OBJECTIVE_STATUS = {
  needs_work: "有待订正或复测证据",
  evidence: "关联训练已有独立证据",
  insufficient: "尚缺独立作答证据",
  unassessed: "暂无自动核验训练",
};
const READINESS = {
  needs_work: "需回看基础",
  evidence: "关联训练已有独立证据",
  partial: "部分目标有证据",
  insufficient: "证据不足",
};

const ANSWER_FIELDS: Record<string, string> = {
  value: "作答",
  code: "代码",
  frames: "页框",
  event: "命中情况",
  evicted: "淘汰页",
  process: "进程",
  start: "开始时间",
  end: "结束时间",
  need: "需求",
  work: "可用资源",
  verdict: "判断",
};

export function LearningPathWorkspace({
  userId,
  section,
  initialSubject,
  onOpenAttempt,
  onQuestion,
}: {
  userId: string;
  section: "path" | "review";
  initialSubject?: LearningSubject;
  onOpenAttempt: (id: string) => void;
  onQuestion: (
    subject: LearningSubject,
    prompt: string,
    context?: LearningQuestionContext,
  ) => void;
}) {
  const [subject, setSubject] = useState<LearningSubject>(() => {
    const saved = sessionStorage.getItem(`learning-subject:${userId}`);
    return saved === "C_program" ||
      saved === "operating_systems" ||
      saved === "cybersec_lab"
      ? saved
      : initialSubject || "operating_systems";
  });
  const [chapterId, setChapterId] = useState("");
  const [pathView, setPathView] = useState<"chapters" | "plan" | "evidence">("chapters");
  const [reviewFilter, setReviewFilter] = useState("pending");
  const [material, setMaterial] = useState<ChapterMaterial | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [explaining, setExplaining] = useState("");
  const [error, setError] = useState("");
  const reader = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    contentRef.current?.scrollTo({ top: 0 });
  }, [pathView, subject, section]);
  useEffect(() => {
    if (material)
      reader.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [material]);
  const path = useQuery({
    queryKey: ["learning-path", userId],
    queryFn: api.learningPath,
    retry: false,
    refetchInterval: 30000,
  });
  const plan = useQuery({
    queryKey: ["study-plan", userId, subject],
    queryFn: () => api.studyPlan(subject),
    enabled: section === "path",
    retry: false,
    refetchInterval: 30000,
  });
  const [studyMinutes, setStudyMinutes] = useState(30);
  const workflow = useLearningWorkflow(userId, "plan", subject, () => plan.refetch(), section === "path");
  const [targetChapter, setTargetChapter] = useState("");
  useEffect(() => {
    setStudyMinutes(plan.data?.profile.minutes ?? 30);
    setTargetChapter(plan.data?.profile.chapter_id ?? "");
  }, [subject, plan.data?.profile.minutes, plan.data?.profile.chapter_id]);
  const course = path.data?.courses.find((c) => c.id === subject);
  const points =
    path.data?.points.filter(
      (p) =>
        p.subject_id === subject && (!chapterId || p.chapter_id === chapterId),
    ) || [];
  const reviews =
    path.data?.reviews.filter(
      (r) =>
        r.exercise.subject_id === subject &&
        (reviewFilter === "all" ||
          (reviewFilter === "due" && (r.due || r.active_attempt_id)) ||
          (reviewFilter === "pending" && !r.complete) ||
          (reviewFilter === "complete" && r.complete)),
    ) || [];

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await path.refetch();
      if (section === "path") await plan.refetch();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  function openMaterial(id: string, start = 1, q = "") {
    return run(async () => {
      setMaterial(await api.chapterMaterial(id, start, q));
      setSearch(q);
    });
  }

  function followAction(action: LearningAction) {
    return run(async () => {
      const started = await api.beginLearningRecommendation(
        action.point_id,
        action.token,
      );
      if (started.attempt_id) onOpenAttempt(started.attempt_id);
      if (action.kind === "material") {
        setMaterial(
          await api.chapterMaterial(action.chapter_id!, 1, action.query || ""),
        );
        setSearch(action.query || "");
      }
      if (action.kind === "explain") {
        setExplaining(action.point_id);
        try {
          await api.learningExplanation(action.point_id);
        } finally {
          setExplaining("");
        }
      }
    });
  }

  return (
    <main className="training-surface">
      <header className="training-header">
        <h1>{section === "path" ? "课程进度" : "错题复习"}</h1>
        <span>三门课程</span>
        <button
          disabled={busy}
          onClick={() => {
            path.refetch();
            if (section === "path") plan.refetch();
          }}
        >
          刷新记录
        </button>
      </header>
      {section === "path" && (
        <nav className="training-tabs" aria-label="课程进度视图">
          {([["chapters", "章节路径"], ["plan", "学习计划"], ["evidence", "学习证据"]] as const).map(([id, label]) => (
            <button key={id} className={pathView === id ? "selected" : ""} aria-current={pathView === id ? "page" : undefined} onClick={() => { setPathView(id); setMaterial(null); }}>{label}</button>
          ))}
        </nav>
      )}
      {error && (
        <p className="training-error" role="alert">
          {error}
        </p>
      )}
      <div className="training-content" ref={contentRef}>
        <section className="learning-records">
          {path.isPending && <p role="status">正在读取学习记录…</p>}
          {path.error && (
            <p role="alert">
              {path.error.message}
              <button className="training-link" onClick={() => path.refetch()}>
                重试
              </button>
            </p>
          )}
          <div className="training-courses" role="group" aria-label="复习课程">
            {path.data?.courses.map((c) => (
              <button
                key={c.id}
                className={subject === c.id ? "selected" : ""}
                disabled={busy}
                onClick={() => {
                  setSubject(c.id);
                  sessionStorage.setItem(`learning-subject:${userId}`, c.id);
                  setChapterId("");
                  setMaterial(null);
                }}
                aria-pressed={subject === c.id}
              >
                {c.name}
                <small>
                  {c.read_chapters}/{c.chapters.length} 章标记已读
                </small>
              </button>
            ))}
          </div>
          <p className="training-muted">{path.data?.notice}</p>
          {section === "path" && course && (
            <>
              <section
                hidden={pathView !== "plan"}
                className="personal-next study-plan"
                aria-label="入门诊断与学习计划"
              >
                <h2>入门诊断与学习计划</h2>
                {plan.isPending && <p role="status">正在读取计划…</p>}
                {plan.error && (
                  <p role="alert">
                    {plan.error.message}
                    <button onClick={() => plan.refetch()}>重试</button>
                  </p>
                )}
                {plan.data && (
                  <>
                    <p className="training-muted">{plan.data.notice}</p>
                    <div className="study-plan-settings">
                      <label>
                        目标章节
                        <select
                          aria-label="学习目标章节"
                          value={targetChapter}
                          disabled={busy}
                          onChange={(e) => setTargetChapter(e.target.value)}
                        >
                          <option value="">全课程基础与薄弱点</option>
                          {course.chapters.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.title}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        每次学习时间（分钟）
                        <input
                          aria-label="每次学习时间"
                          type="number"
                          min={15}
                          max={120}
                          value={studyMinutes}
                          disabled={busy}
                          onChange={(e) =>
                            setStudyMinutes(Number(e.target.value))
                          }
                        />
                      </label>
                      <button
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await api.configureStudyPlan(
                              subject,
                              studyMinutes,
                              targetChapter,
                            );
                          })
                        }
                      >
                        {plan.data.profile.configured
                          ? "更新学习安排"
                          : "保存学习目标"}
                      </button>
                    </div>
                    <div>
                      {(studyMinutes !== plan.data.profile.minutes ||
                        targetChapter !== plan.data.profile.chapter_id) && (
                        <p>请先保存目标和时间，再生成 AI 计划。</p>
                      )}
                      <button
                        className="send-action"
                        disabled={
                          busy || workflow.running ||
                          studyMinutes !== plan.data.profile.minutes ||
                          targetChapter !== plan.data.profile.chapter_id
                        }
                        onClick={() =>
                          run(async () => {
                            try { await api.generateStudyPlan(subject); }
                            finally { await workflow.refresh(); }
                          })
                        }
                      >
                        {busy
                          ? "正在处理…"
                          : plan.data.ai_plan
                            ? "重新生成 AI 计划"
                            : "让 AI 分析并安排"}
                      </button>
                      {workflow.running && <p>AI 计划正在生成，刷新或离开后仍可返回查看。</p>}
                      {workflow.run?.error && <p>{workflow.run.error}</p>}
                      {workflow.error && <p>{workflow.error}</p>}
                      {workflow.run?.can_resume && <button className="ghost-action" disabled={busy || workflow.running}
                        onClick={() => run(async () => {
                          try { await api.generateStudyPlan(subject, workflow.run!.id); }
                          finally { await workflow.refresh(); }
                        })}>继续上次计划</button>}
                      {plan.data.ai_stale && (
                        <p>
                          学习记录或目标已变化，原 AI
                          计划已失效。当前显示基础安排，可重新生成。
                        </p>
                      )}
                      {plan.data.ai_plan ? (
                        <>
                          <p className="training-muted">
                            AI 个性化计划 ·{" "}
                            {new Date(
                              plan.data.ai_plan.created_at * 1000,
                            ).toLocaleString()}
                          </p>
                          <MarkdownMessage
                            content={plan.data.ai_plan.analysis}
                          />
                        </>
                      ) : (
                        <p className="training-muted">
                          当前为基础安排。AI
                          会结合答题证据分析薄弱点，并从可用任务中选择学习安排。
                        </p>
                      )}
                    </div>
                    <h3>
                      短诊断 · {plan.data.diagnostic_completed}/
                      {plan.data.diagnostics.length} 个知识点已有作答
                    </h3>
                    {!plan.data.diagnostics.length && (
                      <p>当前范围没有可用的诊断训练，可以先阅读课程资料。</p>
                    )}
                    {plan.data.blocked_points > 0 && (
                      <p>
                        部分知识点涉及尚未公布的测验，待公布后再纳入诊断和计划。
                      </p>
                    )}
                    <ul>
                      {plan.data.diagnostics.map((d) => (
                        <li key={d.point_id}>
                          <strong>{d.title}</strong> ·{" "}
                          {d.completed ? d.status : "等待作答"}
                          <p>{d.basis}</p>
                          <details>
                            <summary>诊断覆盖的目标</summary>
                            {d.objectives.map((o) => (
                              <p key={o}>{o}</p>
                            ))}
                          </details>
                          {!d.completed && (
                            <button
                              className="ghost-action"
                              disabled={
                                busy || (!d.exercise_id && !d.attempt_id)
                              }
                              onClick={() =>
                                run(async () => {
                                  const result = await api.beginDiagnostic(
                                    subject,
                                    d.point_id,
                                  );
                                  onOpenAttempt(result.attempt_id);
                                })
                              }
                            >
                              {d.attempt_id ? "继续诊断" : "开始诊断"}
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                    <h3>
                      本次学习安排 · 预计 {plan.data.estimated_minutes} 分钟
                    </h3>
                    <p className="training-muted">
                      {plan.data.diagnostic_completed <
                      plan.data.diagnostics.length
                        ? "尚有诊断未完成，以下为已有证据下的暂定安排。"
                        : "根据当前证据安排；继续作答后会重新调整。"}
                    </p>
                    {!plan.data.tasks.length && (
                      <p>
                        当前没有可执行的推荐任务。可查看课程目标、资料和已有训练记录。
                      </p>
                    )}
                    <ol>
                      {plan.data.tasks.map((t) => (
                        <li key={t.token}>
                          <strong>
                            {t.point_title}：{t.title}
                          </strong>{" "}
                          · 约 {t.estimated_minutes} 分钟
                          <p>{t.ai_reason || t.reason}</p>
                          <button
                            className="ghost-action"
                            disabled={busy || !!explaining}
                            onClick={() => followAction(t)}
                          >
                            执行此项
                          </button>
                          {t.evidence_ids.length > 0 && (
                            <button
                              className="training-link"
                              disabled={busy}
                              onClick={() =>
                                onOpenAttempt(
                                  t.evidence_ids[t.evidence_ids.length - 1],
                                )
                              }
                            >
                              查看相关作答
                            </button>
                          )}
                        </li>
                      ))}
                    </ol>
                  </>
                )}
              </section>
              <section hidden={pathView !== "chapters"}>
              <dl className="learning-summary">
                <div>
                  <dt>本人标记已读</dt>
                  <dd>
                    {course.read_chapters} / {course.chapters.length} 章
                  </dd>
                </div>
                <div>
                  <dt>已有独立训练证据</dt>
                  <dd>{course.independent_chapters} 章</dd>
                </div>
                <div>
                  <dt>已开放训练</dt>
                  <dd>{course.exercise_count} 道</dd>
                </div>
              </dl>
              <h2>章节学习路径</h2>
              <div className="training-table-wrap">
                <table className="training-table learning-path-table">
                  <thead>
                    <tr>
                      <th>章节</th>
                      <th>阅读记录</th>
                      <th>训练证据</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {course.chapters.map((c) => (
                      <tr key={c.id}>
                        <td>
                          <strong>
                            {c.number}. {c.title}
                          </strong>
                          <details className="chapter-objectives">
                            <summary>学习目标与先修要求</summary>
                          <ul>
                            {c.objectives.map((o) => (
                              <li key={o.id}>
                                {o.text}
                                <small className="path-detail">
                                  {OBJECTIVE_STATUS[o.status]} ·{" "}
                                  {o.exercise_count} 道开放关联题
                                </small>
                              </li>
                            ))}
                          </ul>
                          {c.prerequisites.length > 0 ? (
                            <details>
                              <summary>
                                先修基础 · {c.prerequisites.length} 章
                              </summary>
                              {c.prerequisites.map((p) => (
                                <p key={p.id}>
                                  <button
                                    className="training-link"
                                    disabled={busy}
                                    onClick={() => openMaterial(p.id)}
                                  >
                                    {p.title}
                                  </button>{" "}
                                  · {READINESS[p.readiness]}
                                </p>
                              ))}
                              <small className="path-detail">
                                完整依赖顺序：
                                {c.prerequisite_path
                                  .map(
                                    (id) =>
                                      course.chapters.find((x) => x.id === id)
                                        ?.title,
                                  )
                                  .join(" → ")}
                                。各分支可并行学习，箭头表示基础优先的阅读顺序。
                              </small>
                            </details>
                          ) : (
                            <small className="path-detail">
                              本课程起点，无本课程内先修要求。
                            </small>
                          )}
                          </details>
                        </td>
                        <td>
                          {c.read_at
                            ? `已读 · ${new Date(c.read_at * 1000).toLocaleDateString()}`
                            : "未标记"}
                        </td>
                        <td>
                          {c.point_count
                            ? `${c.independent_points}/${c.point_count} 个知识点有独立证据 · ${c.exercise_count} 道开放题`
                            : "暂无自动核验训练"}
                        </td>
                        <td>
                          <div className="path-actions">
                            <button
                              className="ghost-action"
                              disabled={busy || !c.material_available}
                              onClick={() => openMaterial(c.id)}
                            >
                              阅读资料
                            </button>
                            <button
                              className="training-link"
                              disabled={busy}
                              onClick={() =>
                                run(async () => {
                                  await api.markReading(c.id, !c.read_at);
                                })
                              }
                            >
                              {c.read_at ? "撤销已读" : "标记已读"}
                            </button>
                            <button
                              className="training-link"
                              onClick={() => {
                                setChapterId(c.id);
                                setPathView("evidence");
                                setMaterial(null);
                              }}
                            >
                              知识点
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              </section>
              <section hidden={pathView !== "evidence"}>
              {plan.data && <>
                    <LearningEvidence key={subject} subject={subject} timeline={plan.data.timeline} onOpenAttempt={onOpenAttempt} />
                    <details className="evidence-section">
                    <summary>推荐效果核验</summary>
                    <p className="training-muted">对照同知识点推荐前后的新题首次独立作答，包含未通过的尝试。订正和辅助作答另行保留，不能仅凭前后变化认定推荐有效。</p>
                    {!plan.data.recommendation_checks.length && <p>还没有已接受的推荐。开始一项学习安排后，这里会跟进实际作答。</p>}
                    {plan.data.recommendation_checks.map((f) => <details key={f.id}>
                      <summary>{f.point_title} · {f.action.title} · {f.label}</summary>
                      <p>推荐前独立通过 {f.validation.baseline.passed}/{f.validation.baseline.attempts} 题；推荐后独立通过 {f.validation.followup.passed}/{f.validation.followup.attempts} 题。没有作答时不计算通过率。</p>
                      <p>{f.validation.retention_label}。</p>
                      {f.validation.other_recommendations > 0 && <p>期间还接受了 {f.validation.other_recommendations} 项同知识点推荐，不能把变化单独归于这一项。</p>}
                      <ul>{f.validation.checks.map((check) => <li key={`${check.attempt_id}-${check.submission_number}`}>
                        {check.delayed ? "间隔新题" : check.independent ? "首次独立作答" : "辅助、重复或订正作答"}：{check.passed ? "通过" : "未通过"}
                        <button className="training-link" disabled={busy} onClick={() => onOpenAttempt(check.attempt_id)}>查看第 {check.submission_number} 次提交所在练习</button>
                      </li>)}</ul>
                    </details>)}
                    </details>
                    <details className="evidence-section">
                    <summary>持续学习记录</summary>
                    <p className="training-muted">依据当前范围内最近的互动与后续作答更新。AI 假设不等于事实，后续通过也不代表具体错因已被证实。</p>
                    {!plan.data.memories.length && <p>还没有互动记录。完成一次互动诊断或过程预测后，这里会跟进后续作答。</p>}
                    {plan.data.memories.map((memory) => <details key={memory.attempt_id}>
                      <summary>{memory.title} · {({pending: "待核验", needs_work: "仍需订正", independent_evidence: "新增独立证据", assisted_evidence: "仍需独立复测"} as Record<string, string>)[memory.status]}</summary>
                      {memory.hypothesis && <p>当时的待验证假设：{memory.hypothesis}</p>}
                      {memory.answers.map((a, i) => <p key={i}>{a.question} — 你的回答：{a.answer}</p>)}
                      {memory.predictions.map((p, i) => <p key={i}>{p.title} — 你的预测：{p.prediction}</p>)}
                      <p>{memory.basis}</p>
                      <button className="training-link" onClick={() => onOpenAttempt(memory.attempt_id)}>查看原练习与互动</button>
                      <ul>{memory.followups.map((f) => <li key={`${f.attempt_id}-${f.submission_number}`}>
                        第 {f.submission_number} 次提交：{f.passed ? "通过" : `未通过${f.error ? `：${f.error}` : ""}`}；{f.independent_new_question ? "不同题目首次独立作答" : "订正、辅导或原题作答"}
                        <button className="training-link" onClick={() => onOpenAttempt(f.attempt_id)}>查看核验记录</button>
                      </li>)}</ul>
                    </details>)}
                    </details>
              </>}
              <h2>个人学习状态与证据</h2>
              <p className="training-muted">
                按已开放训练知识点整理。新题首次独立通过、辅助通过与订正分别记录；阅读标记不计入独立证据。
              </p>
              {chapterId && (
                <p>
                  <span>
                    {course.chapters.find((c) => c.id === chapterId)?.title}
                  </span>{" "}
                  <button
                    className="training-link"
                    onClick={() => setChapterId("")}
                  >
                    查看全课程
                  </button>
                </p>
              )}
              {!points.length && (
                <p className="training-muted">
                  本章暂未开放自动核验训练，可以先阅读资料和课程问答。
                </p>
              )}
              {points.map((p) => (
                <div className="path-point" key={p.id}>
                  <div>
                    <h3>
                      {p.title} <span>{p.status}</span>
                    </h3>
                    <p>{p.reason}</p>
                    <p className="training-muted">
                      已提交 {p.learning_state.submitted_count} 次练习 ·
                      新题首次独立通过 {p.learning_state.independent_count} 道 ·
                      辅助、重复或订正通过{" "}
                      {p.learning_state.non_independent_pass_count} 次
                    </p>
                    {p.learning_state.last_activity_at && (
                      <p className="training-muted">
                        最近学习活动：
                        {new Date(
                          p.learning_state.last_activity_at * 1000,
                        ).toLocaleString()}
                        {p.learning_state.last_independent_at &&
                          `；最近独立通过：${new Date(p.learning_state.last_independent_at * 1000).toLocaleString()}`}
                      </p>
                    )}
                    <details className="state-evidence">
                      <summary>
                        查看判断依据与错误记录（
                        {p.learning_state.evidence.length} 次练习）
                      </summary>
                      {p.learning_state.errors.length > 0 && (
                        <section aria-label={`${p.title}的错误记录`}>
                          <h4>已记录的错误</h4>
                          <p className="training-muted">
                            每次提交只记录首个错误。次数按提交统计，涉及练习数另列；已订正的错误仍保留。
                          </p>
                          {p.learning_state.errors.map((error) => (
                            <details key={error.code}>
                              <summary>
                                {error.label}：{error.count} 次提交，涉及{" "}
                                {error.attempt_count} 次练习
                              </summary>
                              <ul>
                                {error.occurrences.map((occurrence) => (
                                  <li
                                    key={`${occurrence.attempt_id}:${occurrence.submission_number}`}
                                  >
                                    {new Date(
                                      occurrence.created_at * 1000,
                                    ).toLocaleString()}{" "}
                                    · 第 {occurrence.submission_number}{" "}
                                    次提交，第 {occurrence.step} 步：
                                    {occurrence.message}
                                    <button
                                      className="training-link"
                                      onClick={() =>
                                        onOpenAttempt(occurrence.attempt_id)
                                      }
                                    >
                                      查看原作答
                                    </button>
                                  </li>
                                ))}
                              </ul>
                            </details>
                          ))}
                        </section>
                      )}
                      {!p.learning_state.evidence.length && (
                        <p>
                          还没有可展示的练习记录。未公布测验的结果不会进入这里。
                        </p>
                      )}
                      {p.learning_state.evidence.map((evidence) => (
                        <article
                          className="state-attempt"
                          key={evidence.attempt_id}
                        >
                          <h4>
                            {evidence.title}
                            {evidence.review_id
                              ? " · 间隔复测"
                              : evidence.parent_id
                                ? " · 新题复测"
                                : ""}
                          </h4>
                          <p className="training-muted">
                            题目版本 {evidence.content_version} · 当前累计提示{" "}
                            {evidence.activity.hint_count} 次
                            {evidence.activity.solution_viewed
                              ? " · 已查看解析"
                              : ""}
                            {evidence.activity.tutoring_viewed
                              ? " · 已进入作答问答"
                              : ""}
                          </p>
                          {!evidence.submissions.length && (
                            <p>尚未提交，不作为通过证据。</p>
                          )}
                          <ol className="state-submissions">
                            {evidence.submissions.map((submission) => (
                              <li key={submission.number}>
                                <strong>
                                  第 {submission.number} 次提交：
                                  {
                                    {
                                      incorrect: "未通过",
                                      independent_pass: "新题首次独立通过",
                                      corrected_pass: "订正后通过",
                                      non_independent_pass:
                                        "通过，未计入新题独立证据",
                                    }[submission.outcome]
                                  }
                                </strong>
                                <span className="path-detail">
                                  {new Date(
                                    submission.created_at * 1000,
                                  ).toLocaleString()}
                                </span>
                                {submission.error && (
                                  <p>
                                    第 {submission.error.step} 步 ·{" "}
                                    {submission.error.label}：
                                    {submission.error.message}
                                  </p>
                                )}
                                {submission.error?.possible_cause && (
                                  <p className="training-muted">
                                    待确认原因：
                                    {submission.error.possible_cause}
                                  </p>
                                )}
                                <p className="training-muted">
                                  {submission.assistance
                                    ? `提交前：提示 ${submission.assistance.hint_count} 次；${submission.assistance.solution_viewed ? "已查看" : "未查看"}解析；${submission.assistance.tutoring_viewed ? "已使用" : "未使用"}作答问答；${submission.assistance.previously_seen ? "重复练习此题" : "首次练习此题"}。`
                                    : "历史记录未保存提交时的辅助明细，保留当时的独立性判断。"}
                                </p>
                                <details>
                                  <summary>查看本次提交内容</summary>
                                  <pre className="state-answer">
                                    {submission.rows
                                      .map(
                                        (row, index) =>
                                          `第 ${index + 1} 步\n${Object.entries(
                                            row,
                                          )
                                            .map(
                                              ([field, value]) =>
                                                `${ANSWER_FIELDS[field] || field}：${field === "event" ? { hit: "命中", fault: "缺页" }[value] || value : value}`,
                                            )
                                            .join("\n")}`,
                                      )
                                      .join("\n\n")}
                                  </pre>
                                </details>
                              </li>
                            ))}
                          </ol>
                          <button
                            className="training-link"
                            disabled={busy}
                            onClick={() => onOpenAttempt(evidence.attempt_id)}
                          >
                            打开这次练习
                          </button>
                        </article>
                      ))}
                      <p className="training-muted">
                        判断依据来自已保存的提交结果。后续查看解析或问答不会改写此前提交的独立性。
                      </p>
                    </details>
                    {p.objectives.length > 0 && (
                      <p className="training-muted">
                        本项训练关联目标：
                        {p.objectives.map((o) => o.text).join("；")}。
                      </p>
                    )}
                    {p.prerequisite_gaps.length > 0 && (
                      <details>
                        <summary>
                          先修基础的证据缺口（{p.prerequisite_gaps.length} 章）
                        </summary>
                        <p className="training-muted">
                          证据不足不等于未掌握；阅读不会清除待订正或复测记录，也不限制直接练习。
                        </p>
                        {p.prerequisite_gaps.map((c) => (
                          <p key={c.id}>
                            <button
                              className="training-link"
                              disabled={busy}
                              onClick={() => openMaterial(c.id)}
                            >
                              {c.title}
                            </button>{" "}
                            · {READINESS[c.readiness]}
                            {c.evidence_ids?.length
                              ? ` · ${c.evidence_ids.length} 条作答记录`
                              : ""}
                          </p>
                        ))}
                      </details>
                    )}
                    {p.prerequisites.length > 0 && (
                      <p className="training-muted">
                        建议先熟悉：
                        {p.prerequisites.map((c) => (
                          <button
                            key={c.id}
                            className="training-link"
                            disabled={busy}
                            onClick={() => openMaterial(c.id)}
                          >
                            {c.title}
                          </button>
                        ))}
                      </p>
                    )}
                    {p.followups.length > 0 && (
                      <section
                        className="personal-followup"
                        aria-label={`${p.title}的推荐效果`}
                      >
                        <h4>推荐后的变化</h4>
                        <p className="training-muted">
                          按最新记录更新，最多显示最近 5
                          项。这里只描述前后变化，不能单凭这些记录认定改善由某次推荐造成。
                        </p>
                        {p.followups.map((f) => (
                          <details key={f.id}>
                            <summary>
                              {f.action.title} · {f.label}
                            </summary>
                            <p className="training-muted">
                              开始于{" "}
                              {new Date(f.created_at * 1000).toLocaleString()} ·{" "}
                              {f.completed_at
                                ? `完成于 ${new Date(f.completed_at * 1000).toLocaleString()}`
                                : "任务尚未完成"}
                            </p>
                            <p>{f.reason}</p>
                            <p>首次独立作答：推荐前通过 {f.validation.baseline.passed}/{f.validation.baseline.attempts} 题，推荐后通过 {f.validation.followup.passed}/{f.validation.followup.attempts} 题；{f.validation.retention_label}。</p>
                            {f.validation.other_recommendations > 0 && <p>同期还接受了其他同知识点推荐，不能单独归因。</p>}
                            <p>
                              学习状态：{f.before.status} → {f.after.status}
                            </p>
                            <p>
                              独立通过题数：{f.before.independent_count} →{" "}
                              {f.after.independent_count}；待间隔验证：
                              {f.before.pending_review_count} →{" "}
                              {f.after.pending_review_count}；已完成间隔验证：
                              {f.before.verified_review_count} →{" "}
                              {f.after.verified_review_count}
                            </p>
                            {f.repeated_errors.length > 0 && (
                              <p>
                                再次出现的错误：{f.repeated_errors.join("、")}
                                。历史错误仍保留，订正不等于长期消除。
                              </p>
                            )}
                            <div className="training-actions">
                              {f.attempt_id && (
                                <button
                                  className="training-link"
                                  disabled={busy}
                                  onClick={() => onOpenAttempt(f.attempt_id!)}
                                >
                                  打开推荐练习
                                </button>
                              )}
                              {f.evidence_ids.map((id, index) => (
                                <button
                                  key={id}
                                  className="training-link"
                                  disabled={busy}
                                  onClick={() => onOpenAttempt(id)}
                                >
                                  查看后续作答 {index + 1}
                                </button>
                              ))}
                            </div>
                          </details>
                        ))}
                      </section>
                    )}
                    {p.guidance_blocked && (
                      <p>
                        该知识点有尚未公布的测验，结束后再使用个性化建议与 AI
                        讲解。
                      </p>
                    )}
                    {!p.exercise_id && !p.guidance_blocked && (
                      <p className="training-muted">
                        当前没有尚未练过的已开放题目，可以回看资料、订正已有练习或等待新题发布。
                      </p>
                    )}
                    <div className="personal-point-actions">
                      {p.actions.map((action) => (
                        <div key={action.kind}>
                          <button
                            className="training-link"
                            disabled={busy}
                            onClick={() => followAction(action)}
                          >
                            {action.kind === "explain" && explaining === p.id
                              ? "正在结合你的记录生成讲解…"
                              : action.title}
                          </button>
                          <span className="path-detail">{action.reason}</span>
                        </div>
                      ))}
                    </div>
                    {explaining === p.id && (
                      <p role="status">正在读取学习证据和课程资料，请稍候。</p>
                    )}
                    {p.guidance && (
                      <section
                        className="personal-guidance"
                        aria-label={`${p.title}的AI讲解`}
                      >
                        <h4>针对你的 AI 讲解</h4>
                        <p className="training-muted">
                          {new Date(
                            p.guidance.created_at * 1000,
                          ).toLocaleString()}{" "}
                          · AI 辅助解释，核验结果以作答记录为准。
                        </p>
                        {p.guidance.stale && (
                          <p>
                            作答记录已变化，以下保留上次讲解，可点击上方按钮重新生成。
                          </p>
                        )}
                        <div className="personal-guidance-text">
                          <MarkdownMessage content={p.guidance.content} />
                        </div>
                        <p className="path-source">
                          依据：{p.guidance.source.source}，第{" "}
                          {p.guidance.source.start_line}—
                          {p.guidance.source.end_line} 行。
                        </p>
                        <button
                          className="training-link"
                          disabled={busy}
                          onClick={() =>
                            openMaterial(
                              p.guidance!.source.id,
                              p.guidance!.source.start_line,
                            )
                          }
                        >
                          核对资料原文
                        </button>
                        {p.guidance.evidence_ids.map((id, index) => (
                          <button
                            className="training-link"
                            key={id}
                            disabled={busy}
                            onClick={() => onOpenAttempt(id)}
                          >
                            作答依据 {index + 1}
                          </button>
                        ))}
                      </section>
                    )}
                  </div>
                </div>
              ))}
              </section>
            </>
          )}
          {section === "review" && (
            <>
              <dl className="learning-summary">
                <div>
                  <dt>全部课程到期复习</dt>
                  <dd>{path.data?.summary.due_reviews ?? 0}</dd>
                </div>
                <div>
                  <dt>待间隔验证</dt>
                  <dd>{path.data?.summary.pending_reviews ?? 0}</dd>
                </div>
                <div>
                  <dt>已完成两次间隔复测</dt>
                  <dd>{path.data?.summary.completed_reviews ?? 0}</dd>
                </div>
              </dl>
              <label className="path-filter">
                筛选{" "}
                <select
                  value={reviewFilter}
                  onChange={(e) => setReviewFilter(e.target.value)}
                >
                  <option value="pending">待复习</option>
                  <option value="due">已到期或作答中</option>
                  <option value="complete">已复测</option>
                  <option value="all">全部错题</option>
                </select>
              </label>
              {!reviews.length && !path.isPending && (
                <p className="training-muted">
                  当前课程没有符合筛选条件的错题。测验错题在结束后公布。
                </p>
              )}
              {reviews.map((r) => (
                <article className="review-entry" key={r.id}>
                  <h2>{r.exercise.title}</h2>
                  <p>
                    {r.exercise.chapter_title} ·{" "}
                    {r.corrected ? "原题已订正" : "原题待订正"} · 间隔独立复测{" "}
                    {r.stage}/2
                  </p>
                  <p>
                    首次错误：第 {r.first_error.step} 步 · {r.first_error.label}
                  </p>
                  <p className="training-muted">
                    {r.errors.map((e) => `${e.label} ${e.count} 次`).join("；")}
                  </p>
                  <p>
                    {r.complete
                      ? "已完成间隔验证"
                      : r.active_attempt_id
                        ? "复测作答中"
                        : `${r.due ? "已到复习时间" : "下次复习"}：${new Date(r.due_at! * 1000).toLocaleString()}`}
                  </p>
                  <p className="training-muted">{r.reason}</p>
                  {!r.recommendation && !r.complete && !r.active_attempt_id && (
                    <p>
                      已开放题库中没有未做过的同类新题，暂时无法继续独立验证。
                    </p>
                  )}
                  <div className="path-actions">
                    <button
                      className="training-link"
                      disabled={busy}
                      onClick={() => onOpenAttempt(r.id)}
                    >
                      查看原题与订正
                    </button>
                    <button
                      className="training-link"
                      disabled={busy}
                      onClick={() => openMaterial(r.exercise.chapter_id)}
                    >
                      回看章节资料
                    </button>
                    <button
                      className="training-link"
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          const context = await api.learningQuestionContext(
                            r.id,
                          );
                          onQuestion(
                            context.subject_id,
                            "请帮我分析这道错题涉及的概念和检查方法。",
                            context,
                          );
                        })
                      }
                    >
                      进入问答辅导
                    </button>
                    <button
                      className="ghost-action"
                      disabled={
                        busy ||
                        r.complete ||
                        !(r.active_attempt_id || (r.due && r.recommendation))
                      }
                      onClick={() =>
                        run(async () => {
                          onOpenAttempt((await api.startReview(r.id)).id);
                        })
                      }
                    >
                      {r.active_attempt_id ? "继续复测" : "开始间隔复测"}
                    </button>
                  </div>
                  {r.checks.map((c) => (
                    <p key={c.attempt_id} className="training-muted">
                      {new Date(c.created_at * 1000).toLocaleString()} ·{" "}
                      {c.independent_pass
                        ? "新题首次独立通过"
                        : "尚未形成间隔独立证据"}{" "}
                      <button
                        className="training-link"
                        onClick={() => onOpenAttempt(c.attempt_id)}
                      >
                        查看复测
                      </button>
                    </p>
                  ))}
                </article>
              ))}
            </>
          )}
          {material && (
            <section
              ref={reader}
              className="chapter-reader"
              aria-label="章节资料"
            >
              <div className="path-actions">
                <h2>{material.title}</h2>
                <button
                  className="training-link"
                  onClick={() => setMaterial(null)}
                >
                  关闭资料
                </button>
              </div>
              <p className="path-source">
                来源：{material.source} · 第 {material.start_line}—
                {material.end_line} 行 / 共 {material.total_lines} 行
              </p>
              <form
                className="path-actions"
                onSubmit={(e) => {
                  e.preventDefault();
                  openMaterial(material.id, 1, search);
                }}
              >
                <input
                  aria-label="资料搜索词"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  maxLength={100}
                  placeholder="在本章查找关键词"
                />
                <button className="ghost-action" disabled={busy}>
                  查找
                </button>
                {search && !material.match_line && (
                  <span>当前未找到匹配行</span>
                )}
              </form>
              <pre>{material.text}</pre>
              <div className="path-actions">
                <button
                  className="ghost-action"
                  disabled={busy || material.start_line <= 1}
                  onClick={() =>
                    openMaterial(
                      material.id,
                      Math.max(1, material.start_line - 120),
                    )
                  }
                >
                  上一段
                </button>
                <button
                  className="ghost-action"
                  disabled={busy || material.end_line >= material.total_lines}
                  onClick={() =>
                    openMaterial(material.id, material.end_line + 1)
                  }
                >
                  下一段
                </button>
                <button
                  className="training-link"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api.markReading(material.id, true);
                    })
                  }
                >
                  标记本章已读
                </button>
                <button
                  className="training-link"
                  onClick={() =>
                    onQuestion(
                      material.subject_id,
                      `请解释“${material.title}”中的核心概念。资料来源：${material.source}，第${material.start_line}—${material.end_line}行。\n以下是我正在阅读的原文：\n${material.text.slice(0, 2500)}`,
                    )
                  }
                >
                  就这段资料提问
                </button>
              </div>
            </section>
          )}
        </section>
      </div>
    </main>
  );
}
