export type ModeId = "instant" | "deepsearch";
export type SubjectId = "C_program" | "operating_systems" | "cybersec_lab";
export type AgentStatus = "pending" | "running" | "success" | "error" | "skipped";
export type HitType = "direct" | "related" | "normal" | "none";
export type ExplainabilityStatus = "streaming" | "done" | "error" | "cancelled";

export interface MessageDetails {
  explainability?: ExplainabilityDetails;
  [key: string]: unknown;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  meta?: string;
  details?: MessageDetails;
}

export interface ChatSession {
  chat_id: string;
  title: string;
  mode: ModeId;
  pinned: boolean;
  created_at: number;
  updated_at: number;
  message_count: number;
  messages?: ChatMessage[];
  workflow_run?: {
    id: string;
    execution_id: string;
    status: "running" | "stopping" | "cancelled" | "failed" | "interrupted" | "completed";
    mode: ModeId;
    message: string;
    error: string;
    can_resume: boolean;
    can_restart: boolean;
  } | null;
}

export interface GraphNode {
  id: string;
  label: string;
  subjectId: string;
  type: string;
  hitType: HitType;
  score?: number;
  metadata?: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  weight?: number;
  metadata?: Record<string, unknown>;
}

export interface RetrievedChunk {
  id: string;
  chunkId: string;
  subjectId: string;
  preview: string;
  content: string;
  tokens?: number | string;
  filePath?: string;
  rawChunkId?: string;
}

export interface LocalSubgraph {
  id: string;
  title: string;
  subjectId: string;
  summary?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  centerEntityIds: string[];
  chunkIds: string[];
}

export interface GraphPayload {
  ok: boolean;
  error?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  chunks: RetrievedChunk[];
  centerEntityIds: string[];
  subjectIds: string[];
}

export interface AgentExecutionStep {
  nodeId: string;
  nodeName: string;
  status: AgentStatus;
  inputSummary?: string;
  outputSummary?: string;
  durationMs?: number;
  error?: string;
  details?: Record<string, unknown>;
}

export interface DeepSearchSubQuestion {
  id: string;
  question: string;
  usedQuestion?: string;
  queryMode?: string;
  topK?: number | null;
  chunkTopK?: number | null;
}

export interface DeepSearchReviewItem {
  subQuestionId: string;
  sufficient?: boolean | null;
  judgeReason?: string;
}

export interface DeepSearchSubqueryTask {
  taskId?: string;
  subQuestionId: string;
  question?: string;
  usedQuestion?: string;
  subjectId?: string;
  subjectLabel?: string;
  queryMode?: string;
  topK?: number | null;
  chunkTopK?: number | null;
}

export interface DeepSearchSubqueryResult {
  taskId?: string;
  subQuestionId: string;
  subjectId?: string;
  subjectLabel?: string;
  queryMode?: string;
  topK?: number | null;
  chunkTopK?: number | null;
  queryStatus?: string;
  queryMessage?: string;
  failureReason?: string;
  elapsedMs?: number | null;
  answerPreview?: string;
}

export interface DeepSearchRetryInfo {
  queryAttempt: number;
  needsRetry?: boolean;
  insufficientSubquestionIds: string[];
  rewrites?: DeepSearchRetryRewrite[];
}

export interface DeepSearchRetryRewrite {
  attempt?: number | null;
  subQuestionId: string;
  question?: string;
  previousUsedQuestion?: string;
  rewrittenQuestion?: string;
  appliedQuestion?: string;
  judgeReason?: string;
  rewriteReason?: string;
  queryMode?: string;
  topK?: number | null;
  chunkTopK?: number | null;
  targetSubjects?: string[];
  targetSubjectLabels?: string[];
}

export interface DeepSearchTrace {
  subQuestions: DeepSearchSubQuestion[];
  subqueryTasks?: DeepSearchSubqueryTask[];
  subqueryResults?: DeepSearchSubqueryResult[];
  review: DeepSearchReviewItem[];
  retry: DeepSearchRetryInfo;
  finalAnswerPrompt?: string;
  finalAnswerPromptChars?: number | null;
}

export interface AnswerCitations {
  status: "cited" | "uncited" | "invalid" | "unavailable";
  invalidIds: string[];
  sources: { id: string; subject_id: string; chunk_id: string; source: string; title: string; text: string; cited: boolean }[];
}

export interface ExplainabilityDetails {
  citations?: AnswerCitations;
  responseTiming?: {
    firstTextMs: number | null;
    totalMs: number;
    outputMs: number | null;
  };
  mode?: ModeId | string;
  modeUsed?: ModeId | string;
  subject?: SubjectId | string;
  detectedSubject?: SubjectId | string;
  subjectRoute?: Record<string, unknown>;
  workflowSteps: AgentExecutionStep[];
  localSubgraphs: LocalSubgraph[];
  chunks: RetrievedChunk[];
  graphError?: string;
  status: ExplainabilityStatus;
  createdAt?: string;
  retrievalUsed?: boolean;
  retrievalGateResult?: string;
  retrievalGateConfidence?: number | string | null;
  retrievalGateReason?: string;
  deepsearchTrace?: DeepSearchTrace;
}

export type StreamEvent = { id?: number } & (
  | { event: "delta"; data: { text?: string } }
  | { event: "meta"; data: Record<string, unknown> }
  | { event: "done"; data: Record<string, unknown> }
  | { event: "graph_update"; data: GraphPayload }
  | { event: "chunks_update"; data: { chunks?: RetrievedChunk[] } }
  | { event: "workflow_node_start"; data: AgentExecutionStep }
  | { event: "workflow_node_end"; data: AgentExecutionStep }
  | { event: "workflow_node_error"; data: AgentExecutionStep }
  | { event: string; data: Record<string, unknown> });
