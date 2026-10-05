import { memo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import type { ChatMessage } from "../../types";
import { ExplainabilityCollapse } from "./ExplainabilityCollapse";
import { MarkdownMessage } from "../shared/MarkdownMessage";

export const AnswerMessage = memo(function AnswerMessage({ message, question, userId, subject, disabled, onOpenExercise, onOpenPath }: {
  message: ChatMessage;
  question: string;
  userId: string;
  subject: string;
  disabled: boolean;
  onOpenExercise: (id: string) => void;
  onOpenPath: () => void;
}) {
  const details = message.details?.explainability;
  const course = details?.subject || subject;
  const complete = Boolean(message.content.trim()) && (!details?.status || details.status === "done");
  const training = useQuery({
    queryKey: ["training-match", userId, question, course],
    queryFn: () => api.matchTraining(question, course),
    enabled: complete && Boolean(question),
    staleTime: 30000,
    retry: false,
  });
  if (!message.content.trim() && message.details?.explainability?.status === "streaming") {
    return (
      <div className="answer-waiting" role="status" aria-label="正在生成回答">
        <span aria-hidden="true" />
        <span aria-hidden="true" />
        <span aria-hidden="true" />
      </div>
    );
  }

  return (
    <article className="message assistant answer-message">
      <ExplainabilityCollapse message={message} />
      {message.details?.explainability?.status === "cancelled" ? <p className="composer-notice" role="status">已停止生成</p> : null}
      <MarkdownMessage content={message.content} />
      {complete && question && <details className="related-training">
        <summary>相关训练{training.data?.matches.length ? ` · ${training.data.matches.length} 项` : ""}</summary>
        <div className="chat-training-links">
          {training.data?.matches.map(point => <button key={point.id} className="training-link" title={`${point.reason}；关联目标：${point.objectives.join("；")}`} disabled={disabled || !point.exercise_id} onClick={() => { if (point.exercise_id) onOpenExercise(point.exercise_id); }}>
            {point.title}{point.exercise_id ? "" : "（暂无新题）"}
          </button>)}
          {!training.data?.matches.length && <span>{training.isError ? "训练匹配暂不可用" : training.isFetching ? "正在匹配训练…" : "暂未匹配到训练知识点"}</span>}
          <button className="training-link" disabled={disabled} onClick={onOpenPath}>查看课程路径</button>
        </div>
      </details>}
    </article>
  );
});
