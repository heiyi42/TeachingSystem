import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useWorkbenchStore } from "../store";
import type { SchoolUser } from "../schoolTypes";
import { TrainingWorkspace } from "./TrainingWorkspace";

export function IdentityGate({
  children,
}: {
  children: (user: SchoolUser, logout: () => Promise<void>) => ReactNode;
}) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<SchoolUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [setup, setSetup] = useState(false);
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [demo, setDemo] = useState(false);
  const [section, setSection] = useState<"training" | "records">("training");

  function clearWorkspace() {
    queryClient.cancelQueries();
    queryClient.clear();
    useWorkbenchStore.getState().setActiveChat(null);
    useWorkbenchStore.getState().setChats([]);
    useWorkbenchStore.getState().setSending(false);
  }

  useEffect(() => {
    let alive = true;
    async function refresh() {
      if (alive) setLoading(true);
      try {
        const result = await api.identity();
        if (alive) {
          clearWorkspace();
          setUser(result.user);
          setSetup(result.setup_needed);
        }
      } catch (reason) {
        if (alive) setError(String(reason));
      } finally {
        if (alive) setLoading(false);
      }
    }
    function expired() {
      setUser(null);
      setDemo(false);
      window.setTimeout(clearWorkspace, 0);
    }
    function changed(event: StorageEvent) {
      if (event.key === "gm.identity.change") refresh();
    }
    window.addEventListener("gm-session-expired", expired);
    window.addEventListener("storage", changed);
    refresh();
    return () => {
      alive = false;
      window.removeEventListener("gm-session-expired", expired);
      window.removeEventListener("storage", changed);
    };
  }, []);

  async function logout() {
    setLoading(true);
    try {
      await api.signOut();
      clearWorkspace();
      setUser(null);
      localStorage.setItem("gm.identity.change", String(Date.now()));
    } finally {
      setLoading(false);
    }
  }

  if (loading)
    return <main className="identity-screen">正在读取登录状态…</main>;
  if (user) return <>{children(user, logout)}</>;
  if (demo)
    return (
      <div className="identity-demo">
        <TrainingWorkspace
          userId="guest"
          section={section}
          onSectionChange={setSection}
          onOpenCourse={() => setDemo(false)}
          onExitGuestDemo={() => setDemo(false)}
        />
      </div>
    );
  return (
    <main className="identity-screen">
      <form
        className="identity-form"
        onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          setLoading(true);
          try {
            const result = await api.signIn(
              setup ? "setup" : register ? "register" : "login",
              { username, name, password },
            );
            clearWorkspace();
            setUser(result.user);
            setPassword("");
            setSetup(false);
            localStorage.setItem("gm.identity.change", String(Date.now()));
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
          } finally {
            setLoading(false);
          }
        }}
      >
        <h1>图思助教</h1>
        <h2>{setup ? "初始化管理员" : register ? "学生注册" : "登录"}</h2>
        {setup && (
          <p>
            首次使用，请在服务器本机设置管理员账号。管理员可以创建教师账号，并保留查看旧版共享记录的权限。
          </p>
        )}
        <label>
          用户名
          <input
            aria-label="用户名"
            autoComplete="username"
            value={username}
            maxLength={32}
            required
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        {(setup || register) && (
          <label>
            姓名
            <input
              aria-label="姓名"
              value={name}
              maxLength={40}
              required
              onChange={(event) => setName(event.target.value)}
            />
          </label>
        )}
        <label>
          密码
          <input
            aria-label="密码"
            type="password"
            autoComplete={
              setup || register ? "new-password" : "current-password"
            }
            value={password}
            minLength={setup || register ? 8 : undefined}
            maxLength={128}
            required
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {(setup || register) && (
          <p className="training-muted">
            用户名使用 3 至 32 位字母、数字或下划线；密码至少 8 位。
          </p>
        )}
        {error && <p role="alert">{error}</p>}
        <button className="send-action" type="submit">
          {setup ? "完成初始化" : register ? "注册并登录" : "登录"}
        </button>
        {!setup && (
          <button
            className="training-link"
            type="button"
            onClick={() => {
              setRegister(!register);
              setError("");
            }}
          >
            {register ? "返回登录" : "注册学生账号"}
          </button>
        )}
        <button
          className="training-link"
          type="button"
          onClick={async () => {
            try {
              const result = await api.createLearningDemo();
              sessionStorage.setItem("gm.learning.guest.demo", result.demo_id);
              setDemo(true);
            } catch (reason) {
              setError(
                reason instanceof Error ? reason.message : String(reason),
              );
            }
          }}
        >
          查看训练演示
        </button>
      </form>
    </main>
  );
}
