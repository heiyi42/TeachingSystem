import type { ExamOverview, ExamPlan } from "./components/ExamPlanPanel";
import type { AssistantView } from "./components/PersonalAssistant";
import type { ChatSession, StreamEvent } from "./types";
import type {
  GradingReview,
  LearningWorkflowRun,
  Roadshow,
  StudyPlan,
  TrainingMatch,
  LearningAttempt,
  LearningCourse,
  LearningExercise,
  LearningProgress,
  LearningRow,
  LearningPath,
  ChapterMaterial,
  LearningQuestionContext,
  LearningGuidance,
  LearningAction,
} from "./learningTypes";
import type {
  SchoolUser,
  SchoolClass,
  ContentVersion,
  ContentDetail,
  SchoolAttempt,
  SchoolTask,
  TaskDetail,
  ClassReport,
} from "./schoolTypes";

async function apiJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const identityVersion = localStorage.getItem("gm.identity.change");
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (localStorage.getItem("gm.identity.change") !== identityVersion)
    throw new Error("账号已切换，请重试");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("gm-session-expired"));
    const message =
      typeof data?.error === "string" ? data.error : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return data as T;
}

export function createLearningApi(demoId = "", classId = "") {
  const prefix = demoId ? `/api/learning-demo/${demoId}` : "/api/learning";
  return {
    learningRoadshow: (subject: string) => apiJson<Roadshow>(`${prefix}/roadshow/${subject}`),
    learningCourses: () =>
      apiJson<{ courses: LearningCourse[] }>(`${prefix}/courses`),
    learningExercises: () =>
      apiJson<{ exercises: LearningExercise[] }>(`${prefix}/exercises`),
    learningProgress: () => apiJson<LearningProgress>(`${prefix}/progress`),
    matchTraining: (
      question: string,
      subject_id: string,
      chapter_id?: string,
    ) =>
      apiJson<{ matches: TrainingMatch[] }>(`${prefix}/training-match`, {
        method: "POST",
        body: JSON.stringify({ question, subject_id, chapter_id }),
      }),
    learningWorkflow: (kind: "dialogue" | "plan", target: string) =>
      apiJson<LearningWorkflowRun | null>(kind === "dialogue"
        ? `${prefix}/attempts/${target}/dialogue` : `${prefix}/plan/${target}/ai`),
    generateStudyPlan: (subject: string, resume_run_id?: string) =>
      apiJson<StudyPlan>(`${prefix}/plan/${subject}/ai`, {
        method: "POST",
        body: JSON.stringify({ resume_run_id }),
      }),
    studyPlan: (subject: string) =>
      apiJson<StudyPlan>(`${prefix}/plan/${subject}`),
    configureStudyPlan: (
      subject: string,
      minutes: number,
      chapter_id: string,
    ) =>
      apiJson<StudyPlan>(`${prefix}/plan/${subject}`, {
        method: "PUT",
        body: JSON.stringify({ minutes, chapter_id }),
      }),
    beginDiagnostic: (subject: string, point_id: string) =>
      apiJson<{ attempt_id: string }>(`${prefix}/plan/${subject}/diagnostic`, {
        method: "POST",
        body: JSON.stringify({ point_id }),
      }),
    learningWalkthrough: (id: string, revision: number, prediction: string) =>
      apiJson<LearningAttempt>(`${prefix}/attempts/${id}/walkthrough`, {
        method: "POST", body: JSON.stringify({ revision, prediction }),
      }),
    learningDialogue: (id: string, action: string, revision: number, answer = "", resume_run_id?: string) =>
      apiJson<LearningAttempt>(`${prefix}/attempts/${id}/dialogue`, {
        method: "POST", body: JSON.stringify({ action, revision, answer, resume_run_id }),
      }),
    requestGradingReview: (
      id: string,
      submission_number: number,
      reason: string,
    ) =>
      apiJson<LearningAttempt>(`${prefix}/attempts/${id}/grading-reviews`, {
        method: "POST",
        body: JSON.stringify({ submission_number, reason }),
      }),
    learningPath: () => apiJson<LearningPath>(`${prefix}/path`),
    beginLearningRecommendation: (pointId: string, token: string) =>
      apiJson<{
        id: string;
        action: LearningAction;
        attempt_id: string | null;
      }>(`${prefix}/points/${encodeURIComponent(pointId)}/recommendations`, {
        method: "POST",
        body: JSON.stringify({ token }),
      }),
    learningExplanation: (pointId: string) =>
      apiJson<LearningGuidance>(
        `${prefix}/points/${encodeURIComponent(pointId)}/explanation`,
        { method: "POST", body: "{}" },
      ),
    chapterMaterial: (id: string, start = 1, q = "") =>
      apiJson<ChapterMaterial>(
        `${prefix}/chapters/${encodeURIComponent(id)}/material?start=${start}&q=${encodeURIComponent(q)}`,
      ),
    markReading: (id: string, read: boolean) =>
      apiJson(`${prefix}/chapters/${encodeURIComponent(id)}/reading`, {
        method: "PUT",
        body: JSON.stringify({ read }),
      }),
    startReview: (id: string) =>
      apiJson<LearningAttempt>(`${prefix}/reviews/${id}/start`, {
        method: "POST",
        body: "{}",
      }),
    learningQuestionContext: (id: string) =>
      apiJson<LearningQuestionContext>(
        `${prefix}/attempts/${id}/question-context`,
        { method: "POST", body: "{}" },
      ),
    startLearning: (exerciseId?: string, parentId?: string) =>
      apiJson<LearningAttempt>(`${prefix}/attempts`, {
        method: "POST",
        body: JSON.stringify({
          exercise_id: exerciseId,
          parent_id: parentId,
          class_id: parentId ? undefined : classId || undefined,
        }),
      }),
    getLearning: (id: string) =>
      apiJson<LearningAttempt>(`${prefix}/attempts/${id}`),
    saveLearningDraft: (id: string, rows: LearningRow[]) =>
      apiJson<LearningAttempt>(`${prefix}/attempts/${id}/draft`, {
        method: "PUT",
        body: JSON.stringify({ rows }),
      }),
    submitLearning: (id: string, rows: LearningRow[]) =>
      apiJson<LearningAttempt>(`${prefix}/attempts/${id}/submit`, {
        method: "POST",
        body: JSON.stringify({ rows }),
      }),
    learningHint: (id: string, step?: number) =>
      apiJson<LearningAttempt>(`${prefix}/attempts/${id}/hint`, {
        method: "POST",
        body: JSON.stringify({ step }),
      }),
    learningSolution: (id: string) =>
      apiJson<LearningAttempt>(`${prefix}/attempts/${id}/solution`, {
        method: "POST",
        body: "{}",
      }),
  };
}

export const api = {
  examPlans: () => apiJson<ExamOverview>("/api/assistant/exam-plans"),
  examDraft: (data: { subject_id: string; exam_date: string; chapters: string[]; minutes: number }) => apiJson<ExamPlan>("/api/assistant/exam-plans", { method: "POST", body: JSON.stringify(data) }),
  examAdopt: (id: string, revision: number) => apiJson<ExamPlan>(`/api/assistant/exam-plans/${id}/adopt`, { method: "POST", body: JSON.stringify({revision}) }),
  examToday: (id: string, revision: number, minutes: number) => apiJson<ExamPlan>(`/api/assistant/exam-plans/${id}/today`, { method: "PUT", body: JSON.stringify({revision, minutes}) }),
  examStartTask: (id: string, task: string) => apiJson<LearningAttempt>(`/api/assistant/exam-plans/${id}/tasks/${task}/start`, { method: "POST", body: "{}" }),
  assistant: () => apiJson<AssistantView>("/api/assistant"),
  assistantMessage: (content: string, request_id: string) => apiJson<AssistantView>("/api/assistant/messages", { method: "POST", body: JSON.stringify({content, request_id}) }),
  assistantMemory: (enabled: boolean) => apiJson<AssistantView>("/api/assistant/memory", { method: "PUT", body: JSON.stringify({enabled}) }),
  assistantForget: (id?: string) => apiJson<AssistantView>(`/api/assistant/memory${id ? `/${encodeURIComponent(id)}` : ""}`, { method: "DELETE", body: "{}" }),
  assistantMemoryRetry: () => apiJson<AssistantView>("/api/assistant/memory/retry", { method: "POST", body: "{}" }),
  cancelChatRun: (chatId: string, runId: string, executionId: string) =>
    apiJson(`/api/chats/${chatId}/runs/${runId}/cancel`, {
      method: "POST", body: JSON.stringify({ execution_id: executionId }),
    }),
  identity: () =>
    apiJson<{ user: SchoolUser | null; setup_needed: boolean }>(
      "/api/identity/session",
    ),
  signIn: (
    action: "setup" | "login" | "register",
    data: { username: string; password: string; name: string },
  ) =>
    apiJson<{ user: SchoolUser }>(`/api/identity/${action}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  signOut: () =>
    apiJson("/api/identity/logout", { method: "POST", body: "{}" }),
  changePassword: (old_password: string, new_password: string) =>
    apiJson("/api/identity/password", {
      method: "POST",
      body: JSON.stringify({ old_password, new_password }),
    }),
  createTeacher: (data: { username: string; password: string; name: string }) =>
    apiJson("/api/school/teachers", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  schoolClasses: () =>
    apiJson<{ classes: SchoolClass[] }>("/api/school/classes"),
  createClass: (title: string, course_id: string) =>
    apiJson<SchoolClass>("/api/school/classes", {
      method: "POST",
      body: JSON.stringify({ title, course_id }),
    }),
  joinClass: (join_code: string) =>
    apiJson("/api/school/classes/join", {
      method: "POST",
      body: JSON.stringify({ join_code }),
    }),
  rotateClassCode: (id: string) =>
    apiJson(`/api/school/classes/${id}/rotate-code`, {
      method: "POST",
      body: "{}",
    }),
  schoolMembers: (id: string) =>
    apiJson<{ members: SchoolUser[] }>(`/api/school/classes/${id}/members`),
  removeMember: (classId: string, userId: string) =>
    apiJson(`/api/school/classes/${classId}/members/${userId}`, {
      method: "DELETE",
      body: "{}",
    }),
  studentProgress: (classId: string, userId: string) =>
    apiJson<LearningProgress>(
      `/api/school/classes/${classId}/students/${userId}/progress`,
    ),
  studentAttempt: (classId: string, userId: string, id: string) =>
    apiJson<SchoolAttempt>(
      `/api/school/classes/${classId}/students/${userId}/attempts/${id}`,
    ),
  schoolContent: () =>
    apiJson<{ items: ContentVersion[] }>("/api/school/content"),
  contentDetail: (key: string) =>
    apiJson<ContentDetail>(`/api/school/content/${encodeURIComponent(key)}`),
  newContentVersion: (key: string) =>
    apiJson(`/api/school/content/${encodeURIComponent(key)}/versions`, {
      method: "POST",
      body: "{}",
    }),
  saveContent: (
    key: string,
    version: number,
    data: Record<string, unknown>,
    source: string,
  ) =>
    apiJson(`/api/school/content/${encodeURIComponent(key)}/${version}`, {
      method: "PUT",
      body: JSON.stringify({ data, source }),
    }),
  contentAction: (key: string, version: number, action: string, note: string) =>
    apiJson(
      `/api/school/content/${encodeURIComponent(key)}/${version}/${action}`,
      { method: "POST", body: JSON.stringify({ note }) },
    ),
  taskPublishedExercises: () =>
    apiJson<{ exercises: LearningExercise[] }>("/api/school/task-exercises"),
  schoolTasks: (classId: string) =>
    apiJson<{ tasks: SchoolTask[] }>(`/api/school/classes/${classId}/tasks`),
  taskDetail: (id: string, studentId?: string) =>
    apiJson<TaskDetail>(
      `/api/school/tasks/${id}${studentId ? `?student_id=${studentId}` : ""}`,
    ),
  saveTask: (data: Record<string, unknown>, id?: string) =>
    apiJson<SchoolTask>(id ? `/api/school/tasks/${id}` : "/api/school/tasks", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(data),
    }),
  taskAction: (id: string, action: "publish" | "close") =>
    apiJson<SchoolTask>(`/api/school/tasks/${id}/${action}`, {
      method: "POST",
      body: "{}",
    }),
  startTask: (id: string, exerciseId: string) =>
    apiJson<LearningAttempt>(`/api/school/tasks/${id}/attempts`, {
      method: "POST",
      body: JSON.stringify({ exercise_id: exerciseId }),
    }),
  finalizeTask: (id: string) =>
    apiJson<TaskDetail>(`/api/school/tasks/${id}/finalize`, {
      method: "POST",
      body: "{}",
    }),
  saveTaskReport: (id: string, report: string) =>
    apiJson<TaskDetail>(`/api/school/tasks/${id}/report`, {
      method: "PUT",
      body: JSON.stringify({ report }),
    }),
  uploadTaskFile: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return apiJson<TaskDetail>(`/api/school/tasks/${id}/files`, {
      method: "POST",
      body,
      headers: {},
    });
  },
  deleteTaskFile: (id: string, fileId: string) =>
    apiJson(`/api/school/tasks/${id}/files/${fileId}`, {
      method: "DELETE",
      body: "{}",
    }),
  reviewTask: (
    id: string,
    studentId: string,
    comment: string,
    score?: number,
  ) =>
    apiJson<TaskDetail>(
      `/api/school/tasks/${id}/students/${studentId}/review`,
      { method: "POST", body: JSON.stringify({ comment, score }) },
    ),
  classReport: (classId: string) =>
    apiJson<ClassReport>(`/api/school/classes/${classId}/report`),
  gradingReviews: () =>
    apiJson<{
      items: Array<
        GradingReview & {
          attempt_id: string;
          title: string;
          owner_id: string;
          student_name: string;
          class_id: string | null;
        }
      >;
    }>("/api/school/grading-reviews"),
  gradingReviewDetail: (id: string) =>
    apiJson<{
      exercise: Record<string, unknown>;
      submissions: Array<Record<string, unknown>>;
      content_version: string | number;
      grading_version: string;
    }>(`/api/school/grading-reviews/${id}`),
  resolveGradingReview: (
    id: string,
    review: string,
    decision: string,
    note: string,
  ) =>
    apiJson<LearningAttempt>(`/api/school/grading-reviews/${id}/${review}`, {
      method: "POST",
      body: JSON.stringify({ decision, note }),
    }),
  ...createLearningApi(),
  createLearningDemo: () =>
    apiJson<{ demo_id: string; attempt: LearningAttempt }>(
      "/api/learning/demo",
      { method: "POST", body: "{}" },
    ),
  listChats: () => apiJson<{ chats: ChatSession[] }>("/api/chats"),
  createChat: (mode: string) =>
    apiJson<ChatSession>("/api/chats", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  getChat: (chatId: string) => apiJson<ChatSession>(`/api/chats/${chatId}`),
  deleteChat: (chatId: string) =>
    apiJson<{ ok: boolean }>(`/api/chats/${chatId}/delete`, { method: "POST" }),
  renameChat: (chatId: string, title: string) =>
    apiJson<ChatSession>(`/api/chats/${chatId}/rename`, {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  pinChat: (chatId: string, pinned: boolean) =>
    apiJson<ChatSession>(`/api/chats/${chatId}/pin`, {
      method: "POST",
      body: JSON.stringify({ pinned }),
    }),
  setMode: (chatId: string, mode: string) =>
    apiJson<ChatSession>(`/api/chats/${chatId}/mode`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
};

export async function streamChatMessage(
  chatId: string,
  payload: Record<string, unknown> | null,
  signal: AbortSignal,
  onEvent: (event: StreamEvent) => void,
  execution?: { id: string; execution_id: string },
): Promise<void> {
  const identityVersion = localStorage.getItem("gm.identity.change");
  let runId = execution?.id || "";
  let executionId = execution?.execution_id || "";
  let cursor = 0;
  let finished = false;
  let terminal = false;
  let lastError: unknown;
  for (let attempt = 0; attempt < 4; attempt++) {
    signal.throwIfAborted();
    if (localStorage.getItem("gm.identity.change") !== identityVersion)
      throw new Error("账号已切换");
    try {
      const initial = attempt === 0 && payload !== null;
      const response = await fetch(initial
        ? `/api/chats/${chatId}/messages/stream`
        : `/api/chats/${chatId}/runs/${runId}/events?execution_id=${executionId}&after=${cursor}`, {
        method: initial ? "POST" : "GET",
        headers: initial ? { "Content-Type": "application/json" } : undefined,
        body: initial ? JSON.stringify(payload) : undefined,
        signal,
      });
      if (!response.ok || !response.body) {
        terminal = true;
        if (response.status === 401) window.dispatchEvent(new Event("gm-session-expired"));
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data?.error === "string" ? data.error : `HTTP ${response.status}`);
      }
      runId = response.headers.get("X-Workflow-Run") || runId;
      executionId = response.headers.get("X-Workflow-Execution") || executionId;
      if (runId && executionId) onEvent({ event: "meta", data: { workflow_run_id: runId, execution_id: executionId } });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          if (localStorage.getItem("gm.identity.change") !== identityVersion) {
            terminal = true;
            throw new Error("账号已切换");
          }
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          const parts = buffer.split("\n\n");
          buffer = parts.pop() || "";
          for (const part of parts) {
            const event = parseSseBlock(part);
            if (!event || (event.id !== undefined && event.id <= cursor)) continue;
            if (event.event === "meta") {
              runId = String(event.data.workflow_run_id || runId);
              executionId = String(event.data.execution_id || executionId);
            }
            if (event.event === "stream_end") {
              terminal = true;
              if (!finished) throw new Error("生成已中断，请恢复进度或重新生成");
              return;
            }
            onEvent(event);
            cursor = event.id ?? cursor;
            if (event.event === "done") finished = true;
          }
        }
      } finally {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
      }
      if (finished) return;
      throw new Error("连接已断开");
    } catch (error) {
      lastError = error;
      if (signal.aborted || terminal || !runId || !executionId) throw error;
      if (attempt < 3) await new Promise(resolve => window.setTimeout(resolve, 300 * (attempt + 1)));
    }
  }
  throw lastError;
}

function parseSseBlock(block: string): StreamEvent | null {
  const lines = block.split(/\r?\n/);
  let event = "message";
  let id: number | undefined;
  let data = "";
  for (const line of lines) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("id:")) {
      const value = Number(line.slice(3).trim());
      if (Number.isSafeInteger(value) && value > 0) id = value;
    } else if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      data += line.slice("data:".length).trim();
    }
  }
  if (!data) return null;
  try {
    return { id, event, data: JSON.parse(data) } as StreamEvent;
  } catch {
    return { id, event, data: { text: data } } as StreamEvent;
  }
}
