import { isValidElement, memo, useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Code2, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

interface MarkdownMessageProps {
  content: string;
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const code = useRef<HTMLPreElement>(null);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");
  const language = isValidElement<{ className?: string }>(children)
    ? children.props.className?.match(/language-([^\s]+)/)?.[1]
    : undefined;
  useEffect(() => {
    if (copyStatus === "idle") return;
    const timer = window.setTimeout(() => setCopyStatus("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [copyStatus]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(code.current?.textContent || "");
      setCopyStatus("copied");
    } catch {
      setCopyStatus("failed");
    }
  }
  return <div className="markdown-code-block">
    <div className="markdown-code-header">
      <span><Code2 size={16} aria-hidden="true" />{language === "text" || language === "plaintext" || !language ? "纯文本" : language}</span>
      <button type="button" onClick={() => void copy()} aria-label={copyStatus === "copied" ? "已复制" : copyStatus === "failed" ? "复制失败，请重试" : "复制代码"} title={copyStatus === "copied" ? "已复制" : copyStatus === "failed" ? "复制失败，请重试" : "复制代码"}>
        {copyStatus === "copied" ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
        <span className="sr-only" aria-live="polite">{copyStatus === "copied" ? "已复制" : copyStatus === "failed" ? "复制失败，请重试" : ""}</span>
      </button>
    </div>
    <pre ref={code}>{children}</pre>
  </div>;
}

export const MarkdownMessage = memo(function MarkdownMessage({ content }: MarkdownMessageProps) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          )
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
