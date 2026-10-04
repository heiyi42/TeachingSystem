import type { ChatMessage } from "../../types";
import { ExplainabilityCollapse } from "./ExplainabilityCollapse";
import { MarkdownMessage } from "../shared/MarkdownMessage";

export function AnswerMessage({ message }: { message: ChatMessage }) {
  return (
    <article className="message assistant answer-message">
      {message.details?.explainability?.status === "cancelled" ? <p className="composer-notice" role="status">已停止生成</p> : null}
      <div className="answer-content">
        <MarkdownMessage content={message.content} />
      </div>
      <ExplainabilityCollapse message={message} />
      {message.meta ? <div className="mt-3 text-xs text-stone-500">{message.meta}</div> : null}
    </article>
  );
}
