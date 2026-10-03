import { ChevronDown } from "lucide-react";
import { useState } from "react";
import type { AnswerCitations } from "../types";

export function RetrievedChunksCollapse({ citations }: { citations?: AnswerCitations }) {
  const [open, setOpen] = useState(false);
  const sources = citations?.sources || [];
  const cited = sources.filter(source => source.cited);
  return (
    <div className="trace-block">
      <button className="trace-section-toggle" type="button" onClick={() => setOpen(!open)}>
        <span>
          <span className="trace-heading">引用原文</span>
          <span className="trace-section-summary">{cited.length ? `${cited.length} 条引用，点击核对` : sources.length ? "已检索资料，回答未标注引用" : "本次没有可核对的原文引用"}</span>
        </span>
        <ChevronDown size={16} className={open ? "rotate-180 transition-transform" : "transition-transform"} />
      </button>
      {open ? <div className="chunk-list">
        {citations?.status === "invalid" ? <p className="empty-inline">部分引用编号无对应原文，已标记为“出处未核实”。</p> : null}
        {sources.length ? sources.map(source => <details className="chunk-card" key={source.id}>
          <summary className="chunk-toggle">[{source.id}] {source.title}{source.cited ? " · 已引用" : " · 检索资料"}</summary>
          <div className="chunk-content">
            <div className="mb-2 text-xs text-stone-500">{source.source}</div>
            <p style={{ whiteSpace: "pre-wrap" }}>{source.text}</p>
          </div>
        </details>) : <p className="empty-inline">没有检索到可核对的课程原文时，不把图谱关联材料当作回答出处。</p>}
      </div> : null}
    </div>
  );
}
