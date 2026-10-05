import { useDiscardChanges } from "./components/shared/useDiscardChanges";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  ChevronDown,
  Menu,
  X,
  MessageSquare,
  Route,
  History,
  RotateCcw,
  GraduationCap,
  BarChart3,
  ClipboardList,
  NotebookPen,
  Settings,
  ListChecks,
  Network,
  ArrowUpRight,
  Cpu,
  ShieldCheck,
  Braces,
  MessageSquarePlus,
  MoreHorizontal,
  PencilLine,
  Pin,
  ArrowUp,
  Square,
  Trash2,
} from "lucide-react";
import { api, streamChatMessage } from "./api";
import { AnswerMessage } from "./components/chat/AnswerMessage";
import { MarkdownMessage } from "./components/shared/MarkdownMessage";
import { ModeDropdown } from "./components/chat/ModeDropdown";
import { SubjectDropdown } from "./components/chat/SubjectDropdown";
import { PersonalAssistant } from "./components/assistant/PersonalAssistant";
import { TrainingWorkspace } from "./components/learning/TrainingWorkspace";
import { IdentityGate } from "./components/auth/IdentityGate";
import { SchoolWorkspace, type SchoolView } from "./components/school/SchoolWorkspace";
import { LearningPathWorkspace } from "./components/learning/LearningPathWorkspace";
import type { LearningQuestionContext, LearningSubject } from "./learningTypes";
import type { SchoolUser } from "./schoolTypes";
import { useWorkbenchStore } from "./store";
import type {
  AgentExecutionStep,
  ChatSession,
  GraphPayload,
  MessageDetails,
  ModeId,
  RetrievedChunk,
  StreamEvent,
  SubjectId,
} from "./types";

const SUBJECTS: Array<{ id: SubjectId; subjects: string[] }> = [
  { id: "C_program", subjects: ["C_program"] },
  { id: "operating_systems", subjects: ["operating_systems"] },
  { id: "cybersec_lab", subjects: ["cybersec_lab"] },
];

export function App() {
  return (
    <IdentityGate>
      {(user, logout) => (
        <Workbench key={user.id} user={user} onLogout={logout} />
      )}
    </IdentityGate>
  );
}

function Workbench({
  user,
  onLogout,
}: {
  user: SchoolUser;
  onLogout: () => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const mountedRef = useRef(true);
  const initialChatSelected = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const executionRef = useRef<{ chatId: string; id: string; execution_id: string } | null>(null);
  const attachAttemptRef = useRef("");
  const connectionChatRef = useRef("");
  const stopRequestedRef = useRef(false);
  const [stopping, setStopping] = useState(false);
  const streamRenderQueueRef = useRef("");
  const streamRenderFrameRef = useRef<number | null>(null);
  const [draft, setDraft] = useState("");
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState({ workspace: false, management: false, conversations: false });
  const navigationToggle = useRef<HTMLButtonElement>(null);
  const [openChatMenuId, setOpenChatMenuId] = useState<string | null>(null);
  const [chatMenuPosition, setChatMenuPosition] = useState({ left: 0, top: 0 });
  const [taskAttempt, setTaskAttempt] = useState<{
    id: string;
    taskId: string;
    nonce: number;
  } | null>(null);
  const [returnTask, setReturnTask] = useState("");
  const [questionContext, setQuestionContext] =
    useState<LearningQuestionContext | null>(null);
  const [navigationError, setNavigationError] = useState("");
  const [openingExercise, setOpeningExercise] = useState(false);
  const [workspace, setWorkspace] = useState<
    "chat" | "training" | "records" | "school" | "path" | "review" | "assistant"
  >(() => {
    const saved = localStorage.getItem("gm.workspace");
    return saved === "assistant" || saved === "training" ||
      saved === "records" ||
      saved === "path" ||
      saved === "review" ||
      saved === "school"
      ? saved
      : "chat";
  });
  const [schoolView, setSchoolView] = useState<SchoolView>(() => {
    const saved = sessionStorage.getItem(`school-view:${user.id}`);
    if (saved === "classes" || saved === "tasks" || saved === "account") return saved;
    if (user.role !== "student" && (saved === "reports" || saved === "preparation" || saved === "content" || saved === "grading")) return saved;
    return "tasks";
  });
  useEffect(() => { sessionStorage.setItem(`school-view:${user.id}`, schoolView); }, [schoolView, user.id]);
  useEffect(() => {
    const input = composerInput.current;
    if (!input || workspace !== "chat") return;
    const resize = () => {
      input.style.height = "auto";
      input.style.height = `${input.scrollHeight}px`;
    };
    resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [draft, workspace]);
  const { confirmDiscard, discardDialog } = useDiscardChanges();
  const [assistantDirty, setAssistantDirty] = useState(false);
  const [schoolDirty, setSchoolDirty] = useState(false);
  const teacher = user.role !== "student";
  const store = useWorkbenchStore();
  useEffect(() => {
    const key = `learning-subject:${user.id}`;
    const saved = sessionStorage.getItem(key);
    useWorkbenchStore.getState().setPreferredSubject(
      saved === "C_program" || saved === "operating_systems" || saved === "cybersec_lab" ? saved : "C_program",
    );
    return useWorkbenchStore.subscribe((next, previous) => {
      if (next.preferredSubject !== previous.preferredSubject) sessionStorage.setItem(key, next.preferredSubject);
    });
  }, [user.id]);
  const [reviewReturnId, setReviewReturnId] = useState("");

  function openAttempt(id: string) {
    setTaskAttempt({ id, taskId: "", nonce: Date.now() });
    setWorkspace("training");
  }

  function openQuestion(
    subject: LearningSubject,
    prompt: string,
    context?: LearningQuestionContext,
  ) {
    store.setPreferredSubject(subject);
    setQuestionContext(context || null);
    if (!context || context.attempt_id !== questionContext?.attempt_id || !draft.trim()) setDraft(prompt);
    setWorkspace("chat");
  }

  async function openNewExercise(id: string) {
    setOpeningExercise(true);
    setNavigationError("");
    try {
      openAttempt((await api.startLearning(id)).id);
    } catch (reason) {
      setNavigationError(
        reason instanceof Error ? reason.message : String(reason),
      );
    } finally {
      setOpeningExercise(false);
    }
  }

  useEffect(() => {
    localStorage.setItem("gm.workspace", workspace);
    setNavigationOpen(false);
  }, [workspace]);

  const chatsQuery = useQuery({ queryKey: ["chats"], queryFn: api.listChats });

  useEffect(() => {
    const updates = new EventSource("/api/events/chat-updates");
    const refreshTitles = () => { void queryClient.invalidateQueries({ queryKey: ["chats"] }); };
    updates.addEventListener("title_updated", refreshTitles);
    updates.addEventListener("open", refreshTitles);
    return () => updates.close();
  }, [queryClient]);

  const activeChatQuery = useQuery({
    queryKey: ["chat", store.activeChatId],
    queryFn: () => api.getChat(store.activeChatId || ""),
    enabled: Boolean(store.activeChatId),
    refetchInterval: (query) => !store.sending && ["running", "stopping"].includes(query.state.data?.workflow_run?.status || "") ? 2000 : false,
  });

  useEffect(() => {
    if (chatsQuery.data?.chats) {
      store.setChats(chatsQuery.data.chats);
      if (!initialChatSelected.current) {
        initialChatSelected.current = true;
        if (!store.activeChatId && chatsQuery.data.chats.length) {
          store.setActiveChat(chatsQuery.data.chats[0]);
        }
      }
    }
  }, [chatsQuery.data]);

  useEffect(() => {
    if (connectionChatRef.current && connectionChatRef.current !== store.activeChatId) {
      abortRef.current?.abort();
      resetStreamRenderer();
      executionRef.current = null;
      connectionChatRef.current = "";
      attachAttemptRef.current = "";
      store.setSending(false);
      setStopping(false);
    }
  }, [store.activeChatId]);

  useEffect(() => {
    const chat = activeChatQuery.data;
    if (chat && !useWorkbenchStore.getState().sending) {
      store.setActiveChat(chat);
      const run = chat.workflow_run;
      if (run && ["running", "stopping"].includes(run.status) && attachAttemptRef.current !== run.execution_id) {
        attachAttemptRef.current = run.execution_id;
        void attachStream(chat);
      }
    }
  }, [activeChatQuery.data]);

  useEffect(() => {
    mountedRef.current = true;
    function onPointerDown(event: PointerEvent) {
      const target = event.target as Element | null;
      if (!target?.closest(".chat-menu-wrap, .chat-menu")) setOpenChatMenuId(null);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpenChatMenuId(null);
        setNavigationOpen(false);
      }
    }

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
      useWorkbenchStore.getState().setSending(false);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  useEffect(() => {
    if (!openChatMenuId) return;
    const closeMenu = () => setOpenChatMenuId(null);
    const onScroll = (event: Event) => {
      if (!(event.target instanceof Element) || !event.target.closest(".chat-menu")) closeMenu();
    };
    window.addEventListener("resize", closeMenu);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", closeMenu);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [openChatMenuId]);

  useEffect(() => () => resetStreamRenderer(), []);

  function startNewChat() {
    initialChatSelected.current = true;
    setQuestionContext(null);
    setDraft("");
    setNavigationError("");
    setOpenChatMenuId(null);
    setNavigationOpen(false);
    setWorkspace("chat");
    store.setActiveChat(null);
    composerInput.current?.focus();
  }

  const currentSubject = useMemo(
    () =>
      SUBJECTS.find((item) => item.id === store.preferredSubject) ||
      SUBJECTS[0],
    [store.preferredSubject],
  );

  async function ensureChat(): Promise<ChatSession> {
    if (store.activeChatId) {
      return activeChatQuery.data || (await api.getChat(store.activeChatId));
    }
    const chat = await api.createChat(store.preferredMode);
    if (mountedRef.current) {
      store.setActiveChat(chat);
      queryClient.invalidateQueries({ queryKey: ["chats"] });
    }
    return chat;
  }

  async function sendMessage(extraPayload: Record<string, unknown> = {}) {
    const text = String(extraPayload.message || draft).trim();
    if (!text || useWorkbenchStore.getState().sending) return;
    setDraft("");
    store.setSending(true);
    try {
      const chat = await ensureChat();
      if (!mountedRef.current) return;
      if (extraPayload.resume_run_id) {
        const saved = await api.getChat(chat.chat_id);
        const messages = [...(saved.messages || [])];
        if (messages[messages.length - 1]?.details?.workflow_run_id === extraPayload.resume_run_id) {
          messages.splice(-2);
        }
        store.setActiveChat({ ...saved, messages });
      }
      store.appendUserMessage(text);
      const messageMode = extraPayload.mode === "instant" || extraPayload.mode === "deepsearch" ? extraPayload.mode : store.preferredMode;
      store.startAssistantMessage(messageMode, store.preferredSubject);
      await receiveStream(chat.chat_id, {
        message: text, mode: messageMode, subjects: currentSubject.subjects,
        ...(questionContext ? { learning_attempt_id: questionContext.attempt_id } : {}),
        ...extraPayload,
      });
    } catch (error) {
      store.setSending(false);
      setNavigationError(error instanceof Error ? error.message : String(error));
    }
  }

  async function attachStream(chat: ChatSession) {
    const run = chat.workflow_run;
    if (!run || useWorkbenchStore.getState().sending) return;
    const messages = [...(chat.messages || [])];
    if (messages[messages.length - 1]?.details?.workflow_run_id === run.id) messages.splice(-2);
    store.setActiveChat({ ...chat, messages });
    store.appendUserMessage(run.message);
    store.startAssistantMessage(run.mode, store.preferredSubject);
    setStopping(run.status === "stopping");
    await receiveStream(chat.chat_id, null, run);
  }

  async function receiveStream(chatId: string, payload: Record<string, unknown> | null, run?: NonNullable<ChatSession["workflow_run"]>) {
    abortRef.current?.abort();
    resetStreamRenderer();
    const controller = new AbortController();
    abortRef.current = controller;
    connectionChatRef.current = chatId;
    stopRequestedRef.current = false;
    setNavigationError("");
    executionRef.current = run ? { chatId, id: run.id, execution_id: run.execution_id } : null;
    if (payload) setStopping(false);
    store.setSending(true);
    const isCurrent = () => mountedRef.current && abortRef.current === controller && useWorkbenchStore.getState().activeChatId === chatId;
    try {
      await streamChatMessage(chatId, payload, controller.signal, event => {
        if (!isCurrent()) return;
        if (event.event === "meta" && event.data.workflow_run_id && event.data.execution_id) {
          executionRef.current = { chatId, id: String(event.data.workflow_run_id), execution_id: String(event.data.execution_id) };
          attachAttemptRef.current = String(event.data.execution_id);
          if (stopRequestedRef.current) { stopRequestedRef.current = false; void stopGeneration(); }
        }
        handleStreamEvent(event);
      }, run);
      if (isCurrent()) flushAssistantDeltas();
    } catch (error) {
      if (isCurrent() && !controller.signal.aborted) {
        flushAssistantDeltas();
        setNavigationError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (isCurrent()) {
        store.setSending(false);
        setStopping(false);
        queryClient.invalidateQueries({ queryKey: ["chats"] });
        queryClient.invalidateQueries({ queryKey: ["chat", chatId] });
      }
    }
  }

  async function stopGeneration() {
    const run = executionRef.current;
    if (!run) { stopRequestedRef.current = true; setStopping(true); return; }
    setStopping(true);
    try {
      await api.cancelChatRun(run.chatId, run.id, run.execution_id);
    } catch (error) {
      setStopping(false);
      setNavigationError(error instanceof Error ? error.message : String(error));
    }
  }

  function handleStreamEvent(event: StreamEvent) {
    if (!mountedRef.current) return;
    if (event.event === "delta") {
      enqueueAssistantDelta(String(event.data.text || ""));
      return;
    }
    if (event.event === "meta") {
      store.updateCurrentMeta(event.data);
      return;
    }
    if (
      event.event === "workflow_node_start" ||
      event.event === "workflow_node_end" ||
      event.event === "workflow_node_error"
    ) {
      const step = event.data as AgentExecutionStep;
      if (step.nodeId === "final_response" && event.event === "workflow_node_end") {
        flushAssistantDeltas();
      }
      store.updateCurrentWorkflowStep(step);
      return;
    }
    if (event.event === "graph_update") {
      store.updateCurrentGraph(event.data as GraphPayload);
      return;
    }
    if (event.event === "chunks_update") {
      store.updateCurrentChunks((event.data.chunks || []) as RetrievedChunk[]);
      return;
    }
    if (event.event === "done") {
      const answer =
        typeof event.data.answer === "string" ? event.data.answer : "";
      const meta =
        typeof event.data.assistant_meta === "string"
          ? event.data.assistant_meta
          : "";
      const details = isMessageDetails(event.data.message_details)
        ? event.data.message_details
        : undefined;
      if (!answer) return;
      flushAssistantDeltas();
      store.finishAssistantMessage(answer, meta, details);
    }
  }

  function resetStreamRenderer() {
    if (streamRenderFrameRef.current !== null) {
      window.cancelAnimationFrame(streamRenderFrameRef.current);
      streamRenderFrameRef.current = null;
    }
    streamRenderQueueRef.current = "";
  }

  function enqueueAssistantDelta(text: string) {
    if (!text) return;
    streamRenderQueueRef.current += text;
    if (streamRenderFrameRef.current === null) {
      streamRenderFrameRef.current = window.requestAnimationFrame(flushAssistantDeltas);
    }
  }

  function flushAssistantDeltas() {
    const text = streamRenderQueueRef.current;
    resetStreamRenderer();
    if (text && mountedRef.current && connectionChatRef.current === useWorkbenchStore.getState().activeChatId) store.appendAssistantDelta(text);
  }

  async function renameChat(chat: ChatSession) {
    const nextTitle = window.prompt("重命名会话", chat.title);
    if (!nextTitle) return;
    await api.renameChat(chat.chat_id, nextTitle);
    queryClient.invalidateQueries({ queryKey: ["chats"] });
    queryClient.invalidateQueries({ queryKey: ["chat", chat.chat_id] });
  }

  async function deleteChat(chat: ChatSession) {
    if (!window.confirm(`删除「${chat.title}」？`)) return;
    await api.deleteChat(chat.chat_id);
    if (store.activeChatId === chat.chat_id) store.setActiveChat(null);
    queryClient.invalidateQueries({ queryKey: ["chats"] });
  }

  async function togglePin(chat: ChatSession) {
    await api.pinChat(chat.chat_id, !chat.pinned);
    queryClient.invalidateQueries({ queryKey: ["chats"] });
  }

  async function updateMode(mode: ModeId) {
    store.setPreferredMode(mode);
    if (store.activeChatId) await api.setMode(store.activeChatId, mode);
  }

  return (
    <div className={`app-shell${navigationOpen ? " navigation-open" : ""}`}>
      {discardDialog}
      <div className="mobile-topbar">
        <button ref={navigationToggle} className="ghost-action" aria-label={navigationOpen ? "收起导航" : "展开导航"} aria-expanded={navigationOpen} aria-controls="workbench-navigation" onClick={() => setNavigationOpen(!navigationOpen)}>
          {navigationOpen ? <X size={18} /> : <Menu size={18} />}
          导航
        </button>
        <strong>图思助教</strong>
      </div>
      <aside id="workbench-navigation" className={`sidebar${navigationOpen ? " is-open" : ""}`} onKeyDown={(event) => {
        if (event.key === "Escape") navigationToggle.current?.focus();
      }}>

        <div className="brand-block">
          <div className="brand-mark"><Network size={24} strokeWidth={1.7} /></div>
          <div>
            <div className="brand-title">图思助教</div>
            <div className="brand-subtitle">课程学习工作台</div>
          </div>
        </div>
        <nav className="workspace-nav" aria-label="工作区">
          <button className={workspace === "assistant" ? "active" : ""} aria-current={workspace === "assistant" ? "page" : undefined} onClick={() => {
            setWorkspace("assistant");
            setNavigationOpen(false);
            if (navigationOpen) navigationToggle.current?.focus();
          }}><MessageSquarePlus size={18} aria-hidden="true" />个人助理</button>
          <button type="button" className="nav-group-label nav-group-toggle" aria-expanded={!collapsedGroups.workspace} onClick={() => setCollapsedGroups(current => ({ ...current, workspace: !current.workspace }))}>
            <ChevronDown size={14} aria-hidden="true" />{teacher ? "教学工作区" : "学习工作区"}
          </button>
          {([
            ...(teacher ? [
              ["reports", "班级学情", BarChart3],
              ["preparation", "备课安排", NotebookPen],
              ["tasks", "作业与测验", ClipboardList],
              ["classes", "班级管理", GraduationCap],
            ] as const : [
              ["path", "课程进度", Route],
              ["tasks", "我的作业", ClipboardList],
              ["review", "错题复习", RotateCcw],
              ["records", "学习记录", History],
              ["classes", "我的班级", GraduationCap],
            ] as const),
            ...(user.role === "admin" ? [
              ["path", "课程进度", Route],
              ["review", "错题复习", RotateCcw],
              ["records", "学习记录", History],
            ] as const : []),
            ["training", "训练中心", BookOpen],
            ["chat", "课程问答", MessageSquare],
            ...(teacher ? [
              ["content", "题库审核", BookOpen],
              ["grading", "评测复核", ListChecks],
            ] as const : []),
          ] as const).map(([id, label, Icon]) => {
            const school = id === "reports" || id === "preparation" || id === "tasks" || id === "classes" || id === "content" || id === "grading";
            const active = school ? workspace === "school" && schoolView === id : workspace === id;
            return <Fragment key={id}>
              {id === "content" && <button type="button" className="nav-group-label nav-group-toggle nav-management" aria-expanded={!collapsedGroups.management} onClick={() => setCollapsedGroups(current => ({ ...current, management: !current.management }))}>
                <ChevronDown size={14} aria-hidden="true" />教学管理
              </button>}
              <button hidden={id === "content" || id === "grading" ? collapsedGroups.management : collapsedGroups.workspace} className={active ? "active" : ""} aria-current={active ? "page" : undefined} onClick={() => {
                if (school && id !== schoolView && schoolDirty) { confirmDiscard(() => { setSchoolView(id); setReturnTask(""); setWorkspace("school"); }); return; }
                if (school) { setSchoolView(id); setReturnTask(""); setWorkspace("school"); }
                else setWorkspace(id);
                setNavigationOpen(false);
                if (navigationOpen) navigationToggle.current?.focus();
              }}><Icon size={18} aria-hidden="true" />{label}</button>
            </Fragment>;
          })}
        </nav>
        <div className={`sidebar-conversations${collapsedGroups.conversations ? " is-collapsed" : ""}`}>
          <div className="conversation-heading">
            <button type="button" className="nav-group-label nav-group-toggle" aria-expanded={!collapsedGroups.conversations} aria-controls="recent-conversations" onClick={() => {
              setCollapsedGroups(current => ({ ...current, conversations: !current.conversations }));
              setOpenChatMenuId(null);
            }}><ChevronDown size={14} aria-hidden="true" />最近会话</button>
            <button className="chat-menu-trigger" title="新建聊天" aria-label="新建聊天" onClick={startNewChat}>
              <MessageSquarePlus size={18} />
            </button>
          </div>
          <div id="recent-conversations" className="conversation-content" hidden={collapsedGroups.conversations}>
          {store.chats.length === 0 && <p className="sidebar-empty">还没有会话，输入问题即可开始。</p>}
        <div className="chat-list">
          {store.chats.map((chat) => (
            <div
              key={chat.chat_id}
              className={`chat-row ${workspace === "chat" && store.activeChatId === chat.chat_id ? "active" : ""}`}
            >
              <button className="chat-select" aria-label={chat.title_pending ? "正在生成会话标题" : chat.title} aria-busy={chat.title_pending || undefined} aria-current={workspace === "chat" && store.activeChatId === chat.chat_id ? "true" : undefined} onClick={() => {
                setWorkspace("chat");
                setOpenChatMenuId(null);
                setNavigationOpen(false);
                if (store.activeChatId !== chat.chat_id) store.setActiveChat(chat);
              }}>{chat.title_pending ? <span className="chat-title-pending" aria-hidden="true" /> : chat.title}</button>
              <div
                className="chat-menu-wrap"
                onClick={(event) => event.stopPropagation()}
              >
                <button
                  aria-expanded={openChatMenuId === chat.chat_id}
                  aria-label="会话操作"
                  className="chat-menu-trigger"
                  type="button"
                  aria-haspopup="menu"
                  onClick={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    const menuHeight = 146;
                    setChatMenuPosition({
                      left: Math.max(8, Math.min(rect.right - 200, window.innerWidth - 208)),
                      top: Math.max(8, rect.bottom + menuHeight + 6 <= window.innerHeight - 8
                        ? rect.bottom + 6 : rect.top - menuHeight - 6),
                    });
                    setOpenChatMenuId((current) => current === chat.chat_id ? null : chat.chat_id);
                  }}
                >
                  <MoreHorizontal size={16} />
                </button>
                {openChatMenuId === chat.chat_id ? createPortal(
                  <div className="chat-menu" role="menu" aria-label="会话操作" style={chatMenuPosition}>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setOpenChatMenuId(null);
                        renameChat(chat);
                      }}
                    >
                      <PencilLine size={18} />
                      重命名
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setOpenChatMenuId(null);
                        togglePin(chat);
                      }}
                    >
                      <Pin size={18} />
                      {chat.pinned ? "取消置顶" : "置顶"}
                    </button>
                    <div className="chat-menu-divider" role="separator" />
                    <button
                      className="danger"
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setOpenChatMenuId(null);
                        deleteChat(chat);
                      }}
                    >
                      <Trash2 size={18} />
                      删除
                    </button>
                  </div>, document.body,
                ) : null}
              </div>
            </div>
          ))}
        </div>
        </div>
        </div>
        <div className="identity-status">
          <button className="account-link" aria-current={workspace === "school" && schoolView === "account" ? "page" : undefined} onClick={() => { if (schoolDirty && schoolView !== "account") { confirmDiscard(() => { setSchoolView("account"); setWorkspace("school"); }); return; } setSchoolView("account"); setWorkspace("school"); }}><Settings size={16} />账号设置</button>
          {user.name} ·{" "}
          {user.role === "admin"
            ? "管理员"
            : user.role === "teacher"
              ? "教师"
              : "学生"}
          <button
            className="training-link"
            onClick={() => {
              const logout = () => { void onLogout().catch(reason => window.alert(reason instanceof Error ? reason.message : String(reason))); };
              if (schoolDirty || assistantDirty) confirmDiscard(logout);
              else logout();
            }}
          >
            退出登录
          </button>
        </div>
      </aside>

      <main className="chat-surface" hidden={workspace !== "chat"}>
        <header className="workspace-toolbar">
          <div className="workspace-title-block">
            <div>
              <h1 className="workspace-title">课程问答</h1>
            </div>
            <div className="toolbar-controls">
              <ModeDropdown value={store.preferredMode} onChange={updateMode} />
              <SubjectDropdown
                value={store.preferredSubject}
                onChange={(subject) => {
                  setQuestionContext(null);
                  store.setPreferredSubject(subject);
                }}
              />
            </div>
          </div>
        </header>

        <section className="message-pane">
          {questionContext && (
            <div className="question-context">
              <strong>来自训练：{questionContext.title}</strong>
              <details><summary>查看资料出处</summary><p>{questionContext.source} · 第 {questionContext.start_line}—{questionContext.end_line} 行</p></details>
              <div className="path-actions">
                <button
                  className="training-link"
                  disabled={store.sending}
                  onClick={() => openAttempt(questionContext.attempt_id)}
                >
                  返回原练习
                </button>
                <button
                  className="training-link"
                  disabled={store.sending}
                  onClick={() => setQuestionContext(null)}
                >
                  结束作答辅导
                </button>
              </div>
            </div>
          )}
          {store.activeMessages.length ? (
            store.activeMessages.map((message, index) =>
              message.role === "assistant" ? (
                <AnswerMessage
                  key={`${message.role}-${index}`}
                  message={message}
                  question={[...store.activeMessages.slice(0, index)].reverse().find(item => item.role === "user")?.content || ""}
                  userId={user.id}
                  subject={store.preferredSubject}
                  disabled={store.sending || openingExercise}
                  onOpenExercise={openNewExercise}
                  onOpenPath={() => setWorkspace("path")}
                />
              ) : (
                <article
                  key={`${message.role}-${index}`}
                  className="message user"
                >
                  <MarkdownMessage content={message.content} />
                </article>
              ),
            )
          ) : questionContext ? (
            <p className="training-muted">可以描述卡住的步骤，或说明你希望检查的计算方法。</p>
          ) : (
            <div className="empty-state">
              <h2>从一个具体问题开始</h2>
              <p className="welcome-description">选择上方课程，或用下面的问题开始讨论。</p>
              <div className="course-starters">
                {([
                  ["C_program", "C 语言", "语法基础、指针与程序分析", "如何理解 C 语言中指针与数组的区别？", Braces],
                  ["operating_systems", "操作系统", "进程调度、内存与文件系统", "请用一个例子说明进程调度的执行过程。", Cpu],
                  ["cybersec_lab", "网络安全实验", "安全原理、协议与实验分析", "做网络安全实验时，应该如何分析协议中的安全风险？", ShieldCheck],
                ] as const).map(([id, title, description, prompt, Icon]) => (
                  <button key={id} className="course-starter" onClick={() => {
                    setQuestionContext(null);
                    store.setPreferredSubject(id);
                    setDraft(prompt);
                    composerInput.current?.focus();
                  }}>
                    <Icon size={24} strokeWidth={1.5} />
                    <strong>{title}</strong>
                    <span>{description}</span>
                    <span className="course-starter-action">开始提问 <ArrowUpRight size={15} /></span>
                  </button>
                ))}
              </div>

            </div>
          )}
        </section>

        <footer className="composer">
          {!store.sending && activeChatQuery.data?.workflow_run?.can_restart && (
            <div className="composer-notice" role="status">
              上次回答未完成。
              <button className="ghost-action" onClick={() => {
                const run = activeChatQuery.data?.workflow_run;
                if (run) sendMessage({ message: run.message, mode: run.mode, ...(run.can_resume ? { resume_run_id: run.id } : { restart_run_id: run.id }) });
              }}>{activeChatQuery.data?.workflow_run?.can_resume ? "继续上次回答" : "重新生成"}</button>
            </div>
          )}
          {!store.sending && ["running", "stopping"].includes(activeChatQuery.data?.workflow_run?.status || "") && (
            <div className="composer-notice" role="status">后台仍在生成。
              <button className="ghost-action" onClick={() => { if (activeChatQuery.data) void attachStream(activeChatQuery.data); }}>重新连接</button>
              <button className="ghost-action" disabled={stopping} onClick={() => {
                const chat = activeChatQuery.data, run = chat?.workflow_run;
                if (chat && run) { executionRef.current = { chatId: chat.chat_id, id: run.id, execution_id: run.execution_id }; void stopGeneration(); }
              }}>停止生成</button>
            </div>
          )}
          {navigationError && (
            <p className="composer-notice" role="alert">
              {navigationError}
            </p>
          )}
          <div className="composer-input">
            <textarea
              ref={composerInput}
              rows={1}
              aria-label="课程问题"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  sendMessage();
                }
              }}
              placeholder="输入课程问题、题目或代码"
            />
            <button
              className="send-action"
              type="button"
              aria-label={store.sending ? (stopping ? "正在停止" : "停止生成") : "发送"}
              title={store.sending ? (stopping ? "正在停止" : "停止生成") : "发送"}
              disabled={store.sending ? stopping : !draft.trim()}
              onClick={() => store.sending ? stopGeneration() : sendMessage()}
            >
              {store.sending ? <Square size={16} aria-hidden="true" /> : <ArrowUp size={20} aria-hidden="true" />}
            </button>
          </div>
        </footer>
      </main>

      <div
        className="training-host"
        hidden={workspace !== "training" && workspace !== "records"}
      >
        <TrainingWorkspace
          userId={user.id}
          initialAttemptId={taskAttempt?.id}
          requestNonce={taskAttempt?.nonce}
          reviewReturnId={reviewReturnId}
          onBackToReview={() => {
            void queryClient.invalidateQueries({ queryKey: ["learning-path", user.id] });
            setWorkspace("review");
          }}
          onBackToTask={(taskId) => {
            setReturnTask(taskId);
            void queryClient.invalidateQueries({ queryKey: ["school-tasks"] });
            setSchoolView("tasks");
            setWorkspace("school");
          }}
          section={workspace === "records" ? "records" : "training"}
          onSectionChange={setWorkspace}
          onOpenCourse={(subject) => {
            setQuestionContext(null);
            store.setPreferredSubject(subject);
            setWorkspace("chat");
          }}
          onQuestion={(context) =>
            openQuestion(
              context.subject_id,
              "请帮我分析这道练习涉及的概念和检查方法。",
              context,
            )
          }
        />
      </div>
      {(workspace === "path" || workspace === "review") && (
        <div className="training-host">
          <LearningPathWorkspace
            userId={user.id}
            section={workspace}
            onOpenAttempt={(id) => {
              setReviewReturnId(workspace === "review" ? id : "");
              openAttempt(id);
            }}
            onQuestion={openQuestion}
          />
        </div>
      )}
      <div className="training-host" hidden={workspace !== "assistant"}><PersonalAssistant onDirtyChange={setAssistantDirty} active={workspace === "assistant"} key={user.id} userId={user.id} user={user} onOpen={(next) => { if (next === "school") { setSchoolView("tasks"); setReturnTask(""); } setWorkspace(next); }} onOpenAttempt={openAttempt} /></div>
      <div className="training-host" hidden={workspace !== "school"}>
          <SchoolWorkspace
            user={user}
            view={schoolView}
            onDirtyChange={setSchoolDirty}
            initialTaskId={returnTask || undefined}
            onOpenAttempt={(id, taskId) => {
              setTaskAttempt({ id, taskId, nonce: Date.now() });
              setReturnTask("");
              setWorkspace("training");
            }}
          />
      </div>
    </div>
  );
}

function isMessageDetails(value: unknown): value is MessageDetails {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
