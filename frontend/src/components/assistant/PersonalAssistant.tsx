import { useDiscardChanges } from "../shared/useDiscardChanges";
import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { MarkdownMessage } from "../shared/MarkdownMessage";
import { ExamPlanPanel, type ExamOverview } from "./ExamPlanPanel";
import mascot from "../../assets/assistant-mascot.png";
import { AssignmentWorkspace } from "../school/AssignmentWorkspace";
import type { SchoolUser, LessonPlan } from "../../schoolTypes";

const ROLE_CONTENT = {
  teacher: {
    heading: "今天要推进哪项教学工作？",
    description: "告诉我授课班级、章节和教学目标，我会结合授权的班级记录协助备课与讲评。",
    suggestions: ["帮我分析授课班级的常见错误，先确认班级和章节。", "帮我安排一节45分钟的讲评课，先确认班级和章节。", "给已保存的备课安排配一组课后补练。"],
    placeholder: "例如：给一班安排一节45分钟的操作系统讲评课…",
  },
  student: {
    heading: "今天想学会什么？",
    description: "告诉我正在学的课程、遇到的困难和可用时间，我们一起确定下一步。",
    suggestions: ["帮我安排考前复习，先问我需要哪些信息。", "根据我的练习记录，看看下一步该学什么。", "我今天只有二十分钟，帮我安排一下。"],
    placeholder: "例如：下周考操作系统，每天能复习半小时…",
  },
  admin: {
    heading: "今天需要处理哪项管理工作？",
    description: "告诉我管理目标或遇到的问题，我会帮助梳理处理步骤和需要核对的信息。",
    suggestions: ["帮我梳理新建班级和创建教师账号的流程。", "题库发布前需要核对什么？", "评测结果有争议时，应该如何复核？"],
    placeholder: "例如：帮我梳理题库审核和发布的步骤…",
  },
};

interface TeacherResult {
  request_id: string;
  class_id: string;
  class_title: string;
  title: string;
  generated_at: number;
  kind: "report" | "lesson" | "homework";
  status?: "running" | "completed" | "failed";
  error?: string;
  chapter_id?: string;
  plan?: LessonPlan;
}

function AssistantMascot({ animated = false }: { animated?: boolean }) {
  return <img className={`assistant-mascot${animated ? " is-floating" : ""}`} src={mascot} alt="" aria-hidden="true" width={48} height={48} />;
}

export interface AssistantView {
  name?: string;
  teacher_results?: TeacherResult[];
  enabled: boolean;
  version: number;
  memory_status: "ready" | "updating" | "paused" | "failed";
  memory_error: string | null;
  messages: { id: string; role: string; content: string; status: string; created: number }[];
  memories: { id: string; content: string; temporal?: string; sources: { id: string; text: string; timestamp?: string; timestamp_label?: string | null }[] }[];
}

export function PersonalAssistant({ userId, user, active, onDirtyChange, onOpen, onOpenAttempt }: {
  userId: string;
  user: SchoolUser;
  active: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onOpenAttempt: (id: string) => void;
  onOpen: (page: "path" | "training" | "school") => void;
}) {
  const { confirmDiscard, discardDialog } = useDiscardChanges();
  const queryClient = useQueryClient();
  const queryKey = ["personal-assistant", userId];
  const [view, setView] = useState<"conversation" | "plan">("conversation");
  const [draft, setDraft] = useState("");
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const [busy, setBusy] = useState(false);
  const query = useQuery({ queryKey, queryFn: api.assistant, enabled: active, refetchInterval: active ? (busy ? 1000 : 5000) : false });
  const [error, setError] = useState("");
  const [showMemory, setShowMemory] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [assistantName, setAssistantName] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [teachingResult, setTeachingResult] = useState<TeacherResult | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  useEffect(() => { onDirtyChange(editorDirty); return () => onDirtyChange(false); }, [editorDirty, onDirtyChange]);
  const teacher = user.role === "teacher";
  const student = user.role === "student";
  const content = ROLE_CONTENT[user.role];
  useEffect(() => {
    const input = composerInput.current;
    if (!input || !active || editorOpen || (student && view !== "conversation")) return;
    const resize = () => {
      input.style.height = "auto";
      input.style.height = `${input.scrollHeight}px`;
    };
    resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [draft, active, editorOpen, student, view]);
  const classes = useQuery({ queryKey: ["school-classes", userId], queryFn: api.schoolClasses, enabled: teacher && active });
  const plans = useQuery<ExamOverview>({ queryKey: ["exam-plans", userId], queryFn: api.examPlans, enabled: student && active, refetchInterval: student && active ? 15000 : false });
  const currentPlan = plans.data?.plans.find(plan => plan.status === "active" && !plan.expired);
  const todayTasks = currentPlan?.days.find(day => day.date === plans.data?.today)?.tasks || [];
  const data = query.data;
  useEffect(() => {
    if (data && teachingResult && !data.teacher_results?.some(r => r.request_id === teachingResult.request_id)) { setTeachingResult(null); setEditorOpen(false); }
  }, [data, teachingResult]);
  async function action(work: () => Promise<AssistantView>) {
    setBusy(true); setError("");
    try { queryClient.setQueryData(queryKey, await work()); }
    catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); await queryClient.invalidateQueries({ queryKey }); if (teacher) await queryClient.invalidateQueries({ queryKey: ["lesson-plans", userId] }); if (student) await queryClient.invalidateQueries({ queryKey: ["exam-plans", userId] }); }
  }
  async function send(content: string, requestId: string = crypto.randomUUID()) {
    setDraft("");
    await action(() => api.assistantMessage(content, requestId));
  }
  function renderMessages(messages: AssistantView["messages"]) { return messages.map(message => <article key={message.id} className={`assistant-message ${message.role}`}>
          <MarkdownMessage content={message.content} />
          {message.role === "user" && (message.status === "failed" || (message.status === "pending" && Date.now() / 1000 - message.created >= 180)) && <button disabled={busy} className="ghost-action" onClick={() => void send(message.content, message.id)}>重试回复</button>}
        </article>); }
  const status = { ready: "已更新", updating: "后台更新中", paused: "自动记忆已关闭", failed: "更新失败" };
  return <>
  {discardDialog}
  <main className="personal-assistant" hidden={editorOpen}>
    <div className="workspace-page-actions assistant-topbar">
      <div className="assistant-companion">
        <AssistantMascot animated={busy} />
        {renaming ? <form className="assistant-name-editor" onSubmit={async e => {
          e.preventDefault();
          if (savingName || !assistantName.trim()) return;
          setSavingName(true); setError("");
          try {
            await api.renameAssistant(assistantName.trim());
            await queryClient.invalidateQueries({ queryKey });
            setRenaming(false);
          } catch (e) { setError(e instanceof Error ? e.message : "改名失败，请重试"); }
          finally { setSavingName(false); }
        }}>
          <input autoFocus aria-label="助理名字" value={assistantName} maxLength={24} disabled={savingName} onChange={e => setAssistantName(e.target.value)} onKeyDown={e => { if (e.key === "Escape" && !savingName) setRenaming(false); }} />
          <button className="training-link" disabled={savingName || !assistantName.trim()}>{savingName ? "保存中…" : "保存"}</button>
          <button type="button" className="training-link" disabled={savingName} onClick={() => setRenaming(false)}>取消</button>
        </form> : <button className="assistant-name-label" title="给助理改名" aria-label={`给${data?.name || "个人助理"}改名`} onClick={() => { setAssistantName(data?.name || "个人助理"); setRenaming(true); }}>
          {data?.name || "个人助理"}
        </button>}
      </div>
      <button className="ghost-action" onClick={() => setShowMemory(!showMemory)}>助理记忆 · {data ? status[data.memory_status] : "加载中"}</button>
    </div>
    {(error || query.error) && <p className="workspace-feedback" role="alert">{error || String(query.error)}{query.error && <button className="training-link" disabled={query.isFetching} onClick={() => query.refetch()}>重新读取对话</button>}</p>}
    {student && <nav className="training-tabs" aria-label="助理视图">
      <button className={view === "conversation" ? "selected" : ""} onClick={() => setView("conversation")}>对话</button>
      <button className={view === "plan" ? "selected" : ""} onClick={() => setView("plan")}>复习计划</button>
    </nav>}
    <div className="assistant-body">
      <section className="assistant-conversation" aria-label="个人助理对话" hidden={student && view !== "conversation"}>
        {student && <div className="assistant-next">
          <div><strong>{currentPlan ? "今日复习" : "学习安排"}</strong><p>{currentPlan ? (todayTasks.length ? `${todayTasks.length} 项待做 · 预计 ${todayTasks.reduce((total, task) => total + task.minutes, 0)} 分钟 · ${todayTasks[0].title}` : "今天没有待做任务，可查看后续安排。") : plans.isPending ? "正在读取复习安排…" : plans.isError ? "复习安排暂时无法读取，请进入计划页重试。" : "查看复习草稿，或按考试日期安排学习。"}</p></div>
          <button className="ghost-action" onClick={() => setView("plan")}>{todayTasks.length ? "继续今日任务" : "查看复习计划"}</button>
        </div>}
        {query.isPending && <p role="status">正在读取助理对话…</p>}
        {data && !data.messages.length && <div className="assistant-welcome"><h2>{content.heading}</h2><p>{content.description}</p>
          {content.suggestions.map(text => <button className="ghost-action" key={text} disabled={busy} onClick={() => setDraft(text)}>{text}</button>)}
        </div>}
        {!!data && data.messages.length > 6 && <details className="assistant-history"><summary>更早的对话 · {data.messages.length - 6} 条</summary>{renderMessages(data.messages.slice(0, -6))}</details>}
        {renderMessages(data?.messages.slice(-6) || [])}
        {busy && !data?.teacher_results?.some(r => r.status === "running") && <div className="assistant-working" role="status"><AssistantMascot animated /><span>正在处理…</span></div>}
        {teacher && <section aria-label="教学对话结果">
          {data?.teacher_results?.map(result => <article className="assistant-message assistant" key={result.request_id}>
            <strong>{result.status === "running" && <span className="assistant-task-indicator" aria-hidden="true" />}{result.class_title} · {result.title}{result.status === "running" ? (result.kind === "report" ? " · 分析中" : " · 生成中") : result.status === "failed" ? " · 未完成" : result.status === "completed" || result.kind === "lesson" ? " · 已完成" : ""}</strong>
            {result.status === "running" ? <p role="status">{result.kind === "report" ? "学情分析任务已启动，正在核对本班任务、作答记录和常见错误证据。" : "备课任务已启动，正在结合课程资料、班级学情和教学要求编写草案。"}</p> : result.status === "failed" ? <p role="alert">{result.error}</p> : <>
            <button className="ghost-action" disabled={busy || !classes.data} onClick={() => {
              if (!classes.data?.classes.some(c => c.id === result.class_id)) { setError("当前已无该班级访问权限"); return; }
              if (teachingResult?.request_id !== result.request_id) {
                if (editorDirty) { confirmDiscard(() => { setTeachingResult(result); setEditorOpen(true); }); return; }
                setTeachingResult(result);
              }
              setEditorOpen(true);
            }}>{result.kind === "report" ? "查看学情与证据" : result.kind === "lesson" ? "查看备课草案" : "核对补练并填写草稿"}</button>
            </>}
          </article>)}
        </section>}
      </section>
      {student && <section className="assistant-plan" hidden={view !== "plan"}><ExamPlanPanel userId={userId} active={active && view === "plan"} onOpenAttempt={onOpenAttempt} /></section>}
      {showMemory && <aside className="assistant-memory" aria-label="助理记忆">
        <h2>助理记忆</h2><p>{data ? status[data.memory_status] : "加载中"}</p>
        <p>记忆在后台整理，不影响本轮对话。时间范围和来源会保留，推测不代表已确认的事实。</p>
        <label><input type="checkbox" checked={data?.enabled ?? false} disabled={busy || !data} onChange={e => void action(() => api.assistantMemory(e.target.checked))} />自动整理个人记忆</label>
        <p>关闭后停止写入和读取长期记忆；当前聊天仍保留。</p>
        {data?.memory_error && <p role="status">{data.memory_error}</p>}
        {data?.memory_status === "failed" && <button className="ghost-action" disabled={busy} onClick={() => void action(api.assistantMemoryRetry)}>重试更新</button>}
        {data && !data.memories.length && <p>还没有整理好的记忆。整理完成后会显示在这里。</p>}
        {data?.memories.map(memory => <article key={memory.id}>
          <p>{memory.content}</p>{memory.temporal && <small>{memory.temporal}</small>}
          <details><summary>查看来源</summary>{memory.sources.map(source => <p key={source.id}>{source.timestamp_label && <small>{source.timestamp_label}<br /></small>}{source.text}</p>)}</details>
          <button className="ghost-action" disabled={busy} onClick={() => {
            if (window.confirm("删除此记忆及其来源记录，并清空当前助理对话，避免旧上下文再次带回它。同一来源产生的其他记忆也会移除。")) void action(() => api.assistantForget(memory.id));
          }}>删除来源与记忆</button>
        </article>)}
        <button className="ghost-action" disabled={busy || !data} onClick={() => {
          if (window.confirm("清空所有助理记忆及对话？课程问答、复习计划、练习和成绩记录不会删除。")) void action(() => api.assistantForget());
        }}>清空记忆与助理对话</button>
        <p>纠正记忆可以直接在对话中说明；后台会保留变化的时间与依据。</p>
      </aside>}
    </div>
    <footer className="assistant-composer" hidden={student && view !== "conversation"}>
      {student && <div className="assistant-shortcuts"><button className="training-link" onClick={() => onOpen("path")}>课程进度与学习计划</button><button className="training-link" onClick={() => onOpen("training")}>去练习</button><button className="training-link" onClick={() => onOpen("school")}>班级与任务</button></div>}
      <form className="composer-input" onSubmit={e => { e.preventDefault(); if (draft.trim() && !busy) void send(draft.trim()); }}>
        <textarea ref={composerInput} rows={1} aria-label="给个人助理的消息" value={draft} maxLength={4000} onChange={e => setDraft(e.target.value)} onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (draft.trim() && !busy) void send(draft.trim());
          }
        }} placeholder={content.placeholder} />
        <button className="send-action" aria-label="发送" title="发送" disabled={busy || !draft.trim()}><ArrowUp size={20} aria-hidden="true" /></button>
      </form>
    </footer>
  </main>
  {teachingResult && <main className="training-surface teacher-result-surface" hidden={!editorOpen}>
    <div className="workspace-page-actions">
      <button className="training-link" onClick={() => setEditorOpen(false)}>返回个人助理</button>
      <span>{teachingResult.class_title}{editorDirty ? " · 有未保存修改" : ""}</span>
    </div>
    <div className="training-content school-content">
      <AssignmentWorkspace key={teachingResult.request_id} user={user}
        classes={classes.data?.classes.filter(c => c.id === teachingResult.class_id) || []}
        reports={teachingResult.kind === "report"} preparation={teachingResult.kind === "lesson"}
        initialTeaching={teachingResult} onOpenAttempt={onOpenAttempt} onDirtyChange={setEditorDirty} />
    </div>
  </main>}
  </>;
}
