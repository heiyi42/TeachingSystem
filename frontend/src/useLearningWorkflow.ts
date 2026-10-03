import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export function useLearningWorkflow(
  owner: string, kind: "dialogue" | "plan", target: string,
  onComplete: () => Promise<unknown>, enabled = true,
) {
  const completed = useRef("");
  const callback = useRef(onComplete);
  callback.current = onComplete;
  const [refreshError, setRefreshError] = useState("");
  const query = useQuery({
    queryKey: ["learning-workflow", owner, kind, target],
    queryFn: () => api.learningWorkflow(kind, target),
    enabled,
    retry: false,
    refetchInterval: (q) => q.state.data?.status === "running" ? 1000 : 5000,
  });
  useEffect(() => {
    const run = query.data;
    if (!enabled || run?.status !== "done" || completed.current === run.id) return;
    completed.current = run.id;
    let active = true;
    callback.current().then(() => {
      if (active) setRefreshError("");
    }).catch(() => {
      if (active) {
        completed.current = "";
        setRefreshError("执行已完成，刷新结果失败，请刷新页面。");
      }
    });
    return () => { active = false; };
  }, [enabled, query.data, query.dataUpdatedAt]);
  return {
    run: query.data,
    running: query.data?.status === "running",
    refresh: query.refetch,
    error: query.error ? "暂时无法查询执行状态，请稍后刷新。" : refreshError,
  };
}
