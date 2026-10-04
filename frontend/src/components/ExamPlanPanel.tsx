import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { MarkdownMessage } from "./MarkdownMessage";
import type { ChapterMaterial } from "../learningTypes";

export interface ExamTask {
  id: string; kind: "reading" | "practice"; chapter_id: string; title: string;
  minutes: number; status: string; reason: string; detail?: string; attempt_id?: string;
}
export interface ExamPlan {
  id: string; status: "draft" | "active"; revision: number; subject_id: string;
  exam_date: string; minutes: number; chapters: string[]; summary: string; expired: boolean;
  days: { date: string; budget: number; used: number; completed_minutes?: number; tasks: ExamTask[] }[];
  completed: ExamTask[]; blocked: ExamTask[]; overflow: ExamTask[];
  gaps: string[]; covered: { title: string; reason: string }[];
  budget_proposal?: { date: string; minutes: number } | null;
}
export interface ExamOverview {
  today: string; plans: ExamPlan[];
  courses: { id: string; name: string; chapters: { id: string; title: string }[] }[];
}

export function ExamPlanPanel({ userId, onOpenAttempt }: { userId: string; onOpenAttempt: (id: string) => void }) {
  const client = useQueryClient();
  const queryKey = ["exam-plans", userId];
  const query = useQuery({ queryKey, queryFn: api.examPlans, refetchInterval: 15000 });
  const [subject, setSubject] = useState("operating_systems");
  const [chapters, setChapters] = useState<string[]>([]);
  const [examDate, setExamDate] = useState("");
  const [minutes, setMinutes] = useState(30);
  const [todayMinutes, setTodayMinutes] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reader, setReader] = useState<ChapterMaterial | null>(null);
  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); }
    catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); await client.invalidateQueries({ queryKey }); await client.invalidateQueries({queryKey:["personal-assistant",userId]}); }
  }
  function renderTask(plan: ExamPlan, task: ExamTask) {
    return <li key={task.id} className="exam-task">
      <div><strong>{task.title}</strong><small>预计 {task.minutes} 分钟 · {task.kind === "reading" ? "资料阅读" : "独立练习"}</small><p>{task.detail || task.reason}</p></div>
      <button className="ghost-action" disabled={busy || plan.status !== "active" || plan.expired} onClick={() => void run(async () => {
        if (task.kind === "reading") setReader(await api.chapterMaterial(task.chapter_id));
        else onOpenAttempt((await api.examStartTask(plan.id, task.id)).id);
      })}>{task.kind === "reading" ? "打开资料" : task.status === "needs_retest" ? "开始新题复测" : task.attempt_id ? "继续作答" : "开始练习"}</button>
    </li>;
  }
  return <section className="exam-panel" aria-label="考前复习计划">
    <header><h2>考前复习</h2><p>确认目标后，把每天的复习变成可以完成的小任务。</p></header>
    {(error || query.error) && <p role="alert">{error || String(query.error)}</p>}
    <details className="exam-goal-form" open={!query.data?.plans.length}>
      <summary>制定新的考试目标</summary>
      <form onSubmit={e => {e.preventDefault(); void run(() => api.examDraft({subject_id:subject,chapters,exam_date:examDate,minutes}));}}>
        <div className="exam-fields">
          <label>课程<select value={subject} onChange={e => {setSubject(e.target.value);setChapters([]);}}>{query.data?.courses.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          <label>考试日期<input type="date" required value={examDate} min={query.data?.today} onChange={e => setExamDate(e.target.value)} /></label>
          <label>每天可用时间（分钟）<input type="number" min={15} max={120} required value={minutes} onChange={e => setMinutes(Number(e.target.value))} /></label>
        </div>
        <fieldset><legend>考试章节范围</legend><div className="exam-chapters">{query.data?.courses.find(c=>c.id===subject)?.chapters.map(c => <label key={c.id}><input type="checkbox" checked={chapters.includes(c.id)} onChange={e => setChapters(e.target.checked ? [...chapters,c.id] : chapters.filter(id=>id!==c.id))} />{c.title}</label>)}</div></fieldset>
        <button className="ghost-action" disabled={busy || !chapters.length || !examDate}>生成草稿</button>
        <small>考试前最多安排90天；采用新计划后，原计划归档，作答记录保留。</small>
      </form>
    </details>
    {query.data?.plans.map(plan => <article className="exam-plan-card" key={plan.id}>
      <header><strong>{plan.status === "draft" ? "待确认草稿" : "正在执行"} · {query.data.courses.find(c=>c.id===plan.subject_id)?.name}</strong><p>{plan.exam_date} 考试 · 默认每天 {plan.minutes} 分钟 · 上海时间</p>
        <p>范围：{plan.chapters.map(id=>query.data.courses.find(c=>c.id===plan.subject_id)?.chapters.find(c=>c.id===id)?.title || id).join("、")}</p><p>{plan.summary}</p>
      </header>
      {plan.expired && <p role="status">考试日期已到，停止安排新任务。你可以创建下一份计划。</p>}
      {plan.status === "draft" ? <button className="ghost-action exam-primary" disabled={busy || plan.expired} onClick={() => void run(() => api.examAdopt(plan.id,plan.revision))}>采用计划{query.data.plans.some(p=>p.status==="active") ? "并替换原计划" : ""}</button> : !plan.expired && <div className="exam-budget">
        <label>今天可用时间<input aria-label="今天可用时间" type="number" min={0} max={120} value={todayMinutes ?? plan.days[0]?.budget ?? plan.minutes} onChange={e=>setTodayMinutes(Number(e.target.value))} /></label>
        <button className="ghost-action" disabled={busy} onClick={()=>void run(()=>api.examToday(plan.id,plan.revision,todayMinutes ?? plan.days[0]?.budget ?? plan.minutes))}>仅调整今天</button>
        <button className="ghost-action" disabled={busy} onClick={()=>void run(()=>api.examToday(plan.id,plan.revision,0))}>今天休息</button>
      </div>}
      {plan.budget_proposal && <div className="exam-proposal"><p>助理建议今天安排 {plan.budget_proposal.minutes} 分钟，尚未生效。</p><button className="ghost-action" disabled={busy} onClick={()=>void run(()=>api.examToday(plan.id,plan.revision,plan.budget_proposal!.minutes))}>确认今日调整</button></div>}
      {plan.days[0] && <section className="exam-today"><h3>今天 · {plan.days[0].date}</h3><p>可用 {plan.days[0].budget} 分钟 · 待做预计 {plan.days[0].tasks.reduce((n,t)=>n+t.minutes,0)} 分钟{plan.days[0].completed_minutes ? ` · 已完成任务预计 ${plan.days[0].completed_minutes} 分钟` : ""}</p>
        {!plan.days[0].tasks.length && <p>{plan.days[0].budget===0 ? "今天休息，未完成任务已尝试顺延。" : "今天没有待做任务，已完成的进展会保留。"}</p>}
        <ul>{plan.days[0].tasks.map(task=>renderTask(plan,task))}</ul>
      </section>}
      <details><summary>后续安排</summary>{plan.days.slice(1).filter(day=>day.tasks.length).map(day=><section key={day.date}><h3>{day.date} · 预计 {day.used} / {day.budget} 分钟</h3><ul>{day.tasks.map(task=>renderTask(plan,task))}</ul></section>)}</details>
      {!!plan.completed.length && <details><summary>已完成 {plan.completed.length} 项</summary><ul>{plan.completed.map(t=><li key={t.id}>{t.title} · {t.kind==="reading" ? "已标记阅读" : "已有独立通过证据"}</li>)}</ul></details>}
      {!!plan.covered.length && <details><summary>减少重复练习的内容</summary>{plan.covered.map((c,i)=><p key={i}>{c.title}：{c.reason}</p>)}</details>}
      {(!!plan.gaps.length || !!plan.blocked.length || !!plan.overflow.length) && <div className="exam-gaps"><strong>需要留意</strong><ul>{plan.gaps.map((g,i)=><li key={i}>{g}</li>)}{plan.blocked.map(t=><li key={t.id}>{t.title}：{t.detail}</li>)}{plan.overflow.map(t=><li key={t.id}>{t.title}：剩余时间不足，尚未排入考试前日程。</li>)}</ul></div>}
    </article>)}
    {reader && <section className="exam-reader" aria-label="复习资料"><header><h3>{reader.title}</h3><button className="ghost-action" onClick={()=>setReader(null)}>收起资料</button></header><MarkdownMessage content={reader.text} /><p>第 {reader.start_line}—{reader.end_line} 行 / 共 {reader.total_lines} 行</p><div className="exam-budget">
      <button className="ghost-action" disabled={busy || reader.start_line<=1} onClick={()=>void run(async()=>setReader(await api.chapterMaterial(reader.id,Math.max(1,reader.start_line-120))))}>上一段</button>
      <button className="ghost-action" disabled={busy || reader.end_line>=reader.total_lines} onClick={()=>void run(async()=>setReader(await api.chapterMaterial(reader.id,reader.end_line+1)))}>下一段</button>
      <button className="ghost-action" disabled={busy} onClick={()=>void run(()=>api.markReading(reader.id,true))}>标记本章已读</button>
    </div><small>阅读标记用于任务进度；知识掌握仍以独立练习为依据。</small></section>}
  </section>;
}
