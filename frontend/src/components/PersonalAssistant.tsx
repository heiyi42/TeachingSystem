import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { MarkdownMessage } from "./MarkdownMessage";
import { ExamPlanPanel } from "./ExamPlanPanel";
import mascot from "../assets/assistant-mascot.png";

function AssistantMascot({ variant = "avatar", animated = false }: { variant?: "avatar" | "welcome"; animated?: boolean }) {
  return <img className={`assistant-mascot assistant-mascot--${variant}${animated ? " is-floating" : ""}`} src={mascot} alt="" aria-hidden="true" width={variant === "welcome" ? 180 : 48} height={variant === "welcome" ? 180 : 48} />;
}

export interface AssistantView {
  enabled: boolean;
  version: number;
  memory_status: "ready" | "updating" | "paused" | "failed";
  memory_error: string | null;
  messages: { id: string; role: string; content: string; status: string; created: number }[];
  memories: { id: string; content: string; temporal?: string; sources: { id: string; text: string; timestamp?: string }[] }[];
}

export function PersonalAssistant({ userId, onOpen, onOpenAttempt }: {
  userId: string;
  onOpenAttempt: (id: string) => void;
  onOpen: (page: "path" | "training" | "school") => void;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["personal-assistant", userId];
  const query = useQuery({ queryKey, queryFn: api.assistant, refetchInterval: 5000 });
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showMemory, setShowMemory] = useState(false);
  const data = query.data;
  async function action(work: () => Promise<AssistantView>) {
    setBusy(true); setError("");
    try { queryClient.setQueryData(queryKey, await work()); }
    catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); await queryClient.invalidateQueries({ queryKey }); await queryClient.invalidateQueries({ queryKey: ["exam-plans", userId] }); }
  }
  async function send(content: string, requestId: string = crypto.randomUUID()) {
    await action(async () => {
      const result = await api.assistantMessage(content, requestId);
      setDraft("");
      return result;
    });
  }
  const status = { ready: "已更新", updating: "后台更新中", paused: "自动记忆已关闭", failed: "更新失败" };
  return <main className="personal-assistant">
    <header className="assistant-header">
      <div className="assistant-identity"><AssistantMascot /><div><h1>个人助理</h1><p>聊目标、安排与进展，把下一步想清楚。</p></div></div>
      <button className="ghost-action" onClick={() => setShowMemory(!showMemory)}>助理记忆 · {data ? status[data.memory_status] : "加载中"}</button>
    </header>
    {(error || query.error) && <p role="alert">{error || String(query.error)}</p>}
    <div className="assistant-body">
      <section className="assistant-conversation" aria-label="个人助理对话">
        {!data?.messages.length && <div className="assistant-welcome"><AssistantMascot variant="welcome" animated /><h2>今天想推进什么？</h2><p>告诉我考试范围、可用时间或教学目标。我会结合已有记录提供建议。</p>
          {["帮我安排考前复习，先问我需要哪些信息。", "根据我的练习记录，看看下一步该学什么。", "我今天只有二十分钟，帮我安排一下。"].map(text => <button className="ghost-action" key={text} onClick={() => setDraft(text)}>{text}</button>)}
        </div>}
        {data?.messages.map(message => <article key={message.id} className={`assistant-message ${message.role}`}>
          <strong className="assistant-message-author">{message.role !== "user" && <AssistantMascot />}{message.role === "user" ? "你" : "个人助理"}</strong>
          <MarkdownMessage content={message.content} />
          {message.role === "user" && (message.status === "failed" || (message.status === "pending" && Date.now() / 1000 - message.created >= 120)) && <button disabled={busy} className="ghost-action" onClick={() => void send(message.content, message.id)}>重试回复</button>}
          {message.role === "user" && message.status === "pending" && <small>回复处理中；若长时间没有结果，可稍后重试。</small>}
        </article>)}
        {busy && <div className="assistant-working" role="status"><AssistantMascot animated /><span>正在处理…</span></div>}
        <ExamPlanPanel userId={userId} onOpenAttempt={onOpenAttempt} />
      </section>
      {showMemory && <aside className="assistant-memory" aria-label="助理记忆">
        <h2>助理记忆</h2><p>{data ? status[data.memory_status] : "加载中"}</p>
        <p>记忆在后台整理，不影响本轮对话。时间范围和来源会保留，推测不代表已确认的事实。</p>
        <label><input type="checkbox" checked={data?.enabled ?? false} disabled={busy || !data} onChange={e => void action(() => api.assistantMemory(e.target.checked))} />自动整理个人记忆</label>
        <p>关闭后停止写入和读取长期记忆；当前聊天仍保留。</p>
        {data?.memory_error && <p role="status">{data.memory_error}</p>}
        {data?.memory_status === "failed" && <button className="ghost-action" disabled={busy} onClick={() => void action(api.assistantMemoryRetry)}>重试更新</button>}
        {!data?.memories.length && <p>还没有整理好的记忆。整理完成后会显示在这里。</p>}
        {data?.memories.map(memory => <article key={memory.id}>
          <p>{memory.content}</p>{memory.temporal && <small>{memory.temporal}</small>}
          <details><summary>查看来源</summary>{memory.sources.map(source => <p key={source.id}>{source.timestamp && <small>{source.timestamp}<br /></small>}{source.text}</p>)}</details>
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
    <footer className="assistant-composer">
      <div className="assistant-shortcuts"><button className="ghost-action" onClick={() => onOpen("path")}>课程进度与学习计划</button><button className="ghost-action" onClick={() => onOpen("training")}>去练习</button><button className="ghost-action" onClick={() => onOpen("school")}>班级与任务</button></div>
      <form onSubmit={e => { e.preventDefault(); if (draft.trim() && !busy) void send(draft.trim()); }}>
        <textarea aria-label="给个人助理的消息" value={draft} maxLength={4000} onChange={e => setDraft(e.target.value)} placeholder="例如：下周考操作系统，每天能复习半小时…" />
        <button className="ghost-action" disabled={busy || !draft.trim()}>发送</button>
      </form><small>复习草稿需确认后采用；完成练习后，回到这里继续跟进。</small>
    </footer>
  </main>;
}
