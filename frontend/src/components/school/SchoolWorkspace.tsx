import { GradingReviewWorkspace } from "./GradingReviewWorkspace";
import { AssignmentWorkspace } from "./AssignmentWorkspace";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type {
  SchoolUser,
  ContentDetail,
  ContentVersion,
  SchoolAttempt,
} from "../../schoolTypes";
import type { LearningProgress } from "../../learningTypes";

const COURSE_NAMES: Record<string, string> = {
  C_program: "C 语言",
  operating_systems: "操作系统",
  cybersec_lab: "网络安全实验",
};
const ACTION_NAMES: Record<string, string> = {
  content_drafted: "建立草稿",
  content_edited: "编辑草稿",
  content_approve: "审核通过",
  content_reject: "审核退回",
  content_publish: "发布",
  content_withdraw: "撤下",
};
const FIELD_NAMES: Record<string, string> = {
  frames: "内存页框",
  event: "访问结果",
  evicted: "换出页面",
  process: "进程",
  start: "开始时间",
  end: "结束时间",
  value: "答案",
  code: "代码",
  need: "尚需资源",
  work: "可用资源",
  verdict: "结论",
};
const CONTENT_STATUS = {
  draft: "草稿",
  approved: "审核通过",
  rejected: "审核退回",
  published: "已发布",
  withdrawn: "已撤下",
};

export type SchoolView = "classes" | "content" | "account" | "tasks" | "reports" | "grading" | "preparation";

export function SchoolWorkspace({
  user,
  view: tab,
  onDirtyChange,
  initialTaskId,
  onOpenAttempt,
}: {
  user: SchoolUser;
  view: SchoolView;
  onDirtyChange: (dirty: boolean) => void;
  initialTaskId?: string;
  onOpenAttempt: (id: string, taskId: string) => void;
}) {
  const teacher = user.role !== "student";
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [title, setTitle] = useState("");
  const [course, setCourse] = useState("operating_systems");
  const [joinCode, setJoinCode] = useState("");
  const [classId, setClassId] = useState(() => sessionStorage.getItem(`school-class:${user.id}`) || "");
  useEffect(() => {
    if (classId) sessionStorage.setItem(`school-class:${user.id}`, classId);
  }, [classId, user.id]);
  const [studentId, setStudentId] = useState("");
  const [studentProgress, setStudentProgress] =
    useState<LearningProgress | null>(null);
  const [studentAttempt, setStudentAttempt] = useState<SchoolAttempt | null>(
    null,
  );
  const [content, setContent] = useState<ContentDetail | null>(null);
  const [version, setVersion] = useState(0);
  const [source, setSource] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [trainingDraft, setTrainingDraft] = useState<Record<string, unknown>>(
    {},
  );
  const [templateId, setTemplateId] = useState("");
  const [conditionText, setConditionText] = useState("");
  const [frameCount, setFrameCount] = useState(3);
  const [quantum, setQuantum] = useState(2);

  function loadTraining(data: Record<string, unknown>) {
    setTrainingDraft(data);
    const p = data.parameters as {
      sequence?: number[];
      frames?: number;
      processes?: Array<{ name: string; arrival: number; service: number }>;
      quantum?: number;
    };
    setConditionText(
      p?.sequence?.join(", ") ||
        p?.processes
          ?.map((x) => `${x.name} ${x.arrival} ${x.service}`)
          .join("\n") ||
        "",
    );
    setFrameCount(p?.frames || 3);
    setQuantum(p?.quantum || 2);
  }

  const [answer, setAnswer] = useState("");
  const [steps, setSteps] = useState("");
  const [mistakes, setMistakes] = useState("");
  const [assumptions, setAssumptions] = useState("");
  const [knowledge, setKnowledge] = useState("");
  const [note, setNote] = useState("");
  const [filterCourse, setFilterCourse] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [teacherPassword, setTeacherPassword] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const classes = useQuery({
    queryKey: ["school-classes", user.id],
    queryFn: api.schoolClasses,
  });
  const members = useQuery({
    queryKey: ["school-members", classId],
    queryFn: () => api.schoolMembers(classId),
    enabled: teacher && Boolean(classId),
  });
  const items = useQuery({
    queryKey: ["school-content"],
    queryFn: api.schoolContent,
    enabled: teacher && tab === "content",
  });
  const selected = content?.versions.find((item) => item.version === version);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  function selectVersion(item: ContentVersion) {
    setVersion(item.version);
    setTemplateId("");
    loadTraining(item.data);
    setSource(item.source);
    setEditTitle(String(item.data.title || item.data.question || ""));
    setAnswer(String(item.data.answer || ""));
    setSteps(((item.data.solution_steps as string[]) || []).join("\n"));
    setMistakes(((item.data.common_mistakes as string[]) || []).join("\n"));
    setAssumptions(((item.data.assumptions as string[]) || []).join("\n"));
    setKnowledge(((item.data.knowledge_points as string[]) || []).join("\n"));
    setNote("");
  }

  async function openContent(key: string) {
    const detail = await api.contentDetail(key);
    setContent(detail);
    selectVersion(detail.versions[0]);
  }

  async function transition(action: string) {
    if (!selected) return;
    await api.contentAction(selected.item_key, selected.version, action, note);
    await openContent(selected.item_key);
    await queryClient.invalidateQueries({ queryKey: ["school-content"] });
    await queryClient.invalidateQueries({ queryKey: ["learning-courses"] });
    await queryClient.invalidateQueries({ queryKey: ["learning-exercises"] });
  }

  return (
    <main className="training-surface school-workspace">
      <header className="training-header">
        <h1>{{ classes: "班级管理", tasks: teacher ? "作业与测验" : "我的作业", reports: "班级学情", preparation: "备课安排", content: "题库审核", grading: "评测复核", account: "账号设置" }[tab]}</h1>
      </header>
      <div className="training-content school-content">
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        {["classes", "tasks", "reports", "preparation"].includes(tab) && <>
            {classes.isPending && <p role="status">正在读取班级…</p>}
            {classes.error && <p role="alert">{classes.error.message} <button className="training-link" disabled={classes.isFetching} onClick={() => classes.refetch()}>重新读取班级</button></p>}
        </>}
        {(tab === "tasks" || tab === "reports" || tab === "preparation") && classes.data && (
          <AssignmentWorkspace
            key={tab}
            user={user}
            onDirtyChange={onDirtyChange}
            classes={classes.data?.classes || []}
            selectedClassId={classId}
            onClassChange={setClassId}
            reports={tab === "reports"}
            preparation={tab === "preparation"}
            initialTaskId={initialTaskId}
            onOpenAttempt={onOpenAttempt}
          />
        )}
        {tab === "grading" && <GradingReviewWorkspace userId={user.id} />}
        {tab === "classes" && (
          <section>
            <h2>{teacher ? "我的授课班级" : "已加入班级"}</h2>
            <form
              className="training-filters"
              onSubmit={(event) => {
                event.preventDefault();
                run(async () => {
                  if (teacher) {
                    await api.createClass(title, course);
                    setTitle("");
                  } else {
                    await api.joinClass(joinCode);
                    setJoinCode("");
                  }
                  await classes.refetch();
                });
              }}
            >
              {teacher ? (
                <>
                  <label>
                    班级名称
                    <input
                      aria-label="班级名称"
                      required
                      maxLength={80}
                      value={title}
                      onChange={(event) => setTitle(event.target.value)}
                    />
                  </label>
                  <label>
                    课程
                    <select
                      aria-label="班级课程"
                      value={course}
                      onChange={(event) => setCourse(event.target.value)}
                    >
                      {Object.entries(COURSE_NAMES).map(([id, name]) => (
                        <option key={id} value={id}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </label>
                </>
              ) : (
                <label>
                  加入码
                  <input
                    aria-label="班级加入码"
                    required
                    maxLength={32}
                    value={joinCode}
                    onChange={(event) => setJoinCode(event.target.value)}
                  />
                </label>
              )}
              <button className="send-action" disabled={busy}>
                {teacher ? "创建班级" : "加入班级"}
              </button>
            </form>

            <div className="training-table-wrap">
              <table className="training-table">
                <thead>
                  <tr>
                    <th>班级</th>
                    <th>课程</th>
                    {teacher && <th>加入码</th>}
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {classes.data?.classes.map((item) => (
                    <tr key={item.id}>
                      <td>{item.title}</td>
                      <td>{COURSE_NAMES[item.course_id]}</td>
                      {teacher && <td>{item.join_code}</td>}
                      <td>
                        {teacher ? (
                          <>
                            <button
                              className="training-link"
                              disabled={busy}
                              onClick={() => {
                                setClassId(item.id);
                                setStudentProgress(null);
                                setStudentAttempt(null);
                              }}
                            >
                              查看成员
                            </button>
                            <button
                              className="training-link"
                              disabled={busy}
                              onClick={() =>
                                run(async () => {
                                  await api.rotateClassCode(item.id);
                                  await classes.refetch();
                                })
                              }
                            >
                              更新加入码
                            </button>
                          </>
                        ) : (
                          "在训练中心选择该班级即可记录课堂练习"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {teacher && classId && (
              <>
                <h3>班级成员</h3>
                {members.error && <p role="alert">{members.error.message}</p>}
                <div className="training-table-wrap">
                  <table className="training-table">
                    <thead>
                      <tr>
                        <th>姓名</th>
                        <th>用户名</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {members.data?.members.map((member) => (
                        <tr key={member.id}>
                          <td>{member.name}</td>
                          <td>{member.username}</td>
                          <td>
                            <button
                              className="training-link"
                              disabled={busy}
                              onClick={() =>
                                run(async () => {
                                  setStudentId(member.id);
                                  setStudentAttempt(null);
                                  setStudentProgress(
                                    await api.studentProgress(
                                      classId,
                                      member.id,
                                    ),
                                  );
                                })
                              }
                            >
                              查看班级记录
                            </button>
                            <button
                              className="training-link"
                              disabled={busy}
                              onClick={() =>
                                run(async () => {
                                  await api.removeMember(classId, member.id);
                                  await members.refetch();
                                  setStudentProgress(null);
                                  setStudentAttempt(null);
                                })
                              }
                            >
                              移出班级
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {studentProgress && (
                  <>
                    <h3>本班练习记录</h3>
                    <p>
                      仅包含提交到本班的记录；个人练习及其他班级记录不会显示。
                    </p>
                    <div className="training-table-wrap">
                      <table className="training-table">
                        <thead>
                          <tr>
                            <th>题目与版本</th>
                            <th>结果</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {studentProgress.records.map((record) => (
                            <tr key={record.id}>
                              <td>
                                {record.exercise.title} · v
                                {record.content_version}
                              </td>
                              <td>
                                {record.status === "passed"
                                  ? "已通过"
                                  : record.status === "needs_correction"
                                    ? "待订正"
                                    : "作答中"}
                              </td>
                              <td>
                                <button
                                  className="training-link"
                                  onClick={() =>
                                    run(async () =>
                                      setStudentAttempt(
                                        await api.studentAttempt(
                                          classId,
                                          studentId,
                                          record.id,
                                        ),
                                      ),
                                    )
                                  }
                                >
                                  查看作答证据
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
                {studentAttempt && (
                  <section>
                    <h3>
                      {studentAttempt.exercise.title} · v
                      {studentAttempt.content_version}
                    </h3>
                    <p>
                      首次错因：
                      {studentAttempt.first_error?.message || "未记录首错"}
                    </p>
                    <p>
                      提交 {studentAttempt.submission_count} 次，订正{" "}
                      {studentAttempt.correction_count} 次；提示{" "}
                      {studentAttempt.hint_count} 次，
                      {studentAttempt.solution_viewed
                        ? "已查看解析"
                        : "未查看解析"}
                      {studentAttempt.tutoring_viewed ? "，已打开问答辅导" : ""}
                      。
                    </p>
                    {[
                      {
                        label: "当前草稿",
                        rows: studentAttempt.draft,
                        evaluation: null,
                        created_at: studentAttempt.updated_at,
                        unassisted: null,
                      },
                      ...studentAttempt.submissions.map((submission, i) => ({
                        ...submission,
                        label: `第 ${i + 1} 次提交`,
                      })),
                    ].map((evidence, i) => (
                      <details key={i} open={i === 0}>
                        <summary>
                          {evidence.label} ·{" "}
                          {new Date(evidence.created_at * 1000).toLocaleString(
                            "zh-CN",
                          )}
                        </summary>
                        <p>
                          {i === 0
                            ? "草稿可能尚未提交，请以提交快照核对判定。"
                            : evidence.evaluation?.passed
                              ? "核验通过"
                              : evidence.evaluation?.first_error?.message ||
                                "核验未通过"}
                        </p>
                        {i > 0 && (
                          <p className="training-muted">
                            {evidence.unassisted
                              ? "本次提交未受提示、解析或已做同题的影响。"
                              : "本次提交已使用辅助或受已做同题的影响。"}
                          </p>
                        )}
                        <div className="training-table-wrap">
                          <table className="training-table">
                            <thead>
                              <tr>
                                <th>步骤</th>
                                <th>作答</th>
                              </tr>
                            </thead>
                            <tbody>
                              {evidence.rows.map((row, j) => (
                                <tr key={j}>
                                  <td>{j + 1}</td>
                                  <td>
                                    {Object.entries(row)
                                      .map(
                                        ([field, value]) =>
                                          `${FIELD_NAMES[field] || field}: ${value || "—"}`,
                                      )
                                      .join("；")}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </details>
                    ))}
                  </section>
                )}
              </>
            )}
          </section>
        )}
        {tab === "content" && teacher && (
          <section>
            <h2>题目审核与发布</h2>
            <p>
              训练定义与规范问答题均以草稿导入。教师核查题干、条件、参考解和提示后填写审核依据，再发布；学生只能开始已发布的训练。
            </p>
            {selected && (
              <section className="school-editor">
                <h3>
                  {selected.item_key} · v{selected.version} ·{" "}
                  {CONTENT_STATUS[selected.status]}
                </h3>
                <label>
                  查看版本
                  <select
                    aria-label="题目版本"
                    value={version}
                    onChange={(event) => {
                      const item = content?.versions.find(
                        (item) => item.version === Number(event.target.value),
                      );
                      if (item) selectVersion(item);
                    }}
                  >
                    {content?.versions.map((item) => (
                      <option key={item.version} value={item.version}>
                        v{item.version} · {CONTENT_STATUS[item.status]}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  {selected.category === "training" ? "标题" : "题干"}
                  <textarea
                    aria-label="审核题干"
                    value={editTitle}
                    disabled={busy || selected.status !== "draft"}
                    onChange={(event) => setEditTitle(event.target.value)}
                  />
                </label>
                {selected.category === "qa" ? (
                  <>
                    <label>
                      参考答案
                      <textarea
                        aria-label="参考答案"
                        value={answer}
                        disabled={busy || selected.status !== "draft"}
                        onChange={(event) => setAnswer(event.target.value)}
                      />
                    </label>
                    <label>
                      解题步骤（每行一项）
                      <textarea
                        value={steps}
                        disabled={busy || selected.status !== "draft"}
                        onChange={(event) => setSteps(event.target.value)}
                      />
                    </label>
                    <label>
                      易错点（每行一项）
                      <textarea
                        value={mistakes}
                        disabled={busy || selected.status !== "draft"}
                        onChange={(event) => setMistakes(event.target.value)}
                      />
                    </label>
                    <label>
                      适用条件（每行一项）
                      <textarea
                        aria-label="适用条件"
                        value={assumptions}
                        disabled={busy || selected.status !== "draft"}
                        onChange={(event) => setAssumptions(event.target.value)}
                      />
                    </label>
                    <label>
                      知识点（每行一项）
                      <textarea
                        aria-label="知识点"
                        value={knowledge}
                        disabled={busy || selected.status !== "draft"}
                        onChange={(event) => setKnowledge(event.target.value)}
                      />
                    </label>
                  </>
                ) : (
                  <>
                    <label>
                      核验规则与题目模板
                      <select
                        aria-label="核验规则与题目模板"
                        disabled={busy || selected.status !== "draft"}
                        value={templateId}
                        onChange={(e) => {
                          setTemplateId(e.target.value);
                          const template = content?.templates.find(
                            (x) => x.id === e.target.value,
                          );
                          loadTraining(
                            template
                              ? { ...template, id: selected.data.id }
                              : selected.data,
                          );
                          setEditTitle(
                            String((template || selected.data).title),
                          );
                        }}
                      >
                        <option value="">保持当前版本</option>
                        {content?.templates.map((t) => (
                          <option key={String(t.id)} value={String(t.id)}>
                            {String(t.title)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <p>{String(trainingDraft.rules || "")}</p>
                    <p className="training-muted">
                      模板同步更新条件、参考结果和判分规则。保存草稿后可核查新参考结果，发布后才对新练习生效。
                    </p>
                    {trainingDraft.kind === "page_replacement" && (
                      <label>
                        页框数
                        <input
                          type="number"
                          aria-label="页框数"
                          min={1}
                          max={8}
                          value={frameCount}
                          disabled={busy || selected.status !== "draft"}
                          onChange={(e) =>
                            setFrameCount(Number(e.target.value))
                          }
                        />
                      </label>
                    )}
                    {["page_replacement", "cpu_scheduling"].includes(
                      String(trainingDraft.kind),
                    ) && (
                      <label>
                        {trainingDraft.kind === "page_replacement"
                          ? "页面访问序列（逗号分隔）"
                          : "进程条件（每行：名称 到达时间 服务时间）"}
                        <textarea
                          aria-label="训练条件"
                          value={conditionText}
                          disabled={busy || selected.status !== "draft"}
                          onChange={(e) => setConditionText(e.target.value)}
                        />
                      </label>
                    )}
                    {trainingDraft.algorithm === "RR" && (
                      <label>
                        时间片
                        <input
                          type="number"
                          aria-label="时间片"
                          min={1}
                          max={20}
                          value={quantum}
                          disabled={busy || selected.status !== "draft"}
                          onChange={(e) => setQuantum(Number(e.target.value))}
                        />
                      </label>
                    )}
                    <details>
                      <summary>题目条件</summary>
                      <pre className="training-code">
                        {JSON.stringify(trainingDraft.parameters, null, 2)}
                      </pre>
                    </details>
                    <details>
                      <summary>参考核验结果</summary>
                      <pre className="training-code">
                        {JSON.stringify(
                          content?.solutions[String(selected.version)],
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  </>
                )}
                <label>
                  来源与核查材料
                  <textarea
                    aria-label="题目来源"
                    value={source}
                    maxLength={1000}
                    disabled={busy || selected.status !== "draft"}
                    onChange={(event) => setSource(event.target.value)}
                  />
                </label>
                <div className="training-actions">
                  <button
                    className="send-action"
                    disabled={busy || selected.status !== "draft"}
                    onClick={() =>
                      run(async () => {
                        const data = {
                          ...(selected.category === "training"
                            ? trainingDraft
                            : selected.data),
                        };
                        if (selected.category === "training") {
                          data.title = editTitle;
                          if (
                            data.kind === "page_replacement" &&
                            !/^\d+(?:[,，\s]+\d+)*$/.test(conditionText.trim())
                          )
                            throw new Error(
                              "请填写用逗号或空格分隔的非负整数序列",
                            );
                          if (data.kind === "page_replacement")
                            data.parameters = {
                              ...(data.parameters as object),
                              frames: frameCount,
                              sequence: conditionText
                                .trim()
                                .split(/[,，\s]+/)
                                .map(Number),
                            };
                          if (data.kind === "cpu_scheduling")
                            data.parameters = {
                              ...(data.parameters as object),
                              quantum: data.algorithm === "RR" ? quantum : null,
                              processes: conditionText
                                .trim()
                                .split(/\n+/)
                                .map((line) => {
                                  const values = line.trim().split(/[,，\s]+/);
                                  if (values.length !== 3)
                                    throw new Error(
                                      "每行须填写名称、到达时间和服务时间",
                                    );
                                  return {
                                    name: values[0],
                                    arrival: Number(values[1]),
                                    service: Number(values[2]),
                                  };
                                }),
                            };
                        } else {
                          data.question = editTitle;
                          data.answer = answer;
                          data.solution_steps = steps
                            .split("\n")
                            .filter(Boolean);
                          data.common_mistakes = mistakes
                            .split("\n")
                            .filter(Boolean);
                        }
                        if (selected.category === "qa") {
                          data.assumptions = assumptions
                            .split("\n")
                            .filter(Boolean);
                          data.knowledge_points = knowledge
                            .split("\n")
                            .filter(Boolean);
                        }
                        await api.saveContent(
                          selected.item_key,
                          selected.version,
                          data,
                          source,
                        );
                        await openContent(selected.item_key);
                        await items.refetch();
                        setMessage("草稿已保存");
                      })
                    }
                  >
                    保存草稿
                  </button>
                  <button
                    className="ghost-action"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.newContentVersion(selected.item_key);
                        await openContent(selected.item_key);
                        await items.refetch();
                      })
                    }
                  >
                    建立新版本
                  </button>
                  <button
                    className="training-link"
                    disabled={busy}
                    onClick={() => setContent(null)}
                  >
                    返回列表
                  </button>
                </div>
                <label>
                  审核或发布依据
                  <textarea
                    aria-label="审核依据"
                    value={note}
                    maxLength={2000}
                    onChange={(event) => setNote(event.target.value)}
                  />
                </label>
                <div className="training-actions">
                  {selected.status === "draft" && (
                    <>
                      <button
                        className="ghost-action"
                        disabled={busy || !note.trim()}
                        onClick={() => run(() => transition("approve"))}
                      >
                        审核通过
                      </button>
                      <button
                        className="training-link"
                        disabled={busy || !note.trim()}
                        onClick={() => run(() => transition("reject"))}
                      >
                        审核退回
                      </button>
                    </>
                  )}
                  {selected.status === "approved" && (
                    <button
                      className="send-action"
                      disabled={busy || !note.trim()}
                      onClick={() => run(() => transition("publish"))}
                    >
                      发布此版本
                    </button>
                  )}
                  {selected.status === "published" && (
                    <button
                      className="training-link"
                      disabled={busy || !note.trim()}
                      onClick={() => run(() => transition("withdraw"))}
                    >
                      撤下此版本
                    </button>
                  )}
                </div>
                <h3>操作记录</h3>
                <ul>
                  {content?.audit.map((entry) => (
                    <li key={entry.id}>
                      {new Date(entry.created_at * 1000).toLocaleString(
                        "zh-CN",
                      )}{" "}
                      · {entry.actor_name || "未知用户"} ·{" "}
                      {ACTION_NAMES[entry.action] || entry.action} ·{" "}
                      {entry.detail.startsWith("{")
                        ? `v${JSON.parse(entry.detail).version} · ${JSON.parse(entry.detail).note}`
                        : `v${entry.detail}`}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {!selected && (
              <>
                <div className="training-filters">
                  <label>
                    课程
                    <select
                      value={filterCourse}
                      onChange={(event) => setFilterCourse(event.target.value)}
                    >
                      <option value="">全部课程</option>
                      {Object.entries(COURSE_NAMES).map(([id, name]) => (
                        <option key={id} value={id}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    状态
                    <select
                      value={filterStatus}
                      onChange={(event) => setFilterStatus(event.target.value)}
                    >
                      <option value="">全部状态</option>
                      {Object.entries(CONTENT_STATUS).map(([id, name]) => (
                        <option key={id} value={id}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {items.error && <p role="alert">{items.error.message}</p>}
                <div className="training-table-wrap">
                  <table className="training-table">
                    <thead>
                      <tr>
                        <th>题目</th>
                        <th>课程 / 用途</th>
                        <th>版本 / 状态</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.data?.items
                        .filter(
                          (item) =>
                            (!filterCourse ||
                              item.data.subject_id === filterCourse) &&
                            (!filterStatus || item.status === filterStatus),
                        )
                        .map((item) => (
                          <tr key={item.item_key}>
                            <td>
                              {String(item.data.title || item.data.question)}
                            </td>
                            <td>
                              {COURSE_NAMES[String(item.data.subject_id)]} /{" "}
                              {item.category === "training"
                                ? "结构化训练"
                                : "问答参考题"}
                            </td>
                            <td>
                              v{item.version} · {CONTENT_STATUS[item.status]}
                            </td>
                            <td>
                              <button
                                className="training-link"
                                disabled={busy}
                                onClick={() =>
                                  run(() => openContent(item.item_key))
                                }
                              >
                                查看与审核
                              </button>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>
        )}
        {tab === "account" && (
          <section className="school-editor">
            <h2>账号设置</h2>
            <p>
              {user.name} · {user.username}
            </p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                run(async () => {
                  await api.changePassword(oldPassword, password);
                  setOldPassword("");
                  setPassword("");
                  window.dispatchEvent(new Event("gm-session-expired"));
                  localStorage.setItem(
                    "gm.identity.change",
                    String(Date.now()),
                  );
                });
              }}
            >
              <label>
                原密码
                <input
                  type="password"
                  autoComplete="current-password"
                  value={oldPassword}
                  required
                  onChange={(event) => setOldPassword(event.target.value)}
                />
              </label>
              <label>
                新密码
                <input
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  minLength={8}
                  maxLength={128}
                  required
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
              <button className="send-action" disabled={busy}>
                修改密码并重新登录
              </button>
            </form>
            {user.role === "admin" && (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  run(async () => {
                    await api.createTeacher({
                      username,
                      name,
                      password: teacherPassword,
                    });
                    setTeacherPassword("");
                    setMessage(
                      "教师账号已创建，请由教师使用该账号登录并修改密码",
                    );
                  });
                }}
              >
                <h3>创建教师账号</h3>
                <label>
                  教师用户名
                  <input
                    aria-label="教师用户名"
                    value={username}
                    required
                    maxLength={32}
                    onChange={(event) => setUsername(event.target.value)}
                  />
                </label>
                <label>
                  教师姓名
                  <input
                    aria-label="教师姓名"
                    value={name}
                    required
                    maxLength={40}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
                <label>
                  初始密码
                  <input
                    aria-label="教师初始密码"
                    type="password"
                    autoComplete="new-password"
                    value={teacherPassword}
                    minLength={8}
                    maxLength={128}
                    required
                    onChange={(event) => setTeacherPassword(event.target.value)}
                  />
                </label>
                <button className="ghost-action" disabled={busy}>
                  创建教师账号
                </button>
              </form>
            )}
          </section>
        )}
      </div>
    </main>
  );
}
