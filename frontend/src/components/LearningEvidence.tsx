import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { EvidenceEvent, LearningSubject } from "../learningTypes";

const ACTIONS: Record<string, string> = {
  material: "阅读资料", explain: "针对性讲解", practice: "新题练习", continue: "继续订正", review: "独立复测",
};
export function LearningEvidence({subject, timeline, onOpenAttempt}: {
  subject: LearningSubject; timeline: EvidenceEvent[]; onOpenAttempt: (id: string) => void;
}) {
  const [showDemo, setShowDemo] = useState(false);
  const [step, setStep] = useState(0);
  const demo = useQuery({queryKey: ["roadshow", subject], queryFn: () => api.learningRoadshow(subject), enabled: showDemo, staleTime: Infinity});
  const count = Math.max(0, ...(demo.data?.students.map(s => s.timeline.length) || []));
  const visible = Math.min(step, count);
  return <section className="learning-evidence">
    <h3>学习证据时间线</h3>
    <p>当前目标范围内最近 80 条课外记录，按发生时间倒序排列；历史提交保留当时结果，复核后的判定以原练习为准。</p>
    <details>
      <summary>查看个人时间线 · {timeline.length} 条</summary>
      {!timeline.length && <p>暂无可展示的记录。完成练习或互动后会出现在这里。</p>}
      <ol>{timeline.map(event => <li key={event.id}>
        <p><time>{new Date(event.created_at * 1000).toLocaleString("zh-CN")}</time> · <strong>{event.label}</strong> · {event.title}</p>
        {event.detail && <p>{event.detail}</p>}
        <button className="training-link" onClick={() => onOpenAttempt(event.attempt_id)}>查看原练习</button>
      </li>)}</ol>
    </details>
    <h3>双学生对照演示</h3>
    <p>模拟场景，用于讲解个性化流程，不能作为学习效果数据。</p>
    <button className="ghost-action" onClick={() => {setShowDemo(!showDemo); setStep(0);}}>{showDemo ? "收起模拟演示" : "打开模拟演示"}</button>
    {showDemo && <>
      {demo.isPending && <p>正在用真实评测逻辑生成模拟记录…</p>}
      {demo.error && <p role="alert">演示加载失败：{demo.error.message}<button className="training-link" onClick={() => demo.refetch()}>重试</button></p>}
      {demo.data && <>
        <p>{demo.data.notice}</p>
        <div className="training-actions">
          <button className="ghost-action" disabled={visible === 0} onClick={() => setStep(visible - 1)}>演示上一步</button>
          <span>已展示 {visible} / {count} 个步骤</span>
          <button className="ghost-action" disabled={visible === count} onClick={() => setStep(visible + 1)}>演示下一步</button>
          <button className="training-link" onClick={() => setStep(count)}>查看完整对照</button>
        </div>
        <div className="learning-comparison">{demo.data.students.map(student => <article key={student.name}>
          <h4>{student.name}</h4>
          {!visible && <p>相同知识点、相同原题，从首次作答开始。</p>}
          {visible > 0 && <p><strong>第 {visible} 步：{student.timeline[visible - 1]?.label}</strong><br />{student.timeline[visible - 1]?.detail}</p>}
          <details><summary>查看已展开过程 · {visible} 条</summary>
          <ol>{student.timeline.slice(0, visible).map(event => <li key={event.id}>
            <strong>{event.label}</strong> · {event.title}
            {event.detail && <p>{event.detail}</p>}
          </li>)}</ol></details>
          {visible === count && <>
            <p><strong>{student.outcome}</strong></p>
            {student.memories.map(m => <p key={m.attempt_id}>{m.basis}</p>)}
            <h4>系统当前安排</h4>
            <ul>{student.tasks.map((task, i) => <li key={i}>{ACTIONS[task.kind] || task.kind} · {task.title} · 约 {task.minutes} 分钟{task.reason && <p>{task.reason}</p>}</li>)}</ul>
          </>}
        </article>)}</div>
      </>}
    </>}
  </section>;
}
