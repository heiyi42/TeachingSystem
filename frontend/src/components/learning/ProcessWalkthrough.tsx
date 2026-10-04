import { useState } from "react";
import type { LearningAttempt } from "../../learningTypes";

const LABELS: Record<string, string> = {
  value: "检查点结果", explanation: "依据", step: "步骤", page: "访问页面",
  frames_before: "访问前页框", frames_after: "访问后页框", event: "事件",
  evicted: "换出页面", process: "进程", start: "开始时间", end: "结束时间",
  work_before: "分配前可用资源", work_after: "回收后可用资源",
  allocation_released: "释放资源", safe: "是否安全", number: "用例编号",
  input: "输入", expected: "期望输出", output: "实际输出", passed: "是否通过",
  status: "状态", compiler: "编译反馈", inputs: "实验输入", task: "实验任务", name: "产物名称",
  size: "字节数", mode: "文件权限", first_error: "首个问题",
  next_uses: "后续访问位置", optimal_victims: "可选换出页面",
};
function display(value: unknown): string {
  if (value === null || value === undefined) return "无";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  const text = String(value);
  return ({ hit: "命中", fault: "缺页", replace: "置换", idle: "空闲", passed: "通过", failed: "未通过" } as Record<string, string>)[text] || text;
}

export function ProcessWalkthrough({ attempt, busy, onAdvance }: {
  attempt: LearningAttempt;
  busy: boolean;
  onAdvance: (prediction: string) => Promise<boolean>;
}) {
  const [prediction, setPrediction] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const history = attempt.walkthrough;
  const runtime = ["c_program", "security_lab"].includes(attempt.exercise.kind);
  const stale = Boolean(runtime && history && history.submission_count !== attempt.submission_count);
  const steps = stale ? [] : history?.steps || [];
  const cursor = Math.min(selected ?? steps.length - 1, steps.length - 1);
  const step = steps[cursor];
  const parameters = attempt.exercise.parameters;
  const nextPrompt = parameters.checkpoints?.[steps.length]
    || parameters.security_checks?.[steps.length]?.label
    || (attempt.exercise.kind === "page_replacement" && parameters.sequence?.[steps.length] !== undefined
      ? `访问页面 ${parameters.sequence[steps.length]} 后，页框如何变化？`
      : "预测下一步结果或说明判断依据");
  const done = !stale && Boolean(history && steps.length >= history.total);
  if (attempt.assignment || ["c_repair", "pv_design"].includes(attempt.exercise.kind)) return null;
  return <section className="process-walkthrough" aria-label="过程推演">
    <h3>{runtime ? "运行证据回放" : "逐步预测与推演"}</h3>
    <p>{runtime ? "先提交运行，再逐项查看实际测试或实验产物。" : "先预测下一步，再展开参考过程。可回看已展开的步骤。"}
      展开过程会记录为辅导，不计作独立掌握。</p>
    {steps.length > 0 && <>
      <nav className="training-actions" aria-label="推演步骤">
        <button className="ghost-action" disabled={cursor <= 0} onClick={() => setSelected(cursor - 1)}>上一步</button>
        <span>第 {cursor + 1} / {steps.length} 步已展开</span>
        <button className="ghost-action" disabled={cursor >= steps.length - 1} onClick={() => setSelected(cursor + 1)}>下一步</button>
      </nav>
      {step && <div aria-live="polite">
        <h4>{step.title}</h4>
        <p><strong>你的预测：</strong>{step.prediction}</p>
        {Object.entries(step.details).sort(([a], [b]) => {
          const priority = ["frames_before", "frames_after", "page", "event", "evicted"];
          return (priority.indexOf(a) < 0 ? 99 : priority.indexOf(a)) - (priority.indexOf(b) < 0 ? 99 : priority.indexOf(b));
        }).map(([key, value]) => <div key={key} className="process-detail">
          <strong>{LABELS[key] || key}</strong>
          {key.startsWith("frames_") && Array.isArray(value)
            ? <div className="process-frames">{Array.from({length: attempt.exercise.parameters.frames || value.length}, (_, i) =>
                <span key={i} aria-label={`页框 ${i + 1}`}>{value[i] ?? "空"}</span>)}</div>
            : <pre>{display(value)}</pre>}
        </div>)}
      </div>}
    </>}
    {!done && (!runtime || attempt.submission_count > 0) && <>
      <label>第 {steps.length + 1} 步：{nextPrompt}
        <textarea aria-label="过程预测" maxLength={2000} value={prediction}
          placeholder="写下预测，也可以填写不确定。" onChange={(e) => setPrediction(e.target.value)} />
      </label>
      <button className="ghost-action" disabled={busy || !prediction.trim()} onClick={async () => {
        if (await onAdvance(prediction)) { setPrediction(""); setSelected(null); }
      }}>记录预测并展开一步</button>
    </>}
    {done && <p>过程已展开完毕。请回到作答区完成核验；有多种合法解的题目，这里仅展示一种参考过程。</p>}
    {stale && <p>检测到新提交，下一次展开将查看最新运行；历史预测仍保留在学习事件中。</p>}
    <p className="training-muted">自由文字预测不自动判分，正式结果以提交核验为准。AI 互动诊断会读取最近的预测与展开记录。</p>
  </section>;
}
