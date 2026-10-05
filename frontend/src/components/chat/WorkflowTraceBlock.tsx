import { useMemo, useState } from "react";
import type {
  AgentExecutionStep,
  AgentStatus,
  DeepSearchTrace,
  DeepSearchSubqueryTask,
  ExplainabilityDetails,
  ModeId
} from "../../types";

const STATUS_LABEL: Record<string, string> = {
  pending: "等待",
  running: "运行中",
  success: "完成",
  error: "错误",
  skipped: "跳过"
};

interface FlowNode {
  id: string;
  label: string;
  detail: string;
  stepIds: string[];
}

interface FlowEdge {
  label?: string;
}

interface FlowStart {
  type: "start";
  label: string;
}

interface FlowTemplate {
  title: string;
  summary: string;
  rows: Array<Array<FlowNode | FlowEdge | FlowStart>>;
}

type TraceMode = ModeId;

const DEEPSEARCH_FLOW: FlowTemplate = {
  title: "DeepSearch",
  summary: "课程知识库检索",
  rows: [[
    start("用户问题"),
    edge(),
    node("deepsearch_plan", "拆解问题", "将问题拆成可检索的子问题。", ["deepsearch_plan"]),
    edge(),
    node("deepsearch_retrieve", "课程内检索", "在所选课程知识库内并行检索子问题。", ["deepsearch_retrieve"]),
    edge(),
    node("deepsearch_review", "证据评审", "检查证据是否足以回答问题。", ["deepsearch_review"]),
    edge(),
    node("answer_generate", "生成回答", "综合检索证据，流式生成回答。", ["answer_generate", "final_response"])
  ]]
};

const retryNode = node("deepsearch_retry", "改写问题", "改写证据不足的子问题，在所选课程内补充检索后重新评审。", ["deepsearch_retry"]);

export function WorkflowTraceBlock({ details }: { details?: ExplainabilityDetails }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const mode = normalizeMode(details?.modeUsed || details?.mode);
  const template = DEEPSEARCH_FLOW;
  const stepMap = useMemo(() => new Map((details?.workflowSteps || []).map((step) => [step.nodeId, step])), [details?.workflowSteps]);
  if (mode !== "deepsearch") return null;
  const selectedNode = [...template.rows.flat(), retryNode]
    .find((item): item is FlowNode => "id" in item && item.id === selectedId);
  const selectedSteps = selectedNode
    ? selectedNode.stepIds
        .map((stepId) => stepMap.get(stepId))
        .filter((step): step is AgentExecutionStep => Boolean(step))
    : [];

  return (
    <div className="trace-block">
      <div className="flow-header">
        <div>
          <div className="trace-heading">回答过程</div>
          <div className="flow-title">
            {template.title}
          </div>
          <div className="flow-summary">{template.summary}</div>
          {details?.responseTiming && (
            <div className="flow-summary" title="服务端从开始处理到答案生成完成的计时，不含会话存储和网络传输；评审仅统计本次实际执行的步骤。">
              首段文字 {formatOptionalDuration(details.responseTiming.firstTextMs ?? undefined)}
              {` · 回答总耗时 ${formatDuration(details.responseTiming.totalMs)}`}
            </div>
          )}
        </div>
      </div>
      <div className={`flow-canvas ${mode}`}>
        <div className={`flow-diagram ${mode}`}>
          {template.rows.map((row, rowIndex) => (
            <div
              className="flow-row"
              key={`${mode}-${rowIndex}`}
            >
              {row.map((item, index) =>
                "id" in item ? (
                  <FlowNodeButton
                    details={details}
                    isSelected={selectedId === item.id}
                    key={`${item.id}-${rowIndex}-${index}`}
                    node={item}
                    onClick={() => setSelectedId((value) => (value === item.id ? null : item.id))}
                    stepMap={stepMap}
                  />
                ) : "type" in item ? (
                  <div className="flow-start" key={`start-${rowIndex}-${index}`}>{item.label}</div>
                ) : (
                  <div className="flow-edge" key={`edge-${rowIndex}-${index}`}>
                    {item.label ? <span>{item.label}</span> : null}
                  </div>
                )
              )}
            </div>
          ))}
          <div className="flow-retry-branch">
            <span>证据不足时</span>
            <FlowNodeButton details={details} isSelected={selectedId === "deepsearch_retry"}
              node={retryNode} onClick={() => setSelectedId(value => value === "deepsearch_retry" ? null : "deepsearch_retry")}
              stepMap={stepMap} />
            <span>补检索后重新评审</span>
          </div>
        </div>
      </div>
      {selectedNode ? (
        <div className="trace-detail">
          <div className="font-semibold text-stone-900">{selectedNode.label}</div>
          <p className="mt-1 text-sm leading-6 text-stone-600">{selectedNode.detail}</p>
          <DeepSearchNodeDetail details={details} node={selectedNode} />
          {selectedSteps.length ? (
            <div className="mt-3 grid gap-2 text-sm text-stone-600">
              {selectedSteps.map((step) => {
                const outputSummary = step.outputSummary;
                return (
                  <div className="flow-step-detail" key={step.nodeId}>
                    <div className="font-semibold text-stone-800">{step.nodeName}</div>
                    {step.inputSummary ? <div>输入：{step.inputSummary}</div> : null}
                    {outputSummary ? <div>输出：{outputSummary}</div> : null}
                    {step.durationMs !== undefined ? <div>耗时：{step.durationMs} ms</div> : null}
                    {step.error ? <div className="text-rose-700">错误：{step.error}</div> : null}
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function FlowNodeButton({
  details,
  isSelected,
  node,
  onClick,
  stepMap
}: {
  details?: ExplainabilityDetails;
  isSelected: boolean;
  node: FlowNode;
  onClick: () => void;
  stepMap: Map<string, AgentExecutionStep>;
}) {
  const status = resolveNodeStatus(node, stepMap, details);
  const duration = status === "skipped" ? null : resolveNodeDuration(node, stepMap);
  return (
    <button
      className={`flow-node ${status} ${isSelected ? "selected" : ""}`}
      type="button"
      onClick={onClick}
    >
      <span className="flow-node-title">{node.label}</span>
      <span className="flow-node-status">
        {STATUS_LABEL[status]}
        {duration !== null ? ` · ${formatDuration(duration)}` : ""}
      </span>
    </button>
  );
}

function DeepSearchNodeDetail({
  details,
  node
}: {
  details?: ExplainabilityDetails;
  node: FlowNode;
}) {
  const mode = normalizeMode(details?.modeUsed || details?.mode);
  const trace = resolveDeepSearchTrace(details, node);
  const isDeepSearch = mode === "deepsearch";
  if (!isDeepSearch || !trace) return null;

  if (node.id === "deepsearch_plan") {
    const subQuestions = trace.subQuestions || [];
    return (
      <div className="deeptrace-list">
        {subQuestions.length ? (
          subQuestions.map((item, index) => (
            <div className="deeptrace-item" key={item.id || index}>
              <div className="deeptrace-item-title">
                {item.id || `q${index + 1}`} · {item.question || "未记录子问题"}
              </div>
              <div>使用问题：{item.usedQuestion || item.question || "未记录"}</div>
              <div>
                检索参数：{item.queryMode || "hybrid"} · top_k：
                {item.topK ?? "-"} · chunk_top_k：{item.chunkTopK ?? "-"}
              </div>
            </div>
          ))
        ) : (
          <div className="deeptrace-empty">本次回答没有保存子问题拆解明细。</div>
        )}
      </div>
    );
  }

  if (node.id === "deepsearch_retrieve") {
    const tasks = trace.subqueryTasks || [];
    const fallbackSubQuestions = trace.subQuestions || [];
    return (
      <div className="deeptrace-list">
        {tasks.length ? (
          tasks.map((task, index) => {
            const subQuestion = findSubQuestion(trace, task.subQuestionId);
            const result = findSubqueryResult(trace, task);
            return (
              <div className="deeptrace-item" key={task.taskId || `${task.subQuestionId}-${index}`}>
                <div className="deeptrace-item-title">
                  {task.subQuestionId || `q${index + 1}`} ·{" "}
                  {subQuestion?.question || task.question || "未记录子问题"}
                </div>
                <div>使用问题：{task.usedQuestion || subQuestion?.usedQuestion || subQuestion?.question || "未记录"}</div>
                <div>
                  目标知识库：
                  {task.subjectLabel || subjectLabel(task.subjectId) || "未记录"}
                </div>
                <div>
                  检索参数：{task.queryMode || "hybrid"} · top_k：
                  {task.topK ?? "-"} · chunk_top_k：{task.chunkTopK ?? "-"}
                </div>
                {result ? (
                  <>
                    <div>
                      检索状态：{result.queryStatus || "未记录"}
                      {typeof result.elapsedMs === "number"
                        ? ` · ${formatDuration(result.elapsedMs)}`
                        : ""}
                    </div>
                    {result.queryMessage ? <div>检索信息：{result.queryMessage}</div> : null}
                    {result.failureReason ? <div>失败原因：{result.failureReason}</div> : null}
                    {result.answerPreview ? <div>结果摘要：{result.answerPreview}</div> : null}
                  </>
                ) : null}
              </div>
            );
          })
        ) : fallbackSubQuestions.length ? (
          fallbackSubQuestions.map((item, index) => (
            <div className="deeptrace-item" key={item.id || index}>
              <div className="deeptrace-item-title">
                {item.id || `q${index + 1}`} · {item.question || "未记录子问题"}
              </div>
              <div>
                检索参数：{item.queryMode || "hybrid"} · top_k：
                {item.topK ?? "-"} · chunk_top_k：{item.chunkTopK ?? "-"}
              </div>
            </div>
          ))
        ) : (
          <div className="deeptrace-empty">本次回答没有保存子问题检索参数。</div>
        )}
      </div>
    );
  }

  if (node.id === "deepsearch_review") {
    const review = trace.review || [];
    return (
      <div className="deeptrace-list">
        {review.length ? (
          review.map((item, index) => {
            const subQuestion = findSubQuestion(trace, item.subQuestionId);
            return (
              <div className="deeptrace-item" key={`${item.subQuestionId}-${index}`}>
                <div className="deeptrace-item-title">
                  {item.subQuestionId || `q${index + 1}`} ·{" "}
                  {subQuestion?.question || "未记录子问题"}
                </div>
                <div>评审结果：{sufficientLabel(item.sufficient)}</div>
                {item.judgeReason ? <div>评审原因：{item.judgeReason}</div> : null}
              </div>
            );
          })
        ) : (
          <div className="deeptrace-empty">本次回答没有保存证据评审明细。</div>
        )}
      </div>
    );
  }

  if (node.id === "deepsearch_retry") {
    const retry = trace.retry;
    const ids = retry?.insufficientSubquestionIds || [];
    const rewrites = retry?.rewrites || [];
    return (
      <div className="deeptrace-list">
        <div className="deeptrace-item">
          <div className="deeptrace-item-title">改写子问题并补充检索</div>
          <div>重试轮次：{retry?.queryAttempt ?? 0}</div>
          <div>
            不足子问题：
            {ids.length ? ids.join("、") : "无，证据评审未触发补充检索"}
          </div>
          <div>改写方式：本节点调用 LLM 生成改写问题，然后提高检索强度进入下一轮检索。</div>
        </div>
        {rewrites.length ? (
          rewrites.map((item, index) => {
            const targetLabels = item.targetSubjectLabels?.length
              ? item.targetSubjectLabels
              : item.targetSubjects || [];
            return (
              <div className="deeptrace-item" key={`${item.subQuestionId}-${index}`}>
                <div className="deeptrace-item-title">
                  {item.subQuestionId || `retry-${index + 1}`}
                  {item.attempt ? ` · 第 ${item.attempt} 轮` : ""}
                </div>
                {item.question ? <div>原始子问题：{item.question}</div> : null}
                {item.previousUsedQuestion ? (
                  <div>改写前查询：{item.previousUsedQuestion}</div>
                ) : null}
                {item.rewrittenQuestion ? (
                  <div>LLM 改写后：{item.rewrittenQuestion}</div>
                ) : (
                  <div>LLM 改写后：未生成新改写，沿用原查询并提高检索强度</div>
                )}
                {item.appliedQuestion ? <div>实际重试查询：{item.appliedQuestion}</div> : null}
                {item.judgeReason ? <div>评审原因：{item.judgeReason}</div> : null}
                {item.rewriteReason ? <div>改写理由：{item.rewriteReason}</div> : null}
                <div>
                  检索参数：
                  {item.queryMode || "默认"} · topK {item.topK ?? "默认"} · chunkTopK{" "}
                  {item.chunkTopK ?? "默认"}
                </div>
                {targetLabels.length ? <div>目标知识库：{targetLabels.join("、")}</div> : null}
              </div>
            );
          })
        ) : null}
      </div>
    );
  }

  if (node.id === "answer_generate") {
    const prompt = trace.finalAnswerPrompt || "";
    const promptChars = trace.finalAnswerPromptChars ?? (prompt ? prompt.length : null);
    return (
      <div className="deeptrace-list">
        {prompt ? (
          <div className="deeptrace-item">
            <div className="deeptrace-item-title">最终回答 Prompt</div>
            <div>Prompt 长度：{promptChars ?? "未记录"} 字符</div>
            <pre className="deeptrace-prompt">{prompt}</pre>
          </div>
        ) : (
          <div className="deeptrace-empty">最终回答 Prompt 尚未生成或未保存。</div>
        )}
      </div>
    );
  }

  return null;
}

function node(
  id: string,
  label: string,
  detail: string,
  stepIds: string[]
): FlowNode {
  return { id, label, detail, stepIds };
}

function edge(label?: string): FlowEdge {
  return { label };
}

function start(label: string): FlowStart {
  return { type: "start", label };
}

const SUBJECT_LABELS: Record<string, string> = {
  C_program: "C语言",
  operating_systems: "操作系统",
  cybersec_lab: "网络安全",
  auto: "未记录课程"
};

function subjectLabel(subjectId?: string): string {
  if (!subjectId) return "";
  return SUBJECT_LABELS[subjectId] || subjectId;
}

function formatOptionalDuration(durationMs: number | undefined): string {
  return typeof durationMs === "number" && durationMs >= 0 ? formatDuration(durationMs) : "未记录";
}

function resolveDeepSearchTrace(
  details: ExplainabilityDetails | undefined,
  node: FlowNode
): DeepSearchTrace | undefined {
  const step = details?.workflowSteps?.find((item) => node.stepIds.includes(item.nodeId));
  const stepDetails = step?.details;
  const stepTrace =
    stepDetails && typeof stepDetails === "object"
      ? (stepDetails.deepsearchTrace as DeepSearchTrace | undefined)
      : undefined;
  return stepTrace || details?.deepsearchTrace;
}

function findSubQuestion(details: DeepSearchTrace, id?: string) {
  return (details.subQuestions || []).find((item) => item.id === id);
}

function findSubqueryResult(details: DeepSearchTrace, task: DeepSearchSubqueryTask) {
  return (details.subqueryResults || []).find((item) => {
    if (task.taskId && item.taskId === task.taskId) return true;
    const sameSubQuestion = item.subQuestionId === task.subQuestionId;
    const sameSubject = !task.subjectId || item.subjectId === task.subjectId;
    return sameSubQuestion && sameSubject;
  });
}

function sufficientLabel(value: boolean | null | undefined): string {
  if (value === true) return "充分";
  if (value === false) return "不足";
  return "未评审";
}

function normalizeMode(raw: unknown): TraceMode {
  return raw === "deepsearch" ? "deepsearch" : "instant";
}

function resolveNodeStatus(
  node: FlowNode,
  stepMap: Map<string, AgentExecutionStep>,
  details?: ExplainabilityDetails
): AgentStatus {
  const mode = normalizeMode(details?.modeUsed || details?.mode);
  const finalStatus = stepMap.get("final_response")?.status;
  const completed = finalStatus === "success" || details?.status === "done";
  const actualSteps = node.stepIds
    .map((stepId) => stepMap.get(stepId))
    .filter((step): step is AgentExecutionStep => Boolean(step && step.status !== "pending"));
  if (actualSteps.some(step => step.status === "error")) return "error";
  if (node.id === "answer_generate" && !completed && actualSteps.some(step => step.status === "success")) {
    return details?.status === "error" ? "error" : "running";
  }
  if (actualSteps.length) return aggregateStatus(actualSteps);
  if (mode === "deepsearch" && completed) {
    const deepsearchRetrievalNodes = new Set([
      "deepsearch_plan",
      "deepsearch_retrieve",
      "deepsearch_review",
      "deepsearch_retry"
    ]);
    if (details?.retrievalUsed === false && deepsearchRetrievalNodes.has(node.id)) {
      return "skipped";
    }
    if (node.id === "deepsearch_retry" && shouldSkipDeepSearchRetry(stepMap, details)) {
      return "skipped";
    }
  }

  const directSteps = node.stepIds.map((stepId) => stepMap.get(stepId)).filter(Boolean) as AgentExecutionStep[];
  if (directSteps.length) return aggregateStatus(directSteps);

  return "pending";
}

function shouldSkipDeepSearchRetry(
  stepMap: Map<string, AgentExecutionStep>,
  details?: ExplainabilityDetails
): boolean {
  const retryStep = stepMap.get("deepsearch_retry");
  if (retryStep && retryStep.status !== "pending") return retryStep.status === "skipped";
  const retry = details?.deepsearchTrace?.retry;
  if (retry) {
    const insufficient = retry.insufficientSubquestionIds || [];
    return retry.needsRetry === false || insufficient.length === 0;
  }
  return stepMap.get("deepsearch_review")?.status === "success";
}

function resolveNodeDuration(
  node: FlowNode,
  stepMap: Map<string, AgentExecutionStep>
): number | null {
  const steps = node.stepIds
    .map((stepId) => stepMap.get(stepId))
    .filter((step): step is AgentExecutionStep => Boolean(step));
  const durations = steps
    .map((step) => step.durationMs)
    .filter((duration): duration is number => typeof duration === "number" && duration >= 0);
  if (!durations.length) return null;
  return durations.reduce((sum, duration) => sum + duration, 0);
}

function formatDuration(durationMs: number): string {
  if (durationMs === 0) return "<1 ms";
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(durationMs < 10_000 ? 1 : 0)} s`;
}

function aggregateStatus(steps: AgentExecutionStep[]): AgentStatus {
  if (steps.some((step) => step.status === "error")) return "error";
  if (steps.some((step) => step.status === "running")) return "running";
  if (steps.every((step) => step.status === "success")) return "success";
  if (steps.some((step) => step.status === "success")) return "success";
  if (steps.some((step) => step.status === "skipped")) return "skipped";
  return "pending";
}
