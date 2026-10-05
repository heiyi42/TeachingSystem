import { ChevronDown } from "lucide-react";
import { useState } from "react";
import type { ChatMessage } from "../../types";
import { LocalKnowledgeGraphBlock } from "./LocalKnowledgeGraphBlock";
import { RetrievedChunksCollapse } from "./RetrievedChunksCollapse";
import { WorkflowTraceBlock } from "./WorkflowTraceBlock";

interface ExplainabilityCollapseProps {
  message: ChatMessage;
}

export function ExplainabilityCollapse({ message }: ExplainabilityCollapseProps) {
  const [open, setOpen] = useState(false);
  const details = message.details?.explainability;

  if ((details?.modeUsed || details?.mode) !== "deepsearch") return null;

  const stepCount = new Set(details?.workflowSteps?.filter(step => ["deepsearch_plan", "deepsearch_retrieve", "deepsearch_review", "deepsearch_retry", "answer_generate"].includes(step.nodeId) && step.status !== "skipped" && step.status !== "pending").map(step => step.nodeId)).size;
  const graphCount = details?.localSubgraphs?.reduce((sum, graph) => sum + graph.nodes.length, 0) || 0;
  const chunkCount = details?.citations?.sources.filter(source => source.cited).length || 0;
  const hasRuntimeData = stepCount > 0 || graphCount > 0 || chunkCount > 0 || details?.graphError;

  return (
    <section className="explainability-box">
      <button className="explainability-toggle" type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span>
          <span className="explainability-title">检索与引用</span>
          <span className="explainability-summary">
            {hasRuntimeData
              ? `${stepCount} 个节点 · ${graphCount} 个实体 · ${chunkCount} 条证据`
              : "本次回答暂无可解释信息"}
          </span>
        </span>
        <ChevronDown size={18} className={open ? "rotate-180 transition-transform" : "transition-transform"} />
      </button>
      {open ? (
        <div className="explainability-content">
          <WorkflowTraceBlock details={details} />
          <LocalKnowledgeGraphBlock
            subgraphs={details?.localSubgraphs || []}
            graphError={details?.graphError || ""}
          />
          <RetrievedChunksCollapse citations={details?.citations} />
        </div>
      ) : null}
    </section>
  );
}
