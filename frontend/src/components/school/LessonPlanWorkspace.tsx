import { useDiscardChanges } from "../shared/useDiscardChanges";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { LessonPlan } from "../../schoolTypes";

export function LessonPlanWorkspace({ classId, userId, onAssign, onOpenTask, initialPlan, onDirtyChange }: {
  initialPlan?: LessonPlan;
  onDirtyChange: (dirty: boolean) => void;
  classId: string;
  userId: string;
  onAssign: (plan: LessonPlan) => void;
  onOpenTask: (id: string, student?: string) => void;
}) {
  const { confirmDiscard, discardDialog } = useDiscardChanges();
  const client = useQueryClient();
  const [chapterId, setChapterId] = useState(initialPlan?.chapter_id || "");
  const [minutes, setMinutes] = useState(initialPlan?.minutes || 45);
  const [plan, setPlan] = useState<LessonPlan | null>(initialPlan || null);
  const [dirty, setDirty] = useState(Boolean(initialPlan && !initialPlan.id));
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const overview = useQuery({
    queryKey: ["lesson-plans", userId, classId],
    queryFn: () => api.lessonPlans(classId),
  });
  const effects = useQuery({
    queryKey: ["lesson-effects", userId, classId, plan?.id],
    queryFn: () => api.lessonEffects(classId, plan!.id!),
    enabled: Boolean(plan?.id),
  });
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await action(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  function change(values: Partial<LessonPlan>) {
    setPlan(current => current ? { ...current, ...values } : null);
    setDirty(true); setMessage("");
  }
  const selectedChapter = chapterId || overview.data?.chapters[0]?.id || "";
  const total = plan?.stages.reduce((sum, stage) => sum + stage.minutes, 0) || 0;

  return <section className="lesson-workspace">
    {discardDialog}
    {!plan && <><h2>备课安排</h2>
    <p className="training-muted">根据本班任务记录和已发布题库生成可编辑草案。保存备课不会发布作业；课堂练习与课后补练请选不同题目。</p></>}
    {(error || overview.error || effects.error) && <p role="alert">{error || String((overview.error || effects.error)?.message)}{(overview.error || effects.error) && <button type="button" className="training-link" disabled={overview.isFetching || effects.isFetching} onClick={() => { if (overview.error) void overview.refetch(); if (effects.error) void effects.refetch(); }}>重新读取</button>}</p>}
    {busy && <p role="status">正在处理备课安排，请稍候…</p>}
    {message && <p role="status">{message}</p>}
    {overview.isLoading && <p role="status">正在读取备课安排…</p>}
    <form hidden={Boolean(plan)} className="training-filters" onSubmit={event => {
      event.preventDefault();
      run(async () => {
        setPlan(await api.generateLessonPlan(classId, selectedChapter, minutes));
        setDirty(true);
        setMessage("草案尚未保存，请核对学情依据、教学内容和选题。");
      });
    }}>
      <label>备课章节<select value={selectedChapter} disabled={busy || dirty} onChange={e => setChapterId(e.target.value)}>
        {overview.data?.chapters.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}
      </select></label>
      <label>课时总时长（分钟）<input type="number" min={15} max={240} required value={minutes} disabled={busy || dirty} onChange={e => setMinutes(Number(e.target.value))} /></label>
      <button disabled={busy || dirty || !selectedChapter}>生成备课草案</button>
    </form>
    {!!overview.data?.plans.length && <label>已保存安排<select value={plan?.id || ""} disabled={busy || dirty} onChange={event => {
      const saved = overview.data!.plans.find(p => p.id === event.target.value) || null;
      setPlan(saved);
      if (saved) { setChapterId(saved.chapter_id); setMinutes(saved.minutes); }
      setDirty(false); setError(""); setMessage("");
    }}>
      <option value="">选择已保存安排</option>
      {overview.data.plans.map(p => <option key={p.id} value={p.id!}>{p.title} · 第 {p.revision} 版</option>)}
    </select></label>}
    {plan && <>
      <form className="task-editor lesson-editor" onSubmit={event => {
        event.preventDefault();
        run(async () => {
          const saved = await api.saveLessonPlan(plan);
          setPlan(saved); setDirty(false);
          await client.invalidateQueries({ queryKey: ["lesson-plans", userId, classId] });
          setMessage("备课安排已确认保存，尚未创建或发布补练作业。");
        });
      }}>
        <h3>{plan.chapter_title} · {plan.id ? `第 ${plan.revision} 版${dirty ? "（有未保存修改）" : ""}` : "未保存草案"}</h3>
        <details className="lesson-context"><summary>学情依据与资料准备 · {plan.baseline.member_count} 名学生{plan.gaps.length ? ` · ${plan.gaps.length} 项待核对` : ""}</summary>
        <p className="training-muted">{plan.id ? "首次保存时" : "生成时"}的学情依据：{new Date(plan.baseline.generated_at * 1000).toLocaleString("zh-CN")} · {plan.baseline.member_count} 名学生。首次保存会重新读取最新依据，后续编辑保留该快照。</p>
        <p>{plan.baseline.counts ? `本章应作答 ${plan.baseline.counts.expected_answers} 题次，已提交 ${plan.baseline.counts.submitted}，独立通过 ${plan.baseline.counts.independent}，辅助通过 ${plan.baseline.counts.assisted}，订正通过 ${plan.baseline.counts.corrected}。` : "本章尚无已发布任务。"}</p>
        <p>本章课程资料：{plan.material_available ? "已检测到，可在课程进度中查阅" : "未检测到，请准备讲义"}。</p>
        {!!plan.gaps.length && <ul>{plan.gaps.map(gap => <li key={gap}>{gap}</li>)}</ul>}
        <details className="task-evidence"><summary>查看讲评依据（前 {plan.baseline.errors.length} 类常见首错）</summary>
          {!plan.baseline.errors.length && <p>没有可用于归纳薄弱点的首错记录。草案采用课堂诊断安排。</p>}
          {plan.baseline.errors.map(group => <section key={group.id}>
            <h4>{group.label}</h4><p>涉及 {group.student_count} 人 · 首错提交 {group.occurrences} 次 · 对应作答最新提交仍出现 {group.current_student_count} 人</p>
            <p>{group.teaching_advice}</p>
            <ul>{group.evidence.slice(0, 3).map(e => <li key={`${e.attempt_id}:${e.submission_number}`}>
              {e.task_title} · {e.exercise_title} · v{e.content_version} · 第 {e.submission_number} 次提交：{e.message}
              <button type="button" className="training-link" onClick={() => onOpenTask(e.task_id, e.student_id)}>查看作答证据</button>
            </li>)}</ul>
          </section>)}
        </details>
        </details>
        <fieldset disabled={busy}>
          <label>备课标题<input maxLength={120} required value={plan.title} onChange={e => change({ title: e.target.value })} /></label>
          <label>讲评重点<textarea required maxLength={6000} value={plan.focus} onChange={e => change({ focus: e.target.value })} /></label>
          <label>安排总时长（分钟）<input type="number" min={15} max={240} required value={plan.minutes} onChange={e => change({ minutes: Number(e.target.value) })} /></label>
          <p>各环节合计 {total} / {plan.minutes} 分钟{total !== plan.minutes && "，请调整为一致后保存"}。</p>
          {plan.stages.map((stage, index) => <div className="lesson-stage" key={index}>
            <label>环节 {index + 1} 名称<input required maxLength={120} value={stage.title} onChange={e => change({ stages: plan.stages.map((s, i) => i === index ? { ...s, title: e.target.value } : s) })} /></label>
            <label>环节 {index + 1} 分钟数<input type="number" min={1} max={240} required value={stage.minutes} onChange={e => change({ stages: plan.stages.map((s, i) => i === index ? { ...s, minutes: Number(e.target.value) } : s) })} /></label>
            <label>环节 {index + 1} 内容<textarea required maxLength={6000} value={stage.content} onChange={e => change({ stages: plan.stages.map((s, i) => i === index ? { ...s, content: e.target.value } : s) })} /></label>
          </div>)}
          <details><summary>选题说明</summary><p className="training-muted">补练优先匹配首错标签及本班未布置的题目。调整后请核对适用性；课堂讲解和私人练习可能影响后续作答的独立性。</p></details>
          {(["classroom_ids", "homework_ids"] as const).map(key => <fieldset key={key}>
            <legend>{key === "classroom_ids" ? "课堂练习" : "课后补练"}（已选 {plan[key].length}）</legend>
            {plan.exercises.map(exercise => <label className="lesson-exercise" key={exercise.id}>
              <input type="checkbox" disabled={plan[key === "classroom_ids" ? "homework_ids" : "classroom_ids"].includes(exercise.id)} checked={plan[key].includes(exercise.id)} onChange={e => change({ [key]: e.target.checked ? [...plan[key], exercise.id] : plan[key].filter(id => id !== exercise.id) })} />
              {exercise.title} · v{exercise.content_version}
            </label>)}
            {!plan.exercises.length && <p>本章没有已发布题目，可先保存教学安排。</p>}
          </fieldset>)}
          <label>教师备注<textarea maxLength={6000} value={plan.teacher_notes} onChange={e => change({ teacher_notes: e.target.value })} /></label>
        </fieldset>
        <div className="training-actions">
          <button className="send-action" disabled={busy || !dirty || total !== plan.minutes}>确认并保存备课安排</button>
          <button type="button" className="ghost-action" disabled={busy} onClick={() => { const close = () => { setPlan(null); setDirty(false); setMessage(""); setError(""); }; if (dirty) confirmDiscard(close); else close(); }}>{dirty ? "放弃修改" : "关闭备课"}</button>
        </div>
      </form>
      {plan.id && <section>
        <h3>关联补练与效果</h3>
        <button className="ghost-action" disabled={busy || dirty || !plan.homework_ids.length} onClick={() => onAssign(plan)}>用已保存安排填写补练草稿</button>
        <button className="training-link" disabled={effects.isFetching} onClick={() => effects.refetch()}>刷新补练效果</button>
        <p className="training-muted">下表仅统计本安排关联的补练任务，分母为当前班级成员应作答题次。原学情和补练的题目、人数可能不同，不能直接据此认定教学效果或提升幅度。</p>
        {effects.isLoading && <p>正在读取补练效果…</p>}
        {effects.data && !effects.data.tasks.length && <p>尚未创建关联补练任务。</p>}
        <div className="training-table-wrap"><table className="training-table">
          <thead><tr><th>补练任务 / 备课版本</th><th>状态</th><th>已提交 / 应作答</th><th>独立 / 辅助 / 订正通过</th><th>重复 / 信息缺失通过</th><th>未通过 / 未提交</th><th>操作</th></tr></thead>
          <tbody>{effects.data?.tasks.map(task => <tr key={task.id}>
            <td>{task.title} · 第 {task.plan_revision} 版</td><td>{{ draft: "草稿", published: "已发布", closed: "已结束" }[task.status]}</td>
            <td>{task.status === "draft" ? "尚未发布" : `${task.counts.submitted} / ${task.counts.expected_answers}`}</td>
            <td>{task.counts.independent} / {task.counts.assisted} / {task.counts.corrected}</td>
            <td>{task.counts.repeated} / {task.counts.unclassified_pass}</td>
            <td>{task.counts.unpassed} / {task.counts.unsubmitted}</td>
            <td><button className="training-link" onClick={() => onOpenTask(task.id)}>查看任务</button></td>
          </tr>)}</tbody>
        </table></div>
      </section>}
    </>}
  </section>;
}
