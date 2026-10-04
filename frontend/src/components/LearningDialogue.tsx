import { useEffect, useState } from "react";
import type { LearningAttempt } from "../learningTypes";
import { useLearningWorkflow } from "../useLearningWorkflow";

export function LearningDialogue({ attempt, busy, onAction, onRefresh, onRetest }: {
  attempt: LearningAttempt;
  busy: boolean;
  onAction: (action: string, answer?: string, resumeRunId?: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  onRetest: () => void;
}) {
  const [answer, setAnswer] = useState("");
  const dialogue = attempt.dialogue;
  const workflow = useLearningWorkflow(attempt.id, "dialogue", attempt.id, onRefresh);
  const waiting = busy || workflow.running;
  useEffect(() => { setAnswer(""); }, [dialogue?.revision]);
  async function act(action: string, value?: string, resumeRunId?: string) {
    try { await onAction(action, value, resumeRunId); }
    finally { await workflow.refresh(); }
  }
  const verified = dialogue && attempt.submission_count > dialogue.baseline_submissions;
  return <section className="learning-dialogue">
    <h2>错因确认与针对性补练</h2>
    <p>说说判断依据 → 对比讲解 → 原题订正 → 新题独立验证。最多追问三轮。</p>
    {attempt.parent_id && <p><strong>新题验证结果：</strong>{!attempt.evaluation ? "请先独立作答，提交后查看验证结果。" : attempt.first_unassisted_pass ? "新题首次独立通过，本次验证达标。" : attempt.evaluation.passed ? "本题已通过；有辅助、订正或重复练习，仍需另一道新题独立验证。" : "新题尚未通过，建议结合首错继续确认原因并补练。"}</p>}
    {workflow.running && <p>诊断正在进行，刷新或离开后仍可返回查看。</p>}
    {workflow.run?.error && <p>{workflow.run.error}</p>}
    {workflow.error && <p>{workflow.error}</p>}
    {workflow.run?.can_resume && <button className="ghost-action" disabled={waiting}
      onClick={() => void act("resume", undefined, workflow.run!.id)}>继续上次诊断</button>}
    {!dialogue ? <button className="ghost-action" disabled={waiting || attempt.status === "passed"}
      onClick={() => void act("start")}>{workflow.run?.can_resume ? "重新开始诊断" : "开始互动诊断"}</button> : <>
      {dialogue.turns.map((turn, i) => <div key={i}>
        <p><strong>追问 {i + 1}：</strong>{turn.question}</p>
        {turn.answer && <p>你的回答：{turn.answer}</p>}
      </div>)}
      <p><strong>待验证的错因假设：</strong>{dialogue.hypothesis}</p>
      {dialogue.explanation && <div><h3>对比讲解</h3><p>{dialogue.explanation}</p></div>}
      {dialogue.status === "talking" && attempt.status !== "passed" ? <>
        <textarea aria-label="诊断回答" value={answer} maxLength={2000}
          placeholder="说说你的判断依据，也可以回答不确定。"
          onChange={(e) => setAnswer(e.target.value)} />
        <button className="ghost-action" disabled={waiting || !answer.trim()} onClick={async () => {
          await act("answer", answer);
        }}>回答并继续</button>
        <button className="ghost-action" disabled={waiting} onClick={() => void act("answer", "我不确定")}>不确定</button>
        <button className="ghost-action" disabled={waiting} onClick={() => void act("verify")}>跳过追问，直接核验</button>
        <button className="ghost-action" disabled={waiting} onClick={() => void act("exit")}>结束诊断</button>
      </> : <p>{attempt.status === "passed" ? "原题已通过，接下来用新题验证。" : dialogue.status === "closed" ? "诊断已结束，记录已保存。" : "请在左侧完成或修改本题，再提交核验。"}</p>}
      <p>{dialogue.next_step}</p>
      <p><strong>作答核验：</strong>{verified
        ? attempt.evaluation?.passed ? "本题已通过评测；经过辅导，仍需新题独立复测。" : "本题尚未通过，请结合评测结果继续订正。"
        : "尚无诊断后的提交，不能据此判断掌握。"}</p>
      {attempt.status === "passed" && !attempt.review_id && <div>
        <h3>针对性新题复测</h3>
        <p>{attempt.recommendation ? `${attempt.recommendation.title}：${attempt.recommendation.reason}` : "该题型暂无尚未练习的新题，暂时无法验证独立掌握。"}</p>
        <button className="ghost-action" disabled={waiting || !attempt.recommendation} onClick={onRetest}>开始新题独立复测</button>
      </div>}
      <p>对话假设不影响判分；提交后的评测结果进入学习状态和后续安排。</p>
    </>}
  </section>;
}
