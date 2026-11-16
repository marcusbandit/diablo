import { memo, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Icon } from "./Icon";

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="ghost small"
      title={done ? "Copied" : "Copy to the clipboard"}
      onClick={() => {
        navigator.clipboard?.writeText(text).then(
          () => {
            setDone(true);
            setTimeout(() => setDone(false), 1400);
          },
          () => {},
        );
      }}
    >
      <Icon name={done ? "done" : "copy"} size={14} />
      <span>{done ? "Copied" : label}</span>
    </button>
  );
}

function text(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (typeof node === "object" && "props" in node) return text((node.props as { children?: ReactNode }).children);
  return "";
}

export function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  return (
    <div className="codeblock">
      <div className="codebar">
        <span className="lang">{lang || "code"}</span>
        <CopyButton text={code} />
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" title={href}>
      {children}
    </a>
  ),
  pre: ({ children }) => {
    const child = Array.isArray(children) ? children[0] : children;
    const className = (child && typeof child === "object" && "props" in child ? (child.props as { className?: string }).className : "") ?? "";
    const lang = /language-([\w+-]+)/.exec(className)?.[1];
    return <CodeBlock code={text(children).replace(/\n$/, "")} lang={lang} />;
  },
  table: ({ children }) => (
    <div className="tablewrap">
      <table>{children}</table>
    </div>
  ),
  img: ({ src, alt }) => (typeof src === "string" && /^https?:/.test(src) ? <img src={src} alt={alt ?? ""} title={alt ?? undefined} loading="lazy" /> : null),
};

/** A reply's Markdown, GitHub flavoured. Raw HTML stays text: react-markdown never runs it. */
export const Markdown = memo(function Markdown({ source }: { source: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {source}
      </ReactMarkdown>
    </div>
  );
});
