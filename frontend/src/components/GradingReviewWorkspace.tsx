import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

const DECISIONS: Record<string, string> = {
  uphold: "维持原判",
  pass: "改判通过",
  fail: "改判未通过",
};
export function GradingReviewWorkspace({ userId }: { userId: string }) {
  const [attemptId, setAttemptId] = useState("");
  const [reviewId, setReviewId] = useState("");
  const [decision, setDecision] = useState("uphold");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const list = useQuery({
    queryKey: ["grading-reviews", userId],
    queryFn: api.gradingReviews,
  });
  const detail = useQuery({
    queryKey: ["grading-review-detail", userId, attemptId],
    queryFn: () => api.gradingReviewDetail(attemptId),
    enabled: !!attemptId,
  });
  const selected = list.data?.items.find((r) => r.id === reviewId);
  const submission =
    selected && detail.data?.submissions[selected.submission_number - 1];
  return (
    <section>
      <h2>评测复核</h2>
      <p>
        教师处理本人授课班级的异议；课外自主练习由管理员处理。复核保留原判定、作答及处理依据，并同步学习状态。实验总评分仍由实验评阅流程管理。
      </p>
      {(error || list.error || detail.error) && (
        <p role="alert">
          {error || list.error?.message || detail.error?.message}
        </p>
      )}
      <button
        disabled={busy}
        onClick={() => {
          list.refetch();
          if (attemptId) detail.refetch();
        }}
      >
        刷新复核记录
      </button>
      {list.isPending && <p>正在读取…</p>}
      {list.data?.items.length === 0 && <p>暂无评测异议。</p>}
      <ul>
        {list.data?.items.map((r) => (
          <li key={r.id}>
            <button
              className="training-link"
              disabled={busy}
              onClick={() => {
                setAttemptId(r.attempt_id);
                setReviewId(r.id);
                setNote("");
                setDecision("uphold");
                setError("");
              }}
            >
              {r.student_name} · {r.title} · 第 {r.submission_number} 次提交 ·{" "}
              {r.status === "pending" ? "待复核" : DECISIONS[r.decision!]}
            </button>
          </li>
        ))}
      </ul>
      {selected && detail.data && submission && (
        <div>
          <h3>
            {selected.student_name} · {selected.title} · 第{" "}
            {selected.submission_number} 次提交
          </h3>
          <p>异议理由：{selected.reason}</p>
          <p>
            题目版本：{String(detail.data.content_version)} · 判分版本：
            {String(detail.data.grading_version)}
          </p>
          <details open>
            <summary>题目与核验条件</summary>
            <pre className="training-code">
              {JSON.stringify(detail.data.exercise, null, 2)}
            </pre>
          </details>
          <details open>
            <summary>本次原始作答与判定（含辅助记录）</summary>
            <pre className="training-code">
              {JSON.stringify(submission, null, 2)}
            </pre>
          </details>
          {selected.status === "pending" ? (
            <>
              <label>
                复核结论
                <select
                  aria-label="复核结论"
                  value={decision}
                  disabled={busy}
                  onChange={(e) => setDecision(e.target.value)}
                >
                  {Object.entries(DECISIONS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                复核依据
                <textarea
                  aria-label="复核依据"
                  value={note}
                  maxLength={3000}
                  disabled={busy}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              <button
                disabled={busy || note.trim().length < 5}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    await api.resolveGradingReview(
                      attemptId,
                      reviewId,
                      decision,
                      note,
                    );
                    await list.refetch();
                    await detail.refetch();
                    setNote("");
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                提交复核结论
              </button>
            </>
          ) : (
            <p>
              {DECISIONS[selected.decision!]} · {selected.reviewer_name}：
              {selected.note}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
